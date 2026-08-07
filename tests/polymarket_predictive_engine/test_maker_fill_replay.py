"""WO-40 maker fill replay: last-in-queue realism for maker-carry."""
from __future__ import annotations

import csv
import gzip
import json
import os
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from polymarket_predictive_engine import maker_fill_replay
from polymarket_predictive_engine import trade_print_collector
from polymarket_predictive_engine.cli import COMMANDS
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.maker_fill_replay import run_maker_fill_replay
from polymarket_predictive_engine.utils import (
    append_csv_rows,
    csv_columns,
    parse_timestamp,
    read_csv_rows,
    read_json,
    write_csv,
    write_json,
)


def _config(tmp_path: Path):
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    raw["maker_fill_replay"] = {"enabled": True, "max_markets": 10, "replay_days": 7, "book_source": "archive"}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def _write_gzip_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _seed_maker_portfolio(cfg) -> None:
    out = cfg.output_root / "maker_carry"
    write_json(
        out / "maker_carry_study.json",
        {
            "generated_at_utc": "2026-07-10T00:00:00Z",
            "portfolio": [
                {
                    "question": "Synthetic maker market",
                    "condition_id": "0xcond",
                    "size_multiple": 1,
                    "quote_size_shares": 10,
                    "quote_distance": 0.01,
                    "quote_bid_price": 0.49,
                    "quote_ask_price": 0.51,
                    "net_carry_usd_per_day": 5.0,
                }
            ],
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )
    write_csv(
        out / "maker_carry_candidates.csv",
        [
            {
                "condition_id": "0xcond",
                "token_id": "tok1",
                "adverse_selection_usd_per_day": 2.0,
            }
        ],
        fieldnames=["condition_id", "token_id", "adverse_selection_usd_per_day"],
    )


def _seed_archive(cfg) -> None:
    fields = [
        "source_timestamp",
        "asset_id",
        "best_bid",
        "best_ask",
        "midpoint",
        "top_bid_size",
        "top_ask_size",
        "resting_bid_depth_at_quote",
        "resting_ask_depth_at_quote",
    ]
    _write_gzip_csv(
        cfg.output_root / "polymarket_training_archive" / "features_synthetic.csv.gz",
        [
            {"source_timestamp": 1_000, "asset_id": "tok1", "best_bid": 0.49, "best_ask": 0.51, "midpoint": 0.50, "top_bid_size": 20, "top_ask_size": 20, "resting_bid_depth_at_quote": 20, "resting_ask_depth_at_quote": 20},
            {"source_timestamp": 1_300, "asset_id": "tok1", "best_bid": 0.43, "best_ask": 0.47, "midpoint": 0.45, "top_bid_size": 20, "top_ask_size": 20, "resting_bid_depth_at_quote": 20, "resting_ask_depth_at_quote": 20},
            {"source_timestamp": 1_900, "asset_id": "tok1", "best_bid": 0.42, "best_ask": 0.46, "midpoint": 0.44, "top_bid_size": 20, "top_ask_size": 20, "resting_bid_depth_at_quote": 20, "resting_ask_depth_at_quote": 20},
            {"source_timestamp": 4_600, "asset_id": "tok1", "best_bid": 0.38, "best_ask": 0.42, "midpoint": 0.40, "top_bid_size": 20, "top_ask_size": 20, "resting_bid_depth_at_quote": 20, "resting_ask_depth_at_quote": 20},
        ],
        fields,
    )


def _seed_official_books(cfg) -> None:
    _write_gzip_csv(
        cfg.output_root / "maker_carry" / "official_books" / "0xcond.csv.gz",
        [
            {"condition_id": "0xcond", "source_timestamp": 1_000, "asset_id": "tok1", "best_bid": 0.49, "best_ask": 0.51, "midpoint": 0.50, "top_bid_size": 20, "top_ask_size": 20, "bids_json": '[{"price":0.49,"size":20}]', "asks_json": '[{"price":0.51,"size":20}]', "hash": "h1"},
            {"condition_id": "0xcond", "source_timestamp": 1_300, "asset_id": "tok1", "best_bid": 0.43, "best_ask": 0.47, "midpoint": 0.45, "top_bid_size": 20, "top_ask_size": 20, "bids_json": '[{"price":0.43,"size":20}]', "asks_json": '[{"price":0.47,"size":20}]', "hash": "h2"},
            {"condition_id": "0xcond", "source_timestamp": 1_900, "asset_id": "tok1", "best_bid": 0.42, "best_ask": 0.46, "midpoint": 0.44, "top_bid_size": 20, "top_ask_size": 20, "bids_json": '[{"price":0.42,"size":20}]', "asks_json": '[{"price":0.46,"size":20}]', "hash": "h3"},
            {"condition_id": "0xcond", "source_timestamp": 4_600, "asset_id": "tok1", "best_bid": 0.38, "best_ask": 0.42, "midpoint": 0.40, "top_bid_size": 20, "top_ask_size": 20, "bids_json": '[{"price":0.38,"size":20}]', "asks_json": '[{"price":0.42,"size":20}]', "hash": "h4"},
        ],
        maker_fill_replay.OFFICIAL_BOOK_FIELDS,
    )


def test_replay_loaders_stream_and_filter_before_materialising(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    _seed_archive(cfg)
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [
            {"market": "0xcond", "asset_id": "tok1", "side": "SELL", "price": 0.49, "size": 25, "timestamp": 1_000},
            {"market": "other", "asset_id": "noise", "side": "BUY", "price": 0.10, "size": 1_000, "timestamp": 1_001},
        ],
        fieldnames=["market", "asset_id", "side", "price", "size", "timestamp"],
    )

    def fail_materialisation(_path):
        raise AssertionError("replay must not materialise complete source files")

    monkeypatch.setattr(maker_fill_replay, "_read_csv_any", fail_materialisation)

    states = maker_fill_replay._book_states(cfg, {"tok1"}, replay_days=7)
    trades = maker_fill_replay._trades(cfg, {"0xcond"}, {"tok1"})

    assert set(states) == {"tok1"}
    assert len(states["tok1"]) == 4
    assert [(trade["market"], trade["token_id"]) for trade in trades] == [("0xcond", "tok1")]


def test_book_state_stream_keeps_latest_timestamp_per_token_minute():
    rows = iter(
        [
            {"source_timestamp": 119, "asset_id": "tok1", "best_bid": 0.48, "best_ask": 0.52},
            {"source_timestamp": 61, "asset_id": "tok1", "best_bid": 0.47, "best_ask": 0.53},
            {"source_timestamp": 120, "asset_id": "tok1", "best_bid": 0.46, "best_ask": 0.54},
            {"source_timestamp": 121, "asset_id": "noise", "best_bid": 0.10, "best_ask": 0.20},
        ]
    )

    states = maker_fill_replay._book_states_from_rows(rows, {"tok1"}, replay_days=7)

    assert [state["stamp"] for state in states["tok1"]] == [119.0, 120.0]
    assert states["tok1"][0]["best_bid"] == 0.48


def test_replay_uses_all_price_levels_ahead_of_the_quote():
    rows = [
        {
            "source_timestamp": 1_000,
            "asset_id": "tok1",
            "best_bid": 0.50,
            "best_ask": 0.52,
            "midpoint": 0.51,
            "top_bid_size": 5,
            "bids_json": '[{"price":0.50,"size":5},{"price":0.49,"size":15},{"price":0.48,"size":100}]',
            "asks_json": '[{"price":0.52,"size":20}]',
        },
        {"source_timestamp": 1_300, "asset_id": "tok1", "best_bid": 0.44, "best_ask": 0.46, "midpoint": 0.45, "bids_json": "[]", "asks_json": "[]"},
        {"source_timestamp": 1_900, "asset_id": "tok1", "best_bid": 0.43, "best_ask": 0.45, "midpoint": 0.44, "bids_json": "[]", "asks_json": "[]"},
        {"source_timestamp": 4_600, "asset_id": "tok1", "best_bid": 0.39, "best_ask": 0.41, "midpoint": 0.40, "bids_json": "[]", "asks_json": "[]"},
    ]
    states = maker_fill_replay._book_states_from_rows(iter(rows), {"tok1"}, 7)

    result = maker_fill_replay._replay_against_states(
        source="official",
        states_by_token=states,
        trades=[{"market": "0xcond", "token_id": "tok1", "side": "SELL", "price": 0.49, "size": 25.0, "stamp": 1_000.0}],
        portfolio=[{"condition_id": "0xcond", "token_id": "tok1", "question": "exact levels", "quote_size_shares": 10.0, "quote_distance": 0.01, "quote_bid_price": 0.49, "quote_ask_price": 0.53}],
        study_charge=2.0,
        study_charge_by_condition={"0xcond": 2.0},
        max_state_lag_seconds=1800,
    )

    assert result["last_in_queue_evaluable_opportunities"] == 1
    assert result["fills_preview"][0]["depth_ahead"] == 20.0
    assert result["fills_preview"][0]["fill_size"] == 5.0
    assert result["fills_preview"][0]["queue_depth_source"] == "full_book_levels"


def test_replay_does_not_substitute_top_size_for_missing_level_history():
    states = maker_fill_replay._book_states_from_rows(
        iter([{"source_timestamp": 1_000, "asset_id": "tok1", "best_bid": 0.49, "best_ask": 0.51, "midpoint": 0.50, "top_bid_size": 20, "top_ask_size": 20}]),
        {"tok1"},
        7,
    )

    result = maker_fill_replay._replay_against_states(
        source="archive",
        states_by_token=states,
        trades=[{"market": "0xcond", "token_id": "tok1", "side": "SELL", "price": 0.49, "size": 25.0, "stamp": 1_000.0}],
        portfolio=[{"condition_id": "0xcond", "token_id": "tok1", "question": "missing levels", "quote_size_shares": 10.0, "quote_distance": 0.01, "quote_bid_price": 0.49, "quote_ask_price": 0.51}],
        study_charge=2.0,
        study_charge_by_condition={"0xcond": 2.0},
        max_state_lag_seconds=1800,
    )

    assert result["simulated_fill_opportunities"] == 1
    assert result["last_in_queue_evaluable_opportunities"] == 0
    assert result["queue_depth_unavailable_opportunities"] == 1
    assert result["confirmed_fills"] == 0


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_last_in_queue_blocks_fill_when_depth_ahead_absorbs_print(tmp_path):
    cfg = _config(tmp_path)
    _seed_maker_portfolio(cfg)
    _seed_archive(cfg)
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [{"market": "0xcond", "asset_id": "tok1", "side": "SELL", "price": 0.49, "size": 20, "timestamp": 1_000}],
        fieldnames=["market", "asset_id", "side", "price", "size", "timestamp"],
    )

    summary = run_maker_fill_replay(cfg)

    assert summary["status"] == "ok"
    assert summary["simulated_fills"] == 0
    assert summary["simulated_fill_opportunities"] == 1
    assert summary["confirmed_fill_ratio"] == 0.0
    assert summary["coverage"]["windows_covered"] == 3
    assert summary["simulated_fills_per_day"] == 0.0
    assert summary["implied_adverse_usd_per_day"] == 0.0
    assert summary["paper_trading_invoked"] is False
    assert summary["live_trading_invoked"] is False


def test_fill_when_trade_volume_exceeds_depth_ahead_and_markouts_are_reported(tmp_path):
    cfg = _config(tmp_path)
    _seed_maker_portfolio(cfg)
    _seed_archive(cfg)
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [{"market": "0xcond", "asset_id": "tok1", "side": "SELL", "price": 0.49, "size": 25, "timestamp": 1_000}],
        fieldnames=["market", "asset_id", "side", "price", "size", "timestamp"],
    )

    summary = run_maker_fill_replay(cfg)

    assert summary["status"] == "ok"
    assert summary["simulated_fills"] == 1
    assert summary["simulated_fills_per_day"] == 24.0
    assert summary["markout_per_fill"]["5m"] == 0.04
    assert summary["markout_per_fill"]["15m"] == 0.05
    assert summary["markout_per_fill"]["60m"] == 0.09
    assert summary["implied_adverse_usd_per_day"] == 4.8
    assert summary["study_adverse_usd_per_day"] == 2.0
    assert summary["realism_ratio"] == 2.4
    assert summary["simulation_to_reality_haircut"] == 2.4
    assert summary["confirmed_fill_ratio"] == 1.0
    assert summary["portfolio_generated_at_utc"] == "2026-07-10T00:00:00Z"
    assert summary["coverage"] == {
        "windows_simulated": 3,
        "windows_covered": 3,
        "coverage_ratio": 1.0,
        "by_horizon": {
            "5m": {"windows_simulated": 1, "windows_covered": 1, "coverage_ratio": 1.0},
            "15m": {"windows_simulated": 1, "windows_covered": 1, "coverage_ratio": 1.0},
            "60m": {"windows_simulated": 1, "windows_covered": 1, "coverage_ratio": 1.0},
        },
    }
    assert summary["realized_markout_distribution"]["5m"]["median"] == 0.04
    assert summary["regime_cut"]["last_7_days"]["confirmed_fills"] == 1
    assert summary["regime_cut"]["prior_to_last_7_days"]["confirmed_fills"] == 0
    assert summary["fills_preview"][0]["fill_size"] == 5.0
    assert summary["fills_preview"][0]["depth_ahead"] == 20.0
    market = summary["per_market_coverage"][0]
    assert market["realized_markout_distribution"]["5m"]["count"] == 1
    assert market["realized_markout_distribution"]["60m"]["mean"] == 0.09
    assert market["realized_adverse_usd_per_day"] == 4.8
    assert market["simulated_adverse_charge_usd_per_day"] == 2.0
    assert market["simulation_to_reality_haircut"] == 2.4


def test_absent_archive_is_tolerated(tmp_path):
    cfg = _config(tmp_path)
    _seed_maker_portfolio(cfg)

    summary = run_maker_fill_replay(cfg)

    assert summary["status"] == "no_replay_data"
    assert summary["simulated_fills"] == 0
    persisted = read_json(cfg.output_root / "maker_carry" / "maker_fill_replay.json")
    assert persisted["paper_trading_invoked"] is False
    assert persisted["live_trading_invoked"] is False


def test_feature_replay_excludes_compacted_official_book_snapshots(tmp_path):
    cfg = _config(tmp_path)
    archive = cfg.output_root / "polymarket_training_archive"
    feature_path = archive / "features_websocket.csv.gz"
    official_path = archive / "daily_official_books_2026-07-18.csv.gz"
    fields = ["source_timestamp", "asset_id", "best_bid", "best_ask"]
    rows = [{"source_timestamp": 1_000, "asset_id": "tok1", "best_bid": 0.48, "best_ask": 0.52}]
    _write_gzip_csv(feature_path, rows, fields)
    _write_gzip_csv(official_path, rows, fields)

    assert maker_fill_replay._feature_files(cfg) == [feature_path]


def test_snapshot_official_books_retains_repeated_observations_of_unchanged_book(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    raw = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    raw["maker_fill_replay"].update({"book_source": "official", "request_pause_seconds": 0})
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    cfg = load_config(tmp_path / "config.yaml")
    _seed_maker_portfolio(cfg)
    monkeypatch.setattr(maker_fill_replay.time, "sleep", lambda _: None)
    observation_times = iter(["1970-01-01T00:16:00Z", "1970-01-01T00:31:00Z"])
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: next(observation_times))
    calls = []

    def fake_get(url, params=None, timeout=None):
        assert url.endswith("/book")
        assert params == {"token_id": "tok1"}
        calls.append(dict(params or {}))
        return _Response({
            "asset_id": "tok1",
            "timestamp": 1_000,
            "hash": "h1",
            "bids": [{"price": "0.48", "size": "20"}],
            "asks": [{"price": "0.52", "size": "20"}],
        })

    monkeypatch.setattr(maker_fill_replay.requests, "get", fake_get)

    summary = maker_fill_replay.snapshot_official_books(cfg)
    repeated = maker_fill_replay.snapshot_official_books(cfg)

    assert summary["status"] == "ok"
    rows = maker_fill_replay._read_csv_any(cfg.output_root / "maker_carry" / "official_books" / "0xcond.csv.gz")
    assert [(row["source_timestamp"], row["hash"]) for row in rows] == [
        ("1000.0", "h1"),
        ("1000.0", "h1"),
    ]
    assert [row["observation_timestamp"] for row in rows] == ["960.0", "1860.0"]
    assert summary["rows_added"] == 1
    assert repeated["rows_added"] == 1
    assert len(calls) == 2
    assert summary["paper_trading_invoked"] is False
    assert summary["live_trading_invoked"] is False


def test_both_source_replay_reports_source_agreement(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    raw = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    raw["maker_fill_replay"]["book_source"] = "both"
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    cfg = load_config(tmp_path / "config.yaml")
    _seed_maker_portfolio(cfg)
    _seed_archive(cfg)
    _seed_official_books(cfg)
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [{"market": "0xcond", "asset_id": "tok1", "side": "SELL", "price": 0.49, "size": 25, "timestamp": 1_000}],
        fieldnames=["market", "asset_id", "side", "price", "size", "timestamp"],
    )

    def fake_get(url, params=None, timeout=None):
        raise RuntimeError("official endpoint absent")

    monkeypatch.setattr(maker_fill_replay.requests, "get", fake_get)

    summary = run_maker_fill_replay(cfg)

    assert summary["status"] == "ok"
    assert summary["available_book_sources"] == ["archive", "official"]
    assert summary["primary_book_source"] == "official"
    assert summary["source_agreement"]["fills_per_day_divergence"] == 0.0
    assert summary["realism_ratio_by_source"] == {"archive": 2.4, "official": 2.4}


def test_missing_official_coverage_is_not_masked_by_archive(tmp_path):
    cfg = _config(tmp_path)
    raw = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    raw["maker_fill_replay"]["book_source"] = "both"
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    cfg = load_config(tmp_path / "config.yaml")
    _seed_maker_portfolio(cfg)
    _seed_archive(cfg)
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [{"market": "0xcond", "asset_id": "tok1", "side": "SELL", "price": 0.49, "size": 25, "timestamp": 1_000}],
        fieldnames=["market", "asset_id", "side", "price", "size", "timestamp"],
    )

    summary = run_maker_fill_replay(cfg)

    assert summary["status"] == "insufficient_coverage"
    assert summary["primary_book_source"] == "official"
    assert summary["available_book_sources"] == ["archive"]
    assert summary["source_results"]["archive"]["confirmed_fills"] == 1
    assert summary["simulated_fill_opportunities"] == 0
    assert summary["no_contemporaneous_state_opportunities"] == 1
    assert summary["coverage"]["windows_covered"] == 0
    assert summary["realism_ratio"] == "insufficient_coverage"
    assert summary["simulation_to_reality_haircut"] == "insufficient_coverage"


