from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import yaml

from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.dashboard import render_dashboard
from polymarket_predictive_engine.execution.paper import paper_trade_report
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
        [{"signal_cohort": "macro_economy", "market_slug": "macro-test", "token_id": "macro-token"}],
    )

    result = render_dashboard(cfg)
    data = read_json(result["dashboard_data"])

    paper_status = data["price_action_paper_signals"]
    assert paper_status["broker_refresh_needed"] is True
    assert paper_status["pending_broker_signals"] == 3
    assert paper_status["pending_broker_confirmation_signals"] == 3
    assert data["evidence_freshness"]["broker_refresh_needed"] is True
    assert data["evidence_freshness"]["pending_broker_signals"] == 3


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
    assert "Why no trade?" in html
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
            "decision": "collect_more_closed_price_action_scout_evidence",
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
    assert strategy_v2["decision"] == "candidate_family_found"
    assert strategy_v2["shadow_candidates"] == 1
    assert strategy_v2["anchored_rows"] == 3
    assert strategy_v2["worldcup_validated_anchor_rows"] == 2
    assert strategy_v2["cycle_status"]["status"] == "ok"
    assert strategy_v2["forward_evidence"]["decision"] == "collect_more_resolved_forward_evidence"
    assert strategy_v2["forward_evidence_cohorts"][0]["signal_cohort"] == "strategy_v2|macro_rates"
    assert strategy_v2["round_trip_evidence"]["decision"] == "collect_more_round_trip_evidence"
    assert strategy_v2["round_trip_evidence_cohorts"][0]["closed_trades"] == "1"
    assert strategy_v2["round_trip_evidence_top_candidates"][0]["round_trip_status"] == "closed_take_profit"
    assert data["price_action_scout"]["decision"] == "collect_more_closed_price_action_scout_evidence"
    assert data["price_action_scout"]["closed_trades"] == 1
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
