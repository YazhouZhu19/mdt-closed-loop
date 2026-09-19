"""Adversarial checks of v2.2 missingness, provenance and publication gates."""

from __future__ import annotations

import math
import unittest
from dataclasses import replace
from typing import cast

from mdt_core.evidence_protocol import (
    AuditRecord,
    AuditUnit,
    AuditVerdict,
    BaselineIndependenceDeclaration,
    BernoulliAssumptions,
    EvidenceClass,
    EvidenceSource,
    GateStatus,
    IndependenceStatus,
    M0Protocol,
    evaluate_m0,
    partition_evidence,
    summarize_audit,
)

UNIT = AuditUnit(
    definition="One prospectively eligible independent execution session",
    population="Declared target deployment",
    eligibility_rule="Every started session in the frozen sampling frame",
    event_definition="At least one independently confirmed invalid execution",
    protocol_reference="protocol-v1#primary-event",
)
ASSUMPTIONS = BernoulliAssumptions(
    True, True, "Independent sampled sessions under the predeclared common-risk model"
)


def row(session="s1", verdict=AuditVerdict.RULED_OUT, **kwargs):
    return replace(
        AuditRecord(
            session,
            "invalid_execution",
            "real-logs",
            verdict,
            (f"log-manifest#{session}",),
            True,
            True,
            f"ack-{session}",
        ),
        **kwargs,
    )


def audit(rows, identifiers=None, **kwargs):
    return summarize_audit(
        rows,
        eligible_session_ids=(
            list(dict.fromkeys(record.session_id for record in rows))
            if identifiers is None
            else identifiers
        ),
        event_type="invalid_execution",
        audit_unit=UNIT,
        **kwargs,
    )


def f1():
    return EvidenceSource(
        "real-logs",
        EvidenceClass.F1,
        "execution-archive#manifest-sha256",
        "Actual execution logs; provenance still needs external authentication",
        True,
        True,
        ("execution-archive#session-records",),
    )


def f2():
    return EvidenceSource(
        "external",
        EvidenceClass.F2,
        "external-report#fault-observations",
        "External qualitative fault report with unknown occurrence frequency",
    )


def f3():
    return EvidenceSource(
        "constructed",
        EvidenceClass.F3,
        "fault-protocol#delay-scenario",
        "Constructed delay for verification",
        construction_specification="Inject a 2-second delay at a chosen boundary",
    )


