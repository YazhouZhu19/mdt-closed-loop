"""L1 状态估计。

关键约束：只输出唤醒度。EDA/HRV 无法可靠区分正负效价，
效价一律从自评获取，不在此处建模。
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
from dataclasses import asdict, dataclass, field
from typing import Protocol

from .config import StateConfig
from .types import Features, SignalQuality, State

EDA_KEYS = ("scl_slope", "scr_rate")
HRV_KEYS = ("rmssd", "hf_power", "sd1")
REQUIRED_KEYS = frozenset((*EDA_KEYS, *HRV_KEYS))
# HRV 副交感指标与唤醒度反向
INVERTED = {"rmssd", "hf_power", "sd1"}


@dataclass
class IndividualBaseline:
    """个体常模。用前 N 次会话的静息段拟合，不使用人群常模。"""

    mu: dict[str, float] = field(default_factory=dict)
    sigma: dict[str, float] = field(default_factory=dict)
    sessions_collected: int = 0
    _acc: dict[str, list[float]] = field(default_factory=dict)

    @property
    def is_ready(self) -> bool:
        if not REQUIRED_KEYS.issubset(self.mu) or not REQUIRED_KEYS.issubset(
            self.sigma
        ):
            return False
        return all(
            math.isfinite(self.mu[key])
            and math.isfinite(self.sigma[key])
            and self.sigma[key] > 0
            for key in REQUIRED_KEYS
        )

    @property
    def accumulated_value_count(self) -> int:
        """Number of valid scalar feature values collected for calibration."""
        return sum(len(values) for values in self._acc.values())

    def accumulate(self, feats: Features) -> None:
        if feats.quality is not SignalQuality.OK:
            return
        for k, v in feats.as_dict().items():
            if v is not None and math.isfinite(v):
                self._acc.setdefault(k, []).append(v)

    def finalize_session(self, cfg: StateConfig) -> None:
        self.sessions_collected += 1
        if self.sessions_collected < cfg.baseline_sessions_required:
            return
        for k, vals in self._acc.items():
            if len(vals) < 5:
                continue
            n = len(vals)
            mean = sum(vals) / n
            var = sum((x - mean) ** 2 for x in vals) / max(n - 1, 1)
            self.mu[k] = mean
            self.sigma[k] = max(math.sqrt(var), cfg.min_sigma)

    def z(self, key: str, value: float) -> float | None:
        if key not in self.mu or value is None or not math.isfinite(value):
            return None
        return (value - self.mu[key]) / self.sigma[key]


def _squash(z: float, max_abs_z: float = 12.0) -> float:
    """Z 分数映射到 [0,1]，±2.5 SD 覆盖大部分动态范围。"""
    scaled = max(-max_abs_z, min(max_abs_z, z)) / 1.25
    if scaled >= 0:
        return 1.0 / (1.0 + math.exp(-scaled))
    exp_scaled = math.exp(scaled)
    return exp_scaled / (1.0 + exp_scaled)


def baseline_fingerprint(baseline: IndividualBaseline) -> str:
    """Identify the individual normalization used to fit an emission model."""
    payload = {"mu": baseline.mu, "sigma": baseline.sigma}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, allow_nan=False).encode("utf-8")
    ).hexdigest()


def filter_fingerprint(cfg: StateConfig) -> str:
    """Bind posterior calibration to the complete frozen L1 configuration."""
    return hashlib.sha256(
        json.dumps(asdict(cfg), sort_keys=True, allow_nan=False).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class EmissionObservation:
    """A scalar likelihood summarized for the existing one-dimensional filter."""

    measurement: float
    variance: float
    confidence: float


class EmissionModel(Protocol):
    """Optional frozen, offline-trained observation component; no state filter."""

    @property
    def version(self) -> str: ...

    @property
    def model_hash(self) -> str: ...

    @property
    def baseline_hash(self) -> str: ...

    def validation_error(self) -> str | None: ...

    def observe(
        self, z_scores: dict[str, float], quality: SignalQuality, cfg: StateConfig
    ) -> EmissionObservation | None: ...


class DefaultEmission:
    """Original fixed EDA/HRV fusion, extracted without numerical changes."""

    def observe(
        self, z_scores: dict[str, float], quality: SignalQuality, cfg: StateConfig
    ) -> EmissionObservation | None:
        eda_z = [z_scores[k] for k in EDA_KEYS if k in z_scores]
        hrv_z = [z_scores[k] for k in HRV_KEYS if k in z_scores]
        if not eda_z and not hrv_z:
            return None
        parts, weights = [], []
        if eda_z:
            parts.append(sum(eda_z) / len(eda_z))
            weights.append(cfg.eda_weight)
        if hrv_z:
            parts.append(sum(hrv_z) / len(hrv_z))
            weights.append(cfg.hrv_weight)
        fused_z = sum(p * w for p, w in zip(parts, weights)) / sum(weights)
        measurement = _squash(fused_z, cfg.max_abs_z)
        r = cfg.kalman_r * (2.0 if quality is SignalQuality.NOISY else 1.0)
        coverage = len(z_scores) / (len(EDA_KEYS) + len(HRV_KEYS))
        confidence = coverage * (0.5 if quality is SignalQuality.NOISY else 1.0)
        return EmissionObservation(measurement, r, confidence)


class ArousalEstimator:
    """EDA/HRV observations with the original one-dimensional Kalman filter.

    Learned emissions are optional and must pass their held-out calibration
    gate. Timeout, invalid output, normalization/version changes and OOD inputs
    all select the original observation. A timed-out worker is daemonized and
    disabled for the remainder of this estimator's lifetime, bounding workers.
    Missing/noisy observations, unavailable baselines and unsupported filter
    configurations/cadences quarantine learning for the rest of the session:
    later reliable windows cannot undo an unvalidated posterior history.
    """

    def __init__(
        self,
        baseline: IndividualBaseline,
        cfg: StateConfig,
        *,
        emission_model: EmissionModel | None = None,
        emission_timeout_s: float = 0.02,
        expected_emission_hash: str | None = None,
    ):
        if not math.isfinite(emission_timeout_s) or emission_timeout_s <= 0:
            raise ValueError("emission_timeout_s must be finite and > 0")
        self.baseline = baseline
        self.cfg = cfg
        self.emission_model = emission_model
        self.emission_timeout_s = emission_timeout_s
        self._expected_emission_hash = (
            expected_emission_hash
            if expected_emission_hash is not None
            else getattr(emission_model, "model_hash", None)
        )
        self._expected_emission_version = getattr(emission_model, "version", None)
        self._emission_disabled_reason: str | None = None
        self._default_emission = DefaultEmission()
        self.last_emission_status = "default"
        self.last_emission_version: str | None = None
        self.last_emission_hash: str | None = None
        self._x = 0.5
        self._p = 1.0

    def _learned_observation(
        self, zs: dict[str, float], quality: SignalQuality, process_scale: float
    ) -> EmissionObservation | None:
        model = self.emission_model
        if model is None:
            return None
        if self._emission_disabled_reason is not None:
            self.last_emission_status = (
                "fallback:disabled_after_" + self._emission_disabled_reason
            )
            return None
        if quality is not SignalQuality.OK or not REQUIRED_KEYS.issubset(zs):
            self._emission_disabled_reason = "unvalidated_history"
            self.last_emission_status = (
                "fallback:unvalidated_signal_quality"
                if quality is not SignalQuality.OK
                else "fallback:unvalidated_feature_history"
            )
            return None
        completed = threading.Event()
        result: list[tuple[EmissionObservation | None, str]] = []

        def infer() -> None:
            try:
                if (
                    model.model_hash != self._expected_emission_hash
                    or model.version != self._expected_emission_version
                    or not model.version
                    or not model.model_hash
                ):
                    result.append((None, "fallback:version_mismatch"))
                elif model.baseline_hash != baseline_fingerprint(self.baseline):
                    result.append((None, "fallback:baseline_mismatch"))
                elif (error := model.validation_error()) is not None:
                    result.append((None, "fallback:" + error))
                else:
                    filter_check = getattr(model, "filter_validation_error", None)
                    if filter_check is not None:
                        filter_error = filter_check(self.cfg, process_scale)
                        if filter_error is not None:
                            result.append((None, "fallback:" + filter_error))
                            return
                    observation = model.observe(dict(zs), quality, self.cfg)
                    if (
                        model.model_hash != self._expected_emission_hash
                        or model.version != self._expected_emission_version
                    ):
                        result.append((None, "fallback:version_mismatch"))
                    elif observation is None:
                        result.append((None, "fallback:missing_or_ood"))
                    elif (
                        not isinstance(observation, EmissionObservation)
                        or not math.isfinite(observation.measurement)
                        or not 0 <= observation.measurement <= 1
                        or not math.isfinite(observation.variance)
                        or observation.variance <= 0
                        or not math.isfinite(observation.confidence)
                        or not 0 <= observation.confidence <= 1
                    ):
                        result.append((None, "fallback:invalid_output"))
                    else:
                        result.append((observation, "learned"))
            except Exception:  # noqa: BLE001 -- optional model failure must fall back
                result.append((None, "fallback:exception"))
            finally:
                completed.set()

        worker = threading.Thread(target=infer, daemon=True, name="mdt-l1-emission")
        worker.start()
        if not completed.wait(self.emission_timeout_s):
            self._emission_disabled_reason = "timeout"
            self.last_emission_status = "fallback:timeout"
            return None
        if not result:
            self.last_emission_status = "fallback:exception"
            return None
        observation, self.last_emission_status = result[0]
        if self.last_emission_status in {
            "fallback:filter_config_mismatch",
            "fallback:process_scale_out_of_support",
        }:
            self._emission_disabled_reason = "unvalidated_history"
        if observation is not None:
            self.last_emission_version = model.version
            self.last_emission_hash = model.model_hash
        return observation

    def update(self, feats: Features, process_scale: float = 1.0) -> State:
        if not math.isfinite(process_scale) or process_scale <= 0:
            raise ValueError("process_scale must be finite and > 0")
        self.last_emission_status = "default"
        self.last_emission_version = None
        self.last_emission_hash = None
        # Prediction always runs, including gaps. Otherwise a long period with
        # no usable measurement would incorrectly preserve stale certainty.
        self._p += self.cfg.kalman_q * process_scale
        if not self.baseline.is_ready or feats.quality is SignalQuality.LOST:
            if self.emission_model is not None:
                # A gap is outside the clean chronological posterior validation,
                # even when it precedes the first accepted learned observation.
                self._emission_disabled_reason = "unvalidated_history"
                self.last_emission_status = "fallback:baseline_or_signal_unavailable"
            return State(
                t=feats.t,
                arousal=self._x,
                confidence=0.0,
                uncertainty=self._p,
            )

        zs: dict[str, float] = {}
        for k, v in feats.as_dict().items():
            z = self.baseline.z(k, v)
            if z is None:
                continue
            zs[k] = -z if k in INVERTED else z

        observation = self._learned_observation(zs, feats.quality, process_scale)
        if observation is None:
            observation = self._default_emission.observe(zs, feats.quality, self.cfg)
        if observation is None:
            return State(
                t=feats.t,
                arousal=self._x,
                confidence=0.0,
                z_scores=zs,
                uncertainty=self._p,
            )

        k_gain = self._p / (self._p + observation.variance)
        self._x += k_gain * (observation.measurement - self._x)
        self._p *= 1.0 - k_gain
        return State(
            t=feats.t,
            arousal=self._x,
            confidence=observation.confidence,
            z_scores=zs,
            uncertainty=self._p,
        )
