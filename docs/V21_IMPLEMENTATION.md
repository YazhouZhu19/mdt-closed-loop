# v2.1 implementation profile

This revision implements an **opt-in public-data replay and simulation profile**
for the v2.1 research proposal. It retains the physiological feedback loop and
the existing compact learning models. It does not establish physiological
stability, clinical efficacy, or calibrated hearing protection.

The default configuration keeps the legacy execution path, PI formulation and
state blending for reproducibility. Existing experiment arms are unchanged.
The new execution profile currently supports `Arm.FULL_LOOP` only; use explicit
profile variants for architecture comparisons instead of relabeling the other
experiment arms.

## Enable the profile

```python
from mdt_core.config import DEFAULT
from mdt_core.profiles import v21_config

cfg = v21_config(DEFAULT)
assert cfg.execution.enabled
assert cfg.control.formulation == "normalized"
assert cfg.learning.v21_gates
assert cfg.learning.state_fusion == "select"
assert not cfg.control.ack_backcalculation
```

`v21_config(base)` does not load models or enable learning. To use a learned
module, first supply its independently calibrated artifact, the corresponding
enable flag, a trial ID, deployment mode, and pinned content hash in `base`.
Then call `v21_config(base)`. Baseline-only operation is a valid starting point.
Freeze the complete configuration and artifact manifest before evaluation.

Run the standalone contract demonstration from the repository root:

```bash
python examples/v21_contract_demo.py --output docs/validation/v21_contract_demo.json
```

The script uses `v21_config()`, `ExecutionGateway` and `NullEngine` with a fixed
`demo` epoch. It checks latest-target replacement, boundary deduplication,
ACK-based state updates, source-age expiry despite a newly created decision,
and finite HOLD followed by terminal STOP. Its 19 checks use only constructed
numbers and explicit independent simulation-clock ticks. The shortened TTL and
HOLD values illustrate the mechanism and are not recommended research limits.
The saved [JSON trace](validation/v21_contract_demo.json) contains the complete
event sequence; it is a software-contract demonstration, not an effect study.

The numerical defaults are software research settings, not recommended
physiological, clinical, or acoustic limits. In particular, normalized PI gains
need their own simulation evaluation. Merely changing the integral time unit
does not preserve a legacy controller unless the integral initial value, clamp,
and integral gain are transformed together.

## What is implemented

| Area | Implemented behavior | Important boundary |
| --- | --- | --- |
| Execution contract | Immutable absolute target; observation and decision ages; epoch, sequence and parent-ACK checks; final boundary projection; serialized STOP/submit ordering | One atomic music-vector channel, not a distributed per-channel service |
| Execution facts | `NullEngine.submit(command)` returns an `ExecutionAck`; only validated matching ACKs update authoritative music state | Synchronous simulation adapter; sending a hardware message is not execution confirmation |
| Duplicate handling | A command ID identifies one fixed projected payload; reusing that ID with another payload is rejected | A persistent target may progress at later boundaries through new command IDs |
| Temporal failure | Expired or unreliable input cancels pending targets and enters HOLD; maximum HOLD duration leads to STOP | The simulator must continue supplying watchdog ticks independently of sensor/music events |
| Recovery | Fresh observations establish baseline recovery before learning is reconsidered; quarantined modules put the session in DEGRADED | Session-level quarantines survive gate reset and signal recovery |
| State Agent | Optional whole-branch selection, preserving the selected branch's mean, variance and confidence | No claim that branch selection itself establishes calibrated post-selection uncertainty |
| Continuous authority | Minimum of module/domain/session caps; normalized disagreement; slow increase and immediate decrease of influence | Weight ramping alone is not a proof of bumpless total output or stability |
| PI | Explicit legacy and normalized variants; conditional integration and time-based inactive leakage | Changing leakage/conditional integration changes behavior and requires evaluation |
| ACK compensation | Explicit single-controller tempo-command registration and acknowledgement API | Not automatically connected to the mixed dual-PI Session output |
| Music protection | Frozen software dynamics ceiling and change budget in the execution path | Normalized dynamics is not dB SPL; no real device calibration has been performed |
| Audit | Proposals, gates, rejection reasons, accepted targets, commands and ACK events | Logs are evidence of software behavior, not a causal estimate of user benefit |

