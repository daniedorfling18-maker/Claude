from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import yaml

from polymarket_predictive_engine.config import load_config
import polymarket_predictive_engine.crypto_updown_settlement as crypto_settlement
from polymarket_predictive_engine.models.calibrated import prediction_confidence
from polymarket_predictive_engine.paper_broker import run_paper_broker
from polymarket_predictive_engine.paper_cycle import run_paper_cycle
from polymarket_predictive_engine.paper_round_trip import build_paper_round_trip_evidence
from polymarket_predictive_engine.storage import connect_db, init_db
from polymarket_predictive_engine.utils import read_csv_rows, write_csv, write_json


def _config(tmp_path: Path):
    raw = yaml.safe_load(
        Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8")
    )
    raw["paths"] = {
        "data_root": str(tmp_path),
        "output_root": str(tmp_path / "outputs"),
        "database_path": str(tmp_path / "work" / "paper.sqlite"),
    }
    raw["governance_thresholds"]["min_paper_labels"] = 1
    raw["mispricing_alpha"]["enabled"] = False
    raw["paper_trading"]["max_exit_quote_age_minutes"] = 10000000
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return load_config(config_path)


def _seed_forward_fixture(cfg) -> None:
    raw_path = (
        cfg.data_root
        / "outputs"
        / "polymarket_fixed"
        / "worldcup"
        / "ml"
        / "raw_market_snapshots.csv"
    )
    write_csv(
        raw_path,
        [
            {
                "snapshot_timestamp": "2026-06-25T10:00:00Z",
                "market_id": "market-forward-1",
                "market_slug": "synthetic-forward-market",
                "token_id": "token-forward-yes",
                "question": "Will the synthetic event happen?",
                "category": "worldcup",
                "best_bid": 0.35,
                "best_ask": 0.40,
                "top_bid_size": 1000,
                "top_ask_size": 1000,
                "bid_depth_1pct": 1000,
                "ask_depth_1pct": 1000,
                "bid_depth_5pct": 1000,
                "ask_depth_5pct": 1000,
                "websocket_quote_age_seconds": 30,
                "midpoint": 0.375,
                "liquidity": 1000,
                "close_time": "2026-12-31T00:00:00Z",
            }
        ],
    )
    training = cfg.output_root / "polymarket_training"
    write_csv(
        training / "labels.csv",
        [
            {
                "market_id": "training-market-1",
                "token_id": "training-token-1",
                "prediction_timestamp": "2026-01-01T00:00:00Z",
                "target": 1,
                "label_source": "clean_settlement",
            }
        ],
    )
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
    mapping = {
        str(bucket): {
            "probability_min": bucket / 10,
            "probability_max": (bucket + 1) / 10,
            "calibrated_probability": 0.90 if bucket == 3 else (bucket + 0.5) / 10,
            "row_count": 10,
        }
        for bucket in range(10)
    }
    write_json(
        cfg.output_root / "polymarket_models" / "calibration_v2.json",
        {
            "model_version": "synthetic-calibrator-v1",
            "feature_set_version": "pm-point-in-time-v2",
            "bucket_count": 10,
            "bucket_mapping": mapping,
            "trained_at": "2026-06-24T00:00:00Z",
        },
    )


def _seed_fast_crypto_fixture(cfg) -> None:
    raw_path = (
        cfg.data_root
        / "outputs"
        / "polymarket_fixed"
        / "crypto"
        / "ml"
        / "raw_market_snapshots.csv"
    )
    write_csv(
        raw_path,
        [
            {
                "snapshot_timestamp": "2026-06-25T10:00:00Z",
                "market_id": "btc-fast-market",
                "market_slug": "btc-updown-5m-1782491400",
                "token_id": "btc-fast-up-token",
                "question": "Bitcoin Up or Down - synthetic 5M",
                "category": "crypto",
                "outcome": "Up",
                "best_bid": 0.35,
                "best_ask": 0.40,
                "top_bid_size": 1000,
                "top_ask_size": 1000,
                "bid_depth_1pct": 1000,
                "ask_depth_1pct": 1000,
                "bid_depth_5pct": 1000,
                "ask_depth_5pct": 1000,
                "websocket_quote_age_seconds": 30,
                "midpoint": 0.375,
                "liquidity": 1000,
                "close_time": "2026-12-31T00:00:00Z",
            }
        ],
    )
    training = cfg.output_root / "polymarket_training"
    write_csv(
        training / "labels.csv",
        [
            {
                "market_id": "training-market-1",
                "token_id": "training-token-1",
                "prediction_timestamp": "2026-01-01T00:00:00Z",
                "target": 1,
                "label_source": "clean_settlement",
            }
        ],
    )
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
    mapping = {
        str(bucket): {
            "probability_min": bucket / 10,
            "probability_max": (bucket + 1) / 10,
            "calibrated_probability": 0.90 if bucket == 3 else (bucket + 0.5) / 10,
            "row_count": 10,
        }
        for bucket in range(10)
    }
    write_json(
        cfg.output_root / "polymarket_models" / "calibration_v2.json",
        {
            "model_version": "synthetic-calibrator-v1",
            "feature_set_version": "pm-point-in-time-v2",
            "bucket_count": 10,
            "bucket_mapping": mapping,
            "trained_at": "2026-06-24T00:00:00Z",
        },
    )


