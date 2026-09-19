"""Mathematical regression checks; no participant data or efficacy assertions."""

from __future__ import annotations

import importlib.util
import json
import unittest
from fractions import Fraction
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "v22_operator_ope_sanity", REPOSITORY / "examples/v22_operator_ope_sanity.py"
)
assert SPEC is not None and SPEC.loader is not None
SANITY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SANITY)


def exact(quantity: dict) -> Fraction:
    return Fraction(quantity["exact"])


class OperatorOpeSanityTests(unittest.TestCase):
    def test_raw_ips_remains_valid_with_the_same_operator(self) -> None:
        report = SANITY.discrete_same_operator_check()
        oracle = exact(report["oracle_target_value"])
        self.assertEqual(oracle, Fraction(1, 2))
        for estimator in report["estimators"].values():
            self.assertEqual(exact(estimator["expected_value"]), oracle)
            self.assertEqual(exact(estimator["bias"]), 0)

    def test_true_weight_coarsening_reduces_this_fixture_variance(self) -> None:
        report = SANITY.discrete_same_operator_check()
        raw = report["estimators"]["raw_ips"]
        execution = report["estimators"]["execution_mips"]
        self.assertGreater(
            exact(raw["variance_one_observation"]),
            exact(execution["variance_one_observation"]),
        )
        for row in report["coarsening_identities"]:
            self.assertEqual(
                exact(row["conditional_mean_raw_weight"]),
                exact(row["execution_weight"]),
            )
        for estimator in (raw, execution):
            self.assertEqual(
                exact(estimator["variance_of_iid_mean_n_1000"]) * 1000,
                exact(estimator["variance_one_observation"]),
            )

    def test_positive_density_is_not_boundary_event_mass(self) -> None:
        report = SANITY.uniform_clip_migration_check()
        self.assertTrue(report["topological_support_inclusion"])
        self.assertFalse(
            report["target_absolutely_continuous_with_respect_to_logging"]
        )
        for event in report["new_boundary_events"]:
            self.assertEqual(exact(event["logging_event_probability"]), 0)
            self.assertEqual(
                exact(event["logging_interior_density_at_point"]), Fraction(1, 4)
            )
            self.assertEqual(exact(event["target_event_probability"]), Fraction(3, 8))
            self.assertIsNone(event["importance_weight"])

    def test_operator_change_alone_invalidates_the_raw_target_estimand(self) -> None:
        report = SANITY.uniform_clip_migration_check()
        self.assertEqual(exact(report["raw_ips_weight"]), 1)
        self.assertEqual(exact(report["oracle_logging_value"]), Fraction(2, 3))
        self.assertEqual(exact(report["oracle_target_value"]), Fraction(5, 24))
        self.assertEqual(
            exact(report["raw_ips_bias_for_new_operator_value"]), Fraction(11, 24)
        )

    def test_known_bounded_reward_interval_has_width_of_singular_mass(self) -> None:
        report = SANITY.uniform_clip_migration_check()
        bound = report["no_smoothness_identification_bound"]
        self.assertEqual(exact(bound["lower"]), Fraction(1, 48))
        self.assertEqual(exact(bound["upper"]), Fraction(37, 48))
        self.assertEqual(exact(bound["width"]), Fraction(3, 4))
        self.assertEqual(exact(bound["width"]), exact(report["target_singular_mass"]))
        self.assertLess(exact(bound["lower"]), exact(report["oracle_target_value"]))
        self.assertGreater(exact(bound["upper"]), exact(report["oracle_target_value"]))

    def test_saved_artifact_is_reproducible_and_does_not_claim_evidence(self) -> None:
        saved = json.loads(
            (REPOSITORY / "docs/validation/v22_operator_ope_sanity.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(saved, SANITY.build_report())
        self.assertEqual(saved["provenance"], "constructed_mathematical_sanity_check")
        self.assertFalse(saved["real_data_used"])
        self.assertFalse(saved["sampling_used"])
        self.assertFalse(any(saved["claims"].values()))


if __name__ == "__main__":
    unittest.main()
