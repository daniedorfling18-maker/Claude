from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import yaml
from pytest import approx

from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.dashboard import render_dashboard
from polymarket_predictive_engine.execution.paper import paper_trade_report
from polymarket_predictive_engine.profit_target import reset_profit_target_baseline, write_profit_target_tracker
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
    assert second["status"] == "collecting_verified_paper_evidence"
    assert second["on_pace_by_actual_pnl"] is True
    assert second["on_pace_by_decision_pnl"] is False
    assert second["profit_target_proof_status"] == "unverified_paper_run_rate"
    assert "paper_round_trip_audit_missing" in second["profit_target_proof_blockers"]

    write_json(
        cfg.output_root / "polymarket_price_action" / "paper_broker_round_trip_summary.json",
        {
            "audited_baseline_realized_pnl_usdc": 10,
            "audited_baseline_closed_round_trips": 5,
            "baseline_closed_round_trips": 5,
            "quote_conflict_round_trips": 0,
            "quote_unverified_round_trips": 0,
        },
    )
    verified = write_profit_target_tracker(
        cfg,
        {
            "equity": 1010,
            "cash": 940,
            "total_exposure": 70,
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )
    assert verified["status"] == "on_pace"
    assert verified["on_pace_by_decision_pnl"] is True
    assert verified["profit_target_proof_status"] == "verified_quote_consistent"
    assert verified["profit_target_proof_blockers"] == []


def test_profit_target_uses_audited_pnl_when_raw_ledger_has_quote_conflicts(tmp_path):
    cfg = _config(tmp_path)
    write_json(
        cfg.governance_root / "paper_profit_target_baseline.json",
        {
            "created_at_utc": (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "baseline_equity_usdc": 1000,
            "baseline_cash_usdc": 1000,
            "baseline_total_exposure_usdc": 0,
        },
    )
    write_json(
        cfg.output_root / "polymarket_price_action" / "paper_broker_round_trip_summary.json",
        {
            "audited_baseline_realized_pnl_usdc": 0.22,
            "audited_baseline_closed_round_trips": 5,
            "baseline_closed_round_trips": 11,
            "quote_conflict_round_trips": 5,
            "quote_unverified_round_trips": 6,
        },
    )

    payload = write_profit_target_tracker(
        cfg,
        {
            "equity": 1054.66,
            "cash": 1054.66,
            "total_exposure": 0,
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )

    assert payload["status"] == "not_on_pace"
    assert payload["actual_pnl_since_baseline_usdc"] == approx(54.66)
    assert payload["raw_account_pnl_since_baseline_usdc"] == approx(54.66)
    assert payload["audited_pnl_since_baseline_usdc"] == approx(0.22)
    assert payload["decision_pnl_usdc"] == approx(0.22)
    assert payload["pnl_audit_state"] == "raw_pnl_contains_quote_conflicts"
    assert payload["profit_target_proof_status"] == "unverified_paper_run_rate"
    assert "quote_conflict_round_trips_5" in payload["profit_target_proof_blockers"]
    assert payload["quote_conflict_round_trips"] == 5
    assert payload["quote_unverified_round_trips"] == 6
    assert payload["raw_account_monthly_run_rate_usdc"] > 100
    assert payload["decision_monthly_run_rate_usdc"] < 100


def test_profit_target_uses_baseline_quote_audit_window_not_all_history(tmp_path):
    cfg = _config(tmp_path)
    write_json(
        cfg.governance_root / "paper_profit_target_baseline.json",
        {
            "created_at_utc": (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "baseline_equity_usdc": 1000,
            "baseline_cash_usdc": 1000,
            "baseline_total_exposure_usdc": 0,
        },
    )
    write_json(
        cfg.output_root / "polymarket_price_action" / "paper_broker_round_trip_summary.json",
        {
            "audited_baseline_realized_pnl_usdc": 10.0,
            "audited_baseline_closed_round_trips": 5,
            "baseline_closed_round_trips": 5,
            "baseline_quote_conflict_round_trips": 0,
            "baseline_quote_unverified_round_trips": 0,
            "quote_conflict_round_trips": 7,
            "quote_unverified_round_trips": 2,
        },
    )

    payload = write_profit_target_tracker(
        cfg,
        {
            "equity": 1010,
            "cash": 1010,
            "total_exposure": 0,
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )

    assert payload["status"] == "on_pace"
    assert payload["profit_target_proof_status"] == "verified_quote_consistent"
    assert payload["profit_target_proof_blockers"] == []
    assert payload["pnl_audit_state"] == "quote_consistent"
    assert payload["quote_conflict_round_trips"] == 0
    assert payload["quote_unverified_round_trips"] == 0
    assert payload["all_time_quote_conflict_round_trips"] == 7
    assert payload["all_time_quote_unverified_round_trips"] == 2
    assert payload["on_pace_by_decision_pnl"] is True


def test_profit_target_reset_archives_history_and_restarts_active_proof_window(tmp_path):
    cfg = _config(tmp_path)
    write_json(
        cfg.governance_root / "paper_profit_target_baseline.json",
        {
            "created_at_utc": (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "baseline_equity_usdc": 1000,
            "baseline_cash_usdc": 1000,
            "baseline_total_exposure_usdc": 0,
        },
    )
    write_json(
        cfg.governance_root / "paper_profit_target_tracker.json",
        {
            "status": "unverified_paper_run_rate",
            "decision_pnl_usdc": -0.19,
            "profit_target_proof_status": "unverified_paper_run_rate",
            "profit_target_proof_blockers": ["quote_conflict_round_trips_5"],
            "verified_evidence_ready": False,
        },
    )
    write_json(
        cfg.output_root / "polymarket_price_action" / "paper_broker_round_trip_summary.json",
        {
            "generated_at_utc": "2026-07-04T09:00:00Z",
            "closed_round_trips": 9,
            "quote_conflict_round_trips": 5,
            "quote_unverified_round_trips": 2,
            "baseline_closed_round_trips": 7,
            "baseline_quote_conflict_round_trips": 5,
            "baseline_quote_unverified_round_trips": 2,
            "baseline_proof_verified_closed_round_trips": 0,
            "baseline_proof_verified_realized_pnl_usdc": 0,
        },
    )

    result = reset_profit_target_baseline(
        cfg,
        {
            "equity": 1001.25,
            "cash": 991.25,
            "total_exposure": 10.0,
            "generated_at_utc": "2026-07-04T09:05:00Z",
        },
        reason="quote-capture instrumentation fixed",
        operator="codex-test",
    )

    assert result["status"] == "reset"
    assert result["historical_data_preserved"] is True
    baseline = read_json(cfg.governance_root / "paper_profit_target_baseline.json")
    assert baseline["baseline_equity_usdc"] == approx(1001.25)
    assert baseline["baseline_generation"] == 2
    assert baseline["reset_reason"] == "quote-capture instrumentation fixed"
    assert baseline["historical_data_preserved"] is True
    history = read_json(cfg.governance_root / "paper_profit_target_baseline_history.json")
    assert len(history) == 1
    assert history[0]["previous_baseline"]["baseline_equity_usdc"] == 1000
    assert history[0]["previous_round_trip_summary"]["quote_conflict_round_trips"] == 5
    assert history[0]["previous_tracker"]["profit_target_proof_blockers"] == ["quote_conflict_round_trips_5"]

    write_json(
        cfg.output_root / "polymarket_price_action" / "paper_broker_round_trip_summary.json",
        {
            "generated_at_utc": "2026-07-04T09:06:00Z",
            "baseline_start_utc": baseline["created_at_utc"],
            "closed_round_trips": 9,
            "quote_conflict_round_trips": 5,
            "quote_unverified_round_trips": 2,
            "baseline_closed_round_trips": 0,
            "baseline_quote_conflict_round_trips": 0,
            "baseline_quote_unverified_round_trips": 0,
            "baseline_proof_verified_closed_round_trips": 0,
            "baseline_proof_verified_realized_pnl_usdc": 0,
            "baseline_proof_entry_snapshot_missing_round_trips": 0,
            "baseline_proof_exit_snapshot_missing_round_trips": 0,
        },
    )
    tracker = write_profit_target_tracker(
        cfg,
        {
            "equity": 1001.25,
            "cash": 991.25,
            "total_exposure": 10.0,
            "generated_at_utc": baseline["created_at_utc"],
        },
    )

    assert tracker["profit_target_baseline_generation"] == 2
    assert tracker["profit_target_baseline_reset_reason"] == "quote-capture instrumentation fixed"
    assert tracker["quote_conflict_round_trips"] == 0
    assert tracker["quote_unverified_round_trips"] == 0
    assert tracker["all_time_quote_conflict_round_trips"] == 5
    assert tracker["all_time_quote_unverified_round_trips"] == 2
    assert "quote_conflict_round_trips_5" not in tracker["profit_target_proof_blockers"]
    assert "quote_unverified_round_trips_2" not in tracker["profit_target_proof_blockers"]


def test_profit_target_prefers_proof_verified_entry_exit_pnl_when_available(tmp_path):
    cfg = _config(tmp_path)
    write_json(
        cfg.governance_root / "paper_profit_target_baseline.json",
        {
            "created_at_utc": (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "baseline_equity_usdc": 1000,
            "baseline_cash_usdc": 1000,
            "baseline_total_exposure_usdc": 0,
        },
    )
    write_json(
        cfg.output_root / "polymarket_price_action" / "paper_broker_round_trip_summary.json",
        {
            "audited_baseline_realized_pnl_usdc": 12.0,
            "audited_baseline_closed_round_trips": 5,
            "baseline_proof_verified_realized_pnl_usdc": 2.5,
            "baseline_proof_verified_closed_round_trips": 5,
            "baseline_proof_entry_snapshot_missing_round_trips": 0,
            "baseline_proof_exit_snapshot_missing_round_trips": 0,
            "baseline_closed_round_trips": 5,
            "baseline_quote_conflict_round_trips": 0,
            "baseline_quote_unverified_round_trips": 0,
        },
    )

    payload = write_profit_target_tracker(
        cfg,
        {
            "equity": 1012,
            "cash": 1012,
            "total_exposure": 0,
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )

    assert payload["audited_pnl_since_baseline_usdc"] == approx(12.0)
    assert payload["proof_verified_pnl_since_baseline_usdc"] == approx(2.5)
    assert payload["decision_pnl_usdc"] == approx(2.5)
    assert payload["decision_pnl_source"] == "proof_verified_entry_exit_quotes"
    assert payload["profit_target_proof_status"] == "verified_entry_exit_quote_coverage"
    assert payload["verified_evidence_ready"] is True


def test_profit_target_blocks_goal_proof_when_entry_or_exit_quote_support_is_missing(tmp_path):
    cfg = _config(tmp_path)
    write_json(
        cfg.governance_root / "paper_profit_target_baseline.json",
        {
            "created_at_utc": (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "baseline_equity_usdc": 1000,
            "baseline_cash_usdc": 1000,
            "baseline_total_exposure_usdc": 0,
        },
    )
    write_json(
        cfg.output_root / "polymarket_price_action" / "paper_broker_round_trip_summary.json",
        {
            "audited_baseline_realized_pnl_usdc": 12.0,
            "audited_baseline_closed_round_trips": 6,
            "baseline_proof_verified_realized_pnl_usdc": 10.0,
            "baseline_proof_verified_closed_round_trips": 5,
            "baseline_proof_entry_snapshot_missing_round_trips": 1,
            "baseline_proof_exit_snapshot_missing_round_trips": 0,
            "baseline_closed_round_trips": 6,
            "baseline_quote_conflict_round_trips": 0,
            "baseline_quote_unverified_round_trips": 0,
        },
    )

    payload = write_profit_target_tracker(
        cfg,
        {
            "equity": 1012,
            "cash": 1012,
            "total_exposure": 0,
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )

    assert payload["decision_pnl_usdc"] == approx(10.0)
    assert payload["on_pace_by_decision_pnl"] is False
    assert payload["profit_target_proof_status"] == "unverified_paper_run_rate"
    assert "entry_snapshot_missing_round_trips_1" in payload["profit_target_proof_blockers"]
    assert payload["pnl_audit_state"] == "quote_consistent_but_entry_exit_proof_missing"


def test_dashboard_surfaces_stale_open_paper_positions(tmp_path):
    cfg = _config(tmp_path)
    stale = {
        "position_id": "position-stale",
        "market_id": "eth-stale-market",
        "token_id": "eth-stale-up-token",
        "market_slug": "ethereum-up-or-down-july-2-2026-12pm-et",
        "question": "Ethereum Up or Down - July 2, 2026 12PM ET",
        "outcome": "Up",
        "cost_basis_usdc": 4.0,
        "close_time": "2026-07-02T16:00:00Z",
        "hours_past_close": 12.5,
        "quote_state": "missing",
        "alert": "stale_open_position",
    }
    write_csv(
        cfg.output_root / "polymarket_portfolio" / "positions.csv",
        [
            {
                "position_id": "position-stale",
                "market_id": "eth-stale-market",
                "token_id": "eth-stale-up-token",
                "side": "BUY_YES",
                "status": "open",
                "quantity": 10,
                "average_entry_price": 0.4,
                "cost_basis_usdc": 4.0,
                "updated_at": "2026-07-02T10:00:00Z",
            }
        ],
    )
    write_json(
        cfg.output_root / "polymarket_portfolio" / "paper_trading_summary.json",
        {
            "status": "monitoring_exits_only_readiness_gate_blocked",
            "generated_at_utc": "2026-07-03T00:00:00Z",
            "cash": 1000,
            "equity": 1004,
            "total_exposure": 4,
            "stale_open_position_count": 1,
            "stale_open_positions": [stale],
        },
    )

    render_dashboard(cfg)

    data = read_json(cfg.output_root / "polymarket_dashboard" / "dashboard_data.json")
    html = (cfg.output_root / "polymarket_dashboard" / "index.html").read_text(encoding="utf-8")
    assert data["paper_broker_summary"]["stale_open_position_count"] == 1
    assert data["oversight_status"]["stale_open_position_count"] == 1
    assert any(alert["title"] == "Stale open paper positions" for alert in data["oversight_status"]["alerts"])
    assert "Stale open paper positions" in html
    assert "Do not trust raw equity until they settle or receive fresh quotes" in html


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
    write_csv(
        cfg.output_root / "polymarket_predictions" / "near_miss_learning_candidates.csv",
        [
            {
                "market_slug": "near-miss-market",
                "outcome": "No",
                "signal_cohort": "crypto",
                "alpha_raw_edge": "0.054",
                "edge_lower_bound": "0.009",
                "near_miss_priority_score": "0.052",
                "near_miss_learning_reason": "near_miss_eligible",
            }
        ],
    )
    write_csv(
        cfg.output_root / "polymarket_predictions" / "predictions.csv",
        [
            {
                "market_slug": "expired-one-cent-alpha-market",
                "outcome": "Yes",
                "signal_cohort": "sports_other",
                "alpha_trade_candidate": "true",
                "validation_layer_pass": "true",
                "microstructure_filter_pass": "true",
                "bookmaker_cross_check_pass": "true",
                "close_time": "2026-07-01T00:00:00Z",
                "edge_lower_bound": "0.50",
                "alpha_score": "5.00",
                "executable_price": "0.01",
                "best_ask": "0.01",
                "spread": "",
                "liquidity": "1000",
                "rejection_reason": "expired_out_of_band",
            },
            {
                "market_slug": "alpha-learning-market",
                "outcome": "Yes",
                "signal_cohort": "crypto",
                "alpha_trade_candidate": "true",
                "validation_layer_pass": "true",
                "microstructure_filter_pass": "true",
                "bookmaker_cross_check_pass": "true",
                "edge_lower_bound": "0.08",
                "alpha_score": "0.20",
                "executable_price": "0.50",
                "spread": "0.01",
                "liquidity": "1000",
                "rejection_reason": "cohort_not_promoted",
            }
        ],
    )
    write_json(
        cfg.governance_root / "shadow_signal_cohort_pnl.json",
        {
            "alpha_candidate_learning_candidates_seen": 2,
            "alpha_candidate_learning_opened_this_cycle": 1,
            "alpha_candidate_learning_open_positions": 1,
        },
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
    assert data["trade_diagnostics"]["near_miss_candidates_seen"] == 1
    assert data["trade_diagnostics"]["current_near_miss_candidates"][0]["market_slug"] == "near-miss-market"
    assert data["trade_diagnostics"]["alpha_candidate_learning_candidates_seen"] == 2
    assert data["trade_diagnostics"]["alpha_candidate_learning_openable_candidates_seen"] == 1
    assert data["trade_diagnostics"]["alpha_candidate_learning_opened_this_cycle"] == 1
    assert data["trade_diagnostics"]["alpha_candidate_learning_open_positions"] == 1
    assert data["trade_diagnostics"]["current_alpha_learning_candidates"][0]["market_slug"] == "alpha-learning-market"
    html = Path(result["dashboard_file"]).read_text(encoding="utf-8")
    assert "Alpha-learning candidates" in html


def test_dashboard_surfaces_closing_line_value_artifact(tmp_path):
    cfg = _config(tmp_path)
    write_json(
        cfg.governance_root / "closing_line_value.json",
        {
            "status": "ok",
            "generated_at_utc": "2026-07-02T12:00:00Z",
            "positions_scored": 4,
            "final_line_positions": 3,
            "mean_final_clv": 0.018,
            "beat_close_rate": 0.75,
            "positive_clv_cohorts": ["macro_rates"],
            "cohorts": [
                {
                    "signal_cohort": "macro_rates",
                    "positions": 4,
                    "final_positions": 3,
                    "mean_final_clv": 0.018,
                    "final_clv_ci_low": 0.004,
                    "final_clv_ci_high": 0.031,
                    "final_beat_close_rate": 0.667,
                    "clv_evidence": "positive_clv_evidence",
                }
            ],
            "governance_note": "Diagnostic only.",
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )

    result = render_dashboard(cfg)

    data = read_json(result["dashboard_data"])
    html = Path(result["dashboard_file"]).read_text(encoding="utf-8")
    assert data["closing_line_value"]["positions_scored"] == 4
    assert data["closing_line_value"]["positive_clv_cohorts"] == ["macro_rates"]
    assert data["closing_line_value"]["paper_trading_invoked"] is False
    assert data["closing_line_value"]["live_trading_invoked"] is False
    assert "Closing-line value (CLV)" in html
    assert "CLV by cohort" in html


def test_dashboard_surfaces_smart_flow_clv_artifact(tmp_path):
    cfg = _config(tmp_path)
    wallet = "0xalpha000000000000000000000000000000000001"
    write_json(
        cfg.governance_root / "smart_flow_clv.json",
        {
            "status": "ok",
            "generated_at_utc": "2026-07-03T12:00:00Z",
            "fills_seen": 3,
            "fills_scored": 2,
            "final_line_fills": 2,
            "minimum_final_samples_per_wallet": 2,
            "positive_wallets": [wallet],
            "positions_skipped": {"not_buy_or_missing_required_fields": 1, "no_usable_quote_after_fill": 0},
            "watchlist": [
                {
                    "wallet": wallet,
                    "fills": 2,
                    "final_fills": 2,
                    "mean_final_clv": 0.05,
                    "final_clv_ci_low": 0.02,
                    "final_clv_ci_high": 0.08,
                    "beat_close_rate": 1.0,
                    "clv_evidence": "positive_clv_evidence",
                    "recommended_action": "watch_shadow_only",
                }
            ],
            "wallets": [
                {
                    "wallet": wallet,
                    "fills": 2,
                    "final_fills": 2,
                    "mean_clv": 0.05,
                    "mean_final_clv": 0.05,
                    "clv_evidence": "positive_clv_evidence",
                    "final_samples_needed": 0,
                    "recommended_action": "watch_shadow_only",
                }
            ],
            "governance_note": "Diagnostic only.",
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )

    result = render_dashboard(cfg)

    data = read_json(result["dashboard_data"])
    html = Path(result["dashboard_file"]).read_text(encoding="utf-8")
    assert data["smart_flow_clv"]["positive_wallets"] == [wallet]
    assert data["smart_flow_clv"]["paper_trading_invoked"] is False
    assert data["smart_flow_clv"]["live_trading_invoked"] is False
    assert "Smart-flow CLV" in html
    assert "Smart-flow wallet watchlist" in html


def test_dashboard_handles_empty_closing_line_value_artifact(tmp_path):
    cfg = _config(tmp_path)

    result = render_dashboard(cfg)

    data = read_json(result["dashboard_data"])
    html = Path(result["dashboard_file"]).read_text(encoding="utf-8")
    assert data["closing_line_value"] == {}
    assert data["smart_flow_clv"] == {}
    assert data["edge_attribution"] == {}
    assert data["algo_sweep"] == {}
    assert data["risk_state"] == {}
    assert data["evidence_funnel"]["liquidity_targets_discovered"] == "-"
    assert data["evidence_funnel"]["paper_gate_status"] == "-"
    assert "Closing-line value (CLV)" in html
    assert "No CLV evidence yet" in html
    assert "Smart-flow CLV" in html
    assert "No smart-flow CLV evidence yet" in html
    assert "Edge attribution" in html
    assert "No edge attribution evidence yet" in html
    assert "Evidence funnel" in html
    assert "Road-to-paper view" in html
    assert "Portfolio risk" in html
    assert "No portfolio risk snapshot yet" in html
    assert "Algo sweep lab" in html
    assert "No sweep run yet" in html


def test_dashboard_surfaces_evidence_funnel(tmp_path):
    cfg = _config(tmp_path)
    write_json(cfg.governance_root / "liquidity_discovery_summary.json", {"target_assets": 12})
    write_csv(
        cfg.output_root / "polymarket_predictions" / "predictions.csv",
        [
            {
                "market_id": "m-shadow",
                "token_id": "t-shadow",
                "prediction_timestamp": "2026-07-03T10:00:00Z",
                "shadow_trade_candidate": "true",
                "market_slug": "fed-cut-shadow",
            },
            {
                "market_id": "m-other",
                "token_id": "t-other",
                "prediction_timestamp": "2026-07-03T10:01:00Z",
                "shadow_trade_candidate": "false",
                "market_slug": "not-shadow",
            },
        ],
    )
    write_csv(
        cfg.output_root / "polymarket_shadow" / "shadow_positions.csv",
        [
            {"shadow_position_id": "open-1", "status": "open", "token_id": "t1", "quantity": 1},
            {"shadow_position_id": "closed-1", "status": "closed", "token_id": "t2", "quantity": 1},
        ],
    )
    write_json(
        cfg.governance_root / "closing_line_value.json",
        {
            "status": "ok",
            "generated_at_utc": "2026-07-03T10:02:00Z",
            "positions_scored": 4,
            "final_line_positions": 3,
            "positive_clv_cohorts": ["macro_rates"],
        },
    )
    write_json(
        cfg.governance_root / "edge_attribution.json",
        {
            "status": "ok",
            "generated_at_utc": "2026-07-03T10:03:00Z",
            "attributed_positions": 2,
            "cohorts": [
                {"signal_cohort": "macro_rates", "attribution_class": "positive_edge_confirmed"},
                {"signal_cohort": "tennis_tennis_winner", "attribution_class": "cost_dominated"},
            ],
        },
    )
    write_json(
        cfg.governance_root / "family_calibration_scorecard.json",
        {
            "status": "ok",
            "model_beats_market_families": ["macro_rates", "tennis_tennis_winner"],
        },
    )
    write_json(
        cfg.governance_root / "collection_coverage.json",
        {
            "status": "ok",
            "positions_with_pre_close_quote": 1,
            "positions_missing_pre_close_quote": 4,
            "missing_pre_close_positions": [
                {
                    "shadow_position_id": "missing-1",
                    "family": "macro_rates",
                    "close_time": "2026-07-03T11:30:00Z",
                    "reason": "missing_pre_close_quote",
                }
            ],
        },
    )
    write_json(
        cfg.output_root / "polymarket_algo" / "algo_sweep_summary.json",
        {
            "status": "ok",
            "generated_at_utc": "2026-07-03T10:04:00Z",
            "decision": "sweep_candidate_validated_shadow_only",
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )
    write_json(
        cfg.governance_root / "promotion_gate.json",
        {
            "approved_for_paper_trading": False,
            "paper_blockers": ["needs forward paper evidence"],
        },
    )
    write_csv(
        cfg.governance_root / "evidence_history.csv",
        [
            {
                "recorded_at_utc": "2026-07-03T10:04:00Z",
                "source": "algo_sweep",
                "positions_scored_attributed": "9",
                "final_line_positions": "",
                "positive_cohorts": "",
                "decision_or_class_summary": "sweep_candidate_validated_shadow_only",
            }
        ],
    )

    result = render_dashboard(cfg)

    data = read_json(result["dashboard_data"])
    html = Path(result["dashboard_file"]).read_text(encoding="utf-8")
    funnel = data["evidence_funnel"]
    assert funnel["liquidity_targets_discovered"] == 12
    assert funnel["alpha_shadow_candidates"] == 1
    assert funnel["shadow_positions"] == "1 open / 1 closed"
    assert funnel["closed_with_clv_final_lines"] == 3
    assert funnel["attributed_positions"] == 2
    assert funnel["cohorts_by_attribution_class"] == {
        "cost_dominated": 1,
        "positive_edge_confirmed": 1,
    }
    assert funnel["positive_clv_cohorts"] == ["macro_rates"]
    assert funnel["model_beats_market_families"] == ["macro_rates", "tennis_tennis_winner"]
    assert funnel["collection_gap"] == "4 missing / 1 covered"
    assert funnel["sweep_decision"] == "sweep_candidate_validated_shadow_only"
    assert funnel["paper_gate_status"].startswith("blocked_for_paper")
    assert funnel["evidence_history_rows"] == 1
    assert funnel["missing_pre_close_positions"][0]["shadow_position_id"] == "missing-1"
    assert "Evidence funnel" in html
    assert "Missing pre-close quote positions" in html


def test_dashboard_surfaces_portfolio_risk_state(tmp_path):
    cfg = _config(tmp_path)
    write_json(
        cfg.output_root / "polymarket_portfolio" / "risk_state.json",
        {
            "portfolio_risk": {
                "generated_at_utc": "2026-07-03T09:00:00Z",
                "decision_use": "reporting_only_not_trade_authorisation",
                "open_positions": 3,
                "marked_positions": 3,
                "unmarked_positions": 0,
                "total_cost_usdc": 18.0,
                "var_95_usdc": 3.42,
                "cvar_95_usdc": 3.6,
                "worst_position_return_pct": -20.0,
                "exposure_by_correlation_key": [
                    {"key": "fed-july-2026", "cost_basis_usdc": 10.0},
                    {"key": "tennis-final-2026", "cost_basis_usdc": 8.0},
                ],
                "exposure_by_category": [
                    {"key": "macro_rates", "cost_basis_usdc": 10.0},
                    {"key": "tennis", "cost_basis_usdc": 8.0},
                ],
                "paper_trading_invoked": False,
                "live_trading_invoked": False,
            }
        },
    )

    result = render_dashboard(cfg)

    data = read_json(result["dashboard_data"])
    html = Path(result["dashboard_file"]).read_text(encoding="utf-8")
    risk = data["risk_state"]["portfolio_risk"]
    assert risk["open_positions"] == 3
    assert risk["var_95_usdc"] == 3.42
    assert risk["cvar_95_usdc"] == 3.6
    assert risk["exposure_by_correlation_key"][0]["key"] == "fed-july-2026"
    assert risk["paper_trading_invoked"] is False
    assert risk["live_trading_invoked"] is False
    assert "Portfolio risk" in html
    assert "Top correlated exposure" in html
    assert "Exposure by category" in html


def test_dashboard_surfaces_dutch_arb_watch(tmp_path):
    cfg = _config(tmp_path)
    write_json(
        cfg.output_root / "polymarket_arbitrage" / "dutch_arb_monitor_summary.json",
        {
            "status": "paper_analysis",
            "generated_at_utc": "2026-07-03T08:00:00Z",
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
            "dry_run": True,
            "scan_stats_latest_poll": {"discovered": 12, "priced_complete": 4},
            "events_priced_complete_latest_poll": 4,
            "complete_arbs_latest_poll": 1,
            "alerts_total": 3,
            "persistent_alert_count": 1,
            "best_annualised_return_on_capital": 0.21,
            "best_opportunity": {
                "event_id": "arb-1",
                "event": "Example complete basket",
                "outcomes": 3,
                "ask_sum": 0.94,
                "lock_per_set": 0.06,
                "annualised_return_on_capital": 0.21,
                "capital_usdc": 94,
                "profit_usdc": 6,
                "days_to_resolution": 30,
            },
            "top_opportunities": [
                {
                    "event_id": "arb-1",
                    "event": "Example complete basket",
                    "outcomes": 3,
                    "ask_sum": 0.94,
                    "lock_per_set": 0.06,
                    "annualised_return_on_capital": 0.21,
                    "capital_usdc": 94,
                    "profit_usdc": 6,
                    "days_to_resolution": 30,
                }
            ],
            "persistent_alerts": [
                {
                    "event_id": "arb-1",
                    "event": "Example complete basket",
                    "consecutive_scans_above_alert": 3,
                    "alert_annualised": 0.10,
                    "title": "Dutch-book arb basket persisted above alert threshold",
                }
            ],
        },
    )

    result = render_dashboard(cfg)

    data = read_json(result["dashboard_data"])
    html = Path(result["dashboard_file"]).read_text(encoding="utf-8")
    assert data["dutch_arb"]["complete_arbs_latest_poll"] == 1
    assert data["dutch_arb"]["paper_trading_invoked"] is False
    assert data["dutch_arb"]["live_trading_invoked"] is False
    arb_alerts = [alert for alert in data["oversight_status"]["alerts"] if alert["title"] == "Dutch-book arb basket persisted"]
    assert arb_alerts
    assert arb_alerts[0]["severity"] == "info"
    assert "Dutch-book arb watch" in html
    assert "Top Dutch-book baskets" in html
    assert "Observed ask baskets" in html


def test_dashboard_surfaces_edge_lane_runtime_fallbacks(tmp_path):
    cfg = _config(tmp_path)
    write_json(
        cfg.governance_root / "live_paper_loop_heartbeat.json",
        {
            "status": "ran",
            "generated_at_utc": "2026-07-03T08:05:00Z",
            "dutch_arb": {
                "status": "skipped_interval",
                "last_scan_at_utc": "2026-07-03T08:00:00Z",
                "next_due_minutes": 7.5,
                "paper_trading_invoked": False,
                "live_trading_invoked": False,
            },
        },
    )
    write_json(
        cfg.governance_root / "forward_paper_cycle.json",
        {
            "status": "ran",
            "generated_at_utc": "2026-07-03T08:05:00Z",
            "longshot_bias": {
                "status": "ok",
                "generated_at_utc": "2026-07-03T08:05:00Z",
                "source_rows": 3,
                "markets_scanned": 2,
                "candidates": 1,
                "candidate_cohorts": {"structural|longshot_no|macro_rates": 1},
                "candidate_preview": [
                    {
                        "question": "Will an over-hyped tail event happen?",
                        "family": "macro_rates",
                        "outcome": "No",
                        "ask": 0.09,
                        "bid": 0.08,
                        "spread": 0.01,
                        "liquidity": 1200,
                        "signal_cohort": "structural|longshot_no|macro_rates",
                    }
                ],
                "emit_shadow_positions": False,
                "shadow_update": {"opened_this_cycle": 0},
                "decision_use": "shadow_clv_learning_target_not_paper_trade_authorisation",
            },
        },
    )

    result = render_dashboard(cfg)

    data = read_json(result["dashboard_data"])
    html = Path(result["dashboard_file"]).read_text(encoding="utf-8")
    assert data["dutch_arb"]["source"] == "live_loop_heartbeat"
    assert data["dutch_arb"]["status"] == "skipped_interval"
    assert data["longshot_bias"]["source"] == "forward_paper_cycle"
    assert data["longshot_bias"]["paper_trading_invoked"] is False
    assert data["longshot_bias"]["live_trading_invoked"] is False
    assert data["longshot_bias"]["candidates"] == 1
    assert "Longshot-bias shadow lane" in html
    assert "Longshot NO candidates" in html


def test_dashboard_surfaces_edge_attribution_artifact(tmp_path):
    cfg = _config(tmp_path)
    write_json(
        cfg.governance_root / "edge_attribution.json",
        {
            "status": "ok",
            "generated_at_utc": "2026-07-02T12:10:00Z",
            "closed_positions_seen": 3,
            "attributed_positions": 2,
            "skipped_unattributable_closed": 1,
            "minimum_positions_per_cohort": 1,
            "identity_note": "per share identity",
            "cohorts": [
                {
                    "signal_cohort": "macro_rates",
                    "attributed_positions": 2,
                    "total_pnl_usdc": -2.2,
                    "execution_cost_usdc": 0.4,
                    "line_movement_usdc": -1.0,
                    "settlement_surprise_usdc": -0.8,
                    "attribution_class": "model_direction_not_confirmed",
                    "recommended_action": "re-model",
                }
            ],
            "governance_note": "Diagnostic only.",
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )
    write_json(
        cfg.output_root / "polymarket_algo" / "algo_sweep_summary.json",
        {
            "status": "ok",
            "decision": "sweep_candidate_failed_out_of_sample_validation",
            "strategy": "tight_spread_join_bid_shadow",
            "events_total": 90,
            "train_events": 60,
            "validation_events": 30,
            "combos_tested": 2,
            "train_candidates": 1,
            "selected": {
                "tight_spread_maximum": 0.02,
                "minimum_book_imbalance": 0.55,
                "train_pnl_usdc": 0.2,
                "validation_pnl_usdc": -0.1,
            },
            "combos": [
                {
                    "tight_spread_maximum": 0.02,
                    "minimum_book_imbalance": 0.55,
                    "train_fills": 2,
                    "train_pnl_usdc": 0.2,
                    "validation_fills": 1,
                    "validation_pnl_usdc": -0.1,
                    "train_candidate": True,
                    "selected": True,
                    "status": "ok",
                }
            ],
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )

    result = render_dashboard(cfg)

    data = read_json(result["dashboard_data"])
    html = Path(result["dashboard_file"]).read_text(encoding="utf-8")
    assert data["edge_attribution"]["attributed_positions"] == 2
    assert data["edge_attribution"]["paper_trading_invoked"] is False
    assert data["edge_attribution"]["live_trading_invoked"] is False
    assert data["algo_sweep"]["decision"] == "sweep_candidate_failed_out_of_sample_validation"
    assert data["algo_sweep"]["paper_trading_invoked"] is False
    assert data["algo_sweep"]["live_trading_invoked"] is False
    assert "Edge attribution" in html
    assert "Attribution by cohort" in html
    assert "Algo sweep lab" in html
    assert "Sweep combinations" in html


def test_dashboard_surfaces_algo_replay_evidence(tmp_path):
    cfg = _config(tmp_path)
    algo_root = cfg.output_root / "polymarket_algo"
    write_json(
        algo_root / "replay_null_summary.json",
        {
            "status": "ok",
            "strategy": "null",
            "events_processed": 12,
            "intents_emitted": 0,
            "fills": 0,
            "total_cost_usdc": 0.0,
            "unrealised_mark_to_bid_pnl_usdc": 0.0,
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )
    write_json(
        algo_root / "replay_tight_spread_join_bid_shadow_summary.json",
        {
            "status": "ok",
            "strategy": "tight_spread_join_bid_shadow",
            "events_processed": 50,
            "intents_emitted": 4,
            "fills": 2,
            "resting_orders_at_end": 1,
            "total_cost_usdc": 2.0,
            "unrealised_mark_to_bid_pnl_usdc": -0.42,
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )

    result = render_dashboard(cfg)

    data = read_json(result["dashboard_data"])
    html = Path(result["dashboard_file"]).read_text(encoding="utf-8")
    assert "Algo replay lab" in html
    assert data["algo_replay"]["strategy_count"] == 2
    assert data["algo_replay"]["best_strategy"]["strategy"] == "null"
    assert data["algo_replay"]["summaries"][1]["strategy"] == "tight_spread_join_bid_shadow"
    assert data["algo_replay"]["paper_trading_invoked"] is False
    assert data["algo_replay"]["live_trading_invoked"] is False


def test_dashboard_gates_extrapolated_edge_route_evidence(tmp_path):
    cfg = _config(tmp_path)
    write_json(
        cfg.governance_root / "paper_profit_goal_plan.json",
        {
            "status": "not_on_pace",
            "target_monthly_profit_usdc": 100,
            "decision_pnl_usdc": 0.22,
            "decision_monthly_run_rate_usdc": 9.4,
            "profit_target_proof_status": "unverified_paper_run_rate",
            "profit_target_proof_blockers": ["needs_5_audited_round_trips_has_2"],
            "audited_round_trips_since_baseline": 2,
            "minimum_audited_round_trips_for_on_pace": 5,
            "verified_evidence_ready": False,
            "quote_conflict_round_trips": 0,
            "quote_unverified_round_trips": 0,
            "all_time_quote_conflict_round_trips": 7,
            "all_time_quote_unverified_round_trips": 2,
            "price_action_goal_state": {
                "state": "forward_paper_on_monthly_target",
                "best_repricing_monthly_run_rate_usdc": 2344.677548049622,
                "best_forward_paper_monthly_run_rate_usdc": 1973.9336651190747,
                "top_cohorts": [
                    {
                        "source": "paper_broker_forward",
                        "cohort": "crypto_eth_updown_event",
                        "forward_edge_blocker": "",
                        "forward_paper_pnl_usdc": 0.1865793780687397,
                        "forward_paper_roi": 0.046644844517184925,
                        "monthly_run_rate_usdc": 1973.9336651190747,
                        "paper_audited_round_trips": 2,
                        "paper_buy_fills": 2,
                        "paper_sell_fills": 2,
                    },
                    {
                        "source": "shadow_forward",
                        "cohort": "exploratory_crypto_updown_live_model|crypto_sol_updown_15m|outcome=up",
                        "forward_edge_blocker": "",
                        "forward_shadow_pnl_usdc": 9.67895748754814,
                        "forward_shadow_roi": 0.9678957487548139,
                        "monthly_run_rate_usdc": 2344.677548049622,
                        "shadow_fills": 1,
                        "shadow_sell_fills": 1,
                    },
                ],
            },
        },
    )
    write_json(
        cfg.governance_root / "paper_profit_target_tracker.json",
        {
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "not_on_pace",
            "actual_pnl_since_baseline_usdc": 0.22,
            "monthly_run_rate_usdc": 9.4,
            "target_monthly_profit_usdc": 100,
            "current": {"equity_usdc": 1000.22, "cash_usdc": 1000.22},
        },
    )

    result = render_dashboard(cfg)

    data = read_json(result["dashboard_data"])
    html = Path(result["dashboard_file"]).read_text(encoding="utf-8")
    edge_card = next(card for card in data["decision_useful_summary"]["decision_cards"] if card["label"] == "Edge route")
    edge_lane = next(row for row in data["decision_useful_summary"]["evidence_lanes"] if row["lane"] == "Best shadow repricing route")
    assert edge_card["severity"] == "warn"
    assert "best paper cohort: +$0.19 on 2 round trips over 4m (n too small to annualise)" in edge_card["value"]
    assert "best shadow cohort: +$9.68 on 1 round trip over 3.0h (n too small to annualise)" in edge_card["value"]
    assert "$1973.93" not in edge_card["value"]
    assert "$2344.68" not in edge_card["value"]
    assert edge_lane["key_metric"] == edge_card["value"]
    target_card = next(card for card in data["decision_useful_summary"]["decision_cards"] if card["label"] == "$100/month state")
    target_lane = next(row for row in data["decision_useful_summary"]["evidence_lanes"] if row["lane"] == "Forward paper P&L")
    assert target_card["severity"] == "warn"
    assert "proof unverified_paper_run_rate" in target_card["value"]
    assert "audited round trips 2/5" in target_card["value"]
    assert "audited round trips 2/5" in target_lane["blocker_or_next"]
    assert "needs_5_audited_round_trips_has_2" in data["decision_useful_summary"]["profit_target_proof_blockers"]
    assert "n too small to annualise" in html
    assert "Proof status" in html
    assert "Baseline quote conflicts" in html
    assert "All-time quote conflicts" in html
    assert "Entry miss" in html
    assert "Exit miss" in html
    assert data["paper_profit_goal_plan"]["quote_conflict_round_trips"] == 0
    assert data["paper_profit_goal_plan"]["all_time_quote_conflict_round_trips"] == 7
    assert "provisional lines await market close" in html
    assert "no strategy beat doing nothing" in html
    assert "raw; audited" in html


def test_dashboard_serves_capped_compact_cockpit_payload(tmp_path):
    cfg = _config(tmp_path)
    focus_path = cfg.governance_root / "research_focus.json"
    large_rows = [{"row_id": i, "diagnostic": "kept in source artifact"} for i in range(75)]
    write_json(
        focus_path,
        {
            "status": "ok",
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "large_diagnostic_rows": large_rows,
        },
    )

    result = render_dashboard(cfg)

    data = read_json(result["dashboard_data"])
    dashboard_text = Path(result["dashboard_data"]).read_text(encoding="utf-8")
    source_focus = read_json(focus_path)
    assert len(source_focus["large_diagnostic_rows"]) == 75
    assert len(data["research_focus"]["large_diagnostic_rows"]) == 50
    assert data["dashboard_payload_limits"]["source_artifacts_complete"] is True
    assert data["dashboard_payload_limits"]["max_list_items"] == 50
    assert {
        "path": "research_focus.large_diagnostic_rows",
        "original_items": 75,
        "dashboard_items": 50,
    } in data["dashboard_payload_limits"]["truncated_lists"]
    assert "\n" not in dashboard_text.strip()


def test_dashboard_surfaces_side_missing_analogue_context_targets(tmp_path):
    cfg = _config(tmp_path)
    write_json(
        cfg.governance_root / "research_focus.json",
        {
            "status": "ok",
            "collection_queries": ["fed"],
            "price_action_side_missing_analogues": {
                "state": "needs_trade_flow_side_context",
                "collection_queries": ["fed"],
                "trade_authorisation": "no_trade_side_agnostic_history_is_shadow_only",
                "targets": [
                    {
                        "decision_use": "collect_trade_flow_side_context_only_not_trade_authorisation",
                        "family": "macro_rates",
                        "market_slug": "will-the-fed-cut-rates-after-the-july-2026-meeting",
                        "latest_ask": 0.5,
                        "side_agnostic_validation_rows": 12,
                        "side_agnostic_positive_rows": 5,
                        "side_agnostic_validation_roi": 0.041,
                        "recommended_collection_query": "fed",
                        "trade_authorisation": "no_trade_side_agnostic_history_is_shadow_only",
                    }
                ],
                "paper_only": True,
            },
        },
    )

    result = render_dashboard(cfg)

    data = read_json(result["dashboard_data"])
    html = Path(result["dashboard_file"]).read_text(encoding="utf-8")
    side_missing = data["research_focus"]["price_action_side_missing_analogues"]
    assert side_missing["state"] == "needs_trade_flow_side_context"
    assert side_missing["targets"][0]["recommended_collection_query"] == "fed"
    assert "Side-missing positive analogue context targets" in html
    assert "Side-free ROI" in html


def test_dashboard_emits_decision_useful_summary_for_missing_fresh_candidate(tmp_path):
    cfg = _config(tmp_path)
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_model_summary.json",
        {
            "status": "trained",
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "decision": "collect_more_bid_ask_price_action_model_evidence",
            "promotion_ready": False,
            "train_positive_targets": 4,
            "validation_positive_targets": 2,
        },
    )
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_paper_signal_summary.json",
        {
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "decision": "trusted_shadow_edge_waiting_for_fresh_executable_candidate",
            "signals": 0,
            "rejections": 16,
            "paper_confirmation_candidates": 2,
            "paper_confirmation_signals": 0,
        },
    )
    write_json(
        cfg.governance_root / "trade_signal_audit.json",
        {
            "verdict": "trusted_edge_missing_fresh_candidate",
            "next_action": "Collect fresh websocket/price-action candidates for: btc updown, solana updown.",
            "missing_confirmation_target_count": 2,
            "missing_confirmation_queries": ["btc updown", "solana updown"],
            "recent_exit_count": 4,
            "recent_loss_exit_count": 3,
            "recent_realised_pnl_usdc": -0.13,
        },
    )
    write_json(
        cfg.governance_root / "paper_profit_goal_plan.json",
        {
            "status": "not_on_pace",
            "target_monthly_profit_usdc": 100,
            "required_daily_from_here_usdc": 4.05,
            "main_gap": "trusted shadow repricing evidence exists; it needs governed paper-confirmation probes",
            "recommended_action": "Generate governed paper-confirmation probes for trusted positive shadow repricing cohorts.",
            "price_action_goal_state": {
                "state": "trusted_shadow_needs_paper_confirmation",
                "best_repricing_monthly_run_rate_usdc": 23.45,
                "best_forward_paper_monthly_run_rate_usdc": 0,
                "collection_queries": ["btc updown", "solana updown"],
            },
        },
    )
    write_json(
        cfg.governance_root / "paper_profit_target_tracker.json",
        {
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "not_on_pace",
            "actual_pnl_since_baseline_usdc": -0.13,
            "monthly_run_rate_usdc": -0.71,
            "target_monthly_profit_usdc": 100,
            "current": {"equity_usdc": 999.87, "cash_usdc": 999.87},
        },
    )
    write_json(
        cfg.path.parent / "work" / "shadow_research_cycle_latest_status.json",
        {
            "status": "ok",
            "started_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ended_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )

    result = render_dashboard(cfg)

    html = Path(result["dashboard_file"]).read_text(encoding="utf-8")
    data = read_json(result["dashboard_data"])
    summary = data["decision_useful_summary"]
    assert "Action board" in html
    assert "Operator questions" in html
    assert "Decision evidence drill-down" in html
    assert summary["trade_decision"] == "COLLECT FRESH CANDIDATE"
    assert "fresh open market row" in summary["primary_blocker"]
    assert summary["collect_now"] == ["btc updown", "solana updown"]
    assert summary["decision_cards"][0]["label"] == "Can paper trade now?"
    assert summary["decision_cards"][0]["value"].startswith("No - COLLECT FRESH CANDIDATE")
    assert any(row["question"] == "What unlocks the next trade?" for row in summary["decision_questions"])
    assert summary["priority_actions"][0]["success_metric"]
    assert any(row["panel"] == "Legacy local live-loop heartbeat" for row in summary["audit_only_panels"])
    assert any(row["lane"] == "Paper trade gate" for row in summary["evidence_lanes"])


def test_dashboard_collect_now_prefers_research_focus_queue_over_raw_missing_queries(tmp_path):
    cfg = _config(tmp_path)
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_model_summary.json",
        {
            "status": "trained",
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "decision": "collect_more_bid_ask_price_action_model_evidence",
            "promotion_ready": False,
            "train_positive_targets": 4,
            "validation_positive_targets": 2,
        },
    )
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_paper_signal_summary.json",
        {
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "decision": "trusted_shadow_edge_waiting_for_fresh_executable_candidate",
            "signals": 0,
            "rejections": 16,
            "paper_confirmation_candidates": 2,
            "paper_confirmation_signals": 0,
        },
    )
    write_json(
        cfg.governance_root / "trade_signal_audit.json",
        {
            "verdict": "trusted_edge_missing_fresh_candidate",
            "next_action": "Collect broad missing confirmation queries.",
            "missing_confirmation_target_count": 5,
            "missing_confirmation_queries": [
                "world cup",
                "btc updown",
                "ethereum",
                "eth updown",
                "xrp updown",
            ],
        },
    )
    write_json(
        cfg.governance_root / "research_focus.json",
        {
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "collection_queries": ["solana updown", "esports", "economy"],
            "raw_collection_queries": ["solana updown", "btc updown", "eth updown"],
            "collection_query_guard": {
                "accepted_queries": ["solana updown", "esports", "economy"],
                "rejected_queries": [
                    {"query": "btc updown", "reason": "max_updown_queries"},
                    {"query": "eth updown", "reason": "max_updown_queries"},
                ],
            },
        },
    )
    write_json(
        cfg.path.parent / "work" / "shadow_research_cycle_latest_status.json",
        {
            "status": "ok",
            "started_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ended_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )

    result = render_dashboard(cfg)

    data = read_json(result["dashboard_data"])
    summary = data["decision_useful_summary"]
    assert summary["trade_decision"] == "COLLECT FRESH CANDIDATE"
    assert summary["collect_now"] == ["solana updown", "esports", "economy"]
    assert summary["decision_questions"][2]["answer"] == "solana updown, esports, economy"
    assert summary["state_badges"][-1]["label"] == "collect: solana updown, esports, economy"


def test_dashboard_reports_blocked_candidates_when_fresh_targets_exist(tmp_path):
    cfg = _config(tmp_path)
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_model_summary.json",
        {
            "status": "trained",
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "decision": "collect_more_bid_ask_price_action_model_evidence",
            "promotion_ready": False,
            "train_positive_targets": 4,
            "validation_positive_targets": 2,
        },
    )
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_paper_signal_summary.json",
        {
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "decision": "trusted_shadow_edge_waiting_for_fresh_executable_candidate",
            "signals": 0,
            "rejections": 18,
            "paper_confirmation_candidates": 2,
            "paper_confirmation_signals": 0,
        },
    )
    write_json(
        cfg.governance_root / "trade_signal_audit.json",
        {
            "verdict": "trusted_edge_current_candidates_blocked_by_gate",
            "next_action": "Do not enter; fresh executable candidates exist for trusted confirmation cohorts, but none has passed the governed signal thesis.",
            "missing_confirmation_target_count": 0,
            "missing_confirmation_queries": [],
            "paper_confirmation_targets": [
                {
                    "cohort": "crypto_eth_updown_event",
                    "recommended_collection_query": "eth updown",
                    "current_candidate_rows": 12,
                    "current_executable_rows": 3,
                    "missing_fresh_candidate": False,
                    "trusted_for_goal": True,
                }
            ],
        },
    )
    write_json(
        cfg.governance_root / "research_focus.json",
        {
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "collection_queries": ["eth updown", "world cup", "solana updown"],
        },
    )
    write_json(
        cfg.path.parent / "work" / "shadow_research_cycle_latest_status.json",
        {
            "status": "ok",
            "started_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ended_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )

    result = render_dashboard(cfg)

    data = read_json(result["dashboard_data"])
    summary = data["decision_useful_summary"]
    assert summary["trade_decision"] == "WAIT: CANDIDATES BLOCKED"
    assert "Fresh executable candidates exist" in summary["primary_blocker"]
    assert "fresh open market row" not in summary["primary_blocker"]
    assert summary["decision_cards"][0]["value"].startswith("No - WAIT: CANDIDATES BLOCKED")
    assert summary["evidence_lanes"][0]["state"] == "WAIT: CANDIDATES BLOCKED"


def test_dashboard_prioritises_zero_selection_model_blocker_over_generic_candidate_block(tmp_path):
    cfg = _config(tmp_path)
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_model_summary.json",
        {
            "status": "trained",
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "decision": "collect_more_bid_ask_price_action_model_evidence",
            "promotion_ready": False,
            "train_positive_targets": 228,
            "validation_positive_targets": 108,
            "validation_selected": {
                "selected_trades": 0,
                "selected_roi": 0.0,
                "selected_profit_factor": 0.0,
            },
            "train_threshold_selection": {
                "chosen_train_metrics": {
                    "train_threshold_pass": False,
                    "threshold_source": "configured_probability_grid",
                }
            },
            "validation_blockers": [
                "no probability threshold cleared the training split trade gates",
                "selected validation trades 0 < 5",
            ],
            "validation_rank_diagnostics": {
                "best_positive_rank": 220,
                "next_action": "Treat selected validation losers as hard negatives; add anti-chase/cohort features before promotion.",
            },
        },
    )
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_paper_signal_summary.json",
        {
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "decision": "trusted_shadow_edge_waiting_for_fresh_executable_candidate",
            "signals": 0,
            "rejections": 473,
            "paper_confirmation_candidates": 2,
            "paper_confirmation_signals": 0,
        },
    )
    write_json(
        cfg.governance_root / "trade_signal_audit.json",
        {
            "verdict": "trusted_edge_current_candidates_blocked_by_gate",
            "next_action": "Do not enter; fresh executable candidates exist but none has passed the governed signal thesis.",
            "missing_confirmation_target_count": 0,
            "missing_confirmation_queries": [],
            "paper_confirmation_targets": [
                {
                    "cohort": "crypto_eth_updown_event",
                    "recommended_collection_query": "eth updown",
                    "current_candidate_rows": 12,
                    "current_executable_rows": 3,
                    "missing_fresh_candidate": False,
                    "trusted_for_goal": True,
                }
            ],
        },
    )
    write_json(
        cfg.governance_root / "research_focus.json",
        {
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "collection_queries": ["solana updown", "world cup", "fed"],
        },
    )
    write_json(
        cfg.path.parent / "work" / "shadow_research_cycle_latest_status.json",
        {
            "status": "ok",
            "started_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ended_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )

    result = render_dashboard(cfg)

    data = read_json(result["dashboard_data"])
    summary = data["decision_useful_summary"]
    model_lane = next(row for row in summary["evidence_lanes"] if row["lane"] == "Price-action ML")
    assert summary["trade_decision"] == "LEARN: MODEL SELECTS NO TRADES"
    assert summary["model_threshold_blocked"] is True
    assert summary["model_selected_validation_trades"] == 0
    assert summary["model_train_threshold_pass"] is False
    assert summary["model_best_positive_validation_rank"] == 220
    assert "selected validation trades=0" in summary["primary_blocker"]
    assert "train positives=228" in summary["primary_blocker"]
    assert summary["decision_cards"][0]["value"].startswith("No - LEARN: MODEL SELECTS NO TRADES")
    assert "selected 0 validation trades" in model_lane["key_metric"]
    assert "anti-chase" in model_lane["blocker_or_next"]


def test_dashboard_decision_summary_prioritises_failed_cycle_over_stale_model(tmp_path):
    cfg = _config(tmp_path)
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_model_summary.json",
        {
            "status": "trained",
            "generated_at_utc": "2026-06-25T00:00:00Z",
            "decision": "collect_more_bid_ask_price_action_model_evidence",
            "promotion_ready": False,
        },
    )
    write_json(
        cfg.path.parent / "work" / "shadow_research_cycle_latest_status.json",
        {
            "status": "error",
            "started_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ended_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "phase": "generate-signals-dry",
            "error": "generate-signals-dry timed out after 180 seconds",
            "error_type": "step_timeout",
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )

    result = render_dashboard(cfg)

    data = read_json(result["dashboard_data"])
    summary = data["decision_useful_summary"]
    assert summary["trade_decision"] == "WAIT: CYCLE FAILED"
    assert summary["headline"] == "The latest research cycle failed before producing trustworthy signals"
    assert summary["primary_blocker"] == "generate-signals-dry timed out after 180 seconds"
    assert summary["shadow_research_status"] == "error"


def test_dashboard_decision_summary_waits_for_running_cycle_before_manual_refresh(tmp_path):
    cfg = _config(tmp_path)
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_model_summary.json",
        {
            "status": "trained",
            "generated_at_utc": "2026-06-25T00:00:00Z",
            "decision": "collect_more_bid_ask_price_action_model_evidence",
            "promotion_ready": False,
        },
    )
    write_json(
        cfg.path.parent / "work" / "shadow_research_cycle_latest_status.json",
        {
            "status": "running",
            "started_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )

    result = render_dashboard(cfg)

    data = read_json(result["dashboard_data"])
    summary = data["decision_useful_summary"]
    assert summary["trade_decision"] == "WAIT: CYCLE RUNNING"
    assert summary["headline"] == "A fresh research cycle is already updating the model"
    assert "do not start a competing governance refresh" in summary["next_action"]
    assert summary["decision_cards"][0]["value"].startswith("No - WAIT: CYCLE RUNNING")


def test_dashboard_prefers_fresh_broker_and_profit_tracker_over_stale_forward_cycle(tmp_path):
    cfg = _config(tmp_path)
    write_json(
        cfg.governance_root / "forward_paper_cycle.json",
        {
            "status": "ran",
            "generated_at_utc": "2026-06-25T00:00:00Z",
            "broker": {
                "generated_at_utc": "2026-06-25T00:00:00Z",
                "equity": 900,
                "cash": 900,
                "total_exposure": 0,
            },
            "actual_profit_target": {
                "generated_at_utc": "2026-06-25T00:00:00Z",
                "actual_pnl_since_baseline_usdc": -100,
                "current": {"timestamp_utc": "2026-06-25T00:00:00Z", "equity_usdc": 900},
            },
        },
    )
    write_json(
        cfg.output_root / "polymarket_portfolio" / "paper_trading_summary.json",
        {
            "generated_at_utc": "2026-06-25T00:10:00Z",
            "equity": 1000,
            "cash": 1000,
            "total_exposure": 0,
        },
    )
    write_json(
        cfg.governance_root / "paper_profit_target_tracker.json",
        {
            "status": "collecting_forward_evidence",
            "generated_at_utc": "2026-06-25T00:10:01Z",
            "actual_pnl_since_baseline_usdc": 0,
            "current": {
                "timestamp_utc": "2026-06-25T00:10:00Z",
                "equity_usdc": 1000,
                "cash_usdc": 1000,
                "total_exposure_usdc": 0,
            },
        },
    )
    write_json(
        cfg.governance_root / "local_live_loop_heartbeat.json",
        {"status": "ok", "generated_at_utc": "2020-01-01T00:00:00Z", "websocket_seconds": 5},
    )

    result = render_dashboard(cfg)
    data = read_json(result["dashboard_data"])

    assert data["paper_broker_summary"]["equity"] == 1000
    assert data["paper_trading_account"]["source"] == "paper_trading_summary"
    assert data["paper_trading_account"]["broker"]["equity"] == 1000
    assert data["paper_trading_account"]["forward_cycle_broker_mismatch"] is True
    assert "equity" in data["paper_trading_account"]["forward_cycle_broker_mismatch_fields"]
    assert data["actual_profit_target"]["actual_pnl_since_baseline_usdc"] == 0
    assert data["evidence_freshness"]["broker_source"] == "paper_trading_summary"
    assert data["evidence_freshness"]["target_source"] == "paper_profit_target_tracker"
    assert data["evidence_freshness"]["scoreboard_status"] == "aligned"
    assert data["evidence_freshness"]["live_loop_status"] == "stale"


def test_dashboard_prefers_fresh_paper_trade_refresh_over_stale_summaries(tmp_path):
    cfg = _config(tmp_path)
    write_json(
        cfg.output_root / "polymarket_portfolio" / "paper_trading_summary.json",
        {
            "generated_at_utc": "2026-06-25T00:10:00Z",
            "equity": 990,
            "cash": 990,
            "total_exposure": 0,
        },
    )
    write_json(
        cfg.governance_root / "paper_profit_target_tracker.json",
        {
            "status": "collecting_forward_evidence",
            "generated_at_utc": "2026-06-25T00:10:01Z",
            "actual_pnl_since_baseline_usdc": -10,
            "current": {"timestamp_utc": "2026-06-25T00:10:00Z", "equity_usdc": 990},
        },
    )
    write_json(
        cfg.governance_root / "paper_trade_refresh.json",
        {
            "status": "ran",
            "generated_at_utc": "2026-06-25T00:12:00Z",
            "broker": {
                "status": "ran",
                "generated_at_utc": "2026-06-25T00:12:00Z",
                "equity": 1005,
                "cash": 1003,
                "total_exposure": 2,
                "buy_orders_filled": 1,
                "exit_orders_filled": 0,
            },
            "actual_profit_target": {
                "status": "collecting_forward_evidence",
                "generated_at_utc": "2026-06-25T00:12:01Z",
                "actual_pnl_since_baseline_usdc": 5,
                "current": {"timestamp_utc": "2026-06-25T00:12:00Z", "equity_usdc": 1005},
            },
        },
    )

    result = render_dashboard(cfg)
    data = read_json(result["dashboard_data"])

    assert data["paper_broker_summary"]["equity"] == 1005
    assert data["paper_broker_summary"]["buy_orders_filled"] == 1
    assert data["paper_trading_account"]["source"] == "paper_trade_refresh"
    assert data["paper_trading_account"]["broker"]["equity"] == 1005
    assert data["paper_trading_account"]["forward_cycle_broker_mismatch"] is False
    assert data["actual_profit_target"]["actual_pnl_since_baseline_usdc"] == 5
    assert data["evidence_freshness"]["broker_source"] == "paper_trade_refresh"
    assert data["evidence_freshness"]["target_source"] == "paper_trade_refresh"
    assert data["evidence_freshness"]["scoreboard_status"] == "aligned"


def test_dashboard_flags_price_action_signals_waiting_for_broker_refresh(tmp_path):
    cfg = _config(tmp_path)
    write_json(
        cfg.governance_root / "paper_trade_refresh.json",
        {
            "status": "ran",
            "generated_at_utc": "2026-06-30T18:59:25Z",
            "broker": {
                "status": "ran",
                "generated_at_utc": "2026-06-30T18:59:25Z",
                "equity": 1000,
                "cash": 1000,
                "total_exposure": 0,
                "orders_filled": 0,
            },
        },
    )
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_paper_signal_summary.json",
        {
            "status": "computed",
            "generated_at_utc": "2026-06-30T21:20:14Z",
            "signals": 3,
            "paper_confirmation_signals": 3,
            "paper_confirmation_candidates": 3,
        },
    )
    write_csv(
        cfg.output_root / "polymarket_price_action" / "price_action_paper_signals.csv",
        [
            {
                "price_action_entry_source": "paper_confirmation_candidate",
                "signal_cohort": "macro_economy",
                "market_slug": "macro-test",
                "token_id": "macro-token",
            }
        ],
    )

    result = render_dashboard(cfg)
    data = read_json(result["dashboard_data"])
    summary = data["decision_useful_summary"]

    paper_status = data["price_action_paper_signals"]
    assert paper_status["signals"] == 1
    assert paper_status["summary_signals"] == 3
    assert paper_status["summary_signal_mismatch"] is True
    assert paper_status["broker_refresh_needed"] is True
    assert paper_status["pending_broker_signals"] == 1
    assert paper_status["pending_broker_confirmation_signals"] == 1
    assert summary["trade_decision"] == "RUN PAPER CONFIRMATION BROKER"
    assert "not live approval or sizing promotion" in summary["primary_blocker"]
    assert summary["evidence_lanes"][0]["state"] == "RUN PAPER CONFIRMATION BROKER"
    assert data["evidence_freshness"]["broker_refresh_needed"] is True
    assert data["evidence_freshness"]["pending_broker_signals"] == 1


def test_dashboard_does_not_request_broker_refresh_when_signals_are_already_open(tmp_path):
    cfg = _config(tmp_path)
    write_json(
        cfg.governance_root / "paper_trade_refresh.json",
        {
            "status": "ran",
            "generated_at_utc": "2026-06-30T18:59:25Z",
            "broker": {
                "status": "ran",
                "generated_at_utc": "2026-06-30T18:59:25Z",
                "equity": 1000,
                "cash": 998,
                "total_exposure": 2,
            },
        },
    )
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_paper_signal_summary.json",
        {
            "status": "computed",
            "generated_at_utc": "2026-06-30T21:20:14Z",
            "signals": 1,
            "paper_confirmation_signals": 1,
            "paper_confirmation_candidates": 1,
        },
    )
    write_csv(
        cfg.output_root / "polymarket_price_action" / "price_action_paper_signals.csv",
        [
            {
                "market_id": "btc-confirmation-market",
                "market_slug": "btc-confirmation-market",
                "token_id": "btc-confirmation-token",
                "side": "BUY_YES",
                "price_action_entry_source": "paper_confirmation_candidate",
                "price_action_evidence_status": "trusted_shadow_requires_broker_paper_confirmation",
                "signal_cohort": "exploratory_crypto_updown_live_model|crypto_btc_updown_daily|outcome=down",
            }
        ],
    )
    write_csv(
        cfg.output_root / "polymarket_portfolio" / "positions.csv",
        [
            {
                "market_id": "btc-confirmation-market",
                "token_id": "btc-confirmation-token",
                "side": "BUY_YES",
                "quantity": "6.0",
                "cost_basis_usdc": "2.0",
                "average_entry_price": "0.33",
                "status": "open",
                "updated_at": "2026-06-30T19:00:00Z",
            }
        ],
    )

    result = render_dashboard(cfg)
    data = read_json(result["dashboard_data"])

    paper_status = data["price_action_paper_signals"]
    assert paper_status["signals"] == 1
    assert paper_status["broker_refresh_needed"] is False
    assert paper_status["pending_broker_signals"] == 0
    assert paper_status["pending_broker_confirmation_signals"] == 0
    assert paper_status["broker_matched_open_signal_positions"] == 1
    assert "already has an open paper position" in paper_status["broker_refresh_reason"]
    assert data["decision_useful_summary"]["trade_decision"] == "PAPER CONFIRMATION READY"


def test_dashboard_tracks_paper_confirmation_probe_exit_horizon(tmp_path):
    cfg = _config(tmp_path)
    opened_at = (datetime.now(timezone.utc) - timedelta(minutes=130)).strftime("%Y-%m-%dT%H:%M:%SZ")
    signal = {
        "price_action_entry_source": "paper_confirmation_candidate",
        "signal_cohort": "macro_economy",
        "market_slug": "macro-test",
        "question": "Macro test market?",
        "outcome": "Yes",
        "max_hold_minutes_before_exit": "120",
    }
    write_csv(
        cfg.output_root / "polymarket_portfolio" / "positions.csv",
        [
            {
                "market_id": "macro-test",
                "token_id": "macro-token",
                "quantity": "4",
                "average_entry_price": "0.5",
                "cost_basis_usdc": "2",
                "status": "open",
                "updated_at": opened_at,
            }
        ],
    )
    write_csv(
        cfg.output_root / "polymarket_portfolio" / "paper_orders.csv",
        [
            {
                "created_at": opened_at,
                "market_id": "macro-test",
                "token_id": "macro-token",
                "side": "BUY_YES",
                "source_signal_json": json.dumps(signal),
            }
        ],
    )

    result = render_dashboard(cfg)
    data = read_json(result["dashboard_data"])
    watch = data["paper_probe_exit_watch"]

    assert watch["status"] == "fixed_horizon_due"
    assert watch["open_confirmation_probes"] == 1
    assert watch["fixed_horizon_due_count"] == 1
    assert watch["next_due_minutes"] == 0.0
    assert watch["preview"][0]["market_slug"] == "macro-test"
    assert watch["preview"][0]["fixed_horizon_due"] is True
    paper_status = data["price_action_paper_signals"]
    assert paper_status["broker_refresh_needed"] is True
    assert paper_status["broker_exit_refresh_needed"] is True
    assert paper_status["pending_broker_exit_probes"] == 1
    assert data["evidence_freshness"]["broker_exit_refresh_needed"] is True
    assert data["evidence_freshness"]["pending_broker_exit_probes"] == 1


def test_dashboard_surfaces_lightweight_paper_maintenance_status(tmp_path):
    cfg = _config(tmp_path)
    status_path = cfg.path.parent / "work" / "polymarket_paper_maintenance_latest_status.json"
    write_json(
        status_path,
        {
            "status": "skipped_high_memory",
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "memory_used_percent": 97.2,
            "max_memory_percent": 95,
            "next_exit_due_utc": "2026-07-01T01:33:39Z",
            "open_confirmation_probes": 3,
            "reason": "Paper broker/dashboard maintenance was skipped because local memory is at or above the guardrail.",
        },
    )

    result = render_dashboard(cfg)
    data = read_json(result["dashboard_data"])
    html = Path(result["dashboard_file"]).read_text(encoding="utf-8")

    assert data["paper_maintenance"]["status"] == "skipped_high_memory"
    assert data["paper_maintenance"]["memory_used_percent"] == 97.2
    assert data["paper_maintenance"]["age_seconds"] is not None
    assert "Paper maintenance" in html


def test_dashboard_treats_old_running_shadow_research_status_as_stale(tmp_path):
    cfg = _config(tmp_path)
    status_path = cfg.path.parent / "work" / "shadow_research_cycle_latest_status.json"
    write_json(
        status_path,
        {
            "status": "running",
            "started_at_utc": (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )

    result = render_dashboard(cfg)
    data = read_json(result["dashboard_data"])

    assert data["shadow_research_cycle"]["status"] == "running"
    assert data["shadow_research_cycle"]["effective_status"] == "stale"
    assert data["oversight_status"]["shadow_research_status"] == "stale"
    assert any(alert["title"] == "Shadow research cycle is stale" for alert in data["oversight_status"]["alerts"])


def test_dashboard_treats_dead_running_shadow_research_runner_as_interrupted(tmp_path):
    cfg = _config(tmp_path)
    status_path = cfg.path.parent / "work" / "shadow_research_cycle_latest_status.json"
    write_json(
        status_path,
        {
            "status": "running",
            "started_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "runner_process_id": 999999999,
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )

    result = render_dashboard(cfg)
    data = read_json(result["dashboard_data"])

    assert data["shadow_research_cycle"]["status"] == "running"
    assert data["shadow_research_cycle"]["effective_status"] == "interrupted"
    assert data["shadow_research_cycle"]["runner_process_active"] is False
    assert data["oversight_status"]["shadow_research_status"] == "interrupted"
    assert data["decision_useful_summary"]["trade_decision"] == "WAIT: CYCLE INTERRUPTED"
    assert any(
        alert["title"] == "Shadow research cycle was interrupted"
        for alert in data["oversight_status"]["alerts"]
    )


def test_dashboard_marks_stale_legacy_full_cycle_as_audit_only(tmp_path):
    cfg = _config(tmp_path)
    write_json(
        cfg.governance_root / "local_live_loop_heartbeat.json",
        {
            "status": "ok",
            "generated_at_utc": "2020-01-01T00:00:00Z",
            "websocket_seconds": 5,
            "full_prediction_cycle": {"status": "running"},
        },
    )

    result = render_dashboard(cfg)
    data = read_json(result["dashboard_data"])

    legacy = data["evidence_freshness"]["legacy_full_cycle"]
    assert data["evidence_freshness"]["live_loop_status"] == "stale"
    assert legacy["raw_status"] == "running"
    assert legacy["effective_status"] == "stale"
    assert legacy["display_status"] == "stale legacy heartbeat (raw: running)"
    assert "do not treat" in legacy["reason"]


def test_dashboard_warns_when_no_driver_is_fresh_and_shadow_cycle_missing(tmp_path):
    cfg = _config(tmp_path)

    result = render_dashboard(cfg)
    data = read_json(result["dashboard_data"])

    alert_titles = [alert["title"] for alert in data["oversight_status"]["alerts"]]
    assert data["shadow_research_cycle"]["effective_status"] == "not_started"
    assert data["evidence_freshness"]["legacy_full_cycle"]["effective_status"] == "not_started"
    assert "Shadow research cycle has not started" in alert_titles
    assert "Driver: legacy live loop (VPS deployment); shadow-cycle status file not expected." not in alert_titles


def test_dashboard_treats_fresh_legacy_loop_as_vps_driver_when_shadow_cycle_missing(tmp_path):
    cfg = _config(tmp_path)
    write_json(
        cfg.governance_root / "local_live_loop_heartbeat.json",
        {
            "status": "ok",
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "websocket_seconds": 5,
            "prediction_cycle_seconds": 30,
            "full_prediction_cycle": {"status": "running"},
        },
    )

    result = render_dashboard(cfg)
    data = read_json(result["dashboard_data"])

    driver_line = "Driver: legacy live loop (VPS deployment); shadow-cycle status file not expected."
    alerts = data["oversight_status"]["alerts"]
    assert data["shadow_research_cycle"]["effective_status"] == "not_started"
    assert data["evidence_freshness"]["legacy_full_cycle"]["effective_status"] == "live"
    assert any(alert["severity"] == "info" and alert["title"] == driver_line for alert in alerts)
    assert not any(alert["title"] == "Shadow research cycle has not started" for alert in alerts)
    assert data["strategy_v2"]["status"] == "not running in this deployment"
    assert data["strategy_v2"]["decision"] == "not running in this deployment"
    assert data["strategy_v2"]["runtime_posture"] == "not_running_in_this_deployment"
    assert data["strategy_v2"]["runtime_reason"] == "Strategy V2 is not running in this deployment."


def test_dashboard_surfaces_aligned_vps_deployment_health(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    monkeypatch.setenv("PM_VPS_DEPLOYED_SHA", "abcdef123456")
    monkeypatch.setenv("PM_IMAGE_BUILD_SHA", "abcdef1")
    monkeypatch.setenv("THE_ODDS_API_KEY", "test-key")

    result = render_dashboard(cfg)
    data = read_json(result["dashboard_data"])
    html = Path(result["dashboard_file"]).read_text(encoding="utf-8")

    health = data["deployment_health"]
    assert health["status"] == "ok"
    assert health["expected_deploy_sha"] == "abcdef123456"
    assert health["dashboard_code_version"] == "abcdef1"
    assert health["version_match"] is True
    assert health["the_odds_api_key_present"] is True
    assert health["blockers"] == []
    assert "Deployment health" in html
    assert "Expected deploy SHA" in html


def test_dashboard_flags_deployment_sha_mismatch_and_missing_odds_key(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    monkeypatch.setenv("PM_VPS_DEPLOYED_SHA", "abcdef123456")
    monkeypatch.setenv("PM_IMAGE_BUILD_SHA", "1234567")
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)

    result = render_dashboard(cfg)
    data = read_json(result["dashboard_data"])

    health = data["deployment_health"]
    assert health["status"] == "needs_attention"
    assert health["version_match"] is False
    assert "deployed_sha_mismatch" in health["blockers"]
    assert "the_odds_api_key_missing" in health["blockers"]


def test_dashboard_surfaces_shadow_research_memory_pause(tmp_path):
    cfg = _config(tmp_path)
    status_path = cfg.path.parent / "work" / "shadow_research_cycle_latest_status.json"
    write_json(
        status_path,
        {
            "status": "skipped_high_memory",
            "ended_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "reason": "Shadow research cycle skipped before websocket/model work because local memory was 99.1% at or above the 99.0% guardrail.",
            "memory_used_percent": 99.1,
            "max_memory_percent": 99.0,
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )

    result = render_dashboard(cfg)
    data = read_json(result["dashboard_data"])

    assert data["shadow_research_cycle"]["effective_status"] == "skipped_high_memory"
    assert data["oversight_status"]["shadow_research_status"] == "skipped_high_memory"
    assert any(
        alert["title"] == "Shadow research paused by memory guard"
        for alert in data["oversight_status"]["alerts"]
    )


def test_dashboard_surfaces_mid_cycle_shadow_research_memory_stop(tmp_path):
    cfg = _config(tmp_path)
    status_path = cfg.path.parent / "work" / "shadow_research_cycle_latest_status.json"
    write_json(
        status_path,
        {
            "status": "stopped_high_memory",
            "ended_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "phase": "after_collect-websocket",
            "reason": "Shadow research cycle stopped at after_collect-websocket because local memory was 99.3% at or above the 99.0% guardrail.",
            "memory_used_percent": 99.3,
            "max_memory_percent": 99.0,
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )

    result = render_dashboard(cfg)
    data = read_json(result["dashboard_data"])

    assert data["shadow_research_cycle"]["effective_status"] == "stopped_high_memory"
    assert data["oversight_status"]["shadow_research_status"] == "stopped_high_memory"
    assert data["oversight_status"]["shadow_research_reason"].startswith("Shadow research cycle stopped")
    assert any(
        alert["title"] == "Shadow research paused by memory guard"
        for alert in data["oversight_status"]["alerts"]
    )


def test_dashboard_warns_when_liquidity_discovery_degrades_but_cycle_continues(tmp_path):
    cfg = _config(tmp_path)
    status_path = cfg.path.parent / "work" / "shadow_research_cycle_latest_status.json"
    write_json(
        status_path,
        {
            "status": "ok",
            "started_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ended_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "liquidity_discovery_status": "degraded_non_blocking",
            "liquidity_discovery_error": "liquidity-discovery timed out after 180 seconds",
            "liquidity_discovery_mode": "targeted-evidence",
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )

    result = render_dashboard(cfg)
    data = read_json(result["dashboard_data"])

    assert data["shadow_research_cycle"]["effective_status"] == "ok"
    assert any(
        alert["title"] == "Liquidity discovery degraded, cycle continued"
        and "timed out" in alert["body"]
        for alert in data["oversight_status"]["alerts"]
    )


def test_dashboard_surfaces_paper_maintenance_task_status(tmp_path):
    cfg = _config(tmp_path)
    status_path = cfg.path.parent / "work" / "polymarket_paper_maintenance_task_status.json"
    write_json(
        status_path,
        {
            "status": "installed",
            "task_name": "Polymarket Paper Maintenance",
            "task_state": "Ready",
            "interval_minutes": 1,
            "max_memory_percent": 95,
            "next_run_time": "2026-07-01T00:10:00Z",
            "installed_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )

    result = render_dashboard(cfg)
    data = read_json(result["dashboard_data"])
    html = Path(result["dashboard_file"]).read_text(encoding="utf-8")

    assert data["paper_maintenance_task"]["status"] == "installed"
    assert data["paper_maintenance_task"]["task_state"] == "Ready"
    assert data["paper_maintenance_task"]["interval_minutes"] == 1
    assert data["paper_maintenance_task"]["age_seconds"] is not None
    assert "Maintenance task" in html


def test_dashboard_marks_missing_paper_maintenance_task_status(tmp_path):
    cfg = _config(tmp_path)

    result = render_dashboard(cfg)
    data = read_json(result["dashboard_data"])

    assert data["paper_maintenance_task"]["status"] == "not_installed_or_unknown"
    assert "No paper-maintenance scheduled-task status file" in data["paper_maintenance_task"]["reason"]


def test_standalone_paper_trade_report_refreshes_profit_tracker_and_dashboard(tmp_path):
    cfg = _config(tmp_path)

    report = paper_trade_report(cfg)

    tracker = read_json(cfg.governance_root / "paper_profit_target_tracker.json")
    dashboard = read_json(cfg.output_root / "polymarket_dashboard" / "dashboard_data.json")
    assert report["mode"] == "paper_trade_refresh"
    assert tracker["current"]["equity_usdc"] == report["broker"]["equity"]
    assert dashboard["paper_broker_summary"]["equity"] == report["broker"]["equity"]
    assert dashboard["actual_profit_target"]["current"]["equity_usdc"] == report["broker"]["equity"]
    assert dashboard["evidence_freshness"]["scoreboard_status"] == "aligned"


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
    assert "Decision cockpit" in html
    assert "Why no trade?" in html
    assert "Oversight cockpit" in html
    assert "Slow / legacy evidence panels" in html
    assert "Historical research panels" in html
    assert data["oversight_status"]["status"] in {"ok", "warn", "bad"}
    assert data["oversight_status"]["alerts"]
    assert diagnostics["approved_signals_count"] == 0
    assert diagnostics["shadow_candidates_seen"] == 1
    assert diagnostics["quarantined_cohort_count"] == 1
    assert "quarantined" in diagnostics["main_blocker"]
    assert diagnostics["current_shadow_candidates"][0]["market_slug"] == "btc-updown-5m-1782490200"


def test_dashboard_surfaces_worldcup_validation_gap(tmp_path):
    cfg = _config(tmp_path)
    row = {
        "market_id": "worldcup-winner-market",
        "market_slug": "world-cup-2026-winner",
        "question": "Who will win the 2026 FIFA World Cup?",
        "category": "worldcup",
        "outcome": "Brazil",
        "token_id": "brazil-token",
        "bookmaker_cross_check_pass": "false",
        "microstructure_filter_pass": "true",
    }
    write_csv(cfg.output_root / "polymarket_predictions" / "predictions.csv", [row])
    write_csv(
        cfg.output_root / "polymarket_predictions" / "rejected_signals.csv",
        [{**row, "rejection_reason": "bookmaker_fundamental_cross_check_failed"}],
    )

    result = render_dashboard(cfg)
    data = read_json(result["dashboard_data"])

    worldcup = data["worldcup_validation_status"]
    assert "World Cup validation layer" in Path(result["dashboard_file"]).read_text(encoding="utf-8")
    assert worldcup["status"] == "missing_bookmaker_fundamental"
    assert worldcup["worldcup_winner_rows"] == 1
    assert worldcup["fundamental_rows"] == 0
    assert worldcup["top_rejection_reasons"][0]["reason"] == "bookmaker_fundamental_cross_check_failed"


def test_dashboard_surfaces_price_action_cohort_transfer_blocker(tmp_path):
    cfg = _config(tmp_path)
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_model_summary.json",
        {
            "status": "trained",
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "decision": "collect_more_bid_ask_price_action_model_evidence",
            "promotion_ready": False,
            "train_positive_targets": 8,
            "validation_positive_targets": 8,
            "validation_blockers": ["no family or signal cohort has tradable positive targets in both train and validation"],
            "cohort_transfer": {
                "state": "positive_targets_do_not_transfer",
                "reason": "Train and validation both contain tradable positive targets, but not in the same family/signal cohort.",
                "family": {
                    "overlap_positive_cohorts": [],
                    "validation_only_positive_cohorts": ["ai_model_leader"],
                    "top_cohorts": [
                        {
                            "cohort": "ai_model_leader",
                            "train_rows": 10,
                            "train_positive_targets": 0,
                            "train_roi": -0.01,
                            "validation_rows": 8,
                            "validation_positive_targets": 8,
                            "validation_roi": 0.05,
                            "positive_target_transfer": False,
                        }
                    ],
                },
            },
        },
    )

    result = render_dashboard(cfg)
    data = read_json(result["dashboard_data"])
    html = Path(result["dashboard_file"]).read_text(encoding="utf-8")

    assert data["price_action_model"]["cohort_transfer"]["state"] == "positive_targets_do_not_transfer"
    assert data["oversight_status"]["cohort_transfer_state"] == "positive_targets_do_not_transfer"
    assert any(alert["title"] == "Model edge does not transfer across cohorts" for alert in data["oversight_status"]["alerts"])
    assert "Cohort transfer check" in html
    assert data["price_action_model"]["cohort_transfer"]["family"]["top_cohorts"][0]["cohort"] == "ai_model_leader"


def test_dashboard_reports_cross_checked_worldcup_rows_blocked_by_trade_gates(tmp_path):
    cfg = _config(tmp_path)
    row = {
        "market_id": "worldcup-winner-market",
        "market_slug": "world-cup-2026-winner",
        "question": "Who will win the 2026 FIFA World Cup?",
        "category": "worldcup",
        "outcome": "Brazil",
        "token_id": "brazil-token",
        "fundamental_probability": "0.22",
        "haircut_fundamental_probability": "0.20",
        "bookmaker_cross_check_pass": "true",
        "microstructure_filter_pass": "true",
    }
    write_csv(cfg.output_root / "polymarket_predictions" / "predictions.csv", [row])
    write_csv(
        cfg.output_root / "polymarket_predictions" / "rejected_signals.csv",
        [{**row, "rejection_reason": "cohort promotion gate failed"}],
    )

    result = render_dashboard(cfg)
    data = read_json(result["dashboard_data"])

    worldcup = data["worldcup_validation_status"]
    assert worldcup["status"] == "collecting_or_blocked_by_trade_gates"
    assert worldcup["fundamental_coverage_pct"] == 100.0
    assert worldcup["bookmaker_cross_check_pass"] == 1


def test_dashboard_surfaces_strategy_v2_anchored_edge_progress(tmp_path):
    cfg = _config(tmp_path)
    candidate = {
        "family": "macro_rates",
        "market_slug": "will-the-fed-increase-interest-rates-by-25-bps-after-the-july-2026-meeting",
        "outcome": "Yes",
        "status": "shadow_candidate",
        "anchor_source": "Reuters_CME_Fed_funds_futures_proxy",
        "anchor_fair_probability": "0.30",
        "executable_price": "0.176",
        "anchor_raw_edge": "0.124",
        "risk_adjusted_anchor_edge": "0.1185",
        "liquidity": "122534",
        "spread": "0.001",
    }
    write_json(
        cfg.output_root / "polymarket_strategy_v2" / "anchored_edge_report.json",
        {
            "status": "ok",
            "generated_at_utc": "2026-06-30T09:11:24Z",
            "decision": "candidate_family_found",
            "recommended_action": "Keep Strategy V2 shadow-only and collect settled evidence for the candidate families.",
            "rows_scored": 33,
            "anchor_rows_loaded": 5,
            "alpha_validated_anchor_rows": 4,
            "worldcup_validated_anchor_rows": 2,
            "anchored_rows": 3,
            "status_counts": {"shadow_candidate": 1, "rejected": 32},
            "top_blockers": {"none": 1, "missing_independent_anchor; edge_not_computable": 9},
            "family_summary": [
                {
                    "family": "macro_rates",
                    "rows": 4,
                    "anchored_rows": 1,
                    "anchored_candidates": 1,
                    "shadow_candidates": 1,
                    "best_edge": 0.1185,
                    "action": "collect_more_shadow_evidence",
                }
            ],
            "top_anchored_rejections": [],
            "warnings": {"missing_anchor_rows": 30, "shadow_only": True},
            "settings": {"promotion_min_candidates": 20, "promotion_min_settled": 10},
        },
    )
    write_csv(cfg.output_root / "polymarket_strategy_v2" / "anchored_edge_candidates.csv", [candidate])
    write_csv(
        cfg.output_root / "polymarket_strategy_v2" / "anchored_edge_persistence_log.csv",
        [
            {
                "logged_at_utc": "2026-06-30T09:11:24Z",
                **candidate,
            }
        ],
    )
    write_json(
        cfg.output_root / "polymarket_strategy_v2" / "strategy_v2_forward_evidence.json",
        {
            "status": "computed",
            "decision": "collect_more_resolved_forward_evidence",
            "unique_forward_candidates": 1,
            "total_mark_pnl_usdc": 1.25,
            "paper_review_candidates": 0,
        },
    )
    write_csv(
        cfg.output_root / "polymarket_strategy_v2" / "strategy_v2_cohort_forward_evidence.csv",
        [
            {
                "signal_cohort": "strategy_v2|macro_rates",
                "family": "macro_rates",
                "candidates": "1",
                "current_shadow_candidates": "1",
                "resolved_candidates": "0",
                "total_mark_pnl_usdc": "1.25",
                "mark_roi": "0.125",
                "status": "collect_resolved_forward_evidence",
            }
        ],
    )
    write_json(
        cfg.output_root / "polymarket_strategy_v2" / "strategy_v2_round_trip_evidence.json",
        {
            "status": "computed",
            "decision": "collect_more_round_trip_evidence",
            "round_trip_candidates": 1,
            "closed_trades": 1,
            "take_profit_exits": 1,
            "realized_pnl_usdc": 0.55,
            "total_mark_pnl_usdc": 0.55,
            "price_action_review_candidates": 0,
        },
    )
    write_csv(
        cfg.output_root / "polymarket_strategy_v2" / "strategy_v2_round_trip_cohort_evidence.csv",
        [
            {
                "signal_cohort": "strategy_v2|macro_rates",
                "family": "macro_rates",
                "candidates": "1",
                "closed_trades": "1",
                "open_trades": "0",
                "take_profit_exits": "1",
                "stop_loss_exits": "0",
                "win_rate": "1.0",
                "realized_pnl_usdc": "0.55",
                "realized_roi": "0.055",
                "total_mark_pnl_usdc": "0.55",
                "status": "collect_more_closed_round_trips",
            }
        ],
    )
    write_csv(
        cfg.output_root / "polymarket_strategy_v2" / "strategy_v2_round_trip_evidence.csv",
        [
            {
                "signal_cohort": "strategy_v2|macro_rates",
                "family": "macro_rates",
                "market_slug": candidate["market_slug"],
                "outcome": "Yes",
                "entry_price": "0.176",
                "latest_bid": "0.186",
                "exit_price": "0.186",
                "round_trip_status": "closed_take_profit",
                "realized_pnl_usdc": "0.55",
                "mark_pnl_usdc": "0.55",
                "observations": "4",
            }
        ],
    )
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_scout_summary.json",
        {
            "status": "computed",
            "decision": "collect_more_bid_ask_price_action_scout_evidence",
            "new_entries": 2,
            "ledger_entries": 3,
            "observed_candidates": 3,
            "closed_trades": 1,
            "open_trades": 2,
            "take_profit_exits": 1,
            "stop_loss_exits": 0,
            "realized_pnl_usdc": 0.75,
            "total_mark_pnl_usdc": 1.1,
            "mark_roi": 0.0366666667,
            "price_action_review_candidates": 0,
            "paper_confirmation_blocker_targets": 4,
        },
    )
    write_csv(
        cfg.output_root / "polymarket_price_action" / "price_action_scout_cohort_evidence.csv",
        [
            {
                "signal_cohort": "price_action_scout|profit_sprint|macro_rates",
                "family": "macro_rates",
                "candidates": "1",
                "closed_trades": "1",
                "open_trades": "0",
                "take_profit_exits": "1",
                "stop_loss_exits": "0",
                "win_rate": "1.0",
                "realized_pnl_usdc": "0.75",
                "realized_roi": "0.075",
                "total_mark_pnl_usdc": "0.75",
                "status": "collect_more_closed_round_trips",
            }
        ],
    )
    write_csv(
        cfg.output_root / "polymarket_price_action" / "price_action_scout_round_trip_evidence.csv",
        [
            {
                "source": "profit_sprint_target",
                "signal_cohort": "price_action_scout|profit_sprint|macro_rates",
                "market_slug": candidate["market_slug"],
                "outcome": "Yes",
                "entry_price": "0.176",
                "latest_bid": "0.19",
                "exit_price": "0.19",
                "round_trip_status": "closed_take_profit",
                "realized_pnl_usdc": "0.75",
                "mark_pnl_usdc": "0.75",
                "candidate_reason": "Positive model target blocked by label gate.",
            }
        ],
    )
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_paper_signal_summary.json",
        {
            "status": "computed",
            "decision": "no_price_action_paper_signals_until_positive_cohort_evidence",
            "signals": 0,
            "rejections": 1,
            "approved_price_action_cohorts": 0,
            "source_round_trip_rows": 1,
        },
    )
    write_csv(
        cfg.output_root / "polymarket_price_action" / "price_action_paper_rejections.csv",
        [
            {
                "market_slug": candidate["market_slug"],
                "outcome": "Yes",
                "signal_cohort": "price_action_scout|profit_sprint|macro_rates",
                "round_trip_status": "closed_take_profit",
                "rejection_reason": "candidate is not currently open for a fresh paper entry",
            }
        ],
    )
    write_json(
        cfg.output_root / "polymarket_price_action" / "microstructure_summary.json",
        {
            "status": "computed",
            "decision": "microstructure_rules_ready_for_forward_shadow",
            "source_rows": 120,
            "tokens": 6,
            "trade_events": 44,
            "rule_rows": 12,
            "validation_pass_rules": 1,
            "current_candidates": 2,
            "top_rule": {
                "rule_id": "bid_momentum_tight|move>=0.01|spread<=0.02",
                "validation_roi": 0.08,
                "validation_pnl_usdc": 1.2,
            },
            "top_rules": [
                {
                    "rule_id": "bid_momentum_tight|move>=0.01|spread<=0.02",
                    "rule_family": "bid_momentum_tight",
                    "validation_trades": 5,
                    "validation_roi": 0.08,
                    "validation_pnl_usdc": 1.2,
                    "validation_pass": True,
                    "status": "candidate_for_forward_shadow",
                }
            ],
            "current_candidates_preview": [
                {
                    "rule_id": "bid_momentum_tight|move>=0.01|spread<=0.02",
                    "market_slug": "eth-updown-test",
                    "outcome": "Up",
                    "latest_bid": 0.51,
                    "latest_ask": 0.52,
                    "validation_roi": 0.08,
                }
            ],
        },
    )
    cycle_status_path = cfg.path.parent / "work" / "strategy_v2_cycle_latest_status.json"
    cycle_status_path.parent.mkdir(parents=True, exist_ok=True)
    cycle_status_path.write_text(
        json.dumps({"status": "ok", "started_at_utc": "2026-06-30T09:06:22Z", "shadow_candidates": 1, "anchored_rows": 3}),
        encoding="utf-8-sig",
    )

    result = render_dashboard(cfg)
    html = Path(result["dashboard_file"]).read_text(encoding="utf-8")
    data = read_json(result["dashboard_data"])
    strategy_v2 = data["strategy_v2"]

    assert "Strategy V2 anchored edge" in html
    assert "Fast price-action scout" in html
    assert "Microstructure edge lab" in html
    assert "Dashboard snapshot" in html
    assert "Paper signals" in html
    assert strategy_v2["decision"] == "candidate_family_found"
    assert strategy_v2["shadow_candidates"] == 1
    assert strategy_v2["anchored_rows"] == 3
    assert strategy_v2["alpha_validated_anchor_rows"] == 4
    assert strategy_v2["worldcup_validated_anchor_rows"] == 2
    assert strategy_v2["cycle_status"]["status"] == "ok"
    assert strategy_v2["forward_evidence"]["decision"] == "collect_more_resolved_forward_evidence"
    assert strategy_v2["forward_evidence_cohorts"][0]["signal_cohort"] == "strategy_v2|macro_rates"
    assert strategy_v2["round_trip_evidence"]["decision"] == "collect_more_round_trip_evidence"
    assert strategy_v2["round_trip_evidence_cohorts"][0]["closed_trades"] == "1"
    assert strategy_v2["round_trip_evidence_top_candidates"][0]["round_trip_status"] == "closed_take_profit"
    assert data["price_action_scout"]["decision"] == "collect_more_bid_ask_price_action_scout_evidence"
    assert data["price_action_scout"]["closed_trades"] == 1
    assert data["price_action_scout"]["paper_confirmation_blocker_targets"] == 4
    assert data["price_action_scout"]["top_candidates"][0]["source"] == "profit_sprint_target"
    assert data["price_action_paper_signals"]["signals"] == 0
    assert data["price_action_paper_signals"]["rejections"] == 1
    assert data["price_action_microstructure"]["validation_pass_rules"] == 1
    assert strategy_v2["top_shadow_candidates"][0]["market_slug"] == candidate["market_slug"]
    assert strategy_v2["promotion_progress"][0]["remaining_shadow_entries_to_review"] == 19


def test_dashboard_surfaces_strategy_v2_memory_pause(tmp_path):
    cfg = _config(tmp_path)
    cycle_status_path = cfg.path.parent / "work" / "strategy_v2_cycle_latest_status.json"
    cycle_status_path.parent.mkdir(parents=True, exist_ok=True)
    cycle_status_path.write_text(
        json.dumps(
            {
                "status": "skipped_high_memory",
                "started_at_utc": "2026-06-30T16:36:20Z",
                "memory_used_percent": 94.5,
                "max_memory_percent": 94,
                "reason": "Strategy V2 cycle skipped before starting heavy work because local memory was at or above the guardrail.",
            }
        ),
        encoding="utf-8-sig",
    )

    result = render_dashboard(cfg)
    data = read_json(result["dashboard_data"])

    assert data["strategy_v2"]["runtime_posture"] == "memory_paused"
    assert data["strategy_v2"]["memory_used_percent"] == 94.5
    assert data["evidence_freshness"]["strategy_v2_runtime_posture"] == "memory_paused"
    assert data["evidence_freshness"]["strategy_v2_memory_percent"] == 94.5
    reason = data["evidence_freshness"]["strategy_v2_runtime_reason"].lower()
    assert "memory" in reason and "guard" in reason
    assert any(alert["title"] == "Collection paused by memory guard" for alert in data["oversight_status"]["alerts"])


def test_dashboard_suppresses_obsolete_strategy_v2_memory_pause_after_fresh_shadow_cycle(tmp_path):
    cfg = _config(tmp_path)
    work_dir = cfg.path.parent / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    strategy_pause_time = now - timedelta(minutes=5)
    shadow_refresh_time = now - timedelta(minutes=1)
    (work_dir / "strategy_v2_cycle_latest_status.json").write_text(
        json.dumps(
            {
                "status": "skipped_high_memory",
                "started_at_utc": strategy_pause_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "ended_at_utc": strategy_pause_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "memory_used_percent": 98.8,
                "max_memory_percent": 95,
                "reason": "Strategy V2 scheduled wrapper skipped before invoking the cycle because local memory was at or above the guardrail.",
            }
        ),
        encoding="utf-8-sig",
    )
    (work_dir / "shadow_research_cycle_latest_status.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "started_at_utc": shadow_refresh_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "ended_at_utc": shadow_refresh_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "paper_trading_invoked": False,
                "live_trading_invoked": False,
            }
        ),
        encoding="utf-8-sig",
    )

    result = render_dashboard(cfg)
    data = read_json(result["dashboard_data"])

    assert data["strategy_v2"]["runtime_posture"] == "memory_paused"
    assert data["oversight_status"]["strategy_v2_memory_pause_obsolete"] is True
    assert not any(alert["title"] == "Collection paused by memory guard" for alert in data["oversight_status"]["alerts"])


def test_dashboard_explains_independent_anchor_blockers(tmp_path):
    cfg = _config(tmp_path)
    write_json(
        cfg.governance_root / "sharp_odds_fetch_summary.json",
        {"status": "error", "rows": 0, "errors": 1, "output_path": "inputs/polymarket/sharp_odds.csv"},
    )
    write_json(
        cfg.governance_root / "crypto_targets_summary.json",
        {"status": "no_terminal_targets", "target_rows": 0, "output_file": "inputs/polymarket/crypto_targets.csv"},
    )
    write_json(
        cfg.governance_root / "crypto_fundamental_summary.json",
        {"status": "no_targets", "fundamental_rows": 0, "output_file": "outputs/polymarket_training/crypto_fundamental_probabilities.csv"},
    )

    result = render_dashboard(cfg)
    html = Path(result["dashboard_file"]).read_text(encoding="utf-8")
    data = read_json(result["dashboard_data"])
    anchors = data["independent_anchor_status"]

    assert "Crypto target generator" in html
    assert anchors["status"] == "setup_needed"
    assert anchors["sharp_odds_fetch"]["blocker"] == "error: 1"
    assert anchors["crypto_targets"]["blocker"] == "no_terminal_targets"
    assert anchors["main_blocker"] == "sharp_odds_fetch: error: 1"


def test_dashboard_treats_fallback_sharp_odds_as_usable_anchor_input(tmp_path):
    cfg = _config(tmp_path)
    write_json(
        cfg.governance_root / "sharp_odds_fetch_summary.json",
        {
            "status": "fallback_loaded",
            "rows": 2,
            "fallback_rows": 2,
            "output_path": "inputs/polymarket/sharp_odds.csv",
        },
    )

    result = render_dashboard(cfg)
    data = read_json(result["dashboard_data"])
    anchors = data["independent_anchor_status"]

    assert anchors["status"] == "usable"
    assert anchors["sharp_odds_fetch"].get("blocker") is None


def test_dashboard_surfaces_sharp_anchor_coverage_by_sport(tmp_path):
    cfg = _config(tmp_path)
    write_json(
        cfg.governance_root / "sharp_anchor_summary.json",
        {
            "status": "built",
            "fundamental_rows": 22,
            "skipped_no_token": 82,
            "coverage_by_sport_market": [
                {
                    "sport": "soccer_fifa_world_cup",
                    "market_key": "h2h",
                    "rows_in": 27,
                    "priced_rows": 27,
                    "fundamental_rows": 24,
                    "skipped_no_token": 3,
                    "h2h_public_search_token_joins": 24,
                    "token_map_joins": 0,
                    "worldcup_winner_token_joins": 0,
                    "incomplete_market_rows": 0,
                }
            ],
        },
    )

    result = render_dashboard(cfg)

    data = read_json(result["dashboard_data"])
    html = Path(result["dashboard_file"]).read_text(encoding="utf-8")
    coverage = data["independent_anchor_status"]["sharp_anchor"]["coverage_by_sport_market"]
    assert coverage[0]["sport"] == "soccer_fifa_world_cup"
    assert coverage[0]["h2h_public_search_token_joins"] == 24
    assert "Sharp anchor coverage by sport" in html


def test_dashboard_normalizes_missing_sharp_anchor_coverage_for_deploy_checks(tmp_path):
    cfg = _config(tmp_path)
    write_json(
        cfg.governance_root / "sharp_anchor_summary.json",
        {"status": "built", "fundamental_rows": 19, "skipped_no_token": 85},
    )

    result = render_dashboard(cfg)

    data = read_json(result["dashboard_data"])
    assert data["independent_anchor_status"]["sharp_anchor"]["coverage_by_sport_market"] == []


def test_dashboard_surfaces_sharp_anchor_alpha_bridge_blockers(tmp_path):
    cfg = _config(tmp_path)
    write_json(
        cfg.governance_root / "sharp_odds_fetch_summary.json",
        {"status": "partial", "rows": 104, "errors": 0},
    )
    write_json(
        cfg.governance_root / "sharp_anchor_summary.json",
        {"status": "built", "fundamental_rows": 19, "output_path": "outputs/polymarket_training/sharp_fundamental_probabilities.csv"},
    )
    write_json(
        cfg.governance_root / "mispricing_alpha_live_summary.json",
        {
            "status": "scored",
            "predictions": 2,
            "scored": 2,
            "trade_candidates": 0,
            "shadow_trade_candidates": 0,
            "fundamental_probabilities_loaded": 19,
            "fundamental_probability_hits": 1,
            "bookmaker_cross_check_failures": 1,
            "microstructure_filter_failures": 0,
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )
    write_csv(
        cfg.output_root / "polymarket_predictions" / "predictions.csv",
        [
            {
                "market_id": "wc-market",
                "market_slug": "will-france-win-the-2026-fifa-world-cup",
                "question": "Will France win the 2026 FIFA World Cup?",
                "token_id": "france-yes",
                "outcome": "Yes",
                "category": "worldcup",
                "fundamental_probability": "0.18",
                "haircut_fundamental_probability": "0.14",
                "fundamental_edge_after_haircut": "-0.01",
                "bookmaker_cross_check_pass": "false",
                "alpha_trade_candidate": "false",
                "shadow_trade_candidate": "false",
                "validation_layer_reason": "bookmaker_fundamental_cross_check_failed",
                "edge_lower_bound": "-0.02",
                "alpha_raw_edge": "0.01",
            }
        ],
    )
    write_csv(
        cfg.output_root / "polymarket_predictions" / "rejected_signals.csv",
        [
            {
                "token_id": "france-yes",
                "rejection_reason": "alpha lower-bound edge below configured minimum; bookmaker_fundamental_cross_check_failed",
            }
        ],
    )

    result = render_dashboard(cfg)

    data = read_json(result["dashboard_data"])
    html = Path(result["dashboard_file"]).read_text(encoding="utf-8")
    bridge = data["mispricing_alpha_bridge"]
    assert bridge["status"] == "fundamental_rows_scored_but_blocked"
    assert bridge["fundamental_probabilities_loaded"] == 19
    assert bridge["fundamental_probability_hits"] == 1
    assert bridge["sharp_fetch_status"] == "partial"
    assert bridge["sharp_anchor_rows"] == 19
    assert bridge["paper_trading_invoked"] is False
    assert bridge["live_trading_invoked"] is False
    assert bridge["top_blockers"][0]["reason"] == "bookmaker_fundamental_cross_check_failed"
    assert any(row["lane"] == "Sharp-anchor alpha bridge" for row in data["decision_useful_summary"]["evidence_lanes"])
    assert "Sharp-anchor alpha bridge" in html


def test_dashboard_surfaces_sharp_sports_edge_funnel(tmp_path):
    cfg = _config(tmp_path)
    write_json(
        cfg.governance_root / "research_focus.json",
        {
            "status": "ok",
            "collection_queries": ["nba", "mlb", "mma", "tennis"],
            "collection_query_guard": {
                "guarded_collection_queries": ["nba", "mlb", "mma", "tennis"],
                "broad_fill_queries": ["nba", "mlb", "mma"],
            },
        },
    )
    write_json(
        cfg.governance_root / "liquidity_discovery_summary.json",
        {
            "status": "ok",
            "family_summary": [
                {
                    "family": "basketball_nba_match",
                    "tokens": 4,
                    "tradable_tokens": 2,
                    "max_liquidity": 950,
                    "min_spread": 0.01,
                },
                {
                    "family": "baseball_mlb_match",
                    "tokens": 3,
                    "tradable_tokens": 1,
                    "max_liquidity": 800,
                    "min_spread": 0.02,
                },
                {
                    "family": "mma_match",
                    "tokens": 2,
                    "tradable_tokens": 1,
                    "max_liquidity": 700,
                    "min_spread": 0.02,
                },
            ],
        },
    )
    write_json(
        cfg.governance_root / "sharp_anchor_summary.json",
        {
            "status": "built",
            "fundamental_rows": 7,
            "coverage_by_sport_market": [
                {
                    "sport": "basketball_nba",
                    "market_key": "h2h",
                    "rows_in": 4,
                    "priced_rows": 4,
                    "fundamental_rows": 3,
                    "skipped_no_token": 1,
                },
                {
                    "sport": "baseball_mlb",
                    "market_key": "h2h",
                    "rows_in": 3,
                    "priced_rows": 3,
                    "fundamental_rows": 2,
                    "skipped_no_token": 1,
                },
                {
                    "sport": "mma_mixed_martial_arts",
                    "market_key": "h2h",
                    "rows_in": 2,
                    "priced_rows": 2,
                    "fundamental_rows": 2,
                    "skipped_no_token": 0,
                },
            ],
        },
    )
    write_json(
        cfg.governance_root / "mispricing_alpha_live_summary.json",
        {
            "status": "scored",
            "fundamental_probabilities_loaded": 7,
            "fundamental_probability_hits": 1,
            "trade_candidates": 0,
            "shadow_trade_candidates": 1,
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )
    write_csv(
        cfg.output_root / "polymarket_predictions" / "predictions.csv",
        [
            {
                "market_id": "nba-market",
                "market_slug": "nba-celtics-knicks",
                "question": "NBA: Will the Boston Celtics beat the New York Knicks?",
                "token_id": "nba-yes",
                "outcome": "Yes",
                "family": "basketball_nba_match",
                "category": "sports_other",
                "fundamental_probability": "0.56",
                "haircut_fundamental_probability": "0.54",
                "alpha_trade_candidate": "false",
                "shadow_trade_candidate": "true",
            }
        ],
    )
    write_json(
        cfg.governance_root / "closing_line_value.json",
        {
            "status": "ok",
            "cohorts": [
                {
                    "signal_cohort": "strategy_v2|basketball_nba_match",
                    "final_positions": 2,
                    "mean_final_clv": 0.03,
                    "clv_evidence": "positive_clv_evidence",
                }
            ],
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )

    result = render_dashboard(cfg)

    data = read_json(result["dashboard_data"])
    html = Path(result["dashboard_file"]).read_text(encoding="utf-8")
    funnel = data["sharp_sports_funnel"]
    nba = next(row for row in funnel["families"] if row["family"] == "basketball_nba_match")
    mlb = next(row for row in funnel["families"] if row["family"] == "baseball_mlb_match")

    assert funnel["status"] == "sharp_shadow_candidates_need_forward_evidence"
    assert funnel["paper_trading_invoked"] is False
    assert funnel["live_trading_invoked"] is False
    assert funnel["total_anchor_rows"] == 7
    assert funnel["total_tradable_liquidity_rows"] == 4
    assert funnel["total_scored_anchor_hits"] == 1
    assert funnel["total_shadow_candidates"] == 1
    assert funnel["total_final_clv_rows"] == 2
    assert nba["query_active"] is True
    assert nba["anchor_rows"] == 3
    assert nba["scored_anchor_hits"] == 1
    assert nba["shadow_candidates"] == 1
    assert nba["final_clv_rows"] == 2
    assert nba["state"] == "positive_clv_watch"
    assert mlb["state"] == "needs_scored_anchor_overlap"
    assert any(row["lane"] == "Sharp sports edge funnel" for row in data["decision_useful_summary"]["evidence_lanes"])
    assert "Sharp sports edge funnel" in html
    assert "Sharp sports family bottlenecks" in html
