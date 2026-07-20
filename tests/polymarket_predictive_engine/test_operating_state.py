from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from polymarket_predictive_engine.cli import COMMANDS
from polymarket_predictive_engine.config import EngineConfig, load_config
from polymarket_predictive_engine.dashboard import render_dashboard
from polymarket_predictive_engine.operating_state import (
    _latest_websocket_timestamp,
    _key_custody_approval,
    _owner_authorisation,
    assert_front_door_docs_state_pointer_only,
    build_operating_state,
    front_door_drift_violations,
)
from polymarket_predictive_engine.utils import read_json, write_csv, write_json


REPO_ROOT = Path(__file__).resolve().parents[2]


def _config(tmp_path: Path) -> EngineConfig:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True)
    return EngineConfig(
        raw={
            "paths": {"output_root": str(repo_root / "outputs")},
            "paper_trading": {"enabled": True},
            "live_trading": {"enabled": False},
            "trading": {"mode": "paper"},
            "maker_live_test": {"enabled": True, "wallet_address": "0xpublic"},
        },
        path=repo_root / "config.yaml",
    )


def _write_complete_evidence(cfg: EngineConfig) -> None:
    generated = "2026-07-12T09:00:00Z"
    write_json(
        cfg.governance_root / "local_history_audit_summary.json",
        {"generated_at_utc": generated, "paper_decision": {"paper_allowed": False, "reason": "registered gates remain closed"}},
    )
    write_json(cfg.governance_root / "paper_trade_readiness.json", {"generated_at_utc": generated, "decision": "paper_ready"})
    write_json(
        cfg.governance_root / "profit_verdict.json",
        {"generated_at_utc": generated, "verdict": "insufficient_evidence", "gates": {"A_edge_exists": {"state": "pending"}}},
    )
    maker_root = cfg.output_root / "maker_carry"
    write_json(
        maker_root / "maker_carry_study.json",
        {
            "generated_at_utc": generated,
            "maker_gates": {
                "maker_verdict": "pass",
                "M_A_carry_evidence": {"state": "pass"},
                "M_B_adverse_realism": {"state": "pass"},
                "M_C_payout_floor": {"state": "pass_by_construction"},
            },
        },
    )
    write_json(maker_root / "maker_live_test.json", {"generated_at_utc": generated, "status": "ok"})
    write_json(
        maker_root / "decision_policy.json",
        {
            "generated_at_utc": generated,
            "ladder_stage_permitted": 1,
            "ladder": {"stage": 1, "consecutive_live_ok_days": 7},
        },
    )
    performance = cfg.output_root / "performance"
    write_json(
        performance / "independent_merge_gate.json",
        {
            "status": "enforced",
            "enforced": True,
            "branch_protection_enabled": True,
            "independent_review_required": True,
        },
    )
    write_json(
        performance / "wallet_reconciliation.json",
        {
            "status": "ok",
            "reconciliation_status": "clean",
            "generated_at_utc": generated,
            "internal_nav_usd": 100.0,
            "data_api_nav_usd": 100.0,
            "onchain_nav_usd": 100.0,
            "internal": {"status": "ok", "nav_usd": 100.0, "cash_usd": 100.0},
            "data_api": {"status": "ok", "nav_usd": 100.0, "reconstructed_cash_usd": 100.0},
            "onchain": {"status": "ok", "nav_usd": 100.0, "pusd_balance_usd": 100.0},
        },
    )
    write_json(
        performance / "vps_telemetry_manifest.json",
        {
            "pushed_at_utc": "2026-07-12T09:01:00Z",
            "source_observed_at_utc": "2026-07-12T09:01:00Z",
            "source_git_rev": "abcdef1",
            "checkout_git_rev": "abcdef1",
            "deployed_git_rev": "abcdef1",
            "divergence_started_at_utc": "",
            "host": "paper-vps",
        },
    )
    write_json(
        cfg.output_root / "ops_scheduler" / "degraded_state_watchdog.json",
        {
            "status": "ok",
            "generated_at_utc": generated,
            "active_incident_count": 0,
            "new_incident_count": 0,
            "active_incidents": [],
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )
    write_csv(
        cfg.output_root / "polymarket_portfolio" / "paper_fills.csv",
        [
            {"fill_id": "paper-1", "order_id": "order-1"},
            {"fill_id": "paper-2", "order_id": "order-1"},
        ],
        fieldnames=["fill_id", "order_id"],
    )
    write_csv(
        cfg.output_root / "polymarket_portfolio" / "paper_orders.csv",
        [
            {
                "order_id": "order-1",
                "strategy_name": "price_action_round_trip",
                "source_signal_json": json.dumps(
                    {"price_action_entry_source": "paper_confirmation_current_candidate"}
                ),
            }
        ],
        fieldnames=["order_id", "strategy_name", "source_signal_json"],
    )
    write_csv(
        cfg.output_root / "maker_carry" / "maker_execution_ledger.csv",
        [],
        fieldnames=["order_id", "created_at_utc"],
    )
    owner_text = (
        "## Owner amendments\n\n"
        "AUTONOMOUS_MAKER_EXECUTION_AUTHORISED = true\n"
        "AUTONOMOUS_MAKER_EXECUTION_AUTHORISED_AT = 2026-07-12\n"
    )
    (cfg.path.parent / "AGENTS.md").write_text(owner_text, encoding="utf-8")
    (cfg.path.parent / "CLAUDE.md").write_text("Follow AGENTS.md.\n", encoding="utf-8")
    docs = cfg.path.parent / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "KEY_CUSTODY_DESIGN_WO67_P5.md").write_text(
        "# Custody\n\n"
        "**Status: APPROVED — Danie Dörfling, 2026-07-13.**\n\n"
        "## Amendment A1 — single project account\n\n"
        "**Status: APPROVED — Danie Dörfling, 2026-07-13.**\n",
        encoding="utf-8",
    )


