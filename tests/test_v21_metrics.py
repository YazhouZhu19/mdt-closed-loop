"""Exact checks for v2.1 costs, denominators, risk bounds and critical paths."""

from __future__ import annotations

import math
import unittest
from dataclasses import replace
from typing import cast

from mdt_core.execution import ExecutionAck, MusicVector
from mdt_core.research_metrics import (
    CONTINUOUS_MUSIC_CHANNELS,
    CommitAttempt,
    TrackingInterval,
    critical_path_seconds,
    music_change_cost,
    session_commit_metrics,
    tracking_cost,
    zero_event_session_risk_upper_bound,
)
from mdt_core.types import MusicParams


class TrackingCostTests(unittest.TestCase):
    def test_piecewise_constant_integral_is_invariant_to_subdivision(self):
        whole = tracking_cost([TrackingInterval(0, 10, 0.7, 0.5)])
        split = tracking_cost([TrackingInterval(i, i + 1, 0.7, 0.5) for i in range(10)])
        self.assertAlmostEqual(whole.integrated_squared_error, 0.4)
        self.assertAlmostEqual(
            whole.integrated_squared_error, split.integrated_squared_error
        )
        assert whole.rmse is not None
        self.assertAlmostEqual(whole.rmse, 0.2)
        self.assertEqual(whole.coverage, 1)

    def test_irregular_intervals_are_time_weighted(self):
        summary = tracking_cost(
            [
                TrackingInterval(0, 1, 0.8, 0.4),
                TrackingInterval(1, 10, 0.5, 0.4),
            ]
        )
        self.assertAlmostEqual(summary.integrated_squared_error, 0.25)
        assert summary.rmse is not None
        self.assertAlmostEqual(summary.rmse, math.sqrt(0.025))

    def test_explicit_missing_and_gaps_reduce_coverage(self):
        summary = tracking_cost(
            [
                TrackingInterval(0, 2, 0.8, 0.4),
                TrackingInterval(4, 10, None, None, evaluable=False),
            ]
        )
        self.assertAlmostEqual(summary.integrated_squared_error, 0.32)
        self.assertEqual(summary.evaluable_duration, 2)
        self.assertEqual(summary.unevaluable_duration, 8)
        self.assertEqual(summary.coverage, 0.2)

    def test_all_missing_is_not_zero_rmse(self):
        summary = tracking_cost([TrackingInterval(0, 10, None, None, False)])
        self.assertIsNone(summary.rmse)
        self.assertEqual(summary.coverage, 0)

    def test_bad_intervals_are_rejected(self):
        for start, end in ((1, 0), (0, 0), (-1, 1), (0, math.inf)):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                TrackingInterval(start, end, 0.5, 0.5)
        with self.assertRaises(ValueError):
            TrackingInterval(0, 1, None, 0.5)
        with self.assertRaises(ValueError):
            tracking_cost([])
        with self.assertRaises(ValueError):
            tracking_cost(
                [
                    TrackingInterval(0, 2, 0.5, 0.5),
                    TrackingInterval(1, 3, 0.5, 0.5),
                ]
            )


class MusicChangeCostTests(unittest.TestCase):
    def setUp(self):
        self.initial = MusicParams(tempo=70)
        self.scales = {name: 1.0 for name in CONTINUOUS_MUSIC_CHANNELS}
        self.scales["tempo"] = 10.0

    def ack(self, command, timestamp, **values):
        params = replace(self.initial, **values)
        return ExecutionAck("epoch", command, timestamp, MusicVector.of(params))

    def cost(self, acks, **kwargs):
        return music_change_cost(self.initial, acks, scales=self.scales, **kwargs)

    def test_ack_cost_does_not_depend_on_event_time_spacing(self):
        short = [self.ack("a", 1, tempo=69), self.ack("b", 2, tempo=68)]
        long = [self.ack("a", 100, tempo=69), self.ack("b", 900, tempo=68)]
        self.assertAlmostEqual(self.cost(short).normalized_squared_change, 0.02)
        self.assertEqual(self.cost(short), self.cost(long))

    def test_normalization_weights_and_discrete_layers(self):
        weights = {name: 1.0 for name in CONTINUOUS_MUSIC_CHANNELS}
        weights["tempo"] = 2.0
        result = self.cost(
            [
                self.ack("a", 1, tempo=68, dynamics=0.4, layer_mask=3),
            ],
            weights=weights,
        )
        self.assertAlmostEqual(result.normalized_squared_change, 0.09)
        self.assertEqual(result.layer_transitions, 1)
        self.assertEqual(result.acknowledged_commands, 1)

    def test_exact_duplicate_ack_is_not_counted_twice(self):
        ack = self.ack("a", 1, tempo=69)
        self.assertEqual(self.cost([ack, ack]), self.cost([ack]))
        with self.assertRaises(ValueError):
            self.cost([ack, self.ack("a", 2, tempo=68)])

    def test_epoch_and_order_must_be_preserved(self):
        first = self.ack("a", 2, tempo=69)
        with self.assertRaises(ValueError):
            self.cost([first, self.ack("b", 1, tempo=68)])
        with self.assertRaises(ValueError):
            self.cost([first, replace(self.ack("b", 3), epoch="other")])

    def test_missing_scales_and_non_ack_input_are_rejected(self):
        with self.assertRaises(ValueError):
            music_change_cost(self.initial, [], scales={"tempo": 10})
        with self.assertRaises(TypeError):
            self.cost([self.initial])
        with self.assertRaises(ValueError):
            self.cost([], weights={name: 0.0 for name in CONTINUOUS_MUSIC_CHANNELS})