def _seed_open_position(
    cfg,
    *,
    market_id: str,
    token_id: str,
    source_signal: dict,
    quantity: float = 10.0,
    average_entry_price: float = 0.4,
) -> None:
    con = connect_db(cfg.database_path)
    try:
        con.execute(
            """
            INSERT INTO orders(
                order_id, idempotency_key, created_at, updated_at, mode, status,
                market_id, token_id, side, limit_price, stake_usdc, quantity,
                category, event_id, correlation_key, strategy_name, model_version,
                prediction_timestamp, risk_decision_json, source_signal_json
            ) VALUES (?, ?, ?, ?, 'paper', 'filled', ?, ?, 'BUY_YES', ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?)
            """,
            (
                f"order-{token_id}",
                f"order-key-{token_id}",
                "2026-07-02T10:00:00Z",
                "2026-07-02T10:00:00Z",
                market_id,
                token_id,
                average_entry_price,
                quantity * average_entry_price,
                quantity,
                source_signal.get("category", "crypto"),
                source_signal.get("event_id", ""),
                source_signal.get("correlation_key", ""),
                "test",
                "test-model",
                "2026-07-02T10:00:00Z",
                json.dumps(source_signal, sort_keys=True),
            ),
        )
        con.execute(
            """
            INSERT INTO positions(
                position_id, market_id, token_id, side, category, correlation_key,
                quantity, average_entry_price, cost_basis_usdc, realised_pnl_usdc,
                status, updated_at
            ) VALUES (?, ?, ?, 'BUY_YES', ?, ?, ?, ?, ?, 0, 'open', ?)
            """,
            (
                f"position-{token_id}",
                market_id,
                token_id,
                source_signal.get("category", "crypto"),
                source_signal.get("correlation_key", ""),
                quantity,
                average_entry_price,
                quantity * average_entry_price,
                "2026-07-02T10:00:00Z",
            ),
        )
        con.commit()
    finally:
        con.close()


def test_confidence_increases_away_from_half():
    assert prediction_confidence(0.5) == 0
    assert prediction_confidence(0.9) > prediction_confidence(0.7)


def test_existing_payload_tables_are_preserved_during_typed_migration(tmp_path):
    path = tmp_path / "legacy.sqlite"
    con = sqlite3.connect(path)
    try:
        con.execute(
            "CREATE TABLE fills (id INTEGER PRIMARY KEY, created_at TEXT, payload_json TEXT)"
        )
        con.execute(
            "INSERT INTO fills(created_at, payload_json) VALUES ('2026-01-01', '{\"old\":true}')"
        )
        con.commit()
    finally:
        con.close()

    init_db(path)
    con = connect_db(path)
    try:
        assert con.execute("SELECT COUNT(*) FROM fills_legacy_v0").fetchone()[0] == 1
        typed_columns = {
            row[1] for row in con.execute("PRAGMA table_info(fills)").fetchall()
        }
        assert {
            "fill_id",
            "order_id",
            "fill_price",
            "quantity",
            "taker_fee_rate",
            "taker_fee_category",
            "taker_fee_source",
            "taker_fee_model_version",
        }.issubset(typed_columns)
    finally:
        con.close()


