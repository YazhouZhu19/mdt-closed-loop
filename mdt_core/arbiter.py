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
    lambda_mix: float = 0.0
    last_time_s: float | None = None


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
    now_s: float | None = None,
    action_scale: float = 1.0,
    select_branch: bool = False,
    ramp: bool = True,
) -> tuple[float, ArbiterTrace]:
    """Resolve scalar proposals with no hidden mutable state.

    Parameters/music proposals use normalized distance as the scalar action to
    obtain the same gate; callers then blend within their deterministic bounds.
    In the v2.1 profile, ``now_s`` is a monotonic/session-clock timestamp and
    ``action_scale`` is the frozen full range of a scalar action. A binary
    branch selector is deliberately exempt from fractional weighting/ramping:
    it needs full permission and must meet the complete residual bound.
    """
    if not finite_scalar(baseline.action):
        raise ValueError("baseline action must be finite")
    base = float(baseline.action)

    def fallback(reason: str) -> tuple[float, ArbiterTrace]:
        timestamp = (
            now_s
            if cfg.v21_gates and now_s is not None and finite_scalar(now_s) and now_s >= 0
            else None
        )
        return base, ArbiterTrace(reason, 0.0, ArbiterState(False, 0.0, timestamp))

    if cfg.mode == "disabled":
        return fallback("disabled")
    if cfg.v21_gates:
        if now_s is None or not finite_scalar(now_s) or now_s < 0:
            return fallback("invalid_gate_time")
        if previous.last_time_s is not None and now_s < previous.last_time_s:
            return base, ArbiterTrace(
                "nonmonotonic_gate_time", 0.0,
                ArbiterState(False, 0.0, previous.last_time_s),
            )
        if not finite_scalar(action_scale) or action_scale <= 0:
            return fallback("invalid_action_scale")
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
    if cfg.v21_gates:
        disagreement /= action_scale
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
        state = ArbiterState(True) if not cfg.v21_gates else ArbiterState(False, 0.0, now_s)
        return base, ArbiterTrace("shadow", 0.0, state, disagreement)
    if cfg.mode == "suggest" and not approved:
        return base, ArbiterTrace(
            "awaiting_approval", 0.0,
            ArbiterState(True) if not cfg.v21_gates else ArbiterState(False, 0.0, now_s),
            disagreement,
        )
    if cfg.v21_gates:
        # The v2.1 clock was validated before any policy branch above.
        assert now_s is not None
        cap = min(cfg.module_cap, cfg.domain_cap, cfg.session_cap)
        if select_branch:
            if cap < 1.0:
                return fallback("branch_permission_cap")
            if cfg.mode == "restricted" and disagreement > cfg.restricted_delta:
                return fallback("branch_restricted_distance")
            return proposed, ArbiterTrace(
                "agent_selected", 1.0, ArbiterState(True, 1.0, now_s), disagreement
            )
        if cfg.mode == "restricted":
            mix = min(mix, cfg.restricted_delta / max(disagreement, cfg.distance_epsilon))
        mix = min(mix, cap)
        if ramp:
            # No credit for time spent unavailable, paused or in shadow. First
            # eligible proposal establishes the clock and has zero influence.
            elapsed = (
                now_s - previous.last_time_s
                if previous.active and previous.last_time_s is not None else 0.0
            )
            mix = min(mix, previous.lambda_mix + cfg.ramp_up_per_s * elapsed)
        state = ArbiterState(True, mix, now_s)
    else:
        if cfg.mode == "restricted" and disagreement > 0:
            mix = min(mix, cfg.restricted_delta / disagreement)
        state = ArbiterState(True)
    output = base + mix * (proposed - base)
    return output, ArbiterTrace(
        "blended" if mix < 1 else "agent", mix, state, disagreement
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
