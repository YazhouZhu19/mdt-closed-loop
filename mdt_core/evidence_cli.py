"""Strict JSON interface to the v2.2 evidence bookkeeping helpers.

This interface accepts declarations. It does not authenticate execution logs,
register a protocol, review a baseline, or turn simulation into empirical data.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from collections.abc import Callable
from dataclasses import MISSING, asdict, fields
from pathlib import Path
from typing import Any

from .evidence_protocol import (
    AuditRecord,
    AuditUnit,
    AuditVerdict,
    BaselineIndependenceDeclaration,
    BernoulliAssumptions,
    EvidenceClass,
    EvidenceSource,
    IndependenceStatus,
    M0Protocol,
    evaluate_m0,
    partition_evidence,
    summarize_audit,
)

INPUT_SCHEMA = "mdt.evidence.audit.v1"
OUTPUT_SCHEMA = "mdt.evidence.audit-report.v1"


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expected nonempty text")
    return value


def _boolean(value: Any) -> bool:
    if not isinstance(value, bool):
        raise TypeError("expected a JSON boolean")
    return value


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("expected a JSON number")
    try:
        finite = math.isfinite(value)
    except OverflowError as error:
        raise ValueError("number exceeds the supported numeric range") from error
    if not finite:
        raise ValueError("numbers must be finite")
    return value


def _integer(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("expected a nonnegative JSON integer")
    return value


def _nullable(validate: Callable) -> Callable:
    return lambda value: None if value is None else validate(value)


def _array(value: Any) -> list:
    if not isinstance(value, list):
        raise TypeError("expected a JSON array")
    return value


def _texts(value: Any) -> tuple[str, ...]:
    return tuple(_text(item) for item in _array(value))


def _object(value: Any, required: set[str], optional: set[str]) -> dict:
    if not isinstance(value, dict):
        raise TypeError("expected a JSON object")
    missing, unknown = required - value.keys(), value.keys() - required - optional
    if missing:
        raise ValueError(f"missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise ValueError(f"unknown fields: {', '.join(sorted(unknown))}")
    return value


def _dataclass(cls: type, raw: Any, validators: dict[str, Callable]) -> Any:
    required = {
        field.name
        for field in fields(cls)
        if field.default is MISSING and field.default_factory is MISSING
    }
    data = _object(raw, required, set(validators) - required)
    parsed = {}
    for key, value in data.items():
        try:
            parsed[key] = validators[key](value)
        except (ValueError, TypeError) as error:
            raise ValueError(f"{cls.__name__}.{key}: {error}") from error
    return cls(**parsed)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _finite_tree(value: Any) -> None:
    """JSON exponent overflow is not covered by parse_constant alone."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("numbers must be finite")
    if isinstance(value, dict):
        for item in value.values():
            _finite_tree(item)
    elif isinstance(value, list):
        for item in value:
            _finite_tree(item)


def load_manifest(path: Path) -> dict:
    raw = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
    )
    _finite_tree(raw)
    return raw


