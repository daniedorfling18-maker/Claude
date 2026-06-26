from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.dashboard import render_dashboard
from polymarket_predictive_engine.profit_target import write_profit_target_tracker
from polymarket_predictive_engine.utils import read_json, write_csv, write_json


def _config(tmp_path: Path):
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    raw["profit_tracking"]["minimum_tracking_hours_for_on_pace"] = 1
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def test_profit_target_creates_clean_baseline_and_tracks_run_rate(tmp_path):
    cfg = _config(tmp_path)
    first = write_profit_target_tracker(
        cfg,
        {
            "equity": 1000,
            "cash": 950,
            "total_exposure": 50,
            "generated_at_utc": "2026-06-25T00:00:00Z",
        },
    )
    assert first["status"] == "collecting_forward_evidence"
    assert first["actual_pnl_since_baseline_usdc"] == 0

    baseline_path = cfg.governance_root / "paper_profit_target_baseline.json"
    baseline = read_json(baseline_path)
    baseline["created_at_utc"] = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    baseline["baseline_equity_usdc"] = 1000
    write_json(baseline_path, baseline)

    second = write_profit_target_tracker(
        cfg,
        {
            "equity": 1010,
            "cash": 940,
            "total_exposure": 70,
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )
    assert second["actual_pnl_since_baseline_usdc"] == 10
    assert second["monthly_run_rate_usdc"] > 100
    assert second["status"] == "on_pace"


def test_dashboard_renderer_writes_static_dashboard_and_data(tmp_path):
    cfg = _config(tmp_path)
    write_json(
        cfg.governance_root / "live_paper_loop_heartbeat.json",
        {"status": "ran", "scan": {"tokens": 82}},
    )
    write_csv(
        cfg.output_root / "polymarket_portfolio" / "positions.csv",
        [
            {
                "market_id": "m1",
                "token_id": "t1",
                "average_entry_price": "0.1",
                "cost_basis_usdc": "5",
                "quantity": "50",
                "status": "open",
            }
        ],
    )
    write_csv(
        cfg.output_root / "polymarket_portfolio" / "paper_fills.csv",
        [{"created_at": "2026-06-25T00:00:00Z", "market_id": "m1", "token_id": "t1", "fill_price": "0.1"}],
    )
    write_csv(
        cfg.output_root / "polymarket_predictions" / "trade_signals.csv",
        [{"market_slug": "test-market", "edge": "0.04", "priority_score": "4"}],
    )
    result = render_dashboard(
        cfg,
        {
            "status": "ran",
            "broker": {"equity": 1000, "cash": 995, "total_exposure": 5},
            "actual_profit_target": {"status": "collecting_forward_evidence", "actual_pnl_since_baseline_usdc": 0},
        },
    )
    assert result["status"] == "ok"
    assert Path(result["dashboard_file"]).exists()
    data = read_json(result["dashboard_data"])
    assert data["positions"][0]["token_id"] == "t1"
    assert data["approved_signals"][0]["market_slug"] == "test-market"

def test_dashboard_explains_no_trade_when_fast_candidates_are_quarantined(tmp_path):
    cfg = _config(tmp_path)
    write_csv(
        cfg.output_root / "polymarket_predictions" / "predictions.csv",
        [
            {
                "market_slug": "btc-updown-5m-1782490200",
                "outcome": "Up",
                "signal_cohort": "exploratory_crypto_updown_live_model|crypto_btc_updown_5m|outcome=up",
                "shadow_trade_candidate": "True",
                "shadow_candidate_reason": "shadow_eligible",
                "crypto_model_contract_kind": "fast",
                "crypto_model_edge_after_cost": "0.08",
                "spread": "0.01",
                "liquidity": "1200",
                "shadow_priority_score": "0.12",
            }
        ],
    )
    write_csv(
        cfg.output_root / "polymarket_predictions" / "rejected_signals.csv",
        [
            {
                "market_slug": "btc-updown-5m-1782490200",
                "outcome": "Up",
                "rejection_reason": "alpha lower-bound edge below configured minimum",
            }
        ],
    )
    write_json(
        cfg.governance_root / "shadow_signal_cohort_pnl.json",
        {
            "shadow_candidates_seen": 1,
            "opened_this_cycle": 0,
            "open_positions": 3,
            "quarantined_cohorts": [
                {
                    "signal_cohort": "exploratory_crypto_updown_live_model|crypto_btc_updown_5m|outcome=up",
                    "closed_positions": 3,
                    "closed_realised_pnl_usdc": -7.0,
                    "closed_roi": -0.23,
                    "quarantine_reason": "closed evidence below threshold",
                }
            ],
        },
    )

    result = render_dashboard(
        cfg,
        {
            "status": "ran",
            "broker": {"equity": 1000, "cash": 1000, "total_exposure": 0},
            "actual_profit_target": {"status": "collecting_forward_evidence", "actual_pnl_since_baseline_usdc": 0},
        },
    )

    html = Path(result["dashboard_file"]).read_text(encoding="utf-8")
    data = read_json(result["dashboard_data"])
    diagnostics = data["trade_diagnostics"]
    assert "Why no trade?" in html
    assert diagnostics["approved_signals_count"] == 0
    assert diagnostics["shadow_candidates_seen"] == 1
    assert diagnostics["quarantined_cohort_count"] == 1
    assert "quarantined" in diagnostics["main_blocker"]
    assert diagnostics["current_shadow_candidates"][0]["market_slug"] == "btc-updown-5m-1782490200"
