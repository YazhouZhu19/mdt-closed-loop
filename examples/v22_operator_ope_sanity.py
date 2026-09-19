"""Exact toy checks for operator-aware OPE; no data, fitting, or new estimator.

Run from code/: python3 examples/v22_operator_ope_sanity.py \
    --output docs/validation/v22_operator_ope_sanity.json

All mathematical calculations use fractions.Fraction. JSON decimals are display
approximations; the adjacent exact fractions are the authoritative values.
"""

from __future__ import annotations

import argparse
import json
from fractions import Fraction
from pathlib import Path
from typing import TypedDict

F = Fraction


class DiscreteFixture(TypedDict):
    context: str
    probability: Fraction
    behavior: tuple[Fraction, ...]
    target: tuple[Fraction, ...]
    execution: tuple[str, ...]
    reward_probability: dict[str, Fraction]


def quantity(value: Fraction) -> dict[str, str | float]:
    """Serialize exact rational results without claiming numerical estimation."""
    return {"exact": str(value), "decimal": float(value)}


def discrete_same_operator_check() -> dict:
    """Enumerate two contexts, four proposals, and Bernoulli reward outcomes."""
    fixtures: tuple[DiscreteFixture, ...] = (
        {
            "context": "x0",
            "probability": F(1, 3),
            "behavior": (F(1, 10), F(2, 10), F(3, 10), F(4, 10)),
            "target": (F(4, 10), F(1, 10), F(1, 10), F(4, 10)),
            "execution": ("e0", "e0", "e1", "e1"),
            "reward_probability": {"e0": F(1, 4), "e1": F(3, 4)},
        },
        {
            "context": "x1",
            "probability": F(2, 3),
            "behavior": (F(4, 10), F(3, 10), F(2, 10), F(1, 10)),
            "target": (F(1, 10), F(4, 10), F(4, 10), F(1, 10)),
            "execution": ("e0", "e1", "e1", "e2"),
            "reward_probability": {"e0": F(1, 5), "e1": F(1, 2), "e2": F(4, 5)},
        },
    )
    oracle = F(0)
    expectations = {"raw_ips": F(0), "execution_mips": F(0)}
    second_moments = {"raw_ips": F(0), "execution_mips": F(0)}
    rows = []
    coarsening_identities = []
    for fixture in fixtures:
        context_probability = fixture["probability"]
        behavior = fixture["behavior"]
        target = fixture["target"]
        executions = fixture["execution"]
        rewards = fixture["reward_probability"]
        assert sum(behavior) == sum(target) == 1
        assert all(probability > 0 for probability in behavior)
        behavior_execution = {
            executed: sum(
                (p for p, e in zip(behavior, executions) if e == executed), F(0)
            )
            for executed in rewards
        }
        target_execution = {
            executed: sum(
                (p for p, e in zip(target, executions) if e == executed), F(0)
            )
            for executed in rewards
        }
        for executed in rewards:
            conditional_raw_weight = sum(
                (
                    behavior[action]
                    / behavior_execution[executed]
                    * target[action]
                    / behavior[action]
                    for action, e in enumerate(executions)
                    if e == executed
                ),
                F(0),
            )
            execution_weight = (
                target_execution[executed] / behavior_execution[executed]
            )
            assert conditional_raw_weight == execution_weight
            coarsening_identities.append(
                {
                    "context": fixture["context"],
                    "execution": executed,
                    "conditional_mean_raw_weight": quantity(conditional_raw_weight),
                    "execution_weight": quantity(execution_weight),
                    "equal_exactly": True,
                }
            )
        for action, executed in enumerate(executions):
            reward_probability = rewards[executed]
            weights = {
                "raw_ips": target[action] / behavior[action],
                "execution_mips": (
                    target_execution[executed] / behavior_execution[executed]
                ),
            }
            oracle += context_probability * target[action] * reward_probability
            # Explicit outcomes ensure E[R^2] = q, not q^2, for Bernoulli R.
            for reward, reward_mass in (
                (0, 1 - reward_probability),
                (1, reward_probability),
            ):
                outcome_mass = context_probability * behavior[action] * reward_mass
                for method, weight in weights.items():
                    contribution = weight * reward
                    expectations[method] += outcome_mass * contribution
                    second_moments[method] += outcome_mass * contribution**2
            rows.append(
                {
                    "context": fixture["context"],
                    "context_probability": quantity(context_probability),
                    "proposal": f"a{action}",
                    "execution": executed,
                    "behavior_probability": quantity(behavior[action]),
                    "target_probability": quantity(target[action]),
                    "reward_bernoulli_probability": quantity(reward_probability),
                    "behavior_execution_probability": quantity(
                        behavior_execution[executed]
                    ),
                    "target_execution_probability": quantity(
                        target_execution[executed]
                    ),
                    "raw_weight": quantity(weights["raw_ips"]),
                    "execution_weight": quantity(weights["execution_mips"]),
                }
            )
    variances = {
        method: second_moments[method] - expectations[method] ** 2
        for method in expectations
    }
    assert expectations["raw_ips"] == expectations["execution_mips"] == oracle
    assert variances["execution_mips"] <= variances["raw_ips"]
    return {
        "setting": "constructed_iid_contextual_bandit_with_fixed_operator",
        "assumptions": [
            "Same known deterministic execution map for behavior and target.",
            "Known positive behavior probabilities for every proposal.",
            "Same context distribution in behavior and target.",
            "Reward conditional on context and execution is Bernoulli(q).",
            "Proposal has no direct reward effect given context and execution.",
            "Finite action space and bounded rewards give finite second moments.",
        ],
        "oracle_target_value": quantity(oracle),
        "estimators": {
            method: {
                "expected_value": quantity(expectations[method]),
                "bias": quantity(expectations[method] - oracle),
                "second_moment_one_observation": quantity(second_moments[method]),
                "variance_one_observation": quantity(variances[method]),
                "variance_of_iid_mean_n_1000": quantity(variances[method] / 1000),
            }
            for method in expectations
        },
        "variance_reduction_one_observation": quantity(
            variances["raw_ips"] - variances["execution_mips"]
        ),
        "coarsening_identities": coarsening_identities,
        "enumerated_proposals": rows,
        "interpretation": (
            "Both estimators are exactly unbiased in this toy fixture. Raw IPS is "
            "valid with the fixed execution map; its variance is larger here. "
            "This reproduces a known MIPS special case, not a new result."
        ),
    }


