"""Run the offline L2 API with generated mathematical observations.

From the repository root::

    python -m docs.LEARNING_BANDIT_EXAMPLE
    python -m docs.LEARNING_BANDIT_EXAMPLE --output /tmp/l2-example.json

The example does not open a Session or actuate music. Synthetic validation
demonstrates the API only; its resulting model is not participant-ready.
For a real study, supply explicitly defined rewards from logged actions and
predeclare separate training/validation sessions and acceptance criteria.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mdt_core.agents.l2_bandit import (
    HierarchicalTrajectoryBandit,
    TrajectoryObservation,
)
from mdt_core.l2_planner import TrajectoryParams


def generated_observations(prefix: str) -> list[TrajectoryObservation]:
    return [
        TrajectoryObservation(
            context={
                "initial_arousal": 0.3 + (session % 10) * 0.05,
                "subject_id": f"generated-subject-{session % 4}",
                "session_id": f"{prefix}-{session}",
            },
            action=action,
            reward=0.2 if action == 0 else 0.8,
        )
        for session in range(20)
        for action in range(2)
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional JSON model artifact path")
    args = parser.parse_args()
    catalog = (TrajectoryParams(), TrajectoryParams(floor=0.3))
    fitted = HierarchicalTrajectoryBandit.fit(
        generated_observations("train"), actions=catalog
    )
    assert not fitted.calibrated
    validated = fitted.calibrate(generated_observations("heldout"))
    assert validated.calibrated
    context = {
        "initial_arousal": 0.5,
        "subject_id": "unseen-generated-subject",
        "session_id": "shadow-example",
    }
    proposal = validated.propose(context)
    if args.output is not None:
        validated.save(args.output)
        restored = HierarchicalTrajectoryBandit.load(args.output)
        assert restored.version == validated.version
    print(json.dumps({
        "data_kind": "generated mathematical observations; no efficacy evidence",
        "version": validated.version,
        "action_probabilities": validated.probabilities(context),
        "proposed_parameters": proposal.action,
        "logprob": proposal.logprob,
        "heldout_prediction_coverage_lower_bound": proposal.confidence,
        "in_distribution": proposal.in_distribution,
        "executed": False,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
