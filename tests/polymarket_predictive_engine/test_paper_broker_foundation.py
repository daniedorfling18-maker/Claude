from __future__ import annotations

import sqlite3
from pathlib import Path

import yaml

from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.models.calibrated import prediction_confidence
from polymarket_predictive_engine.paper_broker import run_paper_broker
from polymarket_predictive_engine.paper_cycle import run_paper_cycle
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
        assert {"fill_id", "order_id", "fill_price", "quantity"}.issubset(typed_columns)
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
