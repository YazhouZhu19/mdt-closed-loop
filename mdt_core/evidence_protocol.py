"""v2.2 evidence bookkeeping, not an efficacy or independent-review engine.

An audit denominator is supplied independently of available rows. Missing ACKs,
fields and rows remain unknown. F1/F2/F3 describe declared provenance; validating
metadata does not authenticate logs or turn a constructed fault into observation.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from .research_metrics import zero_event_session_risk_upper_bound


def _text(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")


def _texts(name: str, values: tuple[str, ...]) -> None:
    if not isinstance(values, tuple):
        raise TypeError(f"{name} must be an immutable tuple")
    for value in values:
        _text(name, value)
    if len(values) != len(set(values)):
        raise ValueError(f"{name} contains duplicates")


def _probability(name: str, value: float, *, interior: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric")
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{name} must lie in [0, 1]")
    if interior and value in (0, 1):
        raise ValueError(f"{name} must lie strictly between 0 and 1")


class AuditVerdict(str, Enum):
    CONFIRMED = "confirmed"
    RULED_OUT = "ruled_out"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class AuditUnit:
    """Predeclared denominator, event and population; one row per session.

    Session identifiers may name predeclared independent clusters, provided that
    ``definition`` explains their aggregation. Windows/seeds do not become
    independent sessions simply by receiving different identifiers.
    """

    definition: str
    population: str
    eligibility_rule: str
    event_definition: str
    protocol_reference: str

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            _text(name, getattr(self, name))


@dataclass(frozen=True)
class AuditRecord:
    session_id: str
    event_type: str
    source_id: str
    verdict: AuditVerdict = AuditVerdict.INDETERMINATE
    evidence_references: tuple[str, ...] = ()
    required_fields_complete: bool = False
    requires_execution_ack: bool = True
    execution_ack_id: str | None = None
    no_ack_required_reason: str | None = None

    def __post_init__(self) -> None:
        for name in ("session_id", "event_type", "source_id"):
            _text(name, getattr(self, name))
        if not isinstance(self.verdict, AuditVerdict):
            raise TypeError("verdict must be an AuditVerdict")
        _texts("evidence_references", self.evidence_references)
        for name in ("required_fields_complete", "requires_execution_ack"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be bool")
        for name in ("execution_ack_id", "no_ack_required_reason"):
            if getattr(self, name) is not None:
                _text(name, getattr(self, name))
        if not self.requires_execution_ack and not self.no_ack_required_reason:
            raise ValueError("a protocol-grounded reason is required to waive ACK")

    @property
    def effective_verdict(self) -> AuditVerdict:
        """Incomplete evidence cannot substantiate either a positive or zero."""
        if not self.required_fields_complete or not self.evidence_references:
            return AuditVerdict.INDETERMINATE
        if self.requires_execution_ack and self.execution_ack_id is None:
            return AuditVerdict.INDETERMINATE
        return self.verdict


@dataclass(frozen=True)
class BernoulliAssumptions:
    """Explicit analysis assumptions, not inferred from the number of rows."""

    independent_units: bool
    common_risk: bool
    justification: str

    def __post_init__(self) -> None:
        _text("justification", self.justification)
        if not isinstance(self.independent_units, bool) or not isinstance(
            self.common_risk, bool
        ):
            raise TypeError("Bernoulli assumptions must be bool")


@dataclass(frozen=True)
class AuditSummary:
    """Rates for the supplied event-specific at-risk roster.

    ``determinate_risk`` is descriptive only for the assessable subset; it is not
    an estimate for the full roster or target population when outcomes are unknown.
    """

    event_type: str
    audit_unit: AuditUnit
    eligible_n: int
    determinate_n: int
    confirmed: int
    ruled_out: int
    indeterminate: int
    coverage: float | None
    observed_risk: float | None
    determinate_risk: float | None
    cohort_risk_range: tuple[float, float] | None
    exact_lower: float | None
    exact_upper: float | None
    alpha: float
    bernoulli_assumptions: BernoulliAssumptions | None
    source_ids: tuple[str, ...]


def summarize_audit(
    records: Sequence[AuditRecord],
    *,
    eligible_session_ids: Sequence[str],
    event_type: str,
    audit_unit: AuditUnit,
    bernoulli_assumptions: BernoulliAssumptions | None = None,
    alpha: float = 0.05,
) -> AuditSummary:
    """Deduplicate identical audits; reject conflicting session/event rows.

    ``eligible_session_ids`` must already be filtered by the caller to the
    event-specific at-risk roster. NOT_AT_RISK sessions are not passed here;
    external inventory must separately report all eligible and at-risk counts.
    This helper implements three adjudication states, not a fourth eligibility
    state, and does not detect STOP opportunities or other risk opportunities.

    A missing eligible row counts as indeterminate. ``cohort_risk_range`` is the
    logical finite-cohort range [confirmed/n, (confirmed+unknown)/n], NOT a
    confidence interval. Exact bounds require complete adjudication and explicit
    independent common-risk Bernoulli assumptions. Each bound is one-sided at
    1-alpha; the pair must not be described as a 1-alpha two-sided interval.
    ``determinate_risk`` is confirmed/determinate_n for the assessable subset
    only. It must not replace the unknown full-roster risk when coverage is low.
    """
    _text("event_type", event_type)
    _probability("alpha", alpha, interior=True)
    if not isinstance(audit_unit, AuditUnit):
        raise TypeError("audit_unit must be an AuditUnit")
    if bernoulli_assumptions is not None and not isinstance(
        bernoulli_assumptions, BernoulliAssumptions
    ):
        raise TypeError("invalid Bernoulli assumptions")
    identifiers = tuple(eligible_session_ids)
    _texts("eligible_session_ids", identifiers)
    eligible = set(identifiers)
    rows: dict[str, AuditRecord] = {}
    for record in records:
        if not isinstance(record, AuditRecord):
            raise TypeError("records must contain AuditRecord instances")
        if record.event_type != event_type:
            continue
        if record.session_id not in eligible:
            raise ValueError("audit row is outside the predeclared eligible roster")
        if record.session_id in rows and rows[record.session_id] != record:
            raise ValueError("conflicting audits for the same session/event")
        rows[record.session_id] = record
    confirmed = sum(
        row.effective_verdict is AuditVerdict.CONFIRMED for row in rows.values()
    )
    ruled_out = sum(
        row.effective_verdict is AuditVerdict.RULED_OUT for row in rows.values()
    )
    n, determinate = len(eligible), confirmed + ruled_out
    unknown = n - determinate
    lower = upper = None
    if (
        n
        and unknown == 0
        and bernoulli_assumptions is not None
        and bernoulli_assumptions.independent_units
        and bernoulli_assumptions.common_risk
    ):
        from scipy.stats import beta

        lower = (
            0.0
            if confirmed == 0
            else float(beta.ppf(alpha, confirmed, n - confirmed + 1))
        )
        if confirmed == 0:
            upper = zero_event_session_risk_upper_bound(n, alpha=alpha)
        else:
            upper = (
                1.0
                if confirmed == n
                else float(beta.ppf(1 - alpha, confirmed + 1, n - confirmed))
            )
    return AuditSummary(
        event_type=event_type,
        audit_unit=audit_unit,
        eligible_n=n,
        determinate_n=determinate,
        confirmed=confirmed,
        ruled_out=ruled_out,
        indeterminate=unknown,
        coverage=determinate / n if n else None,
        observed_risk=confirmed / n if n and not unknown else None,
        determinate_risk=confirmed / determinate if determinate else None,
        cohort_risk_range=(confirmed / n, (confirmed + unknown) / n) if n else None,
        exact_lower=lower,
        exact_upper=upper,
        alpha=alpha,
        bernoulli_assumptions=bernoulli_assumptions,
        source_ids=tuple(sorted({row.source_id for row in rows.values()})),
    )


class EvidenceClass(str, Enum):
    F1 = "F1_empirical_execution"
    F2 = "F2_external_evidence"
    F3 = "F3_constructed_verification"


@dataclass(frozen=True)
class EvidenceSource:
    source_id: str
    evidence_class: EvidenceClass
    reference: str
    description: str
    real_execution_logs: bool = False
    natural_uninjected_run: bool = False
    traceable_record_references: tuple[str, ...] = ()
    frequency_known: bool = False
    exposure_denominator: int | None = None
    exposure_unit: str | None = None
    construction_specification: str | None = None
    derived_from_source_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("source_id", "reference", "description"):
            _text(name, getattr(self, name))
        if not isinstance(self.evidence_class, EvidenceClass):
            raise TypeError("evidence_class must be an EvidenceClass")
        _texts("traceable_record_references", self.traceable_record_references)
        _texts("derived_from_source_ids", self.derived_from_source_ids)
        if self.source_id in self.derived_from_source_ids:
            raise ValueError("a source cannot derive from itself")
        for name in (
            "real_execution_logs",
            "natural_uninjected_run",
            "frequency_known",
        ):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be bool")
        if self.evidence_class is EvidenceClass.F1:
            if (
                not self.real_execution_logs
                or not self.natural_uninjected_run
                or not self.traceable_record_references
            ):
                raise ValueError(
                    "F1 requires traceable natural uninjected execution logs"
                )
            if self.construction_specification is not None:
                raise ValueError("constructed faults must remain F3")
        elif self.natural_uninjected_run:
            raise ValueError("only F1 can declare a natural uninjected M0 source")
        if self.evidence_class is EvidenceClass.F3:
            _text("construction_specification", self.construction_specification)
            if self.frequency_known:
                raise ValueError("injected frequency is not empirical occurrence")
        elif self.construction_specification is not None:
            raise ValueError("construction specification belongs to F3")
        if self.frequency_known:
            denominator = self.exposure_denominator
            if (
                isinstance(denominator, bool)
                or not isinstance(denominator, int)
                or denominator <= 0
            ):
                raise ValueError("known frequency requires positive exposure count")
            _text("exposure_unit", self.exposure_unit)
        elif self.exposure_denominator is not None or self.exposure_unit is not None:
            raise ValueError("unknown frequency must not advertise an exposure rate")


def partition_evidence(
    sources: Sequence[EvidenceSource],
) -> dict[EvidenceClass, tuple[EvidenceSource, ...]]:
    """Keep the three evidence classes separate; no pooled benefit estimator."""
    grouped: dict[EvidenceClass, list[EvidenceSource]] = {
        category: [] for category in EvidenceClass
    }
    seen: set[str] = set()
    for source in sources:
        if not isinstance(source, EvidenceSource):
            raise TypeError("sources must contain EvidenceSource instances")
        if source.source_id in seen:
            raise ValueError("duplicate evidence source identifier")
        grouped[source.evidence_class].append(source)
        seen.add(source.source_id)
    return {category: tuple(items) for category, items in grouped.items()}


class GateStatus(str, Enum):
    NOT_ASSESSABLE = "not_assessable"
    INCONCLUSIVE = "inconclusive"
    THRESHOLD_EXCEEDED = "threshold_exceeded"
    BELOW_THRESHOLD = "below_threshold"


@dataclass(frozen=True)
class M0Protocol:
    event_type: str
    audit_unit: AuditUnit
    risk_threshold: float
    registration_reference: str
    threshold_rationale: str
    alpha: float = 0.05

    def __post_init__(self) -> None:
        for name in ("event_type", "registration_reference", "threshold_rationale"):
            _text(name, getattr(self, name))
        if not isinstance(self.audit_unit, AuditUnit):
            raise TypeError("audit_unit must be an AuditUnit")
        _probability("risk_threshold", self.risk_threshold)
        _probability("alpha", self.alpha, interior=True)


@dataclass(frozen=True)
class M0GateResult:
    status: GateStatus
    reason: str
    consequence_established: bool = False


def evaluate_m0(
    summary: AuditSummary,
    sources: Sequence[EvidenceSource],
    protocol: M0Protocol,
) -> M0GateResult:
    """Evaluate only declared F1 audit evidence against a frozen risk threshold.

    The gate tests an occurrence threshold. It cannot establish impact, clinical
    relevance, authentic provenance or baseline independence. Unknown evidence
    makes M0 inconclusive; absence of actual logs makes it not assessable. Neither
    state refutes a hypothesis. F2/F3 summaries cannot be substituted or pooled.
    """
    if (
        summary.event_type != protocol.event_type
        or summary.audit_unit != protocol.audit_unit
        or summary.alpha != protocol.alpha
    ):
        raise ValueError("audit and preregistered M0 definitions must match")
    partition_evidence(sources)
    indexed = {source.source_id: source for source in sources}
    if set(summary.source_ids) - set(indexed):
        raise ValueError("audit references an undeclared evidence source")
    selected = [indexed[key] for key in summary.source_ids]
    if selected and any(
        source.evidence_class is not EvidenceClass.F1 for source in selected
    ):
        if any(source.evidence_class is EvidenceClass.F1 for source in selected):
            raise ValueError(
                "M0 must not pool F1 with external or constructed evidence"
            )
        return M0GateResult(
            GateStatus.NOT_ASSESSABLE, "No qualifying F1 execution-log audit."
        )
    if not selected or not summary.eligible_n:
        return M0GateResult(
            GateStatus.NOT_ASSESSABLE, "No qualifying F1 execution-log audit."
        )
    if summary.indeterminate:
        return M0GateResult(
            GateStatus.INCONCLUSIVE,
            "Unknown outcomes prevent a complete risk estimate; hypothesis unresolved.",
        )
    if summary.exact_lower is None or summary.exact_upper is None:
        return M0GateResult(
            GateStatus.INCONCLUSIVE,
            "Exact inference requires declared independent common-risk Bernoulli units.",
        )
    if summary.exact_lower > protocol.risk_threshold:
        return M0GateResult(
            GateStatus.THRESHOLD_EXCEEDED,
            "One-sided lower bound exceeds the predeclared occurrence threshold; "
            "consequences require separate evidence.",
        )
    if summary.exact_upper < protocol.risk_threshold:
        return M0GateResult(
            GateStatus.BELOW_THRESHOLD,
            "One-sided upper bound is below the predeclared occurrence threshold; "
            "low occurrence does not demonstrate high consequence.",
        )
    return M0GateResult(
        GateStatus.INCONCLUSIVE,
        "Bounds do not resolve the predeclared threshold; hypothesis unresolved.",
    )


class IndependenceStatus(str, Enum):
    PENDING = "pending"
    DECLARED_COMPLETE = "declared_complete"
    CONFLICTED = "conflicted"


@dataclass(frozen=True)
class BaselineIndependenceDeclaration:
    """A reported declaration, never a claim of automated external peer review."""

    baseline_id: str
    implementation_reference: str
    requirements_reference: str
    construction_author_ids: tuple[str, ...]
    status: IndependenceStatus = IndependenceStatus.PENDING
    reviewer_ids: tuple[str, ...] = ()
    review_reference: str | None = None
    disclosed_conflicts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "baseline_id",
            "implementation_reference",
            "requirements_reference",
        ):
            _text(name, getattr(self, name))
        if not isinstance(self.status, IndependenceStatus):
            raise TypeError("status must be an IndependenceStatus")
        for name in ("construction_author_ids", "reviewer_ids", "disclosed_conflicts"):
            _texts(name, getattr(self, name))
        if not self.construction_author_ids:
            raise ValueError("baseline construction authors must be declared")
        if self.review_reference is not None:
            _text("review_reference", self.review_reference)
        overlap = set(self.construction_author_ids) & set(self.reviewer_ids)
        if self.status is IndependenceStatus.DECLARED_COMPLETE:
            if not self.reviewer_ids or not self.review_reference:
                raise ValueError("completed declaration requires reviewers and record")
            if overlap or self.disclosed_conflicts:
                raise ValueError(
                    "independence cannot be declared with identified conflicts"
                )
        if (
            self.status is IndependenceStatus.CONFLICTED
            and not overlap
            and not self.disclosed_conflicts
        ):
            raise ValueError("conflicted status requires an identified conflict")