def test_known_fraction_of_crossings_is_confirmed_last_in_queue(tmp_path):
    cfg = _config(tmp_path)
    _seed_maker_portfolio(cfg)
    _seed_archive(cfg)
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [
            {"market": "0xcond", "asset_id": "tok1", "side": "SELL", "price": 0.49, "size": 25, "timestamp": 1_000},
            {"market": "0xcond", "asset_id": "tok1", "side": "SELL", "price": 0.49, "size": 20, "timestamp": 1_000},
        ],
        fieldnames=["market", "asset_id", "side", "price", "size", "timestamp"],
    )

    summary = run_maker_fill_replay(cfg)

    assert summary["simulated_fill_opportunities"] == 2
    assert summary["confirmed_fills"] == 1
    assert summary["confirmed_fill_ratio"] == 0.5
    assert summary["per_market_coverage"][0]["windows_simulated"] == 6
    assert summary["per_market_coverage"][0]["windows_covered"] == 6


def test_market_haircut_uses_market_span_not_other_portfolio_history():
    states = {
        "tok1": [
            {"stamp": 1_000.0, "midpoint": 0.50, "resting_bid_depth_at_quote": 20.0, "resting_ask_depth_at_quote": 20.0},
            {"stamp": 1_300.0, "midpoint": 0.45, "resting_bid_depth_at_quote": 20.0, "resting_ask_depth_at_quote": 20.0},
            {"stamp": 1_900.0, "midpoint": 0.44, "resting_bid_depth_at_quote": 20.0, "resting_ask_depth_at_quote": 20.0},
            {"stamp": 4_600.0, "midpoint": 0.40, "resting_bid_depth_at_quote": 20.0, "resting_ask_depth_at_quote": 20.0},
        ],
        "tok2": [
            {"stamp": 1.0, "midpoint": 0.50, "bid_depth": 20.0, "ask_depth": 20.0},
            {"stamp": 864_001.0, "midpoint": 0.50, "bid_depth": 20.0, "ask_depth": 20.0},
        ],
    }
    trades = [
        {
            "market": "0xcond1",
            "token_id": "tok1",
            "side": "SELL",
            "price": 0.49,
            "size": 25.0,
            "stamp": 1_000.0,
        }
    ]
    portfolio = [
        {
            "condition_id": "0xcond1",
            "token_id": "tok1",
            "question": "short history",
            "quote_size_shares": 10.0,
            "quote_distance": 0.01,
            "quote_bid_price": 0.49,
            "quote_ask_price": 0.51,
        },
        {
            "condition_id": "0xcond2",
            "token_id": "tok2",
            "question": "long unrelated history",
            "quote_size_shares": 10.0,
            "quote_distance": 0.01,
            "quote_bid_price": 0.49,
            "quote_ask_price": 0.51,
        },
    ]

    result = maker_fill_replay._replay_against_states(
        source="official",
        states_by_token=states,
        trades=trades,
        portfolio=portfolio,
        study_charge=4.0,
        study_charge_by_condition={"0xcond1": 2.0, "0xcond2": 2.0},
        max_state_lag_seconds=1800,
    )

    by_market = {row["condition_id"]: row for row in result["per_market_coverage"]}
    assert by_market["0xcond1"]["replay_span_days"] == round(1 / 24, 6)
    assert by_market["0xcond1"]["realized_adverse_usd_per_day"] == 4.8
    assert by_market["0xcond1"]["simulation_to_reality_haircut"] == 2.4


def test_nonzero_simulated_fills_without_coverage_use_explicit_sentinel(tmp_path):
    cfg = _config(tmp_path)
    _seed_maker_portfolio(cfg)
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [{"market": "0xcond", "asset_id": "tok1", "side": "SELL", "price": 0.49, "size": 25, "timestamp": 1_000}],
        fieldnames=["market", "asset_id", "side", "price", "size", "timestamp"],
    )

    summary = run_maker_fill_replay(cfg)

    assert summary["status"] == "insufficient_coverage"
    assert summary["simulated_fill_opportunities"] == 0
    assert summary["no_contemporaneous_state_opportunities"] == 1
    assert summary["confirmed_fills"] == 0
    assert summary["coverage_status"] == "insufficient_coverage"
    assert summary["realism_ratio"] == "insufficient_coverage"


def _wo136_replay(*, trades, states, sheet_stamp=2_000.0, basis="contemporaneous", tick=None):
    entry = {
        "condition_id": "0xcond",
        "token_id": "tok1",
        "question": "WO-136 recorded shape",
        "quote_size_shares": 10.0,
        "quote_distance": 0.01,
        "quote_bid_price": 0.32,
        "quote_ask_price": 0.38,
    }
    if tick is not None:
        entry["order_price_min_tick_size"] = tick
    return maker_fill_replay._replay_against_states(
        source="official",
        states_by_token={"tok1": states},
        trades=trades,
        portfolio=[entry],
        study_charge=1.0,
        study_charge_by_condition={"0xcond": 1.0},
        max_state_lag_seconds=60,
        quote_sheet_generated_stamp=sheet_stamp,
        quoting_basis=basis,
    )


def test_wo136_historical_regime_does_not_replay_current_sheet_quote():
    states = [
        {"stamp": 1_000.0, "midpoint": 0.44, "resting_ask_depth_at_quote": 0.0},
        {"stamp": 1_300.0, "midpoint": 0.44, "resting_ask_depth_at_quote": 0.0},
        {"stamp": 1_900.0, "midpoint": 0.44, "resting_ask_depth_at_quote": 0.0},
        {"stamp": 4_600.0, "midpoint": 0.44, "resting_ask_depth_at_quote": 0.0},
    ]
    trades = [{"token_id": "tok1", "side": "BUY", "price": 0.38, "size": 20.0, "stamp": 1_000.0}]

    contemporary = _wo136_replay(trades=trades, states=states)
    static = _wo136_replay(trades=trades, states=states, basis="static_sheet")

    assert contemporary["confirmed_fills"] == 0
    assert static["confirmed_fills"] == 1
    assert static["simulated_fills_per_day"] > contemporary["simulated_fills_per_day"]


def test_wo136_genuine_contemporaneous_ask_sweep_fills_and_marks_out():
    states = [
        {"stamp": 1_000.0, "midpoint": 0.44, "resting_ask_depth_at_quote": 2.0},
        {"stamp": 1_300.0, "midpoint": 0.47, "resting_ask_depth_at_quote": 0.0},
        {"stamp": 1_900.0, "midpoint": 0.46, "resting_ask_depth_at_quote": 0.0},
        {"stamp": 4_600.0, "midpoint": 0.45, "resting_ask_depth_at_quote": 0.0},
    ]
    result = _wo136_replay(
        trades=[{"token_id": "tok1", "side": "BUY", "price": 0.46, "size": 20.0, "stamp": 1_000.0}],
        states=states,
    )

    assert result["confirmed_fills"] == 1
    assert result["fills_preview"][0]["fill_price"] == 0.45
    assert result["fills_preview"][0]["markout_per_share"]["5m"] == 0.02


def test_wo136_post_sheet_window_matches_static_basis():
    states = [
        {"stamp": 2_100.0, "midpoint": 0.35, "resting_ask_depth_at_quote": 0.0},
        {"stamp": 2_400.0, "midpoint": 0.36, "resting_ask_depth_at_quote": 0.0},
        {"stamp": 3_000.0, "midpoint": 0.36, "resting_ask_depth_at_quote": 0.0},
        {"stamp": 5_700.0, "midpoint": 0.36, "resting_ask_depth_at_quote": 0.0},
    ]
    trades = [{"token_id": "tok1", "side": "BUY", "price": 0.38, "size": 20.0, "stamp": 2_100.0}]

    contemporary = _wo136_replay(trades=trades, states=states)
    static = _wo136_replay(trades=trades, states=states, basis="static_sheet")

    assert contemporary["simulated_fills_per_day"] == static["simulated_fills_per_day"]
    assert contemporary["realism_ratio"] == static["realism_ratio"]


def test_wo136_missing_contemporaneous_state_is_counted_and_excluded():
    result = _wo136_replay(
        trades=[{"token_id": "tok1", "side": "BUY", "price": 0.99, "size": 20.0, "stamp": 1_000.0}],
        states=[],
    )

    assert result["no_contemporaneous_state_opportunities"] == 1
    assert result["simulated_fill_opportunities"] == 0
    assert result["status"] == "insufficient_coverage"


def test_wo136_haircut_policy_strings_survive_verbatim(tmp_path):
    cfg = _config(tmp_path)
    _seed_maker_portfolio(cfg)
    _seed_archive(cfg)
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [{"market": "0xcond", "asset_id": "tok1", "side": "SELL", "price": 0.49, "size": 25, "timestamp": 1_000}],
        fieldnames=["market", "asset_id", "side", "price", "size", "timestamp"],
    )
    summary = run_maker_fill_replay(cfg)

    assert summary["haircut_policy"]["reported_only"] is True
    assert summary["haircut_policy"]["permitted_direction"] == "tighten_only"


def test_wo136_tick_rounding_is_outward():
    states = [
        {"stamp": 1_000.0, "midpoint": 0.445, "resting_ask_depth_at_quote": 0.0},
        {"stamp": 1_300.0, "midpoint": 0.45, "resting_ask_depth_at_quote": 0.0},
        {"stamp": 1_900.0, "midpoint": 0.45, "resting_ask_depth_at_quote": 0.0},
        {"stamp": 4_600.0, "midpoint": 0.45, "resting_ask_depth_at_quote": 0.0},
    ]
    ask = _wo136_replay(
        trades=[{"token_id": "tok1", "side": "BUY", "price": 0.46, "size": 20.0, "stamp": 1_000.0}],
        states=states,
        tick=0.01,
    )
    bid = _wo136_replay(
        trades=[{"token_id": "tok1", "side": "SELL", "price": 0.43, "size": 20.0, "stamp": 1_000.0}],
        states=[{**row, "resting_bid_depth_at_quote": 0.0} for row in states],
        tick=0.01,
    )

    assert ask["fills_preview"][0]["fill_price"] == 0.46
    assert bid["fills_preview"][0]["fill_price"] == 0.43
    assert ask["fills_preview"][0]["quote_rounding"] == "order_price_min_tick_size_outward"


def test_matched_collection_polls_exact_portfolio_and_records_zero_print_coverage(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    _seed_maker_portfolio(cfg)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, dict(params or {})))
        if url.endswith("/book"):
            return _Response(
                {
                    "asset_id": "tok1",
                    "timestamp": 1_783_512_000,
                    "hash": "book-1",
                    "bids": [{"price": "0.48", "size": "20"}],
                    "asks": [{"price": "0.52", "size": "20"}],
                }
            )
        if url.endswith("/trades"):
            return _Response([])
        raise AssertionError(url)

    monkeypatch.setattr(maker_fill_replay.requests, "get", fake_get)
    monkeypatch.setattr(trade_print_collector.requests, "get", fake_get)

    summary = maker_fill_replay.collect_maker_replay_data(cfg)

    assert summary["status"] == "ok"
    assert summary["windows_simulated"] == 1
    assert summary["windows_covered"] == 1
    assert summary["market_windows"][0]["trade_prints_returned"] == 0
    assert summary["market_windows"][0]["covered"] is True
    assert [params for url, params in calls if url.endswith("/trades")] == [
        {"market": "0xcond", "limit": 500}
    ]
    rows = read_json(cfg.output_root / "maker_carry" / "maker_replay_collection.json")
    assert rows["paper_trading_invoked"] is False
    assert rows["live_trading_invoked"] is False


def test_cli_exposes_maker_fill_replay():
    assert "maker-fill-replay" in COMMANDS
    assert "snapshot-official-books" in COMMANDS
    assert "collect-maker-replay-data" in COMMANDS


def test_recent_market_stays_on_snapshot_watchlist_but_stale_one_drops(tmp_path, monkeypatch):
    # WO-104: a market that churned out of the current portfolio but whose
    # book file was appended within the regime window must remain on the
    # snapshot watchlist (so Tier-0 coverage keeps accumulating); a market
    # whose book file is older than the window drops off.
    import os
    import time as _time

    cfg = _config(tmp_path)
    raw = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    # max_candidate_markets=0 isolates the WO-104 persistent-tranche mechanics:
    # with WO-116 seeding enabled, 0xstale (still a ranked candidate) would
    # legitimately return to the watchlist via the candidate tranche instead.
    raw["maker_fill_replay"].update({"book_source": "official", "request_pause_seconds": 0, "regime_days": 7, "max_candidate_markets": 0})
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    cfg = load_config(tmp_path / "config.yaml")

    # Current portfolio is empty (top market churned away).
    write_json(
        cfg.output_root / "maker_carry" / "maker_carry_study.json",
        {"portfolio": [], "paper_trading_invoked": False, "live_trading_invoked": False},
    )
    write_csv(
        cfg.output_root / "maker_carry" / "maker_carry_candidates.csv",
        [
            {"condition_id": "0xrecent", "token_id": "tokR", "adverse_selection_usd_per_day": 2.0},
            {"condition_id": "0xstale", "token_id": "tokS", "adverse_selection_usd_per_day": 2.0},
        ],
        fieldnames=["condition_id", "token_id", "adverse_selection_usd_per_day"],
    )
    book_fields = ["condition_id", "asset_id", "source_timestamp", "observation_timestamp", "hash", "best_bid", "best_ask", "midpoint", "top_bid_size", "top_ask_size", "bids_json", "asks_json", "collected_at_utc"]
    for cond, tok in (("0xrecent", "tokR"), ("0xstale", "tokS")):
        _write_gzip_csv(
            cfg.output_root / "maker_carry" / "official_books" / f"{cond}.csv.gz",
            [{"condition_id": cond, "asset_id": tok, "source_timestamp": "1000.0", "observation_timestamp": "960.0", "hash": "h0", "best_bid": "0.48", "best_ask": "0.52", "midpoint": "0.5", "top_bid_size": "20", "top_ask_size": "20", "bids_json": "[]", "asks_json": "[]", "collected_at_utc": "1970-01-01T00:16:00Z"}],
            fieldnames=book_fields,
        )
    # Age the stale market's file beyond the regime window.
    stale_path = cfg.output_root / "maker_carry" / "official_books" / "0xstale.csv.gz"
    old = _time.time() - 8 * 86400
    os.utime(stale_path, (old, old))

    monkeypatch.setattr(maker_fill_replay.time, "sleep", lambda _: None)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "1970-01-01T00:46:00Z")
    calls: list[dict] = []

    def fake_get(url, params=None, timeout=None):
        calls.append(dict(params or {}))
        return _Response({"asset_id": params["token_id"], "timestamp": 2000, "hash": "h1", "bids": [{"price": "0.48", "size": "20"}], "asks": [{"price": "0.52", "size": "20"}]})

    monkeypatch.setattr(maker_fill_replay.requests, "get", fake_get)

    summary = maker_fill_replay.snapshot_official_books(cfg)

    assert summary["status"] == "ok"
    polled = {c.get("token_id") for c in calls}
    assert "tokR" in polled  # churned-but-recent market kept on the watchlist
    assert "tokS" not in polled  # market older than the regime window drops off


_BOOK_FIELDS = ["condition_id", "asset_id", "source_timestamp", "observation_timestamp", "hash", "best_bid", "best_ask", "midpoint", "top_bid_size", "top_ask_size", "bids_json", "asks_json", "collected_at_utc"]


def _recent_book_file(cfg, cond: str, tok: str) -> None:
    _write_gzip_csv(
        cfg.output_root / "maker_carry" / "official_books" / f"{cond}.csv.gz",
        [{"condition_id": cond, "asset_id": tok, "source_timestamp": "1000.0", "observation_timestamp": "960.0", "hash": "h0", "best_bid": "0.48", "best_ask": "0.52", "midpoint": "0.5", "top_bid_size": "20", "top_ask_size": "20", "bids_json": "[]", "asks_json": "[]", "collected_at_utc": "1970-01-01T00:16:00Z"}],
        fieldnames=_BOOK_FIELDS,
    )


def test_wo139_persistent_tranche_is_newest_first_not_condition_id_order(tmp_path):
    cfg = _config(tmp_path)
    settings = maker_fill_replay._settings(cfg)
    settings["regime_days"] = 7
    _recent_book_file(cfg, "0xaaa", "tokOld")
    _recent_book_file(cfg, "0xffff", "tokNew")
    books = cfg.output_root / "maker_carry" / "official_books"
    now = maker_fill_replay.time.time()
    # Reverse lexicographic order: the lexicographically last id is newest.
    os.utime(books / "0xaaa.csv.gz", (now - 20, now - 20))
    os.utime(books / "0xffff.csv.gz", (now - 10, now - 10))

    recent = maker_fill_replay._recent_book_markets(cfg, settings, exclude=set())

    assert [row["condition_id"] for row in recent] == ["0xffff", "0xaaa"]


def test_wo139_persistent_tranche_uses_name_to_break_mtime_ties(tmp_path):
    cfg = _config(tmp_path)
    settings = maker_fill_replay._settings(cfg)
    _recent_book_file(cfg, "0xffff", "tokLast")
    _recent_book_file(cfg, "0xaaa", "tokFirst")
    books = cfg.output_root / "maker_carry" / "official_books"
    tied_mtime = maker_fill_replay.time.time() - 10
    for path in books.glob("*.csv.gz"):
        os.utime(path, (tied_mtime, tied_mtime))

    recent = maker_fill_replay._recent_book_markets(cfg, settings, exclude=set())

    assert [row["condition_id"] for row in recent] == ["0xaaa", "0xffff"]


