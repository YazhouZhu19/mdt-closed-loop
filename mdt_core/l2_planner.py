"""L2 目标轨迹规划。

strategy 是 A/B 变量而非常量：同质原理是否优于直接引导尚无定论
(Starcke 等, 38 名心境障碍患者的随机实验未显示同质组占优)。
"""

from __future__ import annotations

import math

from .config import PlannerConfig
from .types import Strategy


class TrajectoryPlanner:
    """输出 a*(t)。睡前场景用不回升的 U 型：匹配 -> 下降 -> 谷底维持。"""

    def __init__(self, strategy: Strategy, cfg: PlannerConfig):
        self.strategy = strategy
        self.cfg = cfg
        self._anchor: float | None = None

    def set_anchor(self, initial_arousal: float) -> None:
        """同质原理的起点：会话开始时的实测唤醒度。"""
        if not math.isfinite(initial_arousal) or not 0.0 <= initial_arousal <= 1.0:
            raise ValueError("initial_arousal must be in [0, 1]")
        self._anchor = initial_arousal

    def target(self, t: float) -> float:
        if not math.isfinite(t) or t < 0:
            raise ValueError("trajectory time must be finite and >= 0")
        c = self.cfg
        if self.strategy is Strategy.DIRECT:
            return c.direct_target

        anchor = self._anchor if self._anchor is not None else 0.6
        if t < c.iso_match_duration_s:
            return anchor
        if t < c.iso_match_duration_s + c.descent_duration_s:
            progress = (t - c.iso_match_duration_s) / c.descent_duration_s
            return anchor + (c.floor_arousal - anchor) * progress
        return c.floor_arousal


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