## Timing and execution

All v2.1 times use one monotonic, session-relative simulation clock. Every input
window must explicitly set `RawWindow.observed_end_t` to its original observation
end time, even when delivery is synchronous. A missing source timestamp enters
HOLD; only the legacy profile permits `None` under its synchronous-time
convention. Repackaging an old window must not refresh its age.

The v2.1 Session deduplicates source-end timestamps before estimator updates.
Replaying a source window cannot advance the estimator, planner or integral a
second time. For mixed fast/slow input streams with the same timestamp, deliver
the slow/full observation first; the later equal-source event is rejected as a
duplicate. Data adapters must preserve this ordering instead of manufacturing
different timestamps to bypass deduplication.

At decision and boundary time, both ages must be finite, nonnegative and within
their frozen TTLs. The source observation must not end after the decision was
created. Freshness is necessary, but does not override epoch, sequence,
parent-ACK, deployment or STOP checks.

An absolute tempo target is based on the parent decision's confirmed music:

```text
tempo_target = parent_ack_tempo + 12 * accepted_control
```

The gateway projects that fixed target against the current confirmed state and
the new boundary's budget. It does not repeatedly add the same control increment
to each newly confirmed tempo. Repeated delivery of the same command is
idempotent. A new boundary can issue a different command toward the same target;
that intentional progress is not duplicate execution.

Use the simulator's event loop to call `Session.watchdog(t)` even when there are
no new sensor windows and no music-boundary events. For example, a simulator can
merge regularly scheduled watchdog ticks with its observation and boundary
event streams, all ordered by the same clock. Calling watchdog only from the
sensor callback cannot enforce a sensor-loss deadline after the sensor stops.

`Session.watchdog` is a deterministic polling interface, not an independent OS
process, audio-device watchdog, or hard real-time guarantee. `NullEngine` does
not generate or measure physical audio. A device integration must implement
its own bounded transport, query/ACK behavior, stop acknowledgement, and
measurement of actual audio termination.

## Learning gates and isolation

`LearningConfig` adds these frozen fields:

| Field | Default | Meaning |
| --- | --- | --- |
| `v21_gates` | `False` | Enables the new gate semantics |
| `state_fusion` | `"blend"` | `"select"` is allowed with v2.1 gates |
| `module_cap`, `domain_cap`, `session_cap` | `1.0` each | Upper bounds combined by minimum |
| `ramp_up_per_s` | `0.10` | Maximum continuous weight increase per second |
| `distance_epsilon` | `1e-9` | Positive denominator floor for restricted residuals |

For L3 control, disagreement is divided by the frozen full control range,
`2 * cfg.control.output_clamp` (two units only with the default clamp of one).
Trajectory and music comparisons use their existing per-parameter normalized
distances. Restricted continuous blending satisfies the normalized deviation
bound; zero disagreement never causes division by zero.

The first valid proposal in a continuous channel establishes its ramp clock and
has zero influence. Later valid proposals can increase influence with elapsed
time. A failed gate immediately sets influence to zero. Recovery must pass the
entry reliability threshold again and receives no ramp credit for unavailable
time. `LearningRuntime.suspend()` clears gate history but preserves
`LearningRuntime.quarantined`.

Whole-branch A2 selection has different semantics: it uses a boolean permission
decision, reliability hysteresis, calibration and disagreement checks. It selects
the entire learned state only when all three permission caps are exactly one;
a fractional cap selects the baseline. In restricted mode the full branch must
meet the residual bound. Selection does not blend means or variances and does
not apply a fractional weight ramp. The gate's confidence threshold is an
eligibility check, not a probability that the selected state is correct.

A3 is a one-shot trajectory choice. It is gated, normalized and capped but is
not subjected to a multi-event influence ramp: otherwise its only attempt would
always receive zero weight. The deterministic reference planner governs the
subsequent trajectory progression.

Timeouts, inference exceptions, invalid outputs, version/context errors and
uncalibrated artifacts quarantine the affected module for the session. Its
future callbacks are skipped. Transient OOD and low reliability revoke current
influence without automatically creating a permanent quarantine. For the state
filter, an unsupported update history remains permanently unavailable because
its posterior-history calibration no longer applies. These are software
failure policies, not a claim to recover a statistically validated filter after
arbitrary missing data.

