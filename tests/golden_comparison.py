"""Portable comparison of the immutable pre-change synthetic replay fixture."""

from __future__ import annotations

import math
import unittest

FEATURES = frozenset({"hf_power", "rmssd", "sd1", "scl_slope", "scr_rate"})
STATE_VALUES = frozenset({"arousal", "confidence", "uncertainty"})
MUSIC_VALUES = frozenset(
    {
        "dynamics",
        "harmonic_brightness",
        "register",
        "reverb_depth",
        "rhythmic_accent",
        "tempo",
    }
)
CONTINUOUS_FIELDS = {
    "physio": FEATURES | STATE_VALUES,
    "states": STATE_VALUES,
    "engine": MUSIC_VALUES,
    "music": frozenset(
        {
            "target",
            "estimated",
            "error",
            "state_uncertainty",
            "control_output",
            "control_scale",
            "trajectory_speed",
        }
    ),
}
REL_TOL = 1e-12
ABS_TOL = 1e-12


def _is_continuous_field(path: tuple[str | int, ...]) -> bool:
    if len(path) not in (3, 4) or not isinstance(path[1], int):
        return False
    if len(path) == 3:
        return path[2] in CONTINUOUS_FIELDS.get(str(path[0]), frozenset())
    if (path[0], path[2]) in {("physio", "z"), ("states", "z_scores")}:
        return path[3] in FEATURES
    return (path[0], path[2]) == ("music", "params") and path[3] in MUSIC_VALUES


def assert_legacy_trace_equal(
    case: unittest.TestCase,
    actual: object,
    expected: object,
    path: tuple[str | int, ...] = (),
) -> None:
    """Allow cross-platform roundoff only in declared continuous fields.

    Structure, types, clocks, discrete decisions and zero/sign transitions
    remain exact. A separate CI check compares the old/new implementations
    exactly in the same environment, including every continuous output.
    """
    location = "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in path)
    case.assertIs(type(actual), type(expected), f"{location}: type changed")
    if isinstance(expected, dict):
        assert isinstance(actual, dict)
        case.assertEqual(actual.keys(), expected.keys(), f"{location}: keys changed")
        for key in expected:
            assert_legacy_trace_equal(case, actual[key], expected[key], (*path, key))
    elif isinstance(expected, list):
        assert isinstance(actual, list)
        case.assertEqual(len(actual), len(expected), f"{location}: length changed")
        for i, (a, b) in enumerate(zip(actual, expected)):
            assert_legacy_trace_equal(case, a, b, (*path, i))
    elif isinstance(expected, float):
        assert isinstance(actual, float)
        case.assertTrue(
            math.isfinite(actual) and math.isfinite(expected),
            f"{location}: nonfinite value",
        )
        if _is_continuous_field(path) and actual != 0 and expected != 0:
            case.assertEqual(actual > 0, expected > 0, f"{location}: sign changed")
            case.assertTrue(
                math.isclose(actual, expected, rel_tol=REL_TOL, abs_tol=ABS_TOL),
                f"{location}: {actual!r} != {expected!r} within "
                f"rel_tol={REL_TOL}, abs_tol={ABS_TOL}",
            )
        else:
            case.assertEqual(actual, expected, f"{location}: exact value changed")
    else:
        case.assertEqual(actual, expected, f"{location}: exact value changed")
