from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_goal_status_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "polymarket_goal_status.py"
    spec = importlib.util.spec_from_file_location("polymarket_goal_status", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_goal_status_excludes_known_bad_or_quarantined_cohorts_from_promotion_language():
    module = _load_goal_status_module()

    assert module.cohort_gate_label(
        {"signal_cohort": "near_miss_learning|unknown", "promotion_ready_score": 6, "promotion_ready_checks": 6},
        quarantined=set(),
    ) == "not eligible: excluded_unknown_family"
    assert module.cohort_gate_label(
        {
            "signal_cohort": "exploratory_historical_rule|crypto_btc_updown_5m|outcome=down",
            "promotion_ready_score": 6,
            "promotion_ready_checks": 6,
        },
        quarantined=set(),
    ) == "not eligible: excluded_fast_crypto_5m"
    assert module.cohort_gate_label(
        {"signal_cohort": "macro_economy", "promotion_ready_score": 6, "promotion_ready_checks": 6},
        quarantined={"macro_economy"},
    ) == "not eligible: quarantined"


def test_goal_status_only_marks_full_non_excluded_cohorts_as_review_eligible():
    module = _load_goal_status_module()

    assert module.cohort_gate_label(
        {"signal_cohort": "macro_economy", "promotion_ready_score": 5, "promotion_ready_checks": 6},
        quarantined=set(),
    ) == "not eligible: needs more forward evidence"
    assert module.cohort_gate_label(
        {"signal_cohort": "macro_economy", "promotion_ready_score": 6, "promotion_ready_checks": 6},
        quarantined=set(),
    ) == "eligible for promotion review"
