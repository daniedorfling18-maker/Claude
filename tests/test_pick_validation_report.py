from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def load_script():
    path = ROOT / "scripts/build_pick_validation_report.py"
    spec = importlib.util.spec_from_file_location("build_pick_validation_report", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_report_summary_calculates_mc_half_width():
    script = load_script()
    report = {
        "summaries": {
            "base_mc": {"final_p_finish_first": 0.32},
            "confirmation_500k": {
                "final_p_finish_first": 0.321,
                "final_p_finish_first_or_tied": 0.38,
                "simulations": 500000,
            },
        },
        "stress": {"robust_switch_count": 0, "scenario_count": 11},
        "quality_gates": [{"passed": True}],
        "accepted_policy_switches": [],
    }

    summary = script.report_summary(report)

    assert summary["confirmation_simulations"] == 500000
    assert summary["confirmation_95pct_half_width"] > 0
    assert summary["quality_gates_passed"] is True


def test_build_pick_report_flags_fragile_high_risk_lower():
    script = load_script()
    picks = pd.DataFrame([
        {
            "commence_time": "2026-06-24T19:00:00Z",
            "home_team": "Switzerland",
            "away_team": "Canada",
            "raw_pick": "2-1",
            "final_pick": "2-1",
            "final_switched": False,
            "risk_tier": "high",
            "confidence_tier": "fragile",
        },
        {
            "commence_time": "2026-06-24T22:00:00Z",
            "home_team": "Morocco",
            "away_team": "Haiti",
            "raw_pick": "2-0",
            "final_pick": "2-0",
            "final_switched": False,
            "risk_tier": "low",
            "confidence_tier": "strong",
        },
    ])
    stress = pd.DataFrame([
        {
            "home_team": "Switzerland",
            "away_team": "Canada",
            "raw_pick": "2-1",
            "candidate_pick": "1-1",
            "pass_rate": 0.0,
            "positive_delta_rate": 0.8,
            "mean_delta_p_finish_first": 0.001,
        }
    ])
    final_report = {
        "summaries": {"base_mc": {"final_p_finish_first": 0.32}, "confirmation_500k": {"final_p_finish_first": 0.32, "simulations": 100000}},
        "stress": {"robust_switch_count": 0, "scenario_count": 11},
        "quality_gates": [{"passed": True}],
        "accepted_policy_switches": [],
    }

    report, summary = script.build_pick_report(picks, pd.DataFrame(), stress, final_report, {})

    swiss = report[report["home_team"] == "Switzerland"].iloc[0]
    morocco = report[report["home_team"] == "Morocco"].iloc[0]
    assert swiss["validation_score"] < morocco["validation_score"]
    assert summary["pick_count"] == 2


def test_markdown_report_contains_lowest_validation_section():
    script = load_script()
    report = pd.DataFrame([
        {"home_team": "A", "away_team": "B", "final_pick": "1-0", "validation_score": 0.5, "validation_label": "fragile", "validation_reasons": "test"}
    ])
    text = script.markdown_report(report, {"generated_at_utc": "now"})
    assert "Lowest validation picks" in text
    assert "A vs B" in text
