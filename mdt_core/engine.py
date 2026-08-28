"""音乐引擎抽象。

供应商可替换：Mubert (stem 级 + WebRTC 亚秒延迟)、Loudly、
或自有 stem + FMOD。业务代码只依赖 MusicEngine 接口。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .types import MusicParams

LAYER_NAMES = ("pad", "bass", "texture", "percussion")


class MusicEngine(ABC):
    @abstractmethod
    def start(self, session_id: str, params: MusicParams) -> None: ...

    @abstractmethod
    def apply(self, params: MusicParams) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...


class NullEngine(MusicEngine):
    """离线仿真与单元测试用。"""

    def __init__(self):
        self.history: list[MusicParams] = []
        self.started = False
        self.stopped = False

    def start(self, session_id: str, params: MusicParams) -> None:
        if self.started and not self.stopped:
            raise RuntimeError("engine is already running")
        self.started = True
        self.stopped = False
        self.history.append(params.copy())

    def apply(self, params: MusicParams) -> None:
        if not self.started or self.stopped:
            raise RuntimeError("cannot apply parameters to a stopped engine")
        self.history.append(params.copy())

    def stop(self) -> None:
        if self.started:
            self.stopped = True


class MubertEngine(MusicEngine):
    """骨架实现。实际接入需替换为官方 SDK 调用。"""

    def __init__(self, api_key: str, base_url: str = "https://api.mubert.com"):
        self.api_key = api_key
        self.base_url = base_url
        self._session_id: str | None = None

    @staticmethod
    def _to_payload(p: MusicParams) -> dict:
        active = [n for i, n in enumerate(LAYER_NAMES) if p.layer_mask & (1 << i)]
        return {
            "bpm": round(p.tempo, 1),
            "stems": active,
            "intensity": round(p.dynamics, 3),
            "brightness": round(p.harmonic_brightness, 3),
            "reverb": round(p.reverb_depth, 3),
        }

    def start(self, session_id: str, params: MusicParams) -> None:
        self._session_id = session_id
        # POST {base_url}/v3/stream  with self._to_payload(params)
        raise NotImplementedError("接入 Mubert SDK 后实现")

    def apply(self, params: MusicParams) -> None:
        # PATCH {base_url}/v3/stream/{self._session_id}
        raise NotImplementedError("接入 Mubert SDK 后实现")

    def stop(self) -> None:
        raise NotImplementedError("接入 Mubert SDK 后实现")


class ShamEngine(MusicEngine):
    """B 臂：界面与音乐完全相同，但参数来自预录轨迹而非用户生理信号。

    这是 L6 假反馈对照的实现，也是路径 C 临床评价的核心材料。
    """

    def __init__(self, inner: MusicEngine, recorded_trajectory: list[MusicParams]):
        if not recorded_trajectory:
            raise ValueError("SHAM requires a non-empty pre-registered trajectory")
        self.inner = inner
        self.trajectory = tuple(point.copy() for point in recorded_trajectory)
        self._idx = 0
        self.current: MusicParams | None = None

    def start(self, session_id: str, params: MusicParams) -> None:
        self._idx = 0
        self.current = self._next()
        self.inner.start(session_id, self.current)

    def _next(self) -> MusicParams:
        if not self.trajectory:
            return MusicParams()
        p = self.trajectory[min(self._idx, len(self.trajectory) - 1)]
        self._idx += 1
        return p.copy()

    def apply(self, params: MusicParams) -> None:
        self.current = self._next()
        self.inner.apply(self.current)

    def stop(self) -> None:
        self.inner.stop()
