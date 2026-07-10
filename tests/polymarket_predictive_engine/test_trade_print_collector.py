"""Trade-print collection: executed trades are training substrate the system
never captured before 2026-07-09. Collection only - no labels, no gates."""
from __future__ import annotations

from pathlib import Path

import yaml

from polymarket_predictive_engine import trade_print_collector
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.utils import read_csv_rows, write_csv


def _config(tmp_path: Path):
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_collects_deduplicates_and_persists_prints(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    write_csv(
        cfg.output_root / "polymarket_websocket" / "websocket_features.csv",
        [
            {"market": "0xcond1", "asset_id": "tok1"},
            {"market": "0xcond2", "asset_id": "tok2"},
            {"market": "0xcond1", "asset_id": "tok1"},
        ],
        fieldnames=["market", "asset_id"],
    )
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, dict(params or {})))
        market = params["market"]
        return _FakeResponse([
            {"id": f"{market}-t1", "market": market, "asset": "tok", "side": "BUY", "price": "0.42", "size": "10", "timestamp": "1783590000"},
            {"id": f"{market}-t1", "market": market, "asset": "tok", "side": "BUY", "price": "0.42", "size": "10", "timestamp": "1783590000"},
            {"id": "", "market": market, "price": "0.5", "size": "1"},
        ])

    monkeypatch.setattr(trade_print_collector.requests, "get", fake_get)

    summary = trade_print_collector.collect_trade_prints(cfg)

    assert summary["status"] == "ok"
    assert summary["markets_polled"] == 2
    assert summary["new_prints"] == 2  # one per market; dupes and blank ids dropped
    assert summary["paper_trading_invoked"] is False
    rows = read_csv_rows(cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv")
    assert {row["trade_id"] for row in rows} == {"0xcond1-t1", "0xcond2-t1"}

    # A second run with the same trades adds nothing (ledger-level dedupe).
    summary2 = trade_print_collector.collect_trade_prints(cfg)
    assert summary2["new_prints"] == 0
    assert summary2["ledger_rows"] == 2


def test_provider_errors_are_fail_soft_and_reported(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    write_csv(
        cfg.output_root / "polymarket_websocket" / "websocket_features.csv",
        [{"market": "0xbad", "asset_id": "tok"}],
        fieldnames=["market", "asset_id"],
    )

    def fake_get(url, params=None, timeout=None):
        raise RuntimeError("data-api unreachable")

    monkeypatch.setattr(trade_print_collector.requests, "get", fake_get)

    summary = trade_print_collector.collect_trade_prints(cfg)

    assert summary["status"] == "failed"
    assert summary["errors"] and "data-api unreachable" in summary["errors"][0]
    assert summary["new_prints"] == 0


def test_open_interest_rides_along_with_trade_print_collection(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    write_csv(
        cfg.output_root / "polymarket_websocket" / "websocket_features.csv",
        [{"market": "0xcond1", "asset_id": "tok"}],
        fieldnames=["market", "asset_id"],
    )

    def fake_get(url, params=None, timeout=None):
        market = params["market"]
        if url.endswith("/trades"):
            return _FakeResponse([
                {
                    "id": "trade-1",
                    "market": market,
                    "asset": "tok",
                    "side": "BUY",
                    "price": "0.51",
                    "size": "4",
                    "timestamp": "1783590000",
                }
            ])
        if url.endswith("/oi"):
            return _FakeResponse({"market": market, "openInterest": "1234.5", "timestamp": "1783590001"})
        raise AssertionError(url)

    monkeypatch.setattr(trade_print_collector.requests, "get", fake_get)

    summary = trade_print_collector.collect_trade_prints(cfg)

    assert summary["status"] == "ok"
    assert summary["oi_markets_captured"] == 1
    assert summary["oi_ledger_rows"] == 1
    assert summary["paper_trading_invoked"] is False
    assert summary["live_trading_invoked"] is False
    rows = read_csv_rows(cfg.output_root / "polymarket_trade_prints" / "open_interest_history.csv")
    assert rows == [
        {
            "market": "0xcond1",
            "open_interest": "1234.5",
            "timestamp": "1783590001",
            "collected_at_utc": rows[0]["collected_at_utc"],
        }
    ]


def test_open_interest_endpoint_miss_is_tolerated(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    write_csv(
        cfg.output_root / "polymarket_websocket" / "websocket_features.csv",
        [{"market": "0xcond1", "asset_id": "tok"}],
        fieldnames=["market", "asset_id"],
    )

    def fake_get(url, params=None, timeout=None):
        if url.endswith("/trades"):
            return _FakeResponse([
                {
                    "id": "trade-1",
                    "market": params["market"],
                    "asset": "tok",
                    "side": "SELL",
                    "price": "0.49",
                    "size": "3",
                    "timestamp": "1783590000",
                }
            ])
        if url.endswith("/oi"):
            raise RuntimeError("oi unavailable")
        raise AssertionError(url)

    monkeypatch.setattr(trade_print_collector.requests, "get", fake_get)

    summary = trade_print_collector.collect_trade_prints(cfg)

    assert summary["status"] == "ok"
    assert summary["new_prints"] == 1
    assert summary["oi_markets_captured"] == 0
    assert summary["oi_errors"] and "oi unavailable" in summary["oi_errors"][0]
