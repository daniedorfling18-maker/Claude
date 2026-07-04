from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta, timezone

import yaml

from polymarket_predictive_engine.config import EngineConfig, load_config
from polymarket_predictive_engine.paper_broker import run_paper_broker
from polymarket_predictive_engine.price_action_signals import build_price_action_paper_signals
from polymarket_predictive_engine.utils import read_csv_rows, write_csv, write_json


def _cfg(tmp_path: Path) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "price_action_paper": {
                "enabled": True,
                "max_signals_per_run": 4,
                "max_stake_usdc": 2,
                "minimum_price_edge": 0.005,
                "max_spread": 0.04,
                "max_relative_spread": 0.15,
                "take_profit_return": 0.08,
                "stop_loss_return": 0.06,
                "take_profit_min_usdc": 0.01,
                "minimum_hold_minutes_before_exit": 0,
            },
        },
        path=tmp_path / "cfg.yaml",
    )


def _round_trip_row(**overrides: str) -> dict[str, str]:
    row = {
        "source": "liquidity_fast_feedback",
        "signal_cohort": "price_action_scout|fast_liquidity|crypto",
        "family": "crypto",
        "market_slug": "eth-updown-test",
        "outcome": "Up",
        "token_id": "eth-token",
        "entry_time_utc": "2026-06-30T10:00:00Z",
        "entry_price": "0.50",
        "stake_usdc": "10",
        "quantity": "20",
        "observations": "4",
        "latest_time_utc": "2026-06-30T10:05:00Z",
        "latest_bid": "0.51",
        "latest_ask": "0.52",
        "latest_midpoint": "0.515",
        "latest_spread": "0.01",
        "round_trip_status": "open_marked",
        "mark_pnl_usdc": "0.2",
        "mark_roi": "0.02",
        "take_profit_return": "0.08",
        "stop_loss_return": "0.06",
        "min_profit_usdc": "0.01",
    }
    row.update(overrides)
    return row


def _cohort_row(**overrides: str) -> dict[str, str]:
    row = {
        "signal_cohort": "price_action_scout|fast_liquidity|crypto",
        "family": "crypto",
        "candidates": "8",
        "closed_trades": "5",
        "open_trades": "1",
        "take_profit_exits": "4",
        "stop_loss_exits": "1",
        "win_rate": "0.8",
        "realized_pnl_usdc": "3.4",
        "realized_roi": "0.068",
        "realized_monthly_run_rate_usdc": "120",
        "price_action_review_candidate": "True",
        "status": "ready_for_human_price_action_review",
    }
    row.update(overrides)
    return row


def _entry_row(**overrides: str) -> dict[str, str]:
    row = {
        "source": "liquidity_fast_feedback",
        "signal_cohort": "price_action_scout|fast_liquidity|crypto",
        "family": "crypto",
        "market_slug": "eth-updown-test",
        "question": "ETH up in the next window?",
        "outcome": "Up",
        "token_id": "eth-token",
        "entry_price": "0.50",
        "stake_usdc": "10",
        "quantity": "20",
        "liquidity": "500",
        "spread": "0.01",
        "time_to_close_hours": "1",
        "best_ask": "0.50",
        "top_ask_size": "1000",
        "ask_depth_1pct": "1000",
        "ask_depth_5pct": "1000",
        "websocket_quote_age_seconds": "30",
    }
    row.update(overrides)
    return row


def _microstructure_current_row(**overrides: str) -> dict[str, str]:
    row = {
        "exit_policy_id": "quick_3pct_1obs",
        "rule_id": "bid_momentum_tight|move>=0.035|spread<=0.01",
        "rule_family": "bid_momentum_tight",
        "signal_cohort": "price_action_microstructure|sports_other|bid_momentum_tight|exit=quick_3pct_1obs",
        "token_id": "world-cup-token",
        "market_slug": "world-cup-final-test",
        "question": "World Cup final winner?",
        "family": "sports_other",
        "outcome": "Team A",
        "latest_time_utc": "2026-06-30T10:06:00Z",
        "latest_bid": "0.51",
        "latest_ask": "0.52",
        "latest_midpoint": "0.515",
        "latest_spread": "0.01",
        "best_ask": "0.52",
        "top_ask_size": "1000",
        "ask_depth_1pct": "1000",
        "ask_depth_5pct": "1000",
        "websocket_quote_age_seconds": "30",
        "relative_spread": "0.0192",
        "validation_roi": "0.065",
        "validation_win_rate": "0.75",
        "validation_trades": "4",
        "take_profit_return": "0.03",
        "stop_loss_return": "0.20",
        "max_forward_observations": "1",
        "min_profit_usdc": "0.01",
        "shadow_only": "True",
    }
    row.update(overrides)
    return row


def _ws_feature_row(i: int, **overrides: str) -> dict[str, str]:
    row = {
        "collected_at_utc": f"2026-07-02T05:3{i}:00Z",
        "source_timestamp": str(1000 + i),
        "asset_id": "btc-low-token",
        "market_slug": "bitcoin-above-58k-on-july-2-2026",
        "question": "Will Bitcoin be above 58k?",
        "category": "crypto_btc_special",
        "selection": "No",
        "best_bid": "0.024",
        "best_ask": "0.025",
        "midpoint": "0.0245",
        "spread": "0.001",
        "price_change_side": "BUY",
        "price_change_size": "20",
    }
    row.update(overrides)
    return row


def _low_price_model_summary() -> dict[str, object]:
    return {
        "status": "trained",
        "decision": "collect_more_bid_ask_price_action_model_evidence",
        "promotion_ready": False,
        "validation_rank_diagnostics": {
            "state": "top_ranked_candidates_failed_missed_positive_repricing",
            "missed_positive_examples": [
                {
                    "family": "crypto_btc_special",
                    "market_slug": "bitcoin-above-58k-on-july-2-2026",
                    "entry_ask": "0.025",
                    "exit_bid": "0.026",
                    "roi": 0.04,
                    "low_price_convexity": 0.125,
                    "one_cent_return": 0.40,
                }
            ],
        },
    }


def _non_low_price_model_summary() -> dict[str, object]:
    payload = _low_price_model_summary()
    payload["validation_rank_diagnostics"] = {
        "state": "top_ranked_candidates_failed_missed_positive_repricing",
        "missed_positive_examples": [
            {
                "family": "crypto_eth_updown_daily",
                "market_slug": "eth-up-or-down-on-july-2-2026",
                "entry_ask": "0.40",
                "exit_bid": "0.42",
                "roi": 0.05,
                "low_price_convexity": 0.0,
                "one_cent_return": 0.025,
            }
        ],
    }
    return payload


