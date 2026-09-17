"""Offline fitted, calibrated Gaussian likelihood p(features | arousal).

This is an observation component, not an end-to-end state predictor. It keeps
L1's one-dimensional Kalman transition and the public ``State`` interface. No
weights are bundled: callers supply explicitly split, weakly labelled data.
Calibration describes agreement with those labels, not clinical validity.

For a continuous state, ECE below means mean absolute *central interval*
coverage error over nominal levels 10%, ..., 90%. This definition must not be
confused with classification ECE. A separate untouched evaluation split gates
ECE < .05 and 90% coverage within [.85, .95] for both the observation and
chronological Kalman posterior. Calibration data fits observation variance.
This validation is specific to weak labels, frozen L1 settings and validated
cadences; the downstream arbiter's conservative mixture is not claimed to have
an independently calibrated probability distribution. This artifact validates
complete clean sequences only. ArousalEstimator quarantines it for the rest of
the session after missing/noisy windows, an unavailable baseline or unsupported
cadence/configuration. Mixed EDA-only/full-window sessions therefore continue
on the default estimator until their history is separately supported by a
future artifact; a later complete window does not silently reactivate learning.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from statistics import NormalDist
from typing import Any

import numpy as np

from mdt_core.config import DEFAULT, StateConfig
from mdt_core.l1_state import (
    EDA_KEYS,
    HRV_KEYS,
    INVERTED,
    DefaultEmission,
    EmissionObservation,
    IndividualBaseline,
    baseline_fingerprint,
    filter_fingerprint,
)
from mdt_core.types import Features, SignalQuality

FEATURE_KEYS = (*EDA_KEYS, *HRV_KEYS)
LEVELS = tuple(i / 10 for i in range(1, 10))


def _digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, allow_nan=False).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class EmissionExample:
    features: Features
    weak_label: float
    session_id: str
    process_scale: float = 1.0


@dataclass(frozen=True)
class CalibrationDiagnostics:
    training_count: int
    calibration_count: int
    evaluation_count: int
    ece: float
    coverage_90: float
    coverage_by_level: tuple[float, ...]
    evaluation_ood_fraction: float
    posterior_ece: float
    posterior_coverage_90: float
    posterior_coverage_by_level: tuple[float, ...]
    posterior_sequence_count: int
    # Digests identify the three exact datasets without shipping feature data.
    training_hash: str
    calibration_hash: str
    evaluation_hash: str
    definition: str = "mean_absolute_central_interval_coverage_error_10_to_90"

    @property
    def passed(self) -> bool:
        return (
            min(self.training_count, self.calibration_count, self.evaluation_count)
            >= 50
            and math.isfinite(self.ece)
            and self.ece < 0.05
            and 0.85 <= self.coverage_90 <= 0.95
            and self.evaluation_ood_fraction <= 0.05
            and math.isfinite(self.posterior_ece)
            and self.posterior_ece < 0.05
            and 0.85 <= self.posterior_coverage_90 <= 0.95
            and self.posterior_sequence_count >= 1
        )


@dataclass(frozen=True)
class LearnedGaussianEmission:
    """Frozen fit with explicit provenance, a content hash and OOD envelope.

    Features are individually standardized and HRV directions inverted using
    the same baseline as default L1. The conditional feature means are linear
    in arousal and the full residual covariance retains sensor correlations.
    ``projection`` and ``observation_variance`` summarize that likelihood for
    the scalar Kalman update. The variance scale is fit only on calibration
    data, and never updated during a session.
    """

    version: str
    baseline_hash: str
    filter_config_hash: str
    process_scale_range: tuple[float, float]
    intercept: tuple[float, ...]
    slope: tuple[float, ...]
    covariance: tuple[tuple[float, ...], ...]
    projection: tuple[float, ...]
    observation_variance: float
    variance_scale: float
    ood_center: tuple[float, ...]
    ood_precision: tuple[tuple[float, ...], ...]
    ood_threshold: float
    diagnostics: CalibrationDiagnostics
    model_hash: str = ""
    schema_version: str = "mdt.l1.gaussian-emission.v1"

    @property
    def calibrated(self) -> bool:
        return self.validation_error() is None

    def _payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("model_hash")
        return payload

    def validation_error(self) -> str | None:
        try:
            if self.schema_version != "mdt.l1.gaussian-emission.v1":
                return "unsupported_schema"
            if (
                not self.version
                or not self.baseline_hash
                or not self.filter_config_hash
            ):
                return "missing_version"
            if self.model_hash != _digest(self._payload()):
                return "artifact_hash_mismatch"
            if (
                len(self.process_scale_range) != 2
                or not all(math.isfinite(x) for x in self.process_scale_range)
                or not 0 < self.process_scale_range[0] <= self.process_scale_range[1]
            ):
                return "invalid_artifact"
            n = len(FEATURE_KEYS)
            vectors = (self.intercept, self.slope, self.projection, self.ood_center)
            if any(len(v) != n for v in vectors):
                return "invalid_artifact"
            matrices = (self.covariance, self.ood_precision)
            if any(len(m) != n or any(len(row) != n for row in m) for m in matrices):
                return "invalid_artifact"
            numbers = [x for v in vectors for x in v]
            numbers.extend(x for m in matrices for row in m for x in row)
            numbers.extend(
                (self.observation_variance, self.variance_scale, self.ood_threshold)
            )
            if not all(math.isfinite(x) for x in numbers):
                return "invalid_artifact"
            if (
                min(self.observation_variance, self.variance_scale, self.ood_threshold)
                <= 0
            ):
                return "invalid_artifact"
            if not self.diagnostics.passed:
                return "calibration_rejected"
        except (TypeError, ValueError, OverflowError):
            return "invalid_artifact"
        return None

    def filter_validation_error(
        self, cfg: StateConfig, process_scale: float
    ) -> str | None:
        if filter_fingerprint(cfg) != self.filter_config_hash:
            return "filter_config_mismatch"
        if (
            not self.process_scale_range[0]
            <= process_scale
            <= self.process_scale_range[1]
        ):
            return "process_scale_out_of_support"
        return None

    @classmethod
    def fit(
        cls,
        training: Sequence[EmissionExample],
        calibration: Sequence[EmissionExample],
        evaluation: Sequence[EmissionExample],
        *,
        baseline: IndividualBaseline,
        version: str,
        filter_config: StateConfig = DEFAULT.state,
    ) -> LearnedGaussianEmission:
        """Fit a likelihood and return its independently evaluated artifact.

        All splits need at least 50 clean complete examples. Session identifiers
        must be disjoint across splits; callers remain responsible for using
        correct provenance identifiers and independent held-out sessions.
        Each row's process_scale is the exact process-noise multiplier supplied
        to ArousalEstimator.update during replay (usually dt / hrv_step_s).
        Runtime cadence/configuration outside that evaluated support falls back.
        A failed evaluation returns an inspectable, non-activatable artifact.
        """
        if not baseline.is_ready or not isinstance(version, str) or not version.strip():
            raise ValueError("a ready baseline and a nonempty version are required")
        splits = (training, calibration, evaluation)
        if any(len(split) < 50 for split in splits):
            raise ValueError("each split requires at least 50 complete examples")
        session_sets = [{row.session_id for row in split} for split in splits]
        if any(not isinstance(s, str) or not s for ss in session_sets for s in ss):
            raise ValueError("nonempty session IDs are required")
        if any(session_sets[i] & session_sets[j] for i in range(3) for j in range(i)):
            raise ValueError(
                "training, calibration and evaluation sessions must differ"
            )

        for split in splits:
            window_ids = [(row.session_id, row.features.t) for row in split]
            if len(set(window_ids)) != len(window_ids):
                raise ValueError("duplicate windows cannot inflate calibration support")
            last_times: dict[str, float] = {}
            for row in split:
                if (
                    not math.isfinite(row.features.t)
                    or not math.isfinite(row.process_scale)
                    or row.process_scale <= 0
                ):
                    raise ValueError(
                        "finite timestamps and positive process scales are required"
                    )
                if row.features.t <= last_times.get(row.session_id, -math.inf):
                    raise ValueError("each session must be in chronological order")
                last_times[row.session_id] = row.features.t

        def prepare(rows: Sequence[EmissionExample]) -> tuple[np.ndarray, np.ndarray]:
            values, labels = [], []
            for row in rows:
                if (
                    row.features.quality is not SignalQuality.OK
                    or not math.isfinite(row.weak_label)
                    or not 0 <= row.weak_label <= 1
                ):
                    raise ValueError(
                        "fit requires clean observations and labels in [0,1]"
                    )
                zs = []
                for key in FEATURE_KEYS:
                    value = getattr(row.features, key)
                    z = baseline.z(key, value)
                    if z is None or not math.isfinite(z):
                        raise ValueError("fit requires finite complete observations")
                    zs.append(-z if key in INVERTED else z)
                values.append(zs)
                labels.append(row.weak_label)
            return np.asarray(values), np.asarray(labels)

        (x, y), (cx, cy), (ex, ey) = [prepare(split) for split in splits]
        if float(np.std(y)) < 0.05:
            raise ValueError("training labels need arousal variation")
        design = np.column_stack((np.ones(len(y)), y - 0.5))
        coefficients, *_ = np.linalg.lstsq(design, x, rcond=None)
        intercept, slope = coefficients
        residual = x - design @ coefficients
        covariance = residual.T @ residual / (len(y) - 2)
        ridge = max(float(np.trace(covariance)) / len(FEATURE_KEYS) * 1e-6, 1e-8)
        covariance += np.eye(len(FEATURE_KEYS)) * ridge
        precision_slope = np.linalg.solve(covariance, slope)
        information = float(slope @ precision_slope)
        if not math.isfinite(information) or information <= 1e-8:
            raise ValueError("training features carry no usable arousal information")
        projection = precision_slope / information
        base_variance = 1.0 / information
        cal_prediction = np.clip(0.5 + (cx - intercept) @ projection, 0, 1)
        variance_scale = max(
            float(np.mean((cal_prediction - cy) ** 2)) / base_variance, 1e-6
        )
        observation_variance = base_variance * variance_scale
        covariance *= variance_scale
        prediction = np.clip(0.5 + (ex - intercept) @ projection, 0, 1)
        error = np.abs(prediction - ey)
        normal = NormalDist()
        coverage = tuple(
            float(
                np.mean(
                    error
                    <= normal.inv_cdf((1 + level) / 2) * math.sqrt(observation_variance)
                )
            )
            for level in LEVELS
        )
        ece = sum(abs(actual - level) for actual, level in zip(coverage, LEVELS)) / len(
            LEVELS
        )
        center = x.mean(axis=0)
        marginal_covariance = (
            np.cov(x, rowvar=False) + np.eye(len(FEATURE_KEYS)) * ridge
        )
        ood_precision = np.linalg.inv(marginal_covariance)
        distances = np.einsum("ij,jk,ik->i", x - center, ood_precision, x - center)
        # Empirical training envelope; evaluation independently checks whether
        # it rejects more than 5% of otherwise representative held-out inputs.
        threshold = (
            max(float(len(FEATURE_KEYS)), float(np.quantile(distances, 0.995))) * 1.5
        )
        evaluation_distances = np.einsum(
            "ij,jk,ik->i", ex - center, ood_precision, ex - center
        )

        # Validate the actual Kalman posterior on untouched, chronological
        # sessions as well as the scalar emission. Each session starts with
        # exactly the original estimator's (x=.5, p=1) initialization. OOD
        # observations follow the same frozen default emission fallback.
        state_by_session: dict[str, tuple[float, float]] = {}
        posterior_errors, posterior_variances = [], []
        default_emission = DefaultEmission()
        for row, features, measurement, distance in zip(
            evaluation, ex, prediction, evaluation_distances
        ):
            state, variance = state_by_session.get(row.session_id, (0.5, 1.0))
            variance += filter_config.kalman_q * row.process_scale
            obs_variance = observation_variance
            if distance > threshold:
                default = default_emission.observe(
                    dict(zip(FEATURE_KEYS, features)),
                    row.features.quality,
                    filter_config,
                )
                assert default is not None
                measurement, obs_variance = default.measurement, default.variance
            gain = variance / (variance + obs_variance)
            state += gain * (measurement - state)
            variance *= 1.0 - gain
            state_by_session[row.session_id] = (state, variance)
            posterior_errors.append(abs(state - row.weak_label))
            posterior_variances.append(variance)
        posterior_errors_array = np.asarray(posterior_errors)
        posterior_std = np.sqrt(posterior_variances)
        posterior_coverage = tuple(
            float(
                np.mean(
                    posterior_errors_array
                    <= normal.inv_cdf((1 + level) / 2) * posterior_std
                )
            )
            for level in LEVELS
        )
        posterior_ece = sum(
            abs(actual - level) for actual, level in zip(posterior_coverage, LEVELS)
        ) / len(LEVELS)
        process_scales = [
            row.process_scale for split in (calibration, evaluation) for row in split
        ]

        def dataset_hash(rows: Sequence[EmissionExample]) -> str:
            return _digest(
                [
                    {
                        "session_id": row.session_id,
                        "label": row.weak_label,
                        "process_scale": row.process_scale,
                        "features": asdict(row.features),
                    }
                    for row in rows
                ]
            )

        diagnostics = CalibrationDiagnostics(
            training_count=len(training),
            calibration_count=len(calibration),
            evaluation_count=len(evaluation),
            ece=ece,
            coverage_90=coverage[-1],
            coverage_by_level=coverage,
            evaluation_ood_fraction=float(np.mean(evaluation_distances > threshold)),
            posterior_ece=posterior_ece,
            posterior_coverage_90=posterior_coverage[-1],
            posterior_coverage_by_level=posterior_coverage,
            posterior_sequence_count=len(state_by_session),
            training_hash=dataset_hash(training),
            calibration_hash=dataset_hash(calibration),
            evaluation_hash=dataset_hash(evaluation),
        )
        model = cls(
            version=version,
            baseline_hash=baseline_fingerprint(baseline),
            filter_config_hash=filter_fingerprint(filter_config),
            process_scale_range=(min(process_scales), max(process_scales)),
            intercept=tuple(intercept),
            slope=tuple(slope),
            covariance=tuple(tuple(row) for row in covariance),
            projection=tuple(projection),
            observation_variance=observation_variance,
            variance_scale=variance_scale,
            ood_center=tuple(center),
            ood_precision=tuple(tuple(row) for row in ood_precision),
            ood_threshold=threshold,
            diagnostics=diagnostics,
        )
        return replace(model, model_hash=_digest(model._payload()))

    def observe(
        self, z_scores: dict[str, float], quality: SignalQuality, cfg: StateConfig
    ) -> EmissionObservation | None:
        # This method is also safe when called without ArousalEstimator.
        if (
            self.validation_error() is not None
            or quality is not SignalQuality.OK
            or filter_fingerprint(cfg) != self.filter_config_hash
        ):
            return None
        if any(
            key not in z_scores or not math.isfinite(z_scores[key])
            for key in FEATURE_KEYS
        ):
            return None
        x = np.asarray([z_scores[key] for key in FEATURE_KEYS])
        centered = x - self.ood_center
        if (
            float(centered @ np.asarray(self.ood_precision) @ centered)
            > self.ood_threshold
        ):
            return None
        measurement = float(0.5 + (x - self.intercept) @ self.projection)
        return EmissionObservation(
            measurement=max(0.0, min(1.0, measurement)),
            variance=self.observation_variance
            * (2 if quality is SignalQuality.NOISY else 1),
            confidence=0.5 if quality is SignalQuality.NOISY else 1.0,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> LearnedGaussianEmission:
        """Load an exported artifact; invalid or uncalibrated artifacts cannot run."""
        values = dict(payload)
        values["process_scale_range"] = tuple(values["process_scale_range"])
        for name in ("intercept", "slope", "projection", "ood_center"):
            values[name] = tuple(values[name])
        for name in ("covariance", "ood_precision"):
            values[name] = tuple(tuple(row) for row in values[name])
        diagnostics = dict(values["diagnostics"])
        diagnostics["coverage_by_level"] = tuple(diagnostics["coverage_by_level"])
        diagnostics["posterior_coverage_by_level"] = tuple(
            diagnostics["posterior_coverage_by_level"]
        )
        values["diagnostics"] = CalibrationDiagnostics(**diagnostics)
        model = cls(**values)
        error = model.validation_error()
        if error is not None:
            raise ValueError("invalid emission artifact: " + error)
        return model
