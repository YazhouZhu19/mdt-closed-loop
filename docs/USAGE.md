# Usage and Integration Guide

This guide covers local installation, the synthetic demonstration, session integration, research arms, output records, and music-engine adapters.

## 1. Environment

- Python 3.10+
- NumPy 1.26+
- SciPy 1.11+

Create an isolated environment from the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

On Windows PowerShell, activate with:

```powershell
.venv\Scripts\Activate.ps1
```

## 2. Run the isolated demonstration

```bash
python demo.py
```

The demonstration performs three calibration sessions followed by one closed-loop session. All EDA and RR values are generated locally with a fixed seed. Records are written to a temporary directory and deleted when the program exits.

## 3. Core objects

```python
from mdt_core import Arm, IndividualBaseline, ProgramState, RawWindow, Session
from mdt_core.engine import NullEngine

baseline = IndividualBaseline()
program = ProgramState(baseline_isi=18.0)
engine = NullEngine()

session = Session(
    user_id="pseudonymous-participant-id",
    baseline=baseline,
    program=program,
    engine=engine,
    arm=Arm.FULL_LOOP,
    out_dir="./data",
    is_calibration=True,
)
```

Use a pseudonymous participant identifier. The default recorder writes that identifier into JSON and is not suitable for identifiable health data without additional controls.

## 4. Input windows

`RawWindow.t` is elapsed session time in seconds, not wall-clock time. Events must be processed monotonically.

```python
window = RawWindow(
    t=0.0,
    eda=[...],                 # microsiemens, sampled uniformly
    eda_fs=32.0,               # Hz
    rr_intervals=[...],        # milliseconds
    contact_impedance=1.2e6,   # optional device-specific quality signal
    accel_rms=0.01,            # optional motion-quality signal
)
```

The prototype expects overlapping windows supplied by the integration layer:

| Path | Expected content | Minimum update interval | Purpose |
|---|---|---:|---|
| `fast_tick` | approximately 10 s of EDA | 2 s | responsive EDA-only state update |
| `slow_tick` | approximately 60 s of EDA and RR | 15 s | complete EDA/HRV state update |
| `music_boundary` | audio-clock timestamp | engine event | commit queued musical changes |

Window construction and buffering are intentionally outside this repository because they depend on the sensor SDK.

## 5. Calibration

Calibration sessions must be explicitly marked. Only valid features from the first 120 seconds are accumulated, and calibration never counts as treatment dose or actuates music.

```python
calibration = Session(
    user_id="participant-001",
    baseline=baseline,
    program=program,
    engine=NullEngine(),
    arm=Arm.FULL_LOOP,
    out_dir="./data",
    is_calibration=True,
)

for window in calibration_windows:
    calibration.slow_tick(window)

calibration.finish()
```

By default, three calibration sessions are required. `baseline.is_ready` becomes true only when all required EDA and HRV statistics have valid means and positive standard deviations.

## 6. Treatment session

```python
treatment = Session(
    user_id="participant-001",
    baseline=baseline,
    program=program,
    engine=NullEngine(),
    arm=Arm.FULL_LOOP,
    out_dir="./data",
)

# Sensor scheduler callbacks:
state, params = treatment.fast_tick(eda_window)
state, params = treatment.slow_tick(eda_hrv_window)

# Audio-engine callback at a true bar or phrase boundary:
params = treatment.music_boundary(audio_time, phrase_boundary=False)

record_path = treatment.finish(
    post_survey={"calm": 4},
    isi_score=17.0,
)
```

Do not infer bar or phrase boundaries with floating-point modulo. Call `music_boundary` from the audio engine's own transport callback.

`tick` remains a compatibility alias for `slow_tick`; new integrations should prefer the explicit multi-rate methods.

## 7. Research arms

