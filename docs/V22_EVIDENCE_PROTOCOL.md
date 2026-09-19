# v2.2 evidence protocol helpers

`mdt_core.evidence_protocol` provides bookkeeping and analysis checks for the
publication plan. It does **not** supply execution data, authenticate a source,
perform an independent baseline review, run the four-arm experiment, or establish
clinical effects. It does not change the controller or execution runtime.

## Three-state audit

Freeze an `AuditUnit` and an event-specific at-risk session roster **before**
examining outcomes. The caller must already have filtered `eligible_session_ids`
to this at-risk roster. Do not pass `NOT_AT_RISK` sessions. An external inventory
must separately report the counts of all eligible sessions and at-risk sessions;
`eligible_n` in this helper counts only the supplied event-specific at-risk roster.
The helper implements three adjudication states, not a fourth eligibility state,
and does not automatically recognize STOP opportunities or other risk opportunities.

Supply one `AuditRecord` per session and event type. A session-level event denotes
at least one confirmed occurrence; many boundaries or duplicate rows must not
increase the session denominator. Exact duplicates are ignored and conflicting
duplicates must be reconciled explicitly. Other event types are evaluated
separately.

`confirmed`, `ruled_out`, and `indeterminate` are distinct. A claimed positive or
negative verdict becomes indeterminate when required fields, evidence references,
or a required execution ACK are missing. An eligible session with no audit row is
also indeterminate. A protocol may explicitly declare that an ACK does not apply
to a different endpoint, such as a submission event; the reason must be supplied.
That option must not be used to relabel an unconfirmed execution as successful.

`summarize_audit` returns:

- Eligible and determinate counts, all three outcome counts, and coverage.
- The observed risk only when every eligible session is determinate.
- `determinate_risk = confirmed/determinate_n`, a descriptive rate conditional on
  the assessable subset. It is not an estimate for the full roster or target
  population when unknown outcomes remain. It is `None` when `determinate_n = 0`.
- The finite-cohort logical range `[confirmed/n, (confirmed+unknown)/n]`. This is
  **not a confidence interval** and describes only the audited cohort.
- Optional exact Clopper–Pearson one-sided lower and upper risk bounds, each at
  confidence `1-alpha`. They require complete adjudication and an explicit
  `BernoulliAssumptions` declaration of independent units with a common risk.
  Displaying both does not make their pair a `1-alpha` two-sided interval.
- `None` for risk, range, bounds and coverage when the eligible denominator is 0.

Different windows, seeds or correlated sessions of one participant do not become
independent merely because they have different IDs. A predeclared cluster-level
endpoint may instead aggregate their evidence, with its sampling unit documented.
The helpers do not decide whether the independence assumption is defensible.

## Provenance and fault classes

`EvidenceSource` records declared provenance. It validates metadata, not the
truth of an uploaded archive or reference.

| Class | Required source information | Permissible interpretation |
|---|---|---|
| F1 empirical execution | Actual execution-log declaration, explicit `natural_uninjected_run=True`, source reference, traceable record locators | Audit the represented natural execution workflow, subject to provenance authentication and sampling validity |
| F2 external evidence | External reference and description; explicit `frequency_known` | Support the stated external phenomenon; do not invent a natural occurrence frequency |
| F3 constructed verification | Construction specification and reference | Check behavior under the stated injection; no claim of natural occurrence |

A known frequency also requires a positive exposure denominator and its unit.
Actual device logs can come from injected experiments, so `real_execution_logs`
alone does not qualify as F1. F1 also requires an explicit declaration that the
run was natural and uninjected. Without that declaration it cannot enter F1.
Actual execution during a constructed injection remains F3; F2/F3 cannot set
`natural_uninjected_run=True` to claim natural M0 provenance. These are strict
metadata declarations, not verification of external source authenticity.

An injected schedule is not an empirical occurrence frequency. A constructed
scenario derived from F1 stays F3. `partition_evidence` preserves separate classes;
there is no pooled-benefit function. The M0 gate rejects a summary mixing F1 with
F2/F3 and cannot use an F2/F3-only summary as empirical execution evidence.

## M0 occurrence gate

`M0Protocol` freezes the event definition, audit unit, risk threshold, confidence
level, registration reference and threshold rationale. `evaluate_m0` reports:

| Status | Meaning |
|---|---|
| `not_assessable` | No qualifying F1 execution-log audit, or no eligible sessions |
| `inconclusive` | Unknown outcomes, undeclared independent/common-risk assumptions, or bounds spanning the threshold |
| `threshold_exceeded` | The one-sided exact lower bound exceeds the predeclared threshold |
| `below_threshold` | The one-sided exact upper bound lies below the predeclared threshold |

Missing empirical evidence and an inconclusive audit do not refute a hypothesis.
The gate addresses occurrence alone. Neither frequent events nor rare events
establish consequential harm; `consequence_established` is always false because a
separate, predeclared consequence endpoint and evidence are required. This small
helper is not a complete automatic publication decision procedure.

## Baseline construction and review

`BaselineIndependenceDeclaration` defaults to `pending`. A `declared_complete`
record requires the implementation and requirements references, named construction
authors, disjoint named reviewers, a review record and no disclosed conflict. A
known conflict can be reported as `conflicted`. These are user-supplied declarations,
not verified external peer review; completing fields cannot prove independence.

## Verification

```sh
python -m pip install -e ".[dev]"
python -m unittest tests.test_v22_evidence_protocol -v
```

The 46 tests cover missing ACKs and fields, missing sessions, duplicate and
conflicting audits, zero denominators, exact-bound inversion, assumptions,
provenance class separation, M0 statuses and pending/conflicted review declarations.
They are software tests, not empirical results for the paper.
