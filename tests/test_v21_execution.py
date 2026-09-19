"""Execution failure injection; no participant data or audio hardware."""

import json
import tempfile
import unittest
from dataclasses import replace
from typing import Any, cast

from mdt_core.config import DEFAULT, ExecutionConfig
from mdt_core.engine import NullEngine
from mdt_core.execution import AcceptedDecision, ExecutionGateway, Mode, MusicVector
from mdt_core.l4_l6 import ProgramState
from mdt_core.profiles import v21_config
from mdt_core.session import Session
from mdt_core.types import Arm, MusicParams, SessionStatus
from tests.synthetic import ready_baseline, synthetic_window


def observed_window(t):
    return replace(synthetic_window(t), observed_end_t=t)


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.engine = NullEngine()
        self.gateway = ExecutionGateway(
            "epoch",
            self.engine,
            ExecutionConfig(recovery_observations=1),
            DEFAULT.grammar,
            MusicParams(),
        )

    def decision(self, sequence=1, now=1.0, observed=None, **kwargs):
        return AcceptedDecision(
            "epoch",
            sequence,
            f"obs-{sequence}",
            now if observed is None else observed,
            now,
            self.gateway.ack_id,
            MusicVector.of(MusicParams(tempo=60.0, dynamics=0.9)),
            **kwargs,
        )

    def test_target_immutable_and_defensive_current(self):
        decision = self.decision()
        self.gateway.accept(decision, 1.0)
        self.gateway.current.tempo = 99
        self.assertEqual(self.gateway.current.tempo, 72.0)
        with self.assertRaises(AttributeError):
            decision.target.tempo = 99

    def test_double_ttl_cannot_refresh_old_observation(self):
        decision = self.decision(now=100.0, observed=1.0)
        self.assertFalse(self.gateway.accept(decision, 100.0))
        self.assertEqual(self.gateway.mode, Mode.HOLD)
        self.assertFalse(self.engine.started)

    def test_future_nan_and_backwards_clock(self):
        for source in (2.0, float("nan")):
            self.assertFalse(self.gateway.accept(self.decision(observed=source), 1.0))
        with self.assertRaises(ValueError):
            self.gateway.poll(0.0)

    def test_latest_target_replaces_pending_and_rejects_old(self):
        d1 = self.decision()
        self.gateway.accept(d1, 1.0)
        d2 = replace(
            self.decision(2, 2.0), target=MusicVector.of(MusicParams(tempo=80.0))
        )
        self.assertTrue(self.gateway.accept(d2, 2.0))
        self.assertFalse(self.gateway.accept(d1, 2.0))
        self.assertEqual(self.gateway.pending, d2)
        self.assertGreater(self.gateway.boundary(3.0).tempo, 72.0)

    def test_real_boundaries_progress_fixed_target_and_duplicates_deduplicate(self):
        d = self.decision()
        self.gateway.accept(d, 1.0)
        first = self.gateway.boundary(2.0, phrase=True)
        count = len(self.engine.history)
        self.assertEqual(self.gateway.boundary(2.0, phrase=True), first)
        self.assertEqual(count, len(self.engine.history))
        second = self.gateway.boundary(4.0, phrase=True)
        self.assertLess(second.tempo, first.tempo)
        assert self.gateway.pending is not None
        self.assertEqual(self.gateway.pending.target, d.target)
        self.assertEqual(self.gateway.current, self.engine.history[-1])

    def test_engine_retransmission_is_idempotent_and_payload_reuse_rejected(self):
        self.gateway.accept(self.decision(), 1.0)
        self.gateway.boundary(2.0)
        command = self.gateway.last_command
        assert command is not None
        count = len(self.engine.history)
        self.assertEqual(self.engine.submit(command), self.gateway.last_ack)
        self.assertEqual(len(self.engine.history), count)
        with self.assertRaises(ValueError):
            self.engine.submit(
                replace(command, projected=MusicVector.of(MusicParams()))
            )

    def test_old_epoch_parent_ack_and_reused_observation_rejected(self):
        self.gateway.accept(self.decision(), 1.0)
        self.gateway.boundary(2.0)
        d = self.decision(2, 3.0)
        for bad in (
            replace(d, epoch="old"),
            replace(d, parent_ack_id="bootstrap"),
            replace(d, observation_end=1.0),
        ):
            self.assertFalse(self.gateway.accept(bad, 3.0))

    def test_commit_rechecks_ttl_and_watchdog_stops_without_new_sensors(self):
        self.gateway.accept(self.decision(), 1.0)
        self.gateway.boundary(12.0)
        self.assertFalse(self.engine.started)
        self.assertEqual(self.gateway.mode, Mode.HOLD)
        self.gateway.poll(41.0)
        self.assertEqual(self.gateway.mode, Mode.STOPPED)
        self.assertFalse(self.gateway.accept(self.decision(2, 42.0), 42.0))

    def test_missing_ack_does_not_advance_current_and_stops(self):
        cast(Any, self.engine).submit = lambda command: None
        self.gateway.accept(self.decision(), 1.0)
        before = self.gateway.current
        self.gateway.boundary(2.0)
        self.assertEqual(before, self.gateway.current)
        self.assertEqual(self.gateway.mode, Mode.STOPPED)

    def test_malformed_ack_also_revokes_writes(self):
        cast(Any, self.engine).submit = lambda command: {}
        self.gateway.accept(self.decision(), 1.0)
        self.gateway.boundary(2.0)
        self.assertEqual(self.gateway.mode, Mode.STOPPED)
        self.assertIsNone(self.gateway.pending)

    def test_noninteger_sequence_cannot_poison_ordering(self):
        for value in (float("nan"), 1.5, True):
            with self.assertRaises(ValueError):
                self.decision(sequence=value)

    def test_wrong_ack_rejected(self):
        original = self.engine.submit
        cast(Any, self.engine).submit = lambda cmd: replace(original(cmd), command_id="stale")
        self.gateway.accept(self.decision(), 1.0)
        self.gateway.boundary(2.0)
        self.assertIsNone(self.gateway.last_ack)
        self.assertEqual(self.gateway.mode, Mode.STOPPED)

    def test_dynamics_hard_cap_and_rate(self):
        self.gateway.accept(self.decision(), 1.0)
        first = self.gateway.boundary(2.0)
        second = self.gateway.boundary(4.0)
        self.assertLessEqual(first.dynamics, 0.5 + 0.02 * 2 + 1e-12)
        self.assertLessEqual(second.dynamics - first.dynamics, 0.02 * 2 + 1e-12)
        self.assertLessEqual(second.dynamics, 0.65)

    def test_hold_recovery_requires_distinct_valid_observations_and_baseline(self):
        self.gateway.cfg = replace(self.gateway.cfg, recovery_observations=2)
        self.gateway.hold(0.0, "dropout")
        self.assertFalse(self.gateway.accept(self.decision(), 1.0))
        self.assertFalse(self.gateway.accept(self.decision(), 1.0))
        self.assertTrue(self.gateway.accept(self.decision(2, 2.0), 2.0))
        self.assertEqual(self.gateway.mode, Mode.BASELINE)

    def test_stop_failure_leaves_writes_revoked(self):
        def failing_stop():
            raise RuntimeError("device unavailable")

        cast(Any, self.engine).stop = failing_stop
        self.gateway.stop(1.0)
        self.assertEqual(self.gateway.mode, Mode.STOPPING)
        self.assertFalse(self.gateway.accept(self.decision(2, 2.0), 2.0))