def _low_price_trade_event(**overrides: str) -> dict[str, str]:
    row = {
        "split": "validation",
        "family": "crypto_btc_special",
        "market_slug": "bitcoin-above-58k-on-july-2-2026",
        "outcome": "No",
        "token_id": "btc-low-token",
        "entry_ask": "0.025",
        "entry_bid": "0.024",
        "entry_spread": "0.001",
        "exit_bid": "0.024",
        "stake_usdc": "2",
        "pnl_usdc": "-0.08",
        "roi": "-0.04",
    }
    row.update(overrides)
    return row


def _positive_analogue_rows(
    *,
    family: str,
    market_slug: str,
    outcome: str,
    entry_bid: str,
    entry_ask: str,
    entry_spread: str = "0.01",
    current_side: str = "",
) -> list[dict[str, str]]:
    return [
        _low_price_trade_event(
            split="validation",
            family=family,
            market_slug=market_slug,
            outcome=outcome,
            entry_bid=entry_bid,
            entry_ask=entry_ask,
            entry_spread=entry_spread,
            current_side=current_side,
            exit_bid=exit_bid,
            pnl_usdc=pnl,
            roi=roi,
        )
        for exit_bid, pnl, roi in (("0.72", "0.9", "0.09"), ("0.71", "0.7", "0.07"), ("0.70", "0.5", "0.05"))
    ]


def _microstructure_feedback_payload(cohort: str) -> dict[str, object]:
    return {
        "status": "ok",
        "learning_state": "price_action_candidates_ready_for_governed_paper_bridge",
        "promotion_candidates": 1,
        "top_cohorts": [
            {
                "source": "microstructure_exit_policy",
                "cohort": cohort,
                "family": "sports_other",
                "rule_id": "bid_momentum_tight|move>=0.035|spread<=0.01",
                "promotion_ready": True,
                "action": "candidate_for_forward_shadow_microstructure",
                "validation_trades": 4,
                "validation_win_rate": 0.75,
                "validation_roi": 0.065,
                "realized_roi": 0.065,
                "exit_policy_id": "quick_3pct_1obs",
                "take_profit_return": 0.03,
                "stop_loss_return": 0.20,
                "priority_score": 100.0,
            }
        ],
        "promotion_candidate_preview": [],
    }


def _paper_confirmation_feedback_payload(
    *,
    cohort: str = "macro_economy",
    trusted: bool = True,
    blocker: str = "",
) -> dict[str, object]:
    return {
        "status": "ok",
        "learning_state": "collect_more_positive_price_action_evidence",
        "paper_confirmation_candidates": 1 if trusted and not blocker else 0,
        "paper_confirmation_preview": [
            {
                "source": "shadow_forward",
                "cohort": cohort,
                "trusted_for_goal": trusted,
                "forward_edge_blocker": blocker,
                "forward_shadow_pnl_usdc": 1.35,
                "forward_shadow_roi": 0.033,
                "monthly_run_rate_usdc": 19.8,
                "recommended_collection_query": "",
                "reason": "Forward shadow P&L is positive; collect broker paper evidence before increasing trust.",
                "priority_score": 24.0,
            }
        ],
    }


def test_positive_price_action_cohort_compiles_paper_signal_without_settlement(tmp_path):
    cfg = _cfg(tmp_path)
    root = cfg.output_root / "polymarket_price_action"
    write_csv(root / "price_action_scout_cohort_evidence.csv", [_cohort_row()])
    write_csv(root / "price_action_scout_round_trip_evidence.csv", [_round_trip_row()])
    write_csv(root / "price_action_scout_entries.csv", [_entry_row()])

    summary = build_price_action_paper_signals(cfg)
    signals = read_csv_rows(root / "price_action_paper_signals.csv")

    assert summary["signals"] == 1
    assert summary["decision"] == "signals_ready_for_paper_broker"
    assert signals[0]["strategy_name"] == "price_action_round_trip"
    assert signals[0]["price_action_signal"] == "True"
    assert signals[0]["market_id"] == "eth-updown-test"
    assert float(signals[0]["executable_price"]) == 0.52


def test_price_action_signals_stay_blocked_until_cohort_evidence_is_positive(tmp_path):
    cfg = _cfg(tmp_path)
    root = cfg.output_root / "polymarket_price_action"
    write_csv(root / "price_action_scout_cohort_evidence.csv", [_cohort_row(price_action_review_candidate="False")])
    write_csv(root / "price_action_scout_round_trip_evidence.csv", [_round_trip_row()])
    write_csv(root / "price_action_scout_entries.csv", [_entry_row()])

    summary = build_price_action_paper_signals(cfg)
    signals = read_csv_rows(root / "price_action_paper_signals.csv")
    rejections = read_csv_rows(root / "price_action_paper_rejections.csv")

    assert summary["signals"] == 0
    assert signals == []
    assert "has not passed positive bid/ask evidence gate" in rejections[0]["rejection_reason"]


def test_feedback_approved_microstructure_candidate_compiles_paper_signal(tmp_path):
    cfg = _cfg(tmp_path)
    root = cfg.output_root / "polymarket_price_action"
    cohort = "price_action_microstructure|sports_other|bid_momentum_tight|exit=quick_3pct_1obs"
    write_json(cfg.governance_root / "price_action_feedback.json", _microstructure_feedback_payload(cohort))
    write_csv(root / "microstructure_current_candidates.csv", [_microstructure_current_row()])

    summary = build_price_action_paper_signals(cfg)
    signals = read_csv_rows(root / "price_action_paper_signals.csv")

    assert summary["signals"] == 1
    assert summary["approved_microstructure_cohorts"] == 1
    assert summary["source_microstructure_current_rows"] == 1
    assert signals[0]["signal_cohort"] == cohort
    assert signals[0]["exit_policy_id"] == "quick_3pct_1obs"
    assert float(signals[0]["take_profit_return"]) == 0.03
    assert float(signals[0]["stop_loss_return"]) == 0.20
    assert signals[0]["price_action_entry_source"] == "microstructure_current_candidate"


def test_microstructure_candidate_stays_blocked_without_feedback_promotion(tmp_path):
    cfg = _cfg(tmp_path)
    root = cfg.output_root / "polymarket_price_action"
    write_csv(root / "microstructure_current_candidates.csv", [_microstructure_current_row()])

    summary = build_price_action_paper_signals(cfg)
    signals = read_csv_rows(root / "price_action_paper_signals.csv")
    rejections = read_csv_rows(root / "price_action_paper_rejections.csv")

    assert summary["signals"] == 0
    assert signals == []
    assert "microstructure cohort has not passed governed feedback promotion gate" in rejections[0]["rejection_reason"]


