from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from polymarket_predictive_engine import requote_alerts
from polymarket_predictive_engine.artifact_contracts import (
    CONTRACT_REGISTRY,
    validate_contract_declarations,
    validate_contract_fixture,
)
from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.degraded_state_watchdog import build_degraded_state_watchdog
from polymarket_predictive_engine.deploy_acceptance import build_deploy_acceptance
from polymarket_predictive_engine.requote_alerts import build_requote_alerts
from polymarket_predictive_engine.utils import read_json, write_json
from polymarket_predictive_engine.wallet_reconciliation import _reconciliation_state


AS_OF = datetime(2026, 7, 13, 12, 2, tzinfo=timezone.utc)


class _Response:
    def __init__(self, payload: Any):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self.payload


def _cfg(tmp_path: Path) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "requote_alerts": {
                "enabled": True,
                "query_resolution_status": True,
                "notification_enabled": False,
            },
            "degraded_state_watchdog": {"notification_enabled": True},
            "deploy_acceptance": {"notification_enabled": True},
        },
        path=tmp_path / "cfg.yaml",
    )


def _ticket() -> dict[str, Any]:
    return {
        "condition_id": "0xmarket",
        "question": "Will Team A win?",
        "market_url": "https://polymarket.com/event/team-a-win",
        "outcome": "Yes",
        "token_id": "token-yes",
        "order_price_min_tick_size": 0.01,
        "quote_bid_price": 0.48,
        "quote_ask_price": 0.52,
        "quote_distance": 0.02,
        "reference_mid_price": 0.5,
        "quote_size_shares": 100,
        "capital_usd": 50,
        "order_ticket_status": "exact",
    }


def _seed_acceptance(
    cfg: EngineConfig,
    *,
    requote_state: str = "quotes_ok",
    requote_rule: str | None = None,
    post_operating_state: str = "ALIGNED",
) -> None:
    repo_root = cfg.path.resolve().parent
    (repo_root / "AGENTS.md").write_text(
        "Generated state: `outputs/performance/operating_state.md`\n",
        encoding="utf-8",
    )
    (repo_root / "CLAUDE.md").write_text("Follow AGENTS.md.\n", encoding="utf-8")
    docs = repo_root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "KEY_CUSTODY_DESIGN_WO67_P5.md").write_text(
        "# Custody\n\n## Amendment A1 — single project account\n",
        encoding="utf-8",
    )
    write_json(
        cfg.output_root / "ops_scheduler" / "deploy_acceptance_baseline.json",
        {
            "captured_at_utc": "2026-07-13T12:00:00Z",
            "previous_checkout_sha": "oldsha",
            "previous_deployed_sha": "oldsha",
            "target_deploy_sha": "newsha",
            "operating_state": {
                "generated_at_utc": "2026-07-13T11:59:00Z",
                "rows": [{"key": "source_vs_deployed_sha", "state": "ALIGNED"}],
            },
        },
    )
    write_json(
        cfg.output_root / "ops_scheduler" / "deploy_acceptance_cycle.json",
        {
            "generated_at_utc": "2026-07-13T12:01:00Z",
            "commands": {
                name: {"exit_code": 0}
                for name in (
                    "maker_carry_study",
                    "collect_maker_replay_data",
                    "maker_fill_replay",
                    "decision_policy",
                    "requote_alerts",
                    "reconcile_wallet",
                    "executor_ops_monitor",
                    "operating_state",
                )
            },
        },
    )
    ticket = _ticket()
    write_json(
        cfg.output_root / "maker_carry" / "maker_carry_study.json",
        {"generated_at_utc": "2026-07-13T12:00:30Z", "portfolio": [ticket]},
    )
    write_json(
        cfg.output_root / "maker_carry" / "maker_replay_collection.json",
        {
            "generated_at_utc": "2026-07-13T12:00:40Z",
            "status": "ok",
            "market_windows": [
                {
                    "condition_id": "0xmarket",
                    "asset_id": "token-yes",
                    "collected_at_utc": "2026-07-13T12:00:40Z",
                    "book_poll_status": "ok",
                    "trade_poll_status": "ok",
                    "covered": True,
                }
            ],
        },
    )
    alert = (
        [{"state": requote_state, "rule": requote_rule, "message": "registered blocker"}]
        if requote_rule
        else []
    )
    write_json(
        cfg.output_root / "maker_carry" / "requote_alerts.json",
        {
            "generated_at_utc": "2026-07-13T12:01:10Z",
            "status": "ok",
            "alert_state": requote_state,
            "markets": [{**ticket, "alerts": alert, "alert_state": requote_state}],
            "kill_criteria_triggered": [],
        },
    )
    leg = {"status": "ok", "nav_usd": 100.0}
    write_json(
        cfg.output_root / "performance" / "wallet_reconciliation.json",
        {
            "generated_at_utc": "2026-07-13T12:01:20Z",
            "reconciliation_status": "clean",
            "internal_nav_usd": 100.0,
            "data_api_nav_usd": 100.0,
            "onchain_nav_usd": 100.0,
            "internal": leg,
            "data_api": leg,
            "onchain": leg,
        },
    )
    write_json(
        cfg.output_root / "performance" / "operating_state.json",
        {
            "generated_at_utc": "2026-07-13T12:01:30Z",
            "rows": [{"key": "source_vs_deployed_sha", "state": post_operating_state}],
        },
    )


