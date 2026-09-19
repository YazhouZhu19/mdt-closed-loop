"""Explicit research profiles; legacy defaults are retained for ablations."""

from dataclasses import replace

from .config import DEFAULT, Config


def v21_config(base: Config = DEFAULT) -> Config:
    """Enable v2.1 contracts and normalized PI for public-data simulation.

    Does not enable or invent learned policies. Supply calibrated, pinned
    learning settings in ``base``. ACK back-calculation remains opt-in because
    a blended A4 output cannot be attributed to both PI states automatically.
    """
    return replace(
        base,
        execution=replace(base.execution, enabled=True),
        control=replace(base.control, formulation="normalized"),
        learning=replace(base.learning, v21_gates=True, state_fusion="select"),
    )
