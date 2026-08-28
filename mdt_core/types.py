"""贯穿 L0-L6 的数据类型定义。所有层之间只通过这些类型通信。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum


class SignalQuality(str, Enum):
    OK = "ok"
    NOISY = "noisy"
    LOST = "lost"


class Strategy(str, Enum):
    ISO = "iso"
    DIRECT = "direct"


class Arm(str, Enum):
    FULL_LOOP = "A_full_loop"
    SHAM = "B_sham"
    DIRECT = "C_direct"
    ISO = "D_iso"


class SessionStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    FINISHED = "finished"
    ABORTED = "aborted"


@dataclass
class RawWindow:
    """一个原始信号窗口。"""

    t: float
    eda: list[float]
    eda_fs: float
    rr_intervals: list[float]
    contact_impedance: float | None = None
    accel_rms: float | None = None


@dataclass
class Features:
    """L0 输出。缺失值为 None，由下游门控处理。"""

    t: float
    scl_slope: float | None = None
    scr_rate: float | None = None
    rmssd: float | None = None
    hf_power: float | None = None
    sd1: float | None = None
    quality: SignalQuality = SignalQuality.OK

    def as_dict(self) -> dict:
        return {
            "scl_slope": self.scl_slope,
            "scr_rate": self.scr_rate,
            "rmssd": self.rmssd,
            "hf_power": self.hf_power,
            "sd1": self.sd1,
        }


@dataclass
class State:
    """L1 输出：唤醒度估计与置信度。不含效价，效价只从自评获取。"""

    t: float
    arousal: float
    confidence: float
    z_scores: dict[str, float] = field(default_factory=dict)


@dataclass
class MusicParams:
    """L3.5 输出：交给音乐引擎的完整参数向量。"""

    tempo: float = 72.0
    layer_mask: int = 0b0111
    register: float = 0.5
    dynamics: float = 0.5
    harmonic_brightness: float = 0.5
    rhythmic_accent: float = 0.3
    reverb_depth: float = 0.4

    def __post_init__(self) -> None:
        if not math.isfinite(self.tempo) or self.tempo <= 0:
            raise ValueError("tempo must be finite and > 0")
        if (
            not isinstance(self.layer_mask, int)
            or isinstance(self.layer_mask, bool)
            or not 1 <= self.layer_mask <= 0b1111
        ):
            raise ValueError("layer_mask must be an integer in [1, 15]")
        for name in (
            "register",
            "dynamics",
            "harmonic_brightness",
            "rhythmic_accent",
            "reverb_depth",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be finite and in [0, 1]")

    def copy(self) -> MusicParams:
        return MusicParams(**self.__dict__)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class ControlRecord:
    """L4 音乐轨的一条记录，附带触发原因以便事后归因。"""

    t: float
    params: MusicParams
    target_arousal: float
    estimated_arousal: float
    error: float
    reason: str
