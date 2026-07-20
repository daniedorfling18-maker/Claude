from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from polymarket_predictive_engine.cli import COMMANDS
from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.dashboard import render_dashboard
from polymarket_predictive_engine.executor_ops_monitor import _policy_cap, build_executor_ops_status
from polymarket_predictive_engine.ledger_anchor import DEFAULT_LEDGER_REGISTRY
from polymarket_predictive_engine.operating_state import build_operating_state
from polymarket_predictive_engine.utils import read_json, write_csv, write_json


REPO_ROOT = Path(__file__).resolve().parents[2]
AS_OF = datetime(2026, 7, 13, 15, 0, tzinfo=timezone.utc)


def _cfg(tmp_path: Path) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "paper_trading": {"enabled": True},
            "live_trading": {"enabled": False},
            "trading": {"mode": "paper"},
            "decision_policy": {"stage0_capital_usd": 100.0},
            "wallet_reconciliation": {"discrepancy_threshold_usd": 1.0},
            "executor_ops": {
                "dead_man_gap_seconds": 1800,
                "executor_freshness_slo_seconds": 600,
            },
        },
        path=tmp_path / "config.yaml",
    )


def _stamp(seconds_ago: float) -> str:
    return (AS_OF - timedelta(seconds=seconds_ago)).isoformat().replace("+00:00", "Z")


def _write_policy_sizing(cfg: EngineConfig, binding) -> None:
    payload = {"generated_at_utc": _stamp(30)}
    if binding is not None:
        payload["sizing"] = {"binding_capital_usd": binding}
    write_json(cfg.output_root / "maker_carry" / "decision_policy.json", payload)


def test_policy_cap_honors_zero_binding_capital(tmp_path: Path) -> None:
    # #56: an explicit binding_capital_usd=0.0 (invalid Kelly history -> fail-closed)
    # must clamp the reported cap to 0, not be dropped in favor of a positive stage0.
    cfg = _cfg(tmp_path)
    _write_policy_sizing(cfg, 0.0)
    cap, source = _policy_cap(cfg, {"stage_cap_usd": 100.0})
    assert cap == 0.0
    assert source == "decision_policy.sizing.binding_capital_usd"