def test_trusted_shadow_confirmation_backlog_compiles_paper_probe(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.raw["price_action_paper"]["paper_confirmation_max_hold_minutes_before_exit"] = 45
    root = cfg.output_root / "polymarket_price_action"
    write_json(cfg.governance_root / "price_action_feedback.json", _paper_confirmation_feedback_payload())
    write_csv(
        root / "price_action_scout_round_trip_evidence.csv",
        [
            _round_trip_row(
                source="profit_sprint_target",
                signal_cohort="price_action_scout|profit_sprint|macro_economy",
                family="macro_economy",
                market_slug="will-the-us-economy-be-overheating-at-the-end-of-2026",
                question="Will the US economy be overheating?",
                outcome="Yes",
                token_id="macro-token",
                latest_bid="0.42",
                latest_ask="0.43",
                latest_spread="0.01",
            )
        ],
    )
    write_csv(
        root / "price_action_scout_entries.csv",
        [
            _entry_row(
                source="profit_sprint_target",
                signal_cohort="price_action_scout|profit_sprint|macro_economy",
                family="macro_economy",
                market_slug="will-the-us-economy-be-overheating-at-the-end-of-2026",
                question="Will the US economy be overheating?",
                outcome="Yes",
                token_id="macro-token",
                entry_price="0.43",
                liquidity="600",
                spread="0.01",
            )
        ],
    )
    write_csv(
        root / "microstructure_trade_events.csv",
        _positive_analogue_rows(
            family="macro_economy",
            market_slug="will-the-us-economy-be-overheating-at-the-end-of-2026",
            outcome="Yes",
            entry_bid="0.42",
            entry_ask="0.43",
        ),
    )

    summary = build_price_action_paper_signals(cfg)
    signals = read_csv_rows(root / "price_action_paper_signals.csv")

    assert summary["signals"] == 1
    assert summary["paper_confirmation_candidates"] == 1
    assert summary["paper_confirmation_signals"] == 1
    assert signals[0]["signal_cohort"] == "macro_economy"
    assert signals[0]["price_action_entry_source"] == "paper_confirmation_candidate"
    assert signals[0]["price_action_evidence_status"] == "trusted_shadow_requires_broker_paper_confirmation"
    assert float(signals[0]["price_action_cohort_realized_roi"]) == 0.033
    assert float(signals[0]["max_hold_minutes_before_exit"]) == 45.0


def test_paper_confirmation_signal_respects_broker_entry_price_band(tmp_path):
    cfg = _cfg(tmp_path)
    root = cfg.output_root / "polymarket_price_action"
    write_json(cfg.governance_root / "price_action_feedback.json", _paper_confirmation_feedback_payload())
    write_csv(
        root / "price_action_scout_round_trip_evidence.csv",
        [
            _round_trip_row(
                source="profit_sprint_target",
                signal_cohort="price_action_scout|profit_sprint|macro_economy",
                family="macro_economy",
                market_slug="will-the-us-economy-be-overheating-at-the-end-of-2026",
                question="Will the US economy be overheating?",
                outcome="Yes",
                token_id="macro-token",
                latest_bid="0.98",
                latest_ask="0.99",
                latest_spread="0.01",
            )
        ],
    )

    summary = build_price_action_paper_signals(cfg)
    signals = read_csv_rows(root / "price_action_paper_signals.csv")
    rejections = read_csv_rows(root / "price_action_paper_rejections.csv")

    assert summary["signals"] == 0
    assert signals == []
    assert summary["paper_confirmation_candidates"] == 1
    assert summary["entry_price_band_rejections"] == 1
    assert summary["decision"] == "trusted_shadow_edge_waiting_for_fresh_executable_candidate"
    assert rejections[0]["rejection_reason"] == "entry price 0.9900 outside configured band [0.05, 0.90]"
    assert rejections[0]["source"] == "paper_confirmation_candidate"


def test_low_price_tick_probe_stays_research_only_below_broker_entry_price_band(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.raw["price_action_microstructure"] = {
        "enabled": True,
        "lookback_observations": 1,
        "max_rows_per_token": 20,
    }
    cfg.raw["price_action_paper"].update(
        {
            "low_price_tick_probe_enabled": True,
            "low_price_tick_max_stake_usdc": 1,
            "low_price_tick_min_edge": 0.001,
            "low_price_tick_max_edge": 0.02,
        }
    )
    root = cfg.output_root / "polymarket_price_action"
    write_json(root / "price_action_model_summary.json", _low_price_model_summary())
    write_csv(
        root / "microstructure_trade_events.csv",
        [
            _low_price_trade_event(split="train", exit_bid="0.027", pnl_usdc="0.16", roi="0.08"),
            _low_price_trade_event(split="validation", exit_bid="0.028", pnl_usdc="0.24", roi="0.12"),
        ],
    )
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            _ws_feature_row(0, best_bid="0.022", best_ask="0.023", midpoint="0.0225"),
            _ws_feature_row(1, best_bid="0.023", best_ask="0.024", midpoint="0.0235"),
            _ws_feature_row(2, best_bid="0.024", best_ask="0.025", midpoint="0.0245"),
            _ws_feature_row(3, best_bid="0.025", best_ask="0.026", midpoint="0.0255"),
        ],
    )

    summary = build_price_action_paper_signals(cfg)
    signals = read_csv_rows(root / "price_action_paper_signals.csv")
    rejections = read_csv_rows(root / "price_action_paper_rejections.csv")

    assert summary["signals"] == 0
    assert summary["low_price_tick_probe_candidates"] == 1
    assert summary["low_price_tick_probe_signals"] == 0
    assert summary["entry_price_band_rejections"] == 1
    assert summary["decision"] == "low_price_tick_probe_waiting_for_fresh_executable_candidate"
    assert signals == []
    assert rejections[0]["source"] == "low_price_tick_probe"
    assert rejections[0]["rejection_reason"] == "entry price 0.0260 outside configured band [0.05, 0.90]"


