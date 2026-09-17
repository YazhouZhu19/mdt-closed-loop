"""L2 learning checks use generated constants, never participant data."""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

from mdt_core.agents.l2_bandit import (
    HierarchicalTrajectoryBandit,
    TrajectoryObservation,
)
from mdt_core.config import DEFAULT, LearningConfig, PlannerConfig
from mdt_core.engine import NullEngine
from mdt_core.l2_planner import TrajectoryParams, TrajectoryPlanner
from mdt_core.l4_l6 import ProgramState
from mdt_core.session import Session
from mdt_core.types import Arm, State, Strategy
from tests.synthetic import ready_baseline, synthetic_window


class ParameterizedPlannerTests(unittest.TestCase):
    def test_unmodified_config_outside_learning_limits_remains_exact(self) -> None:
        cfg = PlannerConfig(
            iso_match_duration_s=20,
            descent_duration_s=100,
            floor_arousal=0.05,
            adaptive_max_speed=2.0,
        )
        planner = TrajectoryPlanner(Strategy.ISO, cfg)
        planner.set_anchor(0.8)
        self.assertEqual(planner.target(10), 0.8)
        self.assertEqual(planner.target(70), 0.8 + (0.05 - 0.8) * 0.5)
        self.assertEqual(planner.target(120), 0.05)
        planner.adaptive_target(20, State(20, 0.8, 1), 1)
        self.assertEqual(
            planner.adaptive_target(30, State(30, 0.8, 1), 1),
            0.8 + (0.05 - 0.8) * 0.2,
        )
        self.assertEqual(planner.speed_factor, 2.0)

    def test_default_parameters_reproduce_baseline_trajectory(self) -> None:
        original = TrajectoryPlanner(Strategy.ISO, DEFAULT.planner)
        learned = TrajectoryPlanner(Strategy.ISO, DEFAULT.planner, TrajectoryParams())
        for planner in (original, learned):
            planner.set_anchor(0.78)
        for t in (0, 120, 300, 600, 1200, 1800):
            self.assertEqual(original.target(t), learned.target(t))
        for t, arousal, reliability in (
            (0, 0.78, 1), (300, 0.78, 1), (600, 0.70, 0.8),
            (900, 0.50, 0), (1200, 0.40, 0.5), (1800, 0.25, 1),
        ):
            state = State(t, arousal, reliability)
            self.assertEqual(
                original.adaptive_target(t, state, reliability),
                learned.adaptive_target(t, state, reliability),
            )
            self.assertEqual(original.speed_factor, learned.speed_factor)

    def test_learned_parameters_change_curve_and_freeze_at_anchor(self) -> None:
        planner = TrajectoryPlanner(Strategy.ISO, DEFAULT.planner)
        params = TrajectoryParams(-0.1, 120, 600, 0.3, 0.5)
        planner.set_params(params)
        planner.set_anchor(0.8)
        self.assertAlmostEqual(planner.target(0), 0.7)
        self.assertAlmostEqual(planner.target(420), 0.5)
        self.assertEqual(planner.target(720), 0.3)
        with self.assertRaises(RuntimeError):
            planner.set_params(TrajectoryParams())
        with self.assertRaises(FrozenInstanceError):
            params.floor = 0.4  # type: ignore[misc]

    def test_learning_cannot_select_study_strategy(self) -> None:
        planner = TrajectoryPlanner(
            Strategy.DIRECT, DEFAULT.planner, TrajectoryParams(floor=0.4)
        )
        planner.set_anchor(0.8)
        self.assertEqual(planner.target(400), DEFAULT.planner.direct_target)
        action = TrajectoryParams().as_dict()
        action["strategy"] = "iso"  # type: ignore[assignment]
        with self.assertRaises(ValueError):
            TrajectoryParams.from_mapping(action)

    def test_unreliable_startup_does_not_prevent_first_anchor_selection(self) -> None:
        planner = TrajectoryPlanner(Strategy.ISO, DEFAULT.planner)
        planner.adaptive_target(0, State(0, 0.5, 0, uncertainty=1), 0)
        planner.adaptive_target(15, State(15, 0.5, 0, uncertainty=1), 0)
        planner.set_params(TrajectoryParams(anchor_offset=-0.1))
        planner.set_anchor(0.8)
        self.assertAlmostEqual(planner.target(30), 0.7)

    def test_parameter_bounds_and_nonfinite_values_rejected(self) -> None:
        invalid_values: tuple[dict[str, float], ...] = (
            {"anchor_offset": -0.101}, {"anchor_offset": 0.051},
            {"match_seconds": 119}, {"descent_seconds": 1801},
            {"floor": 0.451}, {"speed_gain": 1.501},
            {"speed_gain": float("nan")}, {"floor": True},
        )
        for kwargs in invalid_values:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                TrajectoryParams(**kwargs)
        with self.assertRaises(ValueError):
            TrajectoryParams.from_mapping({"floor": 0.2})

    def test_speed_gain_preserves_reliability_hold_and_speed_cap(self) -> None:
        planner = TrajectoryPlanner(
            Strategy.ISO, DEFAULT.planner, TrajectoryParams(speed_gain=1.5)
        )
        planner.set_anchor(0.8)
        planner.adaptive_target(300, State(300, 0.8, 1), 1)
        self.assertEqual(planner.adaptive_target(350, State(350, 0.8, 0), 0), 0.8)
        self.assertEqual(planner.speed_factor, 0)
        planner.adaptive_target(400, State(400, 0.8, 1), 1)
        self.assertLessEqual(planner.speed_factor, DEFAULT.planner.adaptive_max_speed)