def test_operating_state_derives_rows_and_wo67_preconditions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _config(tmp_path)
    _write_complete_evidence(cfg)
    monkeypatch.setenv("PM_VPS_DEPLOYED_SHA", "abcdef123456")
    monkeypatch.setenv("PM_IMAGE_BUILD_SHA", "abcdef1")
    monkeypatch.setattr("polymarket_predictive_engine.operating_state.now_utc", lambda: "2026-07-12T10:00:00Z")

    result = build_operating_state(cfg)

    rows = {row["key"]: row for row in result["rows"]}
    preconditions = {row["id"]: row for row in result["wo67_preconditions"]}
    assert result["status"] == "ok"
    assert rows["research_mode"]["state"] == "ACTIVE_SHADOW_PAPER_GATED"
    assert rows["mechanical_paper_capability"]["state"] == "READY"
    assert rows["mechanical_paper_capability"]["evidence"].startswith("mechanical_readiness=ready")
    assert "APPROVED_FOR_PAPER_TRADING" not in rows["mechanical_paper_capability"]["evidence"]
    assert rows["governed_paper_authorisation"]["state"] == "NOT_GRANTED"
    assert rows["paper_activity"]["state"] == "RECORDED_FILLS=2"
    assert rows["live_wallet_monitoring"]["state"] == "ACTIVE_READ_ONLY:ok"
    assert rows["operator_wallet_monitoring"]["state"] == "ACTIVE_READ_ONLY:ok"
    assert rows["executor_wallet_monitoring"]["state"].startswith("A1_SINGLE_PROJECT_WALLET:EXECUTOR_WINDOW_INACTIVE")
    assert rows["a1_sweep_advisory"]["state"] == "NOT_REQUIRED"
    assert rows["live_orders_submitted"]["state"] == "RECORDED_ORDERS=0"
    assert rows["latest_deployed_sha"]["state"] == "abcdef1"
    assert rows["source_vs_deployed_sha"]["state"] == "ALIGNED"
    assert "aligned=True" in rows["latest_deployed_sha"]["evidence"]
    assert rows["autonomous_execution"]["state"] == "PRECONDITIONS_MET_EXECUTOR_NOT_IMPLEMENTED"
    assert rows["degraded_state_watchdog"]["state"] == "OK"
    assert rows["deploy_acceptance"]["state"] == "NOT_RUN"
    assert {identifier: row["state"] for identifier, row in preconditions.items()} == {
        "P1": "met",
        "P2": "met",
        "P3": "met",
        "P4": "met",
        "P5": "met",
    }
    assert "consecutive_live_ok_days=7" in preconditions["P2"]["evidence"]
    assert result["paper_activity_lanes"] == {
        "price_action_round_trip:paper_confirmation_current_candidate": 2
    }
    assert result["paper_trading_invoked"] is False
    assert result["live_trading_invoked"] is False
    assert read_json(cfg.output_root / "performance" / "operating_state.json") == result
    markdown = (cfg.output_root / "performance" / "operating_state.md").read_text(encoding="utf-8")
    assert "Generated operating state" in markdown
    assert "WO-67 autonomous-execution preconditions" in markdown
    assert "Operating SLOs (reporting only)" in markdown
    assert "Degraded-state watchdog (reporting only)" in markdown