def test_low_price_tick_probe_blocks_missed_positive_when_validation_lane_negative(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.raw["price_action_microstructure"] = {
        "enabled": True,
        "lookback_observations": 1,
        "max_rows_per_token": 20,
    }
    cfg.raw["price_action_paper"].update(
        {
            "low_price_tick_probe_enabled": True,
            "low_price_tick_max_stake_usdc": 1,
            "low_price_tick_min_edge": 0.001,
            "low_price_tick_max_edge": 0.02,
        }
    )
    root = cfg.output_root / "polymarket_price_action"
    write_json(root / "price_action_model_summary.json", _low_price_model_summary())
    write_csv(
        root / "microstructure_trade_events.csv",
        [
            _low_price_trade_event(split="validation", exit_bid="0.024", pnl_usdc="-0.08", roi="-0.04"),
            _low_price_trade_event(split="validation", exit_bid="0.028", pnl_usdc="0.24", roi="0.12"),
            _low_price_trade_event(split="validation", exit_bid="0.020", pnl_usdc="-0.40", roi="-0.20"),
        ],
    )
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            _ws_feature_row(0, best_bid="0.022", best_ask="0.023", midpoint="0.0225"),
            _ws_feature_row(1, best_bid="0.023", best_ask="0.024", midpoint="0.0235"),
            _ws_feature_row(2, best_bid="0.024", best_ask="0.025", midpoint="0.0245"),
            _ws_feature_row(3, best_bid="0.025", best_ask="0.026", midpoint="0.0255"),
        ],
    )

    summary = build_price_action_paper_signals(cfg)
    signals = read_csv_rows(root / "price_action_paper_signals.csv")
    evidence = summary["low_price_tick_probe_evidence"]

    assert signals == []
    assert summary["low_price_tick_probe_candidates"] == 0
    assert evidence["diagnostic_missed_positive_examples"] == 1
    assert evidence["gate_reason"] == "model_missed_low_price_positive_but_validation_lane_negative"


def test_low_price_tick_probe_stays_blocked_without_positive_validation_evidence(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.raw["price_action_paper"].update(
        {
            "low_price_tick_probe_enabled": True,
            "low_price_tick_min_validation_positive_examples": 1,
        }
    )
    root = cfg.output_root / "polymarket_price_action"
    write_json(root / "price_action_model_summary.json", _non_low_price_model_summary())
    write_csv(
        root / "microstructure_trade_events.csv",
        [
            _low_price_trade_event(split="train", exit_bid="0.026", pnl_usdc="0.08", roi="0.04"),
            _low_price_trade_event(split="validation", exit_bid="0.024", pnl_usdc="-0.08", roi="-0.04"),
        ],
    )

    summary = build_price_action_paper_signals(cfg)
    signals = read_csv_rows(root / "price_action_paper_signals.csv")
    evidence = summary["low_price_tick_probe_evidence"]

    assert signals == []
    assert summary["low_price_tick_probe_candidates"] == 0
    assert evidence["state"] == "no_positive_validation_low_price_tick_examples"
    assert evidence["validation_positive_rows"] == 0
    assert evidence["positive_rows"] == 1


def test_historical_breadth_scan_surfaces_near_positive_bucket_to_collect(tmp_path):
    cfg = _cfg(tmp_path)
    root = cfg.output_root / "polymarket_price_action"
    rows = []
    for idx in range(3):
        rows.append(
            {
                "split": "validation",
                "family": "crypto_xrp_updown_event",
                "market_slug": "xrp-up-or-down-test",
                "outcome": "Up",
                "token_id": "xrp-token",
                "entry_ask": "0.62",
                "entry_bid": "0.61",
                "entry_spread": "0.01",
                "bid_move_abs": "-0.01",
                "net_buy_events": "-2",
                "net_buy_size": "-120",
                "current_side": "SELL",
                "stake_usdc": "10",
                "pnl_usdc": "0.40",
                "roi": "0.04",
            }
        )
    write_csv(root / "microstructure_trade_events.csv", rows)

    summary = build_price_action_paper_signals(cfg)
    breadth = summary["historical_breadth_scan"]
    near = breadth["top_near_positive_buckets"][0]

    assert breadth["state"] == "positive_validation_pockets_not_robust"
    assert breadth["near_positive_buckets"] >= 1
    assert breadth["recommended_collection_queries"] == ["xrp updown"]
    assert near["recommended_collection_query"] == "xrp updown"
    assert "train_rows<3" in near["blockers"]
    assert "positive_pnl_concentrated_in_one_token" in near["blockers"]


def test_paper_confirmation_current_candidate_requires_positive_historical_analogue(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.raw["price_action_microstructure"] = {
        "enabled": True,
        "lookback_observations": 1,
        "max_rows_per_token": 20,
    }
    cfg.raw["price_action_paper"].update(
        {
            "paper_confirmation_max_stake_usdc": 1,
            "paper_confirmation_max_current_candidates": 4,
            "paper_confirmation_require_positive_historical_analogue": True,
            "paper_confirmation_min_historical_analogue_validation_rows": 3,
            "paper_confirmation_min_historical_analogue_positive_rows": 1,
            "low_price_tick_probe_enabled": False,
        }
    )
    root = cfg.output_root / "polymarket_price_action"
    write_json(
        cfg.governance_root / "price_action_feedback.json",
        _paper_confirmation_feedback_payload(cohort="crypto_btc_special"),
    )
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            _ws_feature_row(0, best_bid="0.057", best_ask="0.058", midpoint="0.0575", price_change_side="BUY"),
            _ws_feature_row(1, best_bid="0.058", best_ask="0.059", midpoint="0.0585", price_change_side="BUY"),
            _ws_feature_row(2, best_bid="0.059", best_ask="0.060", midpoint="0.0595", price_change_side="BUY"),
            _ws_feature_row(3, best_bid="0.060", best_ask="0.061", midpoint="0.0605", price_change_side="BUY"),
        ],
    )
    write_csv(
        root / "microstructure_trade_events.csv",
        [
            _low_price_trade_event(
                split="validation",
                current_side="BUY",
                entry_bid="0.060",
                entry_ask="0.061",
                exit_bid="0.067",
                pnl_usdc="0.20",
                roi="0.10",
            ),
            _low_price_trade_event(
                split="validation",
                current_side="BUY",
                entry_bid="0.060",
                entry_ask="0.061",
                exit_bid="0.066",
                pnl_usdc="0.16",
                roi="0.08",
            ),
            _low_price_trade_event(
                split="validation",
                current_side="BUY",
                entry_bid="0.060",
                entry_ask="0.061",
                exit_bid="0.065",
                pnl_usdc="0.12",
                roi="0.06",
            ),
        ],
    )

    summary = build_price_action_paper_signals(cfg)
    signals = read_csv_rows(root / "price_action_paper_signals.csv")
    analogue = summary["paper_confirmation_current_historical_analogue"]

    assert summary["signals"] == 1
    assert summary["paper_confirmation_current_candidates"] == 1
    assert analogue["state"] == "historical_analogue_current_candidates_ready"
    assert analogue["fresh_matches"] == 1
    assert signals[0]["price_action_entry_source"] == "paper_confirmation_current_candidate"
    assert signals[0]["price_action_evidence_status"] == "trusted_shadow_requires_broker_paper_confirmation"
    assert signals[0]["historical_analogue_gate"] == "positive_historical_analogue"
    assert float(signals[0]["historical_analogue_validation_roi"]) > 0
    assert float(signals[0]["max_stake_usdc"]) == 1.0