class AuditTests(unittest.TestCase):
    def test_three_outcomes_and_missing_rows_preserve_denominator(self):
        summary = audit(
            [row("positive", AuditVerdict.CONFIRMED), row("negative")],
            ["positive", "negative", "missing"],
            bernoulli_assumptions=ASSUMPTIONS,
        )
        self.assertEqual(summary.eligible_n, 3)
        self.assertEqual(summary.determinate_n, 2)
        self.assertEqual(
            (summary.confirmed, summary.ruled_out, summary.indeterminate), (1, 1, 1)
        )
        self.assertAlmostEqual(summary.coverage, 2 / 3)
        self.assertEqual(summary.cohort_risk_range, (1 / 3, 2 / 3))
        self.assertIsNone(summary.observed_risk)
        self.assertEqual(summary.determinate_risk, 0.5)
        self.assertIsNone(summary.exact_lower)
        self.assertIsNone(summary.exact_upper)

    def test_missing_ack_is_unknown_even_when_verdict_claims_zero(self):
        summary = audit([row(execution_ack_id=None)])
        self.assertEqual(summary.ruled_out, 0)
        self.assertEqual(summary.indeterminate, 1)
        self.assertEqual(summary.cohort_risk_range, (0, 1))

    def test_unsubstantiated_positive_is_also_unknown(self):
        summary = audit([row(verdict=AuditVerdict.CONFIRMED, execution_ack_id=None)])
        self.assertEqual(summary.confirmed, 0)
        self.assertEqual(summary.indeterminate, 1)

    def test_missing_fields_and_references_cannot_confirm_absence(self):
        for change in (
            {"required_fields_complete": False},
            {"evidence_references": ()},
        ):
            with self.subTest(change=change):
                summary = audit([row(**change)])
                self.assertEqual(summary.indeterminate, 1)

    def test_ack_waiver_requires_explicit_protocol_reason(self):
        with self.assertRaises(ValueError):
            row(requires_execution_ack=False, execution_ack_id=None)
        record = row(
            requires_execution_ack=False,
            execution_ack_id=None,
            no_ack_required_reason="Frozen event definition concerns submission only",
        )
        self.assertEqual(record.effective_verdict, AuditVerdict.RULED_OUT)

    def test_identical_duplicate_does_not_inflate_n(self):
        record = row(verdict=AuditVerdict.CONFIRMED)
        summary = audit([record] * 100, ["s1"], bernoulli_assumptions=ASSUMPTIONS)
        self.assertEqual((summary.eligible_n, summary.confirmed), (1, 1))
        self.assertAlmostEqual(summary.exact_lower, 0.05)

    def test_conflicting_duplicate_requires_explicit_reconciliation(self):
        with self.assertRaises(ValueError):
            audit([row(), row(verdict=AuditVerdict.CONFIRMED)])
        with self.assertRaises(ValueError):
            audit([row(), row(execution_ack_id="different-ack")])

    def test_duplicate_roster_cannot_create_pseudoreplication(self):
        with self.assertRaises(ValueError):
            audit([row()], ["s1", "s1"])

    def test_ineligible_records_are_not_added_to_denominator(self):
        with self.assertRaises(ValueError):
            audit([row("unexpected")], ["s1"])

    def test_different_event_type_is_not_counted(self):
        summary = audit([row(event_type="rejected_proposal")], ["s1"])
        self.assertEqual(summary.indeterminate, 1)
        self.assertEqual(summary.source_ids, ())

    def test_empty_audit_returns_no_risk_interval_or_coverage(self):
        summary = audit([], [], bernoulli_assumptions=ASSUMPTIONS)
        for field in (
            "coverage",
            "observed_risk",
            "determinate_risk",
            "cohort_risk_range",
            "exact_lower",
            "exact_upper",
        ):
            self.assertIsNone(getattr(summary, field), field)

    def test_nonempty_roster_with_no_rows_is_entirely_unknown(self):
        summary = audit([], ["s1", "s2"])
        self.assertEqual(summary.indeterminate, 2)
        self.assertEqual(summary.coverage, 0)
        self.assertEqual(summary.cohort_risk_range, (0, 1))
        self.assertIsNone(summary.determinate_risk)

    def test_determinate_risk_describes_assessable_subset_not_full_roster(self):
        summary = audit(
            [
                row("known", AuditVerdict.CONFIRMED),
                row("unknown", execution_ack_id=None),
            ],
            ["known", "unknown", "missing"],
            bernoulli_assumptions=ASSUMPTIONS,
        )
        self.assertEqual(summary.determinate_risk, 1)
        self.assertEqual(summary.determinate_n, 1)
        self.assertAlmostEqual(summary.coverage, 1 / 3)
        self.assertIsNone(summary.observed_risk)
        self.assertIsNone(summary.exact_lower)
        self.assertIsNone(summary.exact_upper)

    def test_zero_assessable_count_is_not_zero_descriptive_risk(self):
        summary = audit([row(execution_ack_id=None)])
        self.assertEqual(summary.eligible_n, 1)
        self.assertEqual(summary.determinate_n, 0)
        self.assertIsNone(summary.determinate_risk)

    def test_exact_bounds_require_both_declared_assumptions(self):
        for assumptions in (
            None,
            replace(ASSUMPTIONS, independent_units=False),
            replace(ASSUMPTIONS, common_risk=False),
        ):
            with self.subTest(assumptions=assumptions):
                summary = audit([row()], bernoulli_assumptions=assumptions)
                self.assertEqual(summary.observed_risk, 0)
                self.assertIsNone(summary.exact_upper)

    def test_zero_event_exact_bound_matches_analytic_solution(self):
        summary = audit(
            [row(f"s{i}") for i in range(100)], bernoulli_assumptions=ASSUMPTIONS
        )
        self.assertEqual(summary.exact_lower, 0)
        self.assertAlmostEqual(summary.exact_upper, 1 - 0.05 ** (1 / 100))

    def test_all_event_exact_lower_matches_analytic_solution(self):
        summary = audit(
            [row(f"s{i}", AuditVerdict.CONFIRMED) for i in range(100)],
            bernoulli_assumptions=ASSUMPTIONS,
        )
        self.assertEqual(summary.exact_upper, 1)
        self.assertAlmostEqual(summary.exact_lower, 0.05 ** (1 / 100))

    def test_nontrivial_exact_bounds_invert_binomial_tails(self):
        from scipy.stats import binom

        summary = audit(
            [
                row(
                    f"s{i}", AuditVerdict.CONFIRMED if i < 3 else AuditVerdict.RULED_OUT
                )
                for i in range(10)
            ],
            bernoulli_assumptions=ASSUMPTIONS,
        )
        self.assertAlmostEqual(binom.sf(2, 10, summary.exact_lower), 0.05)
        self.assertAlmostEqual(binom.cdf(3, 10, summary.exact_upper), 0.05)

    def test_bad_alpha_fails_instead_of_silent_invalid_inference(self):
        for alpha in (0, 1, -0.01, math.nan, math.inf, True):
            with self.subTest(alpha=alpha), self.assertRaises((TypeError, ValueError)):
                audit([row()], alpha=alpha)

    def test_record_and_assumptions_validate_types(self):
        for kwargs in ({"verdict": "ruled_out"}, {"required_fields_complete": 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(TypeError):
                row(**kwargs)
        with self.assertRaises(TypeError):
            replace(ASSUMPTIONS, independent_units=cast(bool, 1))
        with self.assertRaises(ValueError):
            replace(UNIT, eligibility_rule=" ")


class ProvenanceTests(unittest.TestCase):
    def test_evidence_classes_are_returned_separately(self):
        partition = partition_evidence([f3(), f1(), f2()])
        self.assertEqual(partition[EvidenceClass.F1], (f1(),))
        self.assertEqual(partition[EvidenceClass.F2], (f2(),))
        self.assertEqual(partition[EvidenceClass.F3], (f3(),))

    def test_f1_requires_actual_log_declaration_and_traceability(self):
        for kwargs in (
            {"real_execution_logs": False},
            {"natural_uninjected_run": False},
            {"traceable_record_references": ()},
            {"reference": ""},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                replace(f1(), **kwargs)

    def test_f2_requires_external_reference(self):
        with self.assertRaises(ValueError):
            replace(f2(), reference="")

    def test_actual_injected_or_undeclared_run_cannot_enter_f1(self):
        with self.assertRaises(ValueError):
            EvidenceSource(
                "actual-device-log",
                EvidenceClass.F1,
                "archive#injected-run",
                "Actual device execution without a natural-run declaration",
                real_execution_logs=True,
                traceable_record_references=("archive#record",),
            )
        injected = replace(f3(), real_execution_logs=True)
        self.assertEqual(injected.evidence_class, EvidenceClass.F3)
        self.assertFalse(injected.natural_uninjected_run)

    def test_f2_and_f3_cannot_declare_natural_m0_provenance(self):
        for source in (f2(), f3()):
            with self.subTest(source=source), self.assertRaises(ValueError):
                replace(source, natural_uninjected_run=True)

    def test_unknown_frequency_stays_unknown(self):
        self.assertFalse(f2().frequency_known)
        with self.assertRaises(ValueError):
            replace(f2(), exposure_denominator=200, exposure_unit="sessions")

    def test_known_frequency_requires_exposure_metadata(self):
        with self.assertRaises(ValueError):
            replace(f2(), frequency_known=True)
        source = replace(
            f2(),
            frequency_known=True,
            exposure_denominator=200,
            exposure_unit="sessions",
        )
        self.assertEqual(source.exposure_denominator, 200)
        for denominator in (0, -1, True, 0.5):
            with self.subTest(denominator=denominator), self.assertRaises(ValueError):
                replace(source, exposure_denominator=denominator)

    def test_injected_schedule_cannot_become_empirical_frequency(self):
        with self.assertRaises(ValueError):
            replace(
                f3(),
                frequency_known=True,
                exposure_denominator=10,
                exposure_unit="runs",
            )

    def test_inherited_fault_specification_remains_constructed(self):
        derived = replace(f3(), derived_from_source_ids=("real-logs",))
        self.assertEqual(derived.evidence_class, EvidenceClass.F3)
        with self.assertRaises(ValueError):
            replace(
                derived,
                evidence_class=EvidenceClass.F1,
                real_execution_logs=True,
                natural_uninjected_run=True,
                traceable_record_references=("generated-log",),
            )

    def test_duplicate_source_ids_and_self_derivation_rejected(self):
        with self.assertRaises(ValueError):
            partition_evidence([f1(), f1()])
        with self.assertRaises(ValueError):
            replace(f3(), derived_from_source_ids=("constructed",))


class M0Tests(unittest.TestCase):
    def setUp(self):
        self.protocol = M0Protocol(
            "invalid_execution",
            UNIT,
            0.1,
            "registration#M0",
            "Predeclared materiality rule",
        )

    def gate(self, summary, sources=None):
        return evaluate_m0(
            summary, [f1()] if sources is None else sources, self.protocol
        )

    def test_no_real_logs_is_not_assessable_not_falsified(self):
        self.assertEqual(self.gate(audit([], [])).status, GateStatus.NOT_ASSESSABLE)
        summary = audit(
            [row(source_id="constructed")], bernoulli_assumptions=ASSUMPTIONS
        )
        self.assertEqual(self.gate(summary, [f3()]).status, GateStatus.NOT_ASSESSABLE)

    def test_unknown_actual_execution_is_inconclusive(self):
        result = self.gate(audit([row(execution_ack_id=None)]))
        self.assertEqual(result.status, GateStatus.INCONCLUSIVE)
        self.assertIn("unresolved", result.reason)

    def test_assumption_free_complete_audit_is_not_exact_inference(self):
        self.assertEqual(self.gate(audit([row()])).status, GateStatus.INCONCLUSIVE)

    def test_sufficient_positive_evidence_exceeds_threshold(self):
        result = self.gate(
            audit(
                [row(f"s{i}", AuditVerdict.CONFIRMED) for i in range(100)],
                bernoulli_assumptions=ASSUMPTIONS,
            )
        )
        self.assertEqual(result.status, GateStatus.THRESHOLD_EXCEEDED)
        self.assertFalse(result.consequence_established)

    def test_low_occurrence_does_not_establish_high_consequence(self):
        result = self.gate(
            audit([row(f"s{i}") for i in range(100)], bernoulli_assumptions=ASSUMPTIONS)
        )
        self.assertEqual(result.status, GateStatus.BELOW_THRESHOLD)
        self.assertFalse(result.consequence_established)
        self.assertIn("does not demonstrate high consequence", result.reason)

    def test_tiny_zero_event_sample_remains_inconclusive(self):
        result = self.gate(audit([row()], bernoulli_assumptions=ASSUMPTIONS))
        self.assertEqual(result.status, GateStatus.INCONCLUSIVE)

    def test_empirical_and_constructed_rows_must_not_be_pooled(self):
        summary = audit([row("s1"), row("s2", source_id="constructed")])
        with self.assertRaises(ValueError):
            self.gate(summary, [f1(), f3()])

    def test_unrelated_source_catalog_entries_do_not_contaminate_f1(self):
        result = self.gate(
            audit([row()], bernoulli_assumptions=ASSUMPTIONS), [f1(), f3()]
        )
        self.assertEqual(result.status, GateStatus.INCONCLUSIVE)

    def test_undeclared_source_is_rejected(self):
        with self.assertRaises(ValueError):
            self.gate(audit([row(source_id="missing-source")]))

    def test_posthoc_definition_and_alpha_switches_rejected(self):
        original = audit([row()], bernoulli_assumptions=ASSUMPTIONS)
        for replacement in (
            replace(self.protocol, event_type="different-event"),
            replace(
                self.protocol, audit_unit=replace(UNIT, eligibility_rule="Changed")
            ),
            replace(self.protocol, alpha=0.01),
        ):
            with self.subTest(protocol=replacement), self.assertRaises(ValueError):
                evaluate_m0(original, [f1()], replacement)

    def test_threshold_requires_valid_predeclared_metadata(self):
        with self.assertRaises(ValueError):
            replace(self.protocol, registration_reference="")
        with self.assertRaises(ValueError):
            replace(self.protocol, risk_threshold=1.1)


class BaselineDeclarationTests(unittest.TestCase):
    def declaration(self, **kwargs):
        return BaselineIndependenceDeclaration(
            "B3",
            "baseline-commit-hash",
            "requirements#baseline",
            ("constructor",),
            **kwargs,
        )

    def test_default_status_is_pending(self):
        self.assertEqual(self.declaration().status, IndependenceStatus.PENDING)

    def test_completion_requires_named_review_and_record(self):
        with self.assertRaises(ValueError):
            self.declaration(status=IndependenceStatus.DECLARED_COMPLETE)

    def test_author_cannot_review_own_baseline_as_independent(self):
        with self.assertRaises(ValueError):
            self.declaration(
                status=IndependenceStatus.DECLARED_COMPLETE,
                reviewer_ids=("constructor",),
                review_reference="review#1",
            )

    def test_declared_completion_is_not_automated_verification(self):
        declaration = self.declaration(
            status=IndependenceStatus.DECLARED_COMPLETE,
            reviewer_ids=("external-reviewer",),
            review_reference="review#1",
        )
        self.assertEqual(declaration.status.value, "declared_complete")
        self.assertFalse(hasattr(declaration, "external_review_verified"))

    def test_conflicts_are_reportable_but_block_completion(self):
        conflict = self.declaration(
            status=IndependenceStatus.CONFLICTED,
            reviewer_ids=("constructor",),
        )
        self.assertEqual(conflict.status, IndependenceStatus.CONFLICTED)
        with self.assertRaises(ValueError):
            self.declaration(
                status=IndependenceStatus.DECLARED_COMPLETE,
                reviewer_ids=("reviewer",),
                review_reference="review#1",
                disclosed_conflicts=("Shared implementation responsibility",),
            )


if __name__ == "__main__":
    unittest.main()
