"""Offline contextual trajectory selection with a hierarchical Bayesian model.

Each finite action has a Gaussian linear reward model with a population prior.
Subject intercepts shrink toward that population model, so sparse histories do
not replace the population estimate. Training requires explicit rewards in
``[0, 1]``; this module neither invents clinical rewards nor learns in-session.

Sampling uses an exploration/softmax mixture over posterior means. The logged
propensity is the *exact probability of that categorical sampler*. It is not
reported as a Thompson-sampling probability. Gaussian reward assumptions and
finite-action coverage are research approximations; posterior variance alone
is never presented as calibrated reliability.
"""

from __future__ import annotations

import json
import math
import random
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from ..l2_planner import TrajectoryParams
from ..policy import Context, context_hash, stable_hash
from ..types import PolicyDecision


@dataclass(frozen=True)
class TrajectoryObservation:
    """One logged action and explicitly supplied, normalized observed reward.

    ``action`` is an index into the training action catalog. Include a stable
    pseudonymous ``subject_id`` in context for partial pooling and a unique
    ``session_id`` for session-disjoint held-out calibration.
    """

    context: Context
    action: int
    reward: float


@dataclass(frozen=True)
class BanditCalibration:
    """Held-out reward-prediction coverage, not evidence of clinical efficacy.

    Confidence is a Wilson lower bound for P(|prediction - reward| <= tolerance)
    per action. The validation sessions must be disjoint from training, and all
    candidate actions require coverage. The artifact remains shadow-only when
    these declared acceptance criteria are not met.
    """

    sample_count: int
    session_count: int
    counts: tuple[int, ...]
    coverage_lower_bounds: tuple[float, ...]
    tolerance: float
    min_coverage: float
    min_per_action: int
    min_sessions: int
    validation_digest: str


def _subject(context: Context) -> str:
    value = context.get("subject_id", "")
    if not isinstance(value, str):
        raise TypeError("subject_id must be a pseudonymous string")
    return value


def _session(context: Context) -> str:
    value = context.get("session_id", "")
    if not isinstance(value, str):
        raise TypeError("session_id must be a string")
    return value


def _features(context: Context, names: tuple[str, ...]) -> np.ndarray:
    values = []
    for name in names:
        value = context.get(name)
        if (
            isinstance(value, bool)
            or not isinstance(value, (float, int))
            or not math.isfinite(value)
        ):
            raise ValueError(f"context feature {name!r} must be finite and numeric")
        values.append(float(value))
    return np.asarray(values, dtype=float)


def _validate_rows(
    observations: Sequence[TrajectoryObservation],
    actions: tuple[TrajectoryParams, ...],
    names: tuple[str, ...],
) -> tuple[TrajectoryObservation, ...]:
    if not observations:
        raise ValueError("at least one explicit reward observation is required")
    rows = tuple(observations)
    for row in rows:
        if not isinstance(row, TrajectoryObservation):
            raise TypeError("observations must be TrajectoryObservation instances")
        if (
            isinstance(row.action, bool)
            or not isinstance(row.action, int)
            or not 0 <= row.action < len(actions)
        ):
            raise ValueError("observation action must index the finite action catalog")
        if (
            isinstance(row.reward, bool)
            or not isinstance(row.reward, (float, int))
            or not math.isfinite(row.reward)
            or not 0 <= row.reward <= 1
        ):
            raise ValueError("explicit normalized rewards must be finite and in [0, 1]")
        _features(row.context, names)
        _subject(row.context)
        _session(row.context)
        context_hash(row.context)
    return rows


def _rows_digest(rows: Sequence[TrajectoryObservation]) -> str:
    # Sorting makes model identity independent of input-record ordering.
    return stable_hash(sorted(stable_hash(asdict(row)) for row in rows))


def _wilson_lower(successes: int, count: int) -> float:
    if not count:
        return 0.0
    z = 1.959963984540054
    rate = successes / count
    return (
        rate
        + z * z / (2 * count)
        - z * math.sqrt(rate * (1 - rate) / count + z * z / (4 * count * count))
    ) / (1 + z * z / count)


