# Contributing

Thank you for helping improve this research prototype. Contributions should preserve reproducibility, participant safety boundaries, and a clear distinction between software correctness and clinical evidence.

## Before opening a change

- Search existing issues before creating a duplicate.
- Use an issue for substantial algorithm, API, or study-design changes.
- Do not submit participant data, credentials, private study documents, or third-party copyrighted material.
- Do not describe a synthetic or offline result as clinical validation.
- Keep real vendor SDK calls behind the `MusicEngine` interface.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Required checks

```bash
ruff check mdt_core tests demo.py
mypy --no-site-packages --ignore-missing-imports mdt_core tests demo.py
python -W error -m unittest discover -s tests -v
```

Run `python demo.py` for changes that affect orchestration, state estimation, control, recording, or package imports.

## Change guidelines

### Signal processing

- Document units, sampling assumptions, window length, missing-data behavior, and physiological filters.
- Add deterministic synthetic tests for normal, noisy, lost, and non-finite input.
- Compare replacement algorithms against an independently validated implementation before claiming improved validity.

### State and control algorithms

- State the equation and parameter units.
- Preserve bounded output, low-confidence fallback, and audio-boundary constraints.
- Add long-running bounded simulations and edge-case tests.
- Treat gain or threshold changes as study configuration changes, not cosmetic refactors.

### Safety or outcome logic

- Safety escalation must remain fail-safe and must not count aborted treatment as completed dose.
- Add tests for ordering and side effects, not only returned values.
- Clinical or regulatory claims require separate evidence and review outside a code pull request.

### Tests and fixtures

- Identify all generated physiology as synthetic.
- Use fixed random seeds.
- Write artifacts only to `tempfile.TemporaryDirectory`.
- Never commit generated session JSON or real participant records.

## Pull requests

A pull request should include:

- a concise problem statement;
- the proposed behavior and its limitations;
- tests added or updated;
- commands used for validation;
- compatibility or migration notes;
- disclosure of any AI-generated code that requires special review under the contributor's organization policy.

Keep unrelated formatting or refactoring out of focused fixes. Maintainers may request additional evidence for changes that affect safety, signal validity, allocation, or outcome rules.

## Licensing

This project is licensed under the Apache License 2.0. Unless explicitly stated
otherwise, an intentionally submitted contribution is provided under the same
license in accordance with Section 5 of the license. Contributors must have the
right to submit their work and must identify third-party material and its
applicable license.
