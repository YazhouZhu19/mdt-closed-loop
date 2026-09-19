# v2.2 JSON evidence audit

The runnable interface to `mdt_core.evidence_protocol` validates a declared audit
manifest and writes a deterministic report. It does not modify the controller,
produce natural deployment logs, authenticate source documents, or establish
clinical benefit. The provided example is **F3 constructed verification** and its
M0 status is **`not_assessable`**.

## Run the example

From the repository root, after installing the project and its dependencies:

```sh
python -m pip install -e .
python -m mdt_core.evidence_cli examples/v22_audit_manifest.json --output v22_audit_report.json
```

Omit `--output` to print JSON to standard output. Existing report files are
preserved unless `--overwrite` is explicitly supplied:

```sh
python -m mdt_core.evidence_cli examples/v22_audit_manifest.json --output v22_audit_report.json --overwrite
python -m unittest tests.test_v22_evidence_cli -v
```

Validation or file errors return exit status 2. No report is published until the
entire input has passed validation and analysis. Writes are atomic; a failed
validation preserves an existing report even with `--overwrite`. The output
directory must already exist. Input and output cannot be the same file.

The example threshold `0.05` is an interface illustration, **not a chosen or
preregistered research threshold**. Its ACK identifiers and reference locators
are synthetic placeholders. Generating the JSON report does not convert them
into measurements. The example omits one required ACK, so one of its two
constructed sessions remains indeterminate.

## Input schema

`schema_version` must be `mdt.evidence.audit.v1`. Every object rejects unknown
fields. The reader also rejects duplicate keys, non-finite numbers, invalid enums,
scalar type coercions, malformed JSON, and conflicting session/event records.
JSON booleans must be `true` or `false`, not 0/1 or strings.

| Top-level field | Required | Meaning |
| --- | --- | --- |
| `schema_version` | Yes | Exact schema identifier above |
| `event_type` | Yes | One nonempty event identifier; every record must match |
| `audit_unit` | Yes | Frozen unit, population, eligibility and event definitions |
| `eligible_session_ids` | Yes | Unique **event-specific at-risk** session identifiers |
| `records` | Yes | Audit record array; an omitted at-risk row remains unknown |
| `sources` | Yes | Declared F1/F2/F3 evidence source array |
| `protocol` | Yes | Threshold, confidence level and registration declaration |
| `inventory` | No | Separate overall eligible-session count and reference |
| `bernoulli_assumptions` | No | Explicit independence/common-risk declaration |
| `baseline_independence` | No | Optional baseline review declaration |

Optional objects must be omitted when absent; an explicit `null` is not an object.
Nullable fields inside objects are listed below. Empty arrays are permitted where
they honestly represent absent evidence; required nonempty identifiers and text
must still be provided.

### Audit unit and denominator

`audit_unit` has five required nonempty text fields: `definition`, `population`,
`eligibility_rule`, `event_definition`, and `protocol_reference`.

Despite its historical name, `eligible_session_ids` must already exclude
`NOT_AT_RISK` sessions. The CLI does not infer execution opportunities or STOP
opportunities. Do not use all device sessions as the denominator for an endpoint
that is only possible in a subset.

Optional `inventory` contains required `all_eligible_session_count` (nonnegative
integer) and `reference` (nonempty text). The overall count cannot be smaller than
the at-risk roster. The report separately shows overall eligible, at-risk, and
not-at-risk counts. Overall counts remain `null` if inventory is omitted. These
are supplied declarations; the CLI cannot validate the sampling frame against
real-world records.

### Audit records

Required fields are `session_id`, `event_type`, and `source_id` (nonempty text).
Every record's source must appear in `sources`; records outside the at-risk roster
are rejected. Exact duplicate records are ignored; conflicting duplicates fail.

| Optional field | Type/default |
| --- | --- |
| `verdict` | `confirmed`, `ruled_out`, or `indeterminate`; default `indeterminate` |
| `evidence_references` | Array of distinct nonempty strings; default empty |
| `required_fields_complete` | Boolean; default `false` |
| `requires_execution_ack` | Boolean; default `true` |
| `execution_ack_id` | Nonempty string or `null`; default `null` |
| `no_ack_required_reason` | Nonempty string or `null`; default `null` |