@dataclass(frozen=True)
class HierarchicalTrajectoryBandit:
    """Immutable fitted artifact; ``fit``/``calibrate`` return new artifacts.

    The model uses an empirical-Bayes subject residual intercept for each arm.
    A subject with n observations gets weight n/(n + subject_prior_strength),
    while an unseen subject uses the population posterior. OOD is a conservative
    per-feature training-support check; unseen subjects alone are not OOD.
    """

    actions: tuple[TrajectoryParams, ...]
    feature_names: tuple[str, ...]
    feature_means: tuple[float, ...]
    feature_scales: tuple[float, ...]
    feature_min: tuple[float, ...]
    feature_max: tuple[float, ...]
    population_means: tuple[tuple[float, ...], ...]
    population_covariances: tuple[tuple[tuple[float, ...], ...], ...]
    subject_offsets: tuple[tuple[str, tuple[float, ...]], ...]
    arm_counts: tuple[int, ...]
    training_sessions: tuple[str, ...]
    training_digest: str
    temperature: float = 0.15
    exploration: float = 0.10
    prior_strength: float = 2.0
    subject_prior_strength: float = 5.0
    noise_variance: float = 0.0625
    ood_margin: float = 0.05
    calibration: BanditCalibration | None = None

    @classmethod
    def fit(
        cls,
        observations: Sequence[TrajectoryObservation],
        actions: Sequence[TrajectoryParams] | None = None,
        *,
        feature_names: Sequence[str] = ("initial_arousal",),
        temperature: float = 0.15,
        exploration: float = 0.10,
        prior_strength: float = 2.0,
        subject_prior_strength: float = 5.0,
        noise_variance: float = 0.0625,
        ood_margin: float = 0.05,
        validation_rows: Sequence[TrajectoryObservation] | None = None,
    ) -> HierarchicalTrajectoryBandit:
        """Fit offline only; without held-out calibration, remain shadow-only."""
        catalog = tuple(actions) if actions is not None else (
            TrajectoryParams(),
            TrajectoryParams(match_seconds=450, descent_seconds=1500, speed_gain=0.75),
            TrajectoryParams(anchor_offset=-0.05, match_seconds=240, floor=0.20),
        )
        if not catalog or any(not isinstance(a, TrajectoryParams) for a in catalog):
            raise ValueError("actions must be a nonempty catalog of bounded parameters")
        if len(set(catalog)) != len(catalog):
            raise ValueError("duplicate actions would make action propensities ambiguous")
        names = tuple(feature_names)
        if (
            not names
            or len(set(names)) != len(names)
            or any(not isinstance(name, str) or not name for name in names)
        ):
            raise ValueError("feature_names must be nonempty unique strings")
        for name, value in (
            ("temperature", temperature),
            ("prior_strength", prior_strength),
            ("subject_prior_strength", subject_prior_strength),
            ("noise_variance", noise_variance),
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if not math.isfinite(exploration) or not 0 < exploration <= 1:
            raise ValueError("exploration must be in (0, 1]")
        if not math.isfinite(ood_margin) or not 0 <= ood_margin <= 0.25:
            raise ValueError("ood_margin must be in [0, 0.25]")
        rows = _validate_rows(observations, catalog, names)
        # Deterministic accumulation also makes the content hash independent
        # of training-record order, including floating-point summation order.
        rows = tuple(sorted(rows, key=lambda row: stable_hash(asdict(row))))
        raw = np.stack([_features(row.context, names) for row in rows])
        means = raw.mean(axis=0)
        scales = raw.std(axis=0)
        if not np.isfinite(means).all() or not np.isfinite(scales).all():
            raise ValueError("context features exceed the model's numerical range")
        scales = np.where(scales < 1e-8, 1.0, scales)
        x = np.column_stack((np.ones(len(rows)), (raw - means) / scales))
        y = np.asarray([row.reward for row in rows])
        choices = np.asarray([row.action for row in rows])
        prior = np.zeros(x.shape[1])
        prior[0] = 0.5
        coefficients = []
        covariances = []
        counts = []
        for action in range(len(catalog)):
            selected = choices == action
            arm_x = x[selected]
            precision = prior_strength * np.eye(x.shape[1]) + arm_x.T @ arm_x
            coefficient = np.linalg.solve(
                precision, prior_strength * prior + arm_x.T @ y[selected]
            )
            coefficients.append(coefficient)
            covariances.append(noise_variance * np.linalg.inv(precision))
            counts.append(int(selected.sum()))
        subjects = sorted({_subject(row.context) for row in rows} - {""})
        offsets = []
        for subject in subjects:
            subject_mask = np.asarray([_subject(row.context) == subject for row in rows])
            adjustments = []
            for action in range(len(catalog)):
                selected = subject_mask & (choices == action)
                residuals = y[selected] - x[selected] @ coefficients[action]
                adjustments.append(
                    float(residuals.sum() / (int(selected.sum()) + subject_prior_strength))
                )
            offsets.append((subject, tuple(adjustments)))
        model = cls(
            actions=catalog,
            feature_names=names,
            feature_means=tuple(float(v) for v in means),
            feature_scales=tuple(float(v) for v in scales),
            feature_min=tuple(float(v) for v in raw.min(axis=0)),
            feature_max=tuple(float(v) for v in raw.max(axis=0)),
            population_means=tuple(tuple(float(v) for v in c) for c in coefficients),
            population_covariances=tuple(
                tuple(tuple(float(v) for v in line) for line in covariance)
                for covariance in covariances
            ),
            subject_offsets=tuple(offsets),
            arm_counts=tuple(counts),
            training_sessions=tuple(sorted({_session(row.context) for row in rows})),
            training_digest=_rows_digest(rows),
            temperature=temperature,
            exploration=exploration,
            prior_strength=prior_strength,
            subject_prior_strength=subject_prior_strength,
            noise_variance=noise_variance,
            ood_margin=ood_margin,
        )
        return model.calibrate(validation_rows) if validation_rows is not None else model

    @property
    def version(self) -> str:
        return stable_hash({"model": "hierarchical-trajectory-bandit-v1", **asdict(self)})

    @property
    def calibrated(self) -> bool:
        evidence = self.calibration
        return bool(
            evidence is not None
            and evidence.session_count >= evidence.min_sessions
            and min(evidence.counts) >= evidence.min_per_action
            and min(evidence.coverage_lower_bounds) >= evidence.min_coverage
        )

    def _predict(self, context: Context) -> tuple[np.ndarray, bool]:
        raw = _features(context, self.feature_names)
        low, high = np.asarray(self.feature_min), np.asarray(self.feature_max)
        padding = self.ood_margin * np.maximum(high - low, np.asarray(self.feature_scales))
        in_distribution = bool(np.all((raw >= low - padding) & (raw <= high + padding)))
        if not in_distribution:
            # Do not extrapolate the regression at extreme/OOD coordinates.
            # The resulting uniform shadow proposal is explicitly ineligible
            # for execution and has zero calibrated confidence.
            return np.full(len(self.actions), 0.5), False
        x = np.concatenate((np.ones(1), (raw - self.feature_means) / self.feature_scales))
        predictions = np.asarray(self.population_means) @ x
        offsets = dict(self.subject_offsets).get(_subject(context))
        if offsets is not None:
            predictions = predictions + offsets
        return np.clip(predictions, 0.0, 1.0), in_distribution

    def probabilities(self, context: Context) -> tuple[float, ...]:
        """Actual categorical distribution used by ``propose`` for audit/OPE."""
        means, _ = self._predict(context)
        logits = (means - means.max()) / self.temperature
        weights = np.exp(logits)
        probabilities = (
            (1 - self.exploration) * weights / weights.sum()
            + self.exploration / len(self.actions)
        )
        return tuple(float(p) for p in probabilities)

    def propose(self, context: Context) -> PolicyDecision:
        fingerprint = context_hash(context)
        try:
            _, in_distribution = self._predict(context)
            probabilities = self.probabilities(context)
        except (TypeError, ValueError):
            return PolicyDecision(
                {}, 0.0, 0.0, False, self.version, fingerprint, "invalid_l2_context"
            )
        # Sampling state is external to the immutable artifact. No reward or
        # parameter updates happen here or elsewhere in a running session.
        index = random.SystemRandom().choices(
            range(len(self.actions)), weights=probabilities, k=1
        )[0]
        confidence = (
            self.calibration.coverage_lower_bounds[index]
            if self.calibrated and in_distribution and self.calibration is not None
            else 0.0
        )
        return PolicyDecision(
            self.actions[index].as_dict(),
            math.log(probabilities[index] / sum(probabilities)),
            confidence,
            in_distribution,
            self.version,
            fingerprint,
        )

    def calibrate(
        self,
        validation_rows: Sequence[TrajectoryObservation],
        *,
        tolerance: float = 0.25,
        min_coverage: float = 0.65,
        min_per_action: int = 10,
        min_sessions: int = 10,
    ) -> HierarchicalTrajectoryBandit:
        """Evaluate disjoint sessions and return a new content-versioned artifact.

        Coverage counts one result per session/action; correlated duplicate
        windows cannot inflate the effective validation sample size. All rows
        within a session/action must meet the declared prediction tolerance.
        """
        if not math.isfinite(tolerance) or not 0 < tolerance < 1:
            raise ValueError("tolerance must be in (0, 1)")
        if not math.isfinite(min_coverage) or not 0 < min_coverage < 1:
            raise ValueError("min_coverage must be in (0, 1)")
        for name, value in (("min_per_action", min_per_action), ("min_sessions", min_sessions)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 10:
                raise ValueError(f"{name} must be an integer >= 10")
        rows = _validate_rows(validation_rows, self.actions, self.feature_names)
        validation_sessions = {_session(row.context) for row in rows}
        if "" in self.training_sessions or "" in validation_sessions:
            raise ValueError("training and calibration require nonempty session_id")
        if validation_sessions & set(self.training_sessions):
            raise ValueError("calibration sessions must be disjoint from training")
        grouped: dict[tuple[str, int], bool] = {}
        for row in rows:
            predictions, in_distribution = self._predict(row.context)
            success = bool(
                in_distribution and abs(float(predictions[row.action]) - row.reward) <= tolerance
            )
            key = (_session(row.context), row.action)
            grouped[key] = grouped.get(key, True) and success
        counts = tuple(
            sum(action == candidate for _, action in grouped)
            for candidate in range(len(self.actions))
        )
        bounds = tuple(
            _wilson_lower(
                sum(success for (_, action), success in grouped.items() if action == candidate),
                counts[candidate],
            )
            for candidate in range(len(self.actions))
        )
        evidence = BanditCalibration(
            sample_count=len(rows),
            session_count=len(validation_sessions),
            counts=counts,
            coverage_lower_bounds=bounds,
            tolerance=tolerance,
            min_coverage=min_coverage,
            min_per_action=min_per_action,
            min_sessions=min_sessions,
            validation_digest=_rows_digest(rows),
        )
        return replace(self, calibration=evidence)

    def save(self, path: str | Path) -> None:
        """Save a JSON model artifact without participant records or raw signals."""
        artifact = {"schema": 1, "version": self.version, "model": asdict(self)}
        Path(path).write_text(
            json.dumps(artifact, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> HierarchicalTrajectoryBandit:
        artifact = json.loads(Path(path).read_text(encoding="utf-8"))
        if artifact.get("schema") != 1:
            raise ValueError("unsupported trajectory bandit artifact schema")
        values: dict[str, Any] = dict(artifact["model"])
        values["actions"] = tuple(TrajectoryParams.from_mapping(a) for a in values["actions"])
        for name in (
            "feature_names", "feature_means", "feature_scales", "feature_min", "feature_max",
            "arm_counts", "training_sessions",
        ):
            values[name] = tuple(values[name])
        values["population_means"] = tuple(tuple(row) for row in values["population_means"])
        values["population_covariances"] = tuple(
            tuple(tuple(row) for row in matrix) for matrix in values["population_covariances"]
        )
        values["subject_offsets"] = tuple(
            (subject, tuple(offsets)) for subject, offsets in values["subject_offsets"]
        )
        if values["calibration"] is not None:
            evidence = dict(values["calibration"])
            evidence["counts"] = tuple(evidence["counts"])
            evidence["coverage_lower_bounds"] = tuple(evidence["coverage_lower_bounds"])
            values["calibration"] = BanditCalibration(**evidence)
        model = cls(**values)
        if model.version != artifact["version"]:
            raise ValueError("trajectory bandit artifact content hash mismatch")
        return model
