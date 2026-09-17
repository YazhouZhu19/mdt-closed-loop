"""Offline preference ranking for L3.5; never calls an engine or updates online.

Each training row pairs numeric context, a complete music parameter vector, and
normalized preference feedback (0 = skip/dislike, 1 = like). Ridge regression
ranks the *observed* candidate vectors using context/parameter interactions.
An artifact is uncalibrated until an independent held-out ECE check passes.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from numbers import Real
from pathlib import Path
from typing import Any

import numpy as np

from ..l35_mapping import layer_level
from ..policy import Context, context_hash, stable_hash
from ..types import MusicParams, PolicyDecision

MusicVector = tuple[float, int, float, float, float, float, float]


def _numeric_context(context: Context, names: tuple[str, ...]) -> tuple[float, ...]:
    values: list[float] = []
    for name in names:
        value = context.get(name)
        if not isinstance(value, Real) or isinstance(value, bool) or not math.isfinite(value):
            raise ValueError(f"context feature {name!r} must be finite and numeric")
        values.append(float(value))
    return tuple(values)


def _params_tuple(params: MusicParams | Mapping[str, Any]) -> MusicVector:
    validated = params.copy() if isinstance(params, MusicParams) else MusicParams(**params)
    return (validated.tempo, validated.layer_mask, validated.register,
            validated.dynamics, validated.harmonic_brightness,
            validated.rhythmic_accent, validated.reverb_depth)


def _row_hash(context: tuple, params: tuple) -> str:
    return stable_hash({"context": context, "params": params})


@dataclass(frozen=True)
class TastePolicy:
    """Immutable deterministic ranker, with truthful log probability zero.

    ``fit`` and ``calibrate`` return new artifacts. Fitting alone does not grant
    execution permission. Calibration tests preference-probability accuracy;
    it is not evidence of clinical benefit or trial authorization.
    """

    feature_names: tuple[str, ...]
    feature_min: tuple[float, ...]
    feature_max: tuple[float, ...]
    candidates: tuple[MusicVector, ...]
    coefficients: tuple[float, ...]
    training_rows: tuple[str, ...]
    ridge: float = 1.0
    training_data_hash: str = ""
    calibration_ece: float | None = None
    calibration_count: int = 0
    calibration_hash: str | None = None

    @classmethod
    def fit(
        cls,
        contexts: Sequence[Context],
        params: Sequence[MusicParams | Mapping[str, Any]],
        feedback: Sequence[float],
        *,
        feature_names: tuple[str, ...] = ("control",),
        ridge: float = 1.0,
    ) -> TastePolicy:
        """Batch fit to completed, normalized preference-feedback rows."""
        names = tuple(feature_names)
        if not names or len(set(names)) != len(names) or not all(isinstance(x, str) for x in names):
            raise ValueError("feature_names must be nonempty unique strings")
        if not math.isfinite(ridge) or ridge <= 0:
            raise ValueError("ridge must be finite and > 0")
        if not contexts or not len(contexts) == len(params) == len(feedback):
            raise ValueError("training sequences must be nonempty with equal lengths")
        observed = [_numeric_context(context, names) for context in contexts]
        actions = [_params_tuple(action) for action in params]
        rewards = cls._feedback(feedback)
        x = np.asarray(observed, dtype=float)
        feature_min = tuple(float(v) for v in x.min(axis=0))
        feature_max = tuple(float(v) for v in x.max(axis=0))
        candidates = tuple(sorted(set(actions)))
        model = cls(names, feature_min, feature_max, candidates, (),
                    tuple(sorted(_row_hash(c, a) for c, a in zip(observed, actions))),
                    float(ridge))
        design = np.asarray([model._features(c, a) for c, a in zip(observed, actions)])
        penalty = np.eye(design.shape[1]) * ridge
        penalty[0, 0] = 1e-9
        coeff = np.linalg.solve(design.T @ design + penalty, design.T @ rewards)
        if not np.isfinite(coeff).all():
            raise ValueError("nonfinite fitted coefficients")
        return replace(model, coefficients=tuple(float(v) for v in coeff),
                       training_data_hash=stable_hash({"contexts": observed,
                                                       "params": actions,
                                                       "feedback": rewards}))

    @staticmethod
    def _feedback(values: Sequence[float]) -> list[float]:
        result: list[float] = []
        for value in values:
            if not isinstance(value, Real) or isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("preference feedback must be finite and in [0, 1]")
            result.append(float(value))
        return result

    @property
    def calibrated(self) -> bool:
        return (self.calibration_count >= 30 and self.calibration_ece is not None
                and self.calibration_ece < 0.05 and self.calibration_hash is not None)

    @property
    def version(self) -> str:
        return stable_hash({"algorithm": "offline-taste-ridge-v1", **self.__dict__})

    def _features(self, context: tuple, action: tuple) -> np.ndarray:
        # Training range scaling is fixed in the artifact, never updated online.
        ctx = np.asarray([(value - lo) / max(hi - lo, 1.0)
                          for value, lo, hi in zip(context, self.feature_min, self.feature_max)])
        music = np.asarray((action[0] / 100.0, layer_level(int(action[1])) / 3.0, *action[2:]))
        return np.concatenate(([1.0], ctx, music, np.outer(ctx, music).ravel()))

    def _prediction(self, context: tuple, action: tuple) -> float:
        value = float(np.dot(self._features(context, action), self.coefficients))
        if not math.isfinite(value):
            raise ValueError("nonfinite preference prediction")
        return max(0.0, min(1.0, value))

    def _in_distribution(self, context: tuple) -> bool:
        return all(lo - 1e-9 <= value <= hi + 1e-9
                   for value, lo, hi in zip(context, self.feature_min, self.feature_max))

    def propose(self, context: Context) -> PolicyDecision:
        observed = _numeric_context(context, self.feature_names)
        scores = [self._prediction(observed, action) for action in self.candidates]
        selected = max(range(len(scores)), key=scores.__getitem__)
        action = MusicParams(*self.candidates[selected])
        in_distribution = self._in_distribution(observed)
        confidence = scores[selected] if self.calibrated and in_distribution else 0.0
        return PolicyDecision(action.as_dict(), 0.0, confidence, in_distribution,
                              self.version, context_hash(context))

    def calibrate(
        self,
        contexts: Sequence[Context],
        params: Sequence[MusicParams | Mapping[str, Any]],
        feedback: Sequence[float],
    ) -> TastePolicy:
        """Validate a frozen ranker on independent held-out preference rows.

        Requires >= 30 distinct, in-distribution rows and 10-bin ECE < 0.05
        to advertise calibration. Failed checks retain an uncalibrated artifact.
        Exact context/action overlap with training and duplicate validation rows are rejected;
        callers remain responsible for participant/session-separated splitting.
        """
        if not contexts or not len(contexts) == len(params) == len(feedback):
            raise ValueError("calibration sequences must be nonempty with equal lengths")
        observed = [_numeric_context(context, self.feature_names) for context in contexts]
        actions = [_params_tuple(action) for action in params]
        rewards = self._feedback(feedback)
        rows = tuple(sorted(_row_hash(c, a) for c, a in zip(observed, actions)))
        if len(set(rows)) != len(rows) or set(rows) & set(self.training_rows):
            raise ValueError("calibration rows must be distinct and held out")
        if not all(self._in_distribution(c) for c in observed):
            raise ValueError("calibration context lies outside training support")
        if any(a not in self.candidates for a in actions):
            raise ValueError("calibration must use the fitted candidate set")
        predictions = np.asarray([self._prediction(c, a) for c, a in zip(observed, actions)])
        labels = np.asarray(rewards)
        indices = np.minimum((predictions * 10).astype(int), 9)
        ece = 0.0
        for bucket in range(10):
            mask = indices == bucket
            if mask.any():
                ece += float(mask.mean() * abs(predictions[mask].mean() - labels[mask].mean()))
        return replace(self, calibration_ece=ece, calibration_count=len(rows),
                       calibration_hash=stable_hash({"contexts": observed,
                                                     "params": actions,
                                                     "feedback": rewards}))

    def save(self, path: str | Path) -> None:
        """Export weights, candidate vectors and evidence hashes as JSON."""
        artifact = {"schema": 1, "version": self.version, "model": asdict(self)}
        Path(path).write_text(json.dumps(artifact, sort_keys=True, indent=2,
                                        allow_nan=False), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> TastePolicy:
        artifact = json.loads(Path(path).read_text(encoding="utf-8"))
        if artifact.get("schema") != 1:
            raise ValueError("unsupported taste artifact schema")
        values = dict(artifact["model"])
        for name in ("feature_names", "feature_min", "feature_max",
                     "coefficients", "training_rows"):
            values[name] = tuple(values[name])
        values["candidates"] = tuple(tuple(row) for row in values["candidates"])
        model = cls(**values)
        if model.version != artifact["version"]:
            raise ValueError("taste artifact content hash mismatch")
        return model


# Explicit alias for callers that want the offline nature visible at the callsite.
OfflineTastePolicy = TastePolicy