def test_wo139_persistent_tranche_window_uses_evaluation_clock(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    settings = maker_fill_replay._settings(cfg)
    settings["regime_days"] = 7
    _recent_book_file(cfg, "0xfixed", "tokFixed")
    archive = cfg.output_root / "maker_carry" / "official_books" / "0xfixed.csv.gz"
    archive_mtime = 1_000_000.0
    os.utime(archive, (archive_mtime, archive_mtime))

    monkeypatch.setattr(maker_fill_replay.time, "time", lambda: archive_mtime + 6.9 * 86400)
    assert [row["condition_id"] for row in maker_fill_replay._recent_book_markets(
        cfg, settings, exclude=set()
    )] == ["0xfixed"]

    monkeypatch.setattr(maker_fill_replay.time, "time", lambda: archive_mtime + 7.1 * 86400)
    assert maker_fill_replay._recent_book_markets(cfg, settings, exclude=set()) == []


def test_wo139_persistent_tranche_ignores_stat_failure(tmp_path):
    cfg = _config(tmp_path)
    settings = maker_fill_replay._settings(cfg)
    _recent_book_file(cfg, "0xvalid", "tokValid")
    books = cfg.output_root / "maker_carry" / "official_books"
    (books / "0xbroken.csv.gz").symlink_to(books / "missing.csv.gz")

    recent = maker_fill_replay._recent_book_markets(cfg, settings, exclude=set())

    assert recent == [{"condition_id": "0xvalid", "token_id": "tokValid"}]


def test_wo139_seed_budget_prioritizes_sizeable_rows_without_shrinking():
    rows = {
        "0xthin": {"token_id": "thin", "net_carry_usd_per_day": "10", "yield_rank": "1",
                    "estimate_quality": "thin_book_untrusted", "band_eligible": "True", "resolution_risk": "low"},
        "0xsize": {"token_id": "size", "net_carry_usd_per_day": "3", "yield_rank": "2",
                    "estimate_quality": "book_and_history", "band_eligible": "True", "resolution_risk": "medium"},
        "0xraw": {"token_id": "raw", "net_carry_usd_per_day": "2", "yield_rank": "3"},
    }

    seeds, _ = maker_fill_replay._candidate_seed_markets(rows, exclude=set(), cap=2)

    assert [row["condition_id"] for row in seeds] == ["0xsize", "0xthin"]
    assert len(seeds) == 2


def test_wo139_tier1_precedes_higher_carry_tier2_and_accepts_blank_risk():
    rows = {
        "0xtier2": {"token_id": "tier2", "net_carry_usd_per_day": "100", "yield_rank": "1",
                     "estimate_quality": "single_window_history", "band_eligible": "True", "resolution_risk": "low"},
        "0xtier1": {"token_id": "tier1", "net_carry_usd_per_day": "5", "yield_rank": "2",
                     "estimate_quality": "book_and_history", "band_eligible": "True", "resolution_risk": ""},
    }

    seeds, _ = maker_fill_replay._candidate_seed_markets(rows, exclude=set(), cap=1)

    assert seeds == [{"condition_id": "0xtier1", "token_id": "tier1"}]


def test_wo139_seed_budget_fills_remainder_in_raw_order_and_keeps_malformed_rows():
    rows = {
        "0xraw1": {"token_id": "raw1", "net_carry_usd_per_day": "9", "yield_rank": "1",
                   "estimate_quality": "???", "band_eligible": "maybe", "resolution_risk": ""},
        "0xraw2": {"token_id": "raw2", "net_carry_usd_per_day": "8", "yield_rank": "2"},
        "0xsize": {"token_id": "size", "net_carry_usd_per_day": "1", "yield_rank": "3",
                   "estimate_quality": "book_and_history", "band_eligible": "True", "resolution_risk": "low"},
    }

    seeds, _ = maker_fill_replay._candidate_seed_markets(rows, exclude=set(), cap=3)

    assert [row["condition_id"] for row in seeds] == ["0xsize", "0xraw1", "0xraw2"]
    assert len(seeds) == len(rows)


def test_scheduled_collector_refreshes_persistent_books_when_portfolio_empty(tmp_path, monkeypatch):
    # WO-104 fix (#257): the SCHEDULED collect-maker-replay-data path used to
    # early-return on an empty quote sheet BEFORE snapshotting books, so the
    # persistent watchlist never ran in a churn gap and Tier-0 coverage stopped
    # maturing. It must now still snapshot recently-active markets.
    cfg = _config(tmp_path)
    raw = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    raw["maker_fill_replay"].update({"book_source": "official", "request_pause_seconds": 0, "regime_days": 7})
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    cfg = load_config(tmp_path / "config.yaml")

    write_json(
        cfg.output_root / "maker_carry" / "maker_carry_study.json",
        {"portfolio": [], "paper_trading_invoked": False, "live_trading_invoked": False},
    )
    write_csv(
        cfg.output_root / "maker_carry" / "maker_carry_candidates.csv",
        [{"condition_id": "0xrecent", "token_id": "tokR", "adverse_selection_usd_per_day": 2.0}],
        fieldnames=["condition_id", "token_id", "adverse_selection_usd_per_day"],
    )
    _recent_book_file(cfg, "0xrecent", "tokR")

    monkeypatch.setattr(maker_fill_replay.time, "sleep", lambda _: None)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "1970-01-01T00:46:00Z")
    calls: list[dict] = []

    def fake_get(url, params=None, timeout=None):
        calls.append(dict(params or {}))
        return _Response({"asset_id": params["token_id"], "timestamp": 2000, "hash": "h1", "bids": [{"price": "0.48", "size": "20"}], "asks": [{"price": "0.52", "size": "20"}]})

    monkeypatch.setattr(maker_fill_replay.requests, "get", fake_get)

    summary = maker_fill_replay.collect_maker_replay_data(cfg)

    assert summary["status"] == "no_portfolio"
    assert summary["persistent_snapshot_status"] == "ok"
    assert "tokR" in {c.get("token_id") for c in calls}  # persistent market still polled


def test_persistent_markets_reserved_when_portfolio_fills_max_markets(tmp_path, monkeypatch):
    # WO-104 fix (#257): a full portfolio (at max_markets) must not crowd a
    # recently-active persistent market off the snapshot watchlist.
    cfg = _config(tmp_path)
    raw = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    raw["maker_fill_replay"].update({"book_source": "official", "request_pause_seconds": 0, "regime_days": 7, "max_markets": 1})
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    cfg = load_config(tmp_path / "config.yaml")

    write_json(
        cfg.output_root / "maker_carry" / "maker_carry_study.json",
        {"portfolio": [{"condition_id": "0xport", "token_id": "tokP", "quote_size_shares": 10, "quote_distance": 0.01}],
         "paper_trading_invoked": False, "live_trading_invoked": False},
    )
    write_csv(
        cfg.output_root / "maker_carry" / "maker_carry_candidates.csv",
        [{"condition_id": "0xport", "token_id": "tokP", "adverse_selection_usd_per_day": 2.0},
         {"condition_id": "0xrecent", "token_id": "tokR", "adverse_selection_usd_per_day": 2.0}],
        fieldnames=["condition_id", "token_id", "adverse_selection_usd_per_day"],
    )
    _recent_book_file(cfg, "0xrecent", "tokR")

    monkeypatch.setattr(maker_fill_replay.time, "sleep", lambda _: None)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "1970-01-01T00:46:00Z")
    polled: list[str] = []

    def fake_post(url, json=None, timeout=None):
        items = json or []
        polled.extend(str(item["token_id"]) for item in items)
        return _Response([{"asset_id": item["token_id"], "timestamp": 2000, "hash": "h1", "bids": [{"price": "0.48", "size": "20"}], "asks": [{"price": "0.52", "size": "20"}]} for item in items])

    monkeypatch.setattr(maker_fill_replay.requests, "post", fake_post)
    monkeypatch.setattr(maker_fill_replay.requests, "get", lambda *a, **k: _Response({}))

    maker_fill_replay.snapshot_official_books(cfg)

    # Portfolio is already at max_markets=1, yet the persistent market is still
    # polled thanks to its reserved budget (old code truncated it away).
    assert "tokP" in polled and "tokR" in polled


def test_wo116_candidate_seeds_are_snapshotted_when_portfolio_empty(tmp_path, monkeypatch):
    # WO-116: with an empty portfolio and NO existing book files (the cold-start
    # starvation state), the top-ranked candidates must still be snapshotted so
    # they season toward the WO-113 book-history requirement before selection.
    cfg = _config(tmp_path)
    raw = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    raw["maker_fill_replay"].update({"book_source": "official", "request_pause_seconds": 0, "max_candidate_markets": 2})
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    cfg = load_config(tmp_path / "config.yaml")

    write_json(
        cfg.output_root / "maker_carry" / "maker_carry_study.json",
        {"portfolio": [], "paper_trading_invoked": False, "live_trading_invoked": False},
    )
    write_csv(
        cfg.output_root / "maker_carry" / "maker_carry_candidates.csv",
        [
            {"condition_id": "0xa", "token_id": "tokA", "net_carry_usd_per_day": 3.0},
            {"condition_id": "0xb", "token_id": "tokB", "net_carry_usd_per_day": 2.0},
            {"condition_id": "0xc", "token_id": "tokC", "net_carry_usd_per_day": 1.0},
        ],
        fieldnames=["condition_id", "token_id", "net_carry_usd_per_day"],
    )

    monkeypatch.setattr(maker_fill_replay.time, "sleep", lambda _: None)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "1970-01-01T00:46:00Z")
    polled: list[str] = []

    def fake_post(url, json=None, timeout=None):
        items = json or []
        polled.extend(str(item["token_id"]) for item in items)
        return _Response([{"asset_id": item["token_id"], "timestamp": 2000, "hash": "h1", "bids": [{"price": "0.48", "size": "20"}], "asks": [{"price": "0.52", "size": "20"}]} for item in items])

    monkeypatch.setattr(maker_fill_replay.requests, "post", fake_post)
    monkeypatch.setattr(maker_fill_replay.requests, "get", lambda *a, **k: _Response({}))

    summary = maker_fill_replay.snapshot_official_books(cfg)

    assert summary["status"] == "ok"
    # Cap of 2 takes the two best-ranked candidates by net carry; 0xc stays off.
    assert set(polled) == {"tokA", "tokB"}
    assert summary["portfolio_markets"] == 0
    assert summary["candidate_seed_markets"] == 2
    assert (cfg.output_root / "maker_carry" / "official_books" / "0xa.csv.gz").is_file()
    assert (cfg.output_root / "maker_carry" / "official_books" / "0xb.csv.gz").is_file()
    assert not (cfg.output_root / "maker_carry" / "official_books" / "0xc.csv.gz").exists()


def test_wo116_candidate_seeds_exclude_portfolio_and_persistent(tmp_path, monkeypatch):
    # WO-116: the seed tranche must not duplicate markets already covered by the
    # portfolio or the WO-104 persistent tranche.
    cfg = _config(tmp_path)
    raw = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    raw["maker_fill_replay"].update({"book_source": "official", "request_pause_seconds": 0, "regime_days": 7, "max_candidate_markets": 5})
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    cfg = load_config(tmp_path / "config.yaml")

    write_json(
        cfg.output_root / "maker_carry" / "maker_carry_study.json",
        {"portfolio": [{"condition_id": "0xport", "token_id": "tokP", "quote_size_shares": 10, "quote_distance": 0.01}],
         "paper_trading_invoked": False, "live_trading_invoked": False},
    )
    write_csv(
        cfg.output_root / "maker_carry" / "maker_carry_candidates.csv",
        [
            {"condition_id": "0xport", "token_id": "tokP", "net_carry_usd_per_day": 3.0},
            {"condition_id": "0xrecent", "token_id": "tokR", "net_carry_usd_per_day": 2.0},
            {"condition_id": "0xnew", "token_id": "tokN", "net_carry_usd_per_day": 1.0},
        ],
        fieldnames=["condition_id", "token_id", "net_carry_usd_per_day"],
    )
    _recent_book_file(cfg, "0xrecent", "tokR")

    monkeypatch.setattr(maker_fill_replay.time, "sleep", lambda _: None)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "1970-01-01T00:46:00Z")
    polled: list[str] = []

    def fake_post(url, json=None, timeout=None):
        items = json or []
        polled.extend(str(item["token_id"]) for item in items)
        return _Response([{"asset_id": item["token_id"], "timestamp": 2000, "hash": "h1", "bids": [{"price": "0.48", "size": "20"}], "asks": [{"price": "0.52", "size": "20"}]} for item in items])

    monkeypatch.setattr(maker_fill_replay.requests, "post", fake_post)
    monkeypatch.setattr(maker_fill_replay.requests, "get", lambda *a, **k: _Response({}))

    summary = maker_fill_replay.snapshot_official_books(cfg)

    assert summary["status"] == "ok"
    assert set(polled) == {"tokP", "tokR", "tokN"}  # each market exactly once
    assert len(polled) == 3
    assert summary["portfolio_markets"] == 1
    assert summary["persistent_markets"] == 1
    assert summary["candidate_seed_markets"] == 1  # only 0xnew; no duplicates


def test_wo116_collection_window_ledger_stays_portfolio_only(tmp_path, monkeypatch):
    # WO-116: seeding books for candidates must NOT add collection-window ledger
    # rows - coverage_ratio semantics stay keyed to the portfolio alone.
    cfg = _config(tmp_path)
    raw = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    raw["maker_fill_replay"].update({"book_source": "official", "request_pause_seconds": 0, "max_candidate_markets": 5})
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    cfg = load_config(tmp_path / "config.yaml")

    write_json(
        cfg.output_root / "maker_carry" / "maker_carry_study.json",
        {"portfolio": [], "paper_trading_invoked": False, "live_trading_invoked": False},
    )
    write_csv(
        cfg.output_root / "maker_carry" / "maker_carry_candidates.csv",
        [{"condition_id": "0xnew", "token_id": "tokN", "net_carry_usd_per_day": 1.0}],
        fieldnames=["condition_id", "token_id", "net_carry_usd_per_day"],
    )

    monkeypatch.setattr(maker_fill_replay.time, "sleep", lambda _: None)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "1970-01-01T00:46:00Z")

    def fake_get(url, params=None, timeout=None):
        return _Response({"asset_id": params["token_id"], "timestamp": 2000, "hash": "h1", "bids": [{"price": "0.48", "size": "20"}], "asks": [{"price": "0.52", "size": "20"}]})

    monkeypatch.setattr(maker_fill_replay.requests, "get", fake_get)

    summary = maker_fill_replay.collect_maker_replay_data(cfg)

    # The scheduled collector still reports no_portfolio for its ledger lane,
    # but the seeded candidate's book was snapshotted for seasoning.
    assert summary["status"] == "no_portfolio"
    assert summary["persistent_snapshot_status"] == "ok"
    assert (cfg.output_root / "maker_carry" / "official_books" / "0xnew.csv.gz").is_file()
    windows_path = cfg.output_root / "maker_carry" / "maker_replay_collection_windows.csv"
    if windows_path.exists():
        window_rows = list(csv.DictReader(windows_path.open(encoding="utf-8")))
        assert all(row.get("condition_id") != "0xnew" for row in window_rows)


def test_wo113_coverage_window_alignment_excludes_unobservable_horizon(tmp_path):
    # WO-113: observed book states run 1000..4600. A fill at t=3000 has its 5m
    # (3300) and 15m (3900) markout targets inside that span, but the 60m target
    # (6600) is beyond the last observed book. The 60m window is physically
    # unmeasurable, so it must be EXCLUDED from the denominator, not counted as
    # simulated-but-uncovered (which used to peg the coverage ratio down).
    cfg = _config(tmp_path)
    _seed_maker_portfolio(cfg)
    _seed_archive(cfg)
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [{"market": "0xcond", "asset_id": "tok1", "side": "SELL", "price": 0.43, "size": 25, "timestamp": 3_000}],
        fieldnames=["market", "asset_id", "side", "price", "size", "timestamp"],
    )

    summary = run_maker_fill_replay(cfg)

    by_horizon = summary["coverage"]["by_horizon"]
    assert by_horizon["5m"]["windows_simulated"] == 1
    assert by_horizon["15m"]["windows_simulated"] == 1
    assert by_horizon["60m"]["windows_simulated"] == 0  # beyond observed book span -> excluded
    assert summary["coverage"]["windows_simulated"] == 2
    assert summary["per_market_coverage"][0]["book_history_span_days"] > 0.0


# --- WO-131: seeding-budget hygiene and the seasoning runway ---


def _seed_cfg(tmp_path, **overrides):
    cfg = _config(tmp_path)
    raw = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    raw["maker_fill_replay"].update(
        {"book_source": "official", "request_pause_seconds": 0, "max_candidate_markets": 10, **overrides}
    )
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(tmp_path / "config.yaml")


def _seed_candidates(cfg, rows):
    write_json(
        cfg.output_root / "maker_carry" / "maker_carry_study.json",
        {"portfolio": [], "paper_trading_invoked": False, "live_trading_invoked": False},
    )
    write_csv(
        cfg.output_root / "maker_carry" / "maker_carry_candidates.csv",
        rows,
        fieldnames=["condition_id", "token_id", "net_carry_usd_per_day", "yield_rank"],
    )


def _poll_with(monkeypatch, dead_tokens, *, now):
    """Stub the CLOB so listed tokens return a book and dead ones 404."""
    polled: list[str] = []

    def fake_post(url, json=None, timeout=None):
        items = json or []
        polled.extend(str(item["token_id"]) for item in items)
        return _Response(
            [
                {
                    "asset_id": item["token_id"],
                    "timestamp": 2000,
                    "hash": "h1",
                    "bids": [{"price": "0.48", "size": "20"}],
                    "asks": [{"price": "0.52", "size": "20"}],
                }
                for item in items
                if str(item["token_id"]) not in dead_tokens
            ]
        )

    def fake_get(url, params=None, timeout=None):
        token = str((params or {}).get("token_id") or "")
        polled.append(token)
        if token in dead_tokens:
            raise maker_fill_replay.requests.HTTPError(
                "404 Client Error: Not Found for url: /book"
            )
        return _Response({"asset_id": token, "timestamp": 2000, "hash": "h1",
                          "bids": [{"price": "0.48", "size": "20"}], "asks": [{"price": "0.52", "size": "20"}]})

    monkeypatch.setattr(maker_fill_replay.time, "sleep", lambda _: None)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: now)
    monkeypatch.setattr(maker_fill_replay.requests, "post", fake_post)
    monkeypatch.setattr(maker_fill_replay.requests, "get", fake_get)
    return polled


def test_wo131_delisted_token_is_skipped_only_after_the_registered_threshold(tmp_path, monkeypatch):
    # Measured 2026-07-27: 7 of 50 polled markets returned HTTP 404 on delisted
    # tokens, so ~14% of the seeding budget bought corpses while M-A had 23 days
    # left. Two 404s are not yet evidence of a delisting; three are.
    cfg = _seed_cfg(tmp_path)
    _seed_candidates(cfg, [
        {"condition_id": "0xdead", "token_id": "tokD", "net_carry_usd_per_day": "9.0", "yield_rank": "1"},
        {"condition_id": "0xlive", "token_id": "tokL", "net_carry_usd_per_day": "1.0", "yield_rank": "2"},
    ])

    for cycle in range(2):
        polled = _poll_with(monkeypatch, {"tokD"}, now=f"2026-07-27T0{cycle}:00:00Z")
        maker_fill_replay.snapshot_official_books(cfg)
        assert "tokD" in polled, cycle  # still polled after 1 and 2 failures

    polled = _poll_with(monkeypatch, {"tokD"}, now="2026-07-27T02:00:00Z")
    summary = maker_fill_replay.snapshot_official_books(cfg)
    assert "tokD" in polled  # the third 404 is what establishes the marker

    polled = _poll_with(monkeypatch, {"tokD"}, now="2026-07-27T03:00:00Z")
    summary = maker_fill_replay.snapshot_official_books(cfg)
    assert "tokD" not in polled  # now inside the cooldown
    assert "tokL" in polled  # the live market keeps its slot
    assert summary["delisted_tokens_skipped"] == ["tokD"]
    assert summary["candidate_seed_exclusions"]["delisted_cooldown"] == 1