def test_registered_contracts_are_satisfiable_on_synthetic_fixtures() -> None:
    assert [row["id"] for row in CONTRACT_REGISTRY] == [
        "quote_sheet_to_requote_alerts",
        "scheduler_jobs_to_status_alerting",
        "reconciliation_legs_to_nav",
        "maker_replay_collection_to_fill_replay",
        "executor_runtime_to_ops_status",
    ]
    assert validate_contract_declarations() == []

    assert validate_contract_fixture(
        "quote_sheet_to_requote_alerts",
        {"portfolio": [_ticket()]},
    )["status"] == "PASS"
    assert validate_contract_fixture(
        "scheduler_jobs_to_status_alerting",
        {"jobs": {"governance_refresh": {"last_run_utc": "2026-07-13T12:00:00Z", "last_exit_code": 0}}},
    )["status"] == "PASS"
    reconciliation = {
        "reconciliation_status": "clean",
        "internal_nav_usd": 100.0,
        "data_api_nav_usd": 100.0,
        "onchain_nav_usd": 100.0,
        "internal": {"status": "ok", "nav_usd": 100.0},
        "data_api": {"status": "ok", "nav_usd": 100.0},
        "onchain": {"status": "ok", "nav_usd": 100.0},
    }
    assert validate_contract_fixture("reconciliation_legs_to_nav", reconciliation)["status"] == "PASS"
    assert _reconciliation_state(
        reconciliation["internal"], reconciliation["data_api"], reconciliation["onchain"], threshold=0.01
    )["reconciliation_status"] == "clean"
    maker_replay_fixture = {
        "portfolio": [_ticket()],
        "market_windows": [
            {
                "condition_id": "0xmarket",
                "asset_id": "token-yes",
                "collected_at_utc": "2026-07-13T12:00:00Z",
                "book_poll_status": "ok",
                "trade_poll_status": "ok",
                "covered": True,
            }
        ],
    }
    assert validate_contract_fixture(
        "maker_replay_collection_to_fill_replay", maker_replay_fixture
    )["status"] == "PASS"
    maker_replay_fixture["market_windows"] = []
    assert validate_contract_fixture(
        "maker_replay_collection_to_fill_replay", maker_replay_fixture
    )["status"] == "FAIL"
    executor_fixture = {
        "execution_ledger": [
            {
                "recorded_at_utc": "2026-07-13T12:00:00Z",
                "mode": "canary",
                "action_type": "place",
                "open_orders_after": 1,
                "exposure_usd_after": 25.0,
            }
        ],
        "heartbeat": {
            "heartbeat_at_utc": "2026-07-13T12:00:01Z",
            "mode": "canary",
            "cycle_id": "cycle-1",
            "executor_build_id": "candidate-sha",
        },
    }
    assert validate_contract_fixture("executor_runtime_to_ops_status", executor_fixture)["status"] == "PASS"
    executor_fixture["heartbeat"] = {"mode": "portfolio"}
    assert validate_contract_fixture("executor_runtime_to_ops_status", executor_fixture)["status"] == "FAIL"


