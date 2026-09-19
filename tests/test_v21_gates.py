"""v2.1 authority, recovery and isolation tests; all inputs are synthetic."""

from __future__ import annotations

import math
import tempfile
import unittest
from dataclasses import asdict, replace

from mdt_core.agents.l3_gain import GainParams
from mdt_core.arbiter import ArbiterState, resolve
from mdt_core.config import DEFAULT, LearningConfig
from mdt_core.l2_planner import TrajectoryPlanner
from mdt_core.l3_control import MusicGrammar
from mdt_core.l4_l6 import SessionRecorder
from mdt_core.learning import LearningRuntime
from mdt_core.policy import context_hash
from mdt_core.types import Arm, Features, MusicParams, PolicyDecision, State, Strategy
from tests.synthetic import ready_baseline
from tests.test_learning_runtime import VERSION, FakePolicy


def config(**changes):
    return replace(DEFAULT, learning=LearningConfig(
        mode="autonomous", trial_id="synthetic-v21",
        policy_versions=tuple((m, VERSION) for m in ("l1", "l2", "l3", "taste")),
        v21_gates=True, state_fusion="select", **changes,
    ))


class V21ArbiterTests(unittest.TestCase):
    def setUp(self):
        self.cfg = config().learning
        self.base = PolicyDecision(-0.6, 0, 1, True, VERSION, "ctx")
        self.agent = replace(self.base, action=0.6)

    def gate(self, q=1.0, previous=None, now=0.0, **kw):
        previous = ArbiterState() if previous is None else previous
        return resolve(self.agent, self.base, q, self.cfg, previous,
                       now_s=now, action_scale=2.0, **kw)

    def test_profile_is_opt_in_and_configuration_is_validated(self):
        self.assertFalse(DEFAULT.learning.v21_gates)
        self.assertEqual(DEFAULT.learning.state_fusion, "blend")
        for change in ({"module_cap": 1.1}, {"domain_cap": float("nan")},
                       {"session_cap": -0.1}, {"ramp_up_per_s": 0.0},
                       {"distance_epsilon": 0.0}, {"state_fusion": "average"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(self.cfg, **change)
        with self.assertRaises(ValueError):
            LearningConfig(state_fusion="select")

    def test_ramp_has_no_first_event_credit_and_immediate_downweight(self):
        first, trace = self.gate(now=100)
        self.assertEqual(first, self.base.action)
        self.assertEqual(trace.lambda_mix, 0)
        _, trace = self.gate(previous=trace.state, now=102)
        self.assertAlmostEqual(trace.lambda_mix, 0.2)
        _, trace = self.gate(q=0.4, previous=trace.state, now=103)
        self.assertAlmostEqual(trace.lambda_mix, 0.1)
        _, trace = self.gate(q=0.2, previous=trace.state, now=104)
        self.assertEqual(trace.lambda_mix, 0)
        _, trace = self.gate(q=0.45, previous=trace.state, now=105)
        self.assertEqual(trace.decision, "reliability_hysteresis")
        _, trace = self.gate(q=1, previous=trace.state, now=1000)
        self.assertEqual(trace.lambda_mix, 0, "HOLD time cannot create ramp credit")

    def test_caps_take_minimum_and_zero_disagreement_is_finite(self):
        cfg = replace(self.cfg, module_cap=0.8, domain_cap=0.2, session_cap=0.6)
        _, trace = resolve(self.agent, self.base, 1, cfg, now_s=0, ramp=False)
        self.assertEqual(trace.lambda_mix, 0.2)
        cfg = replace(cfg, mode="restricted")
        output, trace = resolve(self.base, self.base, 1, cfg, now_s=0, ramp=False)
        self.assertEqual(output, self.base.action)
        self.assertEqual(trace.disagreement, 0)

    def test_control_distance_uses_frozen_two_unit_range(self):
        agent = replace(self.agent, confidence=0.6)
        cfg = replace(self.cfg, disagreement_limit=0.7)
        _, trace = resolve(agent, self.base, 1, cfg, now_s=0,
                           action_scale=2.0, ramp=False)
        self.assertAlmostEqual(trace.disagreement, 0.6)
        self.assertGreater(trace.lambda_mix, 0)
        _, trace = resolve(agent, self.base, 1, replace(cfg, v21_gates=False,
                           state_fusion="blend"))
        self.assertEqual(trace.decision, "policy_disagreement")

    def test_restricted_residual_and_binary_selection_are_distinct(self):
        cfg = replace(self.cfg, mode="restricted", restricted_delta=0.1)
        output, trace = resolve(self.agent, self.base, 1, cfg, now_s=0,
                               action_scale=2.0, ramp=False)
        self.assertAlmostEqual(abs(output - self.base.action) / 2.0, 0.1)
        output, trace = resolve(self.agent, self.base, 1, cfg, now_s=0,
                               action_scale=2.0, select_branch=True)
        self.assertEqual(output, self.base.action)
        self.assertEqual(trace.decision, "branch_restricted_distance")
        output, trace = resolve(self.agent, self.base, 0.6, self.cfg, now_s=0,
                               action_scale=2.0, select_branch=True)
        self.assertEqual((output, trace.lambda_mix), (self.agent.action, 1.0))
        for field in ("module_cap", "domain_cap", "session_cap"):
            output, trace = resolve(self.agent, self.base, 1,
                                   replace(self.cfg, **{field: 0.99}), now_s=0,
                                   select_branch=True)
            self.assertEqual(output, self.base.action)
            self.assertEqual(trace.decision, "branch_permission_cap")

    def test_invalid_or_rewound_time_fails_closed_and_cannot_reset_clock(self):
        _, valid = self.gate(now=10)
        for now in (float("nan"), float("inf"), -1, None):
            output, trace = self.gate(now=now)
            self.assertEqual(output, self.base.action)
            self.assertEqual(trace.decision, "invalid_gate_time")
        _, rejected = self.gate(previous=valid.state, now=9)
        self.assertEqual(rejected.decision, "nonmonotonic_gate_time")
        self.assertEqual(rejected.state.last_time_s, 10)
        _, still_rejected = self.gate(previous=rejected.state, now=9.5)
        self.assertEqual(still_rejected.decision, "nonmonotonic_gate_time")


class StubEstimator:
    def __init__(self, state):
        self.state = state
        self.last_emission_status = "learned"
        self.calls = 0

    def update(self, feats, process_scale):
        self.calls += 1
        return self.state


class V21RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def runtime(self, cfg=None, policies=None):
        rec = SessionRecorder("synthetic-v21", "test", Arm.FULL_LOOP, self.tmp.name)
        return LearningRuntime(cfg or config(), ready_baseline(), rec, policies or {})

    def test_restricted_control_residual_uses_configured_full_range(self):
        for clamp in (0.2, 1.0, 2.0):
            with self.subTest(output_clamp=clamp):
                cfg = config()
                cfg = replace(
                    cfg,
                    control=replace(cfg.control, output_clamp=clamp),
                    learning=replace(cfg.learning, mode="restricted"),
                )
                runtime = self.runtime(cfg)
                for t in (0.0, 10.0):
                    ctx = {"time_seconds": t}
                    decision = PolicyDecision(
                        clamp, 0.0, 1.0, True, VERSION, context_hash(ctx)
                    )
                    output, trace = runtime._gate(
                        "l3", decision, -clamp, clamp, 1.0, ctx,
                        calibrated=True, expected_version=VERSION,
                    )
                    if t == 0:
                        self.assertEqual(output, -clamp)
                self.assertAlmostEqual(trace.disagreement, 1.0)
                self.assertAlmostEqual(
                    abs(output + clamp) / (2.0 * clamp),
                    cfg.learning.restricted_delta,
                )
                self.assertAlmostEqual(output, -0.8 * clamp)

    def test_control_disagreement_uses_configured_full_range(self):
        cfg = config()
        cfg = replace(cfg, control=replace(cfg.control, output_clamp=0.2))
        runtime = self.runtime(cfg)
        ctx = {"time_seconds": 0.0}
        decision = PolicyDecision(
            0.2, 0.0, 0.6, True, VERSION, context_hash(ctx)
        )
        output, trace = runtime._gate(
            "l3", decision, -0.2, 0.2, 1.0, ctx,
            calibrated=True, expected_version=VERSION,
        )
        self.assertEqual(output, -0.2)
        self.assertEqual(trace.decision, "policy_disagreement")

    def test_selected_state_keeps_exact_branch_variance_and_confidence(self):
        runtime = self.runtime()
        baseline = State(0, 0.4, 0.8, {"baseline": 1}, 0.2)
        learned = State(0, 0.7, 0.9, {"learned": 2}, 0.1)
        runtime.emission_estimator = StubEstimator(learned)
        runtime.recorder.policy_manifest["versions"]["l1"] = VERSION
        selected = runtime.estimate(Features(0), 1, baseline, {"time_seconds": 0})
        self.assertIs(selected, learned)
        self.assertEqual(selected.uncertainty, 0.1)
        self.assertEqual(selected.confidence, 0.9)
        runtime.cfg = replace(runtime.cfg, learning=replace(runtime.cfg.learning,
                                                          session_cap=0.5))
        self.assertIs(runtime.estimate(Features(1), 1, baseline,
                                      {"time_seconds": 1}), baseline)

    def test_exception_quarantine_persists_across_suspend_and_stops_inference(self):
        runtime = self.runtime(config(enable_l3=True),
                               {"l3": FakePolicy(asdict(GainParams()), mode="raise")})
        state = State(0, 0.7, 1.0, uncertainty=0.1)
        for t in (0, 1):
            if t:
                runtime.suspend()
            self.assertEqual(runtime.control(-0.1, 0.4, state, 1,
                                             {"time_seconds": t}, 1), -0.1)
        self.assertIn("l3", runtime.quarantined)
        self.assertEqual(runtime.runners["l3"].policy.calls, 1)
        self.assertEqual(runtime.recorder.policy_decisions[-1]["arbiter_decision"],
                         "module_quarantined")

    def test_emission_exception_stops_updating_its_branch(self):
        runtime = self.runtime()
        baseline = State(0, 0.5, 1, uncertainty=0.1)
        estimator = StubEstimator(State(0, 0.8, 1, uncertainty=0.1))
        estimator.last_emission_status = "fallback:exception"
        runtime.emission_estimator = estimator
        runtime.recorder.policy_manifest["versions"]["l1"] = VERSION
        for t in (0, 1):
            self.assertIs(runtime.estimate(Features(t), 1, baseline,
                                          {"time_seconds": t}), baseline)
        self.assertEqual(estimator.calls, 1)
        self.assertIn("l1", runtime.quarantined)

    def test_transient_ood_can_recover_only_through_hysteresis_and_ramp(self):
        runtime = self.runtime(config(enable_l3=True), {"l3": FakePolicy(
            asdict(GainParams()), in_distribution=False)})
        state = State(0, 0.7, 1, uncertainty=0.1)
        runtime.cfg = replace(runtime.cfg, control=replace(
            runtime.cfg.control, formulation="normalized"))
        runtime.gain_controller.cfg = runtime.cfg.control
        runtime.gain_controller._integral = 0.5
        def step(t, q):
            return runtime.control(-0.1, 0.4, state, 1, {"time_seconds": t}, q)
        step(0, 1)
        self.assertAlmostEqual(runtime.gain_controller._integral, 0.5 * math.exp(-1 / 60))
        self.assertNotIn("l3", runtime.quarantined)
        runtime.runners["l3"].policy.in_distribution = True
        step(1, 0.45)
        self.assertEqual(runtime.states["l3"].lambda_mix, 0)
        before_recovery = runtime.gain_controller._integral
        step(100, 1)
        self.assertEqual(runtime.states["l3"].lambda_mix, 0)
        self.assertAlmostEqual(runtime.gain_controller._integral,
                               before_recovery * math.exp(-1 / 60))
        step(101, 1)
        self.assertAlmostEqual(runtime.states["l3"].lambda_mix, 0.1)

    def test_one_shot_trajectory_is_not_silenced_by_continuous_ramp(self):
        action = {"anchor_offset": 0.05, "match_seconds": 120,
                  "descent_seconds": 600, "floor": 0.2, "speed_gain": 1.2}
        runtime = self.runtime(config(enable_l2=True), {"l2": FakePolicy(action)})
        planner = TrajectoryPlanner(Strategy.ISO, DEFAULT.planner)
        runtime.plan(planner, {"time_seconds": 0}, 1)
        runtime.plan(planner, {"time_seconds": 1}, 1)
        self.assertEqual(runtime.runners["l2"].policy.calls, 1)
        assert planner.params is not None
        self.assertEqual(planner.params.match_seconds, 120)

    def test_taste_returns_selected_target_and_retains_legacy_request(self):
        action = MusicParams(tempo=200, dynamics=1).as_dict()
        runtime = self.runtime(config(enable_taste=True), {"taste": FakePolicy(action)})
        grammar = MusicGrammar(DEFAULT.grammar)
        first = runtime.taste(grammar, 0.5, 0, {"time_seconds": 0}, 1)
        self.assertIsInstance(first, MusicParams)
        self.assertIsNone(grammar._pending_params)
        selected = runtime.taste(grammar, 0.5, 10, {"time_seconds": 10}, 1)
        self.assertEqual(selected, grammar._pending_params)
        self.assertEqual(selected.tempo, 85)
        self.assertEqual(grammar.current.tempo, 72, "target is not execution")


if __name__ == "__main__":
    unittest.main()
