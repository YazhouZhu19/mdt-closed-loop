"""Stage 3-A: offline linear gain scheduling; bounded PI structure is retained.

Training labels are independently selected controller gains, not outcome labels.
Fitting this model alone does not establish an improvement in treatment outcome.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np

from ..config import ControlConfig
from ..policy import Context, context_hash, stable_hash
from ..types import PolicyDecision


@dataclass(frozen=True)
class GainParams:
    kp: float = 0.45
    ki: float = 0.05
    deadband: float = 0.08
    deadband_integral_leak: float = 0.95

    def __post_init__(self) -> None:
        for name, (lo, hi) in GAIN_BOUNDS.items():
            value = getattr(self, name)
            if not math.isfinite(value) or not lo <= value <= hi:
                raise ValueError(f"{name} outside frozen gain bounds")

    @classmethod
    def from_mapping(cls, values: Mapping) -> GainParams:
        if set(values) != set(GAIN_BOUNDS):
            raise ValueError("gain action must contain exactly the four gain fields")
        return cls(**{k: float(v) for k, v in values.items()})

    def control_config(self, base: ControlConfig) -> ControlConfig:
        # Never learn output/integral bounds or uncertainty safety thresholds.
        return replace(base, **asdict(self))


GAIN_BOUNDS = {
    "kp": (0.0, 0.9),
    "ki": (0.0, 0.1),
    "deadband": (0.02, 0.2),
    "deadband_integral_leak": (0.8, 1.0),
}


@dataclass(frozen=True)
class GainExample:
    context: Mapping
    gains: GainParams
    session_id: str


@dataclass(frozen=True)
class GainPolicy:
    feature_names: tuple[str, ...]
    mean: tuple[float, ...]
    scale: tuple[float, ...]
    weights: tuple[tuple[float, ...], ...]
    confidence: float = 0.0
    calibration_ece: float = 1.0
    validation_count: int = 0
    ood_limit: float = 4.0

    def __post_init__(self) -> None:
        n = len(self.feature_names)
        if n == 0 or len(set(self.feature_names)) != n:
            raise ValueError("gain feature names must be nonempty and unique")
        if len(self.mean) != n or len(self.scale) != n or len(self.weights) != n + 1:
            raise ValueError("invalid gain model dimensions")
        if any(len(row) != 4 for row in self.weights):
            raise ValueError("gain model must predict four parameters")
        if not all(
            math.isfinite(v)
            for v in (
                *self.mean,
                *self.scale,
                *(v for row in self.weights for v in row),
                self.confidence,
                self.calibration_ece,
                self.ood_limit,
            )
        ):
            raise ValueError("gain artifact must contain finite values")
        if (
            any(v <= 0 for v in self.scale)
            or not 0 <= self.confidence <= 1
            or self.calibration_ece < 0
            or self.ood_limit <= 0
            or not isinstance(self.validation_count, int)
            or self.validation_count < 0
        ):
            raise ValueError("invalid gain artifact diagnostics")

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(
                {"model": asdict(self), "version": self.version},
                allow_nan=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> GainPolicy:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        raw = payload["model"]
        for name in ("feature_names", "mean", "scale"):
            raw[name] = tuple(raw[name])
        raw["weights"] = tuple(tuple(row) for row in raw["weights"])
        model = cls(**raw)
        if model.version != payload["version"]:
            raise ValueError("gain artifact content hash mismatch")
        return model

    @property
    def version(self) -> str:
        return stable_hash({"model": "linear-gain-v1", **asdict(self)})

    @property
    def calibrated(self) -> bool:
        return self.validation_count >= 30 and self.calibration_ece < 0.05

    def _predict(self, context: Context) -> tuple[dict[str, float], bool]:
        values = np.asarray([float(context[k]) for k in self.feature_names])
        if not np.all(np.isfinite(values)):
            raise ValueError("gain context must be finite")
        z = (values - self.mean) / self.scale
        raw = np.r_[1.0, z] @ np.asarray(self.weights)
        action = {
            name: float(np.clip(value, *GAIN_BOUNDS[name]))
            for name, value in zip(GAIN_BOUNDS, raw)
        }
        return action, bool(np.max(np.abs(z)) <= self.ood_limit)

    def propose(self, context: Context) -> PolicyDecision:
        action, supported = self._predict(context)
        return PolicyDecision(
            action, 0.0, self.confidence, supported, self.version, context_hash(context)
        )

    @classmethod
    def fit(
        cls,
        rows: Sequence[GainExample],
        *,
        feature_names: tuple[str, ...] = ("initial_arousal", "reliability"),
        calibration: Sequence[GainExample] = (),
        evaluation: Sequence[GainExample] = (),
        ridge: float = 1.0,
    ) -> GainPolicy:
        if len(rows) < max(5, len(feature_names) + 1) or not feature_names:
            raise ValueError("insufficient gain training examples")
        if not math.isfinite(ridge) or ridge <= 0:
            raise ValueError("ridge must be finite and positive")
        splits = [
            {r.session_id for r in split} for split in (rows, calibration, evaluation)
        ]
        if any("" in ids for ids in splits) or any(
            splits[i] & splits[j] for i in range(3) for j in range(i)
        ):
            raise ValueError(
                "training/calibration/evaluation session IDs must be disjoint"
            )
        x = np.asarray([[float(r.context[k]) for k in feature_names] for r in rows])
        y = np.asarray([[getattr(r.gains, k) for k in GAIN_BOUNDS] for r in rows])
        if not np.all(np.isfinite(x)):
            raise ValueError("training features must be finite")
        mean = x.mean(axis=0)
        scale = np.maximum(x.std(axis=0), 0.1)
        design = np.column_stack((np.ones(len(x)), (x - mean) / scale))
        penalty = np.eye(design.shape[1]) * ridge
        penalty[0, 0] = 0
        weights = np.linalg.solve(design.T @ design + penalty, design.T @ y)
        model = cls(
            feature_names,
            tuple(mean),
            tuple(scale),
            tuple(tuple(float(v) for v in row) for row in weights),
        )

        def accuracy(samples: Sequence[GainExample]) -> float:
            successes = 0
            for sample in samples:
                prediction, supported = model._predict(sample.context)
                errors = [
                    abs(prediction[k] - getattr(sample.gains, k)) / (hi - lo)
                    for k, (lo, hi) in GAIN_BOUNDS.items()
                ]
                successes += supported and max(errors) <= 0.15
            return successes / len(samples)

        if len(splits[1]) >= 30 and len(splits[2]) >= 30:
            confidence = accuracy(calibration)
            model = replace(
                model,
                confidence=confidence,
                calibration_ece=abs(accuracy(evaluation) - confidence),
                validation_count=len(splits[2]),
            )
        return model