def test_paper_confirmation_current_candidate_blocks_out_of_band_price_before_selection(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.raw["price_action_microstructure"] = {
        "enabled": True,
        "lookback_observations": 1,
        "max_rows_per_token": 20,
    }
    cfg.raw["price_action_paper"].update(
        {
            "paper_confirmation_max_stake_usdc": 1,
            "paper_confirmation_max_current_candidates": 4,
            "paper_confirmation_require_positive_historical_analogue": True,
            "paper_confirmation_min_historical_analogue_validation_rows": 3,
            "paper_confirmation_min_historical_analogue_positive_rows": 1,
            "low_price_tick_probe_enabled": False,
        }
    )
    root = cfg.output_root / "polymarket_price_action"
    write_json(
        cfg.governance_root / "price_action_feedback.json",
        _paper_confirmation_feedback_payload(cohort="crypto_btc_special"),
    )
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            _ws_feature_row(0, best_bid="0.972", best_ask="0.982", midpoint="0.977", price_change_side="BUY"),
            _ws_feature_row(1, best_bid="0.976", best_ask="0.986", midpoint="0.981", price_change_side="BUY"),
            _ws_feature_row(2, best_bid="0.978", best_ask="0.988", midpoint="0.983", price_change_side="BUY"),
            _ws_feature_row(3, best_bid="0.980", best_ask="0.990", midpoint="0.985", price_change_side="BUY"),
        ],
    )
    write_csv(
        root / "microstructure_trade_events.csv",
        [
            _low_price_trade_event(
                split="validation",
                current_side="BUY",
                entry_bid="0.980",
                entry_ask="0.990",
                exit_bid="0.999",
                pnl_usdc="0.18",
                roi="0.09",
            ),
            _low_price_trade_event(
                split="validation",
                current_side="BUY",
                entry_bid="0.980",
                entry_ask="0.990",
                exit_bid="0.998",
                pnl_usdc="0.16",
                roi="0.08",
            ),
            _low_price_trade_event(
                split="validation",
                current_side="BUY",
                entry_bid="0.980",
                entry_ask="0.990",
                exit_bid="0.997",
                pnl_usdc="0.14",
                roi="0.07",
            ),
        ],
    )

    summary = build_price_action_paper_signals(cfg)
    signals = read_csv_rows(root / "price_action_paper_signals.csv")
    analogue = summary["paper_confirmation_current_historical_analogue"]
    blocked = analogue["blocked_preview"][0]

    assert signals == []
    assert summary["signals"] == 0
    assert summary["paper_confirmation_current_candidates"] == 0
    assert summary["entry_price_band_rejections"] == 1
    assert summary["decision"] == "trusted_shadow_edge_waiting_for_fresh_executable_candidate"
    assert analogue["state"] == "current_candidates_blocked_by_entry_price_band"
    assert analogue["blocked_by_state"]["entry_price_outside_risk_band"] == 1
    assert blocked["candidate_gate"] == "entry_price_outside_risk_band"
    assert blocked["historical_analogue_gate"] == "positive_historical_analogue"
    assert blocked["rejection_reason"] == "entry price 0.9900 outside configured band [0.05, 0.90]"


def test_paper_confirmation_current_candidate_blocks_negative_historical_analogue(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.raw["price_action_microstructure"] = {
        "enabled": True,
        "lookback_observations": 1,
        "max_rows_per_token": 20,
    }
    cfg.raw["price_action_paper"].update(
        {
            "paper_confirmation_max_current_candidates": 4,
            "paper_confirmation_require_positive_historical_analogue": True,
            "paper_confirmation_min_historical_analogue_validation_rows": 3,
            "low_price_tick_probe_enabled": False,
        }
    )
    root = cfg.output_root / "polymarket_price_action"
    write_json(
        cfg.governance_root / "price_action_feedback.json",
        _paper_confirmation_feedback_payload(cohort="crypto_btc_special"),
    )
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            _ws_feature_row(0, best_bid="0.057", best_ask="0.058", midpoint="0.0575", price_change_side="BUY"),
            _ws_feature_row(1, best_bid="0.058", best_ask="0.059", midpoint="0.0585", price_change_side="BUY"),
            _ws_feature_row(2, best_bid="0.059", best_ask="0.060", midpoint="0.0595", price_change_side="BUY"),
            _ws_feature_row(3, best_bid="0.060", best_ask="0.061", midpoint="0.0605", price_change_side="BUY"),
        ],
    )
    write_csv(
        root / "microstructure_trade_events.csv",
        [
            _low_price_trade_event(
                split="validation",
                current_side="BUY",
                entry_bid="0.060",
                entry_ask="0.061",
                exit_bid="0.058",
                pnl_usdc="-0.10",
                roi="-0.05",
            ),
            _low_price_trade_event(
                split="validation",
                current_side="BUY",
                entry_bid="0.060",
                entry_ask="0.061",
                exit_bid="0.057",
                pnl_usdc="-0.12",
                roi="-0.06",
            ),
            _low_price_trade_event(
                split="validation",
                current_side="BUY",
                entry_bid="0.060",
                entry_ask="0.061",
                exit_bid="0.056",
                pnl_usdc="-0.14",
                roi="-0.07",
            ),
        ],
    )

    summary = build_price_action_paper_signals(cfg)
    signals = read_csv_rows(root / "price_action_paper_signals.csv")
    analogue = summary["paper_confirmation_current_historical_analogue"]

    assert signals == []
    assert summary["decision"] == "trusted_shadow_edge_blocked_by_negative_historical_analogue"
    assert summary["paper_confirmation_current_candidates"] == 0
    assert analogue["state"] == "no_positive_historical_analogue_current_candidate"
    assert analogue["fresh_matches"] == 1
    assert analogue["blocked"] == 1
    assert analogue["blocked_by_state"]["no_positive_historical_analogue_examples"] == 1
    assert analogue["blocked_preview"][0]["family"] == "crypto_btc_special"
    assert analogue["blocked_preview"][0]["historical_analogue_gate"] == "no_positive_historical_analogue_examples"
    assert analogue["blocked_preview"][0]["historical_analogue_key"].startswith("crypto_btc_special|ask=5-10c|")
    assert analogue["blocked_preview"][0]["latest_ask"] == 0.061