def test_operating_state_missing_artifacts_are_unknown_never_guessed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("PM_VPS_DEPLOYED_SHA", raising=False)
    monkeypatch.delenv("PM_IMAGE_BUILD_SHA", raising=False)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    cfg = EngineConfig(
        raw={"paths": {"output_root": str(repo_root / "outputs")}, "paper_trading": {}, "live_trading": {}, "trading": {}},
        path=repo_root / "config.yaml",
    )

    result = build_operating_state(cfg)

    rows = {row["key"]: row for row in result["rows"]}
    assert result["status"] == "incomplete_unknown_inputs"
    assert rows["research_mode"]["state"] == "UNKNOWN"
    assert rows["governed_paper_authorisation"]["state"] == "UNKNOWN"
    assert rows["paper_activity"]["state"] == "UNKNOWN"
    assert rows["live_orders_submitted"]["state"] == "UNKNOWN"
    assert rows["latest_deployed_sha"]["state"] == "UNKNOWN"
    assert rows["source_vs_deployed_sha"]["state"] == "UNKNOWN"
    assert rows["autonomous_execution"]["state"] == "BLOCKED_UNKNOWN"
    assert rows["degraded_state_watchdog"]["state"] == "UNKNOWN"
    assert rows["deploy_acceptance"]["state"] == "NOT_RUN"
    assert "vps_telemetry_manifest" in result["missing_inputs"]
    assert all(row["state"] == "UNKNOWN" for row in result["wo67_preconditions"])
    assert result["slo"]["unknown_count"] == 7
    assert all(row["breach"] is None for row in result["slo"]["rows"])


