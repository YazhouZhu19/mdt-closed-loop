"""Compare the current legacy replay exactly with a pinned original runtime.

Run as a separate CI check: python tests/check_legacy_reference.py
Both runtimes use the same interpreter, installed dependencies and current
synthetic replay inputs. No cross-platform floating-point tolerance is used.
"""

from __future__ import annotations

import io
import json
import math
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

REFERENCE_COMMIT = "bbb66823d85979848069615ac1e486ab1435362a"
REPLAY_HELPERS = ("learning_scenarios.py", "synthetic.py")

# Verify every loaded runtime module, including transitive imports, so an
# editable installation or inherited PYTHONPATH cannot substitute current code.
CHILD_REPLAY = """
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
import numpy
import scipy
from mdt_core.types import Arm
from tests.learning_scenarios import replay

arms = {arm.value: replay(arm) for arm in Arm}
for name, module in tuple(sys.modules.items()):
    if name == "mdt_core" or name.startswith("mdt_core."):
        source = Path(module.__file__).resolve()
        if not source.is_relative_to(root / "mdt_core"):
            raise RuntimeError(f"Runtime import escaped selected checkout: {name}: {source}")
for name in ("tests.learning_scenarios", "tests.synthetic"):
    source = Path(sys.modules[name].__file__).resolve()
    if not source.is_relative_to(root / "tests"):
        raise RuntimeError(f"Replay helper imported from wrong checkout: {name}: {source}")
print(json.dumps({
    "environment": {
        "python": sys.version,
        "numpy": numpy.__version__,
        "scipy": scipy.__version__,
    },
    "arms": arms,
}, allow_nan=False, sort_keys=True))
"""


def extract_reference(repository: Path, destination: Path) -> None:
    """Extract only the immutable runtime; reject links and escaping paths."""
    resolved = subprocess.check_output(
        ["git", "rev-parse", "--verify", f"{REFERENCE_COMMIT}^{{commit}}"],
        cwd=repository,
        text=True,
    ).strip()
    if resolved != REFERENCE_COMMIT:
        raise RuntimeError("The reference must resolve to the pinned original commit")
    archive_bytes = subprocess.check_output(
        ["git", "archive", "--format=tar", REFERENCE_COMMIT, "--", "mdt_core"],
        cwd=repository,
    )
    runtime_root = (destination / "mdt_core").resolve()
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
        for member in archive.getmembers():
            target = (destination / member.name).resolve()
            if not target.is_relative_to(runtime_root):
                raise ValueError(f"Archive member escaped runtime: {member.name}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError(f"Missing archive contents: {member.name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with stream, target.open("wb") as output:
                    shutil.copyfileobj(stream, output)
            else:
                raise ValueError(f"Unsupported archive member: {member.name}")
    if not (runtime_root / "__init__.py").is_file():
        raise ValueError("The pinned archive did not contain an MDT runtime")


def replay_checkout(checkout: Path, repository: Path) -> dict[str, Any]:
    environment = os.environ.copy()
    # Keep dependency-only PYTHONPATH entries used by local verification, while
    # dropping repository paths. Each child additionally validates module files.
    dependency_paths = []
    for entry in environment.get("PYTHONPATH", "").split(os.pathsep):
        if entry and not Path(entry).resolve().is_relative_to(repository):
            dependency_paths.append(entry)
    environment["PYTHONPATH"] = os.pathsep.join(dependency_paths)
    output = subprocess.check_output(
        [sys.executable, "-W", "error", "-c", CHILD_REPLAY, str(checkout)],
        cwd=checkout,
        env=environment,
        text=True,
    )
    return json.loads(output)


def assert_exact_equal(actual: object, expected: object, path: str = "$") -> None:
    """Compare JSON outputs recursively, preserving types and all finite values."""
    if type(actual) is not type(expected):
        raise AssertionError(f"{path}: type changed: {type(actual)} != {type(expected)}")
    if isinstance(expected, dict):
        assert isinstance(actual, dict)
        if actual.keys() != expected.keys():
            raise AssertionError(f"{path}: dictionary keys changed")
        for key in expected:
            assert_exact_equal(actual[key], expected[key], f"{path}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list)
        if len(actual) != len(expected):
            raise AssertionError(f"{path}: list length changed")
        for index, (current, original) in enumerate(zip(actual, expected)):
            assert_exact_equal(current, original, f"{path}[{index}]")
    else:
        if isinstance(expected, float):
            assert isinstance(actual, float)
            if not math.isfinite(actual) or not math.isfinite(expected):
                raise AssertionError(f"{path}: nonfinite numerical output")
        if actual != expected:
            raise AssertionError(f"{path}: {actual!r} != {expected!r}")


def main() -> None:
    repository = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(prefix="mdt-pinned-reference-") as directory:
        reference = Path(directory)
        extract_reference(repository, reference)
        helpers = reference / "tests"
        helpers.mkdir()
        (helpers / "__init__.py").write_text("", encoding="utf-8")
        for helper in REPLAY_HELPERS:
            shutil.copyfile(repository / "tests" / helper, helpers / helper)
        original = replay_checkout(reference, repository)
        current = replay_checkout(repository, repository)
        assert_exact_equal(current["environment"], original["environment"])
        assert_exact_equal(current["arms"], original["arms"])
    print(json.dumps({
        "reference_commit": REFERENCE_COMMIT,
        "environment": current["environment"],
        "arms": list(current["arms"]),
        "all_values_and_types_exactly_equal": True,
        "tolerance_used": False,
        "runtime_import_origins_verified": True,
    }, indent=2))


if __name__ == "__main__":
    main()