A positive or negative claimed verdict remains indeterminate if required fields,
references, or the required ACK are absent. A protocol-grounded nonempty reason
is required when `requires_execution_ack` is `false`. This waiver is for endpoints
where ACK does not apply; it cannot establish an unconfirmed execution.

### Evidence sources

Required fields: `source_id`, `reference`, `description` (nonempty strings), and
`evidence_class` (one of the exact enum values below).

| Class | Additional conditions |
| --- | --- |
| `F1_empirical_execution` | Requires `real_execution_logs: true`, `natural_uninjected_run: true`, and nonempty `traceable_record_references` |
| `F2_external_evidence` | Requires the external reference; does not imply a known occurrence frequency |
| `F3_constructed_verification` | Requires nonempty `construction_specification`; never a natural occurrence frequency |

Optional booleans `real_execution_logs`, `natural_uninjected_run`, and
`frequency_known` default to false. Optional string arrays
`traceable_record_references` and `derived_from_source_ids` default to empty.
The optional `construction_specification` is a string or null and belongs only
to F3. `frequency_known: true` additionally requires a positive integer
`exposure_denominator` and nonempty `exposure_unit`; otherwise these fields must
be omitted or null. F3 cannot claim a known natural frequency. Actual execution
logs from an injected experiment remain F3. F1 cannot include a construction
specification. F2/F3 cannot declare `natural_uninjected_run: true`.

Source classes are partitioned in the report. M0 rejects a summary that pools F1
with F2/F3. Other unselected source declarations may be present in the manifest;
their presence does not add empirical observations. File references are retained
as metadata; their targets are not read or authenticated by this command.

### Protocol and optional declarations

`protocol` requires `registration_status` (`draft` or `declared_registered`),
`risk_threshold` (number in [0,1]), `registration_reference`, and
`threshold_rationale` (nonempty strings). Optional `alpha` defaults to 0.05 and
must be strictly between 0 and 1. The event and audit unit are inherited from the
manifest, ensuring consistent summary and gate definitions. A draft threshold
can be used for software inspection but never represents a preregistered result.
`declared_registered` remains a caller declaration, not a verified registration.

`bernoulli_assumptions` requires booleans `independent_units` and `common_risk`,
and nonempty text `justification`. Exact bounds are calculated only when both
assumptions are declared true and every at-risk outcome is determinate. Repeated
windows or correlated sessions are not independent merely because IDs differ.

`baseline_independence` requires `baseline_id`, `implementation_reference`,
`requirements_reference` (nonempty strings), and nonempty
`construction_author_ids` (array of distinct strings). Optional `status` is
`pending` (default), `declared_complete`, or `conflicted`; `reviewer_ids` and
`disclosed_conflicts` are string arrays, and `review_reference` is a string or null.
A complete declaration requires distinct reviewers, a review reference, and no
disclosed conflict. A conflicted declaration requires an identified conflict.
The report never labels these declarations as independently verified.

## Reading the output

Output schema is `mdt.evidence.audit-report.v1`. `audit_summary` and `m0_gate` are
the existing protocol helpers' outputs. `declared_sources`, `evidence_partitions`,
`denominators`, `protocol`, and optional `baseline_independence` retain context.
The `verification` flags remain false: software validation is not verification of
source authenticity, registration, review independence, or clinical efficacy.

- `observed_risk` exists only for a fully adjudicated at-risk roster.
- `determinate_risk` describes only the assessable subset; it cannot substitute
  for the unknown risk of the full roster or population.
- `cohort_risk_range` is a logical finite-cohort range, **not a confidence interval**.
- Each exact bound is separately one-sided at `1-alpha`. Their pair is **not** a
  two-sided `1-alpha` interval.
- With zero at-risk sessions, risks, coverage, range and bounds are null.
- Missing natural F1 evidence yields `not_assessable`, not zero failure frequency.
- M0 concerns occurrence only. It does not establish harm or clinical consequence.

The supplied example and interface tests validate software behavior only. A
real M0 audit still needs authentic natural uninjected logs, an appropriate
event-specific sampling frame, an adjudication protocol, and defensible analysis
assumptions. See [V22_EVIDENCE_PROTOCOL.md](V22_EVIDENCE_PROTOCOL.md) for the
underlying bookkeeping contract.
