"""Frozen L3.5 projection and explicit audio-clock scheduling.

Learning may propose any parameter vector. Only this module decides which
vector can be applied. ``project`` is pure, and is idempotent for fixed limits.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from numbers import Real
from typing import Any

from .config import DEFAULT, GrammarConfig
from .l35_mapping import LAYER_LEVELS, layer_level, map_control
from .types import MusicParams

_CONTINUOUS = (
    "register", "dynamics", "harmonic_brightness", "rhythmic_accent", "reverb_depth"
)


@dataclass(frozen=True, init=False)
class GuardLimits:
    """Immutable snapshot of the previous output and the current clock event.

    ``elapsed_tempo_s`` is time since the last applied tempo change. Use 30 s
    before the first change, matching the original grammar's initial budget.
    The ``current`` property returns a copy so callers cannot mutate limits.
    """

    _current: tuple[float, int, float, float, float, float, float]
    elapsed_tempo_s: float
    config: GrammarConfig
    bar_boundary: bool
    phrase_boundary: bool

    def __init__(
        self,
        current: MusicParams,
        elapsed_tempo_s: float = 30.0,
        config: GrammarConfig = DEFAULT.grammar,
        bar_boundary: bool = False,
        phrase_boundary: bool = False,
    ) -> None:
        if (
            not isinstance(elapsed_tempo_s, Real)
            or isinstance(elapsed_tempo_s, bool)
            or not math.isfinite(elapsed_tempo_s)
            or elapsed_tempo_s < 0
        ):
            raise ValueError("elapsed_tempo_s must be finite and >= 0")
        if not isinstance(config, GrammarConfig):
            raise TypeError("config must be GrammarConfig")
        if not isinstance(bar_boundary, bool) or not isinstance(phrase_boundary, bool):
            raise TypeError("boundary flags must be bool")
        snapshot = current.copy()
        object.__setattr__(self, "_current", (
            snapshot.tempo, snapshot.layer_mask, snapshot.register,
            snapshot.dynamics, snapshot.harmonic_brightness,
            snapshot.rhythmic_accent, snapshot.reverb_depth,
        ))
        object.__setattr__(self, "elapsed_tempo_s", float(elapsed_tempo_s))
        object.__setattr__(self, "config", config)
        object.__setattr__(self, "bar_boundary", bar_boundary)
        object.__setattr__(self, "phrase_boundary", phrase_boundary)

    @property
    def current(self) -> MusicParams:
        return MusicParams(*self._current)


def _finite(value: Any, fallback: float) -> float:
    if not isinstance(value, Real) or isinstance(value, bool):
        return fallback
    try:
        converted = float(value)
    except (OverflowError, ValueError):
        return fallback
    return converted if math.isfinite(converted) else fallback


def _normalise(
    params: MusicParams | Mapping[str, Any],
    previous: MusicParams,
    cfg: GrammarConfig,
) -> MusicParams:
    raw: Mapping[str, Any]
    if isinstance(params, MusicParams):
        raw = params.as_dict()
    elif isinstance(params, Mapping):
        raw = params
    else:
        raise TypeError("params must be MusicParams or a mapping")
    lo, hi = cfg.tempo_range
    values = {"tempo": max(lo, min(hi, _finite(raw.get("tempo"), previous.tempo)))}
    value = raw.get("layer_mask", previous.layer_mask)
    if isinstance(value, int) and not isinstance(value, bool):
        mask = max(1, min(15, value))
    else:
        mask = previous.layer_mask
    values["layer_mask"] = LAYER_LEVELS[layer_level(mask)]
    for name in _CONTINUOUS:
        values[name] = max(0.0, min(1.0, _finite(raw.get(name), getattr(previous, name))))
    return MusicParams(**values)


def project(
    params: MusicParams | Mapping[str, Any], limits: GuardLimits
) -> MusicParams:
    """Project untrusted candidates onto bounds, rates and music boundaries.

    Nonfinite or nonnumeric entries hold their previous values; finite values
    are clipped. Layers use canonical cumulative masks. Calling this repeatedly
    with the *same* limits never advances its rate budget or consumes an event.
    """
    previous = _normalise(limits.current, limits.current, limits.config)
    desired = _normalise(params, previous, limits.config)
    result = previous.copy()
    if limits.bar_boundary or limits.phrase_boundary:
        lo, hi = limits.config.tempo_range
        # Cap elapsed to the time needed to traverse the range, avoiding float
        # overflow even for otherwise finite adversarial limits.
        full_range_s = (hi - lo) / limits.config.max_tempo_delta_per_30s * 30.0
        elapsed = min(limits.elapsed_tempo_s, full_range_s)
        budget = limits.config.max_tempo_delta_per_30s * (elapsed / 30.0)
        result.tempo = max(
            max(lo, previous.tempo - budget),
            min(min(hi, previous.tempo + budget), desired.tempo),
        )
        for name in _CONTINUOUS:
            setattr(result, name, getattr(desired, name))
    if limits.phrase_boundary:
        level = layer_level(previous.layer_mask)
        target = layer_level(desired.layer_mask)
        delta = limits.config.max_layer_delta_per_phrase
        target = max(level - delta, min(level + delta, target))
        result.layer_mask = LAYER_LEVELS[target]
    return result
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
        self._pending_params: MusicParams | None = None
        self._learned_active = False
        self._restore_default = False
        self._guarded_events = False
        self._last_learned_bar_t: float | None = None
        self._last_learned_phrase_t: float | None = None

    def _layer_level(self) -> int:
        if self.current.layer_mask in self.LAYER_LEVELS:
            return self.LAYER_LEVELS.index(self.current.layer_mask)
        count = self.current.layer_mask.bit_count()
        return max(0, min(len(self.LAYER_LEVELS) - 1, count - 1))

    def request(self, control: float, t: float) -> None:
        """control > 0 表示需要提升唤醒度，< 0 表示需要下压。"""
        if not math.isfinite(control) or not math.isfinite(t) or t < 0:
            raise ValueError("control and time must be finite; time must be >= 0")
        self._restore_default = self._restore_default or self._learned_active
        self._learned_active = False
        self._pending_params = None
        c = self.cfg
        if abs(control) < 1e-12:
            self._pending_tempo = None
            self._pending_layers = None
            return
        candidate = map_control(control, self.current, self.cfg)
        self._pending_tempo = candidate.tempo

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

    def request_params(self, params: MusicParams | Mapping[str, Any], t: float) -> None:
        """Queue a learned candidate; the frozen guard runs at commit time.

        Copy and normalize now, but retain the desired tempo/layer target until
        later boundaries make the permitted progression possible.
        """
        if not math.isfinite(t) or t < 0:
            raise ValueError("music clock time must be finite and >= 0")
        candidate = _normalise(params, self.current, self.cfg)
        self._pending_tempo = None
        self._pending_layers = None
        self._pending_params = candidate
        self._learned_active = True
        self._restore_default = False
        self._guarded_events = True

    def cancel_pending(self) -> None:
        """Cancel commands that were computed from stale or unsafe input."""
        self._pending_params = None
        if self._restore_default:
            self._learned_active = True
            self._restore_default = False
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
        if self._guarded_events:
            # Switching back to the PI mapping cannot spend the same event's
            # layer budget again. Preserve legacy scheduling until learning is
            # first used, then share event accounting across both paths.
            bar_boundary = (bar_boundary or phrase_boundary) and (
                self._last_learned_bar_t is None or t > self._last_learned_bar_t
            )
            phrase_boundary = phrase_boundary and (
                self._last_learned_phrase_t is None or t > self._last_learned_phrase_t
            )
        if self._learned_active:
            return self._commit_learned(t, bar_boundary, phrase_boundary)
        # A fallback following learned timbres must restore derived parameters
        # at a real boundary. Untouched default runs retain legacy semantics.
        if self._restore_default and not (bar_boundary or phrase_boundary):
            return self.current.copy(), False
        before_restore = self.current.copy() if self._restore_default else None
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

        if before_restore is not None:
            changed = changed or before_restore != self.current
            self._restore_default = False

        if self._guarded_events:
            if bar_boundary or phrase_boundary:
                self._last_learned_bar_t = t
            if phrase_boundary:
                self._last_learned_phrase_t = t

        return self.current.copy(), changed

    def _commit_learned(
        self, t: float, bar_boundary: bool, phrase_boundary: bool
    ) -> tuple[MusicParams, bool]:
        if self._pending_params is None:
            return self.current.copy(), False
        continuous_event = (bar_boundary or phrase_boundary) and (
            self._last_learned_bar_t is None or t > self._last_learned_bar_t
        )
        phrase_event = phrase_boundary and (
            self._last_learned_phrase_t is None or t > self._last_learned_phrase_t
        )
        elapsed = (
            30.0 if self._last_tempo_change_t is None
            else max(t - self._last_tempo_change_t, 0.0)
        )
        limits = GuardLimits(
            self.current, elapsed, self.cfg,
            bar_boundary=continuous_event, phrase_boundary=phrase_event,
        )
        candidate = project(self._pending_params, limits)
        changed = candidate != self.current
        if candidate.tempo != self.current.tempo:
            self._last_tempo_change_t = t
        if continuous_event:
            self._last_learned_bar_t = t
        if phrase_event:
            self._last_learned_phrase_t = t
        self.current = candidate
        if self.current == self._pending_params:
            self._pending_params = None
        return self.current.copy(), changed
