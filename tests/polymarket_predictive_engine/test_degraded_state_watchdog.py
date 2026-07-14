"""WO-78 persistent degraded-state watchdog tests."""

from __future__ import annotations

import json
from pathlib import Path

from polymarket_predictive_engine.cli import COMMANDS
from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.degraded_state_watchdog import (
    INCIDENT_LEDGER,
    REGISTERED_MAXIMA,
    _settings,
    build_degraded_state_watchdog,
)
from polymarket_predictive_engine.ledger_anchor import (
    DEFAULT_LEDGER_REGISTRY,
    anchor_ledgers,
    verify_ledger_chain,
)
from polymarket_predictive_engine.utils import read_csv_rows, read_json, write_json


def _cfg(tmp_path: Path) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "degraded_state_watchdog": {"enabled": True, "notification_enabled": True},
            "ledger_anchor": {
                "enabled": True,
                "ledger_globs": [{"glob": INCIDENT_LEDGER, "mode": "append_only"}],
                "external_anchor_branch": "vps-anchor",
            },
        },
        path=tmp_path / "config.yaml",
    )


def _requote(cycle: int, *, rule: str, state: str = "pull_quotes_now") -> dict:
    return {
        "generated_at_utc": f"2026-07-13T00:0{cycle}:00Z",
        "alert_state": state,
        "markets": [
            {
                "condition_id": "condition-1",
                "alert_state": state,
                "alerts": [{"rule": rule, "message": "synthetic observation"}],
            }
        ],
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }


def test_registered_maxima_only_tighten() -> None:
    cfg = EngineConfig(
        raw={
            "degraded_state_watchdog": {
                "requote_missing_input_max_consecutive_cycles": 999,
                "wallet_partial_max_consecutive_harvests": 1,
                "maker_replay_insufficient_coverage_max_consecutive_cycles": 999,
            }
        },
        path=Path("config.yaml"),
    )

    settings = _settings(cfg)

    assert settings["requote_missing_input_max_consecutive_cycles"] == REGISTERED_MAXIMA[
        "requote_missing_input_max_consecutive_cycles"
    ]
    assert settings["wallet_partial_max_consecutive_harvests"] == 1
    assert settings["maker_replay_insufficient_coverage_max_consecutive_cycles"] == REGISTERED_MAXIMA[
        "maker_replay_insufficient_coverage_max_consecutive_cycles"
    ]


