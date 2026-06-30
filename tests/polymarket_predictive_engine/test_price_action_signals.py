from __future__ import annotations

from pathlib import Path

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
    }
    row.update(overrides)
    return row


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
