from __future__ import annotations

from pathlib import Path

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.dashboard_proof_questions import apply_dashboard_proof_questions, build_proof_questions
from polymarket_predictive_engine.utils import read_json, write_json


def _cfg(tmp_path: Path) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "closing_line_value": {"minimum_final_samples": 3},
        },
        path=tmp_path / "cfg.yaml",
    )


def _dashboard_data() -> dict[str, object]:
    return {
        "sharp_sports_funnel": {
            "total_anchor_rows": 5,
            "total_scored_anchor_hits": 3,
        },
        "dutch_arb": {
            "persistent_alert_count": 1,
            "persistent_alerts": [{"event": "basket", "consecutive_scans_above_alert": 3}],
        },
        "closing_line_value": {
            "focus_view": {
                "focus_final_positions": 4,
                "focus_mean_final_clv": 0.012,
                "focus_positive_cohorts": ["sharp|worldcup"],
            }
        },
        "decision_useful_summary": {
            "decision_pnl_usdc": 0.42,
            "proof_verified_round_trips_since_baseline": 5,
            "minimum_audited_round_trips_for_on_pace": 5,
            "profit_target_proof_status": "proof_ready",
        },
    }


def test_build_proof_questions_answers_all_four_streams(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    proof = build_proof_questions(
        cfg,
        dashboard_data=_dashboard_data(),
        sharp_anchor_coverage={"total_rows_fetched": 9, "total_rows_mapped": 5, "flagged_no_mappable_market_count": 0},
    )

    assert proof["status"] == "good"
    assert [row["question"] for row in proof["questions"]] == [
        "Sharp-anchor rows mapped?",
        "Dutch-arb persistent opportunities?",
        "Focus-view CLV positive with enough samples?",
        "Audited paper P&L positive after governed probes?",
    ]
    assert proof["paper_trading_invoked"] is False
    assert proof["live_trading_invoked"] is False


def test_apply_dashboard_proof_questions_writes_data_and_html_overlay(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    dashboard_root = cfg.output_root / "polymarket_dashboard"
    dashboard_root.mkdir(parents=True)
    write_json(dashboard_root / "dashboard_data.json", _dashboard_data())
    (dashboard_root / "index.html").write_text(
        "<html><body><section><h2>Evidence funnel</h2><div id=\"evidenceFunnel\"></div></section></body></html>",
        encoding="utf-8",
    )

    result = apply_dashboard_proof_questions(
        cfg,
        sharp_anchor_coverage={"total_rows_fetched": 9, "total_rows_mapped": 5, "flagged_no_mappable_market_count": 0},
    )

    assert result["status"] == "ok"
    assert result["proof_status"] == "good"
    payload = read_json(dashboard_root / "dashboard_data.json")
    assert payload["proof_questions"]["status"] == "good"
    assert len(payload["proof_questions"]["questions"]) == 4
    html = (dashboard_root / "index.html").read_text(encoding="utf-8")
    assert "Four proof questions" in html
    assert "proof-questions-overlay:start" in html
