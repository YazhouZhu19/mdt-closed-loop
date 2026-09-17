"""Adversarial guard properties and the pre-upgrade default transcript."""

from __future__ import annotations

import hashlib
import json
import math
import random
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

from mdt_core.agents.l35_taste import TastePolicy
from mdt_core.config import DEFAULT
from mdt_core.l3_control import MusicGrammar as LegacyImport
from mdt_core.l35_guard import GuardLimits, MusicGrammar, project
from mdt_core.l35_mapping import LAYER_LEVELS, map_control
from mdt_core.types import MusicParams


class GuardTests(unittest.TestCase):
    def test_adversarial_projection_is_bounded_and_idempotent(self) -> None:
        rng = random.Random(71)
        invalid: list[Any] = [float("nan"), float("inf"), -float("inf"), None, "unsafe", True, {}, 10**500]
        for index in range(1200):
            current = MusicParams(tempo=rng.uniform(55, 85), layer_mask=rng.choice(LAYER_LEVELS))
            elapsed = rng.uniform(0, 120)
            bar, phrase = bool(index % 2), bool(index % 3)
            limits = GuardLimits(current, elapsed, DEFAULT.grammar, bar, phrase)
            raw = {name: rng.choice(invalid) if index % 4 else rng.uniform(-1e10, 1e10)
                   for name in current.as_dict()}
            raw["layer_mask"] = rng.choice(invalid + list(range(-1, 18)))
            saved = current.copy()
            result = project(raw, limits)
            self.assertEqual(project(result, limits), result)
            self.assertEqual(current, saved)
            self.assertTrue(55 <= result.tempo <= 85)
            self.assertIn(result.layer_mask, LAYER_LEVELS)
            self.assertTrue(all(math.isfinite(value) for value in result.as_dict().values()))
            if bar or phrase:
                self.assertLessEqual(abs(result.tempo - current.tempo), elapsed * 2 / 30 + 1e-12)
            else:
                self.assertEqual(result, current)
            if phrase:
                self.assertLessEqual(abs(LAYER_LEVELS.index(result.layer_mask) - LAYER_LEVELS.index(current.layer_mask)), 1)
            else:
                self.assertEqual(result.layer_mask, current.layer_mask)
            for name in ("register", "dynamics", "harmonic_brightness", "rhythmic_accent", "reverb_depth"):
                self.assertTrue(0 <= getattr(result, name) <= 1)

    def test_limits_are_a_frozen_snapshot(self) -> None:
        source = MusicParams()
        limits = GuardLimits(source, phrase_boundary=True)
        source.tempo = 80
        limits.current.tempo = 60
        self.assertEqual(limits.current.tempo, 72)
        with self.assertRaises(FrozenInstanceError):
            limits.elapsed_tempo_s = 100  # type: ignore[misc]
        with self.assertRaises(ValueError):
            GuardLimits(source, float("inf"))

    def test_all_noncanonical_masks_are_projected_by_active_layer_count(self) -> None:
        for mask in range(1, 16):
            current = MusicParams(layer_mask=mask)
            result = project({"layer_mask": mask}, GuardLimits(current))
            self.assertEqual(result.layer_mask, LAYER_LEVELS[mask.bit_count() - 1])
            self.assertEqual(project(result, GuardLimits(current)), result)

    def test_elapsed_zero_does_not_allow_tempo_movement(self) -> None:
        previous = MusicParams()
        result = project({"tempo": 85}, GuardLimits(previous, 0, bar_boundary=True))
        self.assertEqual(result.tempo, previous.tempo)

    def test_learned_request_waits_and_enforces_each_event_budget(self) -> None:
        grammar = MusicGrammar(DEFAULT.grammar, MusicParams(layer_mask=1))
        request = {"tempo": 85, "layer_mask": 15, "dynamics": 1.0}
        grammar.request_params(request, 0)
        request["tempo"] = 55
        self.assertEqual(grammar.commit(1), (MusicParams(layer_mask=1), False))
        first, changed = grammar.commit(4, bar_boundary=True)
        self.assertTrue(changed)
        self.assertEqual((first.tempo, first.layer_mask, first.dynamics), (74, 1, 1))
        second, _ = grammar.commit(8, phrase_boundary=True)
        self.assertAlmostEqual(second.tempo, 74 + 4 * 2 / 30)
        self.assertEqual(second.layer_mask, 3)
        self.assertEqual(grammar.commit(8, phrase_boundary=True), (second, False))
        third, _ = grammar.commit(38, phrase_boundary=True)
        self.assertEqual(third.layer_mask, 7)
        self.assertAlmostEqual(third.tempo - second.tempo, 2)
        grammar.cancel_pending()
        self.assertEqual(grammar.commit(68, phrase_boundary=True), (third, False))

    def test_default_and_learned_paths_clear_each_others_pending_commands(self) -> None:
        grammar = MusicGrammar(DEFAULT.grammar)
        grammar.request_params({"tempo": 85, "layer_mask": 15}, 0)
        grammar.request(0, 1)
        self.assertEqual(grammar.commit(32, phrase_boundary=True)[0].tempo, 72)
        grammar.request(-0.8, 33)
        grammar.request_params({"tempo": 72, "layer_mask": 7}, 34)
        self.assertEqual(grammar.commit(64, phrase_boundary=True)[0].layer_mask, 7)

    def test_default_mapping_is_pure(self) -> None:
        source = MusicParams()
        saved = source.copy()
        result = map_control(-0.5, source)
        self.assertEqual(source, saved)
        self.assertEqual((result.tempo, result.layer_mask), (66, 3))

    def test_fallback_restores_timbre_only_at_a_boundary_and_reports_change(self) -> None:
        grammar = MusicGrammar(DEFAULT.grammar)
        grammar.request_params({"dynamics": 1, "reverb_depth": 0}, 0)
        learned, _ = grammar.commit(4, bar_boundary=True)
        grammar.request(0, 5)
        self.assertEqual(grammar.commit(5), (learned, False))
        restored, changed = grammar.commit(8, bar_boundary=True)
        self.assertTrue(changed)
        self.assertEqual(restored.tempo, learned.tempo)
        self.assertNotEqual(restored.dynamics, learned.dynamics)

    def test_cancel_pending_also_cancels_default_timbre_restoration(self) -> None:
        grammar = MusicGrammar(DEFAULT.grammar)
        grammar.request_params({"dynamics": 1}, 0)
        learned, _ = grammar.commit(4, bar_boundary=True)
        grammar.request(0, 5)
        grammar.cancel_pending()
        self.assertEqual(grammar.commit(8, bar_boundary=True), (learned, False))

    def test_fallback_cannot_spend_the_same_phrase_layer_budget_twice(self) -> None:
        grammar = MusicGrammar(DEFAULT.grammar, MusicParams(layer_mask=1))
        grammar.request_params({"layer_mask": 15}, 0)
        first, _ = grammar.commit(4, phrase_boundary=True)
        self.assertEqual(first.layer_mask, 3)
        grammar.request(.5, 4)
        duplicate, changed = grammar.commit(4, phrase_boundary=True)
        self.assertEqual((duplicate, changed), (first, False))
        next_phrase, _ = grammar.commit(36, phrase_boundary=True)
        self.assertEqual(next_phrase.layer_mask, 7)
        grammar.request(.5, 36)
        self.assertEqual(grammar.commit(36, phrase_boundary=True)[0].layer_mask, 7)
        grammar.request_params({"layer_mask": 15}, 36)
        self.assertEqual(grammar.commit(36, phrase_boundary=True)[0].layer_mask, 7)

    def test_frozen_default_matches_original_full_vector_transcript(self) -> None:
        self.assertIs(LegacyImport, MusicGrammar)
        # Golden digest captured from the untouched pre-upgrade source. Includes
        # derived parameters on non-boundaries, jitter, partial tempo progress,
        # reversible layers, the strict 0.15 threshold, and zero cancellation.
        steps = [(-.5, 0, False, False), (None, 4, True, False),
                 (None, 8, True, False), (None, 32, False, True),
                 (0, 33, False, False), (.15, 34, False, False),
                 (None, 64, True, True), (.7, 66, True, False),
                 (None, 96, False, True), (-.7, 120, False, False),
                 (0, 121, False, False), (None, 128, True, True)]
        grammar = MusicGrammar(DEFAULT.grammar)
        rows = []
        for control, t, bar, phrase in steps:
            if control is not None:
                grammar.request(control, t)
            params, changed = grammar.commit(t, bar_boundary=bar, phrase_boundary=phrase)
            rows.append((params.as_dict(), changed))
        digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
        self.assertEqual(digest, "7d6a256e963a69edf89a4aa6d423e4ee63950703060b04a4945f558bf5fa540e")