def test_blank_side_current_candidate_uses_side_agnostic_history_as_shadow_only_blocker(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.raw["price_action_microstructure"] = {
        "enabled": True,
        "lookback_observations": 1,
        "max_rows_per_token": 20,
    }
    cfg.raw["price_action_paper"].update(
        {
            "paper_confirmation_max_current_candidates": 4,
            "paper_confirmation_require_positive_historical_analogue": True,
            "paper_confirmation_min_historical_analogue_validation_rows": 3,
            "paper_confirmation_min_historical_analogue_positive_rows": 1,
            "low_price_tick_probe_enabled": False,
        }
    )
    root = cfg.output_root / "polymarket_price_action"
    write_json(
        cfg.governance_root / "price_action_feedback.json",
        _paper_confirmation_feedback_payload(cohort="crypto_btc_special"),
    )
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            _ws_feature_row(0, best_bid="0.057", best_ask="0.058", midpoint="0.0575", price_change_side=""),
            _ws_feature_row(1, best_bid="0.058", best_ask="0.059", midpoint="0.0585", price_change_side=""),
            _ws_feature_row(2, best_bid="0.059", best_ask="0.060", midpoint="0.0595", price_change_side=""),
            _ws_feature_row(3, best_bid="0.060", best_ask="0.061", midpoint="0.0605", price_change_side=""),
        ],
    )
    write_csv(
        root / "microstructure_trade_events.csv",
        [
            _low_price_trade_event(
                split="validation",
                current_side="BUY",
                entry_bid="0.060",
                entry_ask="0.061",
                exit_bid="0.067",
                pnl_usdc="0.20",
                roi="0.10",
            ),
            _low_price_trade_event(
                split="validation",
                current_side="BUY",
                entry_bid="0.060",
                entry_ask="0.061",
                exit_bid="0.066",
                pnl_usdc="0.16",
                roi="0.08",
            ),
            _low_price_trade_event(
                split="validation",
                current_side="BUY",
                entry_bid="0.060",
                entry_ask="0.061",
                exit_bid="0.065",
                pnl_usdc="0.12",
                roi="0.06",
            ),
        ],
    )

    summary = build_price_action_paper_signals(cfg)
    signals = read_csv_rows(root / "price_action_paper_signals.csv")
    analogue = summary["paper_confirmation_current_historical_analogue"]
    current_scan = summary["current_historical_analogue_scan"]
    blocked = analogue["blocked_preview"][0]

    assert signals == []
    assert summary["paper_confirmation_current_candidates"] == 0
    assert analogue["fresh_matches"] == 1
    assert analogue["blocked_by_state"]["side_missing_positive_historical_analogue_shadow_only"] == 1
    assert blocked["historical_analogue_gate"] == "side_missing_positive_historical_analogue_shadow_only"
    assert blocked["historical_analogue_validation_rows"] == 0
    assert blocked["side_agnostic_historical_analogue_gate"] == "positive_historical_analogue"
    assert blocked["side_agnostic_historical_analogue_validation_rows"] == 3
    assert float(blocked["side_agnostic_historical_analogue_validation_roi"]) > 0
    assert current_scan["blocked_by_state"]["side_missing_positive_historical_analogue_shadow_only"] == 1
    assert current_scan["positive_matches"] == 0


def test_eth_updown_confirmation_query_matches_current_eth_rows_by_outcome(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.raw["price_action_microstructure"] = {
        "enabled": True,
        "lookback_observations": 1,
        "max_rows_per_token": 20,
    }
    cfg.raw["price_action_paper"].update(
        {
            "paper_confirmation_max_current_candidates": 4,
            "paper_confirmation_require_positive_historical_analogue": True,
            "paper_confirmation_min_historical_analogue_validation_rows": 3,
            "paper_confirmation_min_historical_analogue_positive_rows": 1,
            "low_price_tick_probe_enabled": False,
        }
    )
    root = cfg.output_root / "polymarket_price_action"
    feedback = _paper_confirmation_feedback_payload(
        cohort="exploratory_crypto_updown_live_model|crypto_eth_updown_15m|outcome=up"
    )
    feedback["paper_confirmation_preview"][0]["recommended_collection_query"] = "eth updown"
    write_json(cfg.governance_root / "price_action_feedback.json", feedback)
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            _ws_feature_row(
                0,
                asset_id="eth-up-token",
                market_slug="eth-updown-15m-1783055700",
                category="crypto_eth_updown_15m",
                selection="Up",
                best_bid="0.48",
                best_ask="0.49",
                midpoint="0.485",
                spread="0.01",
                price_change_side="BUY",
            ),
            _ws_feature_row(
                1,
                asset_id="eth-up-token",
                market_slug="eth-updown-15m-1783055700",
                category="crypto_eth_updown_15m",
                selection="Up",
                best_bid="0.49",
                best_ask="0.50",
                midpoint="0.495",
                spread="0.01",
                price_change_side="BUY",
            ),
            _ws_feature_row(
                0,
                asset_id="eth-down-token",
                market_slug="eth-updown-15m-1783055700",
                category="crypto_eth_updown_15m",
                selection="Down",
                best_bid="0.50",
                best_ask="0.51",
                midpoint="0.505",
                spread="0.01",
                price_change_side="BUY",
            ),
            _ws_feature_row(
                1,
                asset_id="eth-down-token",
                market_slug="eth-updown-15m-1783055700",
                category="crypto_eth_updown_15m",
                selection="Down",
                best_bid="0.51",
                best_ask="0.52",
                midpoint="0.515",
                spread="0.01",
                price_change_side="BUY",
            ),
        ],
    )
    write_csv(
        root / "microstructure_trade_events.csv",
        [
            _low_price_trade_event(
                split="validation",
                family="crypto_eth_updown_15m",
                market_slug="eth-updown-15m-1783055700",
                outcome="Up",
                entry_bid="0.49",
                entry_ask="0.50",
                entry_spread="0.01",
                current_side="BUY",
                exit_bid="0.54",
                pnl_usdc="0.8",
                roi="0.08",
            ),
            _low_price_trade_event(
                split="validation",
                family="crypto_eth_updown_15m",
                market_slug="eth-updown-15m-1783055700",
                outcome="Up",
                entry_bid="0.49",
                entry_ask="0.50",
                entry_spread="0.01",
                current_side="BUY",
                exit_bid="0.53",
                pnl_usdc="0.6",
                roi="0.06",
            ),
            _low_price_trade_event(
                split="validation",
                family="crypto_eth_updown_15m",
                market_slug="eth-updown-15m-1783055700",
                outcome="Up",
                entry_bid="0.49",
                entry_ask="0.50",
                entry_spread="0.01",
                current_side="BUY",
                exit_bid="0.52",
                pnl_usdc="0.4",
                roi="0.04",
            ),
        ],
    )

    summary = build_price_action_paper_signals(cfg)
    signals = read_csv_rows(root / "price_action_paper_signals.csv")

    assert summary["signals"] == 1
    assert summary["paper_confirmation_current_historical_analogue"]["fresh_matches"] == 1
    assert signals[0]["market_slug"] == "eth-updown-15m-1783055700"
    assert signals[0]["outcome"] == "Up"
    assert signals[0]["token_id"] == "eth-up-token"
    assert "outcome=up" in signals[0]["signal_cohort"]