def test_wo131_the_skip_is_a_cooldown_not_a_blacklist(tmp_path, monkeypatch):
    # A skipped token has no official-book file, so it never enters the mtime
    # persistent tranche either. Without a TTL nothing would ever request it
    # again and it could never clear its own marker - a transient outage or a
    # re-listing would become permanent exclusion.
    cfg = _seed_cfg(tmp_path)
    _seed_candidates(cfg, [
        {"condition_id": "0xdead", "token_id": "tokD", "net_carry_usd_per_day": "9.0", "yield_rank": "1"},
    ])
    for hour in range(3):
        _poll_with(monkeypatch, {"tokD"}, now=f"2026-07-27T0{hour}:00:00Z")
        maker_fill_replay.snapshot_official_books(cfg)

    polled = _poll_with(monkeypatch, {"tokD"}, now="2026-07-27T05:00:00Z")
    maker_fill_replay.snapshot_official_books(cfg)
    assert "tokD" not in polled  # inside the 24h cooldown

    # Past the TTL it is re-probed, and a valid book clears the marker outright.
    polled = _poll_with(monkeypatch, set(), now="2026-07-28T06:00:00Z")
    summary = maker_fill_replay.snapshot_official_books(cfg)
    assert "tokD" in polled
    assert summary["delisted_tokens_skipped"] == []
    assert summary["delisted_token_count"] == 0

    marker = read_json(cfg.output_root / "maker_carry" / "delisted_token_markers.json")
    assert marker["paper_trading_invoked"] is False
    assert marker["live_trading_invoked"] is False


def test_wo131_a_non_finite_carry_never_displaces_a_real_candidate(tmp_path, monkeypatch):
    # A NaN carry participates in the sort with UNDEFINED ordering and can take a
    # seeding slot ahead of a finite-carry market.
    cfg = _seed_cfg(tmp_path, max_candidate_markets=1)
    _seed_candidates(cfg, [
        {"condition_id": "0xnan", "token_id": "tokN", "net_carry_usd_per_day": "nan", "yield_rank": "1"},
        {"condition_id": "0xreal", "token_id": "tokR", "net_carry_usd_per_day": "4.0", "yield_rank": "2"},
    ])
    polled = _poll_with(monkeypatch, set(), now="2026-07-27T00:00:00Z")
    summary = maker_fill_replay.snapshot_official_books(cfg)

    assert "tokR" in polled
    assert "tokN" not in polled
    assert summary["candidate_seed_exclusions"]["non_finite_rank"] == 1


def test_wo131_runway_matches_the_helper_the_eligibility_rule_uses(tmp_path, monkeypatch):
    # The report must not re-derive depth: a second implementation would drift
    # from the rule it describes. Bind them so drift fails this test.
    from polymarket_predictive_engine.maker_carry_study import (
        MAKER_POLICY_DEFAULTS,
        _book_history_depth,
    )

    cfg = _seed_cfg(tmp_path)
    _seed_candidates(cfg, [
        {"condition_id": "0xseed", "token_id": "tokS", "net_carry_usd_per_day": "4.0", "yield_rank": "1"},
    ])
    _poll_with(monkeypatch, set(), now="2026-07-27T00:00:00Z")
    summary = maker_fill_replay.snapshot_official_books(cfg)

    out_root = cfg.output_root / "maker_carry"
    hours, snapshots = _book_history_depth(out_root, "0xseed")
    row = next(r for r in summary["seasoning_runway"] if r["condition_id"] == "0xseed")
    assert row["book_history_hours"] == round(float(hours), 4)
    assert row["book_snapshot_count"] == snapshots
    assert row["snapshots_remaining"] == max(
        0, int(MAKER_POLICY_DEFAULTS["maker_min_book_snapshots"]) - snapshots
    )
    assert summary["closest_to_eligibility"][0]["condition_id"] == "0xseed"


def test_wo131_a_missing_marker_artifact_polls_every_candidate(tmp_path, monkeypatch):
    # Fail-safe direction: for a collector the conservative action is to COLLECT,
    # so an unreadable marker file must never suppress a poll.
    cfg = _seed_cfg(tmp_path)
    _seed_candidates(cfg, [
        {"condition_id": "0xa", "token_id": "tokA", "net_carry_usd_per_day": "4.0", "yield_rank": "1"},
        {"condition_id": "0xb", "token_id": "tokB", "net_carry_usd_per_day": "3.0", "yield_rank": "2"},
    ])
    marker_path = cfg.output_root / "maker_carry" / "delisted_token_markers.json"
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text("{ this is not json", encoding="utf-8")

    polled = _poll_with(monkeypatch, set(), now="2026-07-27T00:00:00Z")
    summary = maker_fill_replay.snapshot_official_books(cfg)

    assert {"tokA", "tokB"} <= set(polled)
    assert summary["delisted_tokens_skipped"] == []


# --- WO-149: portfolio-scope official-book pulse + join-lag staleness ---

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "recorded"


def _recorded_fixture(name: str):
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def test_wo149_default_scope_is_byte_identical_to_explicit_watchlist_scope(tmp_path, monkeypatch):
    # Test (1): scope="watchlist" (the default, and the explicit keyword) is
    # byte-identical on every path. Two independent fixtures, one run with no
    # scope argument (today's callers) and one with scope="watchlist"
    # explicit, must produce identical official_book_snapshot.json content
    # and identical official_books/*.csv.gz row content.
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")
    monkeypatch.setattr(maker_fill_replay.time, "sleep", lambda _: None)

    def fake_get(url, params=None, timeout=None):
        return _Response(
            {
                "asset_id": params["token_id"],
                "timestamp": 1_000,
                "hash": "h1",
                "bids": [{"price": "0.48", "size": "20"}],
                "asks": [{"price": "0.52", "size": "20"}],
            }
        )

    monkeypatch.setattr(maker_fill_replay.requests, "get", fake_get)

    def _run(root: Path):
        root.mkdir()
        cfg = _config(root)
        _seed_maker_portfolio(cfg)
        return cfg

    cfg_default = _run(tmp_path / "default")
    summary_default = maker_fill_replay.snapshot_official_books(cfg_default)
    cfg_explicit = _run(tmp_path / "explicit")
    summary_explicit = maker_fill_replay.snapshot_official_books(cfg_explicit, scope="watchlist")

    # `files_written` legitimately differs: it is an absolute path under each
    # fixture's own tmp_path root. Compare basenames for that field and
    # everything else as-is.
    assert [Path(p).name for p in summary_default["files_written"]] == [
        Path(p).name for p in summary_explicit["files_written"]
    ]
    comparable_default = {k: v for k, v in summary_default.items() if k != "files_written"}
    comparable_explicit = {k: v for k, v in summary_explicit.items() if k != "files_written"}
    assert comparable_default == comparable_explicit
    # The persisted official_book_snapshot.json is exactly the returned
    # summary dict (write_json serialises it verbatim), so the in-memory
    # equality just proven already covers file content; `files_written`
    # embeds each fixture's own absolute tmp_path root, so the two files'
    # raw bytes cannot be identical across separate tmp_path roots for a
    # reason unrelated to `scope` - normalize that one field before the
    # on-disk comparison instead of asserting raw-byte equality directly.
    read_default = read_json(cfg_default.output_root / "maker_carry" / "official_book_snapshot.json")
    read_explicit = read_json(cfg_explicit.output_root / "maker_carry" / "official_book_snapshot.json")
    read_default["files_written"] = [Path(p).name for p in read_default["files_written"]]
    read_explicit["files_written"] = [Path(p).name for p in read_explicit["files_written"]]
    assert read_default == read_explicit
    # Gzip embeds a write-time mtime in its header, so raw bytes of two
    # separate writes legitimately differ even with identical content -
    # compare the decompressed rows instead.
    rows_default = maker_fill_replay._read_csv_any(
        cfg_default.output_root / "maker_carry" / "official_books" / "0xcond.csv.gz"
    )
    rows_explicit = maker_fill_replay._read_csv_any(
        cfg_explicit.output_root / "maker_carry" / "official_books" / "0xcond.csv.gz"
    )
    assert rows_default == rows_explicit
    assert not (cfg_default.output_root / "maker_carry" / "official_book_pulse.json").exists()