class CommitRateTests(unittest.TestCase):
    def test_rejected_invalid_attempt_is_not_an_invalid_commit(self):
        result = session_commit_metrics(
            [
                CommitAttempt("rejected", False, True),
                CommitAttempt("valid", True, False),
                CommitAttempt("invalid", True, True),
            ],
            commit_opportunities=4,
        )
        self.assertEqual(result.attempts, 3)
        self.assertEqual(result.rejected_attempts, 1)
        self.assertEqual(result.actual_commits, 2)
        self.assertEqual(result.invalid_commits, 1)
        self.assertEqual(result.invalid_per_commit, 0.5)
        self.assertEqual(result.invalid_per_opportunity, 0.25)
        self.assertTrue(result.any_invalid_commit)

    def test_no_execution_does_not_claim_perfect_commit_rate(self):
        result = session_commit_metrics(
            [CommitAttempt("rejected", False, True)],
            commit_opportunities=1,
        )
        self.assertEqual(result.invalid_commits, 0)
        self.assertIsNone(result.invalid_per_commit)
        self.assertFalse(result.any_invalid_commit)
        empty = session_commit_metrics([], commit_opportunities=0)
        self.assertIsNone(empty.invalid_per_opportunity)

    def test_duplicate_attempt_ids_and_invalid_counts_are_rejected(self):
        attempt = CommitAttempt("a", True, False)
        with self.assertRaises(ValueError):
            session_commit_metrics([attempt, attempt], commit_opportunities=1)
        for count in (-1, 1.5, True):
            with self.subTest(count=count), self.assertRaises(ValueError):
                session_commit_metrics([], commit_opportunities=cast(int, count))


class SessionRiskBoundTests(unittest.TestCase):
    def test_exact_zero_event_session_risk_bound(self):
        self.assertAlmostEqual(zero_event_session_risk_upper_bound(1), 0.95)
        self.assertAlmostEqual(
            zero_event_session_risk_upper_bound(100),
            1 - 0.05 ** (1 / 100),
        )
        self.assertLess(zero_event_session_risk_upper_bound(100), 0.03)

    def test_large_n_remains_positive_without_cancellation(self):
        bound = zero_event_session_risk_upper_bound(10**18)
        self.assertGreater(bound, 0)
        self.assertAlmostEqual(bound / (math.log(20) / 10**18), 1)

    def test_no_trials_or_invalid_confidence_is_an_error(self):
        for n in (0, -1, True, 1.1):
            with self.subTest(n=n), self.assertRaises(ValueError):
                zero_event_session_risk_upper_bound(cast(int, n))
        for alpha in (0, 1, math.nan, math.inf):
            with self.subTest(alpha=alpha), self.assertRaises(ValueError):
                zero_event_session_risk_upper_bound(100, alpha=alpha)


class CriticalPathTests(unittest.TestCase):
    def test_serial_dependencies_sum(self):
        self.assertEqual(
            critical_path_seconds({"a": 2, "b": 3}, {"b": ["a"]}),
            5,
        )

    def test_parallel_dependencies_use_max_not_sum(self):
        result = critical_path_seconds(
            {"observe": 1, "a2": 2, "a3": 5, "commit": 3},
            {"a2": ["observe"], "a3": ["observe"], "commit": ["a2", "a3"]},
        )
        self.assertEqual(result, 9)

    def test_cycle_and_unknown_nodes_are_rejected(self):
        with self.assertRaises(ValueError):
            critical_path_seconds({"a": 1, "b": 1}, {"a": ["b"], "b": ["a"]})
        with self.assertRaises(ValueError):
            critical_path_seconds({"a": 1}, {"a": ["unknown"]})
        with self.assertRaises(ValueError):
            critical_path_seconds({"a": 1}, {"unknown": []})

    def test_invalid_duration_and_duplicate_edge_are_rejected(self):
        for duration in (-1, math.nan, math.inf):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                critical_path_seconds({"a": duration}, {})
        with self.assertRaises(ValueError):
            critical_path_seconds({"a": 1, "b": 2}, {"b": ["a", "a"]})

    def test_empty_graph_has_zero_path(self):
        self.assertEqual(critical_path_seconds({}, {}), 0)


if __name__ == "__main__":
    unittest.main()
