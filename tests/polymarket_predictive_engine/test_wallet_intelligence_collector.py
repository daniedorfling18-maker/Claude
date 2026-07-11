"""WO-37 wallet-intelligence collection: holders + leaderboard only."""
from __future__ import annotations

from pathlib import Path

import yaml

from polymarket_predictive_engine import wallet_intelligence_collector
from polymarket_predictive_engine.cli import COMMANDS
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.utils import read_csv_rows, read_json, write_csv


def _config(tmp_path: Path, *, enabled: bool = True, max_rows: int = 200000):
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    raw["wallet_intelligence"] = {
        "enabled": enabled,
        "base_url": "https://fake-data-api.local",
        "max_markets": 2,
        "top_holders_per_market": 3,
        "leaderboard_limit": 3,
        "request_timeout_seconds": 1,
        "max_ledger_rows": max_rows,
    }
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


class _ErrorResponse:
    def __init__(self, message: str = "not found"):
        self.message = message

    def raise_for_status(self):
        raise RuntimeError(self.message)

    def json(self):
        return {}


def _seed_tracked_markets(cfg) -> None:
    write_csv(
        cfg.output_root / "polymarket_websocket" / "websocket_features.csv",
        [
            {"market": "0xold", "asset_id": "tok0"},
            {"market": "0xcond1", "asset_id": "tok1"},
            {"market": "0xcond2", "asset_id": "tok2"},
            {"market": "0xcond1", "asset_id": "tok1"},
        ],
        fieldnames=["market", "asset_id"],
    )


def test_collects_leaderboard_and_holder_overlap_without_trading(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    _seed_tracked_markets(cfg)
    calls: list[tuple[str, dict]] = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, dict(params or {})))
        if url.endswith("/leaderboard"):
            return _FakeResponse(
                {
                    "data": [
                        {"wallet": "0xSmart", "rank": 1, "pnl": "123.45", "volume": "1000"},
                        {"address": "0xOther", "rank": 2, "profit": "-2.5", "vol": "50"},
                    ]
                }
            )
        if url.endswith("/holders"):
            market = params["market"]
            return _FakeResponse(
                {
                    "holders": [
                        {"wallet": "0xSmart", "outcome": "Yes", "size": "15"},
                        {"wallet": f"0xHolder{market[-1]}", "outcome": "No", "shares": "3"},
                        {"wallet": "", "size": "99"},
                    ]
                }
            )
        raise AssertionError(url)

    monkeypatch.setattr(wallet_intelligence_collector.requests, "get", fake_get)

    summary = wallet_intelligence_collector.collect_wallet_intelligence(cfg)

    assert summary["status"] == "ok"
    assert summary["markets_polled"] == 2
    assert summary["leaderboard_rows_added"] == 2
    assert summary["holder_rows_added"] == 4
    assert summary["holder_leaderboard_overlap_count"] == 1
    assert summary["holder_leaderboard_overlap_wallets"] == ["0xsmart"]
    assert summary["paper_trading_invoked"] is False
    assert summary["live_trading_invoked"] is False
    assert any(call[0].endswith("/leaderboard") for call in calls)
    assert [call[1]["market"] for call in calls if call[0].endswith("/holders")] == ["0xcond1", "0xcond2"]

    leaderboard = read_csv_rows(cfg.output_root / "wallet_intelligence" / "leaderboard_history.csv")
    holders = read_csv_rows(cfg.output_root / "wallet_intelligence" / "holders_history.csv")
    assert {row["wallet"] for row in leaderboard} == {"0xsmart", "0xother"}
    assert {row["market"] for row in holders} == {"0xcond1", "0xcond2"}
    assert read_json(cfg.output_root / "wallet_intelligence" / "wallet_intelligence_summary.json")[
        "paper_trading_invoked"
    ] is False


