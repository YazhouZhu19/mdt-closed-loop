# Strong B1 and common-audit specification

Status: **draft requirements, not a frozen implementation or independent review**.
This specification implements v2.2 S5 as a reviewable design. It does not claim that the required independent engineer or reviewer has already participated. Do not relabel a contract ablation as B1.

## Conditions to freeze before testing

| Item | Requirement |
| --- | --- |
| Models | Identical enabled A2/A3/A4 artifacts and training data in B1/B2/H; initial A5 uses frozen rules |
| Constraints | Common parameter bounds, STOP capability and hard guard; no deliberate removal of reasonable existing protections |
| Input and computation | Same source streams for deterministic replay; same initial conditions and exogenous disturbances for reactive simulation; equal compute and tuning budgets |
| Baseline design | Independent common-practice asynchronous controller with documented timestamps, timeout, queue, state and cancellation semantics |
| Reasonable protections | B1 may retain ACKs, deduplication, timestamp checks or other capabilities already justified by its specification |
| Contract comparison | Publish an actual capability matrix; only mechanisms that differ can explain B2-B1 effects |
| Review independence | Declare construction authors, fault-library authors, information they could access, freezing date/hash and reviewer conflicts |
| Timing of freeze | Freeze implementation and tuning before final F3 outcomes are disclosed; keep development and final faults separate |

If B1 already satisfies an MDT contract, retain that capability. A small or uncertain increment is a valid result. Do not weaken the baseline to manufacture a difference. Single-mechanism removal from H is a separate F3 ablation.

## Common external observer

The observer measures the same actual-output, timing, authority and STOP rules for every condition. It may record proposal lineage for analysis, but those audit-only fields must not feed B1 control decisions. Missing MDT-specific internal fields cannot by themselves constitute an illegal execution.

Freeze each event definition and evidence requirement:

- **Stale execution:** source observation end, decision creation, frozen TTL, common clock and confirmed execution. Rejected stale suggestions are not failures.
- **Repeated incremental compounding:** original intent, fixed target and boundary/ACK sequence. Legitimate progress toward a persistent absolute target across new boundaries is not duplicate compounding.
- **STOP race:** predeclared STOP linearization and draining/cancellation semantics, commit order and actual execution or device confirmation.
- **Unconfirmed-state promotion:** complete command/ACK stream and evidence that software declared a physical state without the required basis. A missing file or ACK alone leaves physical execution unknown.

For each event retain the all-eligible count, event-specific risk set, assessable count, unknown count, event count and exposure. Reconcile duplicate session/event judgments before analysis. Save unknown reasons rather than deleting failed or crashed sessions.

## Comparison and inference

- Primary H1: B2-B1, separately for F1, F2 and F3. Unknown execution is a separate outcome.
- Control cost: `RMSE_B2 - RMSE_B1`; coverage loss: `Coverage_B1 - Coverage_B2`; latency cost: `P99_B2 - P99_B1`. Larger values mean greater cost.
- Freeze meaningful differences, noninferiority limits and the multiplicity rule before final outcomes. If limits lack a defensible basis, report effects and intervals without claiming noninferiority.
- E_hier: H-B2, exploratory. B0 is a matched same-contract learning-disabled comparator; the legacy research arms are not renamed B0/B1/B2/H.
- In simulation, continue evaluating the common full horizon after HOLD/STOP using the prespecified stopped-input policy. Do not improve error by deleting difficult time intervals.

## Freeze record template

Required fields for the future signed-off record:

```text
spec_version, status, repository_commit, configuration_hash
model_artifact_hashes, simulator_version, observer_version
capability_matrix, compute_budget, tuning_budget
construction_authors, fault_authors, accessible_information
freeze_time, final_fault_release_time
reviewers, conflicts, review_record
primary_endpoint, risk_set_rule, statistical_unit
missingness_rule, comparison_family, limits_and_rationale
```

These fields form a specification, not an implemented automatic certification service. `BaselineIndependenceDeclaration` stores review declarations only. Completing its fields cannot prove organizational or intellectual independence.