def test_forward_paper_cycle_is_persistent_idempotent_and_settles(tmp_path):
    cfg = _config(tmp_path)
    _seed_forward_fixture(cfg)

    first = run_paper_cycle(cfg, source="raw_snapshot")
    assert first["status"] == "ran"
    assert first["predictions"] == 1
    assert first["signals_approved"] == 1
    assert first["broker"]["orders_filled"] == 1

    con = connect_db(cfg.database_path)
    try:
        fill = con.execute(
            "SELECT fill_price, quantity, fee_usdc, taker_fee_rate, taker_fee_category, "
            "taker_fee_source, taker_fee_model_version FROM fills"
        ).fetchone()
        assert fill["taker_fee_rate"] == 0.05
        assert fill["taker_fee_category"] == "sports"
        assert fill["taker_fee_source"] == "documented_category_fallback"
        assert fill["taker_fee_model_version"]
        expected_fee = round(fill["quantity"] * 0.05 * fill["fill_price"] * (1 - fill["fill_price"]), 5)
        assert fill["fee_usdc"] == expected_fee
    finally:
        con.close()

    prediction = read_csv_rows(
        cfg.output_root / "polymarket_predictions" / "predictions.csv"
    )[0]
    assert float(prediction["calibrated_probability"]) == 0.9
    assert float(prediction["calibrated_probability"]) != float(
        prediction["market_midpoint"]
    )

    second = run_paper_cycle(cfg, source="raw_snapshot")
    assert second["broker"]["orders_filled"] == 0
    assert second["broker"]["duplicates_skipped"] == 1

    resolution_path = cfg.output_root / "polymarket_training" / "market_resolutions.csv"
    resolutions = read_csv_rows(resolution_path)
    resolutions.append(
        {
            "condition_id": "market-forward-1",
            "market_slug": "synthetic-forward-market",
            "token_id": "token-forward-yes",
            "target": 1,
            "resolution_quality": "clean_settlement",
        }
    )
    write_csv(resolution_path, resolutions)
    settlement = run_paper_broker(cfg)
    assert settlement["positions_settled"] == 1
    assert settlement["cash"] > 1000

    con = connect_db(cfg.database_path)
    try:
        assert con.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM settlements").fetchone()[0] == 1
        position = con.execute("SELECT status, quantity FROM positions").fetchone()
        assert position["status"] == "settled"
        assert position["quantity"] == 0
    finally:
        con.close()


def test_forward_paper_cycle_forwards_longshot_bias_only_to_shadow(tmp_path):
    cfg = _config(tmp_path)
    _seed_forward_fixture(cfg)
    write_csv(
        cfg.output_root / "polymarket_liquidity_discovery" / "liquidity_watchlist.csv",
        [
            {
                "timestamp": "2026-07-03T08:00:00Z",
                "market_id": "longshot-market",
                "market_slug": "will-longshot-event-happen",
                "question": "Will the longshot event happen?",
                "outcome": "Yes",
                "token_id": "longshot-yes",
                "close_time": "2026-12-31T00:00:00Z",
                "time_to_close_hours": 4000,
                    "best_bid": 0.07,
                    "best_ask": 0.08,
                    "top_bid_size": 1000,
                    "top_ask_size": 1000,
                    "bid_depth_1pct": 1000,
                    "ask_depth_1pct": 1000,
                    "bid_depth_5pct": 1000,
                    "ask_depth_5pct": 1000,
                    "websocket_quote_age_seconds": 30,
                    "midpoint": 0.075,
                    "spread": 0.01,
                "liquidity": 1200,
            },
            {
                "timestamp": "2026-07-03T08:00:00Z",
                "market_id": "longshot-market",
                "market_slug": "will-longshot-event-happen",
                "question": "Will the longshot event happen?",
                "outcome": "No",
                "token_id": "longshot-no",
                "close_time": "2026-12-31T00:00:00Z",
                "time_to_close_hours": 4000,
                    "best_bid": 0.88,
                    "best_ask": 0.89,
                    "top_bid_size": 1000,
                    "top_ask_size": 1000,
                    "bid_depth_1pct": 1000,
                    "ask_depth_1pct": 1000,
                    "bid_depth_5pct": 1000,
                    "ask_depth_5pct": 1000,
                    "websocket_quote_age_seconds": 30,
                    "midpoint": 0.885,
                "spread": 0.01,
                "liquidity": 1200,
            },
        ],
    )

    result = run_paper_cycle(cfg, source="raw_snapshot")

    assert result["signals_approved"] == 1
    assert result["longshot_bias"]["candidates"] == 1
    assert result["longshot_bias"]["shadow_candidates_forwarded"] == 1
    shadow_positions = read_csv_rows(cfg.output_root / "polymarket_shadow" / "shadow_positions.csv")
    assert len(shadow_positions) == 1
    assert shadow_positions[0]["token_id"] == "longshot-no"
    assert shadow_positions[0]["shadow_source"] == "longshot_bias"