def test_leaderboard_probe_uses_v1_before_legacy_and_falls_back(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    _seed_tracked_markets(cfg)
    calls: list[tuple[str, dict]] = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, dict(params or {})))
        if url.endswith("/v1/leaderboard"):
            return _ErrorResponse("temporary v1 miss")
        if url.endswith("/leaderboard"):
            return _FakeResponse({"data": [{"proxyWallet": "0xSmart", "rank": 1, "pnl": "1", "vol": "10"}]})
        if url.endswith("/holders"):
            return _FakeResponse({"holders": [{"wallet": "0xSmart", "size": "3"}]})
        raise AssertionError(url)

    monkeypatch.setattr(wallet_intelligence_collector.requests, "get", fake_get)

    summary = wallet_intelligence_collector.collect_wallet_intelligence(cfg)

    leaderboard_calls = [call for call in calls if "leaderboard" in call[0]]
    assert leaderboard_calls[0][0].endswith("/v1/leaderboard")
    assert any(call[0].endswith("/leaderboard") and not call[0].endswith("/v1/leaderboard") for call in leaderboard_calls)
    assert summary["leaderboard_rows_added"] == 1
    assert summary["leaderboard_probe_params"]["path"] == "/leaderboard"


def test_wallet_markets_fall_back_to_maker_carry_candidates_when_websocket_empty(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    write_csv(
        cfg.output_root / "maker_carry" / "maker_carry_candidates.csv",
        [
            {"condition_id": "0xmaker1", "question": "maker one"},
            {"condition_id": "0xmaker2", "question": "maker two"},
        ],
        fieldnames=["condition_id", "question"],
    )
    holder_markets: list[str] = []

    def fake_get(url, params=None, timeout=None):
        if url.endswith("/v1/leaderboard"):
            return _FakeResponse({"data": [{"proxyWallet": "0xSmart", "rank": 1, "pnl": "1", "vol": "10"}]})
        if url.endswith("/holders"):
            holder_markets.append(params["market"])
            return _FakeResponse({"holders": [{"wallet": "0xSmart", "size": "3"}]})
        raise AssertionError(url)

    monkeypatch.setattr(wallet_intelligence_collector.requests, "get", fake_get)

    summary = wallet_intelligence_collector.collect_wallet_intelligence(cfg)

    assert summary["markets_polled"] == 2
    assert holder_markets == ["0xmaker1", "0xmaker2"]
    assert summary["tracked_market_sources"] == {"maker_carry_candidates": 2}


def test_daily_dedupe_and_row_cap_are_enforced(tmp_path, monkeypatch):
    cfg = _config(tmp_path, max_rows=2)
    _seed_tracked_markets(cfg)

    def fake_get(url, params=None, timeout=None):
        if url.endswith("/leaderboard"):
            return _FakeResponse(
                [
                    {"wallet": "0xA", "rank": 1, "pnl": "1", "volume": "10"},
                    {"wallet": "0xB", "rank": 2, "pnl": "2", "volume": "20"},
                    {"wallet": "0xC", "rank": 3, "pnl": "3", "volume": "30"},
                ]
            )
        market = params["market"]
        return _FakeResponse(
            [
                {"wallet": "0xA", "outcome": "Yes", "size": "1"},
                {"wallet": "0xB", "outcome": "No", "size": "2"},
                {"wallet": f"0x{market[-1]}", "outcome": "No", "size": "3"},
            ]
        )

    monkeypatch.setattr(wallet_intelligence_collector.requests, "get", fake_get)

    wallet_intelligence_collector.collect_wallet_intelligence(cfg)
    wallet_intelligence_collector.collect_wallet_intelligence(cfg)

    leaderboard = read_csv_rows(cfg.output_root / "wallet_intelligence" / "leaderboard_history.csv")
    holders = read_csv_rows(cfg.output_root / "wallet_intelligence" / "holders_history.csv")
    assert len(leaderboard) == 2
    assert [row["wallet"] for row in leaderboard] == ["0xb", "0xc"]
    assert len(holders) == 2
    assert len({(row["snapshot_date"], row["market"], row["wallet"]) for row in holders}) == 2


def test_disabled_mode_writes_no_trading_summary_and_does_not_call_network(tmp_path, monkeypatch):
    cfg = _config(tmp_path, enabled=False)
    _seed_tracked_markets(cfg)

    def fail_get(*args, **kwargs):
        raise AssertionError("network should not be called when disabled")

    monkeypatch.setattr(wallet_intelligence_collector.requests, "get", fail_get)

    summary = wallet_intelligence_collector.collect_wallet_intelligence(cfg)

    assert summary["status"] == "disabled"
    assert summary["paper_trading_invoked"] is False
    assert summary["live_trading_invoked"] is False
    assert read_json(cfg.output_root / "wallet_intelligence" / "wallet_intelligence_summary.json")["status"] == "disabled"


def test_cli_exposes_collect_wallet_intel():
    assert "collect-wallet-intel" in COMMANDS
