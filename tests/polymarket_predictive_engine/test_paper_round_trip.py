from __future__ import annotations

import json

import pytest

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.paper_round_trip import build_paper_round_trip_evidence
from polymarket_predictive_engine.price_action_model import train_price_action_model
from polymarket_predictive_engine.storage import connect_db
from polymarket_predictive_engine.utils import read_csv_rows


def _cfg(tmp_path) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {
                "output_root": str(tmp_path / "outputs"),
                "database_path": str(tmp_path / "work" / "paper.sqlite"),
            },
            "price_action_model": {
                "minimum_rows": 1,
                "minimum_validation_rows": 1,
                "minimum_profitable_return_label": 0.03,
            },
        },
        path=tmp_path / "config.yaml",
    )


def _insert_order(con, *, order_id: str, created_at: str, market_id: str, token_id: str, side: str, price: float, stake: float, quantity: float, source_signal: dict):
    con.execute(
        """
        INSERT INTO orders(
            order_id, idempotency_key, created_at, updated_at, mode, status,
            market_id, token_id, side, limit_price, stake_usdc, quantity,
            category, event_id, correlation_key, strategy_name, model_version,
            prediction_timestamp, risk_decision_json, source_signal_json
        ) VALUES (?, ?, ?, ?, 'paper', 'filled', ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, '{}', ?)
        """,
        (
            order_id,
            f"idempotency|{order_id}",
            created_at,
            created_at,
            market_id,
            token_id,
            side,
            price,
            stake,
            quantity,
            source_signal.get("category", ""),
            market_id,
            source_signal.get("strategy_name", "price_action_round_trip"),
            source_signal.get("model_version", "price_action_round_trip_v1"),
            created_at,
            json.dumps(source_signal),
        ),
    )


def _insert_fill(con, *, fill_id: str, order_id: str, created_at: str, market_id: str, token_id: str, side: str, price: float, quantity: float):
    con.execute(
        """
        INSERT INTO fills(
            fill_id, order_id, idempotency_key, created_at, market_id, token_id,
            side, fill_price, quantity, gross_notional_usdc, fee_usdc, slippage_usdc, fill_model
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)
        """,
        (
            fill_id,
            order_id,
            f"fill|{fill_id}",
            created_at,
            market_id,
            token_id,
            side,
            price,
            quantity,
            price * quantity,
            "exit_taker" if side == "SELL_YES" else "taker_immediate",
        ),
    )


def _insert_snapshot(con, *, snapshot_id: str, collected_at: str, market_id: str, token_id: str, bid: float, ask: float):
    con.execute(
        """
        INSERT INTO market_snapshots(
            snapshot_id, idempotency_key, collected_at, market_id, token_id,
            best_bid, best_ask, midpoint, spread, liquidity, raw_payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1000, '{}')
        """,
        (
            snapshot_id,
            f"snapshot|{snapshot_id}",
            collected_at,
            market_id,
            token_id,
            bid,
            ask,
            (bid + ask) / 2,
            ask - bid,
        ),
    )


