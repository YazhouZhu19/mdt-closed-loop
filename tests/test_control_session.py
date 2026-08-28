"""Control and orchestration tests with isolated synthetic inputs and outputs."""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from mdt_core.config import DEFAULT
from mdt_core.engine import NullEngine, ShamEngine
from mdt_core.l1_state import IndividualBaseline
from mdt_core.l2_planner import DoseTracker
from mdt_core.l3_control import MusicGrammar, PIController
from mdt_core.l4_l6 import ArmAssigner, ProgramState, SafetyMonitor
from mdt_core.session import Session
from mdt_core.types import Arm, MusicParams, SessionStatus, State
from tests.synthetic import ready_baseline, synthetic_window


class ControllerTests(unittest.TestCase):
    def test_deadband_returns_hold_command(self) -> None:
        controller = PIController(DEFAULT.control)
        controller.step(0.2, State(0, 0.8, 1), 60)
        output, reason = controller.step(0.2, State(60, 0.21, 1), 60)
        self.assertEqual(reason, "deadband")
        self.assertEqual(output, 0.0)

    def test_non_positive_dt_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            PIController(DEFAULT.control).step(0.2, State(0, 0.8, 1), 0)

    def test_layer_change_is_reversible(self) -> None:
        grammar = MusicGrammar(DEFAULT.grammar)
        original = grammar.current.layer_mask
        grammar.request(-0.2, 0)
        down, _ = grammar.commit(0, phrase_boundary=True)
        grammar.request(0.2, 32)
        up, _ = grammar.commit(32, phrase_boundary=True)
        self.assertNotEqual(down.layer_mask, original)
        self.assertEqual(up.layer_mask, original)

    def test_explicit_boundary_tolerates_clock_jitter(self) -> None:
        grammar = MusicGrammar(DEFAULT.grammar)
        grammar.request(-0.5, 60.013)
        _, changed = grammar.commit(60.013, bar_boundary=True)
        self.assertTrue(changed)

    def test_zero_command_cancels_pending_structure(self) -> None:
        grammar = MusicGrammar(DEFAULT.grammar)
        original = grammar.current.layer_mask
        grammar.request(-0.2, 60)
        grammar.request(0, 62)
        params, _ = grammar.commit(64, phrase_boundary=True)
        self.assertEqual(params.layer_mask, original)


