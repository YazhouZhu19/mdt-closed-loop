"""Adversarial gate and session tests with synthetic local policies only."""

from __future__ import annotations

import json
import math
import tempfile
import time
import unittest
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from unittest.mock import patch

from mdt_core.agents.l3_gain import GainExample, GainParams, GainPolicy
from mdt_core.arbiter import ArbiterState, resolve
from mdt_core.config import DEFAULT, LearningConfig
from mdt_core.engine import NullEngine
from mdt_core.l4_l6 import ProgramState, SafetyMonitor
from mdt_core.policy import PolicyRunner, context_hash, stable_hash
from mdt_core.session import Session
from mdt_core.trial import TrialPinError
from mdt_core.types import Arm, MusicParams, PolicyDecision, SessionStatus, State
from tests.learning_scenarios import replay
from tests.synthetic import ready_baseline, synthetic_window

VERSION = stable_hash({"synthetic-policy": 1})


@dataclass
class FakePolicy:
    action: object
    calibrated: bool = True
    confidence: float = 1.0
    in_distribution: bool = True
    delay: float = 0.0
    version: str = VERSION
    calls: int = 0
    mode: str = "normal"

    def propose(self, ctx):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.mode == "raise":
            raise RuntimeError("synthetic failure")
        return PolicyDecision(
            self.action,
            math.log(0.25),
            self.confidence,
            self.in_distribution,
            "bad-version" if self.mode == "version" else self.version,
            "bad-context" if self.mode == "context" else context_hash(ctx),
        )


class ExplodingPolicy:
    def __deepcopy__(self, memo):
        raise AssertionError("an isolated arm loaded a model")


def config(mode="autonomous", **kwargs):
    return replace(
        DEFAULT,
        learning=LearningConfig(
            mode=mode,
            trial_id="synthetic-period",
            policy_versions=(
                ("l1", VERSION),
                ("l2", VERSION),
                ("l3", VERSION),
                ("taste", VERSION),
            ),
            **kwargs,
        ),
    )


class ArbiterTests(unittest.TestCase):
    def setUp(self):
        self.cfg = config().learning
        self.base = PolicyDecision(0.1, 0, 1, True, VERSION, "ctx")
        self.agent = PolicyDecision(0.3, 0, 1, True, VERSION, "ctx")

    def resolve(self, **kwargs):
        return resolve(
            kwargs.pop("agent", self.agent),
            self.base,
            kwargs.pop("q", 1),
            self.cfg,
            **kwargs,
        )

    def test_all_four_fallback_criteria(self):
        cases = (
            (replace(self.agent, in_distribution=False), 1, "out_of_distribution"),
            (replace(self.agent, action=0.8, confidence=0.6), 1, "policy_disagreement"),
            (self.agent, 0.1, "unreliable_state"),
            (replace(self.agent, action=float("nan")), 1, "nonfinite_action"),
            (replace(self.agent, latency_ms=100), 1, "inference_timeout"),
            (replace(self.agent, policy_version="changed"), 1, "version_mismatch"),
            (replace(self.agent, context_hash="changed"), 1, "context_mismatch"),
        )
        for candidate, q, reason in cases:
            with self.subTest(reason=reason):
                output, trace = self.resolve(
                    agent=candidate, q=q, expected_version=VERSION
                )
                self.assertEqual(output, 0.1)
                self.assertEqual(trace.lambda_mix, 0)
                self.assertEqual(trace.decision, reason)

    def test_hysteresis_and_convex_blend(self):
        output, trace = self.resolve(q=0.49)
        self.assertEqual(trace.decision, "reliability_hysteresis")
        self.assertEqual(output, 0.1)
        _, entered = self.resolve(q=0.51)
        output, retained = self.resolve(q=0.49, previous=entered.state)
        self.assertTrue(retained.state.active)
        self.assertGreater(output, 0.1)
        self.assertLess(output, 0.3)
        _, exited = self.resolve(q=0.34, previous=retained.state)
        self.assertEqual(exited.state, ArbiterState(False))

    def test_modes_uncalibrated_and_restricted_neighborhood(self):
        for mode, approved in (
            ("shadow", True),
            ("suggest", False),
            ("disabled", True),
        ):
            output, _ = resolve(
                self.agent,
                self.base,
                1,
                replace(self.cfg, mode=mode),
                approved=approved,
            )
            self.assertEqual(output, 0.1)
        output, trace = self.resolve(calibrated=False)
        self.assertEqual(trace.decision, "uncalibrated_policy")
        output, _ = resolve(
            self.agent,
            self.base,
            1,
            replace(self.cfg, mode="restricted", restricted_delta=0.03),
        )
        self.assertLessEqual(abs(output - 0.1), 0.03000000000001)
        approved_output, _ = resolve(
            self.agent, self.base, 1, replace(self.cfg, mode="suggest"), approved=True
        )
        self.assertAlmostEqual(approved_output, 0.3)

    def test_configuration_rejects_unpinned_trial(self):
        with self.assertRaises(ValueError):
            LearningConfig(mode="autonomous", enable_l3=True, trial_id="period")
        with self.assertRaises(ValueError):
            LearningConfig(reliability_exit=0.7, reliability_enter=0.5)


