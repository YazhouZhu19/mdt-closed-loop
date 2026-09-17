"""Atomic trial registration with generated hashes and temporary directories."""

from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from mdt_core.trial import TrialPinError, pin_trial


class TrialRegistryTests(unittest.TestCase):
    version_a = hashlib.sha256(b"generated-model-a").hexdigest()
    version_b = hashlib.sha256(b"generated-model-b").hexdigest()

    def test_manifest_is_stable_and_contains_only_study_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            versions = {"l2": self.version_a, "l3": self.version_b}
            path = pin_trial(directory, "generated-trial", versions, "restricted", ("l2", "l3"))
            self.assertIsNotNone(path)
            assert path is not None
            original = path.read_bytes()
            repeated = pin_trial(directory, "generated-trial", dict(reversed(list(versions.items()))),
                                 "restricted", ("l3", "l2"))
            self.assertEqual(path, repeated)
            self.assertEqual(path.read_bytes(), original)
            data = json.loads(original)
            self.assertEqual(set(data), {
                "schema", "trial_id", "mode", "enabled_modules", "policy_versions"
            })
            self.assertEqual(data["policy_versions"], versions)
            self.assertEqual(len(list(path.parent.iterdir())), 1)

    def test_disabled_and_shadow_do_not_create_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for mode in ("disabled", "shadow"):
                self.assertIsNone(pin_trial(directory, "", {}, mode, ()))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_version_mode_or_enabled_module_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pins = {"l2": self.version_a, "l3": self.version_b}
            pin_trial(directory, "fixed-trial", pins, "autonomous", ("l2", "l3"))
            for versions, mode, enabled in (
                ({"l2": self.version_b, "l3": self.version_b}, "autonomous", ("l2", "l3")),
                (pins, "restricted", ("l2", "l3")),
                (pins, "autonomous", ("l2",)),
                ({"l2": self.version_a}, "autonomous", ("l2",)),
            ):
                with (
                    self.subTest(versions=versions, mode=mode, enabled=enabled),
                    self.assertRaises(TrialPinError),
                ):
                    pin_trial(directory, "fixed-trial", versions, mode, enabled)

    def test_missing_configured_pin_rejected_before_filesystem_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                pin_trial(directory, "fixed-trial", {"l2": self.version_a},
                          "restricted", ("l2", "l3"))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_conflicting_concurrent_writers_never_both_succeed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workers = 12
            barrier = threading.Barrier(workers)
            def register(index: int) -> tuple[str, bool]:
                version = self.version_a if index % 2 else self.version_b
                barrier.wait()
                try:
                    pin_trial(directory, "concurrent-trial", {"l2": version},
                              "restricted", ("l2",))
                    return version, True
                except TrialPinError:
                    return version, False
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(register, range(workers)))
            accepted_versions = {version for version, accepted in results if accepted}
            self.assertEqual(len(accepted_versions), 1)
            self.assertEqual(sum(accepted for _, accepted in results), workers // 2)
            files = list((Path(directory) / ".trials").iterdir())
            self.assertEqual(len(files), 1)
            self.assertEqual(
                json.loads(files[0].read_text())["policy_versions"]["l2"],
                next(iter(accepted_versions)),
            )

    def test_corrupt_or_symlinked_manifest_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = pin_trial(directory, "generated-trial", {"l2": self.version_a},
                             "restricted", ("l2",))
            assert path is not None
            path.write_text("incomplete JSON")
            with self.assertRaises(TrialPinError):
                pin_trial(directory, "generated-trial", {"l2": self.version_a},
                          "restricted", ("l2",))
            self.assertEqual(path.read_text(), "incomplete JSON")
            path.unlink()
            external = Path(directory) / "unrelated.json"
            external.write_text("{}")
            path.symlink_to(external)
            with self.assertRaises(TrialPinError):
                pin_trial(directory, "generated-trial", {"l2": self.version_a},
                          "restricted", ("l2",))
            self.assertEqual(external.read_text(), "{}")

    def test_trial_id_cannot_escape_registry_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trial_id = "../generated/试验"
            path = pin_trial(directory, trial_id, {"l2": self.version_a},
                             "restricted", ("l2",))
            assert path is not None
            self.assertEqual(path.parent, Path(directory) / ".trials")
            self.assertEqual(path.name, hashlib.sha256(trial_id.encode()).hexdigest() + ".json")


if __name__ == "__main__":
    unittest.main()
