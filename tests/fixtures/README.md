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

Cross-platform FFT/reduction roundoff also propagates through continuous
state estimates and control outputs. The comparator uses
`math.isclose(rel_tol=1e-12, abs_tol=1e-12)` only for fields explicitly declared
in [`golden_comparison.py`](../golden_comparison.py):

- The five physiological features (`hf_power`, `rmssd`, `sd1`, `scl_slope`,
  `scr_rate`) and their `physio.z` / `states.z_scores` values.
- Arousal, confidence and uncertainty.
- Continuous music telemetry and six continuous engine parameters.

All types, dictionary keys, list lengths, timestamps, quality/decision labels,
layer masks, zero/sign transitions and undeclared fields still compare exactly;
nonfinite floats always fail. The tolerance is an explicit numerical boundary,
not a clinical or control-effect threshold.

CI additionally archives the immutable pre-learning commit and runs old/new
implementations against the same synthetic events in the **same environment**.
That comparison is exact for every field, including all continuous state,
control and engine outputs. It runs independently of the portable fixture
comparison; platform tolerance cannot hide a new-versus-old regression there.

This portability change follows a fresh export of the pre-learning commit:
old and current code produced identical replays, while both differed from
this fixture in 13 feature values per arm (maximum absolute difference
`6.252776074688882e-13`). See
[`v22_pre_learning_replay_comparison.json`](../../docs/validation/v22_pre_learning_replay_comparison.json).
Linux CI also found propagated differences such as estimated arousal
`0.6703649367419056` versus `0.6703649367418995` and engine dynamics
`0.504374282940508` versus `0.5043742829405083`. See
[the diagnostic run](https://github.com/YazhouZhu19/mdt-closed-loop/actions/runs/35451018169).
Nine comparator tests verify observed roundoff and rejection of `1e-8`
feature/control perturbations, exact-field changes, zero/sign changes,
schema/type changes and nonfinite values.