`LearningRuntime.taste(...)` returns the chosen absolute `MusicParams` target,
or the rule-generated baseline target. It also retains the legacy grammar
request for compatibility. The v2.1 Session sends the returned target to its
gateway and clears grammar pending work. The return value must never be logged
as an already executed action.

## ACK back-calculation is an explicit integration boundary

The PI API exposes `register_tempo_command(...)` and
`acknowledge_tempo(...)`. A command binds its own parent tempo, mapping scale,
control output, reliability, integral gain and integration interval. Matching
ACKs are consumed once. Unknown, duplicate, stale or reversed-time ACKs are
rejected. Missing ACKs do not create a fictitious executed value.

For the documented single-controller mapping, the residual uses a
dimensionless control quantity:

```text
u_ack = (actual_tempo - command_parent_tempo) / tempo_per_unit
integral_correction = (decision_dt / tracking_time) * (u_ack - u_cmd) / (q * Ki)
```

Zero gain or ineffective reliability cannot be inverted. Both time quantities
in the correction are measured in seconds. The complete Session mixes two PI
outputs and can modify music through preference ranking and projection; it
therefore does **not** automatically send the same execution residual into both
integral states. `ack_backcalculation` remains off in `v21_config` until an
explicit attribution design is implemented and tested.

## Current limits and validation

- A restart creates a new epoch; resuming a persisted running session is not
  supported. The existing one-attempt planning latch is session-local. Do not
  claim durable exactly-once planning across a resumed process.
- The gateway serializes its in-process operations. This is not a distributed
  consensus, signed trust chain, or process-independent watchdog.
- The software dynamics limits do not include calibrated sound-pressure
  measurements, hardware limiters, participant screening, or ethics approval.
- Public records can validate preprocessing and frozen estimators. Recorded
  physiology cannot respond counterfactually to a new music policy. Closed-loop
  effect claims require an action-responsive simulator or a future approved
  prospective study, with their different evidence limits reported.
- Branch selection, normalized PI and the new execution contract remain
  experimental choices. Report coverage, failed/rejected commands, delays and
  fixed-reference control error together; zero illegal commands alone is not
  evidence of a useful controller.

The targeted tests include normalization and cap invariants, zero disagreement,
weight recovery, branch variance preservation, session quarantine, one-shot A3
behavior, command/ACK identity, stale timestamps, HOLD, STOP and PI variants.
Run the repository test suite in a recorded NumPy/SciPy environment. Exact
legacy floating-point snapshots are dependency-sensitive; distinguish numerical
roundoff from changed behavior and do not silently regenerate historical
fixtures. Record the actual test run and remaining limitations with each
release; this document does not invent benchmark or participant results.

### Legacy replay audit (2026-09-19)

The targeted learning/gate run covered 82 test methods. The 12 new v2.1 gate
tests passed. One historical golden-replay method produced four failing arm
subtests; the other 81 methods passed. The fixtures and their strict assertions
were not modified.

To distinguish regression from environment effects, commit
`5285377da14897ff2275b5c0f0918e7874279c9f` was exported with `git archive` to a
separate temporary directory. Its unmodified replay and the revised worktree's
legacy-profile replay ran under the same Python executable, NumPy 1.26.4 and
SciPy 1.13.1. **All four arms were exactly equal between HEAD and the revised
worktree, with zero differing fields.** The historical fixture differed from
both runs in 13 floating-point feature values per arm, with a maximum absolute
difference of `6.252776074688882e-13`.

This establishes that the observed golden failures also occur before these
changes in this environment. It does not justify silently rewriting the fixture
or claiming the strict golden test passed. The exact comparison, dependency
versions, baseline commit and per-arm replay hashes are saved in
[V21_LEGACY_REPLAY_VALIDATION.json](V21_LEGACY_REPLAY_VALIDATION.json).

### Final validation

All 74 newly added tests passed (20 execution, 21 PI, 12 gate, 21 metrics). The full suite ran 198 methods: 197 passed; the historical golden method has four pre-existing failing arm subtests as detailed above. Ruff and diff whitespace checks passed. See [the final test summary](validation/v21_test_summary.json) and the saved logs.