def audit_manifest(raw: Any) -> dict:
    """Validate one event-specific manifest and return a deterministic report."""
    data = _object(
        raw,
        {
            "schema_version",
            "event_type",
            "audit_unit",
            "eligible_session_ids",
            "records",
            "sources",
            "protocol",
        },
        {"inventory", "bernoulli_assumptions", "baseline_independence"},
    )
    _finite_tree(data)
    if data["schema_version"] != INPUT_SCHEMA:
        raise ValueError(f"schema_version must be {INPUT_SCHEMA!r}")
    event_type = _text(data["event_type"])
    unit = _dataclass(
        AuditUnit,
        data["audit_unit"],
        {
            name: _text
            for name in (
                "definition",
                "population",
                "eligibility_rule",
                "event_definition",
                "protocol_reference",
            )
        },
    )
    roster = _texts(data["eligible_session_ids"])
    records = [
        _dataclass(
            AuditRecord,
            row,
            {
                "session_id": _text,
                "event_type": _text,
                "source_id": _text,
                "verdict": AuditVerdict,
                "evidence_references": _texts,
                "required_fields_complete": _boolean,
                "requires_execution_ack": _boolean,
                "execution_ack_id": _nullable(_text),
                "no_ack_required_reason": _nullable(_text),
            },
        )
        for row in _array(data["records"])
    ]
    if any(record.event_type != event_type for record in records):
        raise ValueError("all records must match the manifest event_type")
    sources = [
        _dataclass(
            EvidenceSource,
            source,
            {
                "source_id": _text,
                "evidence_class": EvidenceClass,
                "reference": _text,
                "description": _text,
                "real_execution_logs": _boolean,
                "natural_uninjected_run": _boolean,
                "traceable_record_references": _texts,
                "frequency_known": _boolean,
                "exposure_denominator": _nullable(_integer),
                "exposure_unit": _nullable(_text),
                "construction_specification": _nullable(_text),
                "derived_from_source_ids": _texts,
            },
        )
        for source in _array(data["sources"])
    ]
    partitions = partition_evidence(sources)
    source_ids = {source.source_id for source in sources}
    if any(record.source_id not in source_ids for record in records):
        raise ValueError("audit references an undeclared evidence source")

    protocol_data = dict(
        _object(
            data["protocol"],
            {
                "registration_status",
                "risk_threshold",
                "registration_reference",
                "threshold_rationale",
            },
            {"alpha"},
        )
    )
    registration_status = protocol_data.pop("registration_status")
    if registration_status not in ("draft", "declared_registered"):
        raise ValueError("registration_status must be draft or declared_registered")
    protocol = _dataclass(
        M0Protocol,
        {
            **protocol_data,
            "event_type": event_type,
            "audit_unit": unit,
        },
        {
            "event_type": _text,
            "audit_unit": lambda value: value,
            "risk_threshold": _number,
            "registration_reference": _text,
            "threshold_rationale": _text,
            "alpha": _number,
        },
    )
    assumptions = None
    if "bernoulli_assumptions" in data:
        assumptions = _dataclass(
            BernoulliAssumptions,
            data["bernoulli_assumptions"],
            {
                "independent_units": _boolean,
                "common_risk": _boolean,
                "justification": _text,
            },
        )
    independence = None
    if "baseline_independence" in data:
        independence = _dataclass(
            BaselineIndependenceDeclaration,
            data["baseline_independence"],
            {
                "baseline_id": _text,
                "implementation_reference": _text,
                "requirements_reference": _text,
                "construction_author_ids": _texts,
                "status": IndependenceStatus,
                "reviewer_ids": _texts,
                "review_reference": _nullable(_text),
                "disclosed_conflicts": _texts,
            },
        )
    all_eligible_count, inventory_reference = None, None
    if "inventory" in data:
        inventory = _object(
            data["inventory"],
            {
                "all_eligible_session_count",
                "reference",
            },
            set(),
        )
        all_eligible_count = _integer(inventory["all_eligible_session_count"])
        inventory_reference = _text(inventory["reference"])
        if all_eligible_count < len(roster):
            raise ValueError("all eligible count cannot be below the at-risk count")

    summary = summarize_audit(
        records,
        eligible_session_ids=roster,
        event_type=event_type,
        audit_unit=unit,
        bernoulli_assumptions=assumptions,
        alpha=protocol.alpha,
    )
    gate = evaluate_m0(summary, sources, protocol)
    return {
        "schema_version": OUTPUT_SCHEMA,
        "input_schema_version": INPUT_SCHEMA,
        "audit_summary": asdict(summary),
        "m0_gate": asdict(gate),
        "protocol": {**asdict(protocol), "registration_status": registration_status},
        "threshold_interpretation": (
            "Illustrative draft threshold; not a preregistered research conclusion."
            if registration_status == "draft"
            else "Registration is declared by the caller and has not been authenticated."
        ),
        "denominators": {
            "at_risk_session_count": len(roster),
            "all_eligible_session_count": all_eligible_count,
            "not_at_risk_session_count": (
                all_eligible_count - len(roster)
                if all_eligible_count is not None
                else None
            ),
            "inventory_reference": inventory_reference,
        },
        "declared_sources": [asdict(source) for source in sources],
        "evidence_partitions": {
            category.value: [source.source_id for source in items]
            for category, items in partitions.items()
        },
        "baseline_independence": (
            asdict(independence) if independence is not None else None
        ),
        "verification": {
            "source_authenticity_verified": False,
            "protocol_registration_verified": False,
            "baseline_independence_verified": False,
            "clinical_effect_established": False,
        },
        "interpretation": {
            "provenance": (
                "All sources and review records are declarations. Generating this "
                "JSON does not produce natural execution logs or empirical evidence."
            ),
            "risk": (
                "observed_risk describes only a completely adjudicated at-risk "
                "roster. determinate_risk describes only its assessable subset; "
                "neither automatically estimates a deployment population risk."
            ),
            "unknown": (
                "Missing required ACKs, fields, references or rows remain unknown; "
                "missing evidence is not a zero-event result."
            ),
            "cohort_risk_range": (
                "A logical finite-cohort range, not a confidence interval."
            ),
            "exact_bounds": (
                "Each bound is separately one-sided at 1-alpha, requiring complete "
                "adjudication and declared independent common-risk Bernoulli units. "
                "Their pair is not a 1-alpha two-sided confidence interval."
            ),
        },
    }


def _write_atomic(path: Path, payload: str, *, overwrite: bool) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temporary, path)
        else:
            # Linking is atomic and fails if the destination already exists.
            os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="single-event JSON audit manifest")
    parser.add_argument(
        "--output", type=Path, help="write a JSON report (default: stdout)"
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="atomically replace an existing report"
    )
    args = parser.parse_args(argv)
    try:
        if args.overwrite and args.output is None:
            raise ValueError("--overwrite requires --output")
        if args.output is not None and args.input.resolve() == args.output.resolve():
            raise ValueError("input and output must be different files")
        report = audit_manifest(load_manifest(args.input))
        payload = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
        if args.output is None:
            sys.stdout.write(payload)
        else:
            _write_atomic(args.output, payload, overwrite=args.overwrite)
    except (ValueError, TypeError, OSError, UnicodeError, RecursionError) as error:
        print(f"evidence audit error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
