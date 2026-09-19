"""Deterministic v2.1 execution-contract demonstration, not an effect study.

All observations, targets and timestamps are constructed numbers. No public
dataset, participant, physiological response model, or audio device is used.
Run from the repository root with:
    python examples/v21_contract_demo.py --output docs/validation/v21_contract_demo.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, replace
from pathlib import Path

# Support direct script execution without requiring an editable installation.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mdt_core.engine import NullEngine
from mdt_core.execution import AcceptedDecision, ExecutionGateway, Mode, MusicVector
from mdt_core.profiles import v21_config
from mdt_core.types import MusicParams


def run_demo() -> dict:
    """Run one reproducible scenario and fail if any contract check is false."""
    cfg = v21_config()
    cfg = replace(cfg, execution=replace(
        cfg.execution,
        observation_ttl_s=5.0,
        decision_ttl_s=10.0,
        hold_max_s=3.0,
        recovery_observations=2,
    ))
    engine = NullEngine()
    gateway = ExecutionGateway(
        "demo", engine, cfg.execution, cfg.grammar, MusicParams(),
    )
    checks: dict[str, bool] = {}
    checkpoints: list[dict] = []

    def check(name: str, condition: bool) -> None:
        checks[name] = condition
        if not condition:
            raise RuntimeError(f"contract demonstration failed: {name}")

    def decision(sequence: int, observed: float, created: float,
                 tempo: float) -> AcceptedDecision:
        return AcceptedDecision(
            epoch="demo",
            sequence=sequence,
            observation_id=f"synthetic-window-{sequence}",
            observation_end=observed,
            created_at=created,
            parent_ack_id=gateway.ack_id,
            target=MusicVector.of(MusicParams(tempo=tempo)),
            mode=Mode.BASELINE,
        )

    def checkpoint(name: str, t: float) -> None:
        checkpoints.append({
            "name": name,
            "t": t,
            "mode": gateway.mode.value,
            "ack_id": gateway.ack_id,
            "confirmed": gateway.current.as_dict(),
            "pending_sequence": gateway.pending.sequence if gateway.pending else None,
            "pending_target": asdict(gateway.pending.target) if gateway.pending else None,
            "engine_history_count": len(engine.history),
            "engine_stopped": engine.stopped,
        })

    # Two distinct source windows establish baseline startup. No engine command
    # is issued merely because a candidate was accepted into the gateway.
    check("startup_requires_two_observations",
          not gateway.accept(decision(1, 0.0, 0.0, 70.0), 0.0))
    check("baseline_startup_accepts_second_observation",
          gateway.accept(decision(2, 1.0, 1.0, 71.0), 1.0))
    check("newer_target_replaces_pending_target",
          gateway.accept(decision(3, 2.0, 2.0, 68.0), 2.0))
    check("accepted_target_does_not_change_confirmed_music",
          gateway.current.tempo == 72.0 and not engine.history)
    checkpoint("latest_absolute_target_pending", 2.0)

    gateway.boundary(3.0, phrase=True)
    check("boundary_submits_latest_decision",
          gateway.last_command is not None and gateway.last_command.decision_sequence == 3)
    check("ack_updates_confirmed_music",
          gateway.last_ack is not None and gateway.current == engine.history[-1])
    check("target_is_projected_against_elapsed_budget",
          math.isclose(gateway.current.tempo, 71.8, abs_tol=1e-12))
    checkpoint("first_projected_target_acknowledged", 3.0)

    before_duplicate = (gateway.current, gateway.ack_id, len(engine.history))
    gateway.boundary(3.0, phrase=True)
    check("duplicate_boundary_has_no_execution_effect",
          (gateway.current, gateway.ack_id, len(engine.history)) == before_duplicate)
    checkpoint("duplicate_boundary_ignored", 3.0)

    # A later real boundary has its own budget and command ID. The immutable
    # desired target remains 68 BPM; it is not another decrement from 71.8 BPM.
    first_command_id = gateway.ack_id
    gateway.boundary(4.0, phrase=True)
    check("later_boundary_uses_new_command_id", gateway.ack_id != first_command_id)
    check("same_absolute_target_is_preserved",
          gateway.pending is not None and gateway.pending.target.tempo == 68.0)
    check("later_boundary_respects_incremental_budget",
          math.isclose(gateway.current.tempo, 71.8 - 2.0 / 30.0, abs_tol=1e-12))
    checkpoint("same_target_progresses_at_new_boundary", 4.0)

    # Original observation ends at 2 s: source TTL expires at 7 s, although the
    # decision TTL lasts until 12 s. The watchdog continues without sensors or
    # boundaries. A newly packaged decision cannot refresh the old source age.
    confirmed_before_loss = gateway.current
    history_before_loss = len(engine.history)
    check("source_ttl_enters_hold_before_decision_ttl",
          gateway.poll(8.0) is Mode.HOLD)
    check("repackaged_old_source_is_rejected",
          not gateway.accept(decision(4, 2.0, 8.0, 65.0), 8.0))
    check("hold_cancels_pending_targets", gateway.pending is None)
    check("hold_keeps_complete_confirmed_vector",
          gateway.current == confirmed_before_loss and len(engine.history) == history_before_loss)
    checkpoint("expired_source_is_not_refreshed_by_new_decision", 8.0)
    check("hold_is_finite_before_deadline", gateway.poll(9.0) is Mode.HOLD)
    check("hold_deadline_stops_without_sensor_or_boundary_event",
          gateway.poll(10.0) is Mode.STOPPED and engine.stopped)
    checkpoint("hold_timeout_stops_engine", 10.0)

    gateway.boundary(11.0, phrase=True)
    check("stopped_session_rejects_new_decisions",
          not gateway.accept(decision(5, 11.0, 11.0, 65.0), 11.0))
    check("late_boundary_cannot_restart_execution",
          gateway.mode is Mode.STOPPED and len(engine.history) == history_before_loss)
    checkpoint("stop_is_terminal", 11.0)

    return {
        "schema_version": 1,
        "artifact_kind": "synthetic_execution_contract_demo",
        "epoch": "demo",
        "scope": "Constructed software-contract scenario; not a physiological or intervention-effect experiment.",
        "profile": "mdt_core.profiles.v21_config with explicitly shortened demonstration TTL/HOLD values",
        "clock": "deterministic session-relative seconds; watchdog ticks supplied independently",
        "execution_config": asdict(cfg.execution),
        "limitations": [
            "No participant data, physiological plant, learned model, or efficacy measurement.",
            "NullEngine returns a synchronous simulated ACK and produces no audio.",
            "Software dynamics is not calibrated sound pressure; no hearing-safety claim.",
            "Explicit poll calls demonstrate deadline semantics, not an independent hardware watchdog.",
        ],
        "all_checks_passed": all(checks.values()),
        "checks": checks,
        "checkpoints": checkpoints,
        "events": gateway.events,
        "engine_history": [params.as_dict() for params in engine.history],
        "engine_history_semantics": "Initial engine.start state, followed by ACKed command states.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write the deterministic JSON report to this path.")
    args = parser.parse_args()
    result = run_demo()
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output is None:
        print(encoded, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
        print(f"Passed {len(result['checks'])} contract checks; wrote {args.output}")


if __name__ == "__main__":
    main()