def uniform_clip_migration_check() -> dict:
    """Analytic singular-support example with A ~ Uniform[-2, 2].

    The raw policies are identical. Only clipping changes. For oracle checking
    use q(e)=e^2 on [-1,1]. For the no-assumption identification bounds, the
    evaluator is allowed only the logging law and 0 <= q <= 1, not the formula.
    """
    density = F(1, 4)
    log_lower, log_upper = F(-1), F(1)
    target_lower, target_upper = F(-1, 2), F(1, 2)
    log_atom_mass = F(1, 4)
    target_atom_mass = F(3, 8)
    singular_mass = 2 * target_atom_mass
    target_continuous_mass = density * (target_upper - target_lower)
    # Integral of e^2 / 4 on the respective open interval.
    identified_continuous_value = (
        density * (target_upper**3 - target_lower**3) / 3
    )
    oracle_log_value = (
        density * (log_upper**3 - log_lower**3) / 3
        + log_atom_mass * (log_lower**2 + log_upper**2)
    )
    oracle_target_value = identified_continuous_value + target_atom_mass * (
        target_lower**2 + target_upper**2
    )
    lower_bound = identified_continuous_value
    upper_bound = identified_continuous_value + singular_mass
    assert target_continuous_mass + singular_mass == 1
    assert lower_bound <= oracle_target_value <= upper_bound
    return {
        "raw_behavior_and_target": "Uniform[-2, 2], identical raw policies",
        "logging_operator": "clip(a, -1, 1)",
        "target_operator": "clip(a, -1/2, 1/2)",
        "logging_distribution": {
            "interior_interval": [-1, 1],
            "interior_density": quantity(density),
            "mass_at_each_endpoint": quantity(log_atom_mass),
        },
        "target_distribution": {
            "interior_interval": [-0.5, 0.5],
            "interior_density": quantity(density),
            "mass_at_each_endpoint": quantity(target_atom_mass),
            "continuous_mass": quantity(target_continuous_mass),
        },
        "new_boundary_events": [
            {
                "event": f"execution == {endpoint}",
                "logging_event_probability": quantity(F(0)),
                "logging_interior_density_at_point": quantity(density),
                "target_event_probability": quantity(target_atom_mass),
                "importance_weight": None,
                "weight_status": "undefined_target_mass_over_zero_logging_mass",
            }
            for endpoint in (target_lower, target_upper)
        ],
        "topological_support_inclusion": True,
        "target_absolutely_continuous_with_respect_to_logging": False,
        "target_singular_mass": quantity(singular_mass),
        "oracle_reward_fixture": "q(e) = e^2 on [-1, 1]",
        "oracle_logging_value": quantity(oracle_log_value),
        "oracle_target_value": quantity(oracle_target_value),
        "raw_ips_weight": quantity(F(1)),
        "raw_ips_expected_value_under_old_logging_law": quantity(oracle_log_value),
        "raw_ips_bias_for_new_operator_value": quantity(
            oracle_log_value - oracle_target_value
        ),
        "no_smoothness_identification_bound": {
            "assumptions": (
                "Population logging law known; bounded reward in [0, 1]; "
                "no continuity, parametric reward model, or new data. The oracle "
                "reward formula is not supplied to the evaluator."
            ),
            "identified_absolutely_continuous_contribution": quantity(
                identified_continuous_value
            ),
            "lower": quantity(lower_bound),
            "upper": quantity(upper_bound),
            "width": quantity(upper_bound - lower_bound),
            "construction": (
                "Two witness reward functions equal e^2 except at {-1/2, 1/2}. "
                "Set their means at those points to 0 and 1 respectively. They "
                "induce identical logging laws because logging assigns zero mass "
                "to those events, but achieve the stated target-value endpoints."
            ),
            "not_a_sampling_confidence_interval": True,
            "not_a_new_theorem": True,
        },
        "interpretation": (
            "Positive logging density is not positive event probability. The new "
            "boundary atoms require a measure-level support check; dividing their "
            "mass by the logging density is invalid. Under additional continuity "
            "of the reward mean, neighborhood limits may identify these interior "
            "points at the population level, but do not create ordinary IS "
            "weights. This file does not implement smoothness estimation."
        ),
    }


