"""Frozen, pure arbitration. Hysteresis state is explicit input/output."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import LearningConfig
from .policy import decision_error, finite_scalar
from .types import PolicyDecision


@dataclass(frozen=True)
class ArbiterState:
    active: bool = False


@dataclass(frozen=True)
class ArbiterTrace:
    decision: str
    lambda_mix: float
    state: ArbiterState
    disagreement: float = 0.0


INACTIVE = ArbiterState()


def resolve(
    agent: PolicyDecision,
    baseline: PolicyDecision,
    q: float,
    cfg: LearningConfig,
    previous: ArbiterState = INACTIVE,
    *,
    expected_version: str | None = None,
    calibrated: bool = True,
    approved: bool = False,
) -> tuple[float, ArbiterTrace]:
    """Resolve scalar proposals with no hidden mutable state.

    Parameters/music proposals use normalized distance as the scalar action to
    obtain the same gate; callers then blend within their deterministic bounds.
    """
    if not finite_scalar(baseline.action):
        raise ValueError("baseline action must be finite")
    base = float(baseline.action)

    def fallback(reason: str) -> tuple[float, ArbiterTrace]:
        return base, ArbiterTrace(reason, 0.0, ArbiterState(False))

    if cfg.mode == "disabled":
        return fallback("disabled")
    error = decision_error(agent, expected_version, baseline.context_hash)
    if error:
        return fallback(error)
    if agent.latency_ms > cfg.inference_timeout_ms:
        return fallback("inference_timeout")
    if not agent.in_distribution:
        return fallback("out_of_distribution")
    if not finite_scalar(q) or q < cfg.reliability_exit or q > 1:
        return fallback("unreliable_state")
    if calibrated is not True:
        return fallback("uncalibrated_policy")
    threshold = cfg.reliability_exit if previous.active else cfg.reliability_enter
    if q < threshold:
        return fallback("reliability_hysteresis")
    try:
        proposed = float(agent.action)
    except (TypeError, ValueError, OverflowError):
        return fallback("invalid_scalar_action")
    disagreement = abs(proposed - base)
    if not math.isfinite(disagreement):
        return fallback("nonfinite_disagreement")
    if (
        disagreement > cfg.disagreement_limit
        and agent.confidence < cfg.disagreement_confidence
    ):
        return fallback("policy_disagreement")
    if agent.confidence <= cfg.confidence_min:
        return fallback("low_policy_confidence")
    mix = min(
        1.0, (q - cfg.reliability_exit) / (cfg.reliability_full - cfg.reliability_exit)
    )
    mix *= (agent.confidence - cfg.confidence_min) / (1.0 - cfg.confidence_min)
    if cfg.mode == "shadow":
        return base, ArbiterTrace("shadow", 0.0, ArbiterState(True), disagreement)
    if cfg.mode == "suggest" and not approved:
        return base, ArbiterTrace(
            "awaiting_approval", 0.0, ArbiterState(True), disagreement
        )
    if cfg.mode == "restricted" and disagreement > 0:
        mix = min(mix, cfg.restricted_delta / disagreement)
    output = base + mix * (proposed - base)
    return output, ArbiterTrace(
        "blended" if mix < 1 else "agent", mix, ArbiterState(True), disagreement
    )


class Arbiter:
    """Stateless convenience wrapper around :func:`resolve`."""

    def __init__(self, cfg: LearningConfig):
        self.cfg = cfg

    def resolve(
        self,
        agent: PolicyDecision,
        baseline: PolicyDecision,
        q: float,
        previous: ArbiterState = INACTIVE,
        **kwargs,
    ):
        return resolve(agent, baseline, q, self.cfg, previous, **kwargs)