def test_quote_contract_covers_sheet_market_absent_from_socket_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(tmp_path)
    maker = cfg.output_root / "maker_carry"
    maker.mkdir(parents=True, exist_ok=True)
    (maker / "maker_quote_sheet.md").write_text("# Quote sheet\n", encoding="utf-8")
    write_json(maker / "maker_carry_study.json", {"generated_at_utc": "2026-07-13T12:00:00Z", "portfolio": [_ticket()]})
    write_json(maker / "decision_policy.json", {"kill_criteria_status": {"status": "clear", "triggered": []}})

    def fake_get(url: str, params: dict[str, Any], timeout: float) -> _Response:
        return _Response([{"conditionId": "0xmarket", "closed": False}])

    def fake_post(url: str, json: list[dict[str, str]], timeout: float) -> _Response:
        assert json == [{"token_id": "token-yes"}]
        return _Response(
            [
                {
                    "asset_id": "token-yes",
                    "market": "0xmarket",
                    "tick_size": "0.01",
                    "bids": [{"price": "0.49", "size": "100"}],
                    "asks": [{"price": "0.51", "size": "100"}],
                }
            ]
        )

    monkeypatch.setattr(requote_alerts.requests, "get", fake_get)
    monkeypatch.setattr(requote_alerts.requests, "post", fake_post)
    result = build_requote_alerts(cfg, as_of=AS_OF)

    assert result["alert_state"] == "quotes_ok"
    assert result["markets_checked"] == 1
    assert result["markets"][0]["condition_id"] == "0xmarket"
    assert result["markets"][0]["live_price_source"] == "rest_book_fallback"
    assert result["markets"][0]["order_ticket_status"] == "exact"


def test_scheduler_contract_turns_nonzero_exit_into_immediate_owner_incident(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    write_json(
        cfg.output_root / "ops_scheduler" / "status.json",
        {
            "generated_at_utc": "2026-07-13T12:00:00Z",
            "jobs": {"training_harvest": {"last_run_utc": "2026-07-13T12:00:00Z", "last_exit_code": 124}},
        },
    )

    result = build_degraded_state_watchdog(cfg, as_of="2026-07-13T12:01:00Z")

    scheduler = next(row for row in result["evaluations"] if row["registration_id"] == "scheduler_nonzero_exit")
    assert scheduler["state"] == "incident"
    assert result["new_incident_count"] == 1
    assert result["notification"]["notify"] is True


def test_real_data_deploy_acceptance_passes_and_records_rollback_ref(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _seed_acceptance(cfg)

    result = build_deploy_acceptance(cfg, expected_deploy_sha="newsha", as_of=AS_OF)

    assert result["status"] == "PASS"
    assert result["rollback_ref"] == "oldsha"
    assert all(row["status"] == "PASS" for row in result["checks"])


def test_deploy_acceptance_fails_when_maker_replay_collection_misses_sheet_market(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _seed_acceptance(cfg)
    write_json(
        cfg.output_root / "maker_carry" / "maker_replay_collection.json",
        {
            "generated_at_utc": "2026-07-13T12:00:40Z",
            "status": "failed",
            "market_windows": [],
        },
    )

    result = build_deploy_acceptance(cfg, expected_deploy_sha="newsha", as_of=AS_OF)

    assert result["status"] == "FAIL"
    check = next(row for row in result["checks"] if row["id"] == "maker_replay_exact_portfolio_collection")
    assert check["status"] == "FAIL"
    assert check["defects"]
    assert {
        row["id"]: row["status"] for row in result["checks"]
    }["governance_documents_mounted_read_only"] == "PASS"
    assert result["notification"]["notify"] is False
    assert result["paper_trading_invoked"] is False
    assert result["live_trading_invoked"] is False
    assert read_json(cfg.output_root / "ops_scheduler" / "deploy_acceptance.json")["status"] == "PASS"


def test_deploy_acceptance_fails_when_governance_docs_are_not_mounted(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _seed_acceptance(cfg)
    (cfg.path.resolve().parent / "AGENTS.md").unlink()

    result = build_deploy_acceptance(cfg, expected_deploy_sha="newsha", as_of=AS_OF)
    check = {row["id"]: row for row in result["checks"]}["governance_documents_mounted_read_only"]

    assert result["status"] == "FAIL"
    assert check["status"] == "FAIL"
    assert check["defects"][0]["path"] == "AGENTS.md"


def test_registered_risk_blocker_passes_but_missing_input_and_new_unknown_fail(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _seed_acceptance(
        cfg,
        requote_state="pull_quotes_now",
        requote_rule="scheduled_event_within_window",
    )
    legitimate = build_deploy_acceptance(cfg, expected_deploy_sha="newsha", as_of=AS_OF)
    assert legitimate["status"] == "PASS"

    _seed_acceptance(
        cfg,
        requote_state="requote_advised",
        requote_rule="missing_live_bid_ask",
        post_operating_state="UNKNOWN",
    )
    failed = build_deploy_acceptance(cfg, expected_deploy_sha="newsha", as_of=AS_OF)

    assert failed["status"] == "FAIL"
    assert failed["notification"]["notify"] is True
    by_id = {row["id"]: row for row in failed["checks"]}
    assert by_id["requote_evaluator_state"]["status"] == "FAIL"
    assert by_id["operating_state_unknown_regression"]["new_unknown_rows"] == ["source_vs_deployed_sha"]