def test_p3_and_p5_exact_doc_patterns_distinguish_signed_unsigned_and_unreadable(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    docs = repo / "docs"
    docs.mkdir(parents=True)

    assert _owner_authorisation(repo)[0] == "UNKNOWN"
    assert _key_custody_approval(repo)[0] == "UNKNOWN"

    (repo / "AGENTS.md").write_text(
        "## Owner amendments\n\nAUTONOMOUS_MAKER_EXECUTION_AUTHORISED = false\n"
        "AUTONOMOUS_MAKER_EXECUTION_AUTHORISED_AT = YYYY-MM-DD\n",
        encoding="utf-8",
    )
    (docs / "KEY_CUSTODY_DESIGN_WO67_P5.md").write_text(
        "**Status: DRAFT for owner approval.**\n\n"
        "## Amendment A1 — single project account\n\n**Status: UNSIGNED.**\n",
        encoding="utf-8",
    )
    assert _owner_authorisation(repo)[0] == "not_met"
    assert _key_custody_approval(repo)[0] == "not_met"

    (repo / "AGENTS.md").write_text(
        "## Owner amendments\n\nAUTONOMOUS_MAKER_EXECUTION_AUTHORISED = true\n"
        "AUTONOMOUS_MAKER_EXECUTION_AUTHORISED_AT = 2026-07-17\n",
        encoding="utf-8",
    )
    (docs / "KEY_CUSTODY_DESIGN_WO67_P5.md").write_text(
        "**Status: APPROVED — Owner, 2026-07-13.**\n\n"
        "## Amendment A1 — single project account\n\n"
        "**Status: APPROVED — Owner, 2026-07-13.**\n",
        encoding="utf-8",
    )
    assert _owner_authorisation(repo)[0] == "met"
    assert _key_custody_approval(repo)[0] == "met"


def test_missing_p4_artifact_names_the_generator_instead_of_bare_unknown(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    _write_complete_evidence(cfg)
    (cfg.output_root / "performance" / "independent_merge_gate.json").unlink()

    result = build_operating_state(cfg)
    p4 = next(row for row in result["wo67_preconditions"] if row["id"] == "P4")

    assert p4["state"] == "UNKNOWN"
    assert "scripts/audit_github_merge_gate.py" in p4["evidence"]


def test_governed_paper_authorisation_fails_closed_after_one_day(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _config(tmp_path)
    _write_complete_evidence(cfg)
    monkeypatch.setattr("polymarket_predictive_engine.operating_state.now_utc", lambda: "2026-07-13T10:00:01Z")

    result = build_operating_state(cfg)
    row = {item["key"]: item for item in result["rows"]}["governed_paper_authorisation"]

    assert row["state"] == "NOT_GRANTED_STALE_EVIDENCE"
    assert "failed closed" in row["evidence"]
    assert "maximum=86400" in row["evidence"]


def test_failed_deploy_acceptance_is_explicit_in_operating_state(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    _write_complete_evidence(cfg)
    write_json(
        cfg.output_root / "ops_scheduler" / "deploy_acceptance.json",
        {
            "status": "FAIL",
            "generated_at_utc": "2026-07-13T12:00:00Z",
            "target_deploy_sha": "badsha",
            "rollback_ref": "goodsha",
            "checks": [{"id": "requote_evaluator_state", "status": "FAIL"}],
        },
    )

    result = build_operating_state(cfg)
    row = {item["key"]: item for item in result["rows"]}["deploy_acceptance"]

    assert row["state"] == "FAIL"
    assert "requote_evaluator_state" in row["evidence"]
    assert "rollback_ref=goodsha" in row["evidence"]


def test_operating_slos_measure_breaches_and_targets_only_tighten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _config(tmp_path)
    cfg.raw["operating_state_slos"] = {
        "dashboard_max_age_seconds": 9999,
        "websocket_max_gap_seconds": 60,
    }
    _write_complete_evidence(cfg)
    fixed_now = "2026-07-12T10:00:00Z"
    monkeypatch.setattr("polymarket_predictive_engine.operating_state.now_utc", lambda: fixed_now)

    quote_path = cfg.output_root / "maker_carry" / "maker_quote_sheet.md"
    quote_path.write_text("# Quote sheet\n", encoding="utf-8")
    quote_stamp = datetime(2026, 7, 12, 9, 59, tzinfo=timezone.utc).timestamp()
    os.utime(quote_path, (quote_stamp, quote_stamp))
    write_json(
        cfg.output_root / "ops_scheduler" / "status.json",
        {
            "generated_at_utc": "2026-07-12T09:59:50Z",
            "jobs": {
                "governance_refresh": {
                    "last_run_utc": "2026-07-12T09:59:50Z",
                    "duration_seconds": 2501,
                    "consecutive_skipped_overrun": 0,
                    "consecutive_skipped_intentional": 0,
                    "skipped_overrun_total": 0,
                    "skipped_intentional_total": 0,
                },
                "clv_snapshot": {
                    "consecutive_skipped_overrun": 0,
                    "consecutive_skipped_intentional": 1,
                    "skipped_overrun_total": 0,
                    "skipped_intentional_total": 3,
                },
                "trade_prints": {
                    "consecutive_skipped_overrun": 1,
                    "consecutive_skipped_intentional": 0,
                    "skipped_overrun_total": 1,
                    "skipped_intentional_total": 0,
                },
            },
        },
    )
    write_json(
        cfg.output_root / "polymarket_websocket" / "websocket_messages_latest.json",
        [{"collected_at_utc": "2026-07-12T09:59:30Z", "message": "{}"}],
    )
    write_json(
        cfg.output_root / "polymarket_dashboard" / "dashboard_data.json",
        {"generated_at_utc": "2026-07-12T09:54:00Z"},
    )
    write_json(
        cfg.output_root / "performance" / "wallet_reconciliation.json",
        {"generated_at_utc": "2026-07-12T09:00:00Z"},
    )
    write_json(
        cfg.output_root / "performance" / "ledger_anchor_head.json",
        {"anchored_at_utc": "2026-07-12T09:00:00Z"},
    )

    result = build_operating_state(cfg)
    slos = {row["id"]: row for row in result["slo"]["rows"]}

    assert result["slo"]["status"] == "breach"
    assert result["slo"]["breach_count"] == 3
    assert slos["governance_refresh_duration"]["breach"] is True
    assert slos["scheduler_overrun_cycles"]["breach"] is True
    assert result["slo"]["scheduler_skip_taxonomy"]["intentional_quota_or_preflight_skips_total"] == 3
    assert slos["dashboard_staleness"]["breach"] is True
    assert slos["websocket_gap"]["breach"] is False
    assert slos["dashboard_staleness"]["target"] == 300
    assert slos["websocket_gap"]["target"] == 60


def test_websocket_freshness_uses_last_message_not_refreshed_error_summary(
    tmp_path: Path,
) -> None:
    cfg = _config(tmp_path)
    websocket_root = cfg.output_root / "polymarket_websocket"
    write_json(websocket_root / "websocket_messages_latest.json", [])
    write_json(
        websocket_root / "websocket_messages.json",
        [{"collected_at_utc": "2026-07-12T09:00:00Z", "message": "{}"}],
    )
    write_json(
        websocket_root / "websocket_summary.json",
        {
            "generated_at_utc": "2026-07-12T09:59:59Z",
            "status": "error",
            "new_messages": 0,
        },
    )

    observed, raw, source = _latest_websocket_timestamp(cfg)

    assert observed == datetime(2026, 7, 12, 9, 0, tzinfo=timezone.utc)
    assert raw == "2026-07-12T09:00:00Z"
    assert source.endswith("websocket_messages.json")


def test_source_deployed_divergence_age_is_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _config(tmp_path)
    _write_complete_evidence(cfg)
    write_json(
        cfg.output_root / "performance" / "vps_telemetry_manifest.json",
        {
            "pushed_at_utc": "2026-07-12T09:55:00Z",
            "source_observed_at_utc": "2026-07-12T09:55:00Z",
            "source_git_rev": "f" * 40,
            "checkout_git_rev": "a" * 40,
            "deployed_git_rev": "a" * 40,
            "divergence_started_at_utc": "2026-07-12T09:30:00Z",
        },
    )
    monkeypatch.setattr("polymarket_predictive_engine.operating_state.now_utc", lambda: "2026-07-12T10:00:00Z")

    result = build_operating_state(cfg)
    rows = {row["key"]: row for row in result["rows"]}

    assert rows["source_vs_deployed_sha"]["state"] == "DIVERGED"
    assert result["deployment"]["divergence_age_seconds"] == 1800.0
    assert result["deployment"]["checkout_git_rev"] == "a" * 40


def test_front_door_drift_test_trips_on_planted_stale_claim() -> None:
    readme = "Generated state: `outputs/performance/operating_state.md`\n\n## What we learned\n"
    agents = "Generated state: `outputs/performance/operating_state.md`\n\n## Run model\n"
    assert front_door_drift_violations(readme_text=readme, agents_text=agents) == []

    violations = front_door_drift_violations(
        readme_text="Last project state update: 2026-07-01\n" + readme,
        agents_text=agents,
    )

    assert "README.md: dated_project_state" in violations


def test_readme_drift_after_historical_heading_is_still_rejected() -> None:
    readme = (
        "Generated state: `outputs/performance/operating_state.md`\n"
        "\n## What we learned\n"
        "\nLast project state update: 2026-07-01\n"
    )
    agents = "Generated state: `outputs/performance/operating_state.md`\n\n## Run model\n"

    violations = front_door_drift_violations(
        readme_text=readme,
        agents_text=agents,
    )

    assert "README.md: dated_project_state" in violations


def test_front_door_repo_docs_only_point_to_generated_state() -> None:
    assert_front_door_docs_state_pointer_only(REPO_ROOT)


def test_dashboard_and_daily_harvest_consume_generated_operating_state(tmp_path: Path) -> None:
    raw = yaml.safe_load((REPO_ROOT / "polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    cfg = load_config(config_path)
    generated = {
        "status": "incomplete_unknown_inputs",
        "rows": [{"question": "Latest deployed SHA", "state": "UNKNOWN", "evidence": "missing", "source": "telemetry"}],
        "wo67_preconditions": [{"id": "P1", "title": "Maker gates", "state": "UNKNOWN", "evidence": "missing"}],
    }
    write_json(cfg.output_root / "performance" / "operating_state.json", generated)
    acceptance = {"status": "FAIL", "target_deploy_sha": "badsha", "rollback_ref": "goodsha"}
    write_json(cfg.output_root / "ops_scheduler" / "deploy_acceptance.json", acceptance)

    render_dashboard(cfg)

    dashboard_data = read_json(cfg.output_root / "polymarket_dashboard" / "dashboard_data.json")
    html = (cfg.output_root / "polymarket_dashboard" / "index.html").read_text(encoding="utf-8")
    assert dashboard_data["operating_state"] == generated
    assert dashboard_data["deploy_acceptance"] == acceptance
    assert "Canonical operating state" in html
    assert "Latest deployment failed acceptance" in html
    assert 'id="operatingState"' in html
    assert "Operating SLOs (reporting only)" in html
    assert "operating-state" in COMMANDS
    scheduler = (REPO_ROOT / "scripts" / "run_vps_ops_scheduler.sh").read_text(encoding="utf-8")
    assert "polymarket_predictive_engine.cli operating-state" in scheduler
    telemetry = (REPO_ROOT / "scripts" / "push_vps_telemetry.sh").read_text(encoding="utf-8")
    assert "outputs/performance/vps_telemetry_manifest.json" in telemetry


def test_operating_state_payload_remains_json_serializable_with_structured_evidence(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    result = build_operating_state(cfg)
    assert json.loads(json.dumps(result))["source"] == "point_in_time_config_and_artifacts"
