"""Versioned proposals and bounded inference, independent of any actuator."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import threading
import time
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass, replace
from typing import Any, Protocol

from .types import PolicyDecision

Context = Mapping[str, Any]


def stable_hash(value: Any) -> str:
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def context_hash(ctx: Context) -> str:
    return stable_hash(dict(ctx))


class Policy(Protocol):
    @property
    def version(self) -> str: ...

    def propose(self, context: Context) -> PolicyDecision: ...


def finite_scalar(value: Any) -> bool:
    """Numeric metadata is scalar; containers and booleans are never scalars."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except (TypeError, ValueError, OverflowError):
        return False


def _canonical_action(value: Any) -> Any:
    """Copy only finite numeric JSON trees, without repr or object deepcopy.

    Limits reject recursive or oversized trees deterministically. A model's
    custom Mapping access still runs inside the bounded inference worker.
    """
    remaining = 10000

    def visit(item: Any, depth: int) -> Any:
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > 32:
            raise ValueError("action nesting or size exceeds limits")
        if isinstance(item, Mapping):
            if not item or not all(isinstance(key, str) for key in item):
                raise ValueError("action mappings require nonempty string keys")
            return {key: visit(child, depth + 1) for key, child in item.items()}
        if isinstance(item, (tuple, list)):
            if not item:
                raise ValueError("action sequences cannot be empty")
            return [visit(child, depth + 1) for child in item]
        if not finite_scalar(item):
            raise ValueError("action values must be finite numbers")
        return int(item) if isinstance(item, int) else float(item)

    return visit(value, 0)


def finite_action(value: Any) -> bool:
    try:
        _canonical_action(value)
        return True
    except (TypeError, ValueError, OverflowError, RecursionError):
        return False


def decision_error(
    decision: PolicyDecision,
    expected_version: str | None = None,
    expected_context: str | None = None,
) -> str | None:
    if decision.error is not None:
        return (
            decision.error
            if isinstance(decision.error, str) and decision.error
            else "invalid_error"
        )
    if not finite_action(decision.action):
        return "nonfinite_action"
    if not finite_scalar(decision.logprob) or decision.logprob > 0:
        return "invalid_logprob"
    if not finite_scalar(decision.confidence) or not 0 <= decision.confidence <= 1:
        return "invalid_confidence"
    if not isinstance(decision.in_distribution, bool):
        return "invalid_ood_flag"
    if not finite_scalar(decision.latency_ms) or decision.latency_ms < 0:
        return "invalid_latency"
    if not isinstance(decision.policy_version, str) or not decision.policy_version:
        return "invalid_version"
    if not isinstance(decision.context_hash, str) or not decision.context_hash:
        return "invalid_context_hash"
    if expected_version is not None and decision.policy_version != expected_version:
        return "version_mismatch"
    if expected_context is not None and decision.context_hash != expected_context:
        return "context_mismatch"
    return None


class PolicyRunner:
    """Private session snapshot, pinned version, bounded daemon inference.

    A timed-out runner is quarantined for the rest of the session. A late result
    can never actuate, and subsequent ticks cannot spawn unbounded workers.
    Policies receive copies of context and have no engine/session reference.
    This isolates trusted local models; it is not a sandbox for hostile code.
    """

    def __init__(
        self, policy: Policy, timeout_ms: float, expected_version: str | None = None
    ):
        self.policy = copy.deepcopy(policy)
        self.version = expected_version or self.policy.version
        self.timeout_ms = timeout_ms
        self._quarantined = False
        self._calibrated = False

    @property
    def calibrated(self) -> bool:
        # Never call model properties synchronously from the control path.
        return self._calibrated

    def propose(self, ctx: Context) -> PolicyDecision:
        fingerprint = context_hash(ctx)

        def failed(reason: str, elapsed: float = 0.0) -> PolicyDecision:
            return PolicyDecision(
                0.0, 0.0, 0.0, False, self.version, fingerprint, reason, elapsed
            )

        if self._quarantined:
            return failed("inference_quarantined")
        self._calibrated = False
        result: list[tuple[PolicyDecision, bool]] = []
        started = time.monotonic()
        snapshot = copy.deepcopy(dict(ctx))

        def infer() -> None:
            try:
                if self.policy.version != self.version:
                    result.append((failed("version_mismatch"), False))
                    return
                decision = self.policy.propose(snapshot)
                if not isinstance(decision, PolicyDecision):
                    result.append((failed("invalid_decision"), False))
                    return
                error = decision_error(decision, self.version, fingerprint)
                try:
                    action = _canonical_action(decision.action)
                except (TypeError, ValueError, OverflowError, RecursionError):
                    action = None
                    error = error or "nonfinite_action"
                calibrated = getattr(self.policy, "calibrated", False) is True
                if self.policy.version != self.version:
                    error = "version_mismatch"
                # Rejected payloads still need safe, inspectable logging. Do not
                # let malformed metadata escape via a fallback audit record.
                safe_decision = PolicyDecision(
                    action,
                    float(decision.logprob) if finite_scalar(decision.logprob) else 0.0,
                    float(decision.confidence)
                    if finite_scalar(decision.confidence)
                    else 0.0,
                    decision.in_distribution
                    if isinstance(decision.in_distribution, bool)
                    else False,
                    decision.policy_version
                    if isinstance(decision.policy_version, str)
                    else self.version,
                    decision.context_hash
                    if isinstance(decision.context_hash, str)
                    else fingerprint,
                    error,
                )
                result.append((safe_decision, calibrated and error is None))
            except Exception as exc:  # noqa: BLE001 - all inference failures fall back
                result.append((failed(f"inference_error:{type(exc).__name__}"), False))

        worker = threading.Thread(target=infer, daemon=True)
        worker.start()
        worker.join(self.timeout_ms / 1000.0)
        elapsed = (time.monotonic() - started) * 1000.0
        if worker.is_alive() or elapsed > self.timeout_ms:
            self._quarantined = True
            return failed("inference_timeout", elapsed)
        if not result:
            return failed("inference_failed", elapsed)
        decision, self._calibrated = result[0]
        return replace(decision, latency_ms=elapsed)


class BaselinePolicy:
    """Adapt a PI step already evaluated by the deterministic controller."""

    version = stable_hash({"baseline": "bounded-pi-v1"})
    calibrated = True

    def propose(self, context: Context) -> PolicyDecision:
        return PolicyDecision(
            float(context["baseline_output"]),
            0.0,
            1.0,
            True,
            self.version,
            context_hash(context),
        )
