"""Deterministic synthetic fixtures used only by the isolated test suite.

These signals are mathematical test inputs. They contain no captured, imported,
or inferred patient data. Tests write session artifacts only to
``tempfile.TemporaryDirectory``.
"""

from __future__ import annotations

import numpy as np

from mdt_core.l1_state import IndividualBaseline
from mdt_core.types import RawWindow


def synthetic_window(
    t: float,
    arousal: float = 0.5,
    *,
    impedance: float = 1.0e6,
    accel: float = 0.01,
    nan_count: int = 0,
    duration_s: float = 60.0,
) -> RawWindow:
    fs = 32.0
    count = int(duration_s * fs)
    seed = 1000 + int(t * 10) + int(arousal * 100)
    rng = np.random.default_rng(seed)
    eda = 5.0 + np.linspace(0, arousal * 0.3, count) + rng.normal(0, 0.005, count)
    if nan_count:
        eda[:nan_count] = np.nan
    rr = rng.normal(1000 - arousal * 180, 30, 70)
    return RawWindow(
        t=t,
        eda=eda.tolist(),
        eda_fs=fs,
        rr_intervals=rr.tolist(),
        contact_impedance=impedance,
        accel_rms=accel,
    )


def ready_baseline() -> IndividualBaseline:
    return IndividualBaseline(
        mu={
            "scl_slope": 0.002,
            "scr_rate": 0.0,
            "rmssd": 40.0,
            "hf_power": 1000.0,
            "sd1": 28.0,
        },
        sigma={
            "scl_slope": 0.001,
            "scr_rate": 1.0,
            "rmssd": 10.0,
            "hf_power": 300.0,
            "sd1": 7.0,
        },
        sessions_collected=3,
    )
