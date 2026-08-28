"""Tests for adaptive control using isolated mathematical inputs only.

The ``State`` objects below are generated constants, not participant records.
The integration case uses ``tests.synthetic`` and writes only to a temporary
directory.  No clinical or externally sourced data is read by this module.
"""

from __future__ import annotations

import tempfile
import unittest

from mdt_core.config import DEFAULT, ControlConfig, PlannerConfig
from mdt_core.engine import NullEngine
from mdt_core.l1_state import ArousalEstimator
from mdt_core.l2_planner import TrajectoryPlanner
from mdt_core.l3_control import PIController
from mdt_core.l4_l6 import ProgramState
from mdt_core.session import Session
from mdt_core.types import Arm, Features, SignalQuality, State, Strategy
from tests.synthetic import ready_baseline, synthetic_window


class UncertaintyAwareControlTests(unittest.TestCase):
    def test_posterior_uncertainty_grows_during_signal_loss(self) -> None:
        estimator = ArousalEstimator(ready_baseline(), DEFAULT.state)
        observed = estimator.update(
            Features(
                t=0,
                scl_slope=0.004,
                scr_rate=2.0,
                rmssd=25.0,
                hf_power=500.0,
                sd1=18.0,
            )
        )
        lost = estimator.update(
            Features(t=15, quality=SignalQuality.LOST),
            process_scale=10.0,
        )
        self.assertGreater(lost.uncertainty, observed.uncertainty)
        self.assertEqual(lost.confidence, 0.0)

    def test_controller_derates_as_uncertainty_increases(self) -> None:
        reliable = PIController(DEFAULT.control)
        derated = PIController(DEFAULT.control)
        full_output, full_reason = reliable.step(
            0.2,
            State(t=0, arousal=0.8, confidence=1.0, uncertainty=0.1),
            1.0,
        )
        reduced_output, reduced_reason = derated.step(
            0.2,
            State(t=0, arousal=0.8, confidence=1.0, uncertainty=0.5),
            1.0,
        )
        self.assertEqual(full_reason, "closed_loop")
        self.assertEqual(reduced_reason, "closed_loop_derated")
        self.assertLess(abs(reduced_output), abs(full_output))
        self.assertGreater(derated.last_scale, 0.0)
        self.assertLess(derated.last_scale, 1.0)

    def test_controller_holds_above_hard_uncertainty_limit(self) -> None:
        controller = PIController(DEFAULT.control)
        output, reason = controller.step(
            0.2,
            State(t=0, arousal=0.8, confidence=1.0, uncertainty=0.8),
            1.0,
        )
        self.assertEqual(output, 0.0)
        self.assertEqual(reason, "open_loop_high_uncertainty")
        self.assertEqual(controller.last_scale, 0.0)

    def test_explicit_suspend_clears_output_scale(self) -> None:
        controller = PIController(DEFAULT.control)
        controller.step(
            0.2,
            State(t=0, arousal=0.8, confidence=1.0, uncertainty=0.1),
            1.0,
        )
        controller.suspend()
        self.assertEqual(controller.last_scale, 0.0)

    def test_uncertainty_limits_are_validated(self) -> None:
        with self.assertRaises(ValueError):
            ControlConfig(uncertainty_soft_limit=0.5, uncertainty_hard_limit=0.5)


class AdaptiveTrajectoryTests(unittest.TestCase):
    @staticmethod
    def _planner() -> TrajectoryPlanner:
        planner = TrajectoryPlanner(Strategy.ISO, DEFAULT.planner)
        planner.set_anchor(0.8)
        return planner

    def test_unreliable_state_holds_adaptive_target(self) -> None:
        planner = self._planner()
        planner.adaptive_target(
            300.0,
            State(t=300, arousal=0.8, confidence=0.0, uncertainty=1.0),
            reliability=0.0,
        )
        target = planner.adaptive_target(
            360.0,
            State(t=360, arousal=0.8, confidence=0.0, uncertainty=1.0),
            reliability=0.0,
        )
        self.assertEqual(target, 0.8)
        self.assertEqual(planner.speed_factor, 0.0)
        self.assertEqual(planner.phase, "descent")

    def test_on_track_response_advances_faster_than_lagging_response(self) -> None:
        on_track = self._planner()
        lagging = self._planner()
        initial = State(t=300, arousal=0.8, confidence=1.0, uncertainty=0.1)
        on_track.adaptive_target(300.0, initial, reliability=1.0)
        lagging.adaptive_target(300.0, initial, reliability=1.0)

        fast_target = on_track.adaptive_target(
            360.0,
            State(t=360, arousal=0.79, confidence=1.0, uncertainty=0.1),
            reliability=1.0,
        )
        slow_target = lagging.adaptive_target(
            360.0,
            State(t=360, arousal=0.96, confidence=1.0, uncertainty=0.1),
            reliability=1.0,
        )
        self.assertLess(fast_target, slow_target)
        self.assertGreater(on_track.speed_factor, lagging.speed_factor)

    def test_fixed_iso_comparator_remains_wall_clock_driven(self) -> None:
        fixed = self._planner()
        adaptive = self._planner()
        adaptive.adaptive_target(
            300.0,
            State(t=300, arousal=0.8, confidence=1.0, uncertainty=0.1),
            reliability=1.0,
        )
        adaptive_target = adaptive.adaptive_target(
            360.0,
            State(t=360, arousal=0.79, confidence=1.0, uncertainty=0.1),
            reliability=1.0,
        )
        fixed_target = fixed.target(360.0)
        self.assertLess(adaptive_target, fixed_target)
        self.assertEqual(fixed.speed_factor, 1.0)

    def test_sleep_trajectory_never_raises_a_low_anchor(self) -> None:
        planner = TrajectoryPlanner(Strategy.ISO, DEFAULT.planner)
        planner.set_anchor(0.1)
        self.assertEqual(planner.target(1200.0), 0.1)

    def test_adaptive_configuration_is_validated(self) -> None:
        with self.assertRaises(ValueError):
            PlannerConfig(adaptive_min_speed=2.0, adaptive_max_speed=1.0)


class AdaptiveSessionIntegrationTests(unittest.TestCase):
    def test_full_loop_records_adaptive_and_uncertainty_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as out_dir:
            session = Session(
                "isolated-adaptive-synthetic",
                ready_baseline(),
                ProgramState(18),
                engine=NullEngine(),
                arm=Arm.FULL_LOOP,
                out_dir=out_dir,
            )
            session.slow_tick(synthetic_window(0, arousal=0.8))
            session.slow_tick(synthetic_window(300, arousal=0.8))
            session.slow_tick(synthetic_window(360, arousal=0.7))

            physio = session.recorder.physio[-1]
            music = session.recorder.music[-1]
            self.assertIn("uncertainty", physio)
            self.assertIn("state_uncertainty", music)
            self.assertIn("control_scale", music)
            self.assertEqual(music["trajectory_phase"], "descent")
            self.assertGreater(music["trajectory_speed"], 0.0)


if __name__ == "__main__":
    unittest.main()
