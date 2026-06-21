from __future__ import annotations

from collections import Counter
from dataclasses import replace
from typing import Callable, Iterable

import numpy as np

from superbru_score_engine.config import PublicPickConfig, SensitivityConfig, SuperbruConfig
from superbru_score_engine.model.dixon_coles import apply_dixon_coles
from superbru_score_engine.model.poisson import independent_poisson_matrix

ScenarioPickFn = Callable[[np.ndarray, SuperbruConfig, PublicPickConfig], str]


def build_sensitivity_scenarios(
    *,
    base_matrix: np.ndarray,
    lambda_home: float,
    lambda_away: float,
    rho: float,
    superbru: SuperbruConfig,
    public_pick: PublicPickConfig,
    sensitivity: SensitivityConfig,
) -> list[tuple[str, np.ndarray, SuperbruConfig, PublicPickConfig]]:
    max_goals = base_matrix.shape[0] - 1
    total = max(0.10, float(lambda_home) + float(lambda_away))
    home_share = float(lambda_home) / total

    def matrix_for(home_lambda: float, away_lambda: float, scenario_rho: float = rho) -> np.ndarray:
        home_lambda = max(0.05, float(home_lambda))
        away_lambda = max(0.05, float(away_lambda))
        matrix = independent_poisson_matrix(home_lambda, away_lambda, max_goals)
        return apply_dixon_coles(matrix, home_lambda, away_lambda, scenario_rho)

    scenarios: list[tuple[str, np.ndarray, SuperbruConfig, PublicPickConfig]] = []
    pct = max(0.0, sensitivity.lambda_pct)
    scenarios.extend(
        [
            ("lambda_home_up", matrix_for(lambda_home * (1.0 + pct), lambda_away), superbru, public_pick),
            ("lambda_home_down", matrix_for(lambda_home * max(0.05, 1.0 - pct), lambda_away), superbru, public_pick),
            ("lambda_away_up", matrix_for(lambda_home, lambda_away * (1.0 + pct)), superbru, public_pick),
            ("lambda_away_down", matrix_for(lambda_home, lambda_away * max(0.05, 1.0 - pct)), superbru, public_pick),
        ]
    )

    rho_delta = max(0.0, sensitivity.rho_delta)
    scenarios.append(("rho_up", matrix_for(lambda_home, lambda_away, rho + rho_delta), superbru, public_pick))
    scenarios.append(("rho_down", matrix_for(lambda_home, lambda_away, rho - rho_delta), superbru, public_pick))

    total_delta = max(0.0, sensitivity.total_goals_delta)
    for name, scenario_total in (
        ("total_goals_up", total + total_delta),
        ("total_goals_down", max(0.10, total - total_delta)),
    ):
        scenarios.append(
            (
                name,
                matrix_for(scenario_total * home_share, scenario_total * (1.0 - home_share)),
                superbru,
                public_pick,
            )
        )

    bias_pct = max(0.0, sensitivity.public_bias_pct)
    scenarios.append(
        (
            "public_favourite_bias_up",
            base_matrix,
            superbru,
            replace(public_pick, favourite_bias=public_pick.favourite_bias * (1.0 + bias_pct)),
        )
    )
    scenarios.append(
        (
            "public_favourite_bias_down",
            base_matrix,
            superbru,
            replace(public_pick, favourite_bias=public_pick.favourite_bias * max(0.05, 1.0 - bias_pct)),
        )
    )
    scenarios.append(
        (
            "public_draw_penalty_up",
            base_matrix,
            superbru,
            replace(public_pick, draw_penalty=public_pick.draw_penalty * (1.0 + bias_pct)),
        )
    )
    scenarios.append(
        (
            "public_draw_penalty_down",
            base_matrix,
            superbru,
            replace(public_pick, draw_penalty=public_pick.draw_penalty * max(0.05, 1.0 - bias_pct)),
        )
    )

    for weight in sensitivity.exact_chase_weights:
        if abs(float(weight) - superbru.exact_chase_weight) > 1e-12:
            scenarios.append((f"exact_chase_weight_{float(weight):g}", base_matrix, replace(superbru, exact_chase_weight=float(weight)), public_pick))

    return scenarios


def summarise_sensitivity(
    *,
    base_scoreline: str,
    scenarios: Iterable[tuple[str, np.ndarray, SuperbruConfig, PublicPickConfig]],
    pick_fn: ScenarioPickFn,
    warning_threshold: float,
) -> dict[str, object]:
    rows: list[dict[str, str]] = []
    for name, matrix, superbru, public_pick in scenarios:
        rows.append({"scenario": name, "scoreline": pick_fn(matrix, superbru, public_pick)})

    scenario_count = len(rows)
    unchanged = sum(1 for row in rows if row["scoreline"] == base_scoreline)
    changed = scenario_count - unchanged
    stability = unchanged / scenario_count if scenario_count else 1.0
    alternatives = Counter(row["scoreline"] for row in rows if row["scoreline"] != base_scoreline)
    most_common_alt = alternatives.most_common(1)[0][0] if alternatives else ""

    return {
        "sensitivity_scenario_count": scenario_count,
        "sensitivity_changed_count": changed,
        "sensitivity_stability": float(stability),
        "sensitivity_warning": bool(stability < warning_threshold),
        "sensitivity_most_common_alternative": most_common_alt,
        "sensitivity_scenarios": rows,
        "sensitivity_ratings_weight_note": "Ratings-weight perturbation requires rebuilding the odds-to-scoreline distribution; cover it through backtest/profile sweeps rather than decision-only sensitivity.",
    }
