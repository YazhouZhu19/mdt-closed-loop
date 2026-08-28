"""全部可整定常量集中在此，并在构造时验证配置。"""

import math
from dataclasses import dataclass


def _positive(name: str, value: float) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and > 0, got {value!r}")


def _unit_interval(name: str, value: float) -> None:
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be in [0, 1], got {value!r}")


@dataclass(frozen=True)
class SignalConfig:
    eda_fs: float = 32.0
    eda_lowpass_hz: float = 5.0
    eda_window_s: float = 10.0
    eda_step_s: float = 2.0
    hrv_window_s: float = 60.0
    hrv_step_s: float = 15.0
    scr_amplitude_threshold: float = 0.01
    max_impedance: float = 3.0e6
    max_accel_rms: float = 0.08
    min_rr_count: int = 30
    max_missing_fraction: float = 0.05
    rr_range_ms: tuple[float, float] = (300.0, 2000.0)

    def __post_init__(self) -> None:
        for name in (
            "eda_fs",
            "eda_lowpass_hz",
            "eda_window_s",
            "eda_step_s",
            "hrv_window_s",
            "hrv_step_s",
            "max_impedance",
            "max_accel_rms",
        ):
            _positive(name, float(getattr(self, name)))
        if self.eda_lowpass_hz >= self.eda_fs / 2:
            raise ValueError("eda_lowpass_hz must be below the Nyquist frequency")
        if self.eda_step_s > self.eda_window_s or self.hrv_step_s > self.hrv_window_s:
            raise ValueError("signal step size cannot exceed its window size")
        if (
            not math.isfinite(self.scr_amplitude_threshold)
            or self.scr_amplitude_threshold < 0
            or not isinstance(self.min_rr_count, int)
            or isinstance(self.min_rr_count, bool)
            or self.min_rr_count < 4
        ):
            raise ValueError("invalid signal feature threshold")
        _unit_interval("max_missing_fraction", self.max_missing_fraction)
        lo, hi = self.rr_range_ms
        if not (math.isfinite(lo) and math.isfinite(hi)) or lo <= 0 or lo >= hi:
            raise ValueError("rr_range_ms must be an increasing positive interval")


@dataclass(frozen=True)
class StateConfig:
    baseline_sessions_required: int = 3
    baseline_rest_seconds: float = 120.0
    eda_weight: float = 0.6
    hrv_weight: float = 0.4
    kalman_q: float = 0.01
    kalman_r: float = 0.25
    min_confidence: float = 0.35
    min_sigma: float = 1.0e-6
    max_abs_z: float = 12.0

    def __post_init__(self) -> None:
        if self.baseline_sessions_required < 1:
            raise ValueError("baseline_sessions_required must be >= 1")
        _positive("baseline_rest_seconds", self.baseline_rest_seconds)
        if (
            not math.isfinite(self.eda_weight)
            or not math.isfinite(self.hrv_weight)
            or self.eda_weight < 0
            or self.hrv_weight < 0
            or self.eda_weight + self.hrv_weight <= 0
        ):
            raise ValueError(
                "state fusion weights must be non-negative with a positive sum"
            )
        _positive("kalman_q", self.kalman_q)
        _positive("kalman_r", self.kalman_r)
        _unit_interval("min_confidence", self.min_confidence)
        _positive("min_sigma", self.min_sigma)
        _positive("max_abs_z", self.max_abs_z)


