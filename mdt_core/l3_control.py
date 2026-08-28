"""L3 控制器 + L3.5 音乐语法约束层。

控制器不含 D 项：生理信号噪声大，微分会放大抖动。
语法层是强制的——直接把控制器输出送进引擎会产生听感断裂。
"""

from __future__ import annotations

import math

from .config import ControlConfig, GrammarConfig
from .types import MusicParams, State


class PIController:
    def __init__(self, cfg: ControlConfig):
        self.cfg = cfg
        self._integral = 0.0
        self._last_output = 0.0

    def step(self, target: float, state: State, dt: float) -> tuple[float, str]:
        if not math.isfinite(target) or not 0.0 <= target <= 1.0:
            raise ValueError("target must be finite and in [0, 1]")
        if not math.isfinite(state.arousal) or not 0.0 <= state.arousal <= 1.0:
            raise ValueError("state.arousal must be finite and in [0, 1]")
        if not math.isfinite(dt) or dt <= 0:
            raise ValueError("dt must be finite and > 0")
        if state.confidence < 1e-6:
            self._last_output = 0.0
            return 0.0, "open_loop_no_confidence"

        error = target - state.arousal
        if abs(error) < self.cfg.deadband:
            self._integral *= self.cfg.deadband_integral_leak
            self._last_output = 0.0
            return 0.0, "deadband"

        self._integral += error * dt
        clamp = self.cfg.integral_clamp
        self._integral = max(-clamp, min(clamp, self._integral))

        output = self.cfg.kp * error + self.cfg.ki * self._integral
        output = max(-self.cfg.output_clamp, min(self.cfg.output_clamp, output))
        self._last_output = output
        return output, "closed_loop"

    def reset(self) -> None:
        self._integral = 0.0
        self._last_output = 0.0


class MusicGrammar:
    """把控制量翻译成参数向量，并强制音乐上的合法性。

    - 连续参数变更排队到下一小节线
    - 结构性参数 (层级) 排队到下一乐句边界
    - 每个参数独立限幅与限速
    """

    LAYER_LEVELS = (0b0001, 0b0011, 0b0111, 0b1111)

    def __init__(self, cfg: GrammarConfig, initial: MusicParams | None = None):
        self.cfg = cfg
        self.current = initial.copy() if initial else MusicParams()
        self._pending_tempo: float | None = None
        self._pending_layers: int | None = None
        self._last_tempo_change_t: float | None = None

    def _layer_level(self) -> int:
        if self.current.layer_mask in self.LAYER_LEVELS:
            return self.LAYER_LEVELS.index(self.current.layer_mask)
        count = self.current.layer_mask.bit_count()
        return max(0, min(len(self.LAYER_LEVELS) - 1, count - 1))

    def request(self, control: float, t: float) -> None:
        """control > 0 表示需要提升唤醒度，< 0 表示需要下压。"""
        if not math.isfinite(control) or not math.isfinite(t) or t < 0:
            raise ValueError("control and time must be finite; time must be >= 0")
        c = self.cfg
        lo, hi = c.tempo_range
        if abs(control) < 1e-12:
            self._pending_tempo = None
            self._pending_layers = None
            return
        self._pending_tempo = max(lo, min(hi, self.current.tempo + control * 12.0))

        level = self._layer_level()
        delta = c.max_layer_delta_per_phrase
        if control < -0.15:
            level = max(0, level - delta)
            self._pending_layers = self.LAYER_LEVELS[level]
        elif control > 0.15:
            level = min(len(self.LAYER_LEVELS) - 1, level + delta)
            self._pending_layers = self.LAYER_LEVELS[level]
        else:
            self._pending_layers = None

    def cancel_pending(self) -> None:
        """Cancel commands that were computed from stale or unsafe input."""
        self._pending_tempo = None
        self._pending_layers = None

    def commit(
        self,
        t: float,
        *,
        bar_boundary: bool = False,
        phrase_boundary: bool = False,
    ) -> tuple[MusicParams, bool]:
        """Commit pending changes on explicit events from the audio clock."""
        if not math.isfinite(t) or t < 0:
            raise ValueError("music clock time must be finite and >= 0")
        changed = False
        c = self.cfg

        if self._pending_tempo is not None and (bar_boundary or phrase_boundary):
            elapsed = (
                30.0
                if self._last_tempo_change_t is None
                else max(t - self._last_tempo_change_t, 0.0)
            )
            budget = c.max_tempo_delta_per_30s * max(elapsed, 0.0) / 30.0
            delta = self._pending_tempo - self.current.tempo
            step = max(-budget, min(budget, delta))
            if abs(step) > 1e-3:
                self.current.tempo += step
                self._last_tempo_change_t = t
                changed = True
            if abs(self._pending_tempo - self.current.tempo) < 1e-3:
                self._pending_tempo = None

        if self._pending_layers is not None and phrase_boundary:
            self.current.layer_mask = self._pending_layers
            self._pending_layers = None
            changed = True

        # 派生参数跟随速度，保持整体听感一致
        lo, hi = c.tempo_range
        norm = (self.current.tempo - lo) / (hi - lo)
        self.current.dynamics = 0.25 + 0.5 * norm
        self.current.harmonic_brightness = 0.2 + 0.5 * norm
        self.current.rhythmic_accent = 0.1 + 0.4 * norm
        self.current.reverb_depth = 0.6 - 0.3 * norm

        return self.current.copy(), changed
