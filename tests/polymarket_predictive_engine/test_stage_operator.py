from __future__ import annotations

import json
from pathlib import Path

import pytest

from polymarket_predictive_engine.cli import COMMANDS
from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.ledger_anchor import DEFAULT_LEDGER_REGISTRY, anchor_ledgers
from polymarket_predictive_engine.stage_operator import build_stage_day, record_stage_action, run_stage_day
from polymarket_predictive_engine.utils import read_csv_rows, read_json, write_csv, write_json


ROOT = Path(__file__).resolve().parents[2]
FIXED_NOW = "2026-07-15T06:00:00Z"


def _config(tmp_path: Path) -> EngineConfig:
    path = tmp_path / "config.yaml"
    return EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "stage_operator": {"enabled": True, "lock_stale_seconds": 60},
            "ledger_anchor": {"enabled": True},
        },
        path=path,
    )


def _seed_complete_inputs(cfg: EngineConfig) -> None:
    maker = cfg.output_root / "maker_carry"
    performance = cfg.output_root / "performance"
    write_json(
        maker / "maker_carry_study.json",
        {
            "generated_at_utc": "2026-07-15T05:55:00Z",
            "portfolio": [
                {
                    "question": "Will the example resolve Yes?",
                    "condition_id": "0xmarket",
                    "market_url": "https://polymarket.com/event/example",
                    "outcome": "Yes",
                    "quote_bid_price": 0.48,
                    "quote_ask_price": 0.52,
                    "quote_size_shares": 100,
                    "order_ticket_status": "exact",
                }
            ],
        },
    )
    write_json(
        maker / "requote_alerts.json",
        {
            "generated_at_utc": "2026-07-15T05:58:00Z",
            "alert_state": "quotes_ok",
            "headline": "Current human tickets remain inside the registered band.",
            "markets_checked": 1,
            "markets_requiring_action": 0,
            "kill_criteria_triggered": [],
            "markets": [{"condition_id": "0xmarket", "action": "keep"}],
        },
    )
    write_json(
        maker / "decision_policy.json",
        {
            "generated_at_utc": "2026-07-15T05:56:00Z",
            "kill_criteria_status": {
                "status": "clear",
                "triggered": [],
                "criteria": {
                    "cumulative_real_net_score": {"triggered": False, "value": 2.0, "threshold": -25.0},
                    "scoreboard_stop": {"triggered": False, "value": "winning_so_far", "threshold": "STOP"},
                },
            },
        },
    )
    write_csv(
        performance / "wallet_reconciliation_wallet_history.csv",
        [
            {
                "generated_at_utc": "2026-07-14T23:00:00Z",
                "snapshot_date": "2026-07-14",
                "wallet_role": "operator",
                "wallet_address": "0xpublic",
                "reconciliation_status": "clean",
                "internal_nav_usd": 100.0,
                "data_api_nav_usd": 100.1,
                "onchain_nav_usd": 100.0,
                "max_pairwise_delta_usd": 0.1,
                "unexplained_discrepancy_usd": 0.0,
                "contracts_page_sha256": "abc",
                "rpc_chain_id": 137,
            }
        ],
    )
    write_json(
        performance / "wallet_reconciliation.json",
        {
            "generated_at_utc": "2026-07-15T05:59:00Z",
            "reconciliation_status": "clean",
            "internal_nav_usd": 106.0,
            "data_api_nav_usd": 106.1,
            "onchain_nav_usd": 106.0,
            "internal": {"status": "ok", "nav_usd": 106.0, "cash_usd": 10.0},
            "data_api": {"status": "ok", "nav_usd": 106.1, "reconstructed_cash_usd": 10.1},
            "onchain": {"status": "ok", "nav_usd": 106.0, "pusd_balance_usd": 10.0},
        },
    )
    write_csv(
        performance / "cost_ledger.csv",
        [
            {"date": "2026-07-14", "category": "rail", "usd": 1.5, "cost_ref": "rail:prior", "note": "prior"},
            {"date": "2026-07-15", "category": "gas", "usd": 0.25, "cost_ref": "gas:today", "note": "today"},
        ],
        fieldnames=["date", "category", "usd", "cost_ref", "note"],
    )


