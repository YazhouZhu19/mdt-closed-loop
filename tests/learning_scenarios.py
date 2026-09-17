"""Deterministic baseline replay shared by golden-regression tests."""

from __future__ import annotations

import tempfile
from dataclasses import asdict

from mdt_core.config import DEFAULT
from mdt_core.engine import NullEngine
from mdt_core.l4_l6 import ProgramState
from mdt_core.session import Session
from mdt_core.types import Arm, MusicParams
from tests.synthetic import ready_baseline, synthetic_window

LEGACY_MUSIC_KEYS = (
    "t",
    "params",
    "target",
    "estimated",
    "error",
    "reason",
    "state_uncertainty",
    "control_output",
    "control_scale",
    "trajectory_phase",
    "trajectory_speed",
)


def replay(arm: Arm, *, cfg=DEFAULT, policies=None) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        engine = NullEngine()
        kwargs = {} if policies is None else {"policies": policies}
        session = Session(
            "golden-synthetic",
            ready_baseline(),
            ProgramState(18),
            engine=engine,
            arm=arm,
            cfg=cfg,
            out_dir=directory,
            sham_trajectory=[MusicParams(tempo=70), MusicParams(tempo=68)],
            **kwargs,
        )
        states = []
        for t, arousal in (
            (0, 0.8),
            (15, 0.85),
            (30, 0.7),
            (300, 0.8),
            (360, 0.7),
            (600, 0.9),
        ):
            window = synthetic_window(t, arousal=arousal)
            if t == 30:
                window.contact_impedance = 9e9
                window.rr_intervals = []
            state, _ = session.slow_tick(window)
            states.append(asdict(state))
            session.music_boundary(t + 1, phrase_boundary=True)
        session.finish(isi_score=12)
        return {
            "states": states,
            "physio": session.recorder.physio,
            "music": [
                {key: row[key] for key in LEGACY_MUSIC_KEYS}
                for row in session.recorder.music
            ],
            "engine": [point.as_dict() for point in engine.history],
        }
