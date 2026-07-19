"""WO-40 maker fill replay: last-in-queue realism for maker-carry."""
from __future__ import annotations

import csv
import gzip
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
    ]
    _write_gzip_csv(
        cfg.output_root / "polymarket_training_archive" / "features_synthetic.csv.gz",
        [
            {"source_timestamp": 1_000, "asset_id": "tok1", "best_bid": 0.48, "best_ask": 0.52, "midpoint": 0.50, "top_bid_size": 20, "top_ask_size": 20},
            {"source_timestamp": 1_300, "asset_id": "tok1", "best_bid": 0.43, "best_ask": 0.47, "midpoint": 0.45, "top_bid_size": 20, "top_ask_size": 20},
            {"source_timestamp": 1_900, "asset_id": "tok1", "best_bid": 0.42, "best_ask": 0.46, "midpoint": 0.44, "top_bid_size": 20, "top_ask_size": 20},
            {"source_timestamp": 4_600, "asset_id": "tok1", "best_bid": 0.38, "best_ask": 0.42, "midpoint": 0.40, "top_bid_size": 20, "top_ask_size": 20},
        ],
        fields,
    )


def _seed_official_books(cfg) -> None:
    _write_gzip_csv(
        cfg.output_root / "maker_carry" / "official_books" / "0xcond.csv.gz",
        [
            {"condition_id": "0xcond", "source_timestamp": 1_000, "asset_id": "tok1", "best_bid": 0.48, "best_ask": 0.52, "midpoint": 0.50, "top_bid_size": 20, "top_ask_size": 20, "hash": "h1"},
            {"condition_id": "0xcond", "source_timestamp": 1_300, "asset_id": "tok1", "best_bid": 0.43, "best_ask": 0.47, "midpoint": 0.45, "top_bid_size": 20, "top_ask_size": 20, "hash": "h2"},
            {"condition_id": "0xcond", "source_timestamp": 1_900, "asset_id": "tok1", "best_bid": 0.42, "best_ask": 0.46, "midpoint": 0.44, "top_bid_size": 20, "top_ask_size": 20, "hash": "h3"},
            {"condition_id": "0xcond", "source_timestamp": 4_600, "asset_id": "tok1", "best_bid": 0.38, "best_ask": 0.42, "midpoint": 0.40, "top_bid_size": 20, "top_ask_size": 20, "hash": "h4"},
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
    assert summary["simulated_fill_opportunities"] == 1
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
            {"stamp": 1_000.0, "midpoint": 0.50, "bid_depth": 20.0, "ask_depth": 20.0},
            {"stamp": 1_300.0, "midpoint": 0.45, "bid_depth": 20.0, "ask_depth": 20.0},
            {"stamp": 1_900.0, "midpoint": 0.44, "bid_depth": 20.0, "ask_depth": 20.0},
            {"stamp": 4_600.0, "midpoint": 0.40, "bid_depth": 20.0, "ask_depth": 20.0},
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
    assert summary["simulated_fill_opportunities"] == 1
    assert summary["confirmed_fills"] == 0
    assert summary["coverage_status"] == "insufficient_coverage"
    assert summary["realism_ratio"] == "insufficient_coverage"


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
    raw["maker_fill_replay"].update({"book_source": "official", "request_pause_seconds": 0, "regime_days": 7})
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
