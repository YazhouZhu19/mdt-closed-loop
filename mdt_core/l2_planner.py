"""L2 目标轨迹规划。

strategy 是 A/B 变量而非常量：同质原理是否优于直接引导尚无定论
(Starcke 等, 38 名心境障碍患者的随机实验未显示同质组占优)。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from .config import PlannerConfig
from .types import State, Strategy


@dataclass(frozen=True)
class TrajectoryParams:
    """Bounded actions available to the learned L2 policy.

    These limits apply to learned actions, not to legacy ``PlannerConfig``.
    The randomized study strategy is never part of the learned action space.
    """

    anchor_offset: float = 0.0
    match_seconds: float = 300.0
    descent_seconds: float = 900.0
    floor: float = 0.15
    speed_gain: float = 1.0

    def __post_init__(self) -> None:
        limits = {
            "anchor_offset": (-0.10, 0.05),
            "match_seconds": (120.0, 600.0),
            "descent_seconds": (600.0, 1800.0),
            "floor": (0.10, 0.45),
            "speed_gain": (0.50, 1.50),
        }
        for name, (low, high) in limits.items():
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not low <= value <= high
            ):
                raise ValueError(f"{name} must be finite and in [{low}, {high}]")

    def as_dict(self) -> dict[str, float]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, action: Mapping[str, Any]) -> TrajectoryParams:
        # Require the full action: silently filling omissions would change the
        # action whose logged propensity is later used by offline evaluation.
        expected = set(cls.__dataclass_fields__)
        if set(action) != expected:
            raise ValueError("trajectory action must contain exactly five parameters")
        return cls(**dict(action))


class TrajectoryPlanner:
    """Generate fixed or response-adaptive arousal reference trajectories.

    The fixed ISO trajectory remains available for the open-loop research arm.
    The adaptive variant advances only when the state estimate is reliable and
    slows down when the participant lags behind the current reference.
    """

    def __init__(
        self,
        strategy: Strategy,
        cfg: PlannerConfig,
        params: TrajectoryParams | None = None,
    ):
        self.strategy = strategy
        self.cfg = cfg
        self._params = params
        if params is not None and not isinstance(params, TrajectoryParams):
            raise TypeError("params must be TrajectoryParams or None")
        self._anchor: float | None = None
        self._adaptive_progress = 0.0
        self._adaptive_target: float | None = None
        self._adaptive_last_t: float | None = None
        self._phase = "unanchored"
        self._speed_factor = 0.0

    @property
    def params(self) -> TrajectoryParams | None:
        return self._params

    def set_params(self, params: TrajectoryParams) -> None:
        """Freeze a bounded L2 proposal before the session is anchored."""
        # Low-quality startup windows can advance the unanchored baseline
        # clock. They do not constitute a valid anchoring/learning decision.
        if self._anchor is not None:
            raise RuntimeError("trajectory parameters cannot change during a session")
        if not isinstance(params, TrajectoryParams):
            raise TypeError("params must be TrajectoryParams")
        self._params = params

    @property
    def _match_seconds(self) -> float:
        return (
            self._params.match_seconds
            if self._params is not None
            else self.cfg.iso_match_duration_s
        )

    @property
    def _descent_seconds(self) -> float:
        return (
            self._params.descent_seconds
            if self._params is not None
            else self.cfg.descent_duration_s
        )

    def set_anchor(self, initial_arousal: float) -> None:
        """同质原理的起点：会话开始时的实测唤醒度。"""
        if not math.isfinite(initial_arousal) or not 0.0 <= initial_arousal <= 1.0:
            raise ValueError("initial_arousal must be in [0, 1]")
        offset = self._params.anchor_offset if self._params is not None else 0.0
        self._anchor = min(1.0, max(0.0, initial_arousal + offset))
        self._adaptive_progress = 0.0
        self._adaptive_target = self._anchor
        self._adaptive_last_t = None
        self._phase = "match"
        self._speed_factor = 0.0

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def speed_factor(self) -> float:
        return self._speed_factor

    def _goal(self, anchor: float) -> float:
        # The sleep-oriented ISO path is monotonic downward.  A participant who
        # already starts below the configured floor is held rather than aroused.
        floor = self._params.floor if self._params is not None else self.cfg.floor_arousal
        return min(anchor, floor)

    def target(self, t: float) -> float:
        if not math.isfinite(t) or t < 0:
            raise ValueError("trajectory time must be finite and >= 0")
        c = self.cfg
        if self.strategy is Strategy.DIRECT:
            self._phase = "direct"
            self._speed_factor = 0.0
            return c.direct_target

        anchor = self._anchor if self._anchor is not None else 0.6
        goal = self._goal(anchor)
        if t < self._match_seconds:
            self._phase = "match"
            self._speed_factor = 0.0
            return anchor
        if t < self._match_seconds + self._descent_seconds:
            progress = (t - self._match_seconds) / self._descent_seconds
            self._phase = "descent"
            self._speed_factor = 1.0
            return anchor + (goal - anchor) * progress
        self._phase = "hold"
        self._speed_factor = 0.0
        return goal

    def adaptive_target(self, t: float, state: State, reliability: float) -> float:
        """Return a monotonic ISO target whose rate follows observed tracking.

        This method is intentionally stateful and must be called in monotonic
        session-time order.  ``reliability`` is supplied by the uncertainty-aware
        controller so planning and actuation share the same safety gate.
        """
        if not math.isfinite(t) or t < 0:
            raise ValueError("trajectory time must be finite and >= 0")
        if not math.isfinite(reliability) or not 0.0 <= reliability <= 1.0:
            raise ValueError("reliability must be in [0, 1]")
        if self._adaptive_last_t is not None and t < self._adaptive_last_t:
            raise ValueError("adaptive trajectory time must be monotonic")
        if self.strategy is Strategy.DIRECT or not self.cfg.adaptive_iso:
            return self.target(t)

        anchor = self._anchor if self._anchor is not None else 0.6
        if self._adaptive_target is None:
            self._adaptive_target = anchor
        previous_t = self._adaptive_last_t
        self._adaptive_last_t = t

        if t < self._match_seconds:
            self._phase = "match"
            self._speed_factor = 0.0
            self._adaptive_target = anchor
            return self._adaptive_target

        goal = self._goal(anchor)
        if math.isclose(anchor, goal, abs_tol=1e-12):
            self._adaptive_progress = 1.0
            self._adaptive_target = goal
            self._phase = "hold"
            self._speed_factor = 0.0
            return goal

        start_t = self._match_seconds
        effective_dt = (
            max(0.0, t - start_t)
            if previous_t is None
            else max(0.0, t - max(previous_t, start_t))
        )

        if reliability < self.cfg.adaptive_min_reliability:
            speed = 0.0
        else:
            # For a descending trajectory, positive lag means measured arousal
            # remains above the reference.  Interpolate between cautious and
            # accelerated progress rather than switching discontinuously.
            lag = state.arousal - self._adaptive_target
            on_track = self.cfg.adaptive_on_track_error
            far_behind = self.cfg.adaptive_lag_error
            if lag <= on_track:
                speed = self.cfg.adaptive_max_speed
            elif lag >= far_behind:
                speed = self.cfg.adaptive_min_speed
            else:
                fraction = (lag - on_track) / (far_behind - on_track)
                speed = self.cfg.adaptive_max_speed + fraction * (
                    self.cfg.adaptive_min_speed - self.cfg.adaptive_max_speed
                )
            speed *= reliability
            if self._params is not None:
                speed = min(
                    self.cfg.adaptive_max_speed,
                    speed * self._params.speed_gain,
                )

        self._speed_factor = speed
        self._adaptive_progress = min(
            1.0,
            self._adaptive_progress
            + effective_dt * speed / self._descent_seconds,
        )
        self._adaptive_target = anchor + (goal - anchor) * self._adaptive_progress
        self._phase = "hold" if self._adaptive_progress >= 1.0 else "descent"
        if self._phase == "hold":
            self._speed_factor = 0.0
        return self._adaptive_target


class DoseTracker:
    """按剂量-反应分段判定所处效应区间 (Gold 等)。"""

    def __init__(self, cfg: PlannerConfig):
        self.cfg = cfg
        self.completed = 0

    def record_completion(self) -> None:
        self.completed += 1

    @property
    def band(self) -> str:
        if self.completed < 0:
            raise ValueError("completed sessions cannot be negative")
        matches = []
        for lo, hi, name in self.cfg.dose_bands:
            if lo <= self.completed <= hi:
                matches.append(name)
        if matches:
            return matches[-1]
        if self.completed > max(hi for _lo, hi, _name in self.cfg.dose_bands):
            return "above_studied_range"
        return "below_threshold"

    @property
    def sessions_to_next_band(self) -> int:
        for lo, _hi, _name in self.cfg.dose_bands:
            if self.completed < lo:
                return lo - self.completed
        return 0
