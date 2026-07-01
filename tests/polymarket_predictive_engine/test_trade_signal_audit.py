from __future__ import annotations

import json
from pathlib import Path

from pytest import approx

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.trade_signal_audit import build_trade_signal_audit
from polymarket_predictive_engine.utils import write_csv, write_json


def _cfg(tmp_path: Path) -> EngineConfig:
    return EngineConfig(
        raw={"paths": {"output_root": str(tmp_path / "outputs")}},
        path=tmp_path / "cfg.yaml",
    )


def test_trade_signal_audit_translates_signal_into_executable_bid_math(tmp_path):
    cfg = _cfg(tmp_path)
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_paper_signal_summary.json",
        {"status": "computed", "signals": 1, "rejections": 0},
    )
    write_csv(
        cfg.output_root / "polymarket_price_action" / "price_action_paper_signals.csv",
        [
            {
                "market_slug": "macro-test",
                "outcome": "Yes",
                "signal_cohort": "macro_economy",
                "executable_price": "0.50",
                "market_price": "0.48",
                "spread": "0.02",
                "relative_spread": "0.04",
                "max_stake_usdc": "2",
                "take_profit_return": "0.08",
                "take_profit_min_usdc": "0.05",
                "stop_loss_return": "0.06",
                "max_hold_minutes_before_exit": "120",
                "price_action_evidence_status": "trusted_shadow_requires_broker_paper_confirmation",
                "price_action_entry_source": "paper_confirmation_candidate",
                "edge": "0.04",
            }
        ],
    )

    payload = build_trade_signal_audit(cfg)

    thesis = payload["current_signal_theses"][0]
    assert payload["verdict"] == "current_signals_require_broker_or_manual_review"
    assert thesis["entry_ask"] == approx(0.50)
    assert thesis["current_exit_bid"] == approx(0.48)
    assert thesis["estimated_quantity"] == approx(4.0)
    assert thesis["estimated_spread_cost_usdc"] == approx(0.08)
    assert thesis["break_even_exit_bid"] == approx(0.50)
    assert thesis["required_take_profit_bid"] == approx(0.54)
    assert thesis["current_bid_gap_to_profit"] == approx(0.06)
    assert thesis["stop_loss_bid"] == approx(0.47)


def test_trade_signal_audit_explains_fixed_horizon_loss(tmp_path):
    cfg = _cfg(tmp_path)
    source_signal = {
        "market_slug": "macro-test",
        "outcome": "Yes",
        "signal_cohort": "macro_economy",
        "price_action_entry_source": "paper_confirmation_candidate",
        "price_action_evidence_status": "trusted_shadow_requires_broker_paper_confirmation",
        "max_hold_minutes_before_exit": "120",
        "take_profit_return": "0.08",
        "stop_loss_return": "0.06",
    }
    write_csv(
        cfg.output_root / "polymarket_portfolio" / "paper_orders.csv",
        [
            {
                "order_id": "buy-1",
                "created_at": "2026-06-30T21:33:40Z",
                "market_id": "macro-test",
                "token_id": "token-1",
                "side": "BUY_YES",
                "strategy_name": "price_action_round_trip",
                "source_signal_json": json.dumps(source_signal),
            },
            {
                "order_id": "sell-1",
                "created_at": "2026-07-01T02:12:43Z",
                "market_id": "macro-test",
                "token_id": "token-1",
                "side": "SELL_YES",
                "strategy_name": "paper_exit_fixed_horizon",
            },
        ],
    )
    write_csv(
        cfg.output_root / "polymarket_portfolio" / "paper_fills.csv",
        [
            {
                "order_id": "buy-1",
                "created_at": "2026-06-30T21:33:40Z",
                "market_id": "macro-test",
                "token_id": "token-1",
                "side": "BUY_YES",
                "fill_price": "0.37",
                "quantity": "5.4054054054",
                "gross_notional_usdc": "2.0",
                "fee_usdc": "0",
            },
            {
                "order_id": "sell-1",
                "created_at": "2026-07-01T02:12:43Z",
                "market_id": "macro-test",
                "token_id": "token-1",
                "side": "SELL_YES",
                "fill_price": "0.36",
                "quantity": "5.4054054054",
                "gross_notional_usdc": "1.9459459459",
                "fee_usdc": "0",
            },
        ],
    )
    write_csv(
        cfg.output_root / "polymarket_portfolio" / "positions.csv",
        [{"market_id": "macro-test", "token_id": "token-1", "status": "closed"}],
    )

    payload = build_trade_signal_audit(cfg)

    exit_row = payload["recent_exit_preview"][0]
    assert payload["recent_loss_exit_count"] == 1
    assert exit_row["loss_making_exit"] is True
    assert exit_row["exit_reason"] == "fixed_horizon"
    assert exit_row["realised_pnl_usdc"] == approx(-0.0540540541)
    assert "did not reprice enough" in exit_row["diagnosis"]