class HierarchicalBanditTests(unittest.TestCase):
    actions = (TrajectoryParams(), TrajectoryParams(floor=0.3))

    @classmethod
    def rows(cls, prefix: str, count: int = 20) -> list[TrajectoryObservation]:
        return [
            TrajectoryObservation(
                {
                    "initial_arousal": 0.3 + (i % 10) * 0.05,
                    "subject_id": f"synthetic-{i % 4}",
                    "session_id": f"{prefix}-{i}",
                },
                action,
                0.2 if action == 0 else 0.8,
            )
            for i in range(count)
            for action in range(len(cls.actions))
        ]

    def test_unvalidated_fit_is_shadow_only_and_logpropensity_is_exact(self) -> None:
        model = HierarchicalTrajectoryBandit.fit(self.rows("train"), self.actions)
        context = {"initial_arousal": 0.5, "subject_id": "new-subject"}
        probabilities = model.probabilities(context)
        self.assertAlmostEqual(sum(probabilities), 1.0)
        self.assertGreater(probabilities[1], probabilities[0])
        self.assertFalse(model.calibrated)
        for _ in range(20):
            proposal = model.propose(context)
            action = self.actions.index(TrajectoryParams.from_mapping(proposal.action))
            self.assertAlmostEqual(math.exp(proposal.logprob), probabilities[action])
            self.assertEqual(proposal.confidence, 0)
            self.assertTrue(proposal.in_distribution)
            self.assertEqual(proposal.policy_version, model.version)

    def test_calibration_uses_separate_sessions_and_changes_version(self) -> None:
        model = HierarchicalTrajectoryBandit.fit(self.rows("train"), self.actions)
        calibrated = model.calibrate(self.rows("validation"))
        self.assertFalse(model.calibrated)
        self.assertTrue(calibrated.calibrated)
        self.assertNotEqual(calibrated.version, model.version)
        decision = calibrated.propose({"initial_arousal": 0.5})
        self.assertGreater(decision.confidence, 0.65)
        self.assertLess(decision.confidence, 1.0)
        with self.assertRaisesRegex(ValueError, "disjoint"):
            model.calibrate(self.rows("train"))

    def test_duplicate_validation_windows_cannot_inflate_support(self) -> None:
        model = HierarchicalTrajectoryBandit.fit(self.rows("train"), self.actions)
        rows = self.rows("validation", count=2) * 100
        calibrated = model.calibrate(rows)
        self.assertFalse(calibrated.calibrated)
        self.assertEqual(calibrated.calibration.counts, (2, 2))  # type: ignore[union-attr]

    def test_unseen_subject_pools_but_out_of_support_context_is_ood(self) -> None:
        model = HierarchicalTrajectoryBandit.fit(
            self.rows("train"), self.actions, validation_rows=self.rows("validation")
        )
        self.assertTrue(model.propose({"initial_arousal": 0.5, "subject_id": "new"}).in_distribution)
        ood = model.propose({"initial_arousal": 0.99})
        self.assertFalse(ood.in_distribution)
        self.assertEqual(ood.confidence, 0)
        invalid = model.propose({"subject_id": "missing-feature"})
        self.assertEqual(invalid.error, "invalid_l2_context")

    def test_subject_posterior_shrinks_toward_population(self) -> None:
        rows = []
        for i in range(40):
            for action in range(2):
                subject = "sparse" if i == 0 else "population"
                reward = (1.0 if subject == "sparse" else 0.2) if action == 0 else 0.5
                rows.append(TrajectoryObservation(
                    {"initial_arousal": 0.5, "subject_id": subject}, action, reward
                ))
        model = HierarchicalTrajectoryBandit.fit(rows, self.actions)
        offsets = dict(model.subject_offsets)
        self.assertGreater(offsets["sparse"][0], 0)
        self.assertLess(offsets["sparse"][0], (1.0 - 0.2) / 5)
        sparse = model.probabilities({"initial_arousal": 0.5, "subject_id": "sparse"})
        cold = model.probabilities({"initial_arousal": 0.5, "subject_id": "unseen"})
        self.assertGreater(sparse[0], cold[0])

    def test_immutable_content_identity_and_artifact_roundtrip(self) -> None:
        rows = self.rows("train")
        model = HierarchicalTrajectoryBandit.fit(rows, self.actions)
        reordered = HierarchicalTrajectoryBandit.fit(list(reversed(rows)), self.actions)
        self.assertEqual(model.version, reordered.version)
        changed = HierarchicalTrajectoryBandit.fit(
            [replace(rows[0], reward=0.5), *rows[1:]], self.actions
        )
        self.assertNotEqual(model.version, changed.version)
        with self.assertRaises(FrozenInstanceError):
            model.temperature = 0.4  # type: ignore[misc]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "trajectory-model.json"
            model.save(path)
            loaded = HierarchicalTrajectoryBandit.load(path)
            self.assertEqual(loaded.version, model.version)
            self.assertEqual(loaded.probabilities({"initial_arousal": 0.5}), model.probabilities({"initial_arousal": 0.5}))
            artifact = json.loads(path.read_text())
            artifact["model"]["temperature"] = 0.8
            path.write_text(json.dumps(artifact))
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                HierarchicalTrajectoryBandit.load(path)

    def test_invalid_rewards_and_duplicate_actions_rejected(self) -> None:
        for reward in (float("nan"), -0.01, 1.01):
            with self.assertRaises(ValueError):
                HierarchicalTrajectoryBandit.fit([
                    TrajectoryObservation({"initial_arousal": 0.5}, 0, reward)
                ], self.actions)
        with self.assertRaises(ValueError):
            HierarchicalTrajectoryBandit.fit(self.rows("train"), [self.actions[0]] * 2)

    def test_fitted_l2_session_startup_shadow_active_and_exported_propensity(self) -> None:
        def observations(prefix: str) -> list[TrajectoryObservation]:
            return [
                TrajectoryObservation(
                    {"initial_arousal": i / 19, "session_id": f"{prefix}-{i}"},
                    0,
                    0.5,
                )
                for i in range(20)
            ]

        model = HierarchicalTrajectoryBandit.fit(
            observations("train"),
            [TrajectoryParams(floor=0.3)],
            validation_rows=observations("heldout"),
        )
        for mode in ("shadow", "restricted", "autonomous"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                cfg = replace(DEFAULT, learning=LearningConfig(
                    mode=mode,
                    enable_l2=True,
                    trial_id="generated-smoke",
                    policy_versions=(("l2", model.version),),
                ))
                session = Session(
                    "generated-subject", ready_baseline(), ProgramState(18),
                    engine=NullEngine(), arm=Arm.FULL_LOOP, cfg=cfg,
                    out_dir=directory, policies={"l2": model},
                )
                for t in (0, 15, 30):
                    window = synthetic_window(t, arousal=0.8)
                    if t == 0:
                        window.contact_impedance = 9e9
                        window.rr_intervals = []
                    session.slow_tick(window)
                exported = json.loads(Path(session.finish()).read_text())
                self.assertEqual(len(exported["policy_decisions"]), 1)
                decision = exported["policy_decisions"][0]
                self.assertEqual(decision["policy_version"], model.version)
                self.assertEqual(decision["logprob"], 0.0)
                if mode == "shadow":
                    self.assertIsNone(session.planner.params)
                    self.assertEqual(decision["lambda_mix"], 0.0)
                else:
                    self.assertIsNotNone(session.planner.params)
                    selected = decision["selected_action"]["floor"]
                    self.assertGreater(selected, DEFAULT.planner.floor_arousal)
                    self.assertLessEqual(selected, 0.3)
                    if mode == "restricted":
                        distance = (selected - DEFAULT.planner.floor_arousal) / 0.35
                        self.assertLessEqual(distance, cfg.learning.restricted_delta + 1e-12)


if __name__ == "__main__":
    unittest.main()
