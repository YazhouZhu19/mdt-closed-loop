"""v2.1 single-writer execution contract for deterministic simulation.

All times share a monotonically increasing, session-relative clock. A target is
an immutable absolute vector; each real boundary produces a new fixed command.
Only a matching ACK advances the authoritative output. The synchronous engine
contract is deliberately limited to offline adapters; it is not a hard real-time
or hardware watchdog. Call poll() from the simulator clock even without sensors.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from threading import RLock
from typing import Protocol

from .config import ExecutionConfig, GrammarConfig
from .l35_guard import GuardLimits, project
from .types import MusicParams


@dataclass(frozen=True)
class MusicVector:
    tempo: float
    layer_mask: int
    register: float
    dynamics: float
    harmonic_brightness: float
    rhythmic_accent: float
    reverb_depth: float

    def __post_init__(self) -> None:
        MusicParams(**self.__dict__)

    @classmethod
    def of(cls, params: MusicParams) -> MusicVector:
        return cls(**params.copy().as_dict())

    def params(self) -> MusicParams:
        return MusicParams(**self.__dict__)


class Mode(str, Enum):
    INITIALIZING = "INITIALIZING"
    BASELINE = "BASELINE"
    LEARNING_ENABLED = "LEARNING_ENABLED"
    DEGRADED = "DEGRADED"
    HOLD = "HOLD"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"


@dataclass(frozen=True)
class AcceptedDecision:
    epoch: str
    sequence: int
    observation_id: str
    observation_end: float
    created_at: float
    parent_ack_id: str
    target: MusicVector
    mode: Mode = Mode.BASELINE

    def __post_init__(self) -> None:
        if (
            not isinstance(self.sequence, int)
            or isinstance(self.sequence, bool)
            or self.sequence < 0
        ):
            raise ValueError("decision sequence must be a nonnegative integer")
        if not isinstance(self.target, MusicVector) or not isinstance(self.mode, Mode):
            raise TypeError("decision requires MusicVector and Mode")
        for time in (self.observation_end, self.created_at):
            if not isinstance(time, (int, float)) or isinstance(time, bool):
                raise TypeError("decision timestamps must be numeric")


@dataclass(frozen=True)
class ExecutionCommand:
    epoch: str
    command_id: str
    decision_sequence: int
    parent_ack_id: str
    boundary_at: float
    projected: MusicVector


@dataclass(frozen=True)
class ExecutionAck:
    epoch: str
    command_id: str
    executed_at: float
    executed: MusicVector

    def __post_init__(self) -> None:
        if (
            not isinstance(self.executed_at, (float, int))
            or isinstance(self.executed_at, bool)
            or not math.isfinite(self.executed_at)
        ):
            raise ValueError("ACK time must be finite and numeric")
        if not isinstance(self.executed, MusicVector):
            raise TypeError("ACK requires an immutable MusicVector")


class AckEngine(Protocol):
    def start(self, session_id: str, params: MusicParams) -> None: ...
    def submit(self, command: ExecutionCommand) -> ExecutionAck | None: ...
    def stop(self) -> None: ...


class ExecutionGateway:
    """One atomic music-vector channel; latest accepted decision replaces it.

    Restart creates a new epoch. No persisted session resume is currently
    supported; commands and ACKs from an earlier epoch are rejected.
    """

    def __init__(
        self,
        epoch: str,
        engine: AckEngine,
        cfg: ExecutionConfig,
        grammar: GrammarConfig,
        initial: MusicParams,
    ):
        if not epoch or not callable(getattr(engine, "submit", None)):
            raise ValueError("v2.1 requires an epoch and an ACK-capable engine")
        if initial.dynamics > cfg.dynamics_max:
            raise ValueError("initial dynamics exceeds the frozen software cap")
        if not grammar.tempo_range[0] <= initial.tempo <= grammar.tempo_range[1]:
            raise ValueError("initial tempo exceeds the frozen range")
        self.epoch, self.engine, self.cfg, self.grammar = epoch, engine, cfg, grammar
        self._current = MusicVector.of(initial)
        self.ack_id = "bootstrap"
        self.mode = Mode.INITIALIZING
        self.pending: AcceptedDecision | None = None
        self.events: list[dict] = []
        self.last_command: ExecutionCommand | None = None
        self.last_ack: ExecutionAck | None = None
        self._lock = RLock()
        self._now = 0.0
        self._last_boundary: float | None = None
        self._last_execution = 0.0
        self._last_tempo_change: float | None = None
        self._last_sequence = -1
        self._last_observation_end: float | None = None
        self._hold_since: float | None = None
        self._recoveries = 0
        self._started = False

    @property
    def current(self) -> MusicParams:
        return self._current.params()

    def _event(self, kind: str, **data) -> None:
        self.events.append(
            {
                "t": self._now,
                "event": kind,
                "epoch": self.epoch,
                "mode": self.mode.value,
                **data,
            }
        )

    def _clock(self, now: float) -> None:
        if not math.isfinite(now) or now < self._now:
            raise ValueError("execution clock must be finite and monotonic")
        self._now = now

    def _fresh(self, decision: AcceptedDecision, now: float) -> bool:
        obs_age = now - decision.observation_end
        proc_age = now - decision.created_at
        return (
            math.isfinite(obs_age)
            and math.isfinite(proc_age)
            and 0 <= obs_age <= self.cfg.observation_ttl_s
            and 0 <= proc_age <= self.cfg.decision_ttl_s
            and decision.observation_end <= decision.created_at
        )

    def poll(self, now: float) -> Mode:
        """Sensor-independent simulated watchdog tick; terminal stop is latched."""
        with self._lock:
            self._clock(now)
            if self.mode in (Mode.STOPPING, Mode.STOPPED):
                return self.mode
            if self.mode is Mode.INITIALIZING and self._hold_since is None:
                self._hold_since = now
            if self.pending is not None and not self._fresh(self.pending, now):
                expiry = min(
                    self.pending.observation_end + self.cfg.observation_ttl_s,
                    self.pending.created_at + self.cfg.decision_ttl_s,
                )
                self.hold(now, "expired", since=expiry)
            if (
                self._hold_since is not None
                and now - self._hold_since >= self.cfg.hold_max_s
            ):
                self.stop(now, "hold_timeout")
            return self.mode

    def hold(self, now: float, reason: str, *, since: float | None = None) -> None:
        with self._lock:
            self._clock(now)
            if self.mode in (Mode.STOPPING, Mode.STOPPED):
                return
            self.pending = None
            self._recoveries = 0
            if self._hold_since is None:
                self._hold_since = now if since is None else since
            self.mode = Mode.HOLD
            self._event("hold", reason=reason)

    def accept(self, decision: AcceptedDecision, now: float) -> bool:
        with self._lock:
            self.poll(now)
            reason = None
            if self.mode in (Mode.STOPPING, Mode.STOPPED):
                reason = "terminal"
            elif decision.epoch != self.epoch:
                reason = "epoch"
            elif decision.sequence <= self._last_sequence:
                reason = "sequence"
            elif decision.parent_ack_id != self.ack_id:
                reason = "parent_ack"
            elif not decision.observation_id or not self._fresh(decision, now):
                reason = "freshness"
            elif (
                self._last_observation_end is not None
                and decision.observation_end <= self._last_observation_end
            ):
                reason = "reused_observation"
            elif decision.mode not in (
                Mode.BASELINE,
                Mode.LEARNING_ENABLED,
                Mode.DEGRADED,
            ):
                reason = "inapplicable_mode"
            if reason:
                self._event("reject", reason=reason, sequence=decision.sequence)
                # A delayed old packet must not revoke a newer valid decision.
                if reason == "freshness" and self.pending is None:
                    self.hold(now, reason)
                return False
            self._last_sequence = decision.sequence
            self._last_observation_end = decision.observation_end
            if self.mode in (Mode.INITIALIZING, Mode.HOLD):
                self._recoveries += 1
                if self._recoveries < self.cfg.recovery_observations:
                    self._event("recovery_wait", sequence=decision.sequence)
                    # Start a finite startup deadline even before audio exists.
                    if self._hold_since is None:
                        self._hold_since = now
                    return False
                self.mode = Mode.BASELINE
                # First recovered decision must use the baseline branch.
                if decision.mode is not Mode.BASELINE:
                    self._event("reject", reason="recovery_requires_baseline")
                    self.mode = Mode.HOLD
                    self._recoveries = 0
                    return False
            else:
                self.mode = decision.mode
            self._hold_since = None
            self.pending = decision
            self._event(
                "accepted",
                sequence=decision.sequence,
                observation_id=decision.observation_id,
                observation_end=decision.observation_end,
                created_at=decision.created_at,
                parent_ack_id=decision.parent_ack_id,
                target=decision.target.__dict__,
            )
            return True

    def boundary(self, now: float, *, phrase: bool = False) -> MusicParams:
        with self._lock:
            self.poll(now)
            if self._last_boundary is not None and now <= self._last_boundary:
                self._event("duplicate_boundary")
                return self.current
            self._last_boundary = now
            decision = self.pending
            if decision is None or self.mode in (
                Mode.HOLD,
                Mode.STOPPING,
                Mode.STOPPED,
            ):
                return self.current
            elapsed = (
                0.0
                if self._last_tempo_change is None
                else now - self._last_tempo_change
            )
            # Rate budgets accrue only since session start, never from a
            # fabricated initial 30-second allowance in this profile.
            if self._last_tempo_change is None:
                elapsed = now
            candidate = project(
                decision.target.params(),
                GuardLimits(self.current, elapsed, self.grammar, True, phrase),
            )
            delta = self.cfg.dynamics_rate_per_s * (now - self._last_execution)
            candidate.dynamics = min(
                self.cfg.dynamics_max,
                max(
                    self.current.dynamics - delta,
                    min(self.current.dynamics + delta, candidate.dynamics),
                ),
            )
            command = ExecutionCommand(
                self.epoch,
                f"{self.epoch}:{decision.sequence}:{float(now).hex()}",
                decision.sequence,
                self.ack_id,
                now,
                MusicVector.of(candidate),
            )
            self.last_command = command
            self._event(
                "command",
                command_id=command.command_id,
                sequence=decision.sequence,
                projected=command.projected.__dict__,
            )
            try:
                if not self._started:
                    self.engine.start(self.epoch, self.current)
                    self._started = True
                ack = self.engine.submit(command)
            except Exception as exc:  # noqa: BLE001 - external adapter failure revokes writes
                self._event("engine_error", error=type(exc).__name__)
                self.stop(now, "unknown_execution")
                return self.current
            if (
                not isinstance(ack, ExecutionAck)
                or ack.epoch != self.epoch
                or ack.command_id != command.command_id
                or not math.isfinite(ack.executed_at)
                or ack.executed_at != now
                or ack.executed != command.projected
            ):
                self._event("invalid_ack", command_id=command.command_id)
                self.stop(now, "unknown_execution")
                return self.current
            if ack.executed.tempo != self._current.tempo:
                self._last_tempo_change = now
            self._current, self.ack_id = ack.executed, ack.command_id
            self.last_ack = ack
            self._last_execution = now
            self._event(
                "ack",
                command_id=ack.command_id,
                executed_at=ack.executed_at,
                executed=ack.executed.__dict__,
            )
            return self.current

    def stop(self, now: float, reason: str = "requested") -> None:
        with self._lock:
            self._clock(now)
            if self.mode in (Mode.STOPPING, Mode.STOPPED):
                return
            # Revoke write authority before invoking an external adapter.
            self.mode = Mode.STOPPING
            self.pending = None
            self._event("stopping", reason=reason)
            try:
                self.engine.stop()
            except Exception as exc:  # noqa: BLE001 - stop failure must stay latched
                self._event("stop_unconfirmed", error=type(exc).__name__)
                return
            self.mode = Mode.STOPPED
            self._event("stopped", reason=reason)