def test_paper_round_trip_exports_realised_broker_fills_as_strict_feedback(tmp_path):
    cfg = _cfg(tmp_path)
    con = connect_db(cfg.database_path)
    signal = {
        "market_slug": "bitcoin-up-or-down-on-july-1-2026",
        "question": "Bitcoin Up or Down on July 1?",
        "outcome": "Down",
        "category": "crypto_btc_updown_event",
        "signal_cohort": "exploratory_crypto_updown_live_model|crypto_btc_updown_daily|outcome=down",
        "edge_lower_bound": "0.05",
        "price_action_entry_source": "paper_confirmation_candidate",
        "price_action_evidence_status": "trusted_shadow_requires_broker_paper_confirmation",
        "price_action_latest_bid": "0.39",
        "price_action_latest_ask": "0.40",
        "price_action_spread": "0.01",
        "price_action_latest_time_utc": "2026-07-01T10:00:00Z",
        "take_profit_return": "0.08",
        "stop_loss_return": "0.06",
        "strategy_name": "price_action_round_trip",
        "model_version": "price_action_round_trip_v1",
    }
    try:
        _insert_order(con, order_id="buy-win", created_at="2026-07-01T10:00:00Z", market_id="m1", token_id="t1", side="BUY_YES", price=0.40, stake=4.0, quantity=10.0, source_signal=signal)
        _insert_fill(con, fill_id="fill-buy-win", order_id="buy-win", created_at="2026-07-01T10:00:00Z", market_id="m1", token_id="t1", side="BUY_YES", price=0.40, quantity=10.0)
        _insert_snapshot(con, snapshot_id="snap-buy-win", collected_at="2026-07-01T10:09:30Z", market_id="condition-m1", token_id="t1", bid=0.51, ask=0.52)
        _insert_order(con, order_id="sell-win", created_at="2026-07-01T10:10:00Z", market_id="m1", token_id="t1", side="SELL_YES", price=0.52, stake=5.2, quantity=10.0, source_signal={"side": "SELL_YES", "reason": "take_profit", "quote": {"best_bid": 0.52, "best_ask": 0.53, "spread": 0.01, "timestamp": "2026-07-01T10:10:00Z", "source": "websocket_market_features"}})
        _insert_fill(con, fill_id="fill-sell-win", order_id="sell-win", created_at="2026-07-01T10:10:00Z", market_id="m1", token_id="t1", side="SELL_YES", price=0.52, quantity=10.0)

        losing_signal = {**signal, "market_slug": "xrp-up-or-down-on-july-1-2026", "question": "XRP Up or Down on July 1?", "outcome": "Up", "category": "crypto_xrp_updown_event", "signal_cohort": "crypto_xrp_updown_event"}
        _insert_order(con, order_id="buy-loss", created_at="2026-07-01T11:00:00Z", market_id="m2", token_id="t2", side="BUY_YES", price=0.80, stake=4.0, quantity=5.0, source_signal=losing_signal)
        _insert_fill(con, fill_id="fill-buy-loss", order_id="buy-loss", created_at="2026-07-01T11:00:00Z", market_id="m2", token_id="t2", side="BUY_YES", price=0.80, quantity=5.0)
        _insert_snapshot(con, snapshot_id="snap-buy-loss", collected_at="2026-07-01T11:04:30Z", market_id="m2", token_id="t2", bid=0.60, ask=0.61)
        _insert_order(con, order_id="sell-loss", created_at="2026-07-01T11:05:00Z", market_id="m2", token_id="t2", side="SELL_YES", price=0.60, stake=3.0, quantity=5.0, source_signal={"side": "SELL_YES", "reason": "hard_stop_loss", "quote": {"best_bid": 0.60, "best_ask": 0.61, "spread": 0.01, "timestamp": "2026-07-01T11:05:00Z", "source": "websocket_market_features"}})
        _insert_fill(con, fill_id="fill-sell-loss", order_id="sell-loss", created_at="2026-07-01T11:05:00Z", market_id="m2", token_id="t2", side="SELL_YES", price=0.60, quantity=5.0)
        con.commit()
    finally:
        con.close()

    summary = build_paper_round_trip_evidence(cfg)
    rows = read_csv_rows(cfg.output_root / "polymarket_price_action" / "paper_broker_round_trip_evidence.csv")

    assert summary["closed_round_trips"] == 2
    assert summary["positive_round_trips"] == 1
    assert summary["negative_round_trips"] == 1
    assert summary["realized_pnl_usdc"] == pytest.approx(0.2)
    assert summary["quote_audit_status_counts"] == [
        {
            "quote_consistency_status": "ok",
            "round_trips": 2,
            "pnl_usdc": pytest.approx(0.2),
            "sample_reason": "exit quote bid is consistent with independent snapshot bid",
        }
    ]
    btc_quote_audit = next(
        row
        for row in summary["quote_audit_by_cohort"]
        if row["signal_cohort"] == "exploratory_crypto_updown_live_model|crypto_btc_updown_daily|outcome=down"
    )
    assert btc_quote_audit["round_trips"] == 1
    assert btc_quote_audit["quote_consistent_round_trips"] == 1
    assert btc_quote_audit["audited_pnl_usdc"] == pytest.approx(1.2)
    assert btc_quote_audit["excluded_from_audit_pnl_usdc"] == pytest.approx(0.0)
    assert btc_quote_audit["recommended_action"] == "quote-consistent; eligible for audited paper feedback subject to existing model gates"
    assert [row["round_trip_status"] for row in rows] == ["closed_take_profit", "closed_stop_loss"]
    assert rows[0]["entry_price"] == "0.4"
    assert rows[0]["exit_price"] == "0.52"
    assert rows[0]["snapshot_match_mode"] == "token_only_alias"
    assert rows[0]["snapshot_market_id"] == "condition-m1"
    assert rows[0]["observations"] == "1"
    assert rows[0]["entry_signal_bid"] == "0.39"
    assert rows[0]["entry_signal_ask"] == "0.40"
    assert rows[0]["exit_quote_bid"] == "0.52"
    assert rows[0]["exit_quote_source"] == "websocket_market_features"
    assert rows[0]["quote_consistency_status"] == "ok"
    assert rows[0]["signal_cohort"] == "exploratory_crypto_updown_live_model|crypto_btc_updown_daily|outcome=down"
    assert rows[1]["exit_reason"] == "hard_stop_loss"

    model = train_price_action_model(cfg)
    source = model["training_event_sources"]["paper_broker_round_trip"]
    assert model["training_events"] == 2
    assert source["prepared_rows"] == 2
    assert source["positive_targets"] == 1


