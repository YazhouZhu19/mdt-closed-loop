"""Negative controls for the narrow floating-point fixture tolerance."""

import copy
import json
import math
import unittest
from pathlib import Path

from tests.golden_comparison import assert_legacy_trace_equal


class GoldenComparisonTests(unittest.TestCase):
    def setUp(self):
        self.golden = json.loads(
            (Path(__file__).parent / "fixtures/legacy_trace.json").read_text()
        )["A_full_loop"]

    def test_reference_matches_itself(self):
        assert_legacy_trace_equal(self, self.golden, self.golden)

    def test_observed_platform_roundoff_is_accepted(self):
        actual = copy.deepcopy(self.golden)
        actual["physio"][1]["hf_power"] = 174.10540527500487
        actual["physio"][5]["z"]["rmssd"] += 1e-15
        actual["states"][5]["z_scores"]["sd1"] += 1e-15
        assert_legacy_trace_equal(self, actual, self.golden)

    def test_feature_changes_above_tolerance_are_rejected(self):
        for section, nested in (
            ("physio", None),
            ("physio", "z"),
            ("states", "z_scores"),
        ):
            with self.subTest(section=section, nested=nested):
                actual = copy.deepcopy(self.golden)
                row = actual[section][1]
                if nested:
                    row = row[nested]
                row["hf_power"] += 1e-8
                with self.assertRaises(AssertionError):
                    assert_legacy_trace_equal(self, actual, self.golden)

    def test_clock_state_control_and_engine_remain_exact(self):
        for section, field in (
            ("states", "t"),
            ("states", "arousal"),
            ("physio", "confidence"),
            ("physio", "uncertainty"),
            ("music", "control_output"),
            ("engine", "tempo"),
        ):
            with self.subTest(section=section, field=field):
                actual = copy.deepcopy(self.golden)
                value = actual[section][1][field]
                actual[section][1][field] = (
                    value + 1
                    if isinstance(value, int)
                    else math.nextafter(value, math.inf)
                )
                with self.assertRaises(AssertionError):
                    assert_legacy_trace_equal(self, actual, self.golden)

    def test_structure_and_discrete_decisions_remain_exact(self):
        for mutation in ("missing_key", "extra_key", "missing_row", "quality", "layer"):
            with self.subTest(mutation=mutation):
                actual = copy.deepcopy(self.golden)
                if mutation == "missing_key":
                    del actual["physio"][1]["hf_power"]
                elif mutation == "extra_key":
                    actual["physio"][1]["new_feature"] = 0.0
                elif mutation == "missing_row":
                    actual["engine"].pop()
                elif mutation == "quality":
                    actual["physio"][1]["quality"] = "lost"
                else:
                    actual["engine"][1]["layer_mask"] = 3
                with self.assertRaises(AssertionError):
                    assert_legacy_trace_equal(self, actual, self.golden)

    def test_equal_valued_different_types_are_rejected(self):
        for actual, expected in ((True, 1), (1, 1.0), (1.0, 1), ([1], (1,))):
            with (
                self.subTest(actual=actual, expected=expected),
                self.assertRaises(AssertionError),
            ):
                assert_legacy_trace_equal(self, actual, expected)

    def test_nonfinite_values_are_rejected_even_if_identical(self):
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                for expected in (value, 1.0):
                    with self.assertRaises(AssertionError):
                        assert_legacy_trace_equal(
                            self,
                            {"physio": [{"hf_power": value}]},
                            {"physio": [{"hf_power": expected}]},
                        )