def test_stage_day_renders_complete_operator_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _config(tmp_path)
    _seed_complete_inputs(cfg)
    monkeypatch.setattr("polymarket_predictive_engine.stage_operator.now_utc", lambda: FIXED_NOW)

    result = build_stage_day(cfg)

    assert result["status"] == "ok"
    assert result["stage_date"] == "2026-07-15"
    assert result["order_tickets"]["state"] == "EXACT_HUMAN_TICKETS"
    assert result["requote"]["state"] == "quotes_ok"
    assert result["kill_scoreboard"]["status"] == "clear"
    assert result["yesterday_reconciliation"]["state"] == "clean"
    assert result["cost_delta"]["today_usd"] == 0.25
    assert result["cost_delta"]["yesterday_usd"] == 1.5
    assert result["cost_delta"]["delta_vs_yesterday_usd"] == -1.25
    assert result["a1_sweep_advisory"]["status"] == "sweep_advised"
    assert result["a1_sweep_advisory"]["suggested_sweep_usd"] == 6.0
    page = Path(result["page_path"]).read_text(encoding="utf-8")
    for heading in (
        "Order tickets (WO-66)",
        "Current requote / alert state",
        "Kill scoreboard",
        "Yesterday's reconciliation",
        "Cost ledger delta",
        "A1 excess-balance sweep advisory",
        "A1 sequencing and balance discipline",
        "Kill-criteria response",
    ):
        assert heading in page
    assert "https://polymarket.com/event/example" in page
    assert result["paper_trading_invoked"] is False
    assert result["live_trading_invoked"] is False


def test_recorded_human_actions_append_and_are_anchor_enrolled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _config(tmp_path)
    _seed_complete_inputs(cfg)
    monkeypatch.setattr("polymarket_predictive_engine.stage_operator.now_utc", lambda: FIXED_NOW)
    build_stage_day(cfg)

    first = record_stage_action(
        cfg,
        action_type="quote_placed",
        action_at_utc="2026-07-15T06:01:00Z",
        market_id="0xmarket",
        side="bid",
        price=0.48,
        size_shares=100,
        note="human venue action",
    )
    ledger = cfg.output_root / "execution" / "stage_operator_log.csv"
    prefix = ledger.read_bytes()
    second = record_stage_action(
        cfg,
        action_type="quote_pulled",
        action_at_utc="2026-07-15T06:05:00Z",
        market_id="0xmarket",
        side="bid",
        note="human cancel confirmed",
    )

    assert ledger.read_bytes().startswith(prefix)
    rows = read_csv_rows(ledger)
    assert [row["action_type"] for row in rows] == ["quote_placed", "quote_pulled"]
    assert len(first["page_sha256"]) == 64
    assert first["system_order_action_invoked"] is False
    assert first["paper_trading_invoked"] is False
    assert first["live_trading_invoked"] is False
    assert first["entry_id"] != second["entry_id"]
    assert {row["glob"]: row["mode"] for row in DEFAULT_LEDGER_REGISTRY}["execution/stage_operator_log.csv"] == "append_only"

    anchor = anchor_ledgers(cfg, anchor_date="2026-07-15")
    chain = read_csv_rows(cfg.output_root / "performance" / "ledger_anchor_chain.csv")
    manifest = json.loads(chain[-1]["ledger_manifest_json"])
    enrolled = next(row for row in manifest if row["path"] == "execution/stage_operator_log.csv")
    assert enrolled["mode"] == "append_only"
    assert enrolled["status"] == "present"
    assert anchor["paper_trading_invoked"] is False
    assert anchor["live_trading_invoked"] is False