class TasteTests(unittest.TestCase):
    @staticmethod
    def _model():
        liked, skipped = MusicParams(dynamics=.8), MusicParams(dynamics=.2)
        contexts = [{"control": value} for value in (-1.0, -.5, 0, .5, 1.0) for _ in range(2)]
        model = TastePolicy.fit(contexts, [liked, skipped] * 5, [1.0, 0.0] * 5, ridge=.01)
        return model, liked, skipped

    def test_offline_preference_is_versioned_immutable_and_uncalibrated(self) -> None:
        model, liked, _ = self._model()
        version = model.version
        proposal = model.propose({"control": 0.25})
        self.assertEqual(proposal.action, liked.as_dict())
        self.assertEqual(proposal.confidence, 0)
        self.assertEqual(proposal.logprob, 0)
        self.assertTrue(proposal.in_distribution)
        self.assertFalse(model.calibrated)
        self.assertFalse(model.propose({"control": 2}).in_distribution)
        self.assertEqual(model.version, version)
        with self.assertRaises(FrozenInstanceError):
            model.ridge = 2
        proposal.action["dynamics"] = 0
        self.assertEqual(model.propose({"control": 0.25}).action, liked.as_dict())

    def test_independent_calibration_creates_new_artifact(self) -> None:
        model, liked, skipped = self._model()
        contexts = [{"control": -0.99 + i * .061} for i in range(32)]
        calibrated = model.calibrate(contexts, [liked, skipped] * 16, [1, 0] * 16)
        self.assertTrue(calibrated.calibrated)
        self.assertFalse(model.calibrated)
        self.assertNotEqual(calibrated.version, model.version)
        self.assertGreater(calibrated.propose({"control": .2}).confidence, .95)
        failed = model.calibrate(contexts, [liked, skipped] * 16, [0, 1] * 16)
        self.assertFalse(failed.calibrated)

    def test_training_overlap_and_invalid_feedback_are_rejected(self) -> None:
        model, liked, _ = self._model()
        with self.assertRaises(ValueError):
            model.calibrate([{"control": 0}], [liked], [1])
        with self.assertRaises(ValueError):
            model.calibrate([{"control": 0}], [liked], [0])
        with self.assertRaises(ValueError):
            TastePolicy.fit([{"control": 0}], [liked], [float("nan")])

    def test_artifact_roundtrip_and_tamper_detection(self) -> None:
        model, _, _ = self._model()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "taste.json"
            model.save(path)
            restored = TastePolicy.load(path)
            self.assertEqual(restored.version, model.version)
            self.assertEqual(restored.propose({"control": .2}), model.propose({"control": .2}))
            payload = json.loads(path.read_text())
            payload["model"]["coefficients"][0] += 1
            path.write_text(json.dumps(payload))
            with self.assertRaises(ValueError):
                TastePolicy.load(path)


if __name__ == "__main__":
    unittest.main()
