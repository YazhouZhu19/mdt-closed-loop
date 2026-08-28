"""L1 状态估计。

关键约束：只输出唤醒度。EDA/HRV 无法可靠区分正负效价，
效价一律从自评获取，不在此处建模。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

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


class ArousalEstimator:
    """EDA 主导快分量，HRV 主导慢分量，一维卡尔曼做时间平滑。"""

    def __init__(self, baseline: IndividualBaseline, cfg: StateConfig):
        self.baseline = baseline
        self.cfg = cfg
        self._x = 0.5
        self._p = 1.0

    def update(self, feats: Features, process_scale: float = 1.0) -> State:
        if not math.isfinite(process_scale) or process_scale <= 0:
            raise ValueError("process_scale must be finite and > 0")
        # Prediction always runs, including gaps.  Otherwise a long period with
        # no usable measurement would incorrectly preserve stale certainty.
        self._p += self.cfg.kalman_q * process_scale
        if not self.baseline.is_ready or feats.quality is SignalQuality.LOST:
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

        eda_z = [zs[k] for k in EDA_KEYS if k in zs]
        hrv_z = [zs[k] for k in HRV_KEYS if k in zs]
        if not eda_z and not hrv_z:
            return State(
                t=feats.t,
                arousal=self._x,
                confidence=0.0,
                z_scores=zs,
                uncertainty=self._p,
            )

        parts, weights = [], []
        if eda_z:
            parts.append(sum(eda_z) / len(eda_z))
            weights.append(self.cfg.eda_weight)
        if hrv_z:
            parts.append(sum(hrv_z) / len(hrv_z))
            weights.append(self.cfg.hrv_weight)
        fused_z = sum(p * w for p, w in zip(parts, weights)) / sum(weights)
        measurement = _squash(fused_z, self.cfg.max_abs_z)

        r = self.cfg.kalman_r * (2.0 if feats.quality is SignalQuality.NOISY else 1.0)
        k_gain = self._p / (self._p + r)
        self._x += k_gain * (measurement - self._x)
        self._p *= 1.0 - k_gain

        coverage = len(zs) / (len(EDA_KEYS) + len(HRV_KEYS))
        confidence = coverage * (0.5 if feats.quality is SignalQuality.NOISY else 1.0)
        return State(
            t=feats.t,
            arousal=self._x,
            confidence=confidence,
            z_scores=zs,
            uncertainty=self._p,
        )
