"""Synthetic calibration checks; no weights or clinical observations are bundled."""

from __future__ import annotations

import json
import math
import tempfile
import threading
import time
import unittest
from dataclasses import asdict, replace
from typing import Any, ClassVar

import numpy as np

from mdt_core.agents.l1_emission import (
    FEATURE_KEYS,
    EmissionExample,
    LearnedGaussianEmission,
)
from mdt_core.config import DEFAULT, LearningConfig
from mdt_core.l1_state import (
    INVERTED,
    ArousalEstimator,
    EmissionObservation,
    baseline_fingerprint,
)
from mdt_core.l4_l6 import SessionRecorder
from mdt_core.learning import LearningRuntime, make_context
from mdt_core.types import Arm, Features, SignalQuality
from tests.synthetic import ready_baseline


def examples(
    seed: int, session: str, noise: float = 0.4, *, independent: bool = False
) -> list[EmissionExample]:
    rng = np.random.default_rng(seed)
    baseline = ready_baseline()
    result = []
    label = 0.5
    for i in range(1000):
        label = (
            float(rng.uniform(0.2, 0.8))
            if independent
            else max(0.2, min(0.8, label + float(rng.normal(0, 0.1))))
        )
        zs = np.array([0.2, -0.1, 0.1, 0, 0.05]) + np.array([3, 2, 4, 3, 2]) * (
            label - 0.5
        )
        zs += rng.normal(0, noise, 5)
        values: dict[str, Any] = {
            key: float(
                baseline.mu[key]
                + baseline.sigma[key] * z * (-1 if key in INVERTED else 1)
            )
            for key, z in zip(FEATURE_KEYS, zs)
        }
        result.append(EmissionExample(Features(t=float(i), **values), label, session))
    return result


