"""WO-40 maker fill replay: last-in-queue realism for maker-carry."""
from __future__ import annotations

import csv
import gzip
from pathlib import Path

import yaml

from polymarket_predictive_engine import maker_fill_replay
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
    assert summary["fills_preview"][0]["fill_size"] == 5.0
    assert summary["fills_preview"][0]["depth_ahead"] == 20.0


def test_absent_archive_is_tolerated(tmp_path):
    cfg = _config(tmp_path)
    _seed_maker_portfolio(cfg)

    summary = run_maker_fill_replay(cfg)

    assert summary["status"] == "no_replay_data"
    assert summary["simulated_fills"] == 0
    persisted = read_json(cfg.output_root / "maker_carry" / "maker_fill_replay.json")
    assert persisted["paper_trading_invoked"] is False
    assert persisted["live_trading_invoked"] is False


def test_snapshot_official_books_paginates_and_deduplicates_hashes(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    raw = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    raw["maker_fill_replay"].update({"book_source": "official", "official_book_limit": 2, "request_pause_seconds": 0})
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    cfg = load_config(tmp_path / "config.yaml")
    _seed_maker_portfolio(cfg)
    monkeypatch.setattr(maker_fill_replay.time, "sleep", lambda _: None)
    monkeypatch.setattr(maker_fill_replay, "now_utc", lambda: "1970-01-01T00:16:00Z")
    calls = []

    def fake_get(url, params=None, timeout=None):
        assert url.endswith("/orderbook-history")
        calls.append(dict(params or {}))
        if len(calls) == 1:
            return _Response({
                "data": [
                    {"timestamp": 1_000, "hash": "h1", "bids": [{"price": "0.48", "size": "20"}], "asks": [{"price": "0.52", "size": "20"}]},
                    {"timestamp": 1_000, "hash": "h1", "bids": [{"price": "0.48", "size": "20"}], "asks": [{"price": "0.52", "size": "20"}]},
                ]
            })
        return _Response({
            "data": [
                {"timestamp": 1_300, "hash": "h2", "bids": [{"price": "0.43", "size": "20"}], "asks": [{"price": "0.47", "size": "20"}]},
            ]
        })

    monkeypatch.setattr(maker_fill_replay.requests, "get", fake_get)

    summary = maker_fill_replay.snapshot_official_books(cfg)

    assert summary["status"] == "ok"
    rows = maker_fill_replay._read_csv_any(cfg.output_root / "maker_carry" / "official_books" / "0xcond.csv.gz")
    assert [(row["source_timestamp"], row["hash"]) for row in rows] == [("1000.0", "h1"), ("1300.0", "h2")]
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


def test_official_endpoint_absence_degrades_to_archive(tmp_path, monkeypatch):
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

    def fake_get(url, params=None, timeout=None):
        raise RuntimeError("official endpoint absent")

    monkeypatch.setattr(maker_fill_replay.requests, "get", fake_get)

    summary = run_maker_fill_replay(cfg)

    assert summary["status"] == "ok"
    assert summary["primary_book_source"] == "archive"
    assert summary["available_book_sources"] == ["archive"]
    assert summary["simulated_fills"] == 1
    assert summary["official_snapshot"]["status"] == "failed"


def test_cli_exposes_maker_fill_replay():
    assert "maker-fill-replay" in COMMANDS
    assert "snapshot-official-books" in COMMANDS