def test_paper_round_trip_quarantines_quote_conflicts_from_model_training(tmp_path):
    cfg = _cfg(tmp_path)
    con = connect_db(cfg.database_path)
    signal = {
        "market_slug": "bitcoin-above-60k-on-july-1-2026",
        "question": "Bitcoin above 60k?",
        "outcome": "Yes",
        "category": "crypto_btc_special",
        "signal_cohort": "near_miss_learning|crypto",
        "edge_lower_bound": "0.005",
        "price_action_entry_source": "paper_confirmation_candidate",
        "price_action_evidence_status": "trusted_shadow_requires_broker_paper_confirmation",
    }
    try:
        _insert_order(con, order_id="buy-conflict", created_at="2026-07-01T13:19:07Z", market_id="slug-market", token_id="yes-token", side="BUY_YES", price=0.035, stake=2.0, quantity=57.14285714285714, source_signal=signal)
        _insert_fill(con, fill_id="fill-buy-conflict", order_id="buy-conflict", created_at="2026-07-01T13:19:07Z", market_id="slug-market", token_id="yes-token", side="BUY_YES", price=0.035, quantity=57.14285714285714)
        _insert_snapshot(con, snapshot_id="snap-conflict", collected_at="2026-07-01T13:20:04Z", market_id="condition-market", token_id="yes-token", bid=0.033, ask=0.034)
        _insert_order(con, order_id="sell-conflict", created_at="2026-07-01T13:20:08Z", market_id="slug-market", token_id="yes-token", side="SELL_YES", price=0.966, stake=55.2, quantity=57.14285714285714, source_signal={"side": "SELL_YES", "reason": "take_profit", "quote": {"best_bid": 0.966, "best_ask": 0.967, "spread": 0.001, "timestamp": "2026-07-01T13:20:04Z", "source": "websocket_market_features"}})
        _insert_fill(con, fill_id="fill-sell-conflict", order_id="sell-conflict", created_at="2026-07-01T13:20:08Z", market_id="slug-market", token_id="yes-token", side="SELL_YES", price=0.966, quantity=57.14285714285714)
        con.commit()
    finally:
        con.close()

    summary = build_paper_round_trip_evidence(cfg)
    rows = read_csv_rows(cfg.output_root / "polymarket_price_action" / "paper_broker_round_trip_evidence.csv")

    assert summary["closed_round_trips"] == 1
    assert summary["quote_conflict_round_trips"] == 1
    assert summary["audited_realized_pnl_usdc"] == pytest.approx(0.0)
    assert summary["quote_audit_status_counts"][0]["quote_consistency_status"] == "quote_conflict"
    assert summary["quote_audit_status_counts"][0]["round_trips"] == 1
    quote_audit = summary["quote_audit_by_cohort"][0]
    assert quote_audit["signal_cohort"] == "near_miss_learning|crypto"
    assert quote_audit["quote_conflict_round_trips"] == 1
    assert quote_audit["quote_consistent_round_trips"] == 0
    assert quote_audit["audited_pnl_usdc"] == pytest.approx(0.0)
    assert quote_audit["excluded_from_audit_pnl_usdc"] == pytest.approx(53.2)
    assert "excluded P&L must not train" in quote_audit["recommended_action"]
    assert rows[0]["quote_consistency_status"] == "quote_conflict"
    assert float(rows[0]["exit_quote_snapshot_bid_gap"]) == pytest.approx(0.933)

    model = train_price_action_model(cfg)
    source = model["training_event_sources"]["paper_broker_round_trip"]
    assert source["raw_rows"] == 1
    assert source["prepared_rows"] == 0
    assert source["positive_targets"] == 0
