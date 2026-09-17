# Pre-change synthetic replay fixture

`legacy_trace.json` was captured before edits from commit
`bbb66823d85979848069615ac1e486ab1435362a`, using the events defined in
`tests/learning_scenarios.py` (Python 3.12.4, NumPy 1.26.4, SciPy 1.13.1).

It contains only generated mathematical EDA/RR inputs and their derived outputs:
four study arms, six sensor windows, explicit phrase events, one contact-loss
window, state/physiology/music records, and the NullEngine history. It contains
no participant data, persistent session identifiers, or wall-clock timestamps.

The regression compares all original numerical fields and engine parameters
exactly; new policy audit fields are intentionally excluded. Do not regenerate
the fixture from the modified implementation to make a failed comparison pass.
