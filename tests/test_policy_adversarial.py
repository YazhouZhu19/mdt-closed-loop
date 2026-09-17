"""Malformed proposal handling and bounded access to model metadata."""

from __future__ import annotations

import json
import time
import unittest
from dataclasses import asdict, replace
from types import MappingProxyType
from typing import Any

from mdt_core.arbiter import resolve
from mdt_core.config import LearningConfig
from mdt_core.policy import PolicyRunner, context_hash, finite_action
from mdt_core.types import PolicyDecision


class UnsafeObject:
    def __repr__(self):
        raise AssertionError("unsupported action repr must not execute")

    def __deepcopy__(self, memo):
        raise AssertionError("unsupported action deepcopy must not execute")


class MetadataPolicy:
    version = "synthetic-adversarial-policy"

    def __init__(self, mode="mapping", calibration_mode="ok"):
        self.mode = mode
        self.calibration_mode = calibration_mode
        self.calibration_reads = 0

    @property
    def calibrated(self):
        self.calibration_reads += 1
        if self.calibration_mode == "slow":
            time.sleep(0.06)
        if self.calibration_mode == "error":
            raise RuntimeError("calibration metadata failed")
        return True

    def propose(self, ctx):
        action: Any = MappingProxyType({"gain": 0.4, "nested": (1, 2.0)})
        if self.mode == "unsafe":
            action = UnsafeObject()
        elif self.mode == "recursive":
            action = []
            action.append(action)
        elif self.mode == "huge":
            action = 10**500
        return PolicyDecision(action, 0.0, 1.0, True, self.version, context_hash(ctx))


class MalformedArbiterTests(unittest.TestCase):
    def test_malformed_fields_fall_back_without_exceptions(self):
        base = PolicyDecision(0.1, 0, 1, True, "v", "ctx")
        cases: list[tuple[str, Any, str]] = [
            ("logprob", [0], "invalid_logprob"),
            ("confidence", {"value": 1}, "invalid_confidence"),
            ("latency_ms", [1], "invalid_latency"),
            ("action", 10**500, "nonfinite_action"),
            ("action", {"value": 0.2}, "invalid_scalar_action"),
            ("action", True, "nonfinite_action"),
            ("error", UnsafeObject(), "invalid_error"),
            ("policy_version", None, "invalid_version"),
            ("context_hash", [], "invalid_context_hash"),
        ]
        for field, value, reason in cases:
            with self.subTest(field=field, reason=reason):
                output, trace = resolve(
                    replace(base, **{field: value}),
                    base,
                    1,
                    LearningConfig(mode="shadow"),
                )
                self.assertEqual(output, 0.1)
                self.assertEqual(trace.decision, reason)
                self.assertEqual(trace.lambda_mix, 0)
        invalid_quality: Any = []
        output, trace = resolve(
            base, base, invalid_quality, LearningConfig(mode="shadow")
        )
        self.assertEqual((output, trace.decision), (0.1, "unreliable_state"))

    def test_recursive_action_and_finite_overflow_are_rejected(self):
        recursive: list[Any] = []
        recursive.append(recursive)
        self.assertFalse(finite_action(recursive))
        base = PolicyDecision(-1e308, 0, 1, True, "v", "ctx")
        action = replace(base, action=1e308)
        output, trace = resolve(action, base, 1, LearningConfig(mode="shadow"))
        self.assertEqual(output, base.action)
        self.assertEqual(trace.decision, "nonfinite_disagreement")


class RunnerMetadataTests(unittest.TestCase):
    def test_immutable_mapping_normalized_for_approval_and_json(self):
        runner = PolicyRunner(MetadataPolicy(), 100)
        self.assertFalse(runner.calibrated)
        result = runner.propose({"time_seconds": 0})
        self.assertIsNone(result.error)
        self.assertEqual(result.action, {"gain": 0.4, "nested": [1, 2.0]})
        self.assertIs(type(result.action), dict)
        json.dumps(asdict(result), allow_nan=False)
        self.assertTrue(runner.calibrated)
        assert isinstance(runner.policy, MetadataPolicy)
        self.assertEqual(runner.policy.calibration_reads, 1)
        _ = runner.calibrated
        self.assertEqual(runner.policy.calibration_reads, 1)

    def test_unsupported_actions_never_execute_repr_or_deepcopy(self):
        for mode in ("unsafe", "recursive", "huge"):
            with self.subTest(mode=mode):
                runner = PolicyRunner(MetadataPolicy(mode=mode), 100)
                result = runner.propose({})
                self.assertIsNone(result.action)
                self.assertEqual(result.error, "nonfinite_action")
                self.assertFalse(runner.calibrated)
                json.dumps(asdict(result), allow_nan=False)

    def test_slow_calibration_property_is_bounded_and_late_result_cannot_activate(self):
        runner = PolicyRunner(MetadataPolicy(calibration_mode="slow"), 10)
        started = time.monotonic()
        result = runner.propose({})
        self.assertLess(time.monotonic() - started, 0.05)
        self.assertEqual(result.error, "inference_timeout")
        self.assertFalse(runner.calibrated)
        time.sleep(0.07)
        self.assertFalse(runner.calibrated)
        self.assertEqual(runner.propose({}).error, "inference_quarantined")

    def test_raising_calibration_property_falls_back(self):
        runner = PolicyRunner(MetadataPolicy(calibration_mode="error"), 100)
        result = runner.propose({})
        self.assertEqual(result.error, "inference_error:RuntimeError")
        self.assertFalse(runner.calibrated)


if __name__ == "__main__":
    unittest.main()