class EmissionTests(unittest.TestCase):
    training: ClassVar[list[EmissionExample]]
    calibration: ClassVar[list[EmissionExample]]
    evaluation: ClassVar[list[EmissionExample]]
    model: ClassVar[LearnedGaussianEmission]

    @classmethod
    def setUpClass(cls) -> None:
        cls.training = examples(13, "synthetic-training")
        cls.calibration = examples(29, "synthetic-calibration")
        cls.evaluation = examples(47, "synthetic-evaluation")
        cls.model = LearnedGaussianEmission.fit(
            cls.training,
            cls.calibration,
            cls.evaluation,
            baseline=ready_baseline(),
            version="synthetic-test-only-v1",
        )

    def test_default_matches_pre_extraction_float_bits(self) -> None:
        # Golden trajectory captured from the original estimator before extraction.
        inputs = [
            (
                Features(
                    t=0, scl_slope=0.004, scr_rate=2, rmssd=25, hf_power=500, sd1=18
                ),
                1.0,
            ),
            (
                Features(
                    t=2,
                    scl_slope=0.001,
                    scr_rate=0,
                    rmssd=50,
                    hf_power=1500,
                    sd1=32,
                    quality=SignalQuality.NOISY,
                ),
                1.0,
            ),
            (Features(t=4, quality=SignalQuality.LOST), 3.2),
            (Features(t=8, scl_slope=0.003), 2.0),
            (Features(t=10), 1.0),
            (Features(t=12, rmssd=1e9), 1.0),
            (
                Features(
                    t=14, scl_slope=0.002, scr_rate=0, rmssd=40, hf_power=1000, sd1=28
                ),
                1.0,
            ),
        ]
        expected = [
            ("0x1.7f3cc0f2d4173p-1", "0x1.0000000000000p+0", "0x1.9a69a69a69a68p-3"),
            ("0x1.43f96ee237181p-1", "0x1.0000000000000p-1", "0x1.2f46abc5c5241p-3"),
            ("0x1.43f96ee237181p-1", "0x0.0p+0", "0x1.70cfe3118bcc0p-3"),
            ("0x1.50ff0e5ecc9a8p-1", "0x1.999999999999ap-3", "0x1.c737a0fd6e931p-4"),
            ("0x1.50ff0e5ecc9a8p-1", "0x0.0p+0", "0x1.f02d638ccabc0p-4"),
            ("0x1.ba1e02cddcf24p-2", "0x1.999999999999ap-3", "0x1.605358d1f9487p-4"),
            ("0x1.cd825785ae526p-2", "0x1.0000000000000p+0", "0x1.1c26e4ba140e3p-4"),
        ]
        estimator = ArousalEstimator(ready_baseline(), DEFAULT.state)
        for (feats, scale), golden in zip(inputs, expected):
            state = estimator.update(feats, scale)
            self.assertEqual(
                tuple(
                    value.hex()
                    for value in (state.arousal, state.confidence, state.uncertainty)
                ),
                golden,
            )

    def test_calibration_evaluates_heldout_uncertainty(self) -> None:
        self.assertTrue(self.model.calibrated, asdict(self.model.diagnostics))
        self.assertLess(self.model.diagnostics.ece, 0.05)
        self.assertGreaterEqual(self.model.diagnostics.coverage_90, 0.85)
        self.assertLessEqual(self.model.diagnostics.coverage_90, 0.95)
        estimator = ArousalEstimator(
            ready_baseline(),
            DEFAULT.state,
            emission_model=self.model,
            emission_timeout_s=1,
        )
        state = estimator.update(self.evaluation[0].features)
        self.assertEqual(estimator.last_emission_status, "learned")
        self.assertEqual(estimator.last_emission_hash, self.model.model_hash)
        self.assertGreater(state.uncertainty, 0)
        self.assertEqual(
            set(asdict(state)),
            {"t", "arousal", "confidence", "z_scores", "uncertainty"},
        )

    def test_heldout_posterior_diagnostics_match_running_filter(self) -> None:
        estimator = ArousalEstimator(
            ready_baseline(),
            DEFAULT.state,
            emission_model=self.model,
            emission_timeout_s=1,
        )
        covered = 0
        for row in self.evaluation:
            state = estimator.update(row.features, row.process_scale)
            covered += abs(
                state.arousal - row.weak_label
            ) <= 1.6448536269514722 * math.sqrt(state.uncertainty)
        self.assertAlmostEqual(
            covered / len(self.evaluation), self.model.diagnostics.posterior_coverage_90
        )

    def test_runtime_shadow_identity_and_active_variance_safeguard(self) -> None:
        for mode in ("shadow", "autonomous"):
            cfg = replace(
                DEFAULT,
                learning=LearningConfig(
                    mode=mode,
                    enable_l1=True,
                    trial_id="synthetic-test-only",
                    policy_versions=(("l1", self.model.model_hash),),
                    inference_timeout_ms=1000,
                ),
            )
            baseline = ready_baseline()
            default_estimator = ArousalEstimator(baseline, cfg.state)
            with tempfile.TemporaryDirectory() as directory:
                recorder = SessionRecorder(
                    "synthetic", "synthetic", Arm.FULL_LOOP, directory
                )
                runtime = LearningRuntime(
                    cfg, baseline, recorder, {}, emission_model=self.model
                )
                changed = False
                for row in self.evaluation[:20]:
                    default = default_estimator.update(row.features, row.process_scale)
                    ctx = make_context(
                        "synthetic",
                        baseline,
                        default,
                        cfg,
                        completed_sessions=0,
                        previous_outcome=None,
                    )
                    actual = runtime.estimate(
                        row.features, row.process_scale, default, ctx
                    )
                    if mode == "shadow":
                        self.assertEqual(actual, default)
                        self.assertEqual(recorder.policy_decisions[-1]["lambda_mix"], 0)
                    else:
                        changed |= actual.arousal != default.arousal
                        self.assertGreaterEqual(actual.uncertainty, default.uncertainty)
                if mode == "autonomous":
                    self.assertTrue(changed)
                self.assertTrue(
                    all(row["calibrated"] for row in recorder.policy_decisions)
                )
                # After already using learned estimates, unvalidated history
                # must select the independent default filter, then stay there.
                for features in (
                    Features(t=20.0, scl_slope=0.003),
                    self.evaluation[21].features,
                ):
                    default = default_estimator.update(features)
                    ctx = make_context(
                        "synthetic",
                        baseline,
                        default,
                        cfg,
                        completed_sessions=0,
                        previous_outcome=None,
                    )
                    self.assertEqual(
                        runtime.estimate(features, 1.0, default, ctx), default
                    )
                    self.assertEqual(recorder.policy_decisions[-1]["lambda_mix"], 0)
                assert runtime.emission_estimator is not None
                self.assertEqual(
                    runtime.emission_estimator.last_emission_status,
                    "fallback:disabled_after_unvalidated_history",
                )

    def test_wrong_uncertainty_rejected_despite_accurate_mean(self) -> None:
        # Evaluation labels are estimated very accurately, but reported intervals
        # are too wide: coverage near 100% must also fail the calibration gate.
        model = LearnedGaussianEmission.fit(
            self.training,
            self.calibration,
            examples(31, "evaluation-low-noise", noise=0.01),
            baseline=ready_baseline(),
            version="bad-uncertainty",
        )
        self.assertFalse(model.calibrated)
        self.assertGreater(model.diagnostics.coverage_90, 0.95)
        estimator = ArousalEstimator(
            ready_baseline(), DEFAULT.state, emission_model=model, emission_timeout_s=1
        )
        state = estimator.update(self.evaluation[0].features)
        default = ArousalEstimator(ready_baseline(), DEFAULT.state).update(
            self.evaluation[0].features
        )
        self.assertEqual(state, default)
        self.assertEqual(
            estimator.last_emission_status, "fallback:calibration_rejected"
        )

    def test_posterior_gate_rejects_unmatched_temporal_dynamics(self) -> None:
        model = LearnedGaussianEmission.fit(
            self.training,
            self.calibration,
            examples(47, "temporal-shift", independent=True),
            baseline=ready_baseline(),
            version="temporal-shift",
        )
        self.assertLess(model.diagnostics.ece, 0.05)
        self.assertGreaterEqual(model.diagnostics.coverage_90, 0.85)
        self.assertLess(model.diagnostics.posterior_coverage_90, 0.85)
        self.assertFalse(model.calibrated)

    def test_filter_configuration_and_cadence_require_revalidation(self) -> None:
        cases = [
            (replace(DEFAULT.state, kalman_q=0.001), 1.0, "filter_config_mismatch"),
            (DEFAULT.state, 0.133, "process_scale_out_of_support"),
        ]
        for config, scale, reason in cases:
            estimator = ArousalEstimator(
                ready_baseline(),
                config,
                emission_model=self.model,
                emission_timeout_s=1,
            )
            state = estimator.update(self.evaluation[0].features, scale)
            expected = ArousalEstimator(ready_baseline(), config).update(
                self.evaluation[0].features, scale
            )
            self.assertEqual(state, expected)
            self.assertEqual(estimator.last_emission_status, "fallback:" + reason)

    def test_splits_cannot_share_sessions(self) -> None:
        with self.assertRaisesRegex(ValueError, "sessions must differ"):
            LearnedGaussianEmission.fit(
                self.training,
                self.training,
                self.evaluation,
                baseline=ready_baseline(),
                version="bad-split",
            )

    def test_duplicate_windows_cannot_inflate_support(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate windows"):
            LearnedGaussianEmission.fit(
                self.training,
                [self.calibration[0]] * 50,
                self.evaluation,
                baseline=ready_baseline(),
                version="duplicate-data",
            )

    def test_missing_and_ood_fall_back(self) -> None:
        for features in (
            Features(t=0, scl_slope=0.004),
            Features(
                t=0, scl_slope=1e8, scr_rate=1e8, rmssd=1e8, hf_power=1e8, sd1=1e8
            ),
        ):
            estimator = ArousalEstimator(
                ready_baseline(),
                DEFAULT.state,
                emission_model=self.model,
                emission_timeout_s=1,
            )
            state = estimator.update(features)
            self.assertEqual(
                state,
                ArousalEstimator(ready_baseline(), DEFAULT.state).update(features),
            )
            expected_status = (
                "fallback:unvalidated_feature_history"
                if features.rmssd is None
                else "fallback:missing_or_ood"
            )
            self.assertEqual(estimator.last_emission_status, expected_status)

    def test_unvalidated_history_never_resumes_during_session(self) -> None:
        good = self.evaluation[0].features
        cases = [
            (
                replace(good, quality=SignalQuality.NOISY),
                1.0,
                "unvalidated_signal_quality",
            ),
            (Features(t=0, scl_slope=0.003), 1.0, "unvalidated_feature_history"),
            (
                Features(t=0, quality=SignalQuality.LOST),
                1.0,
                "baseline_or_signal_unavailable",
            ),
            (good, 0.133, "process_scale_out_of_support"),
        ]
        for features, scale, reason in cases:
            estimator = ArousalEstimator(
                ready_baseline(),
                DEFAULT.state,
                emission_model=self.model,
                emission_timeout_s=1,
            )
            reference = ArousalEstimator(ready_baseline(), DEFAULT.state)
            self.assertEqual(
                estimator.update(features, scale), reference.update(features, scale)
            )
            self.assertEqual(estimator.last_emission_status, "fallback:" + reason)
            for row in self.evaluation[1:4]:
                self.assertEqual(
                    estimator.update(row.features), reference.update(row.features)
                )
                self.assertEqual(
                    estimator.last_emission_status,
                    "fallback:disabled_after_unvalidated_history",
                )

    def test_unavailable_baseline_before_first_observation_quarantines(self) -> None:
        baseline = ready_baseline()
        saved = baseline.mu.pop("rmssd")
        estimator = ArousalEstimator(
            baseline, DEFAULT.state, emission_model=self.model, emission_timeout_s=1
        )
        estimator.update(self.evaluation[0].features)
        self.assertEqual(
            estimator.last_emission_status, "fallback:baseline_or_signal_unavailable"
        )
        baseline.mu["rmssd"] = saved
        estimator.update(self.evaluation[1].features)
        self.assertEqual(
            estimator.last_emission_status,
            "fallback:disabled_after_unvalidated_history",
        )

    def test_hash_baseline_and_version_mismatch_fall_back(self) -> None:
        baseline = ready_baseline()
        baseline.mu["rmssd"] += 1
        cases = [
            (self.model, ready_baseline(), "wrong-hash", "version_mismatch"),
            (self.model, baseline, None, "baseline_mismatch"),
            (
                replace(self.model, observation_variance=0.0000001),
                ready_baseline(),
                None,
                "artifact_hash_mismatch",
            ),
        ]
        for model, baseline, expected_hash, reason in cases:
            estimator = ArousalEstimator(
                baseline,
                DEFAULT.state,
                emission_model=model,
                expected_emission_hash=expected_hash,
                emission_timeout_s=1,
            )
            state = estimator.update(self.evaluation[0].features)
            self.assertEqual(
                state,
                ArousalEstimator(baseline, DEFAULT.state).update(
                    self.evaluation[0].features
                ),
            )
            self.assertEqual(estimator.last_emission_status, "fallback:" + reason)

    def test_artifact_roundtrip_and_corruption(self) -> None:
        encoded = json.loads(json.dumps(self.model.to_dict()))
        self.assertEqual(LearnedGaussianEmission.from_dict(encoded), self.model)
        encoded["projection"][0] += 1
        with self.assertRaisesRegex(ValueError, "artifact_hash_mismatch"):
            LearnedGaussianEmission.from_dict(encoded)

    def test_timeout_is_bounded_and_quarantined(self) -> None:
        release = threading.Event()

        class SlowModel:
            version = "test-v1"
            model_hash = "test-hash"
            baseline_hash = baseline_fingerprint(ready_baseline())
            calls = 0

            def validation_error(self):
                return None

            def observe(self, *args):
                self.calls += 1
                release.wait(2)
                return EmissionObservation(0.8, 0.1, 1.0)

        model = SlowModel()
        estimator = ArousalEstimator(
            ready_baseline(),
            DEFAULT.state,
            emission_model=model,
            emission_timeout_s=0.005,
        )
        try:
            start = time.monotonic()
            estimator.update(self.evaluation[0].features)
            self.assertLess(time.monotonic() - start, 0.5)
            self.assertEqual(estimator.last_emission_status, "fallback:timeout")
            estimator.update(self.evaluation[1].features)
            self.assertEqual(
                estimator.last_emission_status, "fallback:disabled_after_timeout"
            )
            self.assertEqual(model.calls, 1)
        finally:
            release.set()

    def test_malformed_output_is_rejected(self) -> None:
        class InvalidModel:
            version = "test-v1"
            model_hash = "test-hash"
            baseline_hash = baseline_fingerprint(ready_baseline())

            def validation_error(self):
                return None

            def observe(self, *args):
                return EmissionObservation(float("nan"), -1.0, 1.0)

        estimator = ArousalEstimator(
            ready_baseline(),
            DEFAULT.state,
            emission_model=InvalidModel(),
            emission_timeout_s=1,
        )
        estimator.update(self.evaluation[0].features)
        self.assertEqual(estimator.last_emission_status, "fallback:invalid_output")


if __name__ == "__main__":
    unittest.main()
