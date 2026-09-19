"""L3 控制器 + L3.5 音乐语法约束层。

控制器不含 D 项：生理信号噪声大，微分会放大抖动。
语法层是强制的——直接把控制器输出送进引擎会产生听感断裂。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import ControlConfig
from .l35_guard import MusicGrammar  # noqa: F401 -- compatibility re-export
from .types import State


@dataclass(frozen=True)
class _TempoCommand:
    command_id: str
    base_tempo_bpm: float
    tempo_per_unit: float
    output: float
    output_scale: float
    integral_gain: float
    integration_dt: float
    submitted_mono_s: float


class PIController:
    """Two explicitly selected recurrences, with independent integral histories.

    ``legacy`` preserves the original second-based, q-scaled accumulation.
    ``normalized`` uses I += error * dt / T_I only when q >= q_on, and
    u = clip(q * (Kp * error + Ki * I)). Active control does not leak I;
    zero reliability, deadband and timed suspension use exp(-dt / tau_I).
    Reliability below q_on but above zero freezes I and still derates output.
    The optional integral q multiplier exists solely for the q-squared ablation.

    For time-normalization equivalence alone, divide legacy I and its clamp by
    T_I, multiply Ki by T_I, and enable normalized_integral_q_scaling. Disabling
    that multiplier changes the controller and requires separate validation.

    ACK correction is opt-in and applies only to this controller's unmixed
    tempo intent. A mixer must define its own attribution before feeding the
    same executed action back into two independent controllers.
    """

    def __init__(self, cfg: ControlConfig):
        self.cfg = cfg
        self._integral = 0.0
        self._last_output = 0.0
        self._last_scale = 0.0
        self._last_dt = 0.0
        self._last_ki = 0.0
        self._can_register_command = False
        self._pending_tempo: _TempoCommand | None = None
        self._used_command_ids: set[str] = set()
        self._last_command_time = -math.inf

    @property
    def integral_state(self) -> float:
        """Integral in the units of the selected formulation (read-only)."""
        return self._integral

    @property
    def last_scale(self) -> float:
        """Reliability multiplier applied to the latest control decision."""
        return self._last_scale

    def reliability(self, state: State) -> float:
        """Combine observation confidence and posterior state uncertainty."""
        if not math.isfinite(state.confidence) or not 0.0 <= state.confidence <= 1.0:
            raise ValueError("state.confidence must be finite and in [0, 1]")
        if not math.isfinite(state.uncertainty) or state.uncertainty < 0:
            raise ValueError("state.uncertainty must be finite and >= 0")
        soft = self.cfg.uncertainty_soft_limit
        hard = self.cfg.uncertainty_hard_limit
        if state.uncertainty <= soft:
            uncertainty_scale = 1.0
        elif state.uncertainty >= hard:
            uncertainty_scale = 0.0
        else:
            uncertainty_scale = (hard - state.uncertainty) / (hard - soft)
        return state.confidence * uncertainty_scale

    def step(self, target: float, state: State, dt: float) -> tuple[float, str]:
        if not math.isfinite(target) or not 0.0 <= target <= 1.0:
            raise ValueError("target must be finite and in [0, 1]")
        if not math.isfinite(state.arousal) or not 0.0 <= state.arousal <= 1.0:
            raise ValueError("state.arousal must be finite and in [0, 1]")
        if not math.isfinite(dt) or dt <= 0:
            raise ValueError("dt must be finite and > 0")
        if self.cfg.formulation == "normalized" and dt > self.cfg.max_step_s:
            raise ValueError("dt exceeds max_step_s; suspend/reset after a long gap")
        scale = self.reliability(state)
        if self.cfg.formulation == "normalized":
            return self._normalized_step(target, state, dt, scale)
        self._last_scale = scale
        if scale < 1e-6:
            self._integral *= self.cfg.deadband_integral_leak
            self._last_output = 0.0
            reason = (
                "open_loop_high_uncertainty"
                if state.uncertainty >= self.cfg.uncertainty_hard_limit
                else "open_loop_no_confidence"
            )
            return 0.0, reason

        error = target - state.arousal
        if abs(error) < self.cfg.deadband:
            self._integral *= self.cfg.deadband_integral_leak
            self._last_output = 0.0
            return 0.0, "deadband"

        # Accumulate only the fraction of error that can safely be acted upon;
        # this prevents hidden integral wind-up while control is derated.
        self._integral += error * dt * scale
        clamp = self.cfg.integral_clamp
        self._integral = max(-clamp, min(clamp, self._integral))

        output = scale * (self.cfg.kp * error + self.cfg.ki * self._integral)
        output = max(-self.cfg.output_clamp, min(self.cfg.output_clamp, output))
        self._last_output = output
        reason = "closed_loop" if scale >= 1.0 - 1e-9 else "closed_loop_derated"
        return output, reason

    def _normalized_step(
        self,
        target: float,
        state: State,
        dt: float,
        scale: float,
    ) -> tuple[float, str]:
        self._last_scale = scale
        self._last_dt = dt
        self._last_ki = self.cfg.ki
        self._can_register_command = False
        if scale < 1e-6:
            self._leak(dt)
            self._pending_tempo = None
            self._last_output = 0.0
            reason = (
                "open_loop_high_uncertainty"
                if state.uncertainty >= self.cfg.uncertainty_hard_limit
                else "open_loop_no_confidence"
            )
            return 0.0, reason
        error = target - state.arousal
        if abs(error) < self.cfg.deadband:
            self._leak(dt)
            self._pending_tempo = None
            self._last_output = 0.0
            return 0.0, "deadband"
        next_integral = self._integral
        if scale >= self.cfg.integration_min_reliability:
            multiplier = scale if self.cfg.normalized_integral_q_scaling else 1.0
            increment = error * (dt / self.cfg.integral_time_s) * multiplier
            if not math.isfinite(increment):
                raise ValueError("normalized integral increment is not finite")
            next_integral = self._clip_integral(self._integral + increment)
        output = scale * (self.cfg.kp * error + self.cfg.ki * next_integral)
        if not math.isfinite(output):
            raise ValueError("normalized PI output is not finite")
        self._integral = next_integral
        self._last_output = max(
            -self.cfg.output_clamp, min(self.cfg.output_clamp, output)
        )
        self._can_register_command = True
        reason = "closed_loop" if scale >= 1.0 - 1e-9 else "closed_loop_derated"
        return self._last_output, reason

    def _clip_integral(self, value: float) -> float:
        return max(-self.cfg.integral_clamp, min(self.cfg.integral_clamp, value))

    def _leak(self, dt: float) -> None:
        self._integral *= math.exp(-dt / self.cfg.integral_leak_tau_s)

    def register_tempo_command(
        self,
        command_id: str,
        *,
        base_tempo_bpm: float,
        submitted_mono_s: float,
        tempo_per_unit: float = 12.0,
    ) -> tuple[bool, str]:
        """Bind one actually submitted command to the most recent PI output.

        The command's requested absolute tempo must be base + tempo_per_unit
        * last_output, before grammar projection. Call only for an unmixed PI
        decision, after submission succeeds. Calling this method does not infer
        execution, change I, or synthesize an ACK. One command may be outstanding;
        reset/suspend invalidates it, and IDs cannot be reused before reset.
        """
        if not self.cfg.ack_backcalculation:
            return False, "ack_disabled"
        if not isinstance(command_id, str) or not command_id or len(command_id) > 128:
            raise ValueError(
                "command_id must be a nonempty string of at most 128 characters"
            )
        for name, value in (
            ("base_tempo_bpm", base_tempo_bpm),
            ("tempo_per_unit", tempo_per_unit),
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if not math.isfinite(submitted_mono_s) or submitted_mono_s < 0:
            raise ValueError("submitted_mono_s must be finite and nonnegative")
        if command_id in self._used_command_ids:
            return False, "ack_duplicate_command"
        if submitted_mono_s < self._last_command_time:
            return False, "ack_time_reversed"
        if self._pending_tempo is not None:
            if (
                submitted_mono_s - self._pending_tempo.submitted_mono_s
                > self.cfg.ack_timeout_s
            ):
                self._pending_tempo = None
            else:
                return False, "ack_command_pending"
        if not self._can_register_command:
            return False, "ack_no_active_decision"
        if self._last_scale * self._last_ki <= 1e-12:
            return False, "ack_unobservable_integral"
        requested_tempo = base_tempo_bpm + tempo_per_unit * self._last_output
        if not math.isfinite(requested_tempo) or requested_tempo <= 0:
            raise ValueError("requested absolute tempo must be finite and positive")
        self._pending_tempo = _TempoCommand(
            command_id,
            base_tempo_bpm,
            tempo_per_unit,
            self._last_output,
            self._last_scale,
            self._last_ki,
            self._last_dt,
            submitted_mono_s,
        )
        self._used_command_ids.add(command_id)
        self._last_command_time = submitted_mono_s
        self._can_register_command = False
        return True, "ack_command_registered"

    def acknowledge_tempo(
        self,
        command_id: str,
        *,
        actual_tempo_bpm: float,
        acknowledged_mono_s: float,
    ) -> tuple[bool, str]:
        """Apply one bounded correction using an actual engine confirmation.

        u_ack = (actual_tempo - base_tempo) / tempo_per_unit is dimensionless.
        Since output is q*(Kp*error + Ki*I), the integral-coordinate correction
        is (dt/T_tracking) * (u_ack-u_cmd)/(q*Ki). dt is the bound decision's
        integration interval, not network delay. Without q*Ki > epsilon this
        inverse is undefined and registration is disabled. No BPM is added to I.
        """
        if not self.cfg.ack_backcalculation:
            return False, "ack_disabled"
        if not math.isfinite(actual_tempo_bpm) or actual_tempo_bpm <= 0:
            raise ValueError("actual_tempo_bpm must be finite and positive")
        if not math.isfinite(acknowledged_mono_s) or acknowledged_mono_s < 0:
            raise ValueError("acknowledged_mono_s must be finite and nonnegative")
        command = self._pending_tempo
        if command is None or command.command_id != command_id:
            return False, "ack_unknown_or_duplicate"
        age = acknowledged_mono_s - command.submitted_mono_s
        if age < 0:
            return False, "ack_time_reversed"
        if age > self.cfg.ack_timeout_s:
            self._pending_tempo = None
            return False, "ack_expired"
        actual_output = (
            actual_tempo_bpm - command.base_tempo_bpm
        ) / command.tempo_per_unit
        correction = (
            command.integration_dt
            / self.cfg.ack_tracking_time_s
            * (actual_output - command.output)
            / (command.output_scale * command.integral_gain)
        )
        if not math.isfinite(correction):
            raise ValueError("ACK integral correction is not finite")
        self._integral = self._clip_integral(self._integral + correction)
        self._pending_tempo = None
        return True, "ack_backcalculated"

    def reset(self) -> None:
        self._integral = 0.0
        self._last_output = 0.0
        self._last_scale = 0.0
        self._last_dt = 0.0
        self._last_ki = 0.0
        self._can_register_command = False
        self._pending_tempo = None
        self._used_command_ids.clear()
        self._last_command_time = -math.inf

    def suspend(self, dt: float | None = None) -> None:
        """Clear output and outstanding ACK binding; optionally advance leakage.

        Legacy keeps its exact per-event leak, with or without dt. Normalized
        mode requires explicit elapsed seconds to leak; an untimed suspend
        only clears output because inventing a duration changes the recurrence.
        Timed holds may exceed max_step_s: no stale error is integrated.
        """
        if dt is not None and (not math.isfinite(dt) or dt < 0):
            raise ValueError("suspend dt must be finite and >= 0")
        if self.cfg.formulation == "legacy":
            self._integral *= self.cfg.deadband_integral_leak
        elif dt is not None:
            self._leak(dt)
        self._last_output = 0.0
        self._last_scale = 0.0
        self._can_register_command = False
        self._pending_tempo = None
