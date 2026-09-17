"""Atomic, local trial policy pins shared by sessions in one study directory.

All processes in a study must use the same ``out_dir``. This filesystem
registry does not synchronize independent machines/output directories; a
distributed study needs a centrally administered registry. No participant
identifier, observation, or outcome is stored here.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path

_MODULES = frozenset({"l1", "l2", "l3", "taste"})
_EXECUTABLE_MODES = frozenset({"suggest", "restricted", "autonomous"})


class TrialPinError(ValueError):
    """The requested trial differs from its existing local registration."""


def _read_registered(path: Path) -> object:
    # An unexpected symlink should not redirect the registry to another file.
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "r", encoding="utf-8") as registered:
            return json.load(registered)
    except (OSError, ValueError, UnicodeError) as exc:
        raise TrialPinError(f"cannot read intact trial registry: {path}") from exc


def _verify_registered(path: Path, expected: dict) -> None:
    actual = _read_registered(path)
    # Compare canonical JSON so changed value types (e.g. True versus 1) do not
    # compare equal through Python's bool/int equality rules.
    if json.dumps(actual, sort_keys=True) != json.dumps(expected, sort_keys=True):
        raise TrialPinError(
            "trial policy registration changed: versions, enabled modules and "
            f"deployment mode must remain frozen for this trial ({path})"
        )


def pin_trial(
    out_dir: str | Path,
    trial_id: str,
    versions: Mapping[str, str],
    mode: str,
    enabled_modules: Iterable[str],
) -> Path | None:
    """Create or verify a local immutable trial manifest before actuation.

    Freeze *configured* pins, including enabled modules whose model may be
    temporarily absent. Callers must not derive ``versions`` from only the
    supplied models. Disabled/shadow modes perform no filesystem operations;
    Session additionally bypasses this function for controls/calibration.

    A completed temporary file is atomically linked into place. Concurrent
    publishers can never replace each other's registrations: only one link
    succeeds and every loser verifies the fully written winner.
    """
    if mode in {"disabled", "shadow"}:
        return None
    if mode not in _EXECUTABLE_MODES:
        raise ValueError("unknown trial deployment mode")
    if not isinstance(trial_id, str):
        raise TypeError("trial_id must be a string")
    if not trial_id.strip():
        raise ValueError("executable trial registration requires a trial_id")
    if not isinstance(versions, Mapping):
        raise TypeError("versions must be a module-to-content-hash mapping")
    if isinstance(enabled_modules, str):
        raise TypeError("enabled_modules must be an iterable of module names")
    modules = tuple(enabled_modules)
    if not modules or len(set(modules)) != len(modules):
        raise ValueError("enabled_modules must be nonempty and unique")
    if any(module not in _MODULES for module in modules):
        raise ValueError("unknown enabled policy module")
    configured = dict(versions)
    for module, version in configured.items():
        if module not in _MODULES:
            raise ValueError("unknown policy module pin")
        if (
            not isinstance(version, str)
            or len(version) != 64
            or any(character not in "0123456789abcdef" for character in version)
        ):
            raise ValueError("trial versions must be SHA-256 content hashes")
    if any(module not in configured for module in modules):
        raise ValueError("all enabled modules require a configured policy pin")
    if not str(out_dir):
        raise ValueError("trial output directory must not be empty")
    expected = {
        "schema": 1,
        "trial_id": trial_id,
        "mode": mode,
        "enabled_modules": sorted(modules),
        "policy_versions": configured,
    }
    registry = Path(out_dir) / ".trials"
    registry.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(trial_id.encode("utf-8")).hexdigest()
    destination = registry / f"{name}.json"
    # This fast path is only an optimization; os.link handles the creation race.
    if destination.exists() or destination.is_symlink():
        _verify_registered(destination, expected)
        return destination
    payload = json.dumps(expected, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix=f".{name}.", suffix=".tmp",
            dir=registry, delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        try:
            os.link(temporary_path, destination)
        except FileExistsError:
            _verify_registered(destination, expected)
        else:
            # Durably persist the new name as well as the already-fsynced data.
            directory_descriptor = os.open(registry, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        return destination
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