def test_paper_broker_proxy_settles_fast_crypto_updown_position(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    cfg.raw["paper_trading"]["settle_crypto_updown_with_public_price"] = True
    cfg.raw["paper_trading"]["settlement_request_timeout_seconds"] = 1
    _seed_fast_crypto_fixture(cfg)

    first = run_paper_cycle(cfg, source="raw_snapshot")
    assert first["broker"]["orders_filled"] == 1

    import polymarket_predictive_engine.paper_broker as broker_module

    monkeypatch.setattr(
        broker_module,
        "_crypto_updown_proxy_settlement_price",
        lambda position, *, timeout_seconds: (
            1.0,
            "crypto_updown_proxy_settlement:test_public_price:up:100->101",
        ),
    )

    settlement = run_paper_broker(cfg)

    assert settlement["positions_settled"] == 1
    assert settlement["cash"] > 1000
    assert "crypto_updown_proxy_settlement" in settlement["settlements"][0]["resolution_source"]

    con = connect_db(cfg.database_path)
    try:
        position = con.execute("SELECT status, quantity, realised_pnl_usdc FROM positions").fetchone()
        assert position["status"] == "settled"
        assert position["quantity"] == 0
        assert position["realised_pnl_usdc"] > 0
        resolution_source = con.execute("SELECT resolution_source FROM settlements").fetchone()[0]
        assert "test_public_price" in resolution_source
    finally:
        con.close()


def test_paper_broker_proxy_settles_hourly_crypto_updown_slug(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    cfg.raw["paper_trading"]["settle_crypto_updown_with_public_price"] = True
    cfg.raw["paper_trading"]["settlement_request_timeout_seconds"] = 1
    _seed_open_position(
        cfg,
        market_id="eth-hourly-market",
        token_id="eth-hourly-up-token",
        source_signal={
            "market_id": "eth-hourly-market",
            "market_slug": "ethereum-up-or-down-july-2-2026-12pm-et",
            "token_id": "eth-hourly-up-token",
            "question": "Ethereum Up or Down - July 2, 2026 12PM ET",
            "outcome": "Up",
            "category": "crypto",
        },
    )

    def fake_binance(asset_key, *, start_utc, interval_minutes, timeout_seconds):
        assert asset_key == "eth"
        assert interval_minutes == 60
        assert start_utc.strftime("%Y-%m-%dT%H:%M:%SZ") == "2026-07-02T15:00:00Z"
        return 100.0, 101.0, "test_hourly_window"

    monkeypatch.setattr(crypto_settlement, "_fetch_binance_window_prices", fake_binance)

    settlement = run_paper_broker(cfg)

    assert settlement["positions_settled"] == 1
    assert "test_hourly_window" in settlement["settlements"][0]["resolution_source"]
    assert settlement["stale_open_position_count"] == 0

    con = connect_db(cfg.database_path)
    try:
        position = con.execute("SELECT status, quantity, realised_pnl_usdc FROM positions").fetchone()
        assert position["status"] == "settled"
        assert position["quantity"] == 0
        assert position["realised_pnl_usdc"] > 0
    finally:
        con.close()


def test_paper_broker_flags_stale_open_position_without_changing_equity(tmp_path):
    cfg = _config(tmp_path)
    cfg.raw["paper_trading"]["settle_crypto_updown_with_public_price"] = False
    cfg.raw["paper_trading"]["stale_open_alert_hours"] = 2
    _seed_open_position(
        cfg,
        market_id="eth-stale-market",
        token_id="eth-stale-up-token",
        source_signal={
            "market_id": "eth-stale-market",
            "market_slug": "ethereum-up-or-down-july-2-2026-12pm-et",
            "token_id": "eth-stale-up-token",
            "question": "Ethereum Up or Down - July 2, 2026 12PM ET",
            "outcome": "Up",
            "category": "crypto",
        },
        quantity=10,
        average_entry_price=0.4,
    )

    result = run_paper_broker(cfg)

    assert result["positions_settled"] == 0
    assert result["stale_open_position_count"] == 1
    stale = result["stale_open_positions"][0]
    assert stale["alert"] == "stale_open_position"
    assert stale["close_time"] == "2026-07-02T16:00:00Z"
    assert stale["quote_state"] == "missing"

    con = connect_db(cfg.database_path)
    try:
        position = con.execute("SELECT status, quantity, cost_basis_usdc, realised_pnl_usdc FROM positions").fetchone()
        assert position["status"] == "open"
        assert position["quantity"] == 10
        assert position["cost_basis_usdc"] == 4
        assert position["realised_pnl_usdc"] == 0
        assert con.execute("SELECT COUNT(*) FROM settlements").fetchone()[0] == 0
    finally:
        con.close()


def test_paper_broker_closes_profitable_position_and_respects_exit_cooldown(tmp_path):
    cfg = _config(tmp_path)
    cfg.raw["paper_trading"]["minimum_hold_minutes_before_exit"] = 0
    cfg.raw["paper_trading"]["take_profit_return"] = 0.1
    cfg.raw["paper_trading"]["take_profit_min_usdc"] = 0.01
    cfg.raw["paper_trading"]["minimum_reentry_minutes_after_exit"] = 240
    _seed_forward_fixture(cfg)

    first = run_paper_cycle(cfg, source="raw_snapshot")
    assert first["broker"]["orders_filled"] == 1

    con = connect_db(cfg.database_path)
    try:
        con.execute(
            """
            INSERT INTO market_snapshots(
                snapshot_id, idempotency_key, collected_at, market_id, token_id,
                best_bid, best_ask, midpoint, spread, liquidity, raw_payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "snapshot_profitable_exit",
                "profitable-exit|market-forward-1|token-forward-yes",
                "2026-06-25T10:05:00Z",
                "market-forward-1",
                "token-forward-yes",
                0.56,
                0.57,
                0.565,
                0.01,
                1000,
                "{}",
            ),
        )
        con.commit()
    finally:
        con.close()

    signals_path = cfg.output_root / "polymarket_predictions" / "trade_signals.csv"
    signals = read_csv_rows(signals_path)
    signals[0]["data_snapshot_timestamp"] = "2026-06-25T10:06:00Z"
    write_csv(signals_path, signals)

    result = run_paper_broker(cfg)
    assert result["exit_orders_filled"] == 1
    assert result["orders_filled"] == 0
    assert result["broker_rejection_reasons"]["recent exit cooldown active"] == 1
    assert result["closed_positions"][0]["realised_pnl_usdc"] > 0

    con = connect_db(cfg.database_path)
    try:
        position = con.execute("SELECT status, quantity, realised_pnl_usdc FROM positions").fetchone()
        assert position["status"] == "closed"
        assert position["quantity"] == 0
        assert position["realised_pnl_usdc"] > 0
        sell_count = con.execute("SELECT COUNT(*) FROM fills WHERE side = 'SELL_YES'").fetchone()[0]
        assert sell_count == 1
    finally:
        con.close()


def test_paper_broker_captures_execution_quotes_for_round_trip_proof(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    cfg.raw["paper_trading"]["minimum_hold_minutes_before_exit"] = 0
    cfg.raw["paper_trading"]["take_profit_return"] = 0.1
    cfg.raw["paper_trading"]["take_profit_min_usdc"] = 0.01
    signals_path = cfg.output_root / "polymarket_predictions" / "trade_signals.csv"
    write_csv(
        signals_path,
        [
            {
                "market_id": "quote-proof-market",
                "market_slug": "quote-proof-market",
                "token_id": "quote-proof-token",
                "question": "Will quote proof work?",
                "outcome": "Yes",
                "side": "BUY_YES",
                "category": "quote_proof_test",
                "edge": "0.08",
                "model_probability": "0.70",
                "confidence": "0.75",
                "executable_price": "0.40",
                "best_bid": "0.39",
                "best_ask": "0.40",
                "spread": "0.01",
                "liquidity": "1000",
                "top_ask_size": "1000",
                "top_bid_size": "1000",
                "ask_depth_1pct": "1000",
                "bid_depth_1pct": "1000",
                "ask_depth_5pct": "1000",
                "bid_depth_5pct": "1000",
                "websocket_quote_age_seconds": "1",
                "price_action_signal": "true",
                "price_action_latest_bid": "0.39",
                "price_action_latest_ask": "0.40",
                "price_action_spread": "0.01",
                "take_profit_return": "0.10",
                "take_profit_min_usdc": "0.01",
                "strategy_name": "price_action_round_trip",
                "model_version": "quote-proof-test-v1",
            }
        ],
    )

    import polymarket_predictive_engine.paper_broker as broker_module

    monkeypatch.setattr(
        broker_module,
        "paper_trade_readiness",
        lambda cfg: {
            "approved_for_paper_trading": True,
            "blockers": [],
        },
    )

    first = run_paper_broker(cfg)
    assert first["orders_filled"] == 1

    con = connect_db(cfg.database_path)
    try:
        entry_snapshot = con.execute(
            """
            SELECT best_bid, best_ask, raw_payload_json
            FROM market_snapshots
            WHERE token_id = 'quote-proof-token'
            """
        ).fetchone()
        assert entry_snapshot is not None
        assert entry_snapshot["best_bid"] == 0.39
        assert entry_snapshot["best_ask"] == 0.40
        payload = json.loads(entry_snapshot["raw_payload_json"])
        assert payload["source"] == "paper_execution_quote_snapshot"
        assert payload["quote_source"] == "paper_entry_signal_bid_ask"
    finally:
        con.close()

    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            {
                "asset_id": "quote-proof-token",
                "market_slug": "quote-proof-market",
                "selection": "Yes",
                "best_bid": "0.55",
                "best_ask": "0.56",
                "midpoint": "0.555",
                "spread": "0.01",
            }
        ],
    )

    second = run_paper_broker(cfg)
    assert second["exit_orders_filled"] == 1

    summary = build_paper_round_trip_evidence(cfg)
    rows = read_csv_rows(cfg.output_root / "polymarket_price_action" / "paper_broker_round_trip_evidence.csv")

    assert summary["proof_verified_closed_round_trips"] == 1
    assert summary["proof_verified_realized_pnl_usdc"] > 0
    assert summary["proof_entry_snapshot_missing_round_trips"] == 0
    assert summary["proof_exit_snapshot_missing_round_trips"] == 0
    assert int(rows[0]["entry_snapshot_observations"]) >= 1
    assert int(rows[0]["exit_snapshot_observations"]) >= 1
    assert rows[0]["quote_proof_status"] == "proof_verified"
    assert float(rows[0]["entry_fill_snapshot_ask_gap"]) == 0.0
    assert float(rows[0]["exit_fill_nearest_snapshot_bid_gap"]) == 0.0


def test_paper_broker_rejects_entry_without_quote_snapshot_support(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    signals_path = cfg.output_root / "polymarket_predictions" / "trade_signals.csv"
    write_csv(
        signals_path,
        [
            {
                "market_id": "missing-entry-quote-market",
                "market_slug": "missing-entry-quote-market",
                "token_id": "missing-entry-quote-token",
                "question": "Will missing entry quotes be rejected?",
                "outcome": "Yes",
                "side": "BUY_YES",
                "category": "quote_proof_test",
                "edge": "0.08",
                "model_probability": "0.70",
                "confidence": "0.75",
                "executable_price": "0.40",
                "best_ask": "0.40",
                "spread": "0.01",
                "liquidity": "1000",
                "top_ask_size": "1000",
                "top_bid_size": "1000",
                "ask_depth_1pct": "1000",
                "bid_depth_1pct": "1000",
                "ask_depth_5pct": "1000",
                "bid_depth_5pct": "1000",
                "websocket_quote_age_seconds": "1",
                "price_action_signal": "true",
                "take_profit_return": "0.10",
                "take_profit_min_usdc": "0.01",
                "strategy_name": "price_action_round_trip",
                "model_version": "quote-proof-test-v1",
            }
        ],
    )

    import polymarket_predictive_engine.paper_broker as broker_module

    monkeypatch.setattr(
        broker_module,
        "paper_trade_readiness",
        lambda cfg: {
            "approved_for_paper_trading": True,
            "blockers": [],
        },
    )

    result = run_paper_broker(cfg)

    assert result["orders_filled"] == 0
    assert result["broker_rejection_reasons"]["missing sane entry bid/ask for proof snapshot"] == 1
    con = connect_db(cfg.database_path)
    try:
        assert con.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0] == 0
    finally:
        con.close()


def test_paper_broker_refuses_wrong_side_websocket_exit_quote(tmp_path):
    cfg = _config(tmp_path)
    cfg.raw["paper_trading"]["minimum_hold_minutes_before_exit"] = 0
    cfg.raw["paper_trading"]["take_profit_return"] = 0.1
    cfg.raw["paper_trading"]["take_profit_min_usdc"] = 0.01
    cfg.raw["paper_trading"]["require_exit_snapshot_crosscheck"] = True
    cfg.raw["paper_trading"]["exit_quote_snapshot_tolerance"] = 0.05
    _seed_forward_fixture(cfg)

    first = run_paper_cycle(cfg, source="raw_snapshot")
    assert first["broker"]["orders_filled"] == 1

    con = connect_db(cfg.database_path)
    try:
        con.execute(
            """
            INSERT INTO market_snapshots(
                snapshot_id, idempotency_key, collected_at, market_id, token_id,
                best_bid, best_ask, midpoint, spread, liquidity, raw_payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "snapshot_true_same_token_bid",
                "true-same-token-bid|market-forward-1|token-forward-yes",
                "2026-06-25T10:05:00Z",
                "market-forward-1",
                "token-forward-yes",
                0.41,
                0.42,
                0.415,
                0.01,
                1000,
                "{}",
            ),
        )
        con.commit()
    finally:
        con.close()

    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            {
                "collected_at_utc": "2026-06-25T10:06:00Z",
                "asset_id": "token-forward-yes",
                "market_slug": "market-forward-1",
                "selection": "Yes",
                "best_bid": "0.90",
                "best_ask": "0.91",
                "midpoint": "0.905",
                "spread": "0.01",
            }
        ],
    )

    result = run_paper_broker(cfg)

    assert result["exit_orders_filled"] == 0
    assert result["exit_rejection_reasons"]["exit quote conflicts with latest snapshot bid: gap 0.490 > 0.050"] == 1

    con = connect_db(cfg.database_path)
    try:
        position = con.execute("SELECT status, quantity FROM positions").fetchone()
        assert position["status"] == "open"
        assert position["quantity"] > 0
        sell_count = con.execute("SELECT COUNT(*) FROM fills WHERE side = 'SELL_YES'").fetchone()[0]
        assert sell_count == 0
    finally:
        con.close()