def test_paper_confirmation_rejects_mutually_exclusive_same_market_probe(tmp_path):
    cfg = _cfg(tmp_path)
    root = cfg.output_root / "polymarket_price_action"
    feedback = _paper_confirmation_feedback_payload(
        cohort="exploratory_crypto_updown_live_model|crypto_btc_updown_daily|outcome=down"
    )
    feedback["paper_confirmation_preview"][0]["recommended_collection_query"] = "btc updown"
    up_candidate = dict(feedback["paper_confirmation_preview"][0])
    up_candidate["cohort"] = "exploratory_crypto_updown_live_model|crypto_btc_updown_daily|outcome=up"
    up_candidate["priority_score"] = float(up_candidate["priority_score"]) - 1.0
    feedback["paper_confirmation_preview"].append(up_candidate)
    write_json(
        cfg.governance_root / "price_action_feedback.json",
        feedback,
    )
    write_csv(
        root / "price_action_scout_round_trip_evidence.csv",
        [
            _round_trip_row(
                source="profit_sprint_target",
                signal_cohort="price_action_scout|profit_sprint|crypto_btc_updown_event",
                family="crypto_btc_updown_event",
                market_slug="bitcoin-up-or-down-on-july-1-2026",
                question="Bitcoin Up or Down on July 1?",
                outcome="Down",
                token_id="btc-down-token",
                latest_bid="0.32",
                latest_ask="0.33",
                latest_spread="0.01",
            ),
            _round_trip_row(
                source="profit_sprint_target",
                signal_cohort="price_action_scout|profit_sprint|crypto_btc_updown_event",
                family="crypto_btc_updown_event",
                market_slug="bitcoin-up-or-down-on-july-1-2026",
                question="Bitcoin Up or Down on July 1?",
                outcome="Up",
                token_id="btc-up-token",
                latest_bid="0.67",
                latest_ask="0.68",
                latest_spread="0.01",
            ),
        ],
    )
    write_csv(
        root / "price_action_scout_entries.csv",
        [
            _entry_row(
                family="crypto_btc_updown_event",
                market_slug="bitcoin-up-or-down-on-july-1-2026",
                question="Bitcoin Up or Down on July 1?",
                outcome="Down",
                token_id="btc-down-token",
                liquidity="9000",
            ),
            _entry_row(
                family="crypto_btc_updown_event",
                market_slug="bitcoin-up-or-down-on-july-1-2026",
                question="Bitcoin Up or Down on July 1?",
                outcome="Up",
                token_id="btc-up-token",
                liquidity="9000",
            ),
        ],
    )
    write_csv(
        root / "microstructure_trade_events.csv",
        _positive_analogue_rows(
            family="crypto_btc_updown_event",
            market_slug="bitcoin-up-or-down-on-july-1-2026",
            outcome="Down",
            entry_bid="0.32",
            entry_ask="0.33",
        )
        + _positive_analogue_rows(
            family="crypto_btc_updown_event",
            market_slug="bitcoin-up-or-down-on-july-1-2026",
            outcome="Up",
            entry_bid="0.67",
            entry_ask="0.68",
        ),
    )

    summary = build_price_action_paper_signals(cfg)
    signals = read_csv_rows(root / "price_action_paper_signals.csv")
    rejections = read_csv_rows(root / "price_action_paper_rejections.csv")

    assert summary["signals"] == 1
    assert summary["paper_confirmation_signals"] == 1
    assert summary["mutually_exclusive_signal_rejections"] == 1
    assert len(signals) == 1
    assert {row["market_slug"] for row in signals} == {"bitcoin-up-or-down-on-july-1-2026"}
    assert any(
        row["rejection_reason"] == "mutually exclusive paper signal already selected for market"
        for row in rejections
    )


def test_blocked_shadow_confirmation_backlog_stays_rejected(tmp_path):
    cfg = _cfg(tmp_path)
    root = cfg.output_root / "polymarket_price_action"
    write_json(
        cfg.governance_root / "price_action_feedback.json",
        _paper_confirmation_feedback_payload(
            cohort="exploratory_historical_rule|crypto_xrp_updown_5m|outcome=down",
            trusted=False,
            blocker="quarantined_fast_crypto_updown_family",
        ),
    )
    write_csv(
        root / "price_action_scout_round_trip_evidence.csv",
        [
            _round_trip_row(
                signal_cohort="price_action_scout|profit_sprint|crypto_xrp_updown_5m",
                family="crypto_xrp_updown_5m",
                token_id="xrp-token",
                latest_bid="0.42",
                latest_ask="0.43",
                latest_spread="0.01",
            )
        ],
    )

    summary = build_price_action_paper_signals(cfg)
    signals = read_csv_rows(root / "price_action_paper_signals.csv")
    rejections = read_csv_rows(root / "price_action_paper_rejections.csv")

    assert summary["signals"] == 0
    assert summary["paper_confirmation_candidates"] == 0
    assert signals == []
    assert "has not passed positive bid/ask evidence gate" in rejections[0]["rejection_reason"]
    assert rejections[0]["family"] == "crypto_xrp_updown_5m"
    assert rejections[0]["latest_bid"] == "0.42"
    assert rejections[0]["latest_ask"] == "0.43"
    assert rejections[0]["latest_spread"] == "0.01"