| Arm | Target trajectory | Live physiology controls music? | Notes |
|---|---|---|---|
| `FULL_LOOP` | response-adaptive ISO | yes | complete loop; freezes on unreliable state |
| `DIRECT` | fixed direct target | yes | closed-loop direct guidance |
| `ISO` | ISO | no continuous feedback | open-loop planned trajectory |
| `SHAM` | pre-registered | no | playback advances only on audio boundaries |

For a SHAM session, provide an immutable study trajectory at construction:

```python
from mdt_core.types import MusicParams

trajectory = [
    MusicParams(tempo=68.0),
    MusicParams(tempo=66.0),
    MusicParams(tempo=64.0),
]

sham = Session(
    user_id="participant-002",
    baseline=baseline,
    program=program,
    engine=NullEngine(),
    arm=Arm.SHAM,
    sham_trajectory=trajectory,
    out_dir="./data",
)
```

The list is copied when `ShamEngine` is created. Actual SHAM parameters, rather than controller proposals, are written to the music record.

## 8. Signal-quality behavior

- Invalid timestamps or sampling rates raise an exception.
- Small EDA gaps are interpolated and marked `NOISY`.
- Excessive EDA loss removes EDA features; usable HRV may still be retained.
- Implausible or ectopic RR intervals are rejected.
- Bad contact impedance marks the signal `LOST`.
- Motion, partial coverage, or filtered samples lower confidence.
- Kalman posterior variance is exposed separately and grows across measurement gaps.
- Moderate uncertainty continuously derates PI output and integral accumulation.
- `LOST`, low confidence, or posterior uncertainty above the hard limit disables live feedback and cancels queued changes.

Integrations should catch validation errors at the device boundary, record the device event separately, and avoid retrying with fabricated values.

## 9. Safety and abnormal exit

Connect the escalation hook before any supervised study use:

```python
from mdt_core.l4_l6 import SafetyMonitor

def enqueue_human_review(user_id: str, text: str) -> None:
    # Replace with an authenticated, audited, staffed workflow.
    ...

monitor = SafetyMonitor(enqueue_human_review)
```

Submit subjective content as soon as it is received:

```python
hit = treatment.submit_subjective(instrument={"free_text": response})
```

A keyword hit stops actuation, marks the program for safety escalation, persists available data, and does not count the session as completed treatment. For other abnormal exits:

```python
treatment.abort("device_disconnected")
```

## 10. Session output

`SessionRecorder` writes one JSON object containing:

- `session_id`, `user_id`, and research `arm`;
- `physio`: timestamped features, quality, state, confidence, posterior uncertainty, and Z scores;
- `music`: actual parameters, target, estimate, error, control output/scale, trajectory phase/speed, and reason;
- `subjective`: pre/post surveys and instrument events.

Files are written to a sibling `.json.tmp` and atomically replaced at completion. Atomic replacement prevents partial JSON, but it does not provide encryption, authentication, retention policy, or audit logging.

## 11. Implement a real music engine

Implement the three-method interface in `mdt_core.engine.MusicEngine`:

```python
class VendorEngine(MusicEngine):
    def start(self, session_id: str, params: MusicParams) -> None:
        ...

    def apply(self, params: MusicParams) -> None:
        ...

    def stop(self) -> None:
        ...
```

The adapter must provide its own transport callbacks, map `MusicParams` to vendor controls, handle acknowledgement and reconnection, and measure end-to-end latency. Do not treat the current `MubertEngine` comments as a functioning SDK integration.

## 12. Configuration

All tunable constants are immutable dataclasses in `mdt_core/config.py`. Create a new `Config` rather than modifying `DEFAULT`:

```python
from mdt_core.config import Config, ControlConfig

cfg = Config(control=ControlConfig(kp=0.35, ki=0.03))
```

`ControlConfig.uncertainty_soft_limit` and `uncertainty_hard_limit` define the derating range. The `PlannerConfig.adaptive_*` fields define adaptive ISO speed and tracking-error thresholds. Any parameter change used in a study should be versioned, preregistered, and validated independently.
