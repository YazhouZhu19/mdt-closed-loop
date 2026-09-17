"""L3 控制器 + L3.5 音乐语法约束层。

控制器不含 D 项：生理信号噪声大，微分会放大抖动。
语法层是强制的——直接把控制器输出送进引擎会产生听感断裂。
"""

from __future__ import annotations

import math

from .config import ControlConfig
from .l35_guard import MusicGrammar  # noqa: F401 -- compatibility re-export
from .types import State


class PIController:
    def __init__(self, cfg: ControlConfig):
        self.cfg = cfg
        self._integral = 0.0
        self._last_output = 0.0
        self._last_scale = 0.0

    @property
    def last_scale(self) -> float:
        """Reliability multiplier applied to the latest control decision."""
        return self._last_scale

    def reliability(self, state: State) -> float:
        """Combine observation confidence and posterior state uncertainty."""
        if (
            not math.isfinite(state.confidence)
            or not 0.0 <= state.confidence <= 1.0
        ):
            raise ValueError("state.confidence must be finite and in [0, 1]")
        if not math.isfinite(state.uncertainty) or state.uncertainty < 0:
            raise ValueError("state.uncertainty must be finite and >= 0")
        soft = self.cfg.uncertainty_soft_limit
        hard = self.cfg.uncertainty_hard_limit
        if state.uncertainty <= soft:
            uncertainty_scale = 1.0
        elif state.uncertainty >= hard:
            uncertainty_scale = 0.0
        else:
            uncertainty_scale = (hard - state.uncertainty) / (hard - soft)
        return state.confidence * uncertainty_scale

    def step(self, target: float, state: State, dt: float) -> tuple[float, str]:
        if not math.isfinite(target) or not 0.0 <= target <= 1.0:
            raise ValueError("target must be finite and in [0, 1]")
        if not math.isfinite(state.arousal) or not 0.0 <= state.arousal <= 1.0:
            raise ValueError("state.arousal must be finite and in [0, 1]")
        if not math.isfinite(dt) or dt <= 0:
            raise ValueError("dt must be finite and > 0")
        scale = self.reliability(state)
        self._last_scale = scale
        if scale < 1e-6:
            self._integral *= self.cfg.deadband_integral_leak
            self._last_output = 0.0
            reason = (
                "open_loop_high_uncertainty"
                if state.uncertainty >= self.cfg.uncertainty_hard_limit
                else "open_loop_no_confidence"
            )
            return 0.0, reason

        error = target - state.arousal
        if abs(error) < self.cfg.deadband:
            self._integral *= self.cfg.deadband_integral_leak
            self._last_output = 0.0
            return 0.0, "deadband"

        # Accumulate only the fraction of error that can safely be acted upon;
        # this prevents hidden integral wind-up while control is derated.
        self._integral += error * dt * scale
        clamp = self.cfg.integral_clamp
        self._integral = max(-clamp, min(clamp, self._integral))

        output = scale * (self.cfg.kp * error + self.cfg.ki * self._integral)
        output = max(-self.cfg.output_clamp, min(self.cfg.output_clamp, output))
        self._last_output = output
        reason = "closed_loop" if scale >= 1.0 - 1e-9 else "closed_loop_derated"
        return output, reason

    def reset(self) -> None:
        self._integral = 0.0
        self._last_output = 0.0
        self._last_scale = 0.0

    def suspend(self) -> None:
        """Safely hold control while shedding integral memory."""
        self._integral *= self.cfg.deadband_integral_leak
        self._last_output = 0.0
        self._last_scale = 0.0
