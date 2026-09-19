"""Explicit v2.1 research estimands; these helpers do not establish efficacy.

Tracking is integrated over predeclared, piecewise-constant evaluation intervals.
Music variation uses actual ACKs and is a boundary sum, never a time integral.
Contract rejection, submission and confirmed execution remain different events.
Callers must freeze evaluation references, channel scales and definitions before
comparisons, and group observations by independent session/participant as needed.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from .execution import ExecutionAck
from .types import MusicParams

CONTINUOUS_MUSIC_CHANNELS = (
    "tempo",
    "register",
    "dynamics",
    "harmonic_brightness",
    "rhythmic_accent",
    "reverb_depth",
)


def _finite(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite number")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")


def _count(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")


@dataclass(frozen=True)
class TrackingInterval:
    """Evaluation values held constant on [start, end), not interpolated labels.

    Missing intervals must use ``evaluable=False``; values may then be None.
    For sparse subjective ratings, use a separately defined discrete endpoint,
    rather than pretending these intervals contain continuous ground truth.
    """

    start: float
    end: float
    state: float | None
    reference: float | None
    evaluable: bool = True

    def __post_init__(self) -> None:
        _finite("start", self.start)
        _finite("end", self.end)
        if self.start < 0 or self.end <= self.start:
            raise ValueError("intervals require 0 <= start < end")
        if not isinstance(self.evaluable, bool):
            raise TypeError("evaluable must be bool")
        for name in ("state", "reference"):
            value = getattr(self, name)
            if value is None:
                if self.evaluable:
                    raise ValueError("evaluable intervals require state and reference")
            else:
                _finite(name, value)


@dataclass(frozen=True)
class TrackingSummary:
    integrated_squared_error: float
    evaluable_duration: float
    unevaluable_duration: float
    total_duration: float
    coverage: float
    rmse: float | None


def tracking_cost(intervals: Sequence[TrackingInterval]) -> TrackingSummary:
    """Sum (state-reference)^2 * duration using a frozen external evaluator.

    Input must be chronological and non-overlapping. The evaluation horizon is
    [first.start, last.end); gaps inside it count as unevaluable duration. Supply
    explicit unevaluable intervals at the ends to include missing leading or
    trailing periods. No error value is imputed for missing periods. Coverage
    accompanies ISE/RMSE so excluding hard periods cannot look like success.
    """
    if not intervals:
        raise ValueError("at least one evaluation interval is required")
    error_terms = []
    valid_durations = []
    previous_end = intervals[0].start
    for interval in intervals:
        if interval.start < previous_end:
            raise ValueError("evaluation intervals must be ordered and non-overlapping")
        previous_end = interval.end
        if interval.evaluable:
            assert interval.state is not None and interval.reference is not None
            error = interval.state - interval.reference
            duration = interval.end - interval.start
            term = error * error * duration
            _finite("squared-error integral", term)
            error_terms.append(term)
            valid_durations.append(duration)
    total = intervals[-1].end - intervals[0].start
    valid = math.fsum(valid_durations)
    ise = math.fsum(error_terms)
    _finite("squared-error integral", ise)
    return TrackingSummary(
        ise,
        valid,
        max(0.0, total - valid),
        total,
        valid / total,
        math.sqrt(ise / valid) if valid > 0 else None,
    )


@dataclass(frozen=True)
class MusicChangeSummary:
    normalized_squared_change: float
    acknowledged_commands: int
    layer_transitions: int


def music_change_cost(
    initial: MusicParams,
    acknowledgements: Sequence[ExecutionAck],
    *,
    scales: Mapping[str, float],
    weights: Mapping[str, float] | None = None,
) -> MusicChangeSummary:
    """Compute sum_ACK sum_j w_j * ((p_j-p_prev,j)/scale_j)^2.

    This is the squared W norm with W_jj=w_j/scale_j^2, summed once per
    confirmed action boundary. Positive scales must be supplied for all six
    continuous channels; optional nonnegative weights must also cover all six.
    There is no dt multiplier. Discrete layer masks are excluded from this norm
    and reported as the number of ACK-to-ACK mask transitions (not bit counts).

    The caller supplies the initial confirmed/bootstrap vector and only actual
    execution acknowledgements, never proposed/submitted vectors. Exact repeated
    ACKs are deduplicated; a reused ID with changed payload is an error. Records
    from different epochs or decreasing execution times must not be combined.
    """
    channels = set(CONTINUOUS_MUSIC_CHANNELS)
    if set(scales) != channels:
        raise ValueError("scales must cover exactly the continuous music channels")
    weights = {name: 1.0 for name in channels} if weights is None else weights
    if set(weights) != channels:
        raise ValueError("weights must cover exactly the continuous music channels")
    for name in channels:
        _finite(f"scale {name}", scales[name])
        _finite(f"weight {name}", weights[name])
        if scales[name] <= 0 or weights[name] < 0:
            raise ValueError("scales must be positive and weights nonnegative")
    if not any(weights.values()):
        raise ValueError("at least one channel weight must be positive")
    previous = initial.copy()
    seen: dict[str, ExecutionAck] = {}
    epoch: str | None = None
    last_time = -math.inf
    terms = []
    transitions = 0
    for ack in acknowledgements:
        if not isinstance(ack, ExecutionAck):
            raise TypeError("music cost requires ExecutionAck records")
        if not isinstance(ack.command_id, str) or not ack.command_id:
            raise ValueError("ACK command_id must be a nonempty string")
        if not isinstance(ack.epoch, str) or not ack.epoch:
            raise ValueError("ACK epoch must be a nonempty string")
        _finite("ACK executed_at", ack.executed_at)
        if ack.executed_at < 0:
            raise ValueError("ACK executed_at must be nonnegative")
        duplicate = seen.get(ack.command_id)
        if duplicate is not None:
            if ack != duplicate:
                raise ValueError("ACK command ID reused with a different payload")
            continue
        if epoch is not None and ack.epoch != epoch:
            raise ValueError("cannot combine ACKs from different epochs")
        if ack.executed_at < last_time:
            raise ValueError("ACK execution times must be nondecreasing")
        current = ack.executed.params()
        for name in CONTINUOUS_MUSIC_CHANNELS:
            delta = (getattr(current, name) - getattr(previous, name)) / scales[name]
            term = weights[name] * delta * delta
            _finite("normalized music change", term)
            terms.append(term)
        transitions += int(current.layer_mask != previous.layer_mask)
        seen[ack.command_id] = ack
        epoch, last_time, previous = ack.epoch, ack.executed_at, current
    cost = math.fsum(terms)
    _finite("music change sum", cost)
    return MusicChangeSummary(cost, len(seen), transitions)


@dataclass(frozen=True)
class CommitAttempt:
    """One request classified by a separate frozen contract evaluator.

    committed means the request crossed the execution submit/commit point; it
    does not assert receipt of an engine ACK. violates_contract is an independent
    validity verdict. A rejected invalid proposal has committed=False and never
    becomes an invalid commit merely because rejection was logged.
    """

    attempt_id: str
    committed: bool
    violates_contract: bool

    def __post_init__(self) -> None:
        if not isinstance(self.attempt_id, str) or not self.attempt_id:
            raise ValueError("attempt_id must be a nonempty string")
        if not isinstance(self.committed, bool) or not isinstance(
            self.violates_contract, bool
        ):
            raise TypeError("commit and validity flags must be bool")


@dataclass(frozen=True)
class SessionCommitSummary:
    attempts: int
    rejected_attempts: int
    actual_commits: int
    invalid_commits: int
    commit_opportunities: int
    invalid_per_commit: float | None
    invalid_per_opportunity: float | None
    any_invalid_commit: bool


def session_commit_metrics(
    attempts: Sequence[CommitAttempt],
    *,
    commit_opportunities: int,
) -> SessionCommitSummary:
    """Report counts and both predeclared denominators for one session.

    Opportunities must be defined before the experiment. Multiple invalid
    submissions per opportunity can make invalid_per_opportunity exceed one;
    that quantity is a rate, not an independent Bernoulli risk. Zero denominators
    return None rather than falsely claiming a zero event rate. Independence
    across sessions is not implied by these within-session summaries.
    """
    _count("commit_opportunities", commit_opportunities)
    ids = [attempt.attempt_id for attempt in attempts]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate attempt IDs must be resolved before counting")
    committed = sum(attempt.committed for attempt in attempts)
    invalid = sum(
        attempt.committed and attempt.violates_contract for attempt in attempts
    )
    return SessionCommitSummary(
        len(attempts),
        len(attempts) - committed,
        committed,
        invalid,
        commit_opportunities,
        invalid / committed if committed else None,
        invalid / commit_opportunities if commit_opportunities else None,
        invalid > 0,
    )


def zero_event_session_risk_upper_bound(
    independent_sessions: int,
    *,
    alpha: float = 0.05,
) -> float:
    """Exact one-sided (1-alpha) upper bound after zero event-bearing sessions.

    Returns 1-alpha**(1/n) under independent, identically distributed Bernoulli
    session risk. n counts independent sessions, never windows, repeated seeds,
    boundaries or correlated sessions of the same participant. Call only after
    observing zero sessions with at least one invalid commit; this does not
    estimate the per-commit rate. Zero tested sessions provide no such bound.
    """
    _count("independent_sessions", independent_sessions)
    if independent_sessions == 0:
        raise ValueError("at least one independent session is required")
    _finite("alpha", alpha)
    if not 0 < alpha < 1:
        raise ValueError("alpha must be strictly between zero and one")
    return -math.expm1(math.log(alpha) / independent_sessions)


def critical_path_seconds(
    durations: Mapping[str, float],
    predecessors: Mapping[str, Iterable[str]],
) -> float:
    """Ideal dependency-DAG makespan: serial sum, parallel predecessor maximum.

    Durations must use one clock/unit and include the operations whose path is
    being assessed. Missing predecessor entries mean roots. This calculation
    assumes unlimited parallel resources and adds no scheduling, queue or engine
    overhead; it is not a measurement of end-to-end latency or a hard deadline.
    """
    for node, duration in durations.items():
        if not isinstance(node, str) or not node:
            raise ValueError("DAG node names must be nonempty strings")
        _finite(f"duration {node}", duration)
        if duration < 0:
            raise ValueError("DAG durations must be nonnegative")
    if set(predecessors) - set(durations):
        raise ValueError("predecessor map names an unknown node")
    incoming = {}
    children: dict[str, list[str]] = {node: [] for node in durations}
    for node in durations:
        parents = tuple(predecessors.get(node, ()))
        if len(parents) != len(set(parents)):
            raise ValueError("DAG has duplicate dependencies")
        if set(parents) - set(durations):
            raise ValueError("DAG dependency names an unknown node")
        incoming[node] = len(parents)
        for parent in parents:
            children[parent].append(node)
    ready = deque(node for node in durations if incoming[node] == 0)
    earliest_start = {node: 0.0 for node in durations}
    completed = 0
    longest = 0.0
    while ready:
        node = ready.popleft()
        finish = earliest_start[node] + durations[node]
        _finite("critical path", finish)
        longest = max(longest, finish)
        completed += 1
        for child in children[node]:
            earliest_start[child] = max(earliest_start[child], finish)
            incoming[child] -= 1
            if incoming[child] == 0:
                ready.append(child)
    if completed != len(durations):
        raise ValueError("dependency graph contains a cycle")
    return longest
