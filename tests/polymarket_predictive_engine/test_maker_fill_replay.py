"""WO-40 maker fill replay: last-in-queue realism for maker-carry."""
from __future__ import annotations

import csv
import gzip
import os
from pathlib import Path

import yaml

from polymarket_predictive_engine import maker_fill_replay
from polymarket_predictive_engine import trade_print_collector
from polymarket_predictive_engine.cli import COMMANDS
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.maker_fill_replay import run_maker_fill_replay
from polymarket_predictive_engine.utils import read_json, write_csv, write_json


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