def test_trusted_shadow_confirmation_waits_for_fresh_open_candidate(tmp_path):
    cfg = _cfg(tmp_path)
    root = cfg.output_root / "polymarket_price_action"
    write_json(cfg.governance_root / "price_action_feedback.json", _paper_confirmation_feedback_payload())
    write_csv(
        root / "price_action_scout_round_trip_evidence.csv",
        [
            _round_trip_row(
                source="profit_sprint_target",
                signal_cohort="price_action_scout|profit_sprint|macro_economy",
                family="macro_economy",
                market_slug="will-the-us-economy-be-overheating-at-the-end-of-2026",
                question="Will the US economy be overheating?",
                outcome="Yes",
                token_id="macro-token",
                latest_bid="0.42",
                latest_ask="0.43",
                latest_spread="0.01",
                round_trip_status="closed_take_profit",
            )
        ],
    )

    summary = build_price_action_paper_signals(cfg)
    signals = read_csv_rows(root / "price_action_paper_signals.csv")
    rejections = read_csv_rows(root / "price_action_paper_rejections.csv")

    assert signals == []
    assert summary["paper_confirmation_candidates"] == 1
    assert summary["decision"] == "trusted_shadow_edge_waiting_for_fresh_executable_candidate"
    assert rejections[0]["rejection_reason"] == "candidate is not currently open for a fresh paper entry"


def _broker_cfg(tmp_path: Path):
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"] = {
        "data_root": str(tmp_path),
        "output_root": str(tmp_path / "outputs"),
        "database_path": str(tmp_path / "work" / "paper.sqlite"),
    }
    raw["governance_thresholds"]["min_paper_labels"] = 1
    raw["mispricing_alpha"]["enabled"] = False
    raw["paper_trading"]["minimum_reentry_minutes_after_exit"] = 240
    raw["paper_trading"]["minimum_hold_minutes_before_exit"] = 15
    raw["paper_trading"]["take_profit_min_usdc"] = 0.25
    raw["paper_trading"]["max_exit_quote_age_minutes"] = 10000000
    raw["paper_trading"]["require_exit_snapshot_crosscheck"] = False
    raw["price_action_paper"]["observation_minutes"] = 1
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return load_config(config_path)


def _seed_readiness(cfg) -> None:
    training = cfg.output_root / "polymarket_training"
    write_csv(
        training / "market_resolutions.csv",
        [
            {
                "condition_id": "training-market-1",
                "market_slug": "training-market-1",
                "token_id": "training-token-1",
                "target": 1,
                "resolution_quality": "clean_settlement",
            }
        ],
    )
    write_json(
        cfg.output_root / "polymarket_models" / "calibration_v2.json",
        {
            "model_version": "synthetic-calibrator-v1",
            "feature_set_version": "pm-point-in-time-v2",
            "bucket_count": 1,
            "bucket_mapping": {},
            "trained_at": "2026-06-24T00:00:00Z",
        },
    )


def test_paper_broker_fills_price_action_signal_and_exits_from_websocket_bid(tmp_path):
    cfg = _broker_cfg(tmp_path)
    _seed_readiness(cfg)
    root = cfg.output_root / "polymarket_price_action"
    write_csv(root / "price_action_scout_cohort_evidence.csv", [_cohort_row()])
    write_csv(root / "price_action_scout_round_trip_evidence.csv", [_round_trip_row(latest_bid="0.49", latest_ask="0.50")])
    write_csv(root / "price_action_scout_entries.csv", [_entry_row(liquidity="1000")])
    build_price_action_paper_signals(cfg)

    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            {
                "collected_at_utc": "2026-06-30T10:00:00Z",
                "asset_id": "eth-token",
                "market_slug": "eth-updown-test",
                "selection": "Up",
                "best_bid": "0.49",
                "best_ask": "0.50",
                "midpoint": "0.495",
                "spread": "0.01",
            }
        ],
    )

    first = run_paper_broker(cfg)
    assert first["orders_filled"] == 1
    assert first["filled_orders"][0]["risk"]["risk_profile"] == "price_action_paper_probe"

    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            {
                "collected_at_utc": "2026-06-30T10:06:00Z",
                "asset_id": "eth-token",
                "market_slug": "eth-updown-test",
                "selection": "Up",
                "best_bid": "0.55",
                "best_ask": "0.56",
                "midpoint": "0.555",
                "spread": "0.01",
            }
        ],
    )

    second = run_paper_broker(cfg)
    assert second["exit_orders_filled"] == 1
    assert second["closed_positions"][0]["reason"] == "take_profit"
    assert second["closed_positions"][0]["realised_pnl_usdc"] > 0


def test_paper_broker_exits_microstructure_signal_at_fixed_horizon(tmp_path):
    cfg = _broker_cfg(tmp_path)
    _seed_readiness(cfg)
    root = cfg.output_root / "polymarket_price_action"
    cohort = "price_action_microstructure|sports_other|bid_momentum_tight|exit=quick_3pct_1obs"
    write_json(cfg.governance_root / "price_action_feedback.json", _microstructure_feedback_payload(cohort))
    write_csv(root / "microstructure_current_candidates.csv", [_microstructure_current_row(latest_bid="0.49", latest_ask="0.50")])
    build_price_action_paper_signals(cfg)

    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            {
                "collected_at_utc": "2026-06-30T10:00:00Z",
                "asset_id": "world-cup-token",
                "market_slug": "world-cup-final-test",
                "selection": "Team A",
                "best_bid": "0.49",
                "best_ask": "0.50",
                "midpoint": "0.495",
                "spread": "0.01",
            }
        ],
    )

    first = run_paper_broker(cfg)
    assert first["orders_filled"] == 1

    from polymarket_predictive_engine.storage import connect_db

    old_time = (datetime.now(timezone.utc) - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    con = connect_db(cfg.database_path)
    try:
        with con:
            con.execute("UPDATE positions SET updated_at = ? WHERE token_id = ?", (old_time, "world-cup-token"))
    finally:
        con.close()

    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            {
                "collected_at_utc": "2026-06-30T10:02:00Z",
                "asset_id": "world-cup-token",
                "market_slug": "world-cup-final-test",
                "selection": "Team A",
                "best_bid": "0.50",
                "best_ask": "0.51",
                "midpoint": "0.505",
                "spread": "0.01",
            }
        ],
    )

    second = run_paper_broker(cfg)
    assert second["exit_orders_filled"] == 1
    assert second["closed_positions"][0]["reason"] == "fixed_horizon"
