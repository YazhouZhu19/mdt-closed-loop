# Pre-change synthetic replay fixture

`legacy_trace.json` was captured before edits from commit
`bbb66823d85979848069615ac1e486ab1435362a`, using the events defined in
`tests/learning_scenarios.py` (Python 3.12.4, NumPy 1.26.4, SciPy 1.13.1).

It contains only generated mathematical EDA/RR inputs and their derived outputs:
four study arms, six sensor windows, explicit phrase events, one contact-loss
window, state/physiology/music records, and the NullEngine history. It contains
no participant data, persistent session identifiers, or wall-clock timestamps.

The fixture remains immutable. Do not regenerate it from the modified
implementation to make a failed comparison pass. New policy audit fields are
intentionally excluded.

Cross-platform FFT/reduction roundoff is allowed only in the five derived
physiological features (`hf_power`, `rmssd`, `sd1`, `scl_slope`, `scr_rate`),
their `physio.z` values, and `states.z_scores`. The comparator uses
`math.isclose(rel_tol=1e-12, abs_tol=1e-12)` for these fields. All types,
dictionary keys, list lengths, timestamps, state estimates, quality/decision
labels, controller outputs and engine parameters still compare exactly;
nonfinite floats always fail. Same-environment replay tests remain exact.

This portability change follows a fresh export of the pre-learning commit:
old and current code produced identical replays, while both differed from
this fixture in 13 feature values per arm (maximum absolute difference
`6.252776074688882e-13`). See
[`v22_pre_learning_replay_comparison.json`](../../docs/validation/v22_pre_learning_replay_comparison.json).
Negative-control tests verify that feature perturbations of `1e-8`, any
change to an exact field, schema/type changes and nonfinite values fail.
