"""Session-local learning orchestration; every proposal passes frozen gates.

This module never holds an engine reference. The session alone owns execution,
and the existing grammar alone owns boundary commits and pending cancellation.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import asdict, fields, replace
from typing import Any

from .agents.l3_gain import GainParams
from .arbiter import ArbiterState, ArbiterTrace, resolve
from .config import Config
from .l1_state import ArousalEstimator, IndividualBaseline
from .l2_planner import TrajectoryParams, TrajectoryPlanner
from .l3_control import MusicGrammar, PIController
from .l4_l6 import SessionRecorder
from .policy import (
    BaselinePolicy,
    Context,
    Policy,
    PolicyRunner,
    context_hash,
    finite_scalar,
    stable_hash,
)
from .types import Features, MusicParams, PolicyDecision, State

Approval = Callable[[str, PolicyDecision], bool]


class LearningRuntime:
    def __init__(
        self,
        cfg: Config,
        baseline: IndividualBaseline,
        recorder: SessionRecorder,
        policies: Mapping[str, Policy],
        *,
        emission_model: Any = None,
        approval: Approval | None = None,
    ):
        self.cfg = cfg
        self.recorder = recorder
        self.approval = approval
        pins = dict(cfg.learning.policy_versions)
        self.runners = {
            module: PolicyRunner(
                policy, cfg.learning.inference_timeout_ms, pins.get(module)
            )
            for module, policy in policies.items()
            if module in {"l2", "l3", "taste"}
            and getattr(cfg.learning, f"enable_{module}")
        }
        self.states: dict[str, ArbiterState] = {}
        self.quarantined: dict[str, str] = {}
        self.gain_controller = PIController(cfg.control)
        self.l2_decided = False
        self.last_metadata: dict = {}
        self.emission_estimator: ArousalEstimator | None = None
        self.emission_model: Any = None
        if cfg.learning.enable_l1 and emission_model is not None:
            self.emission_model = copy.deepcopy(emission_model)
            self.emission_estimator = ArousalEstimator(
                baseline,
                cfg.state,
                emission_model=self.emission_model,
                emission_timeout_s=cfg.learning.inference_timeout_ms / 1000,
                expected_emission_hash=pins.get("l1"),
            )
        versions = {key: runner.version for key, runner in self.runners.items()}
        if self.emission_model is not None:
            versions["l1"] = pins.get("l1", self.emission_model.model_hash)
        self.recorder.policy_manifest = {
            "trial_id": cfg.learning.trial_id,
            "mode": cfg.learning.mode,
            "v21_gates": cfg.learning.v21_gates,
            "state_fusion": cfg.learning.state_fusion,
            "versions": versions,
            "probability_semantics": "proposal probability before gate/projection; not executed propensity",
        }
        if self.emission_model is not None:
            model = self.emission_model
            self.recorder.policy_manifest["emission_validation"] = {
                "baseline_hash": getattr(model, "baseline_hash", None),
                "filter_config_hash": getattr(model, "filter_config_hash", None),
                "diagnostics": asdict(model.diagnostics)
                if hasattr(model, "diagnostics")
                else None,
                "process_scale_range": getattr(model, "process_scale_range", None),
            }

    def _quarantine_reason(self, reason: str | None) -> bool:
        """Transient OOD/quality do not revoke a module for the entire session.

        An unvalidated emission history is different: the filter's calibrated
        recursion cannot resume after that history without a new session.
        """
        if not self.cfg.learning.v21_gates or not reason:
            return False
        if reason in {
            "out_of_distribution", "unreliable_state", "reliability_hysteresis",
            "low_policy_confidence", "policy_disagreement", "fallback:missing_or_ood",
            "disabled", "shadow", "awaiting_approval", "branch_permission_cap",
            "branch_restricted_distance", "invalid_gate_time", "nonmonotonic_gate_time",
        }:
            return False
        return reason not in {"blended", "agent", "agent_selected"}

    def _propose(self, module: str, ctx: Context) -> PolicyDecision:
        runner = self.runners[module]
        if self.cfg.learning.v21_gates and module in self.quarantined:
            return PolicyDecision(
                0.0, 0.0, 0.0, False, runner.version, context_hash(ctx),
                "module_quarantined",
            )
        return runner.propose(ctx)

    def _gate(
        self,
        module: str,
        decision: PolicyDecision,
        baseline_action: float,
        candidate_action: float,
        q: float,
        ctx: Context,
        *,
        calibrated: bool,
        expected_version: str,
    ) -> tuple[float, ArbiterTrace]:
        approved = False
        if (
            self.cfg.learning.mode == "suggest"
            and self.approval is not None
            and not decision.error
        ):
            try:
                approved = self.approval(module, copy.deepcopy(decision)) is True
            except Exception:  # noqa: BLE001 - approval adapter errors must fail closed
                approved = False
        baseline = PolicyDecision(
            baseline_action, 0.0, 1.0, True, BaselinePolicy.version, context_hash(ctx)
        )
        scalar = replace(decision, action=candidate_action)
        if self.cfg.learning.v21_gates and module in self.quarantined:
            scalar = replace(scalar, error="module_quarantined")
        output, trace = resolve(
            scalar,
            baseline,
            q,
            self.cfg.learning,
            self.states.get(module, ArbiterState()),
            expected_version=expected_version,
            calibrated=calibrated,
            approved=approved,
            now_s=ctx.get("time_seconds"),
            action_scale=(
                2.0 * self.cfg.control.output_clamp if module == "l3" else 1.0
            ),
            select_branch=module == "l1" and self.cfg.learning.state_fusion == "select",
            # A3 is a one-shot parameter decision, not a time-varying weight.
            ramp=module != "l2",
        )
        if self._quarantine_reason(trace.decision):
            self.quarantined.setdefault(module, trace.decision)
        self.states[module] = trace.state
        self.recorder.log_policy(
            {
                "t": ctx["time_seconds"],
                "module": module,
                "context": copy.deepcopy(dict(ctx)),
                **{
                    field.name: getattr(decision, field.name)
                    for field in fields(decision)
                },
                "ood_flag": not decision.in_distribution,
                "arbiter_decision": trace.decision,
                "lambda_mix": trace.lambda_mix,
                "baseline_action": baseline_action,
                "candidate_control": candidate_action,
                "selected_control": output,
                "disagreement": trace.disagreement,
                "approved": approved,
                "calibrated": calibrated,
                "mode": self.cfg.learning.mode,
                "quarantine_reason": self.quarantined.get(module),
                "normalized_distance": self.cfg.learning.v21_gates,
            }
        )
        self.last_metadata = {
            "policy_version": decision.policy_version,
            "action_logprob": decision.logprob if decision.error is None else None,
            "context_hash": decision.context_hash,
            "ood_flag": not decision.in_distribution,
            "arbiter_decision": trace.decision,
            "lambda_mix": trace.lambda_mix,
        }
        return output, trace

    def estimate(
        self, feats: Features, process_scale: float, baseline_state: State, ctx: Context
    ) -> State:
        if self.emission_estimator is None:
            return baseline_state
        estimator = self.emission_estimator
        if self.cfg.learning.v21_gates and "l1" in self.quarantined:
            proposed = baseline_state
            status = "module_quarantined"
        else:
            proposed = estimator.update(feats, process_scale)
            status = estimator.last_emission_status
        error = None if status == "learned" else status
        version = self.recorder.policy_manifest["versions"]["l1"]
        decision = PolicyDecision(
            proposed.arousal,
            0.0,
            1.0,
            status == "learned",
            version,
            context_hash(ctx),
            error,
        )
        q = self.gain_controller.reliability(baseline_state)
        if self.cfg.learning.v21_gates:
            q = min(q, self.gain_controller.reliability(proposed))
        arousal, trace = self._gate(
            "l1",
            decision,
            baseline_state.arousal,
            proposed.arousal,
            q,
            ctx,
            calibrated=status == "learned",
            expected_version=version,
        )
        mix = trace.lambda_mix
        if mix == 0:
            return baseline_state
        if self.cfg.learning.state_fusion == "select":
            # Preserve the selected branch's calibrated variance/confidence;
            # the legacy conservative fusion belongs only to the blend arm.
            return proposed
        # Mixing means must not create fictitious certainty. Conservatively
        # retain the larger variance and add the between-estimator spread.
        variance = max(baseline_state.uncertainty, proposed.uncertainty)
        variance += mix * (1 - mix) * (proposed.arousal - baseline_state.arousal) ** 2
        return replace(
            proposed,
            arousal=arousal,
            uncertainty=variance,
            confidence=min(proposed.confidence, baseline_state.confidence),
        )

    def plan(self, planner: TrajectoryPlanner, ctx: Context, q: float) -> None:
        if self.l2_decided or "l2" not in self.runners:
            return
        self.l2_decided = True
        runner = self.runners["l2"]
        decision = self._propose("l2", ctx)
        proposal = None
        base = {
            "anchor_offset": 0.0,
            "match_seconds": self.cfg.planner.iso_match_duration_s,
            "descent_seconds": self.cfg.planner.descent_duration_s,
            "floor": self.cfg.planner.floor_arousal,
            "speed_gain": 1.0,
        }
        widths = {
            "anchor_offset": 0.15,
            "match_seconds": 480.0,
            "descent_seconds": 1200.0,
            "floor": 0.35,
            "speed_gain": 1.0,
        }
        distance = 0.0
        try:
            proposal = TrajectoryParams.from_mapping(decision.action)
            values = proposal.as_dict()
            # Custom legacy configuration outside the learned action family is
            # left untouched; a blend cannot silently violate either contract.
            TrajectoryParams.from_mapping(base)
            distance = max(abs(values[k] - base[k]) / widths[k] for k in base)
        except (ValueError, TypeError, KeyError, AttributeError):
            decision = replace(decision, error=decision.error or "invalid_trajectory")
        _, trace = self._gate(
            "l2",
            decision,
            0.0,
            distance,
            q,
            ctx,
            calibrated=runner.calibrated,
            expected_version=runner.version,
        )
        if trace.lambda_mix > 0 and proposal is not None:
            values = proposal.as_dict()
            selected = {
                k: base[k] + trace.lambda_mix * (values[k] - base[k]) for k in base
            }
            planner.set_params(TrajectoryParams.from_mapping(selected))
        else:
            selected = base
        self.recorder.policy_decisions[-1]["selected_action"] = selected

    def control(
        self,
        baseline_output: float,
        target: float,
        state: State,
        dt: float,
        ctx: Context,
        q: float,
    ) -> float:
        if "l3" not in self.runners:
            return baseline_output
        previous_controller = (
            copy.deepcopy(self.gain_controller) if self.cfg.learning.v21_gates else None
        )
        runner = self.runners["l3"]
        decision = self._propose("l3", ctx)
        proposed = baseline_output
        try:
            gains = GainParams.from_mapping(decision.action)
            if decision.error is None and (
                not self.cfg.learning.v21_gates
                or (decision.in_distribution and runner.calibrated
                    and finite_scalar(q) and q >= self.cfg.learning.reliability_exit)
            ):
                self.gain_controller.cfg = gains.control_config(self.cfg.control)
                proposed, _ = self.gain_controller.step(target, state, dt)
        except (ValueError, TypeError, KeyError, AttributeError):
            decision = replace(decision, error=decision.error or "invalid_gains")
        output, trace = self._gate(
            "l3",
            decision,
            baseline_output,
            proposed,
            q,
            ctx,
            calibrated=runner.calibrated,
            expected_version=runner.version,
        )
        if trace.lambda_mix == 0:
            if previous_controller is not None:
                # A rejected or zero-authority preview must not integrate error.
                self.gain_controller = previous_controller
                self.gain_controller.suspend(dt)
            else:
                self.gain_controller.suspend()
        return max(
            -self.cfg.control.output_clamp, min(self.cfg.control.output_clamp, output)
        )

    def taste(
        self, grammar: MusicGrammar, control: float, t: float, ctx: Context, q: float
    ) -> MusicParams:
        """Return the selected absolute candidate and retain legacy queuing.

        A v2.1 session can send the returned candidate to its sole execution
        gateway. This value is a target, never an assertion of engine ACK.
        """
        from .l35_mapping import LAYER_LEVELS, layer_level, map_control

        base = map_control(control, grammar.current, self.cfg.grammar)
        if "taste" not in self.runners or abs(control) < 1e-12:
            return base
        taste_ctx = dict(ctx, current_params=grammar.current.as_dict(), control=control)
        runner = self.runners["taste"]
        decision = self._propose("taste", taste_ctx)
        distance = 0.0
        candidate = base.as_dict()
        try:
            if not isinstance(decision.action, dict) or set(decision.action) != set(
                candidate
            ):
                raise ValueError("invalid music action schema")
            candidate = {k: float(v) for k, v in decision.action.items()}
            widths = {k: 1.0 for k in candidate}
            widths["tempo"] = (
                self.cfg.grammar.tempo_range[1] - self.cfg.grammar.tempo_range[0]
            )
            widths["layer_mask"] = 15.0
            distance = max(
                abs(candidate[k] - base.as_dict()[k]) / widths[k] for k in candidate
            )
        except (ValueError, TypeError, KeyError):
            decision = replace(decision, error=decision.error or "invalid_music_action")
        _, trace = self._gate(
            "taste",
            decision,
            0.0,
            distance,
            q,
            taste_ctx,
            calibrated=runner.calibrated,
            expected_version=runner.version,
        )
        if trace.lambda_mix > 0:
            selected = {
                k: base.as_dict()[k] + trace.lambda_mix * (v - base.as_dict()[k])
                for k, v in candidate.items()
            }
            base_level = layer_level(base.layer_mask)
            candidate_level = layer_level(max(1, min(15, int(candidate["layer_mask"]))))
            selected["layer_mask"] = LAYER_LEVELS[
                round(base_level + trace.lambda_mix * (candidate_level - base_level))
            ]
            grammar.request_params(selected, t)
            # Match request_params' static normalization without prematurely
            # spending a boundary/rate budget or reporting the target executed.
            lo, hi = self.cfg.grammar.tempo_range
            selected["tempo"] = max(lo, min(hi, selected["tempo"]))
            for key, value in selected.items():
                if key not in {"tempo", "layer_mask"}:
                    selected[key] = max(0.0, min(1.0, value))
            return MusicParams(**selected)
        return base

    def suspend(self, dt: float | None = None) -> None:
        self.gain_controller.suspend(dt)
        self.states.clear()
        self.last_metadata = {
            "arbiter_decision": "learning_suspended",
            "lambda_mix": 0.0,
        }


def make_context(
    user_id: str,
    baseline: IndividualBaseline,
    state: State,
    cfg: Config,
    *,
    completed_sessions: int,
    previous_outcome: float | None,
    extra: Mapping | None = None,
) -> dict:
    ctx = dict(extra or {})
    ctx.update(
        {
            "subject_id": stable_hash(
                {"subject": user_id, "trial": cfg.learning.trial_id}
            ),
            "baseline_mu": dict(baseline.mu),
            "baseline_sigma": dict(baseline.sigma),
            "baseline_sessions": baseline.sessions_collected,
            "initial_arousal": state.arousal,
            "arousal": state.arousal,
            "uncertainty": state.uncertainty,
            "confidence": state.confidence,
            "time_seconds": state.t,
            "completed_sessions": completed_sessions,
            "previous_outcome": previous_outcome,
        }
    )
    return ctx
