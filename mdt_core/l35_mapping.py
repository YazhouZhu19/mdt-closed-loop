"""Pure default L3.5 mapping; learned mappings share the frozen guard."""

from __future__ import annotations

import math

from .config import DEFAULT, GrammarConfig
from .types import MusicParams

LAYER_LEVELS = (0b0001, 0b0011, 0b0111, 0b1111)


def layer_level(mask: int) -> int:
    """Use the original grammar's active-layer count for noncanonical masks."""
    if mask in LAYER_LEVELS:
        return LAYER_LEVELS.index(mask)
    return max(0, min(len(LAYER_LEVELS) - 1, mask.bit_count() - 1))


def derive_from_tempo(params: MusicParams, cfg: GrammarConfig) -> MusicParams:
    """Return the original rule mapping's tempo-linked timbral parameters."""
    result = params.copy()
    lo, hi = cfg.tempo_range
    norm = (result.tempo - lo) / (hi - lo)
    result.dynamics = 0.25 + 0.5 * norm
    result.harmonic_brightness = 0.2 + 0.5 * norm
    result.rhythmic_accent = 0.1 + 0.4 * norm
    result.reverb_depth = 0.6 - 0.3 * norm
    return result


def map_control(
    control: float,
    current: MusicParams,
    cfg: GrammarConfig = DEFAULT.grammar,
) -> MusicParams:
    """Translate control into a desired vector, without clocks or side effects.

    This is a *candidate*, so the audio-clock guard still needs to apply tempo
    rate limits and bar/phrase scheduling. A zero command is a hold request.
    """
    if not math.isfinite(control):
        raise ValueError("control must be finite")
    result = current.copy()
    if abs(control) < 1e-12:
        return result
    lo, hi = cfg.tempo_range
    result.tempo = max(lo, min(hi, current.tempo + control * 12.0))
    level = layer_level(current.layer_mask)
    delta = cfg.max_layer_delta_per_phrase
    if control < -0.15:
        result.layer_mask = LAYER_LEVELS[max(0, level - delta)]
    elif control > 0.15:
        result.layer_mask = LAYER_LEVELS[min(len(LAYER_LEVELS) - 1, level + delta)]
    return derive_from_tempo(result, cfg)