def test_paper_broker_refuses_stale_exit_quote(tmp_path):
    cfg = _config(tmp_path)
    cfg.raw["paper_trading"]["minimum_hold_minutes_before_exit"] = 0
    cfg.raw["paper_trading"]["take_profit_return"] = 0.1
    cfg.raw["paper_trading"]["take_profit_min_usdc"] = 0.01
    cfg.raw["paper_trading"]["max_exit_quote_age_minutes"] = 1
    _seed_forward_fixture(cfg)

    first = run_paper_cycle(cfg, source="raw_snapshot")
    assert first["broker"]["orders_filled"] == 1

    con = connect_db(cfg.database_path)
    try:
        con.execute(
            """
            INSERT INTO market_snapshots(
                snapshot_id, idempotency_key, collected_at, market_id, token_id,
                best_bid, best_ask, midpoint, spread, liquidity, raw_payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "snapshot_stale_profitable_exit",
                "stale-profitable-exit|market-forward-1|token-forward-yes",
                "2026-06-25T10:05:00Z",
                "market-forward-1",
                "token-forward-yes",
                0.56,
                0.57,
                0.565,
                0.01,
                1000,
                "{}",
            ),
        )
        con.commit()
    finally:
        con.close()

    result = run_paper_broker(cfg)

    assert result["exit_orders_filled"] == 0
    assert list(result["exit_rejection_reasons"])[0].startswith("stale exit quote:")

    con = connect_db(cfg.database_path)
    try:
        position = con.execute("SELECT status, quantity FROM positions").fetchone()
        assert position["status"] == "open"
        assert position["quantity"] > 0
        sell_count = con.execute("SELECT COUNT(*) FROM fills WHERE side = 'SELL_YES'").fetchone()[0]
        assert sell_count == 0
    finally:
        con.close()


def test_paper_broker_monitors_exits_when_readiness_blocks_new_entries(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    cfg.raw["paper_trading"]["minimum_hold_minutes_before_exit"] = 0
    cfg.raw["paper_trading"]["take_profit_return"] = 0.1
    cfg.raw["paper_trading"]["take_profit_min_usdc"] = 0.01
    _seed_forward_fixture(cfg)

    first = run_paper_cycle(cfg, source="raw_snapshot")
    assert first["broker"]["orders_filled"] == 1

    con = connect_db(cfg.database_path)
    try:
        con.execute(
            """
            INSERT INTO market_snapshots(
                snapshot_id, idempotency_key, collected_at, market_id, token_id,
                best_bid, best_ask, midpoint, spread, liquidity, raw_payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "snapshot_blocked_gate_profitable_exit",
                "blocked-gate-profitable-exit|market-forward-1|token-forward-yes",
                "2026-06-25T10:05:00Z",
                "market-forward-1",
                "token-forward-yes",
                0.56,
                0.57,
                0.565,
                0.01,
                1000,
                "{}",
            ),
        )
        con.commit()
    finally:
        con.close()

    signals_path = cfg.output_root / "polymarket_predictions" / "trade_signals.csv"
    signals = read_csv_rows(signals_path)
    signals[0]["data_snapshot_timestamp"] = "2026-06-25T10:06:00Z"
    write_csv(signals_path, signals)

    import polymarket_predictive_engine.paper_broker as broker_module

    monkeypatch.setattr(
        broker_module,
        "paper_trade_readiness",
        lambda cfg: {
            "approved_for_paper_trading": False,
            "blockers": ["test readiness blocker"],
        },
    )

    result = run_paper_broker(cfg)

    assert result["status"] == "monitoring_exits_only_readiness_gate_blocked"
    assert result["approved_for_paper_trading"] is False
    assert result["exit_monitoring_when_blocked"] is True
    assert result["exit_orders_filled"] == 1
    assert result["orders_filled"] == 0
    assert result["signals_processed"] == 0
    assert result["closed_positions"][0]["reason"] == "take_profit"

    con = connect_db(cfg.database_path)
    try:
        position = con.execute("SELECT status, quantity, realised_pnl_usdc FROM positions").fetchone()
        assert position["status"] == "closed"
        assert position["quantity"] == 0
        assert position["realised_pnl_usdc"] > 0
        buy_count = con.execute("SELECT COUNT(*) FROM fills WHERE side = 'BUY_YES'").fetchone()[0]
        sell_count = con.execute("SELECT COUNT(*) FROM fills WHERE side = 'SELL_YES'").fetchone()[0]
        assert buy_count == 1
        assert sell_count == 1
    finally:
        con.close()


