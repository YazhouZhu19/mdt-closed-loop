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
from pathlib import Path
from typing import Any

from .config import DEFAULT, Config
from .engine import MusicEngine, NullEngine, ShamEngine
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

        self.status = SessionStatus.CREATED
        self._started = False
        self._anchored = False
        self._last_sensor_t: float | None = None
        self._last_fast_t: float | None = None
        self._last_slow_t: float | None = None
        self._last_music_t: float | None = None
        self._last_state = State(t=0.0, arousal=0.5, confidence=0.0)
        self._last_target = 0.5
        self._last_reason = "not_started"
        self._open_loop_target: float | None = None
        self._output_path: Path | None = None
        self._baseline_value_count_at_start = baseline.accumulated_value_count

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
    ) -> tuple[float, str]:
        if self.is_calibration:
            self.grammar.cancel_pending()
            return 0.0, "calibration_open_loop"
        if self.arm is Arm.SHAM and not self.is_calibration:
            self.grammar.cancel_pending()
            return 0.0, "sham_pre_registered"
        if ArmAssigner.is_open_loop_iso(self.arm):
            previous = (
                target if self._open_loop_target is None else self._open_loop_target
            )
            self._open_loop_target = target
            return target - previous, "open_loop_iso_trajectory"
        if state.confidence < self.cfg.state.min_confidence:
            self.grammar.cancel_pending()
            return 0.0, "open_loop_low_confidence"
        return self.controller.step(target, state, dt)

    def _process_features(
        self,
        feats,
        state: State,
        dt: float,
        *,
        bar_boundary: bool,
        phrase_boundary: bool,
    ) -> tuple[State, MusicParams]:
        if not self._anchored and state.confidence > 0:
            self.planner.set_anchor(state.arousal)
            self._anchored = True

        target = self.planner.target(feats.t)
        control, reason = self._select_control(target, state, dt)
        self.grammar.request(control, feats.t)
        params, changed = self.grammar.commit(
            feats.t,
            bar_boundary=bar_boundary,
            phrase_boundary=phrase_boundary,
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
            )
        )
        self._last_state = state
        self._last_target = target
        self._last_reason = reason
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
        step = self.cfg.signal.eda_step_s if dt is None else dt
        if not math.isfinite(step) or step <= 0:
            raise ValueError("dt must be finite and > 0")
        feats = extract_eda(window, self.cfg.signal)
        self._accept_observation(window.t)
        self._last_fast_t = window.t
        state = self.estimator.update(
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

        state = self.estimator.update(
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
        params, changed = self.grammar.commit(
            t,
            bar_boundary=True,
            phrase_boundary=phrase_boundary,
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
        elif changed and self._started:
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
                )
            )
        self._last_music_t = t
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
            if self._started:
                self.engine.stop()
            self.grammar.cancel_pending()
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
        if self._started:
            self.engine.stop()

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
        if self._started:
            self.engine.stop()
        self.grammar.cancel_pending()
        self.program.stopped_reason = reason
        self.status = SessionStatus.ABORTED
        self._output_path = self.recorder.flush()
        return str(self._output_path)
