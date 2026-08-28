"""L4 记录 / L5 疗程管理与安全 / L6 随机化。"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from .config import ProgramConfig
from .types import Arm, ControlRecord, Features, State, Strategy


class SessionRecorder:
    """三轨同一时钟落库，对齐到 1s 网格。这是全部研究价值的来源。"""

    def __init__(
        self, session_id: str, user_id: str, arm: Arm, out_dir: str = "./data"
    ):
        self.session_id = session_id
        self.user_id = user_id
        self.arm = arm
        self.physio: list[dict] = []
        self.music: list[dict] = []
        self.subjective: dict = {}
        self._dir = Path(out_dir)
        self._last_physio_t = -math.inf
        self._last_music_t = -math.inf

    def log_physio(self, feats: Features, state: State) -> None:
        if feats.t < self._last_physio_t:
            raise ValueError("physiology timestamps must be monotonic")
        self._last_physio_t = feats.t
        self.physio.append(
            {
                "t": round(feats.t),
                "quality": feats.quality.value,
                **feats.as_dict(),
                "arousal": state.arousal,
                "confidence": state.confidence,
                "z": state.z_scores,
            }
        )

    def log_music(self, rec: ControlRecord) -> None:
        if rec.t < self._last_music_t:
            raise ValueError("music timestamps must be monotonic")
        self._last_music_t = rec.t
        self.music.append(
            {
                "t": round(rec.t),
                "params": rec.params.as_dict(),
                "target": rec.target_arousal,
                "estimated": rec.estimated_arousal,
                "error": rec.error,
                "reason": rec.reason,
            }
        )

    def log_subjective(
        self,
        pre: dict | None = None,
        post: dict | None = None,
        instrument: dict | None = None,
    ) -> None:
        if pre:
            self.subjective["pre"] = pre
        if post:
            self.subjective["post"] = post
        if instrument:
            self.subjective.setdefault("instruments", []).append(instrument)

    def flush(self) -> Path:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{self.session_id}.json"
        temp_path = path.with_suffix(".json.tmp")
        temp_path.write_text(
            json.dumps(
                {
                    "session_id": self.session_id,
                    "user_id": self.user_id,
                    "arm": self.arm.value,
                    "physio": self.physio,
                    "music": self.music,
                    "subjective": self.subjective,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        temp_path.replace(path)
        return path


@dataclass
class ProgramState:
    """L5 疗程级状态。"""

    baseline_isi: float | None = None
    isi_history: list[float] = field(default_factory=list)
    completed_sessions: int = 0
    stopped_reason: str | None = None
    responder: bool | None = None

    def __post_init__(self) -> None:
        if self.baseline_isi is not None and (
            not math.isfinite(self.baseline_isi) or self.baseline_isi < 0
        ):
            raise ValueError("baseline ISI score must be finite and >= 0")
        if any(not math.isfinite(score) or score < 0 for score in self.isi_history):
            raise ValueError("ISI history must contain only finite non-negative scores")
        if (
            not isinstance(self.completed_sessions, int)
            or isinstance(self.completed_sessions, bool)
            or self.completed_sessions < 0
        ):
            raise ValueError("completed_sessions must be a non-negative integer")
        if self.responder is not None and not isinstance(self.responder, bool):
            raise ValueError("responder must be bool or None")

    def set_baseline_isi(self, score: float) -> None:
        if not math.isfinite(score) or score < 0:
            raise ValueError("ISI score must be finite and >= 0")
        self.baseline_isi = score

    def record_isi(self, score: float) -> None:
        if not math.isfinite(score) or score < 0:
            raise ValueError("ISI score must be finite and >= 0")
        self.isi_history.append(score)


class OutcomeEvaluator:
    def __init__(self, cfg: ProgramConfig):
        self.cfg = cfg

    def is_responder(self, ps: ProgramState) -> bool | None:
        if ps.baseline_isi is None or not ps.isi_history:
            return None
        latest = ps.isi_history[-1]
        drop = ps.baseline_isi - latest
        return (
            drop >= self.cfg.isi_response_drop or latest <= self.cfg.isi_remission_score
        )

    def check_futility(self, ps: ProgramState) -> str | None:
        """无效则停，写进标准的规则，不是可选项。"""
        if ps.completed_sessions < self.cfg.futility_session_count:
            return None
        if ps.baseline_isi is None or not ps.isi_history:
            return None
        if abs(ps.baseline_isi - ps.isi_history[-1]) < self.cfg.futility_min_change:
            return "futility_refer_to_clinician"
        return None


class SafetyMonitor:
    """纯 AI 系统必须有人工兜底。命中即进入人工复核队列。"""

    KEYWORDS = ("自杀", "自伤", "活不下去", "不想活", "结束生命", "伤害自己")

    def __init__(self, escalate_hook=None):
        self._hook = escalate_hook

    def scan(self, text: str, user_id: str) -> bool:
        if not text:
            return False
        hit = any(k in text for k in self.KEYWORDS)
        if hit and self._hook:
            self._hook(user_id, text)
        return hit


class ArmAssigner:
    """注册时分配，全程一致。用 user_id 哈希保证可复现且无需存表。"""

    def __init__(self, weights: dict[Arm, float] | None = None, salt: str = "mdt-v1"):
        self.weights = (
            dict(weights)
            if weights is not None
            else {
                Arm.FULL_LOOP: 0.4,
                Arm.SHAM: 0.2,
                Arm.DIRECT: 0.2,
                Arm.ISO: 0.2,
            }
        )
        if not salt:
            raise ValueError("randomization salt must not be empty")
        if set(self.weights) != set(Arm):
            raise ValueError("randomization weights must define every arm exactly once")
        if any(not math.isfinite(w) or w < 0 for w in self.weights.values()):
            raise ValueError("randomization weights must be finite and non-negative")
        if sum(self.weights.values()) <= 0:
            raise ValueError("at least one randomization weight must be positive")
        self.salt = salt

    def assign(self, user_id: str) -> Arm:
        if not user_id:
            raise ValueError("user_id must not be empty")
        digest = hashlib.sha256(f"{self.salt}:{user_id}".encode()).hexdigest()
        point = int(digest[:8], 16) / 0xFFFFFFFF
        total = sum(self.weights.values())
        acc = 0.0
        for arm, w in self.weights.items():
            acc += w / total
            if point <= acc:
                return arm
        return Arm.FULL_LOOP

    @staticmethod
    def strategy_for(arm: Arm) -> Strategy:
        return Strategy.DIRECT if arm is Arm.DIRECT else Strategy.ISO

    @staticmethod
    def is_sham(arm: Arm) -> bool:
        return arm is Arm.SHAM

    @staticmethod
    def is_closed_loop(arm: Arm) -> bool:
        return arm in (Arm.FULL_LOOP, Arm.DIRECT)

    @staticmethod
    def is_open_loop_iso(arm: Arm) -> bool:
        return arm is Arm.ISO
