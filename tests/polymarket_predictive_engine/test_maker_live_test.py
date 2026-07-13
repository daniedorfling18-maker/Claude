"""WO-36 step 4 scoreboard: inert without a wallet, honest with one - rewards
plus signed inventory PnL scored against the study's modelled fill rate."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from polymarket_predictive_engine import maker_live_test
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.maker_live_test import run_maker_live_test
from polymarket_predictive_engine.utils import read_csv_rows, read_json, write_csv, write_json


def _config(tmp_path: Path, wallet: str = "", executor_wallet: str = ""):
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    raw["maker_live_test"] = {
        "enabled": True,
        "wallet_address": wallet,
        "executor_wallet_address": executor_wallet,
        "fill_alert_multiple": 2.0,
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def _seed_study(cfg, *, crossings: float = 10.0, quote_size: float = 100.0) -> None:
    out = cfg.output_root / "maker_carry"
    write_json(
        out / "maker_carry_study.json",
        {"portfolio": [{"condition_id": "0xm1", "quote_size_shares": quote_size}]},
    )
    write_csv(
        out / "maker_carry_candidates.csv",
        [
            {
                "condition_id": "0xm1",
                "band_crossing_prints_per_day": crossings,
                "competitor_score_bid": 900.0,
                "competitor_score_ask": 900.0,
            }
        ],
        fieldnames=["condition_id", "band_crossing_prints_per_day", "competitor_score_bid", "competitor_score_ask"],
    )


class _Response:
    def __init__(self, payload: Any):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


def _fake_requests(monkeypatch, *, rewards, trades, positions) -> None:
    def fake_get(url: str, params: dict[str, Any] | None = None, timeout: float | None = None):
        params = params or {}
        if url.endswith("/activity"):
            return _Response(rewards if params.get("type") == "REWARD" else trades)
        if url.endswith("/positions"):
            return _Response(positions)
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(maker_live_test.requests, "get", fake_get)


def test_without_wallet_the_module_is_fully_inert(tmp_path):
    cfg = _config(tmp_path)

    summary = run_maker_live_test(cfg)

    assert summary["status"] == "awaiting_wallet_address"
    assert summary["paper_trading_invoked"] is False
    assert summary["live_trading_invoked"] is False
    persisted = read_json(cfg.output_root / "maker_carry" / "maker_live_test.json")
    assert persisted["status"] == "awaiting_wallet_address"
    # No history rows accrue while idle.
    assert read_csv_rows(cfg.output_root / "maker_carry" / "maker_live_test_history.csv") == []


def test_scoreboard_wins_when_rewards_beat_inventory_losses(tmp_path, monkeypatch):
    cfg = _config(tmp_path, wallet="0xabc")
    _seed_study(cfg)
    now = 1_800_000_000
    rewards = [
        {"usdcSize": 2.5, "timestamp": now - 3000, "type": "REWARD"},
        {"usdcSize": 1.5, "timestamp": now - 90000, "type": "REWARD"},  # older than 24h
    ]
    trades = [{"size": 100, "price": 0.5, "timestamp": now - 2000, "type": "TRADE"}] * 3
    positions = [{"size": 100, "curPrice": 0.48, "avgPrice": 0.5}]  # -$2 at mark
    _fake_requests(monkeypatch, rewards=rewards, trades=trades, positions=positions)

    summary = run_maker_live_test(cfg)

    assert summary["status"] == "ok"
    assert summary["rewards_usd_total"] == 4.0
    assert summary["rewards_usd_last_24h"] == 2.5
    assert summary["inventory_pnl_usd"] == -2.0
    assert summary["net_score_usd"] == 2.0
    assert summary["fills_last_24h"] == 3
    # Model: 10 crossings/day x queue share 100/(100+900) = 1 fill/day; 3 real
    # fills breaches the 2x alert bound -> the scoreboard says STOP.
    assert summary["modelled_fills_per_day"] == 1.0
    assert summary["fill_alert"] is True
    assert summary["scoreboard"] == "STOP_fills_outrunning_model"
    history = read_csv_rows(cfg.output_root / "maker_carry" / "maker_live_test_history.csv")
    assert len(history) == 1


def test_scoreboard_loses_when_inventory_swamps_rewards(tmp_path, monkeypatch):
    cfg = _config(tmp_path, wallet="0xabc")
    _seed_study(cfg, crossings=100.0)
    now = 1_800_000_000
    rewards = [{"usdcSize": 1.0, "timestamp": now - 1000, "type": "REWARD"}]
    trades = [{"size": 100, "price": 0.5, "timestamp": now - 2000, "type": "TRADE"}]
    positions = [{"size": 200, "curPrice": 0.40, "avgPrice": 0.5}]  # -$20 at mark
    _fake_requests(monkeypatch, rewards=rewards, trades=trades, positions=positions)

    summary = run_maker_live_test(cfg)

    assert summary["fill_alert"] is False
    assert summary["net_score_usd"] == -19.0
    assert summary["scoreboard"] == "losing_so_far"


def test_operator_and_executor_scoreboards_are_separate_and_never_summed(tmp_path, monkeypatch):
    operator = "0xoperator"
    executor = "0xexecutor"
    cfg = _config(tmp_path, wallet=operator, executor_wallet=executor)
    _seed_study(cfg, crossings=100.0)

    def fake_get(url: str, params: dict[str, Any] | None = None, timeout: float | None = None):
        del timeout
        params = params or {}
        user = params.get("user")
        if url.endswith("/activity"):
            if params.get("type") == "REWARD":
                return _Response([{"usdcSize": 3.0 if user == operator else 1.0, "timestamp": 1_800_000_000}])
            return _Response([])
        if url.endswith("/positions"):
            pnl = -1.0 if user == operator else -4.0
            return _Response([{"size": 10, "currentValue": 5.0, "cashPnl": pnl}])
        raise AssertionError(url)

    monkeypatch.setattr(maker_live_test.requests, "get", fake_get)
    summary = run_maker_live_test(cfg)

    assert summary["wallets_combined"] is False
    assert summary["primary_wallet_role"] == "operator"
    assert summary["net_score_usd"] == 2.0
    assert summary["wallets"]["operator"]["net_score_usd"] == 2.0
    assert summary["wallets"]["executor"]["net_score_usd"] == -3.0
    assert summary["net_score_usd"] != -1.0
    history = read_csv_rows(cfg.output_root / "maker_carry" / "maker_live_test_history.csv")
    assert {row["wallet_role"] for row in history} == {"operator", "executor"}
