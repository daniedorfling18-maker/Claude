from __future__ import annotations

from pathlib import Path

from pytest import approx

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.strategy_v2_round_trip import build_strategy_v2_round_trip_evidence
from polymarket_predictive_engine.utils import read_csv_rows, write_csv


def _cfg(tmp_path: Path) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "strategy_v2": {
                "forward_evidence_stake_usdc": 10,
                "round_trip_take_profit_return": 0.08,
                "round_trip_stop_loss_return": 0.06,
                "round_trip_min_profit_usdc": 0.25,
                "round_trip_min_closed_trades": 2,
                "round_trip_min_roi": 0.03,
                "round_trip_min_win_rate": 0.55,
            },
        },
        path=tmp_path / "cfg.yaml",
    )


def _forward_row(**overrides: str) -> dict[str, str]:
    row = {
        "signal_cohort": "strategy_v2|macro_rates",
        "family": "macro_rates",
        "market_slug": "fed-july",
        "outcome": "Yes",
        "token_id": "fed-token",
        "first_seen_at_utc": "2026-06-30T10:00:00Z",
        "entry_price": "0.50",
        "stake_usdc": "10",
        "quantity": "20",
    }
    row.update(overrides)
    return row


def _websocket_row(collected_at: str, bid: str, **overrides: str) -> dict[str, str]:
    row = {
        "collected_at_utc": collected_at,
        "asset_id": "fed-token",
        "market_slug": "fed-july",
        "selection": "Yes",
        "best_bid": bid,
        "best_ask": str(float(bid) + 0.01),
        "midpoint": str(float(bid) + 0.005),
        "spread": "0.01",
    }
    row.update(overrides)
    return row


def test_strategy_v2_round_trip_closes_take_profit_from_bid(tmp_path):
    cfg = _cfg(tmp_path)
    write_csv(cfg.output_root / "polymarket_strategy_v2" / "strategy_v2_forward_evidence.csv", [_forward_row()])
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            _websocket_row("2026-06-30T10:05:00Z", "0.52"),
            _websocket_row("2026-06-30T10:10:00Z", "0.56"),
        ],
    )

    summary = build_strategy_v2_round_trip_evidence(cfg)
    rows = read_csv_rows(cfg.output_root / "polymarket_strategy_v2" / "strategy_v2_round_trip_evidence.csv")
    cohorts = read_csv_rows(cfg.output_root / "polymarket_strategy_v2" / "strategy_v2_round_trip_cohort_evidence.csv")

    assert summary["closed_trades"] == 1
    assert summary["take_profit_exits"] == 1
    assert rows[0]["round_trip_status"] == "closed_take_profit"
    assert rows[0]["exit_reason"] == "take_profit"
    assert float(rows[0]["exit_price"]) == approx(0.56)
    assert float(rows[0]["realized_pnl_usdc"]) == approx(1.2)
    assert float(rows[0]["realized_roi"]) == approx(0.12)
    assert cohorts[0]["closed_trades"] == "1"
    assert float(cohorts[0]["realized_pnl_usdc"]) == approx(1.2)


def test_strategy_v2_round_trip_closes_stop_loss_from_bid(tmp_path):
    cfg = _cfg(tmp_path)
    write_csv(cfg.output_root / "polymarket_strategy_v2" / "strategy_v2_forward_evidence.csv", [_forward_row()])
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [_websocket_row("2026-06-30T10:05:00Z", "0.46")],
    )

    summary = build_strategy_v2_round_trip_evidence(cfg)
    rows = read_csv_rows(cfg.output_root / "polymarket_strategy_v2" / "strategy_v2_round_trip_evidence.csv")

    assert summary["closed_trades"] == 1
    assert summary["stop_loss_exits"] == 1
    assert rows[0]["round_trip_status"] == "closed_stop_loss"
    assert rows[0]["exit_reason"] == "stop_loss"
    assert float(rows[0]["realized_pnl_usdc"]) == approx(-0.8)
    assert float(rows[0]["realized_roi"]) == approx(-0.08)


def test_strategy_v2_round_trip_keeps_open_position_marked_to_latest_bid(tmp_path):
    cfg = _cfg(tmp_path)
    write_csv(cfg.output_root / "polymarket_strategy_v2" / "strategy_v2_forward_evidence.csv", [_forward_row()])
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            _websocket_row("2026-06-30T10:05:00Z", "0.51"),
            _websocket_row("2026-06-30T10:10:00Z", "0.52"),
        ],
    )

    summary = build_strategy_v2_round_trip_evidence(cfg)
    rows = read_csv_rows(cfg.output_root / "polymarket_strategy_v2" / "strategy_v2_round_trip_evidence.csv")

    assert summary["closed_trades"] == 0
    assert summary["open_trades"] == 1
    assert rows[0]["round_trip_status"] == "open_marked"
    assert rows[0]["exit_reason"] == "still_open"
    assert float(rows[0]["latest_bid"]) == approx(0.52)
    assert float(rows[0]["mark_pnl_usdc"]) == approx(0.4)
    assert float(rows[0]["mark_roi"]) == approx(0.04)