def test_requote_missing_input_trips_on_fourth_distinct_cycle_and_deduplicates(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    path = cfg.output_root / "maker_carry" / "requote_alerts.json"
    results = []
    for cycle in range(1, 5):
        write_json(path, _requote(cycle, rule="missing_live_bid_ask"))
        results.append(build_degraded_state_watchdog(cfg, as_of=f"2026-07-13T00:0{cycle}:30Z"))

    assert [item["status"] for item in results] == ["ok", "ok", "ok", "incident"]
    assert results[2]["evaluations"][0]["consecutive_degraded_observations"] == 3
    assert results[3]["evaluations"][0]["consecutive_degraded_observations"] == 4
    assert results[3]["new_incident_count"] == 1
    assert results[3]["notification"]["notify"] is True
    assert results[3]["paper_trading_invoked"] is False
    assert results[3]["live_trading_invoked"] is False

    repeated = build_degraded_state_watchdog(cfg, as_of="2026-07-13T00:05:00Z")
    assert repeated["status"] == "incident"
    assert repeated["new_incident_count"] == 0
    assert repeated["notification"]["notify"] is False
    assert len(read_csv_rows(cfg.output_root / INCIDENT_LEDGER)) == 1


def test_legitimate_risk_reason_and_transient_missing_input_do_not_incident(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    path = cfg.output_root / "maker_carry" / "requote_alerts.json"
    for cycle in range(1, 7):
        write_json(path, _requote(cycle, rule="scheduled_event_within_window"))
        result = build_degraded_state_watchdog(cfg, as_of=f"2026-07-13T00:0{cycle}:30Z")
        assert result["status"] == "ok"
    assert result["evaluations"][0]["risk_rules_ignored_by_watchdog"] == [
        "scheduled_event_within_window"
    ]

    for cycle in range(7, 10):
        write_json(path, _requote(cycle, rule="incomplete_order_ticket"))
        result = build_degraded_state_watchdog(cfg, as_of=f"2026-07-13T00:{cycle}:30Z")
        assert result["status"] == "ok"
    write_json(path, _requote(0, rule="scheduled_event_within_window", state="quotes_ok"))
    recovered = build_degraded_state_watchdog(cfg, as_of="2026-07-13T00:10:30Z")
    assert recovered["status"] == "ok"
    assert not (cfg.output_root / INCIDENT_LEDGER).exists()


def test_persistent_maker_replay_insufficient_coverage_trips_on_fourth_replay(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    path = cfg.output_root / "maker_carry" / "maker_fill_replay.json"
    results = []
    for cycle in range(1, 5):
        write_json(
            path,
            {
                "generated_at_utc": f"2026-07-13T00:0{cycle}:00Z",
                "status": "insufficient_coverage",
                "coverage_status": "insufficient_coverage",
                "simulated_fill_opportunities": 10,
                "coverage": {"windows_simulated": 30, "windows_covered": 0},
                "realism_ratio": "insufficient_coverage",
                "paper_trading_invoked": False,
                "live_trading_invoked": False,
            },
        )
        results.append(build_degraded_state_watchdog(cfg, as_of=f"2026-07-13T00:0{cycle}:30Z"))

    assert [result["status"] for result in results] == ["ok", "ok", "ok", "incident"]
    evaluation = next(
        row
        for row in results[-1]["evaluations"]
        if row["registration_id"] == "maker_replay_insufficient_coverage"
    )
    assert evaluation["consecutive_degraded_observations"] == 4
    assert results[-1]["active_incidents"][0]["entity"] == "maker_fill_replay"

    write_json(
        path,
        {
            "generated_at_utc": "2026-07-13T00:05:00Z",
            "status": "ok",
            "coverage_status": "covered",
        },
    )
    recovered = build_degraded_state_watchdog(cfg, as_of="2026-07-13T00:05:30Z")
    assert recovered["status"] == "ok"


def test_scheduler_nonzero_exit_is_immediate_and_recovery_clears_active_incident(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    status_path = cfg.output_root / "ops_scheduler" / "status.json"
    write_json(
        status_path,
        {
            "generated_at_utc": "2026-07-13T01:00:00Z",
            "jobs": {"governance_refresh": {"last_exit_code": 124, "last_run_utc": "2026-07-13T01:00:00Z"}},
        },
    )

    failed = build_degraded_state_watchdog(cfg, as_of="2026-07-13T01:00:01Z")

    assert failed["status"] == "incident"
    scheduler_eval = next(row for row in failed["evaluations"] if row["registration_id"] == "scheduler_nonzero_exit")
    assert scheduler_eval["failing_jobs"][0]["last_exit_code"] == 124

    write_json(
        status_path,
        {
            "generated_at_utc": "2026-07-13T01:05:00Z",
            "jobs": {"governance_refresh": {"last_exit_code": 0, "last_run_utc": "2026-07-13T01:05:00Z"}},
        },
    )
    recovered = build_degraded_state_watchdog(cfg, as_of="2026-07-13T01:05:01Z")
    assert recovered["status"] == "ok"
    assert len(read_csv_rows(cfg.output_root / INCIDENT_LEDGER)) == 1


def test_wallet_partial_counts_distinct_harvests_not_watchdog_polls(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    wallet_path = cfg.output_root / "performance" / "wallet_reconciliation.json"
    for harvest in range(1, 4):
        write_json(
            wallet_path,
            {
                "generated_at_utc": f"2026-07-{10 + harvest}T02:00:00Z",
                "reconciliation_status": "partial",
                "note": "one reconciliation leg unavailable",
            },
        )
        result = build_degraded_state_watchdog(cfg, as_of=f"2026-07-{10 + harvest}T02:00:01Z")
        if harvest < 3:
            assert result["status"] == "ok"
            polled = build_degraded_state_watchdog(cfg, as_of=f"2026-07-{10 + harvest}T02:04:01Z")
            wallet_eval = next(
                row for row in polled["evaluations"] if row["registration_id"] == "wallet_reconciliation_partial"
            )
            assert wallet_eval["consecutive_degraded_observations"] == harvest

    assert result["status"] == "incident"
    wallet_eval = next(row for row in result["evaluations"] if row["registration_id"] == "wallet_reconciliation_partial")
    assert wallet_eval["consecutive_degraded_observations"] == 3


def test_previously_known_operating_row_becoming_unknown_is_incident(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    operating_path = cfg.output_root / "performance" / "operating_state.json"
    write_json(
        operating_path,
        {
            "generated_at_utc": "2026-07-13T03:00:00Z",
            "rows": [{"key": "source_vs_deployed_sha", "state": "ALIGNED"}],
        },
    )
    baseline = build_degraded_state_watchdog(cfg, as_of="2026-07-13T03:00:01Z")
    assert baseline["status"] == "ok"

    write_json(
        operating_path,
        {
            "generated_at_utc": "2026-07-13T03:05:00Z",
            "rows": [{"key": "source_vs_deployed_sha", "state": "UNKNOWN"}],
        },
    )
    regressed = build_degraded_state_watchdog(cfg, as_of="2026-07-13T03:05:01Z")

    assert regressed["status"] == "incident"
    operating_eval = next(
        row for row in regressed["evaluations"] if row["registration_id"] == "operating_state_unknown_regression"
    )
    assert operating_eval["regressed_rows"] == ["source_vs_deployed_sha"]


def test_incident_ledger_is_registered_and_prefix_anchored(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    write_json(
        cfg.output_root / "ops_scheduler" / "status.json",
        {
            "generated_at_utc": "2026-07-13T04:00:00Z",
            "jobs": {"training_harvest": {"last_exit_code": 1, "last_run_utc": "2026-07-13T04:00:00Z"}},
        },
    )
    build_degraded_state_watchdog(cfg, as_of="2026-07-13T04:00:01Z")

    anchored = anchor_ledgers(cfg, anchor_date="2026-07-13")
    manifest = json.loads(read_csv_rows(cfg.output_root / "performance" / "ledger_anchor_chain.csv")[0]["ledger_manifest_json"])

    assert {"glob": INCIDENT_LEDGER, "mode": "append_only"} in DEFAULT_LEDGER_REGISTRY
    assert anchored["status"] == "ok"
    assert manifest[0]["path"] == INCIDENT_LEDGER
    assert manifest[0]["status"] == "present"
    assert verify_ledger_chain(cfg)["status"] == "ok"


def test_cli_scheduler_dashboard_and_operating_state_are_wired() -> None:
    scheduler = Path("scripts/run_vps_ops_scheduler.sh").read_text(encoding="utf-8")
    dashboard = Path("src/polymarket_predictive_engine/dashboard.py").read_text(encoding="utf-8")
    operating = Path("src/polymarket_predictive_engine/operating_state.py").read_text(encoding="utf-8")

    assert "degraded-state-watchdog" in COMMANDS
    assert "degraded-state-watchdog" in scheduler
    assert scheduler.index("run_degraded_state_watchdog") < scheduler.index('sleep "$TICK_SECONDS"')
    assert "Degraded-state watchdog registrations" in dashboard
    assert "degraded_state_watchdog" in operating


def test_persisted_watchdog_contract_has_no_trading_invocation(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)

    build_degraded_state_watchdog(cfg, as_of="2026-07-13T05:00:00Z")
    persisted = read_json(cfg.output_root / "ops_scheduler" / "degraded_state_watchdog.json")

    assert persisted["read_only"] is True
    assert persisted["paper_trading_invoked"] is False
    assert persisted["live_trading_invoked"] is False
    assert persisted["order_placement_invoked"] is False
    assert persisted["order_amendment_invoked"] is False
    assert persisted["order_cancellation_invoked"] is False