def test_paper_broker_pauses_new_entries_when_clean_forward_pnl_is_below_threshold(tmp_path):
    cfg = _config(tmp_path)
    cfg.raw["profit_tracking"]["pause_new_entries_below_pnl_usdc"] = -1
    _seed_forward_fixture(cfg)

    first = run_paper_cycle(cfg, source="raw_snapshot")
    assert first["broker"]["orders_filled"] == 1

    write_json(
        cfg.governance_root / "paper_profit_target_baseline.json",
        {
            "created_at_utc": "2026-06-25T00:00:00Z",
            "baseline_equity_usdc": 2000,
            "baseline_cash_usdc": 2000,
            "baseline_total_exposure_usdc": 0,
        },
    )
    signals_path = cfg.output_root / "polymarket_predictions" / "trade_signals.csv"
    signals = read_csv_rows(signals_path)
    signals[0]["data_snapshot_timestamp"] = "2026-06-25T10:06:00Z"
    write_csv(signals_path, signals)

    result = run_paper_broker(cfg)
    assert result["orders_filled"] == 0
    assert result["signals_processed"] == 0
    assert "pause threshold" in result["entry_pause_reason"]


def test_export_pins_enrolled_ledger_columns_and_versions_taker_fees(tmp_path):
    # WO-110: exports backing WO-61 append_only-enrolled ledgers may never use
    # SELECT * (a table schema change would rewrite anchored bytes - the
    # 2026-07-19 paper_fills.csv chain break). Full-schema rows go to the
    # versioned v2 export instead.
    from polymarket_predictive_engine import paper_broker

    for name in ("paper_fills.csv", "cash_ledger.csv", "settlements.csv", "portfolio_snapshots.csv"):
        assert "SELECT *" not in paper_broker.EXPORT_QUERIES[name], name
    assert paper_broker.EXPORT_QUERIES["paper_fills.csv"] == (
        "SELECT fill_id, order_id, idempotency_key, created_at, market_id, "
        "token_id, side, fill_price, quantity, gross_notional_usdc, fee_usdc, "
        "slippage_usdc, fill_model FROM fills ORDER BY created_at, fill_id"
    )
    assert paper_broker.EXPORT_QUERIES["paper_fills_v2.csv"] == "SELECT * FROM fills ORDER BY created_at, fill_id"

    cfg = _config(tmp_path)
    init_db(cfg.database_path)
    con = connect_db(cfg.database_path)
    con.execute("PRAGMA foreign_keys = OFF")  # export projection test only
    with con:
        con.execute(
            "INSERT INTO fills (fill_id, order_id, idempotency_key, created_at, market_id, "
            "token_id, side, fill_price, quantity, gross_notional_usdc, taker_fee_rate, taker_fee_category) "
            "VALUES ('f1','o1','k1','2026-07-19T00:00:00Z','m1','t1','BUY',0.5,1.0,0.5,0.02,'sports')"
        )
    paper_broker.export_ledger(con, cfg)
    out = cfg.output_root / "polymarket_portfolio"
    legacy_header = (out / "paper_fills.csv").read_text(encoding="utf-8").splitlines()[0]
    assert legacy_header == (
        "fill_id,order_id,idempotency_key,created_at,market_id,token_id,side,"
        "fill_price,quantity,gross_notional_usdc,fee_usdc,slippage_usdc,fill_model"
    )
    v2_rows = read_csv_rows(out / "paper_fills_v2.csv")
    assert v2_rows[0]["taker_fee_rate"] == "0.02"
    assert v2_rows[0]["taker_fee_category"] == "sports"
    assert "taker_fee_rate" not in read_csv_rows(out / "paper_fills.csv")[0]
