from __future__ import annotations

import numpy as np

from superbru_score_engine.config import PublicPickConfig, SensitivityConfig, SuperbruConfig
from superbru_score_engine.decision.sensitivity import build_sensitivity_scenarios, summarise_sensitivity


def test_sensitivity_scenarios_are_generated() -> None:
    matrix = np.ones((4, 4), dtype=float)
    matrix /= matrix.sum()
    scenarios = build_sensitivity_scenarios(
        base_matrix=matrix,
        lambda_home=1.4,
        lambda_away=1.0,
        rho=-0.08,
        superbru=SuperbruConfig(),
        public_pick=PublicPickConfig(),
        sensitivity=SensitivityConfig(exact_chase_weights=(1.0, 2.0)),
    )
    names = {name for name, *_ in scenarios}
    assert "lambda_home_up" in names
    assert "rho_down" in names
    assert "total_goals_up" in names
    assert "public_favourite_bias_down" in names
    assert "exact_chase_weight_2" in names


def test_sensitivity_summary_flags_instability() -> None:
    matrix = np.ones((3, 3), dtype=float)
    scenarios = [
        ("a", matrix, SuperbruConfig(), PublicPickConfig()),
        ("b", matrix, SuperbruConfig(), PublicPickConfig()),
        ("c", matrix, SuperbruConfig(), PublicPickConfig()),
    ]
    picks = iter(["1-0", "2-0", "2-0"])
    summary = summarise_sensitivity(
        base_scoreline="1-0",
        scenarios=scenarios,
        pick_fn=lambda *_: next(picks),
        warning_threshold=0.70,
    )
    assert summary["sensitivity_scenario_count"] == 3
    assert summary["sensitivity_changed_count"] == 2
    assert summary["sensitivity_stability"] == 1 / 3
    assert summary["sensitivity_warning"] is True
    assert summary["sensitivity_most_common_alternative"] == "2-0"
