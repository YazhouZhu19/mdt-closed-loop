## Summary

Describe the problem and the resulting behavior.

## Validation

- [ ] `ruff check mdt_core tests demo.py`
- [ ] `mypy --no-site-packages --ignore-missing-imports mdt_core tests demo.py`
- [ ] `python -W error -m unittest discover -s tests -v`
- [ ] `python demo.py` when orchestration, estimation, control, or recording changed

List any additional tests or simulations:

## Safety and research impact

- [ ] No real participant data, credentials, or private study material is included.
- [ ] Synthetic fixtures are identified and isolated in temporary directories.
- [ ] Signal units, timing, and missing-data behavior are documented if changed.
- [ ] Low-confidence fallback and music-boundary constraints remain intact.
- [ ] Safety aborts still do not count as completed treatment.
- [ ] Research-arm or outcome changes are explicitly described.
- [ ] No synthetic result is presented as clinical evidence.

## Compatibility

Describe public API, configuration, record-schema, or migration impact.
