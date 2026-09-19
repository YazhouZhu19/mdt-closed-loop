"""v2.1 PI recurrence checks using mathematical fixtures, not participant data."""

from __future__ import annotations

import math
import unittest
from dataclasses import replace

from mdt_core.config import ControlConfig
from mdt_core.l3_control import PIController
from mdt_core.types import State


def state(confidence: float = 0.8, arousal: float = 0.7) -> State:
    return State(t=0.0, arousal=arousal, confidence=confidence, uncertainty=0.1)


def normalized(**kwargs) -> ControlConfig:
    return replace(ControlConfig(formulation="normalized"), **kwargs)


class NormalizedRecurrenceTests(unittest.TestCase):
    def test_legacy_matches_appendix_d_and_event_leak(self) -> None:
        controller = PIController(ControlConfig(ki=0.015, integral_clamp=4.0))
        controller.step(0.5, state(), 0.625)  # I = -0.10
        output, _ = controller.step(0.5, state(), 15.0)
        self.assertAlmostEqual(controller.integral_state, -2.5)
        self.assertAlmostEqual(output, -0.102)
        controller.suspend()
        self.assertAlmostEqual(controller.integral_state, -2.5 * 0.95)

    def test_time_normalization_is_equivalent_when_gains_and_clamp_scale(self) -> None:
        legacy = PIController(ControlConfig(ki=0.015, integral_clamp=4.0))
        variant = PIController(
            normalized(
                ki=0.015 * 60,
                integral_clamp=4.0 / 60,
                normalized_integral_q_scaling=True,
            )
        )
        for dt in (0.625, 15.0, 12.0, 1.0):
            legacy_output, _ = legacy.step(0.5, state(), dt)
            variant_output, _ = variant.step(0.5, state(), dt)
            self.assertAlmostEqual(variant.integral_state * 60, legacy.integral_state)
            self.assertAlmostEqual(variant_output, legacy_output)

    def test_q_squared_ablation_has_documented_twenty_percent_difference(self) -> None:
        conditional = PIController(normalized(kp=0, ki=0.9))
        q_scaled = PIController(
            normalized(kp=0, ki=0.9, normalized_integral_q_scaling=True)
        )
        conditional_output, _ = conditional.step(0.5, state(), 15)
        q_scaled_output, _ = q_scaled.step(0.5, state(), 15)
        self.assertAlmostEqual(conditional_output, -0.036)
        self.assertAlmostEqual(q_scaled_output, -0.0288)
        self.assertAlmostEqual(q_scaled_output / conditional_output, 0.8)

    def test_active_integral_does_not_leak(self) -> None:
        small = PIController(normalized(integral_clamp=4))
        large = PIController(normalized(integral_clamp=4))
        for _ in range(60):
            small.step(0.5, state(), 1)
        large.step(0.5, state(), 60)
        self.assertAlmostEqual(small.integral_state, large.integral_state)
        self.assertAlmostEqual(small.integral_state, -0.2)

    def test_pause_leak_depends_on_elapsed_time_not_number_of_events(self) -> None:
        small = PIController(normalized())
        large = PIController(normalized())
        for controller in (small, large):
            controller.step(0.5, state(), 60)
        for _ in range(60):
            small.suspend(1)
        large.suspend(60)
        self.assertAlmostEqual(small.integral_state, -0.2 * math.exp(-1))
        self.assertAlmostEqual(small.integral_state, large.integral_state)
        unchanged = large.integral_state
        large.suspend()
        self.assertEqual(large.integral_state, unchanged)

    def test_deadband_and_zero_reliability_use_exponential_leak(self) -> None:
        for next_state in (state(arousal=0.5), state(confidence=0)):
            with self.subTest(next_state=next_state):
                controller = PIController(normalized())
                controller.step(0.5, state(), 60)
                output, _ = controller.step(0.5, next_state, 30)
                self.assertEqual(output, 0)
                self.assertAlmostEqual(controller.integral_state, -0.2 * math.exp(-0.5))

    def test_subthreshold_reliability_freezes_integral_but_derates_output(self) -> None:
        controller = PIController(normalized(kp=0.45, ki=0.9))
        controller.step(0.5, state(), 15)
        previous = controller.integral_state
        output, reason = controller.step(0.5, state(confidence=0.2), 15)
        self.assertEqual(previous, controller.integral_state)
        self.assertAlmostEqual(output, -0.027)
        self.assertEqual(reason, "closed_loop_derated")

    def test_integral_and_output_are_bounded(self) -> None:
        controller = PIController(
            normalized(ki=10, integral_clamp=0.1, output_clamp=0.2)
        )
        output, _ = controller.step(0.0, state(1.0, 1.0), 300)
        self.assertEqual(controller.integral_state, -0.1)
        self.assertEqual(output, -0.2)

    def test_invalid_and_stale_intervals_do_not_change_integral(self) -> None:
        controller = PIController(normalized())
        for dt in (0, -1, math.nan, math.inf, 301):
            with self.subTest(dt=dt), self.assertRaises(ValueError):
                controller.step(0.5, state(), dt)
            self.assertEqual(controller.integral_state, 0)
        for dt in (-1, math.nan, math.inf):
            with self.subTest(dt=dt), self.assertRaises(ValueError):
                controller.suspend(dt)
        controller.suspend(10000)  # Long gap can decay, never integrate stale error.

    def test_config_rejects_invalid_recurrence_parameters(self) -> None:
        for kwargs in (
            {"formulation": "other"},
            {"integral_time_s": 0},
            {"integral_leak_tau_s": math.nan},
            {"max_step_s": -1},
            {"integration_min_reliability": 1.1},
            {"ack_timeout_s": 0},
            {"ack_tracking_time_s": 0},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                normalized(**kwargs)
        with self.assertRaises(ValueError):
            ControlConfig(ack_backcalculation=True)
        with self.assertRaises(TypeError):
            normalized(normalized_integral_q_scaling=1)


class TempoAcknowledgementTests(unittest.TestCase):
    def controller(self, **kwargs) -> PIController:
        controller = PIController(
            normalized(ack_backcalculation=True, ki=0.9, **kwargs)
        )
        controller.step(0.5, state(), 15)
        return controller

    def register(self, controller: PIController, command_id="c1", timestamp=100):
        return controller.register_tempo_command(
            command_id,
            base_tempo_bpm=70,
            submitted_mono_s=timestamp,
        )

    def ack(self, controller: PIController, command_id="c1", timestamp=101, tempo=69.2):
        return controller.acknowledge_tempo(
            command_id,
            actual_tempo_bpm=tempo,
            acknowledged_mono_s=timestamp,
        )

    def test_default_has_no_ack_feedback(self) -> None:
        controller = PIController(normalized())
        controller.step(0.5, state(), 15)
        before = controller.integral_state
        self.assertEqual(self.register(controller), (False, "ack_disabled"))
        self.assertEqual(self.ack(controller), (False, "ack_disabled"))
        self.assertEqual(controller.integral_state, before)

    def test_actual_tempo_is_converted_back_to_integral_coordinates(self) -> None:
        controller = self.controller()
        self.assertTrue(self.register(controller)[0])
        self.assertAlmostEqual(
            controller.integral_state, -0.05
        )  # Registration is not execution.
        self.assertTrue(self.ack(controller)[0])
        # u_cmd=-.108; u_ack=(69.2-70)/12; dI=(15/60)*(u_ack+.108)/(.8*.9).
        self.assertAlmostEqual(controller.integral_state, -0.0356481481481481)

    def test_exact_execution_has_zero_correction(self) -> None:
        controller = self.controller()
        self.register(controller)
        self.assertTrue(self.ack(controller, tempo=70 - 12 * 0.108)[0])
        self.assertAlmostEqual(controller.integral_state, -0.05)

    def test_wrong_duplicate_and_reused_commands_do_not_correct(self) -> None:
        controller = self.controller()
        self.register(controller)
        self.assertFalse(self.ack(controller, command_id="wrong")[0])
        self.assertAlmostEqual(controller.integral_state, -0.05)
        self.assertTrue(self.ack(controller)[0])
        corrected = controller.integral_state
        self.assertFalse(self.ack(controller)[0])
        self.assertFalse(self.register(controller)[0])
        self.assertEqual(controller.integral_state, corrected)

    def test_expired_command_is_not_used(self) -> None:
        controller = self.controller()
        self.register(controller)
        self.assertEqual(self.ack(controller, timestamp=111), (False, "ack_expired"))
        self.assertAlmostEqual(controller.integral_state, -0.05)
        self.assertFalse(self.ack(controller, timestamp=101)[0])

    def test_unacknowledged_command_can_expire_when_new_command_is_registered(
        self,
    ) -> None:
        controller = self.controller()
        self.register(controller)
        controller.step(0.5, state(), 15)
        self.assertEqual(
            self.register(controller, "c2", 105), (False, "ack_command_pending")
        )
        self.assertTrue(self.register(controller, "c2", 111)[0])
        self.assertFalse(self.ack(controller, "c1", 112)[0])
        self.assertTrue(self.ack(controller, "c2", 112)[0])

    def test_reversed_time_is_rejected_without_consuming_valid_ack(self) -> None:
        controller = self.controller()
        self.register(controller)
        self.assertEqual(
            self.ack(controller, timestamp=99), (False, "ack_time_reversed")
        )
        self.assertTrue(self.ack(controller)[0])

    def test_suspend_or_zero_reliability_revokes_outstanding_ack(self) -> None:
        for action in ("suspend", "zero_reliability"):
            with self.subTest(action=action):
                controller = self.controller()
                self.register(controller)
                if action == "suspend":
                    controller.suspend(5)
                else:
                    controller.step(0.5, state(confidence=0), 5)
                before = controller.integral_state
                self.assertFalse(self.ack(controller)[0])
                self.assertEqual(controller.integral_state, before)

    def test_no_inverse_for_zero_ki_or_zero_q(self) -> None:
        zero_ki = PIController(normalized(ack_backcalculation=True, ki=0))
        zero_ki.step(0.5, state(), 15)
        self.assertEqual(self.register(zero_ki), (False, "ack_unobservable_integral"))
        zero_q = self.controller()
        zero_q.step(0.5, state(confidence=0), 15)
        self.assertEqual(self.register(zero_q), (False, "ack_no_active_decision"))

    def test_feedback_is_clamped_and_nonfinite_ack_rejected(self) -> None:
        controller = self.controller(integral_clamp=0.1)
        self.register(controller)
        with self.assertRaises(ValueError):
            self.ack(controller, tempo=math.nan)
        self.assertTrue(self.ack(controller, tempo=100)[0])
        self.assertEqual(controller.integral_state, 0.1)

    def test_gain_snapshot_is_bound_to_submitted_decision(self) -> None:
        controller = self.controller()
        self.register(controller)
        controller.cfg = replace(controller.cfg, ki=0)
        self.assertTrue(self.ack(controller)[0])
        self.assertAlmostEqual(controller.integral_state, -0.0356481481481481)


if __name__ == "__main__":
    unittest.main()
