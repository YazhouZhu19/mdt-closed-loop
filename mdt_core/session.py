"""Session orchestration for the multi-rate MDT closed loop.

Sensor updates and audio-boundary commits use separate clocks:

* ``fast_tick`` consumes an EDA window at the configured fast cadence.
* ``slow_tick`` consumes a full EDA/HRV window at the configured HRV cadence.
* ``music_boundary`` is called by the audio engine on a real bar/phrase event.

``tick`` remains a compatibility alias for ``slow_tick``.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from .config import DEFAULT, Config
from .engine import MusicEngine, NullEngine, ShamEngine
from .execution import AcceptedDecision, AckEngine, ExecutionGateway, Mode, MusicVector
from .l0_signal import extract, extract_eda
from .l1_state import ArousalEstimator, IndividualBaseline
from .l2_planner import DoseTracker, TrajectoryPlanner
from .l3_control import MusicGrammar, PIController
from .l4_l6 import (
    ArmAssigner,
    OutcomeEvaluator,
    ProgramState,
    SafetyMonitor,
    SessionRecorder,
)
from .l35_mapping import map_control
from .learning import Approval, LearningRuntime, make_context
from .policy import Policy
from .trial import pin_trial
from .types import Arm, ControlRecord, MusicParams, RawWindow, SessionStatus, State


class Session:
    def __init__(
        self,
        user_id: str,
        baseline: IndividualBaseline,
        program: ProgramState,
        engine: MusicEngine | None = None,
        arm: Arm | None = None,
        cfg: Config = DEFAULT,
        out_dir: str = "./data",
        *,
        is_calibration: bool = False,
        safety_monitor: SafetyMonitor | None = None,
        sham_trajectory: list[MusicParams] | None = None,
        policies: Mapping[str, Policy] | None = None,
        emission_model: Any = None,
        policy_context: Mapping | None = None,
        policy_approval: Approval | None = None,
    ):
        if not user_id:
            raise ValueError("user_id must not be empty")
        if not out_dir:
            raise ValueError("out_dir must not be empty")
        self.cfg = cfg
        self.user_id = user_id
        self.session_id = uuid.uuid4().hex[:12]
        self.arm = arm or ArmAssigner().assign(user_id)
        self.baseline = baseline
        self.program = program
        self.is_calibration = is_calibration

        self.estimator = ArousalEstimator(baseline, cfg.state)
        self.planner = TrajectoryPlanner(
            ArmAssigner.strategy_for(self.arm), cfg.planner
        )
        self.dose = DoseTracker(cfg.planner)
        self.dose.completed = program.completed_sessions
        self.controller = PIController(cfg.control)
        self.grammar = MusicGrammar(cfg.grammar)

        inner_engine = engine or NullEngine()
        if (
            not self.is_calibration
            and self.arm is Arm.SHAM
            and not isinstance(inner_engine, ShamEngine)
        ):
            if not sham_trajectory:
                raise ValueError(
                    "SHAM sessions require a non-empty pre-registered trajectory"
                )
            self.engine: MusicEngine = ShamEngine(inner_engine, sham_trajectory)
        else:
            if (
                not self.is_calibration
                and self.arm is not Arm.SHAM
                and isinstance(inner_engine, ShamEngine)
            ):
                raise ValueError("ShamEngine can only be used by the SHAM arm")
            self.engine = inner_engine

        self.recorder = SessionRecorder(self.session_id, user_id, self.arm, out_dir)
        self.outcome = OutcomeEvaluator(cfg.program)
        self.safety = safety_monitor or SafetyMonitor()
        self._learning: LearningRuntime | None = None
        self._policy_context = dict(policy_context or {})
        self._quality_counts = {"ok": 0, "noisy": 0, "lost": 0}
        self._initial_arousal: float | None = None
        # Controls and calibration never load, copy, hash, or invoke models.
        if (self.arm is Arm.FULL_LOOP and not is_calibration
                and cfg.learning.mode != "disabled"):
            enabled_modules = tuple(
                module for module in ("l1", "l2", "l3", "taste")
                if getattr(cfg.learning, f"enable_{module}")
            )
            supplied = any(module in (policies or {}) for module in enabled_modules)
            supplied = supplied or (cfg.learning.enable_l1 and emission_model is not None)
            if supplied:
                pin_trial(
                    out_dir, cfg.learning.trial_id,
                    dict(cfg.learning.policy_versions), cfg.learning.mode,
                    enabled_modules,
                )
            self._learning = LearningRuntime(
                cfg, baseline, self.recorder, policies or {},
                emission_model=emission_model, approval=policy_approval,
            )

        self.status = SessionStatus.CREATED
        self._started = False
        self._anchored = False
        self._last_sensor_t: float | None = None
        self._last_fast_t: float | None = None
        self._last_slow_t: float | None = None
        self._last_music_t: float | None = None
        self._last_state = State(
            t=0.0,
            arousal=0.5,
            confidence=0.0,
            uncertainty=1.0,
        )
        self._last_target = 0.5
        self._last_reason = "not_started"
        self._last_control = 0.0
        self._last_control_scale = 0.0
        self._open_loop_target: float | None = None
        self._output_path: Path | None = None
        self._baseline_value_count_at_start = baseline.accumulated_value_count
        self.execution: ExecutionGateway | None = None
        self._source_end = 0.0
        self._execution_sequence = 0
        self._last_control_t: float | None = None
        self._last_source_end: float | None = None
        if cfg.execution.enabled:
            if self.arm is not Arm.FULL_LOOP or is_calibration:
                raise ValueError("v2.1 execution profile currently supports FULL_LOOP simulation only")
            # ExecutionGateway checks the additional submit capability at runtime.
            self.execution = ExecutionGateway(
                self.session_id, cast(AckEngine, self.engine), cfg.execution, cfg.grammar,
                self.grammar.current,
            )
            # The recorder serializes this shared append-only event list.
            self.recorder.execution_events = self.execution.events

    def _learning_allowed(self) -> bool:
        return self.execution is None or self.execution.mode in (
            Mode.BASELINE, Mode.LEARNING_ENABLED, Mode.DEGRADED,
        )

    def _source_valid(self, window: RawWindow) -> bool:
        self._source_end = window.t if window.observed_end_t is None else window.observed_end_t
        if self.execution is None:
            return True
        self.watchdog(window.t)
        if self.execution.mode in (Mode.STOPPING, Mode.STOPPED):
            return False
        if window.observed_end_t is None:
            self._accept_observation(window.t)
            self.execution.hold(window.t, "missing_source_timestamp")
            self._suspend_at(window.t)
            return False
        age = window.t - self._source_end
        if not math.isfinite(age) or not 0 <= age <= self.cfg.execution.observation_ttl_s:
            self._accept_observation(window.t)
            self.execution.hold(window.t, "source_window_age")
            self.grammar.cancel_pending()
            self._suspend_at(window.t)
            return False
        if self._last_source_end is not None and self._source_end <= self._last_source_end:
            self.execution._event("observation_rejected", reason="reused_source_window")
            return False
        self._last_source_end = self._source_end
        return True

    def _suspend_at(self, t: float) -> None:
        dt = 0.0 if self._last_control_t is None else max(0.0, t - self._last_control_t)
        self.controller.suspend(dt)
        if self._learning is not None:
            self._learning.suspend(dt)
        self._last_control_t = t

    def watchdog(self, t: float) -> Mode | None:
        """Poll on the simulator clock even when sensor and music events stop."""
        if self.execution is None:
            return None
        mode = self.execution.poll(t)
        if mode in (Mode.INITIALIZING, Mode.HOLD):
            self._suspend_at(t)
            self.grammar.cancel_pending()
        if mode in (Mode.STOPPING, Mode.STOPPED) and self._output_path is None:
            self.abort("execution_watchdog")
        return mode

    def _stop_engine(self) -> None:
        if self.execution is not None:
            self.execution.stop(self.execution._now, "session_end")
        elif self._started:
            self.engine.stop()

    def _validate_observation_allowed(self, t: float) -> None:
        if self.status in (SessionStatus.FINISHED, SessionStatus.ABORTED):
            raise RuntimeError(f"session is already {self.status.value}")
        if not math.isfinite(t) or t < 0:
            raise ValueError("sensor time must be finite and >= 0")
        if t > self.cfg.planner.session_duration_s:
            raise RuntimeError("session duration has been exceeded")
        if self._last_sensor_t is not None and t < self._last_sensor_t:
            raise ValueError("sensor timestamps must be monotonic")
        if self._last_music_t is not None and t < self._last_music_t:
            raise ValueError(
                "sensor event cannot precede the latest processed music event"
            )

    def _accept_observation(self, t: float) -> None:
        self._last_sensor_t = t
        if self.status is SessionStatus.CREATED:
            self.status = SessionStatus.RUNNING

    @staticmethod
    def _validate_step(
        t: float, last_t: float | None, minimum: float, name: str
    ) -> None:
        if last_t is not None and t - last_t < minimum - 1e-6:
            raise ValueError(f"{name} updates must be at least {minimum:g}s apart")

    def _ensure_started(self, params: MusicParams) -> None:
        if self.is_calibration:
            return
        if not self._started:
            self.engine.start(self.session_id, params)
            self._started = True

    def _select_control(
        self, target: float, state: State, dt: float
    ) -> tuple[float, str, float]:
        if self.is_calibration:
            self.grammar.cancel_pending()
            return 0.0, "calibration_open_loop", 0.0
        if self.arm is Arm.SHAM and not self.is_calibration:
            self.grammar.cancel_pending()
            return 0.0, "sham_pre_registered", 0.0
        if ArmAssigner.is_open_loop_iso(self.arm):
            previous = (
                target if self._open_loop_target is None else self._open_loop_target
            )
            self._open_loop_target = target
            return target - previous, "open_loop_iso_trajectory", 1.0
        if state.confidence < self.cfg.state.min_confidence:
            self.controller.suspend(dt)
            if self._learning is not None:
                self._learning.suspend(dt if self.execution is not None else None)
            self.grammar.cancel_pending()
            return 0.0, "open_loop_low_confidence", 0.0
        if self.execution is not None and (not self._learning_allowed() or dt == 0):
            self.controller.suspend(dt)
            if self._learning is not None:
                self._learning.suspend(dt)
            return 0.0, "baseline_recovery_hold", self.controller.reliability(state)
        output, reason = self.controller.step(target, state, dt)
        if self._learning is not None and self._learning_allowed():
            ctx = self._context(state)
            ctx.update(target=target, baseline_output=output, dt=dt)
            output = self._learning.control(
                output, target, state, dt, ctx, self.controller.last_scale,
            )
        return output, reason, self.controller.last_scale

    def _context(self, state: State) -> dict:
        ctx = make_context(
            self.user_id, self.baseline, state, self.cfg,
            completed_sessions=self.program.completed_sessions,
            previous_outcome=(self.program.isi_history[-1] if self.program.isi_history else None),
            extra=self._policy_context,
        )
        total = max(1, sum(self._quality_counts.values()))
        ctx["quality_distribution"] = {key: value / total for key, value in self._quality_counts.items()}
        ctx["reliability"] = self.controller.reliability(state)
        ctx["initial_arousal"] = self._initial_arousal if self._initial_arousal is not None else state.arousal
        ctx["session_id"] = self.session_id
        return ctx

    def _estimate(self, feats, process_scale: float) -> State:
        self._quality_counts[feats.quality.value] += 1
        if self._learning is not None:
            self._learning.last_metadata = {}
        state = self.estimator.update(feats, process_scale=process_scale)
        if self._learning is not None and self._learning_allowed():
            state = self._learning.estimate(feats, process_scale, state, self._context(state))
        return state

    def _policy_metadata(self) -> dict:
        if self._learning is None:
            return {}
        return {"policy_versions": dict(self.recorder.policy_manifest["versions"]),
                **self._learning.last_metadata}

    def _process_features(
        self,
        feats,
        state: State,
        dt: float,
        *,
        bar_boundary: bool,
        phrase_boundary: bool,
    ) -> tuple[State, MusicParams]:
        if self.execution is not None:
            dt = 0.0 if self._last_control_t is None else max(0.0, feats.t - self._last_control_t)
            self._last_control_t = feats.t
        if not self._anchored and state.confidence > 0:
            self._initial_arousal = state.arousal
            if self._learning is not None:
                self._learning.plan(self.planner, self._context(state),
                                    self.controller.reliability(state))
            self.planner.set_anchor(state.arousal)
            self._anchored = True

        adaptive_full_loop = (
            self.cfg.planner.adaptive_iso and self.arm is Arm.FULL_LOOP
        )
        if adaptive_full_loop:
            reliability = (
                self.controller.reliability(state)
                if state.confidence >= self.cfg.state.min_confidence
                else 0.0
            )
            if self.execution is not None:
                target = self.planner.active_target(
                    dt, state, reliability, enabled=self._learning_allowed(),
                )
            else:
                target = self.planner.adaptive_target(feats.t, state, reliability)
        else:
            target = self.planner.target(feats.t)
        control, reason, control_scale = self._select_control(target, state, dt)
        self.grammar.request(control, feats.t)
        desired = map_control(control, self.grammar.current, self.cfg.grammar)
        if self._learning is not None and self._learning_allowed():
            selected = self._learning.taste(self.grammar, control, feats.t, self._context(state), control_scale)
            if selected is not None:
                desired = selected
            if control_scale == 0:
                # A zero-reliability hold also cancels the timbre restoration
                # that request(0) can queue after a learned output. A trusted
                # deadband has positive reliability and still restores at a
                # real boundary.
                self.grammar.cancel_pending()
        if self.execution is not None:
            self._execution_sequence += 1
            if control_scale <= 0:
                self.execution.hold(feats.t, "unreliable_observation")
            else:
                mode = Mode.BASELINE
                if self._learning_allowed() and self._learning is not None:
                    mode = Mode.DEGRADED if self._learning.quarantined else Mode.LEARNING_ENABLED
                decision = AcceptedDecision(
                    self.session_id, self._execution_sequence,
                    f"observation:{float(self._source_end).hex()}", self._source_end,
                    feats.t, self.execution.ack_id, MusicVector.of(desired), mode,
                )
                self.execution.accept(decision, feats.t)
            if bar_boundary or phrase_boundary:
                self.execution.boundary(feats.t, phrase=phrase_boundary)
            params, changed = self.execution.current, False
            self.grammar.current = params.copy()
            self.grammar.cancel_pending()
            self._started = self.execution._started
        else:
            params, changed = self.grammar.commit(
                feats.t, bar_boundary=bar_boundary, phrase_boundary=phrase_boundary,
            )
            self._ensure_started(params)
        if self.arm is Arm.SHAM and not self.is_calibration:
            if not isinstance(self.engine, ShamEngine) or self.engine.current is None:
                raise RuntimeError(
                    "SHAM engine did not expose its active trajectory point"
                )
            params = self.engine.current.copy()
        elif changed and self._started:
            self.engine.apply(params)

        self.recorder.log_physio(feats, state)
        self.recorder.log_music(
            ControlRecord(
                t=feats.t,
                params=params,
                target_arousal=target,
                estimated_arousal=state.arousal,
                error=target - state.arousal,
                reason=reason,
                state_uncertainty=state.uncertainty,
                control_output=control,
                control_scale=control_scale,
                trajectory_phase=self.planner.phase,
                trajectory_speed=self.planner.speed_factor,
                **self._policy_metadata(),
            )
        )
        self._last_state = state
        self._last_target = target
        self._last_reason = reason
        self._last_control = control
        self._last_control_scale = control_scale
        if self.execution is not None and self.execution.mode in (Mode.STOPPING, Mode.STOPPED):
            self.abort("execution_failure")
        return state, params

    def fast_tick(
        self,
        window: RawWindow,
        dt: float | None = None,
        *,
        bar_boundary: bool = False,
        phrase_boundary: bool = False,
    ) -> tuple[State, MusicParams]:
        """Process an EDA-only update at the fast cadence."""
        self._validate_observation_allowed(window.t)
        self._validate_step(
            window.t, self._last_fast_t, self.cfg.signal.eda_step_s, "EDA"
        )
        if not self._source_valid(window):
            return self._last_state, self.grammar.current.copy()
        step = self.cfg.signal.eda_step_s if dt is None else dt
        if not math.isfinite(step) or step <= 0:
            raise ValueError("dt must be finite and > 0")
        feats = extract_eda(window, self.cfg.signal)
        self._accept_observation(window.t)
        self._last_fast_t = window.t
        state = self._estimate(
            feats, process_scale=step / self.cfg.signal.hrv_step_s
        )
        return self._process_features(
            feats,
            state,
            step,
            bar_boundary=bar_boundary,
            phrase_boundary=phrase_boundary,
        )

    def slow_tick(
        self,
        window: RawWindow,
        dt: float | None = None,
        *,
        bar_boundary: bool = False,
        phrase_boundary: bool = False,
    ) -> tuple[State, MusicParams]:
        """Process a full EDA/HRV window at the slow cadence."""
        self._validate_observation_allowed(window.t)
        self._validate_step(
            window.t, self._last_slow_t, self.cfg.signal.hrv_step_s, "HRV"
        )
        if not self._source_valid(window):
            return self._last_state, self.grammar.current.copy()
        step = self.cfg.signal.hrv_step_s if dt is None else dt
        if not math.isfinite(step) or step <= 0:
            raise ValueError("dt must be finite and > 0")
        feats = extract(window, self.cfg.signal)
        self._accept_observation(window.t)
        self._last_slow_t = window.t

        if (
            self.is_calibration
            and not self.baseline.is_ready
            and window.t <= self.cfg.state.baseline_rest_seconds
        ):
            self.baseline.accumulate(feats)

        state = self._estimate(
            feats, process_scale=step / self.cfg.signal.hrv_step_s
        )
        return self._process_features(
            feats,
            state,
            step,
            bar_boundary=bar_boundary,
            phrase_boundary=phrase_boundary,
        )

    def tick(
        self,
        window: RawWindow,
        dt: float = 60.0,
        *,
        bar_boundary: bool = True,
        phrase_boundary: bool = False,
    ) -> tuple[State, MusicParams]:
        """Compatibility alias for :meth:`slow_tick`."""
        return self.slow_tick(
            window,
            dt,
            bar_boundary=bar_boundary,
            phrase_boundary=phrase_boundary,
        )

    def music_boundary(self, t: float, *, phrase_boundary: bool = False) -> MusicParams:
        """Commit pending changes from an explicit audio-clock event."""
        if self.status is not SessionStatus.RUNNING:
            raise RuntimeError("music boundaries require a running session")
        if self.is_calibration:
            raise RuntimeError("calibration sessions do not actuate music")
        if not math.isfinite(t) or t < 0:
            raise ValueError("music time must be finite and >= 0")
        if t > self.cfg.planner.session_duration_s:
            raise RuntimeError("session duration has been exceeded")
        if self._last_music_t is not None and t < self._last_music_t:
            raise ValueError("music timestamps must be monotonic")
        if self._last_sensor_t is not None and t < self._last_sensor_t:
            raise ValueError(
                "music event cannot precede the latest processed sensor event"
            )
        if self.execution is not None:
            before = self.execution.current
            params = self.execution.boundary(t, phrase=phrase_boundary)
            changed = params != before
            self.grammar.current = params.copy()
            self._started = self.execution._started
        else:
            params, changed = self.grammar.commit(
                t, bar_boundary=True, phrase_boundary=phrase_boundary,
            )
            self._ensure_started(params)
        if self.arm is Arm.SHAM:
            if not isinstance(self.engine, ShamEngine):
                raise RuntimeError("SHAM arm requires ShamEngine")
            self.engine.apply(params)
            if self.engine.current is None:
                raise RuntimeError(
                    "SHAM engine did not expose its active trajectory point"
                )
            params = self.engine.current.copy()
            changed = True
        elif changed and self._started and self.execution is None:
            self.engine.apply(params)
        if changed and self._started:
            self.recorder.log_music(
                ControlRecord(
                    t=t,
                    params=params,
                    target_arousal=self._last_target,
                    estimated_arousal=self._last_state.arousal,
                    error=self._last_target - self._last_state.arousal,
                    reason=(
                        "sham_pre_registered_boundary"
                        if self.arm is Arm.SHAM
                        else "music_boundary_commit"
                    ),
                    state_uncertainty=self._last_state.uncertainty,
                    control_output=self._last_control,
                    control_scale=self._last_control_scale,
                    trajectory_phase=self.planner.phase,
                    trajectory_speed=self.planner.speed_factor,
                    **self._policy_metadata(),
                )
            )
        self._last_music_t = t
        if self.execution is not None:
            if self.execution.mode is Mode.HOLD:
                self._suspend_at(t)
            elif self.execution.mode in (Mode.STOPPING, Mode.STOPPED):
                self.abort("execution_failure")
        return params

    @staticmethod
    def _text_values(value: Any):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from Session._text_values(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                yield from Session._text_values(item)

    def _scan_subjective(self, payload: dict | None) -> bool:
        return any(
            self.safety.scan(text, self.user_id)
            for text in self._text_values(payload or {})
        )

    def submit_subjective(
        self,
        *,
        pre: dict | None = None,
        post: dict | None = None,
        instrument: dict | None = None,
    ) -> bool:
        """Record subjective data and immediately abort on a safety keyword."""
        if self.status in (SessionStatus.FINISHED, SessionStatus.ABORTED):
            raise RuntimeError(f"session is already {self.status.value}")
        self.recorder.log_subjective(pre=pre, post=post, instrument=instrument)
        hit = any(self._scan_subjective(part) for part in (pre, post, instrument))
        if hit:
            self.abort("safety_escalation")
        return hit

    def finish(
        self, post_survey: dict | None = None, isi_score: float | None = None
    ) -> str:
        """Finish exactly once; repeated calls return the same output path."""
        if self._output_path is not None:
            return str(self._output_path)
        if self.status is SessionStatus.ABORTED:
            raise RuntimeError("aborted session has no persisted output path")

        # Safety text is handled before ordinary completion validation: a malformed
        # outcome form must never suppress escalation.
        if self._scan_subjective(post_survey):
            self.recorder.log_subjective(post=post_survey)
            self._stop_engine()
            self.grammar.cancel_pending()
            self.controller.reset()
            if self._learning is not None:
                self._learning.suspend()
            self.program.stopped_reason = "safety_escalation"
            self.status = SessionStatus.ABORTED
            self._output_path = self.recorder.flush()
            return str(self._output_path)

        if self.status is SessionStatus.CREATED:
            raise RuntimeError(
                "cannot finish a session before the first valid observation"
            )
        if (
            not self.is_calibration
            and isi_score is not None
            and self.program.baseline_isi is None
        ):
            raise ValueError(
                "set ProgramState.baseline_isi before recording treatment ISI"
            )
        if (
            self.is_calibration
            and not self.baseline.is_ready
            and self.baseline.accumulated_value_count
            <= self._baseline_value_count_at_start
        ):
            raise RuntimeError("calibration session contains no valid baseline samples")

        self.recorder.log_subjective(post=post_survey)
        self._stop_engine()

        if self.is_calibration:
            if not self.baseline.is_ready:
                self.baseline.finalize_session(self.cfg.state)
        else:
            if isi_score is not None:
                self.program.record_isi(isi_score)
            self.program.completed_sessions += 1
            self.dose.record_completion()
            self.program.responder = self.outcome.is_responder(self.program)
            stopped = self.outcome.check_futility(self.program)
            if stopped:
                self.program.stopped_reason = stopped

        self.status = SessionStatus.FINISHED
        self._output_path = self.recorder.flush()
        return str(self._output_path)

    def abort(self, reason: str) -> str:
        """Stop actuation and persist available data after an abnormal exit."""
        if not reason:
            raise ValueError("abort reason must not be empty")
        if self._output_path is not None:
            return str(self._output_path)
        self._stop_engine()
        self.grammar.cancel_pending()
        self.controller.reset()
        if self._learning is not None:
            self._learning.suspend()
        self._last_control = 0.0
        self._last_control_scale = 0.0
        self.program.stopped_reason = reason
        self.status = SessionStatus.ABORTED
        self._output_path = self.recorder.flush()
        return str(self._output_path)
