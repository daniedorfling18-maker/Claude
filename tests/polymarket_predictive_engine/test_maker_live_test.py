"""WO-36 step 4 scoreboard: inert without a wallet, honest with one - rewards
plus signed inventory PnL scored against the study's modelled fill rate."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from polymarket_predictive_engine import maker_live_test
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.ledger_anchor import anchor_ledgers
from polymarket_predictive_engine.maker_live_test import run_maker_live_test
from polymarket_predictive_engine.stage_operator import build_stage_day, record_stage_action
from polymarket_predictive_engine.utils import read_csv_rows, read_json, write_csv, write_json

RUN_AT = "2026-07-15T12:00:00Z"


@pytest.fixture(autouse=True)
def _fixed_scoreboard_time(monkeypatch):
    monkeypatch.setattr(maker_live_test, "now_utc", lambda: RUN_AT)


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


def _epoch(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp())


def _anchor_owner_trade(
    cfg,
    *,
    action_at_utc: str = "2026-07-15T06:00:00Z",
    action_type: str = "drill_trade",
    market_id: str = "0xm1",
    side: str = "bid",
    price: float = 0.5,
    size_shares: float = 5.0,
):
    stage_date = action_at_utc[:10]
    build_stage_day(cfg, stage_date=stage_date)
    action = record_stage_action(
        cfg,
        action_type=action_type,
        stage_date=stage_date,
        action_at_utc=action_at_utc,
        market_id=market_id,
        side=side,
        price=price,
        size_shares=size_shares,
        note="owner micro-drill; no stage quotes live",
    )
    anchor_ledgers(cfg, anchor_date=stage_date)
    return action


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
    now = _epoch(RUN_AT)
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
    now = _epoch(RUN_AT)
    rewards = [{"usdcSize": 1.0, "timestamp": now - 1000, "type": "REWARD"}]
    trades = [{"size": 100, "price": 0.5, "timestamp": now - 2000, "type": "TRADE"}]
    positions = [{"size": 200, "curPrice": 0.40, "avgPrice": 0.5}]  # -$20 at mark
    _fake_requests(monkeypatch, rewards=rewards, trades=trades, positions=positions)

    summary = run_maker_live_test(cfg)

    assert summary["fill_alert"] is False
    assert summary["net_score_usd"] == -19.0
    assert summary["scoreboard"] == "losing_so_far"


def test_wo118_positive_net_still_reads_winning(tmp_path, monkeypatch):
    # WO-118 boundary: strictly positive rewards+PnL keeps winning_so_far.
    cfg = _config(tmp_path, wallet="0xabc")
    _seed_study(cfg, crossings=100.0)
    now = _epoch(RUN_AT)
    rewards = [{"usdcSize": 1.0, "timestamp": now - 1000, "type": "REWARD"}]
    trades = [{"size": 100, "price": 0.5, "timestamp": now - 2000, "type": "TRADE"}]
    _fake_requests(monkeypatch, rewards=rewards, trades=trades, positions=[])

    summary = run_maker_live_test(cfg)

    assert summary["net_score_usd"] == 1.0
    assert summary["scoreboard"] == "winning_so_far"


def test_quiet_wallet_old_newest_activity_ages_out_against_wall_clock(tmp_path, monkeypatch):
    cfg = _config(tmp_path, wallet="0xabc")
    _seed_study(cfg, crossings=4.0)
    old = _epoch(RUN_AT) - (36 * 3600)
    rewards = [{"usdcSize": 2.5, "timestamp": old, "type": "REWARD"}]
    trades = [{"size": 5, "price": 0.5, "timestamp": old, "type": "TRADE"}]
    _fake_requests(monkeypatch, rewards=rewards, trades=trades, positions=[])

    summary = run_maker_live_test(cfg)

    assert summary["rewards_usd_total"] == 2.5
    assert summary["rewards_usd_last_24h"] == 0.0
    assert summary["fills_last_24h_raw"] == 0
    assert summary["maker_test_fills_last_24h"] == 0
    assert summary["fill_alert"] is False


def test_second_scale_activity_timestamp_is_unchanged():
    seconds = float(_epoch(RUN_AT) - 60)

    assert maker_live_test._stamp({"timestamp": seconds}) == seconds


def test_millisecond_activity_timestamps_normalize_and_age_out(tmp_path, monkeypatch):
    cfg = _config(tmp_path, wallet="0xabc")
    _seed_study(cfg, crossings=4.0)
    now = _epoch(RUN_AT)
    old_ms = (now - 90000) * 1000
    fresh_ms = (now - 60) * 1000
    rewards = [
        {"usdcSize": 3.0, "timestamp": old_ms, "type": "REWARD"},
        {"usdcSize": 1.0, "timestamp": fresh_ms, "type": "REWARD"},
    ]
    trades = [
        {"size": 5, "price": 0.5, "timestamp": old_ms, "type": "TRADE"},
        {"size": 5, "price": 0.5, "timestamp": fresh_ms, "type": "TRADE"},
    ]
    _fake_requests(monkeypatch, rewards=rewards, trades=trades, positions=[])

    summary = run_maker_live_test(cfg)

    assert summary["rewards_usd_total"] == 4.0
    assert summary["rewards_usd_last_24h"] == 1.0
    assert summary["fills_last_24h_raw"] == 1
    assert summary["maker_test_fills_last_24h"] == 1
    assert maker_live_test._stamp({"timestamp": fresh_ms}) == float(now - 60)


def test_unparseable_activity_stamp_is_excluded_without_earning_owner_attribution(tmp_path, monkeypatch):
    cfg = _config(tmp_path, wallet="0xabc")
    _seed_study(cfg, crossings=4.0)
    trades = [
        {"size": 5, "price": 0.5, "timestamp": "not-a-time", "type": "TRADE"},
        {
            "size": 5,
            "price": 0.5,
            "timestamp": _epoch(RUN_AT) - 60,
            "type": "TRADE",
            "conditionId": "0xm1",
            "side": "BUY",
        },
    ]
    _fake_requests(monkeypatch, rewards=[], trades=trades, positions=[])

    summary = run_maker_live_test(cfg)

    assert maker_live_test._stamp(trades[0]) == 0.0
    assert summary["fills_last_24h_raw"] == 1
    assert summary["owner_activity_fills_last_24h"] == 0
    assert summary["maker_test_fills_last_24h"] == 1


def test_anchored_logged_drill_fill_is_reported_but_excluded_from_kill_count(tmp_path, monkeypatch):
    cfg = _config(tmp_path, wallet="0xabc")
    _seed_study(cfg, crossings=4.0)
    action = _anchor_owner_trade(cfg)
    trade = {
        "type": "TRADE",
        "timestamp": _epoch("2026-07-15T06:01:00Z"),
        "conditionId": "0xm1",
        "side": "BUY",
        "price": 0.5,
        "size": 5.0,
        "transactionHash": "0xdrill",
    }
    _fake_requests(monkeypatch, rewards=[], trades=[trade], positions=[])

    summary = run_maker_live_test(cfg)

    assert summary["fills_last_24h_raw"] == 1
    assert summary["owner_activity_fills_last_24h"] == 1
    assert summary["maker_test_fills_last_24h"] == 0
    assert summary["fills_last_24h"] == 0
    assert summary["fill_alert"] is False
    assert summary["scoreboard"] == "owner_activity_only"
    assert summary["fill_attribution"]["operator_log_anchor"]["state"] == "anchored_prefix_verified"
    assert summary["fill_attribution"]["owner_activity_matches"][0]["action_entry_id"] == action["entry_id"]
    history = read_csv_rows(
        cfg.output_root / "maker_carry" / "maker_live_test_attribution_history.csv"
    )
    assert history[-1]["fills_last_24h_raw"] == "1"
    assert history[-1]["owner_activity_fills_last_24h"] == "1"
    assert history[-1]["maker_test_fills_last_24h"] == "0"


def test_unlogged_fill_remains_maker_test_and_still_trips_stop(tmp_path, monkeypatch):
    cfg = _config(tmp_path, wallet="0xabc")
    _seed_study(cfg, crossings=4.0)
    trade = {
        "type": "TRADE",
        "timestamp": _epoch("2026-07-15T06:01:00Z"),
        "conditionId": "0xm1",
        "side": "BUY",
        "price": 0.5,
        "size": 5.0,
        "transactionHash": "0xunknown",
    }
    _fake_requests(monkeypatch, rewards=[], trades=[trade], positions=[])

    summary = run_maker_live_test(cfg)

    assert summary["fills_last_24h_raw"] == 1
    assert summary["owner_activity_fills_last_24h"] == 0
    assert summary["maker_test_fills_last_24h"] == 1
    assert summary["fill_alert"] is True
    assert summary["scoreboard"] == "STOP_fills_outrunning_model"


def test_mixed_window_counts_only_unlogged_fill_as_maker_test(tmp_path, monkeypatch):
    cfg = _config(tmp_path, wallet="0xabc")
    _seed_study(cfg, crossings=10.0)
    _anchor_owner_trade(cfg)
    timestamp = _epoch("2026-07-15T06:01:00Z")
    trades = [
        {
            "type": "TRADE",
            "timestamp": timestamp,
            "conditionId": "0xm1",
            "side": "BUY",
            "price": 0.5,
            "size": 5.0,
            "transactionHash": "0xdrill",
        },
        {
            "type": "TRADE",
            "timestamp": timestamp + 1,
            "conditionId": "0xm1",
            "side": "SELL",
            "price": 0.5,
            "size": 5.0,
            "transactionHash": "0xunlogged",
        },
    ]
    _fake_requests(monkeypatch, rewards=[], trades=trades, positions=[])

    summary = run_maker_live_test(cfg)

    assert summary["fills_last_24h_raw"] == 2
    assert summary["owner_activity_fills_last_24h"] == 1
    assert summary["maker_test_fills_last_24h"] == 1
    assert summary["fills_last_24h"] == 1
    assert summary["fill_alert"] is False
    # WO-118: an equal-price round trip nets exactly zero - not "held positive"
    # under the stated scoring rule, so no longer reads winning_so_far.
    assert summary["scoreboard"] == "flat_no_net_evidence"


def test_logged_but_not_yet_anchored_drill_fill_remains_maker_test(tmp_path, monkeypatch):
    cfg = _config(tmp_path, wallet="0xabc")
    _seed_study(cfg, crossings=4.0)
    build_stage_day(cfg, stage_date="2026-07-15")
    record_stage_action(
        cfg,
        action_type="drill_trade",
        stage_date="2026-07-15",
        action_at_utc="2026-07-15T06:00:00Z",
        market_id="0xm1",
        side="bid",
        price=0.5,
        size_shares=5.0,
    )
    trade = {
        "type": "TRADE",
        "timestamp": _epoch("2026-07-15T06:01:00Z"),
        "conditionId": "0xm1",
        "side": "BUY",
        "price": 0.5,
        "size": 5.0,
    }
    _fake_requests(monkeypatch, rewards=[], trades=[trade], positions=[])

    summary = run_maker_live_test(cfg)

    assert summary["owner_activity_fills_last_24h"] == 0
    assert summary["maker_test_fills_last_24h"] == 1
    assert summary["fill_alert"] is True
    assert summary["fill_attribution"]["operator_log_anchor"]["state"] == "operator_log_not_anchored"


def test_anchored_drill_fill_outside_registered_window_remains_maker_test(tmp_path, monkeypatch):
    cfg = _config(tmp_path, wallet="0xabc")
    _seed_study(cfg, crossings=4.0)
    _anchor_owner_trade(cfg)
    trade = {
        "type": "TRADE",
        "timestamp": _epoch("2026-07-15T06:05:01Z"),
        "conditionId": "0xm1",
        "side": "BUY",
        "price": 0.5,
        "size": 5.0,
    }
    _fake_requests(monkeypatch, rewards=[], trades=[trade], positions=[])

    summary = run_maker_live_test(cfg)

    assert summary["fill_attribution"]["registered_match_window_seconds"] == 300
    assert summary["owner_activity_fills_last_24h"] == 0
    assert summary["maker_test_fills_last_24h"] == 1
    assert summary["scoreboard"] == "STOP_fills_outrunning_model"


def test_operator_and_executor_scoreboards_are_separate_and_never_summed(tmp_path, monkeypatch):
    operator = "0xoperator"
    executor = "0xexecutor"
    cfg = _config(tmp_path, wallet=operator, executor_wallet=executor)
    _seed_study(cfg, crossings=100.0)
    legacy_path = cfg.output_root / "maker_carry" / "maker_live_test_history.csv"
    write_csv(
        legacy_path,
        [
            {
                "generated_at_utc": "2026-07-12T06:16:56Z",
                "rewards_usd_total": 0,
                "rewards_usd_last_24h": 0,
                "inventory_value_usd": 0,
                "inventory_pnl_usd": 0,
                "fills_last_24h": 0,
                "modelled_fills_per_day": 1.0,
                "fill_alert": False,
                "net_score_usd": 0,
                "scoreboard": "no_activity_yet",
            }
        ],
        fieldnames=maker_live_test.LEGACY_HISTORY_FIELDS,
    )
    anchored_prefix = legacy_path.read_bytes()

    def fake_get(url: str, params: dict[str, Any] | None = None, timeout: float | None = None):
        del timeout
        params = params or {}
        user = params.get("user")
        if url.endswith("/activity"):
            if params.get("type") == "REWARD":
                return _Response(
                    [{"usdcSize": 3.0 if user == operator else 1.0, "timestamp": _epoch(RUN_AT) - 1000}]
                )
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
    assert legacy_path.read_bytes().startswith(anchored_prefix)
    assert "wallet_role" not in read_csv_rows(legacy_path)[0]
    history = read_csv_rows(cfg.output_root / "maker_carry" / "maker_live_test_wallet_history.csv")
    assert {row["wallet_role"] for row in history} == {"operator", "executor"}