@dataclass(frozen=True)
class PlannerConfig:
    session_duration_s: float = 2400.0
    iso_match_duration_s: float = 300.0
    descent_duration_s: float = 900.0
    floor_arousal: float = 0.15
    direct_target: float = 0.20
    adaptive_iso: bool = True
    adaptive_min_speed: float = 0.35
    adaptive_max_speed: float = 1.50
    adaptive_on_track_error: float = 0.06
    adaptive_lag_error: float = 0.15
    adaptive_min_reliability: float = 0.35
    dose_bands: tuple = ((3, 10, "small"), (10, 24, "medium"), (16, 51, "large"))

    def __post_init__(self) -> None:
        for name in (
            "session_duration_s",
            "iso_match_duration_s",
            "descent_duration_s",
        ):
            _positive(name, float(getattr(self, name)))
        _unit_interval("floor_arousal", self.floor_arousal)
        _unit_interval("direct_target", self.direct_target)
        if not isinstance(self.adaptive_iso, bool):
            raise TypeError("adaptive_iso must be bool")
        for name in (
            "adaptive_min_speed",
            "adaptive_max_speed",
            "adaptive_on_track_error",
            "adaptive_lag_error",
        ):
            _positive(name, float(getattr(self, name)))
        _unit_interval("adaptive_min_reliability", self.adaptive_min_reliability)
        if self.adaptive_min_speed > self.adaptive_max_speed:
            raise ValueError("adaptive speed limits must be increasing")
        if self.adaptive_on_track_error >= self.adaptive_lag_error:
            raise ValueError("adaptive error thresholds must be increasing")
        if not self.dose_bands:
            raise ValueError("dose_bands must not be empty")
        for lo, hi, name in self.dose_bands:
            if (
                not (math.isfinite(lo) and math.isfinite(hi))
                or lo < 0
                or hi < lo
                or not name
            ):
                raise ValueError(f"invalid dose band: {(lo, hi, name)!r}")


@dataclass(frozen=True)
class ControlConfig:
    kp: float = 0.45
    ki: float = 0.05
    deadband: float = 0.08
    integral_clamp: float = 2.0
    output_clamp: float = 1.0
    deadband_integral_leak: float = 0.95
    uncertainty_soft_limit: float = 0.25
    uncertainty_hard_limit: float = 0.75

    def __post_init__(self) -> None:
        if (
            not (math.isfinite(self.kp) and math.isfinite(self.ki))
            or self.kp < 0
            or self.ki < 0
        ):
            raise ValueError("PI gains must be non-negative")
        for name in ("deadband", "integral_clamp", "output_clamp"):
            _positive(name, float(getattr(self, name)))
        _unit_interval("deadband_integral_leak", self.deadband_integral_leak)
        for name in ("uncertainty_soft_limit", "uncertainty_hard_limit"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and >= 0")
        if self.uncertainty_soft_limit >= self.uncertainty_hard_limit:
            raise ValueError("uncertainty limits must be strictly increasing")
        if self.deadband > 1:
            raise ValueError("deadband cannot exceed the normalized arousal range")


@dataclass(frozen=True)
class GrammarConfig:
    bar_duration_s: float = 4.0
    phrase_bars: int = 8
    max_tempo_delta_per_30s: float = 2.0
    tempo_range: tuple = (55.0, 85.0)
    max_layer_delta_per_phrase: int = 1

    def __post_init__(self) -> None:
        _positive("bar_duration_s", self.bar_duration_s)
        _positive("max_tempo_delta_per_30s", self.max_tempo_delta_per_30s)
        if self.phrase_bars < 1 or self.max_layer_delta_per_phrase < 1:
            raise ValueError("phrase and layer limits must be positive integers")
        lo, hi = self.tempo_range
        if not (math.isfinite(lo) and math.isfinite(hi) and 0 < lo < hi):
            raise ValueError(
                "tempo_range must be a finite increasing positive interval"
            )


@dataclass(frozen=True)
class ProgramConfig:
    isi_response_drop: float = 6.0
    isi_remission_score: float = 7.0
    futility_session_count: int = 16
    futility_min_change: float = 2.0

    def __post_init__(self) -> None:
        for name in ("isi_response_drop", "futility_min_change"):
            _positive(name, float(getattr(self, name)))
        if (
            not math.isfinite(self.isi_remission_score)
            or self.isi_remission_score < 0
            or not isinstance(self.futility_session_count, int)
            or isinstance(self.futility_session_count, bool)
            or self.futility_session_count < 1
        ):
            raise ValueError("invalid program outcome threshold")


@dataclass(frozen=True)
class Config:
    signal: SignalConfig = SignalConfig()
    state: StateConfig = StateConfig()
    planner: PlannerConfig = PlannerConfig()
    control: ControlConfig = ControlConfig()
    grammar: GrammarConfig = GrammarConfig()
    program: ProgramConfig = ProgramConfig()


DEFAULT = Config()
