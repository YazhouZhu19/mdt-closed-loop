"""Unit tests using only deterministic synthetic physiological inputs."""

from __future__ import annotations

import math
import unittest

from mdt_core.config import DEFAULT, SignalConfig
from mdt_core.l0_signal import extract
from mdt_core.l1_state import ArousalEstimator, IndividualBaseline
from mdt_core.types import Features, MusicParams, SignalQuality
from tests.synthetic import ready_baseline, synthetic_window


class SignalTests(unittest.TestCase):
    def test_extracts_finite_features(self) -> None:
        features = extract(synthetic_window(0), DEFAULT.signal)
        self.assertIs(features.quality, SignalQuality.OK)
        values = [
            features.scl_slope,
            features.scr_rate,
            features.rmssd,
            features.hf_power,
            features.sd1,
        ]
        self.assertTrue(
            all(value is not None and math.isfinite(value) for value in values)
        )

    def test_small_nan_gap_is_interpolated_and_downgraded(self) -> None:
        features = extract(synthetic_window(0, nan_count=10), DEFAULT.signal)
        self.assertIs(features.quality, SignalQuality.NOISY)
        slope = features.scl_slope
        self.assertIsNotNone(slope)
        assert slope is not None
        self.assertTrue(math.isfinite(slope))

    def test_large_nan_gap_drops_eda_but_keeps_hrv(self) -> None:
        features = extract(synthetic_window(0, nan_count=200), DEFAULT.signal)
        self.assertIs(features.quality, SignalQuality.NOISY)
        self.assertIsNone(features.scl_slope)
        self.assertIsNotNone(features.rmssd)

    def test_invalid_sampling_rate_is_rejected(self) -> None:
        window = synthetic_window(0)
        for invalid in (-1, 0):
            window.eda_fs = invalid
            with self.assertRaises(ValueError):
                extract(window, DEFAULT.signal)

    def test_configuration_is_validated(self) -> None:
        with self.assertRaises(ValueError):
            SignalConfig(eda_step_s=11, eda_window_s=10)

    def test_music_parameters_reject_non_finite_or_invalid_values(self) -> None:
        with self.assertRaises(ValueError):
            MusicParams(tempo=math.nan)
        with self.assertRaises(ValueError):
            MusicParams(layer_mask=0)
        with self.assertRaises(ValueError):
            MusicParams(dynamics=1.1)


class StateTests(unittest.TestCase):
    def test_baseline_requires_all_features(self) -> None:
        baseline = IndividualBaseline(mu={"rmssd": 40}, sigma={"rmssd": 10})
        self.assertFalse(baseline.is_ready)

    def test_extreme_z_score_is_numerically_stable(self) -> None:
        baseline = ready_baseline()
        baseline.sigma = {key: 1e-6 for key in baseline.sigma}
        state = ArousalEstimator(baseline, DEFAULT.state).update(
            Features(t=0, scl_slope=-1, scr_rate=-1, rmssd=1, hf_power=1, sd1=1)
        )
        self.assertTrue(math.isfinite(state.arousal))
        self.assertGreaterEqual(state.arousal, 0)
        self.assertLessEqual(state.arousal, 1)

    def test_multimodal_estimate_has_full_confidence(self) -> None:
        state = ArousalEstimator(ready_baseline(), DEFAULT.state).update(
            Features(t=0, scl_slope=0.004, scr_rate=2, rmssd=25, hf_power=500, sd1=18)
        )
        self.assertEqual(state.confidence, 1.0)
        self.assertGreater(state.arousal, 0.5)

    def test_kalman_process_scale_is_validated(self) -> None:
        with self.assertRaises(ValueError):
            ArousalEstimator(ready_baseline(), DEFAULT.state).update(
                Features(t=0), process_scale=0
            )


if __name__ == "__main__":
    unittest.main()