def test_owner_activity_action_is_explicit_and_keeps_anchored_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _config(tmp_path)
    _seed_complete_inputs(cfg)
    monkeypatch.setattr("polymarket_predictive_engine.stage_operator.now_utc", lambda: FIXED_NOW)
    build_stage_day(cfg)

    row = record_stage_action(
        cfg,
        action_type="drill_trade",
        action_at_utc="2026-07-15T06:01:00Z",
        market_id="0xmarket",
        side="bid",
        price=0.48,
        size_shares=5,
        note="owner drill",
    )

    assert row["action_type"] == "drill_trade"
    assert row["human_action_recorded"] is True
    ledger = cfg.output_root / "execution" / "stage_operator_log.csv"
    assert list(read_csv_rows(ledger)[0]) == list(row)
    with pytest.raises(ValueError, match="exactly one of bid or ask"):
        record_stage_action(
            cfg,
            action_type="maintenance_trade",
            action_at_utc="2026-07-15T06:02:00Z",
            market_id="0xmarket",
            side="both",
            price=0.48,
            size_shares=5,
        )


def test_stage_day_action_is_logged_then_shown_on_regenerated_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _config(tmp_path)
    _seed_complete_inputs(cfg)
    monkeypatch.setattr("polymarket_predictive_engine.stage_operator.now_utc", lambda: FIXED_NOW)

    result = run_stage_day(
        cfg,
        action_type="no_action",
        action_at_utc="2026-07-15T06:10:00Z",
        note="opening checks completed; no quote entered",
    )

    assert result["recorded_action"]["action_type"] == "no_action"
    assert len(result["actions_today"]) == 1
    page = Path(result["page_path"]).read_text(encoding="utf-8")
    assert "opening checks completed; no quote entered" in page
    assert read_json(cfg.output_root / "execution" / "stage_day.json")["recorded_action"]["action_type"] == "no_action"


def test_missing_inputs_render_unknown_and_invalid_action_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _config(tmp_path)
    monkeypatch.setattr("polymarket_predictive_engine.stage_operator.now_utc", lambda: FIXED_NOW)

    result = build_stage_day(cfg)

    assert result["status"] == "incomplete_unknown_inputs"
    assert result["missing_inputs"] == ["maker_carry_study", "requote_alerts", "decision_policy"]
    assert result["order_tickets"]["state"] == "UNKNOWN"
    assert result["yesterday_reconciliation"]["state"] == "UNKNOWN"
    assert result["cost_delta"]["state"] == "UNKNOWN"
    assert "UNKNOWN" in Path(result["page_path"]).read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="requires market_id"):
        record_stage_action(
            cfg,
            action_type="quote_placed",
            action_at_utc="2026-07-15T06:01:00Z",
            side="bid",
            price=0.48,
            size_shares=100,
        )


def test_disabled_stage_operator_still_writes_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _config(tmp_path)
    cfg.raw["stage_operator"]["enabled"] = False
    monkeypatch.setattr("polymarket_predictive_engine.stage_operator.now_utc", lambda: FIXED_NOW)

    result = build_stage_day(cfg)

    assert result["status"] == "disabled"
    assert read_json(cfg.output_root / "execution" / "stage_day.json")["status"] == "disabled"
    assert Path(result["page_path"]).exists()
    assert result["paper_trading_invoked"] is False
    assert result["live_trading_invoked"] is False


def test_stage_day_is_registered_on_existing_harvest_and_runbook_is_explicit() -> None:
    assert "stage-day" in COMMANDS
    scheduler = (ROOT / "scripts" / "run_vps_ops_scheduler.sh").read_text(encoding="utf-8")
    assert "polymarket_predictive_engine.cli stage-day" in scheduler
    assert scheduler.index("polymarket_predictive_engine.cli stage-day") < scheduler.index(
        "polymarket_predictive_engine.cli anchor-ledgers"
    )
    runbook = (ROOT / "docs" / "HUMAN_STAGE1_OPERATOR_RUNBOOK.md").read_text(encoding="utf-8")
    assert "Portfolio" in runbook
    assert "Open Orders" in runbook
    assert "Cancel all" in runbook
    assert "record-action kill_acknowledged" in runbook
    assert "record-action drill_trade" in runbook
    assert "fixed five-minute time window" in runbook
    assert "cannot place, amend, cancel, sign, or authenticate" in runbook
    config = (ROOT / "polymarket_predictive_config.example.yaml").read_text(encoding="utf-8")
    assert "execution/stage_operator_log.csv" in config
    assert "maker_carry/maker_live_test_attribution_history.csv" in config
