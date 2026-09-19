"""Portable comparison of the immutable pre-change synthetic replay fixture."""

from __future__ import annotations

import math
import unittest

FEATURES = frozenset({"hf_power", "rmssd", "sd1", "scl_slope", "scr_rate"})
REL_TOL = 1e-12
ABS_TOL = 1e-12


def _is_derived_feature(path: tuple[str | int, ...]) -> bool:
    return (
        len(path) == 3
        and path[0] == "physio"
        and isinstance(path[1], int)
        and path[2] in FEATURES
    ) or (
        len(path) == 4
        and (path[0], path[2]) in {("physio", "z"), ("states", "z_scores")}
        and isinstance(path[1], int)
        and path[3] in FEATURES
    )


def assert_legacy_trace_equal(
    case: unittest.TestCase,
    actual: object,
    expected: object,
    path: tuple[str | int, ...] = (),
) -> None:
    """Allow roundoff only in the declared physiological feature fields.

    Structure, types, clocks, state estimates, decisions and engine commands
    remain exact. Same-environment replay comparisons use assertEqual directly.
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
        if _is_derived_feature(path):
            case.assertTrue(
                math.isclose(actual, expected, rel_tol=REL_TOL, abs_tol=ABS_TOL),
                f"{location}: {actual!r} != {expected!r} within "
                f"rel_tol={REL_TOL}, abs_tol={ABS_TOL}",
            )
        else:
            case.assertEqual(actual, expected, f"{location}: exact value changed")
    else:
        case.assertEqual(actual, expected, f"{location}: exact value changed")
