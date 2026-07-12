from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from polymarket_predictive_engine.cli import COMMANDS
from polymarket_predictive_engine.config import EngineConfig, load_config
from polymarket_predictive_engine.dashboard import render_dashboard
from polymarket_predictive_engine.operating_state import (
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
        performance / "key_custody_approval.json",
        {
            "status": "approved",
            "scoped_trade_only": True,
            "withdrawal_disabled": True,
            "storage": "vps_env",
            "rotation_documented": True,
        },
    )
    write_json(
        performance / "vps_telemetry_manifest.json",
        {"pushed_at_utc": "2026-07-12T09:01:00Z", "deployed_git_rev": "abcdef1", "host": "paper-vps"},
    )
    write_csv(
        cfg.output_root / "polymarket_portfolio" / "paper_fills.csv",
        [{"fill_id": "paper-1"}, {"fill_id": "paper-2"}],
        fieldnames=["fill_id"],
    )
    write_csv(
        cfg.output_root / "maker_carry" / "maker_execution_ledger.csv",
        [],
        fieldnames=["order_id", "created_at_utc"],
    )
    owner_text = (
        "AUTONOMOUS_MAKER_EXECUTION_AUTHORISED = true\n"
        "AUTONOMOUS_MAKER_EXECUTION_AUTHORISED_AT = 2026-07-12\n"
    )
    (cfg.path.parent / "AGENTS.md").write_text(owner_text, encoding="utf-8")
    (cfg.path.parent / "CLAUDE.md").write_text(owner_text, encoding="utf-8")


def test_operating_state_derives_rows_and_wo67_preconditions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _config(tmp_path)
    _write_complete_evidence(cfg)
    monkeypatch.setenv("PM_VPS_DEPLOYED_SHA", "abcdef123456")
    monkeypatch.setenv("PM_IMAGE_BUILD_SHA", "abcdef1")

    result = build_operating_state(cfg)

    rows = {row["key"]: row for row in result["rows"]}
    preconditions = {row["id"]: row for row in result["wo67_preconditions"]}
    assert result["status"] == "ok"
    assert rows["research_mode"]["state"] == "ACTIVE_SHADOW_PAPER_GATED"
    assert rows["mechanical_paper_capability"]["state"] == "PRESENT"
    assert rows["governed_paper_authorisation"]["state"] == "NOT_GRANTED"
    assert rows["paper_activity"]["state"] == "RECORDED_FILLS=2"
    assert rows["live_wallet_monitoring"]["state"] == "ACTIVE_READ_ONLY:ok"
    assert rows["live_orders_submitted"]["state"] == "RECORDED_ORDERS=0"
    assert rows["latest_deployed_sha"]["state"] == "abcdef1"
    assert "aligned=True" in rows["latest_deployed_sha"]["evidence"]
    assert rows["autonomous_execution"]["state"] == "PRECONDITIONS_MET_EXECUTOR_NOT_IMPLEMENTED"
    assert {identifier: row["state"] for identifier, row in preconditions.items()} == {
        "P1": "met",
        "P2": "met",
        "P3": "met",
        "P4": "met",
        "P5": "met",
    }
    assert "consecutive_live_ok_days=7" in preconditions["P2"]["evidence"]
    assert result["paper_trading_invoked"] is False
    assert result["live_trading_invoked"] is False
    assert read_json(cfg.output_root / "performance" / "operating_state.json") == result
    markdown = (cfg.output_root / "performance" / "operating_state.md").read_text(encoding="utf-8")
    assert "Generated operating state" in markdown
    assert "WO-67 autonomous-execution preconditions" in markdown


def test_operating_state_missing_artifacts_are_unknown_never_guessed(tmp_path: Path) -> None:
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
    assert rows["autonomous_execution"]["state"] == "BLOCKED_UNKNOWN"
    assert "vps_telemetry_manifest" in result["missing_inputs"]
    assert all(row["state"] == "UNKNOWN" for row in result["wo67_preconditions"])


def test_front_door_drift_test_trips_on_planted_stale_claim() -> None:
    readme = "Generated state: `outputs/performance/operating_state.md`\n\n## What we learned\n"
    agents = "Generated state: `outputs/performance/operating_state.md`\n\n## Run model\n"
    assert front_door_drift_violations(readme_text=readme, agents_text=agents) == []

    violations = front_door_drift_violations(
        readme_text="Last project state update: 2026-07-01\n" + readme,
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

    render_dashboard(cfg)

    dashboard_data = read_json(cfg.output_root / "polymarket_dashboard" / "dashboard_data.json")
    html = (cfg.output_root / "polymarket_dashboard" / "index.html").read_text(encoding="utf-8")
    assert dashboard_data["operating_state"] == generated
    assert "Canonical operating state" in html
    assert 'id="operatingState"' in html
    assert "operating-state" in COMMANDS
    scheduler = (REPO_ROOT / "scripts" / "run_vps_ops_scheduler.sh").read_text(encoding="utf-8")
    assert "polymarket_predictive_engine.cli operating-state" in scheduler
    telemetry = (REPO_ROOT / "scripts" / "push_vps_telemetry.sh").read_text(encoding="utf-8")
    assert "outputs/performance/vps_telemetry_manifest.json" in telemetry


def test_operating_state_payload_remains_json_serializable_with_structured_evidence(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    result = build_operating_state(cfg)
    assert json.loads(json.dumps(result))["source"] == "point_in_time_config_and_artifacts"
