"""Interface checks; these fixtures are not empirical execution evidence."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mdt_core.evidence_cli import audit_manifest, load_manifest, main

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/v22_audit_manifest.json"


def example():
    return load_manifest(EXAMPLE)


def declared_f1():
    data = example()
    data["sources"] = [
        {
            "source_id": "constructed-example",
            "evidence_class": "F1_empirical_execution",
            "reference": "test-fixture#declared-archive",
            "description": "Unit-test declaration only, not actual execution evidence",
            "real_execution_logs": True,
            "natural_uninjected_run": True,
            "traceable_record_references": ["test-fixture#declared-archive/session"],
        }
    ]
    return data


class ManifestTests(unittest.TestCase):
    def test_f3_example_is_not_assessable(self):
        report = audit_manifest(example())
        self.assertEqual(report["m0_gate"]["status"], "not_assessable")
        self.assertEqual(report["audit_summary"]["indeterminate"], 1)
        self.assertIsNone(report["audit_summary"]["observed_risk"])
        self.assertIn("draft", report["threshold_interpretation"])
        self.assertFalse(any(report["verification"].values()))

    def test_declared_f1_without_ack_is_unknown(self):
        report = audit_manifest(declared_f1())
        self.assertEqual(report["m0_gate"]["status"], "inconclusive")
        summary = report["audit_summary"]
        self.assertEqual(summary["confirmed"], 0)
        self.assertEqual(summary["determinate_risk"], 0)
        self.assertEqual(summary["cohort_risk_range"], (0, 0.5))
        self.assertIsNone(summary["observed_risk"])
        self.assertIsNone(summary["exact_upper"])

    def test_zero_denominator_remains_unknown(self):
        data = example()
        data["eligible_session_ids"] = []
        data["records"] = []
        data["inventory"]["all_eligible_session_count"] = 0
        report = audit_manifest(data)
        self.assertEqual(report["m0_gate"]["status"], "not_assessable")
        for key in (
            "observed_risk",
            "determinate_risk",
            "cohort_risk_range",
            "coverage",
            "exact_lower",
            "exact_upper",
        ):
            self.assertIsNone(report["audit_summary"][key])

    def test_exact_duplicate_does_not_change_denominator(self):
        data = example()
        data["records"].append(copy.deepcopy(data["records"][0]))
        self.assertEqual(audit_manifest(data), audit_manifest(example()))

    def test_conflicting_records_are_rejected(self):
        data = example()
        row = copy.deepcopy(data["records"][0])
        row["verdict"] = "confirmed"
        data["records"].append(row)
        with self.assertRaisesRegex(ValueError, "conflicting"):
            audit_manifest(data)

    def test_unknown_fields_at_every_object_level(self):
        paths = [
            (),
            ("audit_unit",),
            ("records", 0),
            ("sources", 0),
            ("protocol",),
            ("inventory",),
            ("bernoulli_assumptions",),
            ("baseline_independence",),
        ]
        for path in paths:
            with self.subTest(path=path):
                data = example()
                data["bernoulli_assumptions"] = {
                    "independent_units": False,
                    "common_risk": False,
                    "justification": "Unjustified synthetic assumptions",
                }
                data["baseline_independence"] = {
                    "baseline_id": "B1",
                    "implementation_reference": "pending",
                    "requirements_reference": "draft",
                    "construction_author_ids": ["example-author"],
                }
                target = data
                for component in path:
                    target = target[component]
                target["typo_field"] = True
                with self.assertRaisesRegex(ValueError, "unknown fields"):
                    audit_manifest(data)

    def test_strict_scalar_types_and_enums(self):
        invalid_values = [
            (("records", 0, "required_fields_complete"), 1),
            (("records", 0, "requires_execution_ack"), "true"),
            (("records", 0, "execution_ack_id"), 123),
            (("records", 0, "verdict"), "valid"),
            (("records", 0, "evidence_references"), "reference"),
            (("sources", 0, "evidence_class"), "F3"),
            (("sources", 0, "real_execution_logs"), 0),
            (("protocol", "risk_threshold"), True),
            (("protocol", "alpha"), "0.05"),
            (("protocol", "registration_status"), True),
            (("inventory", "all_eligible_session_count"), 2.0),
            (("schema_version",), "unknown"),
            (("audit_unit", "definition"), None),
            (("eligible_session_ids",), "session"),
        ]
        for path, value in invalid_values:
            with self.subTest(path=path, value=value):
                data = example()
                target = data
                for component in path[:-1]:
                    target = target[component]
                target[path[-1]] = value
                with self.assertRaises((ValueError, TypeError)):
                    audit_manifest(data)

    def test_missing_required_field(self):
        data = example()
        del data["protocol"]["threshold_rationale"]
        with self.assertRaisesRegex(ValueError, "missing fields"):
            audit_manifest(data)

    def test_other_events_are_not_silently_ignored(self):
        data = example()
        data["records"][0]["event_type"] = "another-event"
        with self.assertRaisesRegex(ValueError, "event_type"):
            audit_manifest(data)

    def test_undeclared_source_rejected(self):
        data = example()
        data["records"][0]["source_id"] = "undeclared"
        with self.assertRaisesRegex(ValueError, "undeclared"):
            audit_manifest(data)

    def test_source_classes_cannot_be_pooled_into_m0(self):
        data = declared_f1()
        source = example()["sources"][0]
        source["source_id"] = "second-source"
        data["sources"].append(source)
        data["records"][1]["source_id"] = "second-source"
        with self.assertRaisesRegex(ValueError, "must not pool"):
            audit_manifest(data)

    def test_duplicate_roster_and_outside_row_rejected(self):
        for identifiers in (["constructed-001", "constructed-001"], ["outside-roster"]):
            data = example()
            data["eligible_session_ids"] = identifiers
            with self.assertRaises(ValueError):
                audit_manifest(data)

    def test_population_denominator_distinguished(self):
        data = example()
        data["inventory"]["all_eligible_session_count"] = 10
        report = audit_manifest(data)
        self.assertEqual(report["denominators"]["at_risk_session_count"], 2)
        self.assertEqual(report["denominators"]["not_at_risk_session_count"], 8)
        del data["inventory"]
        self.assertIsNone(
            audit_manifest(data)["denominators"]["all_eligible_session_count"]
        )

    def test_inconsistent_inventory_rejected(self):
        data = example()
        data["inventory"]["all_eligible_session_count"] = 1
        with self.assertRaisesRegex(ValueError, "at-risk"):
            audit_manifest(data)

    def test_optional_exact_bounds_use_existing_helper(self):
        data = declared_f1()
        data["records"][1]["execution_ack_id"] = "declared-ack"
        data["records"][1]["verdict"] = "ruled_out"
        data["bernoulli_assumptions"] = {
            "independent_units": True,
            "common_risk": True,
            "justification": "Assumptions declared for this software test only",
        }
        report = audit_manifest(data)
        self.assertAlmostEqual(report["audit_summary"]["exact_upper"], 1 - 0.05**0.5)

    def test_optional_review_is_a_declaration(self):
        data = example()
        data["baseline_independence"] = {
            "baseline_id": "B1",
            "implementation_reference": "pending",
            "requirements_reference": "draft",
            "construction_author_ids": ["example-author"],
        }
        report = audit_manifest(data)
        self.assertEqual(report["baseline_independence"]["status"], "pending")
        self.assertFalse(report["verification"]["baseline_independence_verified"])


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.input = self.directory / "input.json"
        self.output = self.directory / "output.json"
        self.input.write_text(EXAMPLE.read_text(), encoding="utf-8")

    def run_main(self, *extra):
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            result = main([str(self.input), "--output", str(self.output), *extra])
        return result, errors.getvalue()

    def test_report_is_byte_reproducible(self):
        self.assertEqual(self.run_main()[0], 0)
        first = self.output.read_bytes()
        self.assertEqual(self.run_main("--overwrite")[0], 0)
        self.assertEqual(first, self.output.read_bytes())

    def test_default_refuses_overwrite(self):
        self.output.write_text("existing report", encoding="utf-8")
        self.assertNotEqual(self.run_main()[0], 0)
        self.assertEqual(self.output.read_text(), "existing report")
        self.assertFalse(list(self.directory.glob("*.tmp")))

    def test_explicit_overwrite_replaces_existing_report(self):
        self.output.write_text("old", encoding="utf-8")
        self.assertEqual(self.run_main("--overwrite")[0], 0)
        self.assertEqual(
            json.loads(self.output.read_text())["m0_gate"]["status"], "not_assessable"
        )

    def test_invalid_input_preserves_existing_output(self):
        self.input.write_text('{"schema_version": "bad"}', encoding="utf-8")
        self.output.write_text("existing report", encoding="utf-8")
        self.assertNotEqual(self.run_main("--overwrite")[0], 0)
        self.assertEqual(self.output.read_text(), "existing report")

    def test_invalid_json_does_not_create_report(self):
        for payload in (
            '{"value": NaN}',
            '{"value": Infinity}',
            '{"value": -Infinity}',
            '{"value": 1e999}',
            '{"nested": {"x": 1, "x": 2}}',
            "{",
            "[]",
        ):
            with self.subTest(payload=payload):
                self.input.write_text(payload, encoding="utf-8")
                status, error = self.run_main()
                self.assertNotEqual(status, 0)
                self.assertIn("evidence audit error", error)
                self.assertFalse(self.output.exists())

    def test_same_input_output_is_rejected(self):
        before = self.input.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()):
            status = main([str(self.input), "--output", str(self.input), "--overwrite"])
        self.assertNotEqual(status, 0)
        self.assertEqual(before, self.input.read_bytes())

    def test_module_invocation(self):
        process = subprocess.run(
            [sys.executable, "-m", "mdt_core.evidence_cli", str(self.input)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(
            json.loads(process.stdout)["schema_version"], "mdt.evidence.audit-report.v1"
        )

    def test_output_io_failure_does_not_leave_report(self):
        self.output = self.directory / "missing-directory" / "output.json"
        self.assertNotEqual(self.run_main()[0], 0)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