class SessionTests(unittest.TestCase):
    def test_calibration_is_rest_only_and_not_treatment_dose(self) -> None:
        baseline = IndividualBaseline()
        program = ProgramState(baseline_isi=18)
        with tempfile.TemporaryDirectory() as out_dir:
            for _ in range(3):
                session = Session(
                    "synthetic-calibration",
                    baseline,
                    program,
                    engine=NullEngine(),
                    arm=Arm.FULL_LOOP,
                    out_dir=out_dir,
                    is_calibration=True,
                )
                for t in (0, 60, 120, 180):
                    session.slow_tick(synthetic_window(t))
                first_path = session.finish()
                self.assertEqual(first_path, session.finish())
            self.assertTrue(baseline.is_ready)
            self.assertEqual(program.completed_sessions, 0)
            self.assertTrue(all(len(values) == 9 for values in baseline._acc.values()))

    def test_fast_and_slow_cadences_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as out_dir:
            fast = Session(
                "fast",
                ready_baseline(),
                ProgramState(18),
                engine=NullEngine(),
                arm=Arm.FULL_LOOP,
                out_dir=out_dir,
            )
            fast.fast_tick(synthetic_window(0, duration_s=10))
            fast.fast_tick(synthetic_window(2, duration_s=10))
            with self.assertRaises(ValueError):
                fast.fast_tick(synthetic_window(3, duration_s=10))

            slow = Session(
                "slow",
                ready_baseline(),
                ProgramState(18),
                engine=NullEngine(),
                arm=Arm.FULL_LOOP,
                out_dir=out_dir,
            )
            slow.slow_tick(synthetic_window(0))
            with self.assertRaises(ValueError):
                slow.slow_tick(synthetic_window(10))

    def test_end_to_end_closed_loop_and_atomic_record(self) -> None:
        program = ProgramState(baseline_isi=18)
        with tempfile.TemporaryDirectory() as out_dir:
            engine = NullEngine()
            session = Session(
                "synthetic-e2e",
                ready_baseline(),
                program,
                engine=engine,
                arm=Arm.FULL_LOOP,
                out_dir=out_dir,
            )
            state, _ = session.slow_tick(synthetic_window(0, 0.8))
            session.music_boundary(4)
            output = Path(session.finish(isi_score=17))
            self.assertTrue(output.exists())
            self.assertFalse(output.with_suffix(".json.tmp").exists())
            self.assertEqual(program.completed_sessions, 1)
            self.assertGreaterEqual(state.confidence, DEFAULT.state.min_confidence)
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8"))["user_id"],
                "synthetic-e2e",
            )

    def test_lifecycle_is_monotonic_and_finish_is_idempotent(self) -> None:
        program = ProgramState(baseline_isi=18)
        with tempfile.TemporaryDirectory() as out_dir:
            session = Session(
                "lifecycle",
                ready_baseline(),
                program,
                engine=NullEngine(),
                arm=Arm.FULL_LOOP,
                out_dir=out_dir,
            )
            session.slow_tick(synthetic_window(0))
            first = session.finish(isi_score=17)
            self.assertEqual(first, session.finish(isi_score=16))
            self.assertEqual(program.completed_sessions, 1)
            with self.assertRaises(RuntimeError):
                session.slow_tick(synthetic_window(60))

    def test_backward_time_and_duration_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as out_dir:
            session = Session(
                "clock",
                ready_baseline(),
                ProgramState(18),
                engine=NullEngine(),
                arm=Arm.FULL_LOOP,
                out_dir=out_dir,
            )
            session.slow_tick(synthetic_window(120))
            with self.assertRaises(ValueError):
                session.fast_tick(synthetic_window(60))

            too_long = Session(
                "duration",
                ready_baseline(),
                ProgramState(18),
                engine=NullEngine(),
                arm=Arm.FULL_LOOP,
                out_dir=out_dir,
            )
            with self.assertRaises(RuntimeError):
                too_long.slow_tick(
                    synthetic_window(DEFAULT.planner.session_duration_s + 1)
                )

    def test_safety_hook_aborts_live_session(self) -> None:
        calls = []
        monitor = SafetyMonitor(lambda user, text: calls.append((user, text)))
        with tempfile.TemporaryDirectory() as out_dir:
            session = Session(
                "safety",
                ready_baseline(),
                ProgramState(18),
                engine=NullEngine(),
                arm=Arm.FULL_LOOP,
                out_dir=out_dir,
                safety_monitor=monitor,
            )
            session.slow_tick(synthetic_window(0))
            self.assertTrue(session.submit_subjective(instrument={"note": "我不想活"}))
            self.assertEqual(session.status, SessionStatus.ABORTED)
            self.assertTrue(calls)

    def test_futility_is_applied_on_finish(self) -> None:
        program = ProgramState(
            baseline_isi=18, isi_history=[17.5], completed_sessions=15
        )
        with tempfile.TemporaryDirectory() as out_dir:
            session = Session(
                "futility",
                ready_baseline(),
                program,
                engine=NullEngine(),
                arm=Arm.FULL_LOOP,
                out_dir=out_dir,
            )
            session.slow_tick(synthetic_window(0))
            session.finish(isi_score=17)
        self.assertEqual(program.stopped_reason, "futility_refer_to_clinician")

    def test_sham_is_automatic_and_uses_pre_registered_trajectory(self) -> None:
        inner = NullEngine()
        trajectory = [MusicParams(tempo=60), MusicParams(tempo=61)]
        with tempfile.TemporaryDirectory() as out_dir:
            session = Session(
                "sham",
                ready_baseline(),
                ProgramState(18),
                engine=inner,
                arm=Arm.SHAM,
                out_dir=out_dir,
                sham_trajectory=trajectory,
            )
            self.assertIsInstance(session.engine, ShamEngine)
            session.slow_tick(synthetic_window(0))
            session.music_boundary(4)
            session.slow_tick(synthetic_window(15))
            self.assertEqual([p.tempo for p in inner.history], [60, 61])
            output = json.loads(
                Path(session.finish(isi_score=17)).read_text(encoding="utf-8")
            )
            self.assertEqual(
                [row["params"]["tempo"] for row in output["music"]], [60, 61, 61]
            )

    def test_sham_without_trajectory_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Session(
                "sham",
                ready_baseline(),
                ProgramState(18),
                engine=NullEngine(),
                arm=Arm.SHAM,
            )

    def test_calibration_never_requires_or_actuates_sham_music(self) -> None:
        baseline = IndividualBaseline()
        engine = NullEngine()
        with tempfile.TemporaryDirectory() as out_dir:
            session = Session(
                "sham-calibration",
                baseline,
                ProgramState(18),
                engine=engine,
                arm=Arm.SHAM,
                out_dir=out_dir,
                is_calibration=True,
            )
            session.slow_tick(synthetic_window(0))
            with self.assertRaises(RuntimeError):
                session.music_boundary(4)
            session.finish()
        self.assertFalse(engine.started)

    def test_safety_at_finish_is_not_counted_as_completed_dose(self) -> None:
        program = ProgramState(baseline_isi=18)
        with tempfile.TemporaryDirectory() as out_dir:
            session = Session(
                "finish-safety",
                ready_baseline(),
                program,
                engine=NullEngine(),
                arm=Arm.FULL_LOOP,
                out_dir=out_dir,
            )
            session.slow_tick(synthetic_window(0))
            session.finish(post_survey={"note": "我想自伤"}, isi_score=17)
        self.assertEqual(session.status, SessionStatus.ABORTED)
        self.assertEqual(program.completed_sessions, 0)
        self.assertEqual(program.isi_history, [])

    def test_finish_without_observation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as out_dir:
            session = Session(
                "empty",
                ready_baseline(),
                ProgramState(18),
                engine=NullEngine(),
                arm=Arm.FULL_LOOP,
                out_dir=out_dir,
            )
            with self.assertRaises(RuntimeError):
                session.finish(isi_score=17)

    def test_calibration_without_usable_signal_is_not_counted(self) -> None:
        baseline = IndividualBaseline()
        with tempfile.TemporaryDirectory() as out_dir:
            session = Session(
                "lost-calibration",
                baseline,
                ProgramState(18),
                engine=NullEngine(),
                arm=Arm.FULL_LOOP,
                out_dir=out_dir,
                is_calibration=True,
            )
            session.slow_tick(synthetic_window(0, impedance=4e6))
            with self.assertRaises(RuntimeError):
                session.finish()
        self.assertEqual(baseline.sessions_collected, 0)

    def test_invalid_finish_input_has_no_side_effects(self) -> None:
        engine = NullEngine()
        with tempfile.TemporaryDirectory() as out_dir:
            session = Session(
                "invalid-finish",
                ready_baseline(),
                ProgramState(),
                engine=engine,
                arm=Arm.FULL_LOOP,
                out_dir=out_dir,
            )
            session.slow_tick(synthetic_window(0))
            with self.assertRaises(ValueError):
                session.finish(post_survey={"calm": 4}, isi_score=17)
            self.assertEqual(session.status, SessionStatus.RUNNING)
            self.assertFalse(engine.stopped)
            self.assertNotIn("post", session.recorder.subjective)

    def test_failed_fast_cadence_does_not_poison_global_clock(self) -> None:
        with tempfile.TemporaryDirectory() as out_dir:
            session = Session(
                "cadence-clock",
                ready_baseline(),
                ProgramState(18),
                engine=NullEngine(),
                arm=Arm.FULL_LOOP,
                out_dir=out_dir,
            )
            session.fast_tick(synthetic_window(0, duration_s=10))
            with self.assertRaises(ValueError):
                session.fast_tick(synthetic_window(1, duration_s=10))
            session.slow_tick(synthetic_window(0.5))

    def test_audio_and_sensor_events_share_monotonic_processing_order(self) -> None:
        with tempfile.TemporaryDirectory() as out_dir:
            session = Session(
                "event-clock",
                ready_baseline(),
                ProgramState(18),
                engine=NullEngine(),
                arm=Arm.FULL_LOOP,
                out_dir=out_dir,
            )
            session.slow_tick(synthetic_window(0))
            session.music_boundary(4)
            with self.assertRaises(ValueError):
                session.fast_tick(synthetic_window(2))

    def test_long_synthetic_closed_loop_stays_bounded(self) -> None:
        """Forty minutes of generated data; output lives only in a temp directory."""
        with tempfile.TemporaryDirectory() as out_dir:
            session = Session(
                "long-synthetic",
                ready_baseline(),
                ProgramState(18),
                engine=NullEngine(),
                arm=Arm.FULL_LOOP,
                out_dir=out_dir,
            )
            states = []
            for index, t in enumerate(range(0, 2401, 60)):
                arousal = 0.75 - 0.4 * index / 40
                state, params = session.tick(synthetic_window(t, arousal), dt=60)
                states.append(state.arousal)
                self.assertTrue(
                    DEFAULT.grammar.tempo_range[0]
                    <= params.tempo
                    <= DEFAULT.grammar.tempo_range[1]
                )
            output = Path(session.finish(isi_score=17))
            self.assertTrue(output.is_relative_to(Path(out_dir)))
            self.assertTrue(
                all(math.isfinite(value) and 0 <= value <= 1 for value in states)
            )


class ProgramTests(unittest.TestCase):
    def test_dose_upper_bound_is_honored(self) -> None:
        tracker = DoseTracker(DEFAULT.planner)
        tracker.completed = 52
        self.assertEqual(tracker.band, "above_studied_range")

    def test_randomization_weights_are_validated(self) -> None:
        with self.assertRaises(ValueError):
            ArmAssigner(weights={arm: 0 for arm in Arm})

    def test_program_state_rejects_impossible_initial_values(self) -> None:
        with self.assertRaises(ValueError):
            ProgramState(baseline_isi=-1)
        with self.assertRaises(ValueError):
            ProgramState(completed_sessions=-1)

    def test_research_modes_are_distinct(self) -> None:
        self.assertTrue(ArmAssigner.is_closed_loop(Arm.FULL_LOOP))
        self.assertTrue(ArmAssigner.is_open_loop_iso(Arm.ISO))
        self.assertFalse(ArmAssigner.is_closed_loop(Arm.ISO))


if __name__ == "__main__":
    unittest.main()
