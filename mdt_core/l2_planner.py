"""L2 目标轨迹规划。

strategy 是 A/B 变量而非常量：同质原理是否优于直接引导尚无定论
(Starcke 等, 38 名心境障碍患者的随机实验未显示同质组占优)。
"""

from __future__ import annotations

import math

from .config import PlannerConfig
from .types import State, Strategy


class TrajectoryPlanner:
    """Generate fixed or response-adaptive arousal reference trajectories.

    The fixed ISO trajectory remains available for the open-loop research arm.
    The adaptive variant advances only when the state estimate is reliable and
    slows down when the participant lags behind the current reference.
    """

    def __init__(self, strategy: Strategy, cfg: PlannerConfig):
        self.strategy = strategy
        self.cfg = cfg
        self._anchor: float | None = None
        self._adaptive_progress = 0.0
        self._adaptive_target: float | None = None
        self._adaptive_last_t: float | None = None
        self._phase = "unanchored"
        self._speed_factor = 0.0

    def set_anchor(self, initial_arousal: float) -> None:
        """同质原理的起点：会话开始时的实测唤醒度。"""
        if not math.isfinite(initial_arousal) or not 0.0 <= initial_arousal <= 1.0:
            raise ValueError("initial_arousal must be in [0, 1]")
        self._anchor = initial_arousal
        self._adaptive_progress = 0.0
        self._adaptive_target = initial_arousal
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
        return min(anchor, self.cfg.floor_arousal)

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
        if t < c.iso_match_duration_s:
            self._phase = "match"
            self._speed_factor = 0.0
            return anchor
        if t < c.iso_match_duration_s + c.descent_duration_s:
            progress = (t - c.iso_match_duration_s) / c.descent_duration_s
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

        if t < self.cfg.iso_match_duration_s:
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

        start_t = self.cfg.iso_match_duration_s
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

        self._speed_factor = speed
        self._adaptive_progress = min(
            1.0,
            self._adaptive_progress
            + effective_dt * speed / self.cfg.descent_duration_s,
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
