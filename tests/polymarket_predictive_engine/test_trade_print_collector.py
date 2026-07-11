"""Trade-print collection: executed trades are training substrate the system
never captured before 2026-07-09. Collection only - no labels, no gates."""
from __future__ import annotations

from pathlib import Path

import yaml

from polymarket_predictive_engine import trade_print_collector
from polymarket_predictive_engine.cli import COMMANDS
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.utils import read_csv_rows, read_json, write_csv, write_json


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


def test_backfill_trade_prints_paginates_deduplicates_and_stamps(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    cfg.raw["trade_prints"].update(
        {
            "backfill_limit_per_page": 2,
            "backfill_max_prints_per_market": 10,
            "backfill_request_pause_seconds": 0.1,
        }
    )
    out = cfg.output_root / "maker_carry"
    write_csv(
        out / "maker_carry_candidates.csv",
        [{"condition_id": "0xaaa"}, {"condition_id": "0xbbb"}],
        fieldnames=["condition_id"],
    )
    write_json(out / "maker_carry_study.json", {"portfolio": [{"condition_id": "0xbbb"}]})
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [
            {
                "trade_id": "0xaaa-1",
                "market": "0xaaa",
                "asset_id": "tok",
                "side": "BUY",
                "price": "0.42",
                "size": "10",
                "timestamp": "1783590000",
                "collected_at_utc": "2026-07-10T00:00:00Z",
            }
        ],
        fieldnames=trade_print_collector.PRINT_FIELDS,
    )
    calls = []
    sleeps = []

    def fake_get(url, params=None, timeout=None):
        params = dict(params or {})
        calls.append(params)
        if params["market"] == "0xbbb":
            return _FakeResponse([])
        offset = params["offset"]
        if offset == 0:
            payload = [
                {"id": "0xaaa-0", "market": "0xaaa", "asset": "tok", "side": "BUY", "price": "0.41", "size": "5", "timestamp": "1783590000"},
                {"id": "0xaaa-1", "market": "0xaaa", "asset": "tok", "side": "SELL", "price": "0.43", "size": "7", "timestamp": "1783590001"},
            ]
        elif offset == 2:
            payload = [
                {"id": "0xaaa-2", "market": "0xaaa", "asset": "tok", "side": "BUY", "price": "0.44", "size": "9", "timestamp": "1783590002"},
            ]
        else:
            payload = []
        return _FakeResponse(payload)

    monkeypatch.setattr(trade_print_collector.requests, "get", fake_get)
    monkeypatch.setattr(trade_print_collector.time, "sleep", lambda seconds: sleeps.append(seconds))

    summary = trade_print_collector.backfill_trade_prints(cfg)

    assert summary["status"] == "ok"
    assert summary["candidate_markets"] == 2
    assert summary["markets_attempted"] == 2
    assert summary["markets_completed"] == 2
    assert summary["new_prints"] == 2
    assert "backfill-trade-prints" in COMMANDS
    assert [call for call in calls if call["market"] == "0xaaa"] == [
        {"market": "0xaaa", "limit": 2, "offset": 0},
        {"market": "0xaaa", "limit": 2, "offset": 2},
    ]
    assert all(seconds >= 0.1 for seconds in sleeps)
    rows = read_csv_rows(cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv")
    assert {row["trade_id"] for row in rows} == {"0xaaa-0", "0xaaa-1", "0xaaa-2"}
    stamp = (cfg.output_root / "polymarket_trade_prints" / "backfill_completed_markets.txt").read_text(encoding="utf-8")
    assert "0xaaa" in stamp and "0xbbb" in stamp
    persisted = read_json(cfg.output_root / "polymarket_trade_prints" / "trade_print_backfill_summary.json")
    assert persisted["paper_trading_invoked"] is False
    assert persisted["live_trading_invoked"] is False


def test_backfill_trade_prints_skips_stamped_markets_and_picks_up_new_candidates(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    cfg.raw["trade_prints"].update({"backfill_limit_per_page": 2, "backfill_max_prints_per_market": 10})
    candidate_path = cfg.output_root / "maker_carry" / "maker_carry_candidates.csv"
    write_csv(candidate_path, [{"condition_id": "0xold"}], fieldnames=["condition_id"])
    stamp_path = cfg.output_root / "polymarket_trade_prints" / "backfill_completed_markets.txt"
    stamp_path.parent.mkdir(parents=True, exist_ok=True)
    stamp_path.write_text("0xold\n", encoding="utf-8")
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(dict(params or {}))
        return _FakeResponse([
            {"id": "new-1", "market": params["market"], "asset": "tok", "side": "BUY", "price": "0.5", "size": "1", "timestamp": "1783590000"}
        ])

    monkeypatch.setattr(trade_print_collector.requests, "get", fake_get)
    monkeypatch.setattr(trade_print_collector.time, "sleep", lambda seconds: None)

    skipped = trade_print_collector.backfill_trade_prints(cfg)
    assert skipped["status"] == "skipped_all_completed"
    assert calls == []

    write_csv(candidate_path, [{"condition_id": "0xold"}, {"condition_id": "0xnew"}], fieldnames=["condition_id"])
    picked_up = trade_print_collector.backfill_trade_prints(cfg)

    assert picked_up["markets_skipped_completed"] == 1
    assert picked_up["markets_attempted"] == 1
    assert calls == [{"market": "0xnew", "limit": 2, "offset": 0}]


def test_backfill_trade_prints_respects_per_market_cap(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    cfg.raw["trade_prints"].update(
        {
            "backfill_limit_per_page": 2,
            "backfill_max_prints_per_market": 3,
            "backfill_request_pause_seconds": 0.1,
        }
    )
    write_csv(
        cfg.output_root / "maker_carry" / "maker_carry_candidates.csv",
        [{"condition_id": "0xcap"}],
        fieldnames=["condition_id"],
    )
    calls = []

    def fake_get(url, params=None, timeout=None):
        params = dict(params or {})
        calls.append(params)
        offset = int(params["offset"])
        limit = int(params["limit"])
        return _FakeResponse(
            [
                {"id": f"cap-{offset + idx}", "market": "0xcap", "asset": "tok", "side": "BUY", "price": "0.5", "size": "1", "timestamp": str(1783590000 + offset + idx)}
                for idx in range(limit)
            ]
        )

    monkeypatch.setattr(trade_print_collector.requests, "get", fake_get)
    monkeypatch.setattr(trade_print_collector.time, "sleep", lambda seconds: None)

    summary = trade_print_collector.backfill_trade_prints(cfg)

    assert summary["status"] == "ok"
    assert summary["new_prints"] == 3
    assert summary["cap_hit_markets"] == ["0xcap"]
    assert calls == [
        {"market": "0xcap", "limit": 2, "offset": 0},
        {"market": "0xcap", "limit": 1, "offset": 2},
    ]