def test_wo149_portfolio_scope_polls_only_the_current_portfolio_2_3_4(tmp_path, monkeypatch):
    # Test (2): scope="portfolio" with 2 portfolio + 3 persistent-eligible +
    # 4 candidate-seed-eligible markets on the watchlist fixture -> exactly 2
    # polled (persistent_markets=0, candidate_seed_markets=0), the pulse JSON
    # is written, and the pre-existing (sentinel) watchlist snapshot JSON is
    # byte-identical afterward - this scope never touches it.
    cfg = _config(tmp_path)
    raw = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    raw["maker_fill_replay"].update(
        {"book_source": "official", "request_pause_seconds": 0, "regime_days": 7, "max_candidate_markets": 4}
    )
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    cfg = load_config(tmp_path / "config.yaml")

    write_json(
        cfg.output_root / "maker_carry" / "maker_carry_study.json",
        {
            "generated_at_utc": "2026-07-10T00:00:00Z",
            "portfolio": [
                {"condition_id": "0xp1", "token_id": "tokP1", "quote_size_shares": 10, "quote_distance": 0.01},
                {"condition_id": "0xp2", "token_id": "tokP2", "quote_size_shares": 10, "quote_distance": 0.01},
            ],
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )
    write_csv(
        cfg.output_root / "maker_carry" / "maker_carry_candidates.csv",
        [
            {"condition_id": "0xp1", "token_id": "tokP1", "net_carry_usd_per_day": "5.0"},
            {"condition_id": "0xp2", "token_id": "tokP2", "net_carry_usd_per_day": "4.0"},
            {"condition_id": "0xc1", "token_id": "tokC1", "net_carry_usd_per_day": "3.0"},
            {"condition_id": "0xc2", "token_id": "tokC2", "net_carry_usd_per_day": "2.0"},
            {"condition_id": "0xc3", "token_id": "tokC3", "net_carry_usd_per_day": "1.0"},
            {"condition_id": "0xc4", "token_id": "tokC4", "net_carry_usd_per_day": "0.5"},
        ],
        fieldnames=["condition_id", "token_id", "net_carry_usd_per_day"],
    )
    for cond, tok in (("0xr1", "tokR1"), ("0xr2", "tokR2"), ("0xr3", "tokR3")):
        _recent_book_file(cfg, cond, tok)

    sentinel = {"sentinel": True, "note": "pre-existing watchlist snapshot must not move"}
    write_json(cfg.output_root / "maker_carry" / "official_book_snapshot.json", sentinel)
    sentinel_bytes = (cfg.output_root / "maker_carry" / "official_book_snapshot.json").read_bytes()

    monkeypatch.setattr(maker_fill_replay.time, "sleep", lambda _: None)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")
    polled: list[str] = []

    def fake_get(url, params=None, timeout=None):
        token = (params or {}).get("token_id")
        polled.append(token)
        return _Response(
            {
                "asset_id": token,
                "timestamp": 2000,
                "hash": "h1",
                "bids": [{"price": "0.48", "size": "20"}],
                "asks": [{"price": "0.52", "size": "20"}],
            }
        )

    monkeypatch.setattr(maker_fill_replay.requests, "get", fake_get)

    summary = maker_fill_replay.snapshot_official_books(cfg, scope="portfolio")

    assert summary["status"] == "ok"
    assert summary["markets_polled"] == 2
    assert summary["portfolio_markets"] == 2
    assert summary["persistent_markets"] == 0
    assert summary["candidate_seed_markets"] == 0
    assert set(polled) == {"tokP1", "tokP2"}
    pulse_path = cfg.output_root / "maker_carry" / "official_book_pulse.json"
    assert pulse_path.is_file()
    pulse = read_json(pulse_path)
    assert pulse["scope"] == "portfolio"
    assert (cfg.output_root / "maker_carry" / "official_book_snapshot.json").read_bytes() == sentinel_bytes


def test_wo149_portfolio_scope_never_touches_the_collection_window_ledger(tmp_path, monkeypatch):
    # Test (3): sentinel collection-windows CSV byte-identical.
    cfg = _config(tmp_path)
    _seed_maker_portfolio(cfg)
    ledger_path = cfg.output_root / "maker_carry" / "maker_replay_collection_windows.csv"
    write_csv(
        ledger_path,
        [
            {
                "window_id": "sentinel",
                "condition_id": "0xsentinel",
                "asset_id": "toksentinel",
                "portfolio_generated_at_utc": "",
                "collected_at_utc": "sentinel",
                "quote_bid_price": "",
                "quote_ask_price": "",
                "quote_size_shares": "",
                "book_poll_status": "ok",
                "book_snapshot_rows": "0",
                "trade_poll_status": "ok",
                "trade_prints_returned": "0",
                "covered": "True",
            }
        ],
        fieldnames=maker_fill_replay.COLLECTION_WINDOW_FIELDS,
    )
    sentinel_bytes = ledger_path.read_bytes()

    monkeypatch.setattr(maker_fill_replay.time, "sleep", lambda _: None)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")

    def fake_get(url, params=None, timeout=None):
        return _Response(
            {
                "asset_id": "tok1",
                "timestamp": 2000,
                "hash": "h1",
                "bids": [{"price": "0.48", "size": "20"}],
                "asks": [{"price": "0.52", "size": "20"}],
            }
        )

    monkeypatch.setattr(maker_fill_replay.requests, "get", fake_get)

    maker_fill_replay.snapshot_official_books(cfg, scope="portfolio")

    assert ledger_path.read_bytes() == sentinel_bytes


def test_wo149_portfolio_scope_never_writes_delisted_markers_even_on_404(tmp_path, monkeypatch):
    # Test (4): sentinel delisted markers byte-identical when a portfolio
    # token 404s; delisted_marker_write == "skipped_portfolio_scope".
    cfg = _config(tmp_path)
    _seed_maker_portfolio(cfg)
    marker_path = cfg.output_root / "maker_carry" / "delisted_token_markers.json"
    write_json(
        marker_path,
        {
            "work_order": "WO-131",
            "generated_at_utc": "sentinel",
            "reporting_only": True,
            "skip_threshold": 3,
            "cooldown_hours": 24.0,
            "tokens": {},
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )
    sentinel_bytes = marker_path.read_bytes()

    monkeypatch.setattr(maker_fill_replay.time, "sleep", lambda _: None)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")

    def fake_get(url, params=None, timeout=None):
        raise maker_fill_replay.requests.HTTPError("404 Client Error: Not Found for url: /book")

    monkeypatch.setattr(maker_fill_replay.requests, "get", fake_get)

    summary = maker_fill_replay.snapshot_official_books(cfg, scope="portfolio")

    assert summary["status"] == "failed"
    assert summary["delisted_marker_write"] == "skipped_portfolio_scope"
    assert marker_path.read_bytes() == sentinel_bytes


def test_wo149_portfolio_scope_still_skips_a_token_in_cooldown(tmp_path, monkeypatch):
    # Test (5): a token in cooldown is still skipped under portfolio scope,
    # even though this scope never writes the marker file.
    cfg = _config(tmp_path)
    write_json(
        cfg.output_root / "maker_carry" / "maker_carry_study.json",
        {
            "generated_at_utc": "2026-07-27T02:00:00Z",
            "portfolio": [
                {"condition_id": "0xdead", "token_id": "tokD", "quote_size_shares": 10, "quote_distance": 0.01}
            ],
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )
    write_csv(
        cfg.output_root / "maker_carry" / "maker_carry_candidates.csv",
        [{"condition_id": "0xdead", "token_id": "tokD", "adverse_selection_usd_per_day": 2.0}],
        fieldnames=["condition_id", "token_id", "adverse_selection_usd_per_day"],
    )
    write_json(
        cfg.output_root / "maker_carry" / "delisted_token_markers.json",
        {"tokens": {"tokD": {"condition_id": "0xdead", "consecutive_404s": 3, "last_404_utc": "2026-07-27T02:00:00Z"}}},
    )

    monkeypatch.setattr(maker_fill_replay.time, "sleep", lambda _: None)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-27T03:00:00Z")
    polled: list[str] = []

    def fake_get(url, params=None, timeout=None):
        polled.append((params or {}).get("token_id"))
        return _Response(
            {
                "asset_id": "tokD",
                "timestamp": 2000,
                "hash": "h1",
                "bids": [{"price": "0.48", "size": "20"}],
                "asks": [{"price": "0.52", "size": "20"}],
            }
        )

    monkeypatch.setattr(maker_fill_replay.requests, "get", fake_get)

    summary = maker_fill_replay.snapshot_official_books(cfg, scope="portfolio")

    assert summary["status"] == "no_portfolio"
    assert polled == []
    assert summary["markets_polled"] == 0


def test_wo149_invalid_scope_raises_before_any_network_or_write(tmp_path, monkeypatch):
    # Test (6): scope="garbage" raises, no file written, no HTTP call.
    cfg = _config(tmp_path)
    _seed_maker_portfolio(cfg)
    calls: list[int] = []
    monkeypatch.setattr(maker_fill_replay.requests, "get", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(maker_fill_replay.requests, "post", lambda *a, **k: calls.append(1))

    with pytest.raises(ValueError):
        maker_fill_replay.snapshot_official_books(cfg, scope="garbage")

    assert calls == []
    assert not (cfg.output_root / "maker_carry" / "official_book_snapshot.json").exists()
    assert not (cfg.output_root / "maker_carry" / "official_book_pulse.json").exists()


def test_wo149_portfolio_scope_empty_portfolio_makes_zero_http_calls(tmp_path, monkeypatch):
    # Test (7): empty portfolio -> no_portfolio, zero HTTP calls, watchlist
    # snapshot untouched.
    cfg = _config(tmp_path)
    write_json(
        cfg.output_root / "maker_carry" / "maker_carry_study.json",
        {"portfolio": [], "paper_trading_invoked": False, "live_trading_invoked": False},
    )
    sentinel_path = cfg.output_root / "maker_carry" / "official_book_snapshot.json"
    write_json(sentinel_path, {"sentinel": True})
    sentinel_bytes = sentinel_path.read_bytes()
    calls: list[int] = []
    monkeypatch.setattr(maker_fill_replay.requests, "get", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(maker_fill_replay.requests, "post", lambda *a, **k: calls.append(1))

    summary = maker_fill_replay.snapshot_official_books(cfg, scope="portfolio")

    assert summary["status"] == "no_portfolio"
    assert calls == []
    assert sentinel_path.read_bytes() == sentinel_bytes


def test_wo149_pulse_and_collector_dedupe_the_shared_official_books_file(tmp_path, monkeypatch):
    # Test (8): dedup - pulse then collector at the SAME generated_at_utc adds
    # exactly one row (same observation_timestamp+hash key); a later pulse at
    # a DIFFERENT stamp adds a second, distinct row.
    cfg = _config(tmp_path)
    _seed_maker_portfolio(cfg)
    monkeypatch.setattr(maker_fill_replay.time, "sleep", lambda _: None)

    def fake_get(url, params=None, timeout=None):
        return _Response(
            {
                "asset_id": "tok1",
                "timestamp": 1_000,
                "hash": "h1",
                "bids": [{"price": "0.48", "size": "20"}],
                "asks": [{"price": "0.52", "size": "20"}],
            }
        )

    monkeypatch.setattr(maker_fill_replay.requests, "get", fake_get)
    book_path = cfg.output_root / "maker_carry" / "official_books" / "0xcond.csv.gz"

    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")
    maker_fill_replay.snapshot_official_books(cfg, scope="portfolio")
    maker_fill_replay.snapshot_official_books(cfg)  # watchlist collector, same generated_at_utc

    assert len(maker_fill_replay._read_csv_any(book_path)) == 1

    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:30:00Z")
    maker_fill_replay.snapshot_official_books(cfg, scope="portfolio")

    assert len(maker_fill_replay._read_csv_any(book_path)) == 2


def test_wo149_entry_state_lag_percentiles_are_nearest_rank_hand_computed():
    # Test (9): trades at t=0,100,400s; states at t=-50,-200,-350s -> lags
    # [50,300,750]; p50==300.0, p90==750.0, max==750.0, fills_beyond_legacy_lag==0.
    portfolio = [
        {
            "condition_id": f"0x{name}",
            "token_id": f"tok{name.upper()}",
            "quote_size_shares": 10.0,
            "quote_distance": 0.01,
            "quote_bid_price": 0.49,
            "quote_ask_price": 0.51,
        }
        for name in ("a", "b", "c")
    ]
    trades = [
        {"token_id": "tokA", "side": "BUY", "price": 0.52, "size": 20.0, "stamp": 0.0},
        {"token_id": "tokB", "side": "BUY", "price": 0.52, "size": 20.0, "stamp": 100.0},
        {"token_id": "tokC", "side": "BUY", "price": 0.52, "size": 20.0, "stamp": 400.0},
    ]
    states = {
        "tokA": [{"stamp": -50.0, "midpoint": 0.50, "resting_ask_depth_at_quote": 0.0}],
        "tokB": [{"stamp": -200.0, "midpoint": 0.50, "resting_ask_depth_at_quote": 0.0}],
        "tokC": [{"stamp": -350.0, "midpoint": 0.50, "resting_ask_depth_at_quote": 0.0}],
    }

    result = maker_fill_replay._replay_against_states(
        source="official",
        states_by_token=states,
        trades=trades,
        portfolio=portfolio,
        study_charge=1.0,
        study_charge_by_condition={"0xa": 1 / 3, "0xb": 1 / 3, "0xc": 1 / 3},
        max_state_lag_seconds=1800.0,
    )

    assert result["confirmed_fills"] == 3
    lags = sorted(fill["entry_state_lag_seconds"] for fill in result["fills_preview"])
    assert lags == [50.0, 300.0, 750.0]
    assert result["entry_state_lag_seconds_p50"] == 300.0
    assert result["entry_state_lag_seconds_p90"] == 750.0
    assert result["entry_state_lag_seconds_max"] == 750.0
    assert result["fills_beyond_legacy_lag"] == 0
    assert result["entry_state_lag_unmeasurable"] == 0


def test_wo149_fills_beyond_legacy_lag_uses_the_fixed_1800_literal(tmp_path):
    # Test (10): tolerance monkeypatched to 3600 with one fill at a 2000s lag
    # -> fills_beyond_legacy_lag == 1, proving the counter works against the
    # 1800.0 literal without this WO changing the deployed tolerance value.
    portfolio = [
        {
            "condition_id": "0xa",
            "token_id": "tokA",
            "quote_size_shares": 10.0,
            "quote_distance": 0.01,
            "quote_bid_price": 0.49,
            "quote_ask_price": 0.51,
        }
    ]
    trades = [{"token_id": "tokA", "side": "BUY", "price": 0.52, "size": 20.0, "stamp": 2000.0}]
    states = {"tokA": [{"stamp": 0.0, "midpoint": 0.50, "resting_ask_depth_at_quote": 0.0}]}

    result = maker_fill_replay._replay_against_states(
        source="official",
        states_by_token=states,
        trades=trades,
        portfolio=portfolio,
        study_charge=1.0,
        study_charge_by_condition={"0xa": 1.0},
        max_state_lag_seconds=3600.0,
    )

    assert result["confirmed_fills"] == 1
    assert result["fills_preview"][0]["entry_state_lag_seconds"] == 2000.0
    assert result["fills_beyond_legacy_lag"] == 1


def test_wo149_non_finite_state_stamp_is_excluded_and_counted_unmeasurable():
    # Test (11): a book row whose timestamp parses to nan -> excluded from
    # the percentile population, entry_state_lag_unmeasurable == 1, and the
    # p90 is computed over the finite remainder.
    portfolio = [
        {
            "condition_id": f"0x{name}",
            "token_id": token,
            "quote_size_shares": 10.0,
            "quote_distance": 0.01,
            "quote_bid_price": 0.49,
            "quote_ask_price": 0.51,
        }
        for name, token in (("a", "tokA"), ("b", "tokB"), ("nan", "tokNan"))
    ]
    trades = [
        {"token_id": "tokA", "side": "BUY", "price": 0.52, "size": 20.0, "stamp": 100.0},
        {"token_id": "tokB", "side": "BUY", "price": 0.52, "size": 20.0, "stamp": 300.0},
        {"token_id": "tokNan", "side": "BUY", "price": 0.52, "size": 20.0, "stamp": 3000.0},
    ]
    states = {
        "tokA": [{"stamp": 0.0, "midpoint": 0.50, "resting_ask_depth_at_quote": 0.0}],
        "tokB": [{"stamp": 0.0, "midpoint": 0.50, "resting_ask_depth_at_quote": 0.0}],
        "tokNan": [{"stamp": float("nan"), "midpoint": 0.50, "resting_ask_depth_at_quote": 0.0}],
    }

    result = maker_fill_replay._replay_against_states(
        source="official",
        states_by_token=states,
        trades=trades,
        portfolio=portfolio,
        study_charge=1.0,
        study_charge_by_condition={"0xa": 1 / 3, "0xb": 1 / 3, "0xnan": 1 / 3},
        max_state_lag_seconds=1800.0,
    )

    assert result["confirmed_fills"] == 3
    assert result["entry_state_lag_unmeasurable"] == 1
    assert result["entry_state_lag_seconds_p90"] == 300.0
    assert result["entry_state_lag_seconds_max"] == 300.0


def test_wo149_empty_lag_population_is_null_not_zero():
    # Test (12): empty population -> p50/p90/max are null, not 0.0.
    portfolio = [
        {
            "condition_id": "0xa",
            "token_id": "tokA",
            "quote_size_shares": 10.0,
            "quote_distance": 0.01,
            "quote_bid_price": 0.49,
            "quote_ask_price": 0.51,
        }
    ]
    trades = [{"token_id": "tokA", "side": "BUY", "price": 0.52, "size": 20.0, "stamp": 100.0}]

    result = maker_fill_replay._replay_against_states(
        source="official",
        states_by_token={"tokA": []},
        trades=trades,
        portfolio=portfolio,
        study_charge=1.0,
        study_charge_by_condition={"0xa": 1.0},
        max_state_lag_seconds=1800.0,
    )

    assert result["confirmed_fills"] == 0
    assert result["entry_state_lag_seconds_p50"] is None
    assert result["entry_state_lag_seconds_p90"] is None
    assert result["entry_state_lag_seconds_max"] is None
    assert result["entry_state_lag_unmeasurable"] == 0
    assert result["fills_beyond_legacy_lag"] == 0


def test_wo149_no_contemporaneous_state_rate_uses_relevant_trade_denominator():
    # Test (13): no_contemporaneous_state_rate - 4 relevant trades, 1 miss ->
    # 0.25; zero relevant trades -> null.
    portfolio = [
        {
            "condition_id": "0xa",
            "token_id": "tokA",
            "quote_size_shares": 10.0,
            "quote_distance": 0.01,
            "quote_bid_price": 0.49,
            "quote_ask_price": 0.51,
        },
        {
            "condition_id": "0xb",
            "token_id": "tokB",
            "quote_size_shares": 10.0,
            "quote_distance": 0.01,
            "quote_bid_price": 0.49,
            "quote_ask_price": 0.51,
        },
    ]
    trades = [
        {"token_id": "tokA", "side": "BUY", "price": 0.40, "size": 5.0, "stamp": 10.0},
        {"token_id": "tokA", "side": "BUY", "price": 0.40, "size": 5.0, "stamp": 20.0},
        {"token_id": "tokA", "side": "BUY", "price": 0.40, "size": 5.0, "stamp": 30.0},
        {"token_id": "tokB", "side": "BUY", "price": 0.40, "size": 5.0, "stamp": 15.0},
    ]
    states = {"tokA": [{"stamp": 0.0, "midpoint": 0.50, "resting_ask_depth_at_quote": 20.0}], "tokB": []}

    result = maker_fill_replay._replay_against_states(
        source="official",
        states_by_token=states,
        trades=trades,
        portfolio=portfolio,
        study_charge=1.0,
        study_charge_by_condition={"0xa": 0.5, "0xb": 0.5},
        max_state_lag_seconds=1800.0,
        quote_sheet_generated_stamp=10_000.0,
        quoting_basis="contemporaneous",
    )

    assert result["no_contemporaneous_state_opportunities"] == 1
    assert result["no_contemporaneous_state_rate"] == 0.25

    empty_result = maker_fill_replay._replay_against_states(
        source="official",
        states_by_token={},
        trades=[],
        portfolio=portfolio,
        study_charge=1.0,
        study_charge_by_condition={"0xa": 0.5, "0xb": 0.5},
        max_state_lag_seconds=1800.0,
    )
    assert empty_result["no_contemporaneous_state_rate"] is None


def test_wo149_max_book_state_lag_seconds_byte_identity(tmp_path):
    # Test (14): tolerance byte-identity.
    cfg = _config(tmp_path)
    settings = maker_fill_replay._settings(cfg)
    assert settings["max_book_state_lag_seconds"] == 1800

    example_config_text = Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8")
    assert "max_book_state_lag_seconds: 1800" in example_config_text


def test_wo149_maker_policy_defaults_byte_identity():
    # Test (15): threshold byte-identity - the four MAKER_POLICY_DEFAULTS
    # values this WO explicitly does not move.
    from polymarket_predictive_engine.maker_carry_study import MAKER_POLICY_DEFAULTS

    assert MAKER_POLICY_DEFAULTS["maker_min_book_history_hours"] == 48.0
    assert MAKER_POLICY_DEFAULTS["maker_min_book_snapshots"] == 100
    assert MAKER_POLICY_DEFAULTS["target_net_usd_per_day"] == 3.33
    assert MAKER_POLICY_DEFAULTS["max_trusted_reward_share"] == 0.05


def test_wo149_snapshot_official_books_caller_set_is_exhaustively_scanned():
    # Test (16): A3/A9 - exhaustive scan roots anchored off __file__, a
    # non-zero visit count, and the production (src/) caller set is exactly
    # the two internal maker_fill_replay.py callers plus the two cli.py
    # command branches. scripts/ and .github/ (deployed automation, not
    # Python callers) contribute zero call sites - proving the scan would
    # catch a stray caller there, not just assert a hand-picked total.
    root = Path(__file__).resolve().parents[2]
    roots = {
        "src": root / "src",
        "scripts": root / "scripts",
        "tests": root / "tests",
        ".github": root / ".github",
    }
    call_pattern = re.compile(r"(?<!def )snapshot_official_books\(")

    visited_files = 0
    calls_by_root: dict[str, list[str]] = {name: [] for name in roots}
    for name, scan_root in roots.items():
        if not scan_root.exists():
            continue
        for path in scan_root.rglob("*.py"):
            # Exclude ROOT/".claude" specifically. Checking ".claude" against
            # the full absolute path is wrong here: this worktree itself is
            # checked out under ".../.claude/worktrees/...", so every single
            # file's absolute path contains ".claude" as an ANCESTOR of ROOT,
            # not just an excluded subdirectory of the repo - that bug would
            # silently exclude every file and pass `visited_files > 0`
            # vacuously false, exactly the A3 failure mode this rule guards.
            if path.relative_to(root).parts[0] == ".claude":
                continue
            visited_files += 1
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in call_pattern.finditer(text):
                line_no = text.count("\n", 0, match.start()) + 1
                calls_by_root[name].append(f"{path.name}:{line_no}")

    assert visited_files > 0
    assert calls_by_root["scripts"] == []
    assert calls_by_root[".github"] == []
    src_calls = calls_by_root["src"]
    assert len(src_calls) == 4
    assert sum(1 for site in src_calls if site.startswith("maker_fill_replay.py:")) == 2
    assert sum(1 for site in src_calls if site.startswith("cli.py:")) == 2


def test_wo149_s4_portfolio_pulse_against_a_recorded_books_and_book_response(tmp_path, monkeypatch):
    # S4: at least one recorded-reality fixture. A sanitised real POST
    # /books response (clob_books_2026-07-15.json) resolves the first
    # market via the batch endpoint; a sanitised real GET /book response
    # (clob_book_2026-07-15.json) resolves the second via the serial
    # fallback - both recorded 2026-07-15, not hand-written.
    recorded_books = _recorded_fixture("clob_books_2026-07-15.json")
    recorded_book = _recorded_fixture("clob_book_2026-07-15.json")
    batch_token = recorded_books[0]["asset_id"]

    cfg = _config(tmp_path)
    write_json(
        cfg.output_root / "maker_carry" / "maker_carry_study.json",
        {
            "generated_at_utc": "2026-07-15T18:30:00Z",
            "portfolio": [
                {"condition_id": "0xbatch", "token_id": batch_token, "quote_size_shares": 10, "quote_distance": 0.01},
                {"condition_id": "0xfallback", "token_id": "tokFallback", "quote_size_shares": 10, "quote_distance": 0.01},
            ],
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )
    write_csv(
        cfg.output_root / "maker_carry" / "maker_carry_candidates.csv",
        [
            {"condition_id": "0xbatch", "token_id": batch_token, "adverse_selection_usd_per_day": 2.0},
            {"condition_id": "0xfallback", "token_id": "tokFallback", "adverse_selection_usd_per_day": 2.0},
        ],
        fieldnames=["condition_id", "token_id", "adverse_selection_usd_per_day"],
    )
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-15T18:30:00Z")
    monkeypatch.setattr(maker_fill_replay.time, "sleep", lambda _: None)
    post_calls: list[Any] = []
    get_calls: list[Any] = []

    def fake_post(url, json=None, timeout=None):
        post_calls.append(json)
        assert url.endswith("/books")
        return _Response(recorded_books)

    def fake_get(url, params=None, timeout=None):
        get_calls.append(params)
        assert url.endswith("/book")
        return _Response(recorded_book)

    monkeypatch.setattr(maker_fill_replay.requests, "post", fake_post)
    monkeypatch.setattr(maker_fill_replay.requests, "get", fake_get)

    summary = maker_fill_replay.snapshot_official_books(cfg, scope="portfolio")

    assert summary["status"] == "ok"
    assert len(post_calls) == 1
    assert len(get_calls) == 1
    assert get_calls[0] == {"token_id": "tokFallback"}

    batch_rows = maker_fill_replay._read_csv_any(cfg.output_root / "maker_carry" / "official_books" / "0xbatch.csv.gz")
    fallback_rows = maker_fill_replay._read_csv_any(
        cfg.output_root / "maker_carry" / "official_books" / "0xfallback.csv.gz"
    )
    assert float(batch_rows[0]["best_bid"]) == 0.25
    assert float(batch_rows[0]["best_ask"]) == 0.26
    assert batch_rows[0]["hash"] == "recorded-sanitized-book-hash"
    assert float(fallback_rows[0]["best_bid"]) == 0.25
    assert float(fallback_rows[0]["best_ask"]) == 0.26


def test_wo149_cli_pulse_command_dispatches_portfolio_scope(tmp_path, monkeypatch):
    # Bonus (beyond the enumerated 25): confirms the CLI branch actually
    # passes scope="portfolio", complementing the static scan in test (16).
    from polymarket_predictive_engine import cli as engine_cli

    calls: list[str] = []

    def fake_snapshot(cfg, *, scope="watchlist"):
        calls.append(scope)
        return {"status": "ok"}

    monkeypatch.setattr(engine_cli, "snapshot_official_books", fake_snapshot)
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    assert "snapshot-official-books-pulse" in engine_cli.COMMANDS
    exit_code = engine_cli.main(["snapshot-official-books-pulse", "--config", str(cfg_path)])

    assert exit_code == 0
    assert calls == ["portfolio"]


def test_wo149_run_maker_fill_replay_publishes_lag_fields_at_top_level(tmp_path):
    # Bonus: the Day-after check reads these fields directly off
    # maker_fill_replay.json's top level, not just off the internal replay
    # result - confirm run_maker_fill_replay actually propagates them.
    cfg = _config(tmp_path)
    _seed_maker_portfolio(cfg)
    _seed_archive(cfg)
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [{"market": "0xcond", "asset_id": "tok1", "side": "SELL", "price": 0.49, "size": 25, "timestamp": 1_000}],
        fieldnames=["market", "asset_id", "side", "price", "size", "timestamp"],
    )

    summary = run_maker_fill_replay(cfg)

    assert summary["confirmed_fills"] == 1
    assert summary["entry_state_lag_seconds_p50"] == 0.0
    assert summary["entry_state_lag_seconds_p90"] == 0.0
    assert summary["entry_state_lag_seconds_max"] == 0.0
    assert summary["fills_beyond_legacy_lag"] == 0
    assert summary["entry_state_lag_unmeasurable"] == 0
    assert summary["no_contemporaneous_state_rate"] == 0.0


# ===========================================================================
# WO-148: the maker-watchlist tier-assignment event ledger.
# Tests (1)-(16) are 148.5's own enumeration; tests (17)-(30) are §148.6's
# appended enumeration. Numbering follows the register, not file order.
# ===========================================================================


def _wo148_config(tmp_path: Path, **overrides: Any):
    """A `snapshot_official_books` config with roomy tranche caps (25 each,
    matching the deployed caps §148.6 reasons about) so small watchlist
    fixtures are never accidentally truncated. Overrides mutate the loaded
    config's own `raw` dict directly - no YAML round-trip, so passing a
    YAML-boolean-alias string such as `"false"` or `"no"` for `enabled`
    cannot be silently reinterpreted as a real boolean by the dumper.
    """

    cfg = _config(tmp_path)
    cfg.raw["maker_fill_replay"].update(
        {
            "book_source": "official",
            "request_pause_seconds": 0,
            "regime_days": 7,
            "max_markets": 25,
            "max_persistent_markets": 25,
            "max_candidate_markets": 25,
        }
    )
    cfg.raw["maker_fill_replay"].update(overrides)
    return cfg


def _wo148_portfolio_entries(pairs) -> list[dict[str, Any]]:
    return [
        {"condition_id": cid, "token_id": tok, "quote_size_shares": 10, "quote_distance": 0.01}
        for cid, tok in pairs
    ]


def _wo148_study(cfg, portfolio_pairs=(), *, generated_at_utc: str = "2026-07-10T00:00:00Z") -> None:
    write_json(
        cfg.output_root / "maker_carry" / "maker_carry_study.json",
        {
            "generated_at_utc": generated_at_utc,
            "portfolio": _wo148_portfolio_entries(portfolio_pairs),
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )


def _wo148_candidates_and_books(
    cfg,
    *,
    candidates=(),
    persistent_books=(),
    write_candidates_csv: bool = True,
    write_books_dir: bool = True,
) -> None:
    out = cfg.output_root / "maker_carry"
    if write_candidates_csv:
        write_csv(
            out / "maker_carry_candidates.csv",
            [{"condition_id": cid, "token_id": tok, "net_carry_usd_per_day": "1.0"} for cid, tok in candidates],
            fieldnames=["condition_id", "token_id", "net_carry_usd_per_day"],
        )
    if write_books_dir:
        (out / "official_books").mkdir(parents=True, exist_ok=True)
    for cid, tok in persistent_books:
        _recent_book_file(cfg, cid, tok)


def _wo148_deny_network(monkeypatch) -> None:
    def _boom(*args, **kwargs):
        raise RuntimeError("WO-148 tests must not perform network calls")

    monkeypatch.setattr(maker_fill_replay.requests, "post", _boom)
    monkeypatch.setattr(maker_fill_replay.requests, "get", _boom)
    monkeypatch.setattr(maker_fill_replay.time, "sleep", lambda *_: None)


def _tier_events_path(cfg) -> Path:
    return cfg.output_root / "maker_carry" / maker_fill_replay.TIER_EVENTS_CSV_NAME


def _tier_state_path(cfg) -> Path:
    return cfg.output_root / "maker_carry" / maker_fill_replay.TIER_STATE_JSON_NAME


def _seed_tier_events(cfg, rows) -> None:
    append_csv_rows(_tier_events_path(cfg), rows, fieldnames=maker_fill_replay.TIER_EVENT_FIELDS)


def _seed_tier_state(cfg, *, generated_at_utc: str, **extra: Any) -> None:
    payload = {
        "generated_at_utc": generated_at_utc,
        "work_order": "WO-148",
        "watchlist_size": 0,
        "tier_counts": {"portfolio": 0, "persistent": 0, "seed": 0},
        "reporting_only": True,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    payload.update(extra)
    write_json(_tier_state_path(cfg), payload)


def test_wo148_test1_genesis_emits_arrivals_from_absent_for_the_whole_watchlist(tmp_path, monkeypatch):
    # Test (1): 2 portfolio + 1 persistent + 3 seed -> exactly 6 rows, header
    # exactly as registered, every previous_tier == "absent".
    cfg = _wo148_config(tmp_path)
    _wo148_study(cfg, [("0xp1", "tokP1"), ("0xp2", "tokP2")])
    _wo148_candidates_and_books(
        cfg,
        candidates=[
            ("0xp1", "tokP1"), ("0xp2", "tokP2"), ("0xr1", "tokR1"),
            ("0xs1", "tokS1"), ("0xs2", "tokS2"), ("0xs3", "tokS3"),
        ],
        persistent_books=[("0xr1", "tokR1")],
    )
    _wo148_deny_network(monkeypatch)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")
    # A fresh state file keeps this run out of resync (the same device test
    # (17) uses) so genesis rows are stamped "absent", not "unknown" - resync
    # itself is exercised separately by tests (6)-(8).
    _seed_tier_state(cfg, generated_at_utc="2026-07-14T12:00:00Z")

    summary = maker_fill_replay.snapshot_official_books(cfg)

    assert summary["tier_events_status"] == "ok"
    assert summary["tier_events_written"] == 6
    assert summary["tier_events_resync"] is False
    assert summary["tier_events_malformed_rows"] == 0
    assert summary["tier_event_burst"] is False
    assert summary["tier_precedence_conflicts"] == 0
    events_path = _tier_events_path(cfg)
    assert csv_columns(events_path) == maker_fill_replay.TIER_EVENT_FIELDS
    rows = read_csv_rows(events_path)
    assert len(rows) == 6
    assert {row["condition_id"] for row in rows} == {"0xp1", "0xp2", "0xr1", "0xs1", "0xs2", "0xs3"}
    assert all(row["previous_tier"] == "absent" for row in rows)
    tiers = {row["condition_id"]: row["tier"] for row in rows}
    assert tiers["0xp1"] == tiers["0xp2"] == "portfolio"
    assert tiers["0xr1"] == "persistent"
    assert tiers["0xs1"] == tiers["0xs2"] == tiers["0xs3"] == "seed"


def test_wo148_test2_idempotent_rerun_appends_nothing_and_bytes_match(tmp_path, monkeypatch):
    # Test (2): re-run identical -> 0 rows, file bytes byte-identical.
    cfg = _wo148_config(tmp_path)
    _wo148_study(cfg, [("0xp1", "tokP1")])
    _wo148_candidates_and_books(cfg, candidates=[("0xp1", "tokP1")])
    _wo148_deny_network(monkeypatch)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")
    _seed_tier_state(cfg, generated_at_utc="2026-07-14T12:00:00Z")

    first = maker_fill_replay.snapshot_official_books(cfg)
    assert first["tier_events_written"] == 1
    events_path = _tier_events_path(cfg)
    bytes_after_first = events_path.read_bytes()

    second = maker_fill_replay.snapshot_official_books(cfg)

    assert second["tier_events_written"] == 0
    assert second["tier_events_status"] == "ok"
    assert events_path.read_bytes() == bytes_after_first


def test_wo148_test3_promotion_seed_to_portfolio_emits_one_row(tmp_path, monkeypatch):
    # Test (3): promotion seed -> portfolio -> exactly 1 row.
    cfg = _wo148_config(tmp_path)
    _wo148_deny_network(monkeypatch)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")
    _seed_tier_state(cfg, generated_at_utc="2026-07-14T12:00:00Z")

    _wo148_study(cfg, [])
    _wo148_candidates_and_books(cfg, candidates=[("0xmove", "tokMove")])
    first = maker_fill_replay.snapshot_official_books(cfg)
    assert first["tier_events_written"] == 1

    _wo148_study(cfg, [("0xmove", "tokMove")])
    second = maker_fill_replay.snapshot_official_books(cfg)

    assert second["tier_events_written"] == 1
    rows = read_csv_rows(_tier_events_path(cfg))
    assert rows[-1]["condition_id"] == "0xmove"
    assert rows[-1]["previous_tier"] == "seed"
    assert rows[-1]["tier"] == "portfolio"


def test_wo148_test4_departure_emits_absent_row(tmp_path, monkeypatch):
    # Test (4): departure -> 1 row, tier == "absent".
    cfg = _wo148_config(tmp_path)
    _wo148_deny_network(monkeypatch)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")
    _seed_tier_state(cfg, generated_at_utc="2026-07-14T12:00:00Z")

    _wo148_study(cfg, [("0xleave", "tokLeave"), ("0xstay", "tokStay")])
    _wo148_candidates_and_books(cfg, candidates=[("0xleave", "tokLeave"), ("0xstay", "tokStay")])
    first = maker_fill_replay.snapshot_official_books(cfg)
    assert first["tier_events_written"] == 2

    # 0xleave genuinely departs - dropped from the portfolio AND the
    # candidate ranking, so it cannot reappear as a seed this cycle.
    _wo148_study(cfg, [("0xstay", "tokStay")])
    _wo148_candidates_and_books(cfg, candidates=[("0xstay", "tokStay")])
    second = maker_fill_replay.snapshot_official_books(cfg)

    assert second["tier_events_written"] == 1
    rows = read_csv_rows(_tier_events_path(cfg))
    assert rows[-1] == {
        "event_utc": "2026-07-14T12:00:00Z",
        "condition_id": "0xleave",
        "token_id": "tokLeave",
        "previous_tier": "portfolio",
        "tier": "absent",
    }


def test_wo148_test5_reentry_seed_absent_seed_produces_three_rows(tmp_path, monkeypatch):
    # Test (5): re-entry seed -> absent -> seed -> three rows; a conversion
    # computation sees two distinct seed entries.
    cfg = _wo148_config(tmp_path)
    _wo148_deny_network(monkeypatch)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")
    _seed_tier_state(cfg, generated_at_utc="2026-07-14T12:00:00Z")
    _wo148_study(cfg, [])  # portfolio stays empty throughout; only seed flickers.

    _wo148_candidates_and_books(cfg, candidates=[("0xflicker", "tokFlicker")])
    c1 = maker_fill_replay.snapshot_official_books(cfg)
    assert c1["tier_events_written"] == 1

    _wo148_candidates_and_books(cfg, candidates=[])
    c2 = maker_fill_replay.snapshot_official_books(cfg)
    assert c2["tier_events_written"] == 1

    _wo148_candidates_and_books(cfg, candidates=[("0xflicker", "tokFlicker")])
    c3 = maker_fill_replay.snapshot_official_books(cfg)
    assert c3["tier_events_written"] == 1

    rows = read_csv_rows(_tier_events_path(cfg))
    flicker_rows = [row for row in rows if row["condition_id"] == "0xflicker"]
    assert [row["tier"] for row in flicker_rows] == ["seed", "absent", "seed"]
    assert [row["previous_tier"] for row in flicker_rows] == ["absent", "seed", "absent"]
    seed_entries = [row for row in flicker_rows if row["tier"] == "seed"]
    assert len(seed_entries) == 2


def test_wo148_test6_missing_state_file_stamps_unknown_and_sets_resync(tmp_path, monkeypatch):
    # Test (6): state missing -> all (emitted) rows "unknown", resync True.
    cfg = _wo148_config(tmp_path)
    _wo148_deny_network(monkeypatch)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")
    _wo148_study(cfg, [])
    _wo148_candidates_and_books(cfg, candidates=[("0xseed1", "tokSeed1")])
    # Prior tier established via the ledger's OWN last row, not the state
    # file - deliberately a DIFFERENT tier than this cycle computes, so a
    # real transition exists to observe the resync stamping on.
    _seed_tier_events(
        cfg,
        [
            {
                "event_utc": "2026-07-10T00:00:00Z",
                "condition_id": "0xseed1",
                "token_id": "tokSeed1",
                "previous_tier": "absent",
                "tier": "portfolio",
            }
        ],
    )
    assert not _tier_state_path(cfg).exists()

    summary = maker_fill_replay.snapshot_official_books(cfg)

    assert summary["tier_events_resync"] is True
    assert summary["tier_events_written"] == 1
    rows = read_csv_rows(_tier_events_path(cfg))
    assert rows[-1]["previous_tier"] == "unknown"
    assert rows[-1]["tier"] == "seed"


def test_wo148_test7_state_age_boundary_at_21600_seconds(tmp_path, monkeypatch):
    # Test (7), S1 clock-advance pair: state at now-21599s -> real prior
    # tier; advance the clock to now-21601s -> "unknown".
    def _run(root: Path, *, offset_seconds: float):
        root.mkdir()
        cfg = _wo148_config(root)
        _wo148_study(cfg, [])
        _wo148_candidates_and_books(cfg, candidates=[("0xclock", "tokClock")])
        _seed_tier_events(
            cfg,
            [
                {
                    "event_utc": "2026-07-14T00:00:00Z",
                    "condition_id": "0xclock",
                    "token_id": "tokClock",
                    "previous_tier": "absent",
                    "tier": "portfolio",
                }
            ],
        )
        _seed_tier_state(cfg, generated_at_utc="2026-07-14T00:00:00Z")
        run_stamp = (parse_timestamp("2026-07-14T00:00:00Z") + timedelta(seconds=offset_seconds)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        return cfg, run_stamp

    _wo148_deny_network(monkeypatch)

    cfg_within, stamp_within = _run(tmp_path / "within", offset_seconds=21599)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: stamp_within)
    within = maker_fill_replay.snapshot_official_books(cfg_within)
    assert within["tier_events_resync"] is False
    assert read_csv_rows(_tier_events_path(cfg_within))[-1]["previous_tier"] == "portfolio"

    cfg_beyond, stamp_beyond = _run(tmp_path / "beyond", offset_seconds=21601)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: stamp_beyond)
    beyond = maker_fill_replay.snapshot_official_books(cfg_beyond)
    assert beyond["tier_events_resync"] is True
    assert read_csv_rows(_tier_events_path(cfg_beyond))[-1]["previous_tier"] == "unknown"


def test_wo148_test8_unparseable_absent_and_future_state_all_resync(tmp_path, monkeypatch):
    # Test (8): state unparseable, absent, and future-by-60s -> "unknown" in
    # all three.
    def _base(root: Path):
        root.mkdir()
        cfg = _wo148_config(root)
        _wo148_study(cfg, [])
        _wo148_candidates_and_books(cfg, candidates=[("0xstate", "tokState")])
        _seed_tier_events(
            cfg,
            [
                {
                    "event_utc": "2026-07-14T00:00:00Z",
                    "condition_id": "0xstate",
                    "token_id": "tokState",
                    "previous_tier": "absent",
                    "tier": "portfolio",
                }
            ],
        )
        return cfg

    _wo148_deny_network(monkeypatch)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T00:10:00Z")

    cfg_unparseable = _base(tmp_path / "unparseable")
    _tier_state_path(cfg_unparseable).parent.mkdir(parents=True, exist_ok=True)
    _tier_state_path(cfg_unparseable).write_text("{not json", encoding="utf-8")
    unparseable = maker_fill_replay.snapshot_official_books(cfg_unparseable)
    assert unparseable["tier_events_resync"] is True
    assert read_csv_rows(_tier_events_path(cfg_unparseable))[-1]["previous_tier"] == "unknown"

    cfg_absent = _base(tmp_path / "absent")
    assert not _tier_state_path(cfg_absent).exists()
    absent = maker_fill_replay.snapshot_official_books(cfg_absent)
    assert absent["tier_events_resync"] is True
    assert read_csv_rows(_tier_events_path(cfg_absent))[-1]["previous_tier"] == "unknown"

    cfg_future = _base(tmp_path / "future")
    _seed_tier_state(cfg_future, generated_at_utc="2026-07-14T00:11:00Z")  # 60s in the future
    future = maker_fill_replay.snapshot_official_books(cfg_future)
    assert future["tier_events_resync"] is True
    assert read_csv_rows(_tier_events_path(cfg_future))[-1]["previous_tier"] == "unknown"


def test_wo148_test9_unreadable_ledger_read_failed(tmp_path, monkeypatch):
    # Test (9): unreadable ledger -> 0 rows, "read_failed", state not
    # written, collector still returns normally.
    cfg = _wo148_config(tmp_path)
    _wo148_deny_network(monkeypatch)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")
    _wo148_study(cfg, [("0xp1", "tokP1")])
    _wo148_candidates_and_books(cfg, candidates=[("0xp1", "tokP1")])
    events_path = _tier_events_path(cfg)
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.mkdir()  # a directory at the ledger's own path is unreadable as a CSV
    state_path = _tier_state_path(cfg)

    summary = maker_fill_replay.snapshot_official_books(cfg)

    assert summary["tier_events_status"] == "read_failed"
    assert summary["tier_events_written"] == 0
    assert not state_path.exists()
    assert summary["status"] in {"ok", "partial", "failed"}  # the collector itself still ran
    assert events_path.is_dir()  # left untouched, never converted into a file


def test_wo148_test10_append_raises_write_failed_state_not_written(tmp_path, monkeypatch):
    # Test (10): append_csv_rows monkeypatched to raise -> "write_failed",
    # state not written, return otherwise unchanged.
    cfg = _wo148_config(tmp_path)
    _wo148_deny_network(monkeypatch)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")
    _wo148_study(cfg, [("0xp1", "tokP1")])
    _wo148_candidates_and_books(cfg, candidates=[("0xp1", "tokP1")])

    def _boom(*args, **kwargs):
        raise OSError("simulated append failure")

    monkeypatch.setattr(maker_fill_replay, "append_csv_rows", _boom)

    summary = maker_fill_replay.snapshot_official_books(cfg)

    assert summary["tier_events_status"] == "write_failed"
    assert summary["tier_events_written"] == 0
    assert not _tier_state_path(cfg).exists()
    assert summary["portfolio_markets"] == 1
    assert summary["markets_polled"] == 1


def test_wo148_test11_malformed_rows_ignored_counted_prefix_preserved(tmp_path, monkeypatch):
    # Test (11): malformed historical rows -> ignored, counted, and the file
    # prefix preceding the append is byte-identical (the WO-115 regression
    # guard).
    cfg = _wo148_config(tmp_path)
    _wo148_deny_network(monkeypatch)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")
    _wo148_study(cfg, [("0xp1", "tokP1"), ("0xp2", "tokP2")])
    _wo148_candidates_and_books(cfg, candidates=[("0xp1", "tokP1"), ("0xp2", "tokP2")])
    _seed_tier_state(cfg, generated_at_utc="2026-07-14T12:00:00Z")

    events_path = _tier_events_path(cfg)
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text(
        "event_utc,condition_id,token_id,previous_tier,tier\n"
        "2026-07-01T00:00:00Z,0xp1,tokP1,absent,portfolio\n"
        "2026-07-01T00:00:00Z,,tokBad,absent,portfolio\n"
        "2026-07-01T00:00:00Z,0xbadtier,tokBad2,absent,not_a_tier\n",
        encoding="utf-8",
    )
    prefix_before = events_path.read_bytes()

    summary = maker_fill_replay.snapshot_official_books(cfg)

    assert summary["tier_events_malformed_rows"] == 2
    assert summary["tier_events_written"] == 1  # only 0xp2 is a genuine new arrival
    assert events_path.read_bytes().startswith(prefix_before)


def test_wo148_test12_burst_of_201_transitions_all_written_and_flagged(tmp_path):
    # Test (12): 201 transitions -> all 201 written, tier_event_burst True.
    out_root = tmp_path / "maker_carry"
    out_root.mkdir(parents=True)
    portfolio = [{"condition_id": f"0xburst{i:04d}", "token_id": f"tokBurst{i:04d}"} for i in range(201)]

    result = maker_fill_replay._write_tier_events(out_root, "2026-07-14T12:00:00Z", portfolio, [], [])

    assert result["tier_events_status"] == "ok"
    assert result["tier_events_written"] == 201
    assert result["tier_event_burst"] is True
    rows = read_csv_rows(out_root / maker_fill_replay.TIER_EVENTS_CSV_NAME)
    assert len(rows) == 201


def test_wo148_test13_precedence_conflict_portfolio_wins_and_is_counted(tmp_path):
    # Test (13): precedence conflict -> registered precedence applied,
    # tier_precedence_conflicts == 1.
    out_root = tmp_path / "maker_carry"
    out_root.mkdir(parents=True)
    portfolio = [{"condition_id": "0xdupe", "token_id": "tokDupe"}]
    persistent = [{"condition_id": "0xdupe", "token_id": "tokDupeStale"}]

    result = maker_fill_replay._write_tier_events(out_root, "2026-07-14T12:00:00Z", portfolio, persistent, [])

    assert result["tier_precedence_conflicts"] == 1
    rows = read_csv_rows(out_root / maker_fill_replay.TIER_EVENTS_CSV_NAME)
    assert len(rows) == 1
    assert rows[0]["tier"] == "portfolio"


def test_wo148_test14_selection_invariance_ledger_write_does_not_move_the_watchlist(tmp_path, monkeypatch):
    # Test (14): selection invariance - the exact watchlist (order and
    # membership) and market_polls are identical whether or not the WO-148
    # ledger sidecar actually writes anything, proving no selection
    # behaviour moved.
    def _build(root: Path):
        root.mkdir()
        cfg = _wo148_config(root)
        _wo148_study(cfg, [("0xp1", "tokP1")])
        _wo148_candidates_and_books(
            cfg,
            candidates=[("0xp1", "tokP1"), ("0xs1", "tokS1")],
            persistent_books=[("0xr1", "tokR1")],
        )
        return cfg

    cfg_control = _build(tmp_path / "control")
    cfg_live = _build(tmp_path / "live")
    _wo148_deny_network(monkeypatch)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")

    def _noop_tier_events(*_args, **_kwargs):
        return {
            "tier_events_status": "ok",
            "tier_events_written": 0,
            "tier_events_resync": False,
            "tier_events_malformed_rows": 0,
            "tier_event_burst": False,
            "tier_precedence_conflicts": 0,
        }

    with monkeypatch.context() as m:
        m.setattr(maker_fill_replay, "_write_tier_events", _noop_tier_events)
        control = maker_fill_replay.snapshot_official_books(cfg_control)
    live = maker_fill_replay.snapshot_official_books(cfg_live)

    tier_keys = {
        "tier_events_status",
        "tier_events_written",
        "tier_events_resync",
        "tier_events_malformed_rows",
        "tier_event_burst",
        "tier_precedence_conflicts",
    }

    def _comparable(summary):
        trimmed = {k: v for k, v in summary.items() if k not in tier_keys}
        trimmed["files_written"] = [Path(p).name for p in trimmed["files_written"]]
        return trimmed

    assert _comparable(control) == _comparable(live)
    assert [poll["condition_id"] for poll in control["market_polls"]] == [
        poll["condition_id"] for poll in live["market_polls"]
    ]


def test_wo148_test15_ledger_artifacts_are_not_enrolled():
    # Test (15): non-enrolment guard - neither new path appears in
    # DEFAULT_LEDGER_REGISTRY nor the example config's ledger_globs.
    from polymarket_predictive_engine.ledger_anchor import DEFAULT_LEDGER_REGISTRY

    registered_globs = {entry["glob"] for entry in DEFAULT_LEDGER_REGISTRY}
    assert f"maker_carry/{maker_fill_replay.TIER_EVENTS_CSV_NAME}" not in registered_globs
    assert f"maker_carry/{maker_fill_replay.TIER_STATE_JSON_NAME}" not in registered_globs

    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    example_globs = {entry["glob"] for entry in raw["ledger_anchor"]["ledger_globs"]}
    assert f"maker_carry/{maker_fill_replay.TIER_EVENTS_CSV_NAME}" not in example_globs
    assert f"maker_carry/{maker_fill_replay.TIER_STATE_JSON_NAME}" not in example_globs


def test_wo148_test16_ledger_path_string_and_caller_set_are_exhaustively_scanned():
    # Test (16), corrected per §148.6 reconciliation item 6: the ledger CSV
    # path string appears in exactly one non-test source file, at exactly
    # one call site (its own module-level constant definition), and
    # `snapshot_official_books(` is called from exactly the four §148.6
    # table sites, split 2 (maker_fill_replay.py) + 2 (cli.py) - the
    # unamended 148.5 expectation ("exactly :957, :968, and cli.py:431",
    # three sites) is superseded.
    root = Path(__file__).resolve().parents[2]
    roots = {
        "src": root / "src",
        "scripts": root / "scripts",
        "tests": root / "tests",
        ".github": root / ".github",
    }
    call_pattern = re.compile(r"(?<!def )snapshot_official_books\(")
    ledger_pattern = re.compile(re.escape(maker_fill_replay.TIER_EVENTS_CSV_NAME))

    visited_files = 0
    caller_hits: dict[str, list[str]] = {name: [] for name in roots}
    ledger_hits: dict[str, list[str]] = {name: [] for name in roots}
    for name, scan_root in roots.items():
        if not scan_root.exists():
            continue
        for path in scan_root.rglob("*.py"):
            if path.relative_to(root).parts[0] == ".claude":
                continue
            visited_files += 1
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in call_pattern.finditer(text):
                line_no = text.count("\n", 0, match.start()) + 1
                caller_hits[name].append(f"{path.name}:{line_no}")
            for match in ledger_pattern.finditer(text):
                line_no = text.count("\n", 0, match.start()) + 1
                ledger_hits[name].append(f"{path.name}:{line_no}")

    assert visited_files > 0
    src_calls = caller_hits["src"]
    assert len(src_calls) == 4
    assert sum(1 for site in src_calls if site.startswith("maker_fill_replay.py:")) == 2
    assert sum(1 for site in src_calls if site.startswith("cli.py:")) == 2
    assert caller_hits["scripts"] == []
    assert caller_hits[".github"] == []

    non_test_ledger_hits = ledger_hits["src"] + ledger_hits["scripts"] + ledger_hits[".github"]
    assert len(non_test_ledger_hits) == 1
    assert non_test_ledger_hits[0].startswith("maker_fill_replay.py:")


def test_wo148_test17_portfolio_scope_appends_nothing_despite_prior_membership(tmp_path, monkeypatch):
    # Test (17): scope="portfolio" with a non-empty portfolio and prior
    # persistent/seed membership seeded as rows in the events CSV appends
    # ZERO rows and the events file is byte-identical before and after. This
    # is the case that fails against the unamended spec and is the reason
    # §148.6 exists - see the discrimination proof recorded in this build's
    # report.
    cfg = _wo148_config(tmp_path)
    _wo148_deny_network(monkeypatch)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")
    _wo148_study(cfg, [("0xport", "tokPort")])
    _wo148_candidates_and_books(cfg, candidates=[("0xport", "tokPort")])
    _seed_tier_events(
        cfg,
        [
            {
                "event_utc": "2026-07-10T00:00:00Z",
                "condition_id": "0xport",
                "token_id": "tokPort",
                "previous_tier": "absent",
                "tier": "portfolio",
            },
            {
                "event_utc": "2026-07-10T00:00:00Z",
                "condition_id": "0xpersist",
                "token_id": "tokPersist",
                "previous_tier": "absent",
                "tier": "persistent",
            },
            {
                "event_utc": "2026-07-10T00:00:00Z",
                "condition_id": "0xseeded",
                "token_id": "tokSeeded",
                "previous_tier": "absent",
                "tier": "seed",
            },
        ],
    )
    _seed_tier_state(cfg, generated_at_utc="2026-07-14T12:00:00Z")
    events_before = _tier_events_path(cfg).read_bytes()
    state_before = _tier_state_path(cfg).read_bytes()

    summary = maker_fill_replay.snapshot_official_books(cfg, scope="portfolio")

    assert summary["tier_events_status"] == "skipped_portfolio_scope"
    assert summary["tier_events_written"] == 0
    assert _tier_events_path(cfg).read_bytes() == events_before
    assert _tier_state_path(cfg).read_bytes() == state_before


def test_wo148_test18_pulse_and_snapshot_each_carry_all_six_typed_keys(tmp_path, monkeypatch):
    # Test (18): both artifacts carry all six tier-event keys with
    # tier_events_status a member of the closed six-value domain and the
    # other five typed int/bool - checked per artifact, never by comparing
    # one artifact's values against the other's.
    cfg = _wo148_config(tmp_path)
    _wo148_deny_network(monkeypatch)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")
    _wo148_study(cfg, [("0xport", "tokPort")])
    _wo148_candidates_and_books(cfg, candidates=[("0xport", "tokPort")])
    _seed_tier_state(cfg, generated_at_utc="2026-07-14T12:00:00Z")

    maker_fill_replay.snapshot_official_books(cfg, scope="portfolio")
    maker_fill_replay.snapshot_official_books(cfg)  # scope="watchlist"

    pulse = read_json(cfg.output_root / "maker_carry" / "official_book_pulse.json")
    snapshot = read_json(cfg.output_root / "maker_carry" / "official_book_snapshot.json")
    domain = {
        "ok",
        "read_failed",
        "write_failed",
        "skipped_portfolio_scope",
        "skipped_unreadable_inputs",
        "skipped_disabled",
    }
    for artifact in (pulse, snapshot):
        assert isinstance(artifact["tier_events_status"], str)
        assert artifact["tier_events_status"] in domain
        assert isinstance(artifact["tier_events_written"], int)
        assert isinstance(artifact["tier_events_resync"], bool)
        assert isinstance(artifact["tier_events_malformed_rows"], int)
        assert isinstance(artifact["tier_event_burst"], bool)
        assert isinstance(artifact["tier_precedence_conflicts"], int)
    assert pulse["scope"] == "portfolio"
    assert pulse["tier_events_status"] == "skipped_portfolio_scope"
    assert snapshot["tier_events_status"] == "ok"


def test_wo148_test19_no_state_file_written_under_portfolio_scope(tmp_path, monkeypatch):
    # Test (19): no state file is written under portfolio scope - a
    # pre-existing state file is byte-identical after the call, and an
    # absent one is still absent.
    cfg = _wo148_config(tmp_path)
    _wo148_deny_network(monkeypatch)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")
    _wo148_study(cfg, [("0xport", "tokPort")])
    _wo148_candidates_and_books(cfg, candidates=[("0xport", "tokPort")])

    assert not _tier_state_path(cfg).exists()
    maker_fill_replay.snapshot_official_books(cfg, scope="portfolio")
    assert not _tier_state_path(cfg).exists()

    _seed_tier_state(cfg, generated_at_utc="2020-01-01T00:00:00Z")
    sentinel_bytes = _tier_state_path(cfg).read_bytes()
    maker_fill_replay.snapshot_official_books(cfg, scope="portfolio")
    assert _tier_state_path(cfg).read_bytes() == sentinel_bytes


def test_wo148_test20_scheduler_order_interleaving_produces_no_spurious_churn(tmp_path, monkeypatch):
    # Test (20): interleaving in the real scheduler order - watchlist cycle
    # (genesis rows) -> portfolio pulse (zero rows) -> portfolio pulse (zero
    # rows) -> watchlist cycle with no tier change (zero rows). Total rows
    # equal the genesis count.
    cfg = _wo148_config(tmp_path)
    _wo148_deny_network(monkeypatch)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")
    _wo148_study(cfg, [("0xp1", "tokP1")])
    _wo148_candidates_and_books(
        cfg,
        candidates=[("0xp1", "tokP1"), ("0xs1", "tokS1")],
        persistent_books=[("0xr1", "tokR1")],
    )
    _seed_tier_state(cfg, generated_at_utc="2026-07-14T12:00:00Z")

    watchlist_1 = maker_fill_replay.snapshot_official_books(cfg)  # run_trade_prints
    genesis_written = watchlist_1["tier_events_written"]
    assert genesis_written == 3

    pulse_1 = maker_fill_replay.snapshot_official_books(cfg, scope="portfolio")  # run_book_pulse
    pulse_2 = maker_fill_replay.snapshot_official_books(cfg, scope="portfolio")  # run_book_pulse
    watchlist_2 = maker_fill_replay.snapshot_official_books(cfg)  # run_trade_prints, unchanged

    assert pulse_1["tier_events_written"] == 0
    assert pulse_2["tier_events_written"] == 0
    assert watchlist_2["tier_events_written"] == 0
    assert len(read_csv_rows(_tier_events_path(cfg))) == genesis_written


def test_wo148_test21_watchlist_scope_behaviour_is_unchanged_by_148_6(tmp_path, monkeypatch):
    # Test (21): scope="watchlist" behaviour is unchanged by this amendment.
    # Tests (1)-(16) above ARE 148.5's list run under the §148.6 guard, per
    # the register's own "asserted by running them rather than by argument."
    # This is a minimal, self-contained re-confirmation in one place.
    cfg = _wo148_config(tmp_path)
    _wo148_deny_network(monkeypatch)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")
    _wo148_study(cfg, [("0xp1", "tokP1")])
    _wo148_candidates_and_books(cfg, candidates=[("0xp1", "tokP1")])
    _seed_tier_state(cfg, generated_at_utc="2026-07-14T12:00:00Z")

    summary = maker_fill_replay.snapshot_official_books(cfg, scope="watchlist")

    assert summary["tier_events_status"] == "ok"
    assert summary["tier_events_written"] == 1


def test_wo148_test22_portfolio_scope_empty_portfolio_still_carries_six_keys(tmp_path, monkeypatch):
    # Test (22): scope="portfolio" with an EMPTY portfolio takes the
    # no_portfolio early return - the path the write-site anchor never
    # reaches - and the summary still carries all six tier-event keys with
    # tier_events_status == "skipped_portfolio_scope".
    cfg = _wo148_config(tmp_path)
    _wo148_deny_network(monkeypatch)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")
    _wo148_study(cfg, [])
    _wo148_candidates_and_books(cfg, candidates=[])

    summary = maker_fill_replay.snapshot_official_books(cfg, scope="portfolio")

    assert summary["status"] == "no_portfolio"
    assert summary["tier_events_status"] == "skipped_portfolio_scope"
    assert summary["tier_events_written"] == 0
    assert summary["tier_events_resync"] is False
    assert summary["tier_events_malformed_rows"] == 0
    assert summary["tier_event_burst"] is False
    assert summary["tier_precedence_conflicts"] == 0
    assert not _tier_state_path(cfg).exists()


def test_wo148_test23_watchlist_scope_genuinely_empty_watchlist_runs_the_diff(tmp_path, monkeypatch):
    # Test (23): scope="watchlist", readable maker_carry_study.json,
    # sequential cycles - nonempty (genesis) -> truly empty (one departure
    # row) -> re-entry at the same tier (one arrival row). Against the
    # unamended (pre-item-2) behaviour both later cycles take the
    # no_portfolio shortcut and emit ZERO rows each.
    cfg = _wo148_config(tmp_path)
    _wo148_deny_network(monkeypatch)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")
    _seed_tier_state(cfg, generated_at_utc="2026-07-14T12:00:00Z")
    _wo148_study(cfg, [])

    _wo148_candidates_and_books(cfg, candidates=[("0xebb", "tokEbb")])
    c1 = maker_fill_replay.snapshot_official_books(cfg)
    assert c1["tier_events_written"] == 1
    assert c1["tier_events_status"] == "ok"

    _wo148_candidates_and_books(cfg, candidates=[])
    c2 = maker_fill_replay.snapshot_official_books(cfg)
    assert c2["status"] == "no_portfolio"
    assert c2["tier_events_status"] == "ok"
    assert c2["tier_events_written"] == 1
    rows_after_c2 = read_csv_rows(_tier_events_path(cfg))
    assert rows_after_c2[-1] == {
        "event_utc": "2026-07-14T12:00:00Z",
        "condition_id": "0xebb",
        "token_id": "tokEbb",
        "previous_tier": "seed",
        "tier": "absent",
    }

    _wo148_candidates_and_books(cfg, candidates=[("0xebb", "tokEbb")])
    c3 = maker_fill_replay.snapshot_official_books(cfg)
    assert c3["tier_events_written"] == 1
    rows_after_c3 = read_csv_rows(_tier_events_path(cfg))
    assert rows_after_c3[-1]["previous_tier"] == "absent"
    assert rows_after_c3[-1]["tier"] == "seed"


def test_wo148_test24_missing_or_unparseable_study_json_skips_the_ledger(tmp_path, monkeypatch):
    # Test (24): scope="watchlist", maker_carry_study.json missing (and,
    # separately, present but unparseable) with the watchlist empty as a
    # consequence - the ledger write is skipped, zero rows appended,
    # "skipped_unreadable_inputs".
    _wo148_deny_network(monkeypatch)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")

    root_missing = tmp_path / "missing"
    root_missing.mkdir()
    cfg_missing = _wo148_config(root_missing)
    _wo148_candidates_and_books(cfg_missing, candidates=[])
    assert not (cfg_missing.output_root / "maker_carry" / "maker_carry_study.json").exists()
    _seed_tier_state(cfg_missing, generated_at_utc="2020-01-01T00:00:00Z")
    sentinel_missing = _tier_state_path(cfg_missing).read_bytes()

    missing_summary = maker_fill_replay.snapshot_official_books(cfg_missing)

    assert missing_summary["status"] == "no_portfolio"
    assert missing_summary["tier_events_status"] == "skipped_unreadable_inputs"
    assert missing_summary["tier_events_written"] == 0
    assert _tier_state_path(cfg_missing).read_bytes() == sentinel_missing

    root_bad = tmp_path / "unparseable"
    root_bad.mkdir()
    cfg_bad = _wo148_config(root_bad)
    _wo148_candidates_and_books(cfg_bad, candidates=[])
    study_path = cfg_bad.output_root / "maker_carry" / "maker_carry_study.json"
    study_path.parent.mkdir(parents=True, exist_ok=True)
    study_path.write_text("{not json", encoding="utf-8")
    _seed_tier_state(cfg_bad, generated_at_utc="2020-01-01T00:00:00Z")
    sentinel_bad = _tier_state_path(cfg_bad).read_bytes()

    bad_summary = maker_fill_replay.snapshot_official_books(cfg_bad)

    assert bad_summary["status"] == "no_portfolio"
    assert bad_summary["tier_events_status"] == "skipped_unreadable_inputs"
    assert bad_summary["tier_events_written"] == 0
    assert _tier_state_path(cfg_bad).read_bytes() == sentinel_bad


@pytest.mark.parametrize("disabled_value", ["false", "0", "no"])
def test_wo148_test25_disabled_watchlist_scope_never_reads_study_json(tmp_path, monkeypatch, disabled_value):
    # Test (25): scope="watchlist" with `enabled` falsy never reaches
    # maker_carry_study.json - mocked to raise if read, to prove the read
    # stage is never entered - appends zero rows, writes no state file, and
    # tier_events_status == "skipped_disabled" on official_book_snapshot.json.
    cfg = _wo148_config(tmp_path, enabled=disabled_value)
    _wo148_deny_network(monkeypatch)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")

    def _boom_read_json(*_args, **_kwargs):
        raise AssertionError("disabled collector must never read maker_carry_study.json")

    monkeypatch.setattr(maker_fill_replay, "read_json", _boom_read_json)

    summary = maker_fill_replay.snapshot_official_books(cfg)

    assert summary["status"] == "disabled"
    assert summary["tier_events_status"] == "skipped_disabled"
    assert summary["tier_events_written"] == 0
    persisted = read_json(cfg.output_root / "maker_carry" / "official_book_snapshot.json")
    assert persisted["tier_events_status"] == "skipped_disabled"
    assert not _tier_events_path(cfg).exists()
    assert not _tier_state_path(cfg).exists()


@pytest.mark.parametrize("disabled_value", ["false", "0", "no"])
def test_wo148_test26_disabled_portfolio_scope_reports_skipped_disabled_not_scope(tmp_path, monkeypatch, disabled_value):
    # Test (26): the same disabled config under scope="portfolio" records
    # tier_events_status == "skipped_disabled", not "skipped_portfolio_scope"
    # - the priority case, since the disabled guard fires before the
    # scope-based one.
    cfg = _wo148_config(tmp_path, enabled=disabled_value)
    _wo148_deny_network(monkeypatch)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")

    summary = maker_fill_replay.snapshot_official_books(cfg, scope="portfolio")

    assert summary["status"] == "disabled"
    assert summary["tier_events_status"] == "skipped_disabled"
    persisted = read_json(cfg.output_root / "maker_carry" / "official_book_pulse.json")
    assert persisted["tier_events_status"] == "skipped_disabled"


@pytest.mark.parametrize(
    "study_payload",
    [
        {},
        {"other_field": 1},
        {"portfolio": None},
        {"portfolio": 5},
        {"portfolio": "not-a-list"},
    ],
)
def test_wo148_test27_contract_invalid_summary_shapes_skip_the_ledger(tmp_path, monkeypatch, study_payload):
    # Test (27): scope="watchlist", maker_carry_study.json present and valid
    # JSON but failing the summary contract (bare {}, missing "portfolio",
    # or "portfolio" present but not a list) with portfolio+persistent+seeds
    # computing to empty as a consequence - skipped, zero rows,
    # "skipped_unreadable_inputs", no unhandled exception.
    cfg = _wo148_config(tmp_path)
    _wo148_deny_network(monkeypatch)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")
    write_json(
        cfg.output_root / "maker_carry" / "maker_carry_study.json",
        {**study_payload, "paper_trading_invoked": False, "live_trading_invoked": False},
    )
    _wo148_candidates_and_books(cfg, candidates=[])
    _seed_tier_state(cfg, generated_at_utc="2020-01-01T00:00:00Z")
    sentinel = _tier_state_path(cfg).read_bytes()

    summary = maker_fill_replay.snapshot_official_books(cfg)

    assert summary["status"] == "no_portfolio"
    assert summary["tier_events_status"] == "skipped_unreadable_inputs"
    assert summary["tier_events_written"] == 0
    assert _tier_state_path(cfg).read_bytes() == sentinel


def test_wo148_test28_missing_candidates_csv_skips_even_with_nonempty_watchlist(tmp_path, monkeypatch):
    # Test (28): a readable, contract-valid, empty-portfolio summary,
    # persistent markets present on disk, but maker_carry_candidates.csv
    # ABSENT - skipped, zero rows, even though portfolio+persistent+seeds is
    # nonempty (from persistent alone).
    cfg = _wo148_config(tmp_path)
    _wo148_deny_network(monkeypatch)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")
    _wo148_study(cfg, [])
    _recent_book_file(cfg, "0xpersist", "tokPersist")
    assert not (cfg.output_root / "maker_carry" / "maker_carry_candidates.csv").exists()
    _seed_tier_state(cfg, generated_at_utc="2020-01-01T00:00:00Z")
    sentinel = _tier_state_path(cfg).read_bytes()

    summary = maker_fill_replay.snapshot_official_books(cfg)

    assert summary["markets_polled"] == 1
    assert summary["tier_events_status"] == "skipped_unreadable_inputs"
    assert summary["tier_events_written"] == 0
    assert _tier_state_path(cfg).read_bytes() == sentinel


def test_wo148_test28b_present_but_empty_candidates_csv_is_genuinely_ok(tmp_path, monkeypatch):
    # Test (28), second half: the SAME fixture with the candidates CSV
    # present but genuinely listing zero rows takes the "ok" path - proving
    # the check distinguishes "file missing" from "file present and
    # genuinely empty".
    cfg = _wo148_config(tmp_path)
    _wo148_deny_network(monkeypatch)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")
    _wo148_study(cfg, [])
    _recent_book_file(cfg, "0xpersist", "tokPersist")
    _wo148_candidates_and_books(cfg, candidates=[])  # present, header-only, zero rows
    _seed_tier_state(cfg, generated_at_utc="2026-07-14T12:00:00Z")

    summary = maker_fill_replay.snapshot_official_books(cfg)

    assert summary["tier_events_status"] == "ok"


def test_wo148_test29_missing_official_books_dir_skips_even_with_nonempty_watchlist(tmp_path, monkeypatch):
    # Test (29), symmetric case for the official_books directory: seed
    # markets present (a populated candidates CSV) but the official_books
    # directory ABSENT - skipped, zero rows appended, even though the
    # watchlist is nonempty (from the seed tranche alone).
    cfg = _wo148_config(tmp_path)
    _wo148_deny_network(monkeypatch)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")
    _wo148_study(cfg, [])
    _wo148_candidates_and_books(cfg, candidates=[("0xseed1", "tokSeed1")], write_books_dir=False)
    assert not (cfg.output_root / "maker_carry" / "official_books").exists()
    _seed_tier_state(cfg, generated_at_utc="2020-01-01T00:00:00Z")
    sentinel = _tier_state_path(cfg).read_bytes()

    summary = maker_fill_replay.snapshot_official_books(cfg)

    assert summary["markets_polled"] == 1
    assert summary["tier_events_status"] == "skipped_unreadable_inputs"
    assert summary["tier_events_written"] == 0
    assert _tier_state_path(cfg).read_bytes() == sentinel


def test_wo148_test29b_present_but_empty_official_books_dir_is_genuinely_ok(tmp_path, monkeypatch):
    # Test (29), second half: the directory present but genuinely empty of
    # matching files takes the "ok" path - the same missing-vs-empty
    # distinction test (28) draws for the candidates CSV.
    cfg = _wo148_config(tmp_path)
    _wo148_deny_network(monkeypatch)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")
    _wo148_study(cfg, [])
    _wo148_candidates_and_books(cfg, candidates=[("0xseed1", "tokSeed1")], write_books_dir=True)
    _seed_tier_state(cfg, generated_at_utc="2026-07-14T12:00:00Z")

    summary = maker_fill_replay.snapshot_official_books(cfg)

    assert summary["tier_events_status"] == "ok"


@pytest.mark.parametrize("portfolio_field", [[{}], ["a", "b"]])
def test_wo148_test30_all_entries_failing_shape_test_is_contract_invalid(tmp_path, monkeypatch, portfolio_field):
    # Test (30): a portfolio field that IS a list still fails the contract if
    # every entry fails the per-entry shape test - [{}] (a dict with none of
    # the four required fields) and ["a", "b"] (no entry a dict at all), with
    # persistent and seed empty too - both skip, zero rows.
    cfg = _wo148_config(tmp_path)
    _wo148_deny_network(monkeypatch)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")
    write_json(
        cfg.output_root / "maker_carry" / "maker_carry_study.json",
        {"portfolio": portfolio_field, "paper_trading_invoked": False, "live_trading_invoked": False},
    )
    _wo148_candidates_and_books(cfg, candidates=[])
    _seed_tier_state(cfg, generated_at_utc="2020-01-01T00:00:00Z")
    sentinel = _tier_state_path(cfg).read_bytes()

    summary = maker_fill_replay.snapshot_official_books(cfg)

    assert summary["tier_events_status"] == "skipped_unreadable_inputs"
    assert summary["tier_events_written"] == 0
    assert _tier_state_path(cfg).read_bytes() == sentinel


def test_wo148_test30b_mixed_portfolio_list_is_not_contract_invalid(tmp_path, monkeypatch):
    # Test (30), second half: a MIXED portfolio list - one well-formed entry
    # alongside a {} and a string - is NOT contract-invalid; the diff runs
    # normally against the single surviving portfolio entry.
    cfg = _wo148_config(tmp_path)
    _wo148_deny_network(monkeypatch)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")
    write_json(
        cfg.output_root / "maker_carry" / "maker_carry_study.json",
        {
            "portfolio": [
                {"condition_id": "0xgood", "token_id": "tokGood", "quote_size_shares": 10, "quote_distance": 0.01},
                {},
                "not-a-dict",
            ],
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )
    _wo148_candidates_and_books(cfg, candidates=[("0xgood", "tokGood")])
    # 0xgood was already on the prior watchlist as a seed, so the diff has a
    # real transition to prove it actually ran against the surviving entry.
    _seed_tier_events(
        cfg,
        [
            {
                "event_utc": "2026-07-10T00:00:00Z",
                "condition_id": "0xgood",
                "token_id": "tokGood",
                "previous_tier": "absent",
                "tier": "seed",
            }
        ],
    )
    _seed_tier_state(cfg, generated_at_utc="2026-07-14T12:00:00Z")

    summary = maker_fill_replay.snapshot_official_books(cfg)

    assert summary["tier_events_status"] == "ok"
    assert summary["tier_events_written"] == 1
    rows = read_csv_rows(_tier_events_path(cfg))
    assert rows[-1] == {
        "event_utc": "2026-07-14T12:00:00Z",
        "condition_id": "0xgood",
        "token_id": "tokGood",
        "previous_tier": "seed",
        "tier": "portfolio",
    }


def _wo148_zero_transition_fixture(cfg, monkeypatch) -> None:
    """A steady-state cycle where the current watchlist exactly matches the
    ledger's last recorded tier for every condition id, so the diff (148.3(6))
    emits ZERO rows and `append_csv_rows` therefore short-circuits before
    touching disk (`utils.py:191-192`) - the case the register's Day-after
    check (2) requires and the case B1's fix round exists to protect, since
    it makes the subsequent `write_json` state-file write the cycle's first
    disk write.
    """

    _wo148_deny_network(monkeypatch)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")
    _wo148_study(cfg, [("0xp1", "tokP1")])
    _wo148_candidates_and_books(cfg, candidates=[("0xp1", "tokP1")])
    _seed_tier_events(
        cfg,
        [
            {
                "event_utc": "2026-07-10T00:00:00Z",
                "condition_id": "0xp1",
                "token_id": "tokP1",
                "previous_tier": "absent",
                "tier": "portfolio",
            }
        ],
    )


def test_wo148_b1_state_write_raising_on_zero_transition_cycle_reports_write_failed(tmp_path, monkeypatch):
    # B1 (blocker): on a zero-transition cycle, append_csv_rows never touches
    # disk (rows == []), so write_json(state_path, ...) is the cycle's first
    # write. A raise there (simulated here; ENOSPC/EROFS/permission on the
    # real VPS) must be caught and reported "write_failed", exactly like an
    # append_csv_rows failure - not escape snapshot_official_books.
    cfg = _wo148_config(tmp_path)
    _wo148_zero_transition_fixture(cfg, monkeypatch)
    _seed_tier_state(cfg, generated_at_utc="2026-07-14T12:00:00Z")
    events_before = _tier_events_path(cfg).read_bytes()
    state_path = _tier_state_path(cfg)
    real_write_json = maker_fill_replay.write_json

    def _raise_on_state_path(path, payload):
        if Path(path) == state_path:
            raise OSError("simulated state write failure (ENOSPC/EROFS/permission)")
        return real_write_json(path, payload)

    monkeypatch.setattr(maker_fill_replay, "write_json", _raise_on_state_path)

    summary = maker_fill_replay.snapshot_official_books(cfg)  # must not raise

    assert summary["tier_events_status"] == "write_failed"
    assert summary["tier_events_written"] == 0
    assert _tier_events_path(cfg).read_bytes() == events_before
    assert summary["status"] in {"ok", "partial", "failed"}  # the collector itself still ran
    snapshot_path = cfg.output_root / "maker_carry" / "official_book_snapshot.json"
    assert snapshot_path.exists()
    persisted = read_json(snapshot_path)
    assert persisted["tier_events_status"] == "write_failed"


def test_wo148_b1_state_path_is_a_directory_reports_write_failed(tmp_path, monkeypatch):
    # B1, second fixture: the state path exists as a directory on disk (the
    # concrete real-world case named in the audit) instead of being raised
    # via monkeypatch - same containment, same assertions.
    cfg = _wo148_config(tmp_path)
    _wo148_zero_transition_fixture(cfg, monkeypatch)
    state_path = _tier_state_path(cfg)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.mkdir()  # a directory at the state file's own path
    events_before = _tier_events_path(cfg).read_bytes()

    summary = maker_fill_replay.snapshot_official_books(cfg)  # must not raise

    assert summary["tier_events_status"] == "write_failed"
    assert summary["tier_events_written"] == 0
    assert _tier_events_path(cfg).read_bytes() == events_before
    assert summary["status"] in {"ok", "partial", "failed"}  # the collector itself still ran
    assert state_path.is_dir()  # left untouched, never converted into a file
    snapshot_path = cfg.output_root / "maker_carry" / "official_book_snapshot.json"
    assert snapshot_path.exists()
    persisted = read_json(snapshot_path)
    assert persisted["tier_events_status"] == "write_failed"


def test_wo148_b1_state_write_raising_on_genesis_cycle_reports_true_event_count_and_burst(tmp_path, monkeypatch):
    # B1, third fixture: the two tests above both use the zero-transition
    # fixture (`len(event_rows) == 0`), so neither can tell
    # `"tier_events_written": len(event_rows)` / `"tier_event_burst":
    # len(event_rows) > _TIER_EVENT_BURST_THRESHOLD` (:915/:918, the second
    # write_failed branch, guarding the state write) apart from a regression
    # that collapses either to a literal `0` / `False` - both read 0 either
    # way. This fixture is a genesis cycle instead: the CSV append SUCCEEDS
    # with N=205 real transition rows (5 portfolio + 0 persistent + 200 seed,
    # hand-counted below), then the state write fails exactly as above.
    #
    # N is deliberately pushed past _TIER_EVENT_BURST_THRESHOLD (200, see
    # :721-725) by overriding max_candidate_markets far above the deployed
    # cap of 25 that the threshold comment says makes 200 "structurally
    # impossible" in production - the override exists purely to exercise
    # tier_event_burst's True arm at all, which no other test in this file
    # reaches under the deployed-cap-shaped default fixture.
    cfg = _wo148_config(tmp_path, max_candidate_markets=250)
    # Hand count: 5 portfolio pairs (0xp001..0xp005) + 200 seed candidates
    # (0xs001..0xs200, disjoint condition-id prefix, all within the
    # max_candidate_markets=250 cap) + 0 persistent (no official_books/*.csv.gz
    # written) = 205 total watchlist entries. The ledger is empty beforehand,
    # so EVERY entry's last tier defaults to "absent" and differs from its
    # current tier -> exactly 205 emitted rows. 205 > 200, so
    # tier_event_burst is True for this cycle.
    portfolio_pairs = [(f"0xp{i:03d}", f"tokP{i:03d}") for i in range(1, 6)]
    seed_pairs = [(f"0xs{i:03d}", f"tokS{i:03d}") for i in range(1, 201)]
    expected_n = len(portfolio_pairs) + len(seed_pairs)
    assert expected_n == 205
    _wo148_study(cfg, portfolio_pairs)
    _wo148_candidates_and_books(cfg, candidates=portfolio_pairs + seed_pairs)
    _wo148_deny_network(monkeypatch)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")
    _seed_tier_state(cfg, generated_at_utc="2026-07-14T12:00:00Z")
    state_path = _tier_state_path(cfg)
    real_write_json = maker_fill_replay.write_json

    def _raise_on_state_path(path, payload):
        if Path(path) == state_path:
            raise OSError("simulated state write failure (ENOSPC/EROFS/permission)")
        return real_write_json(path, payload)

    monkeypatch.setattr(maker_fill_replay, "write_json", _raise_on_state_path)

    summary = maker_fill_replay.snapshot_official_books(cfg)  # must not raise

    assert summary["tier_events_status"] == "write_failed"
    assert summary["tier_events_written"] == 205  # == expected_n, the true count, not 0
    assert summary["tier_event_burst"] is True  # 205 > 200 == _TIER_EVENT_BURST_THRESHOLD
    events_rows = read_csv_rows(_tier_events_path(cfg))
    assert len(events_rows) == 205  # the append landed on disk before the state write failed
    assert summary["status"] in {"ok", "partial", "failed"}  # the collector itself still ran
    snapshot_path = cfg.output_root / "maker_carry" / "official_book_snapshot.json"
    assert snapshot_path.exists()
    persisted = read_json(snapshot_path)
    assert persisted["tier_events_status"] == "write_failed"
    assert persisted["tier_events_written"] == 205
    assert persisted["tier_event_burst"] is True


def test_wo148_n2_malformed_portfolio_contract_raises_under_portfolio_scope_only(tmp_path, monkeypatch):
    # N2 (register fidelity): §148.6's contract-invalid-summary substitution
    # ("neither applies under scope=\"portfolio\", where the portfolio-scope
    # skip fires first") is gated on the SAME `portfolio_scope` boolean the
    # tranche computation already branches on - so a malformed
    # maker_carry_study.json (`portfolio: 5`, a truthy non-list scalar) still
    # raises the pre-existing loud TypeError _portfolio (:427-439) raises on
    # its raw slice under scope="portfolio", rather than being silently
    # substituted into a quiet "no_portfolio"/"skipped_portfolio_scope"
    # artifact. Watchlist scope keeps the substituted, non-raising behaviour
    # on the identical malformed payload (also covered, parametrized, by
    # test 27) - proving the gate is scope-specific, not a blanket change.
    cfg = _wo148_config(tmp_path)
    _wo148_deny_network(monkeypatch)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "2026-07-14T12:00:00Z")
    write_json(
        cfg.output_root / "maker_carry" / "maker_carry_study.json",
        {"portfolio": 5, "paper_trading_invoked": False, "live_trading_invoked": False},
    )
    _wo148_candidates_and_books(cfg, candidates=[])

    with pytest.raises(TypeError):
        maker_fill_replay.snapshot_official_books(cfg, scope="portfolio")

    summary = maker_fill_replay.snapshot_official_books(cfg)  # scope="watchlist" (default)

    assert summary["status"] == "no_portfolio"
    assert summary["tier_events_status"] == "skipped_unreadable_inputs"
    assert summary["tier_events_written"] == 0