def build_report() -> dict:
    return {
        "schema_version": "1.0",
        "provenance": "constructed_mathematical_sanity_check",
        "real_data_used": False,
        "sampling_used": False,
        "method": "exact_fraction_enumeration_and_analytic_uniform_integrals",
        "reproduction_command": (
            "python3 examples/v22_operator_ope_sanity.py "
            "--output docs/validation/v22_operator_ope_sanity.json"
        ),
        "claims": {
            "new_algorithm": False,
            "novelty_evidence": False,
            "clinical_or_sleep_effect_evidence": False,
            "runtime_safety_validation": False,
        },
        "limitations": [
            "Constructed toy arithmetic, not a collected dataset or benchmark.",
            "No sequential OPE, hidden state, ACK missingness, or fitted propensity.",
            "No finite-sample confidence interval or empirical coverage estimate.",
            "The n=1000 variance is theoretical for iid sample means, not a run.",
            "Known results and counterexamples do not establish paper novelty.",
        ],
        "fixed_operator_discrete_bandit": discrete_same_operator_check(),
        "changed_operator_uniform_clipping": uniform_clip_migration_check(),
        "references": [
            {
                "document_reference": "19",
                "title": "Off-Policy Evaluation for Large Action Spaces via Embeddings",
                "authors": "Saito and Joachims",
                "year": 2022,
                "url": "https://proceedings.mlr.press/v162/saito22a.html",
                "use": "Existing MIPS and its fixed deterministic embedding case.",
            },
            {
                "document_reference": "27",
                "title": (
                    "Off-policy Evaluation Beyond Overlap: Sharp Partial "
                    "Identification Under Smoothness"
                ),
                "authors": "Khan, Saveski, and Ugander",
                "year": 2024,
                "url": "https://proceedings.mlr.press/v235/khan24b.html",
                "use": (
                    "Existing beyond-overlap partial identification; this file "
                    "only illustrates the elementary bounded-reward case."
                ),
            },
            {
                "document_reference": "28",
                "title": "Mechanizing Soundness of Off-Policy Evaluation",
                "authors": "Yeager, Moss, Norrish, and Thomas",
                "year": 2022,
                "url": (
                    "https://drops.dagstuhl.de/entities/document/"
                    "10.4230/LIPIcs.ITP.2022.32"
                ),
                "use": "Existing mixed-measure OPE and a clipped-action example.",
            },
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    serialized = json.dumps(build_report(), indent=2, ensure_ascii=False) + "\n"
    if args.output is None:
        print(serialized, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
        print(f"Wrote constructed mathematical check: {args.output}")


if __name__ == "__main__":
    main()
