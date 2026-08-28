"""隔离的合成信号仿真：跑通 3 次基线会话 + 1 次闭环会话。

本文件只生成虚构 EDA/RR 数据，不读取或写入任何真实用户数据。

python demo.py
"""

import tempfile

import numpy as np

from mdt_core import (
    Arm,
    ArmAssigner,
    IndividualBaseline,
    ProgramState,
    RawWindow,
    Session,
)
from mdt_core.engine import NullEngine

RNG = np.random.default_rng(7)


def synth_window(t: float, arousal: float) -> RawWindow:
    """按给定唤醒度生成一段 60s 的 EDA 与 RR。"""
    fs = 32.0
    n = int(60 * fs)
    drift = np.linspace(0, arousal * 0.6, n)
    eda = 5.0 + drift + RNG.normal(0, 0.01, n)
    for _ in range(int(arousal * 8)):
        onset = RNG.integers(0, n - int(3 * fs))
        bump = np.exp(-np.linspace(0, 4, int(3 * fs))) * (0.05 + arousal * 0.1)
        eda[onset : onset + bump.size] += bump

    mean_rr = 1000 - arousal * 220
    sd = 45 * (1.15 - arousal)
    rr = RNG.normal(mean_rr, max(sd, 6), 62)
    return RawWindow(
        t=t,
        eda=eda.tolist(),
        eda_fs=fs,
        rr_intervals=rr.tolist(),
        contact_impedance=1.2e6,
        accel_rms=0.01,
    )


def run_session(
    user_id, baseline, program, label, arousal_profile, out_dir, *, calibration=False
):
    s = Session(
        user_id,
        baseline,
        program,
        engine=NullEngine(),
        arm=Arm.FULL_LOOP,
        out_dir=out_dir,
        is_calibration=calibration,
    )
    print(
        f"\n[{label}]  arm={s.arm.value}  strategy={ArmAssigner.strategy_for(s.arm).value}"
    )
    for i, a in enumerate(arousal_profile):
        t = i * 60.0
        state, params = s.tick(synth_window(t, a), dt=60.0)
        if i % 5 == 0:
            print(
                f"  t={int(t):>5}s  真实={a:.2f}  估计={state.arousal:.2f}  "
                f"conf={state.confidence:.2f}  bpm={params.tempo:.1f}  "
                f"层={bin(params.layer_mask)[2:]:>4}"
            )
    isi = None if calibration else 18.0 - (program.completed_sessions + 1) * 0.7
    path = s.finish(post_survey={"calm": 4}, isi_score=isi)
    print(f"  -> {path}   剂量区间={s.dose.band}")
    return s


def main():
    user = "u_demo_001"
    baseline = IndividualBaseline()
    program = ProgramState(baseline_isi=18.0)

    with tempfile.TemporaryDirectory(prefix="mdt-synthetic-demo-") as out_dir:
        print(f"[隔离说明] 输入均为合成信号；会话JSON仅写入临时目录 {out_dir}")

        # 前 3 次会话建立个体常模，此阶段控制器不闭环
        for k in range(3):
            prof = np.clip(RNG.normal(0.5, 0.12, 20), 0.05, 0.95)
            run_session(
                user,
                baseline,
                program,
                f"基线会话 {k + 1}/3",
                prof,
                out_dir,
                calibration=True,
            )

        print(f"\n基线就绪: {baseline.is_ready}  已采集特征: {sorted(baseline.mu)}")

        # 闭环会话：用户初始唤醒度偏高，随会话推进自然下降
        prof = np.clip(
            np.linspace(0.75, 0.30, 40) + RNG.normal(0, 0.05, 40), 0.05, 0.95
        )
        run_session(user, baseline, program, "闭环会话", prof, out_dir)

    print("[隔离说明] 临时合成输出已自动删除。")


if __name__ == "__main__":
    main()