class IntegrationTests(unittest.TestCase):
    def test_v21_requires_explicit_source_timestamp_and_deduplicates_ingestion(self):
        with tempfile.TemporaryDirectory() as out:
            session = Session(
                "synthetic",
                ready_baseline(),
                ProgramState(),
                arm=Arm.FULL_LOOP,
                cfg=v21_config(),
                out_dir=out,
            )
            session.fast_tick(synthetic_window(0.0))
            self.assertFalse(session.recorder.physio)
            session.slow_tick(observed_window(2.0))
            count = len(session.recorder.physio)
            session.fast_tick(observed_window(2.0))
            self.assertEqual(len(session.recorder.physio), count)

    def test_multirate_clock_never_double_counts_elapsed_time(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as out:
            session = Session(
                "synthetic",
                ready_baseline(),
                ProgramState(),
                arm=Arm.FULL_LOOP,
                cfg=v21_config(),
                out_dir=out,
            )
            intervals = []
            original = session.controller.step

            def measured(target, state, dt):
                intervals.append(dt)
                return original(target, state, dt)

            with patch.object(session.controller, "step", side_effect=measured):
                for t in sorted(set(range(0, 61, 2)) | set(range(0, 61, 15))):
                    if t % 15 == 0:
                        session.slow_tick(observed_window(float(t)))
                    if t % 2 == 0:
                        session.fast_tick(observed_window(float(t)))
                    session.music_boundary(float(t))
            self.assertLessEqual(sum(intervals), 60.0)
            self.assertLessEqual(session.planner._active_time, 60.0)

    def test_boundary_failure_aborts_session_without_recording_completion(self):
        with tempfile.TemporaryDirectory() as out:
            session = Session(
                "synthetic",
                ready_baseline(),
                ProgramState(),
                arm=Arm.FULL_LOOP,
                cfg=v21_config(),
                out_dir=out,
            )
            session.fast_tick(observed_window(0.0))
            session.fast_tick(observed_window(2.0))
            cast(Any, session.engine).submit = lambda command: None
            session.music_boundary(2.0)
            self.assertEqual(session.status, SessionStatus.ABORTED)
            self.assertEqual(session.program.completed_sessions, 0)

    def test_v21_session_uses_ack_and_persists_lineage(self):
        with tempfile.TemporaryDirectory() as out:
            session = Session(
                "synthetic",
                ready_baseline(),
                ProgramState(),
                arm=Arm.FULL_LOOP,
                cfg=v21_config(),
                out_dir=out,
            )
            for t in (0.0, 2.0, 4.0, 6.0):
                session.fast_tick(observed_window(t), bar_boundary=True)
            assert session.execution is not None
            self.assertTrue(any(e["event"] == "ack" for e in session.execution.events))
            session.watchdog(100.0)
            self.assertEqual(session.status, SessionStatus.ABORTED)
            assert session._output_path is not None
            with open(session._output_path) as stream:
                data = json.load(stream)
            self.assertTrue(data["execution_events"])
            assert isinstance(session.engine, NullEngine)
            self.assertTrue(session.engine.stopped)

    def test_ingestion_preserves_original_age(self):
        with tempfile.TemporaryDirectory() as out:
            session = Session(
                "synthetic",
                ready_baseline(),
                ProgramState(),
                arm=Arm.FULL_LOOP,
                cfg=v21_config(),
                out_dir=out,
            )
            window = synthetic_window(60.0)
            window.observed_end_t = 0.0
            session.fast_tick(window)
            assert session.execution is not None
            self.assertEqual(session.execution.mode, Mode.HOLD)
            self.assertFalse(session.recorder.physio)
            session.watchdog(91.0)
            self.assertEqual(session.status, SessionStatus.ABORTED)


if __name__ == "__main__":
    unittest.main()