class RunnerTests(unittest.TestCase):
    def test_exception_version_context_nonfinite(self):
        for mode in ("raise", "version", "context"):
            decision = PolicyRunner(FakePolicy(0.2, mode=mode), 100).propose({"x": 1})
            self.assertIsNotNone(decision.error)
        decision = PolicyRunner(FakePolicy(float("nan")), 100).propose({"x": 1})
        self.assertEqual(decision.error, "nonfinite_action")

    def test_real_timeout_quarantines_late_result_and_bounds_workers(self):
        runner = PolicyRunner(FakePolicy(0.2, delay=0.2), 10)
        started = time.monotonic()
        result = runner.propose({"x": 1})
        self.assertLess(time.monotonic() - started, 0.1)
        self.assertEqual(result.error, "inference_timeout")
        self.assertEqual(runner.propose({"x": 2}).error, "inference_quarantined")
        assert isinstance(runner.policy, FakePolicy)
        self.assertEqual(runner.policy.calls, 1)

    def test_session_model_snapshot_isolated_from_external_mutation(self):
        policy = FakePolicy(0.2)
        runner = PolicyRunner(policy, 100)
        policy.action = 0.8
        self.assertEqual(runner.propose({}).action, 0.2)


class LearningSessionTests(unittest.TestCase):
    def make_session(self, directory, policies, cfg=None, **kwargs):
        return Session(
            "isolated-synthetic",
            ready_baseline(),
            ProgramState(18),
            engine=NullEngine(),
            arm=Arm.FULL_LOOP,
            out_dir=directory,
            cfg=cfg or config(enable_l2=True, enable_l3=True, enable_taste=True),
            policies=policies,
            **kwargs,
        )

    def test_all_arms_match_pre_change_golden_trace(self):
        golden = json.loads(
            (Path(__file__).parent / "fixtures/legacy_trace.json").read_text()
        )
        for arm in Arm:
            with self.subTest(arm=arm):
                self.assertEqual(replay(arm), golden[arm.value])

    def test_disabled_and_control_arms_do_not_even_load_models(self):
        policies = {
            "l1": ExplodingPolicy(),
            "l2": ExplodingPolicy(),
            "l3": ExplodingPolicy(),
            "taste": ExplodingPolicy(),
        }
        for arm in Arm:
            self.assertEqual(replay(arm, policies=policies), replay(arm))
        enabled = config(
            enable_l1=True, enable_l2=True, enable_l3=True, enable_taste=True
        )
        for arm in (Arm.ISO, Arm.SHAM, Arm.DIRECT):
            self.assertEqual(replay(arm, cfg=enabled, policies=policies), replay(arm))

    def test_shadow_outputs_match_baseline(self):
        policy = FakePolicy(asdict(GainParams(kp=0.8, ki=0.09)))
        self.assertEqual(
            replay(
                Arm.FULL_LOOP,
                cfg=config("shadow", enable_l3=True),
                policies={"l3": policy},
            ),
            replay(Arm.FULL_LOOP),
        )

    def test_l2_once_logprob_and_pins_persist(self):
        action = {
            "anchor_offset": -0.1,
            "match_seconds": 120,
            "descent_seconds": 600,
            "floor": 0.3,
            "speed_gain": 1.5,
        }
        with tempfile.TemporaryDirectory() as directory:
            session = self.make_session(directory, {"l2": FakePolicy(action)})
            for t in (0, 15, 30):
                session.slow_tick(synthetic_window(t, arousal=0.8))
            runner = session._learning.runners["l2"]
            self.assertEqual(runner.policy.calls, 1)
            decision = session.recorder.policy_decisions[0]
            self.assertEqual(decision["logprob"], math.log(0.25))
            self.assertEqual(decision["policy_version"], VERSION)
            self.assertEqual(session.planner.params.match_seconds, 120)
            record = json.loads(Path(session.finish()).read_text())
            self.assertEqual(record["policy_manifest"]["trial_id"], "synthetic-period")
            self.assertIn("selected_action", record["policy_decisions"][0])

    def test_gain_proposal_cannot_change_hard_limits_and_waits_for_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            policy = FakePolicy(asdict(GainParams(kp=0.8, ki=0.09, deadband=0.02)))
            session = self.make_session(directory, {"l3": policy})
            session.slow_tick(synthetic_window(0, arousal=0.8))
            before = len(session.engine.history)
            session.slow_tick(synthetic_window(1200, arousal=0.8))
            self.assertEqual(len(session.engine.history), before)
            self.assertNotEqual(session._last_control, 0)
            session.music_boundary(1201, phrase_boundary=True)
            self.assertGreater(len(session.engine.history), before)
            self.assertLessEqual(
                abs(session.recorder.music[-1]["control_output"]),
                DEFAULT.control.output_clamp,
            )
            self.assertEqual(
                session._learning.gain_controller.cfg.output_clamp,
                DEFAULT.control.output_clamp,
            )

    def test_uncalibrated_bad_gain_and_ood_fall_back(self):
        for policy in (
            FakePolicy({"kp": 1e6}),
            FakePolicy(asdict(GainParams()), calibrated=False),
            FakePolicy(asdict(GainParams()), in_distribution=False),
        ):
            with tempfile.TemporaryDirectory() as directory:
                session = self.make_session(directory, {"l3": policy})
                for t in (0, 15):
                    session.slow_tick(synthetic_window(t, arousal=0.8))
                self.assertEqual(session.recorder.policy_decisions[-1]["lambda_mix"], 0)

    def test_taste_guard_and_loss_cancel_pending(self):
        action = MusicParams(tempo=200, layer_mask=15, dynamics=1).as_dict()
        with tempfile.TemporaryDirectory() as directory:
            session = self.make_session(directory, {"taste": FakePolicy(action)})
            session.slow_tick(synthetic_window(0, arousal=0.8))
            session.slow_tick(synthetic_window(300, arousal=0.8))
            session.slow_tick(synthetic_window(1200, arousal=0.8))
            self.assertEqual(session.recorder.policy_decisions[-1]["module"], "taste")
            self.assertGreater(session.recorder.policy_decisions[-1]["lambda_mix"], 0)
            params = session.music_boundary(1201, phrase_boundary=True)
            self.assertLessEqual(params.tempo, 85)
            self.assertEqual(params.dynamics, 1)
            self.assertLessEqual(params.tempo - 72, 2)
            session.slow_tick(synthetic_window(1215, arousal=0.8))
            self.assertIsNotNone(session.grammar._pending_params)
            window = synthetic_window(1230)
            window.contact_impedance = 9e9
            window.rr_intervals = []
            applied_count = len(session.engine.history)
            _, held = session.slow_tick(
                window, bar_boundary=True, phrase_boundary=True
            )
            self.assertEqual(held, params)
            self.assertEqual(len(session.engine.history), applied_count)
            self.assertEqual(
                session.music_boundary(1231, phrase_boundary=True), params
            )
            self.assertEqual(len(session.engine.history), applied_count)
            self.assertEqual(session._last_control, 0)
            self.assertIsNone(session.grammar._pending_params)
            self.assertEqual(session.recorder.music[-1]["lambda_mix"], 0)
            self.assertEqual(
                session.recorder.music[-1]["arbiter_decision"], "learning_suspended"
            )

    def test_uncertain_state_holds_timbre_but_reliable_deadband_restores_it(self):
        action = MusicParams(tempo=200, layer_mask=15, dynamics=1).as_dict()
        with tempfile.TemporaryDirectory() as directory:
            session = self.make_session(directory, {"taste": FakePolicy(action)})
            for t in (0, 300, 1200):
                session.slow_tick(synthetic_window(t, arousal=0.8))
            applied = session.music_boundary(1201, phrase_boundary=True)
            self.assertEqual(applied.dynamics, 1)
            session.slow_tick(synthetic_window(1215, arousal=0.8))
            self.assertIsNotNone(session.grammar._pending_params)
            applied_count = len(session.engine.history)
            uncertain = State(
                t=1230, arousal=0.8, confidence=1.0,
                uncertainty=DEFAULT.control.uncertainty_hard_limit,
            )
            with patch.object(session.estimator, "update", return_value=uncertain):
                _, held = session.slow_tick(
                    synthetic_window(1230), bar_boundary=True, phrase_boundary=True
                )
            self.assertEqual(session._last_reason, "open_loop_high_uncertainty")
            self.assertEqual(session._last_control_scale, 0)
            self.assertEqual(held, applied)
            self.assertEqual(
                session.music_boundary(1231, phrase_boundary=True), applied
            )
            self.assertEqual(len(session.engine.history), applied_count)

            reliable = State(t=1245, arousal=0.5, confidence=1.0, uncertainty=0.1)
            with (
                patch.object(session.estimator, "update", return_value=reliable),
                patch.object(session.planner, "adaptive_target", return_value=0.5),
            ):
                _, held = session.slow_tick(synthetic_window(1245))
            self.assertEqual(session._last_reason, "deadband")
            self.assertEqual(session._last_control, 0)
            self.assertGreater(session._last_control_scale, 0)
            self.assertEqual(held, applied)
            self.assertEqual(len(session.engine.history), applied_count)
            restored = session.music_boundary(1246, phrase_boundary=True)
            self.assertNotEqual(restored.dynamics, applied.dynamics)
            self.assertEqual(len(session.engine.history), applied_count + 1)

    def test_changed_trial_pin_rejected_before_engine_start(self):
        with tempfile.TemporaryDirectory() as directory:
            base = config(enable_l3=True)
            self.make_session(
                directory, {"l3": FakePolicy(asdict(GainParams()))}, cfg=base
            )
            changed = stable_hash({"synthetic-policy": 2})
            updated = replace(
                base,
                learning=replace(base.learning, policy_versions=(("l3", changed),)),
            )
            engine = NullEngine()
            with self.assertRaises(TrialPinError):
                Session(
                    "isolated-synthetic",
                    ready_baseline(),
                    ProgramState(18),
                    engine=engine,
                    arm=Arm.FULL_LOOP,
                    cfg=updated,
                    out_dir=directory,
                    policies={"l3": FakePolicy(asdict(GainParams()), version=changed)},
                )
            self.assertFalse(engine.started)

    def test_safety_abort_overrides_learning_and_blocks_late_boundaries(self):
        escalations = []
        with tempfile.TemporaryDirectory() as directory:
            session = self.make_session(
                directory,
                {"l3": FakePolicy(asdict(GainParams()))},
                safety_monitor=SafetyMonitor(lambda *args: escalations.append(args)),
            )
            session.slow_tick(synthetic_window(0))
            session.submit_subjective(post={"note": "自伤"})
            self.assertEqual(session.status, SessionStatus.ABORTED)
            self.assertTrue(session.engine.stopped)
            self.assertEqual(session._last_control, 0)
            self.assertEqual(len(escalations), 1)
            with self.assertRaises(RuntimeError):
                session.music_boundary(1)


class GainTrainingTests(unittest.TestCase):
    def test_offline_fit_calibration_ood_and_content_version(self):
        def samples(prefix):
            return [
                GainExample(
                    {"initial_arousal": 0.2 + 0.6 * i / 49, "reliability": 0.8},
                    GainParams(),
                    f"{prefix}-{i}",
                )
                for i in range(50)
            ]

        model = GainPolicy.fit(
            samples("train"), calibration=samples("cal"), evaluation=samples("eval")
        )
        self.assertTrue(model.calibrated)
        self.assertEqual(len(model.version), 64)
        self.assertTrue(
            model.propose({"initial_arousal": 0.5, "reliability": 0.8}).in_distribution
        )
        self.assertFalse(
            model.propose({"initial_arousal": 20.0, "reliability": 0.8}).in_distribution
        )
        self.assertFalse(GainPolicy.fit(samples("train")).calibrated)
        with self.assertRaises(ValueError):
            GainPolicy.fit(samples("train"), calibration=samples("train"))