def test_policy_cap_nonfinite_binding_fails_closed(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write_policy_sizing(cfg, "nan")
    cap, _ = _policy_cap(cfg, {"stage_cap_usd": 100.0})
    assert cap == 0.0


def test_policy_cap_positive_binding_still_binds(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write_policy_sizing(cfg, 50.0)
    cap, source = _policy_cap(cfg, {"stage_cap_usd": 100.0})
    assert cap == 50.0
    assert source == "decision_policy.sizing.binding_capital_usd"


def test_policy_cap_absent_binding_uses_positive_fallback(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write_policy_sizing(cfg, None)  # no sizing key
    cap, _ = _policy_cap(cfg, {"stage_cap_usd": 100.0})
    assert cap == 100.0


def _seed_runtime(
    cfg: EngineConfig,
    *,
    heartbeat_age: float = 60,
    action_age: float = 120,
    mode: str = "canary",
    open_orders: int = 0,
    exposure_usd: float = 40.0,
    discrepancy_usd: float = 0.0,
    kill_triggered: bool = False,
    heartbeat: bool = True,
) -> None:
    write_csv(
        cfg.output_root / "execution" / "execution_ledger.csv",
        [
            {
                "recorded_at_utc": _stamp(action_age),
                "mode": mode,
                "action_type": "place",
                "open_orders_after": open_orders,
                "exposure_usd_after": exposure_usd,
                "stage_cap_usd": 100.0,
            }
        ],
    )
    if heartbeat:
        write_json(
            cfg.output_root / "execution" / "executor_heartbeat.json",
            {
                "heartbeat_at_utc": _stamp(heartbeat_age),
                "mode": mode,
                "cycle_id": "cycle-1",
                "executor_build_id": "candidate-sha",
            },
        )
    triggered = ["cumulative_net_score"] if kill_triggered else []
    write_json(
        cfg.output_root / "maker_carry" / "decision_policy.json",
        {
            "generated_at_utc": _stamp(30),
            "sizing": {"binding_capital_usd": 100.0},
            "kill_criteria_status": {
                "status": "triggered" if triggered else "clear",
                "triggered": triggered,
                "criteria": {
                    "cumulative_net_score": {
                        "triggered": kill_triggered,
                        "observed": -30.0 if kill_triggered else 0.0,
                        "threshold": -25.0,
                    }
                },
            },
        },
    )
    write_json(
        cfg.output_root / "performance" / "wallet_reconciliation.json",
        {
            "generated_at_utc": _stamp(30),
            "wallets": {
                "operator": {
                    "reconciliation_status": "DISCREPANCY" if discrepancy_usd > 1.0 else "clean",
                    "unexplained_discrepancy_usd": discrepancy_usd,
                }
            },
        },
    )


def test_absent_executor_is_explicit_and_does_not_create_runtime_inputs(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)

    result = build_executor_ops_status(cfg, as_of=AS_OF)

    assert result["status"] == "ABSENT"
    assert result["mode"] == "ABSENT"
    assert result["executor_present"] is False
    assert result["dead_man"]["state"] == "ABSENT"
    assert result["freshness_slo"]["state"] == "ABSENT"
    assert result["owner_alerts"] == []
    assert result["notify"] is False
    assert result["stop_propagation_binding_implemented"] is False
    assert result["post_amendment_item_2_implemented"] is False
    assert result["credential_loading_invoked"] is False
    assert result["paper_trading_invoked"] is False
    assert result["live_trading_invoked"] is False
    assert not (cfg.output_root / "execution" / "execution_ledger.csv").exists()
    assert not (cfg.output_root / "execution" / "executor_heartbeat.json").exists()


def test_healthy_canary_status_uses_executable_ledger_values(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _seed_runtime(cfg)

    result = build_executor_ops_status(cfg, as_of=AS_OF)

    assert result["status"] == "OK"
    assert result["mode"] == "CANARY"
    assert result["open_orders"] == 0
    assert result["exposure_usd"] == 40.0
    assert result["stage_cap_usd"] == 100.0
    assert result["exposure_vs_stage_cap_state"] == "OK"
    assert result["last_action_age_seconds"] == 120.0
    assert result["dead_man"]["state"] == "ARMED"
    assert result["dead_man"]["countdown_seconds"] == 1740.0
    assert result["freshness_slo"]["state"] == "OK"
    assert result["kill_criteria_scoreboard"]["status"] == "clear"
    assert result["owner_alerts"] == []


def test_registered_owner_alerts_emit_once_and_then_deduplicate(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _seed_runtime(
        cfg,
        heartbeat_age=1901,
        exposure_usd=101.0,
        discrepancy_usd=1.25,
        kill_triggered=True,
    )

    first = build_executor_ops_status(cfg, as_of=AS_OF)
    second = build_executor_ops_status(cfg, as_of=AS_OF)

    identifiers = {row["id"] for row in first["owner_alerts"]}
    assert {
        "kill_criteria_triggered",
        "dead_man_triggered",
        "executor_reconciliation_discrepancy",
        "executor_freshness_slo_breach",
        "executor_stage_cap_breach",
    } <= identifiers
    assert first["status"] == "ALERT"
    assert first["notification"]["pattern"] == "superbru_score_change_state_digest"
    assert first["notification"]["channel"] == "existing_email_wrapper"
    assert first["notify"] is True
    assert first["state_changed"] is True
    assert second["notify"] is False
    assert second["state_changed"] is False
    assert Path(first["body_file"]).exists()


def test_threshold_overrides_can_only_tighten(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg.raw["executor_ops"] = {
        "dead_man_gap_seconds": 99999,
        "executor_freshness_slo_seconds": 99999,
    }
    cfg.raw["wallet_reconciliation"]["discrepancy_threshold_usd"] = 999.0
    _seed_runtime(cfg, heartbeat_age=700, discrepancy_usd=2.0)

    result = build_executor_ops_status(cfg, as_of=AS_OF)
    identifiers = {row["id"] for row in result["owner_alerts"]}

    assert result["dead_man"]["gap_threshold_seconds"] == 1800.0
    assert result["freshness_slo"]["slo_seconds"] == 600.0
    assert result["executor_reconciliation"]["threshold_usd"] == 1.0
    assert result["executor_reconciliation"]["wallet_source_role"] == "operator_project_wallet_A1"
    assert "executor_freshness_slo_breach" in identifiers
    assert "executor_reconciliation_discrepancy" in identifiers
    assert "dead_man_triggered" not in identifiers


def test_missing_heartbeat_fails_closed_once_ledger_exists(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _seed_runtime(cfg, heartbeat=False)

    result = build_executor_ops_status(cfg, as_of=AS_OF)
    identifiers = {row["id"] for row in result["owner_alerts"]}

    assert result["status"] == "ALERT"
    assert result["dead_man"]["state"] == "TRIGGERED"
    assert result["freshness_slo"]["state"] == "BREACH"
    assert {"dead_man_triggered", "executor_freshness_slo_breach"} <= identifiers


def test_operating_state_and_dashboard_expose_absent_executor(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    monitor = build_executor_ops_status(cfg, as_of=AS_OF)

    operating = build_operating_state(cfg)
    rows = {row["key"]: row for row in operating["rows"]}

    for key in (
        "executor_mode",
        "executor_open_orders",
        "executor_exposure_vs_stage_cap",
        "executor_last_action_age",
        "executor_dead_man",
        "executor_freshness_slo",
        "executor_kill_criteria",
    ):
        assert rows[key]["state"] == "ABSENT"
    assert operating["executor_status"] == monitor

    rendered = render_dashboard(cfg)
    dashboard_data = read_json(Path(rendered["dashboard_data"]))
    dashboard_html = Path(rendered["dashboard_file"]).read_text(encoding="utf-8")
    assert dashboard_data["executor_status"]["status"] == "ABSENT"
    assert "Executor live-ops control plane" in dashboard_html
    assert 'id="executorOps"' in dashboard_html


def test_a1_human_advisory_does_not_turn_absent_executor_rows_unknown(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    write_json(
        cfg.output_root / "execution" / "executor_status.json",
        {
            "generated_at_utc": "2026-07-14T10:50:52Z",
            "status": "A1_ADVISORY",
            "mode": "ABSENT",
            "executor_present": False,
            "execution_ledger_rows": 0,
            "open_orders": None,
            "exposure_usd": None,
            "stage_cap_usd": 0.3065,
            "last_action_age_seconds": None,
            "dead_man": {"state": "ABSENT"},
            "freshness_slo": {"state": "ABSENT"},
            "kill_criteria_scoreboard": {"status": "clear", "triggered": []},
            "a1_sweep_advisory": {"status": "sweep_advised", "reporting_only": True},
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )

    operating = build_operating_state(cfg)
    rows = {row["key"]: row for row in operating["rows"]}

    for key in (
        "executor_mode",
        "executor_open_orders",
        "executor_exposure_vs_stage_cap",
        "executor_last_action_age",
        "executor_dead_man",
        "executor_freshness_slo",
        "executor_kill_criteria",
    ):
        assert rows[key]["state"] == "ABSENT"
    assert operating["executor_status"]["status"] == "A1_ADVISORY"


def test_scheduler_monitor_is_independent_and_refreshes_oversight_each_tick() -> None:
    scheduler = (REPO_ROOT / "scripts" / "run_vps_ops_scheduler.sh").read_text(encoding="utf-8")
    block = scheduler.split("run_degraded_state_watchdog() {", 1)[1].split(
        'log "vps-ops-scheduler starting', 1
    )[0]

    monitor = block.index("cli executor-ops-monitor")
    stamp = block.index("stamp_status executor_ops_monitor")
    watchdog = block.index("cli degraded-state-watchdog")
    operating = block.index("cli operating-state")
    dashboard = block.index("render_polymarket_dashboard.py")
    assert monitor < stamp < watchdog < operating < dashboard
    assert "executor_heartbeat.json" not in scheduler
    assert "execution_ledger.csv" not in scheduler


def test_wo75_is_registered_without_an_execution_client() -> None:
    source = (REPO_ROOT / "src" / "polymarket_predictive_engine" / "executor_ops_monitor.py").read_text(
        encoding="utf-8"
    )
    config = yaml.safe_load((REPO_ROOT / "polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    registry = {(row["glob"], row["mode"]) for row in DEFAULT_LEDGER_REGISTRY}

    assert "executor-ops-monitor" in COMMANDS
    assert config["executor_ops"] == {
        "dead_man_gap_seconds": 1800,
        "executor_freshness_slo_seconds": 600,
    }
    assert ("execution/execution_ledger.csv", "append_only") in registry
    assert ("execution/executor_status.json", "snapshot") in registry
    assert "import requests" not in source
    assert "from .execution" not in source
    assert "web3" not in source.lower()
    assert "clobclient" not in source.lower()
