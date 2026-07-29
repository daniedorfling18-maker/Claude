"""WO-78 persistent degraded-state watchdog tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from polymarket_predictive_engine.cli import COMMANDS
from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.degraded_state_watchdog import (
    INCIDENT_LEDGER,
    LEGACY_WALLET_REGISTRATION_ID,
    REGISTERED_MAXIMA,
    WALLET_HEALTHY_STATES,
    WALLET_REGISTRATION_ID,
    _settings,
    build_degraded_state_watchdog as _build_degraded_state_watchdog,
)
from polymarket_predictive_engine.ledger_anchor import (
    DEFAULT_LEDGER_REGISTRY,
    anchor_ledgers,
    verify_ledger_chain,
)
from polymarket_predictive_engine.runtime_lock import acquire_runtime_lock, release_runtime_lock
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


def build_degraded_state_watchdog(cfg: EngineConfig, *, as_of=None):
    """Seed unrelated producer evidence for focused legacy registrations."""
    stamp = str(as_of or "2026-07-29T00:00:00Z")
    dr = cfg.output_root / "performance" / "disaster_recovery_status.json"
    books = cfg.output_root / "maker_carry" / "official_book_snapshot.json"
    current_dr = read_json(dr, default={}) or {}
    if not dr.exists() or current_dr.get("_test_seeded"):
        write_json(dr, {"status": "ok", "remote_push_status": "ok", "rpo": {"compliant": True}, "generated_at_utc": stamp, "_test_seeded": True})
    if not books.exists():
        write_json(books, {"status": "disabled", "generated_at_utc": stamp})
    return _build_degraded_state_watchdog(cfg, as_of=as_of)


@pytest.fixture(autouse=True)
def _offline_notification_environment(monkeypatch) -> None:
    # The offline suite must never inherit the production-only notification URL.
    monkeypatch.delenv("OPS_OWNER_NTFY_TOPIC_URL", raising=False)


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
                "wallet_not_clean_max_consecutive_harvests": 1,
                "maker_replay_insufficient_coverage_max_consecutive_cycles": 999,
            }
        },
        path=Path("config.yaml"),
    )

    settings = _settings(cfg)

    assert settings["requote_missing_input_max_consecutive_cycles"] == REGISTERED_MAXIMA["requote_missing_input_max_consecutive_cycles"]
    assert settings["wallet_not_clean_max_consecutive_harvests"] == 1
    assert (
        settings["maker_replay_insufficient_coverage_max_consecutive_cycles"] == REGISTERED_MAXIMA["maker_replay_insufficient_coverage_max_consecutive_cycles"]
    )

    legacy = EngineConfig(
        raw={"degraded_state_watchdog": {"wallet_partial_max_consecutive_harvests": 1}},
        path=Path("legacy-config.yaml"),
    )
    assert _settings(legacy)["wallet_not_clean_max_consecutive_harvests"] == 1


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



def _write_healthy_slo(cfg: EngineConfig, generated_at: str) -> None:
    """Multi-day fixtures simulate a RUNNING host, which always has operating
    state; without it the (correct) post-grace SLO incident drowns the
    registration actually under test."""
    write_json(
        cfg.output_root / "performance" / "operating_state.json",
        {
            "generated_at_utc": generated_at,
            "slo": {
                "rows": [
                    {"id": row_id, "breach": False}
                    for row_id in (
                        "quote_sheet_age",
                        "governance_refresh_duration",
                        "scheduler_overrun_cycles",
                        "websocket_gap",
                        "dashboard_staleness",
                        "reconciliation_age",
                        "ledger_anchor_age",
                    )
                ]
            },
        },
    )
    for bridge in ("anchor", "telemetry"):
        write_json(
            cfg.output_root / "performance" / f"{bridge}_push_status.json",
            {"generated_at_utc": generated_at, "status": "ok",
             "paper_trading_invoked": False, "live_trading_invoked": False},
        )

def test_legitimate_risk_reason_and_transient_missing_input_do_not_incident(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write_healthy_slo(cfg, "2026-07-13T00:00:00Z")
    path = cfg.output_root / "maker_carry" / "requote_alerts.json"
    for cycle in range(1, 7):
        write_json(path, _requote(cycle, rule="scheduled_event_within_window"))
        result = build_degraded_state_watchdog(cfg, as_of=f"2026-07-13T00:0{cycle}:30Z")
        assert result["status"] == "ok"
    assert result["evaluations"][0]["risk_rules_ignored_by_watchdog"] == ["scheduled_event_within_window"]

    for cycle in range(7, 10):
        write_json(path, _requote(cycle, rule="incomplete_order_ticket"))
        _write_healthy_slo(cfg, f"2026-07-13T00:{cycle:02d}:00Z")
        result = build_degraded_state_watchdog(cfg, as_of=f"2026-07-13T00:{cycle:02d}:30Z")
        assert result["status"] == "ok"
    write_json(path, _requote(0, rule="scheduled_event_within_window", state="quotes_ok"))
    recovered = build_degraded_state_watchdog(cfg, as_of="2026-07-13T00:10:30Z")
    assert recovered["status"] == "ok"
    assert not (cfg.output_root / INCIDENT_LEDGER).exists()


def test_missing_rule_on_advisory_market_does_not_inherit_global_pull_state(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    path = cfg.output_root / "maker_carry" / "requote_alerts.json"
    for cycle in range(1, 5):
        write_json(
            path,
            {
                "generated_at_utc": f"2026-07-13T00:0{cycle}:00Z",
                "alert_state": "pull_quotes_now",
                "markets": [
                    {
                        "condition_id": "risk-market",
                        "alert_state": "pull_quotes_now",
                        "alerts": [{"rule": "scheduled_event_within_window"}],
                    },
                    {
                        "condition_id": "missing-input-market",
                        "alert_state": "requote_advised",
                        "alerts": [{"rule": "missing_live_bid_ask"}],
                    },
                ],
            },
        )
        result = build_degraded_state_watchdog(cfg, as_of=f"2026-07-13T00:0{cycle}:30Z")

    evaluation = result["evaluations"][0]
    assert result["status"] == "ok"
    assert evaluation["consecutive_degraded_observations"] == 0
    assert evaluation["missing_input_rules"] == ["missing_live_bid_ask"]
    assert evaluation["triggering_missing_input_rules"] == []
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
    evaluation = next(row for row in results[-1]["evaluations"] if row["registration_id"] == "maker_replay_insufficient_coverage")
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


def test_live_kill_input_staleness_is_immediate_incident_and_owner_alert(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    policy_path = cfg.output_root / "maker_carry" / "decision_policy.json"
    write_json(
        policy_path,
        {
            "generated_at_utc": "2026-07-14T11:00:00Z",
            "indicated_action": "stop_quoting_review_before_resume",
            "kill_data_stale": True,
            "kill_criteria_status": {
                "status": "triggered",
                "kill_data_stale": True,
                "kill_input_freshness": {
                    "state": "stale",
                    "guard_active": True,
                    "latest_observation_utc": "2026-07-14T10:00:00Z",
                    "age_seconds": 3600.0,
                    "maximum_age_seconds": 1800.0,
                },
            },
        },
    )

    result = build_degraded_state_watchdog(cfg, as_of="2026-07-14T11:00:01Z")
    evaluation = next(row for row in result["evaluations"] if row["registration_id"] == "kill_input_stale_live_stage")
    incident = next(row for row in result["active_incidents"] if row["registration_id"] == "kill_input_stale_live_stage")

    assert result["status"] == "incident"
    assert result["new_incident_count"] == 1
    assert result["notification"]["notify"] is True
    assert evaluation["state"] == "incident"
    assert evaluation["kill_data_stale"] is True
    assert evaluation["indicated_action"] == "stop_quoting_review_before_resume"
    assert incident["owner_notification_eligible"] is True
    assert "measured age 3600.0 seconds" in incident["reason"]

    repeated = build_degraded_state_watchdog(cfg, as_of="2026-07-14T11:05:00Z")
    assert repeated["status"] == "incident"
    assert repeated["new_incident_count"] == 0
    assert repeated["notification"]["notify"] is False
    assert len(read_csv_rows(cfg.output_root / INCIDENT_LEDGER)) == 1


def test_wallet_partial_counts_distinct_harvests_not_watchdog_polls(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write_healthy_slo(cfg, "2026-07-11T00:00:00Z")
    wallet_path = cfg.output_root / "performance" / "wallet_reconciliation.json"
    for harvest in range(1, 4):
        _write_healthy_slo(cfg, f"2026-07-{10 + harvest}T02:00:00Z")
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
            wallet_eval = next(row for row in polled["evaluations"] if row["registration_id"] == WALLET_REGISTRATION_ID)
            assert wallet_eval["consecutive_degraded_observations"] == harvest

    assert result["status"] == "incident"
    wallet_eval = next(row for row in result["evaluations"] if row["registration_id"] == WALLET_REGISTRATION_ID)
    assert wallet_eval["consecutive_degraded_observations"] == 3


def test_wallet_discrepancy_and_unknown_status_are_outside_healthy_allowlist(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write_healthy_slo(cfg, "2026-07-11T00:00:00Z")
    wallet_path = cfg.output_root / "performance" / "wallet_reconciliation.json"
    for harvest in range(1, 4):
        _write_healthy_slo(cfg, f"2026-07-{10 + harvest}T03:00:00Z")
        write_json(
            wallet_path,
            {
                "generated_at_utc": f"2026-07-{10 + harvest}T03:00:00Z",
                "reconciliation_status": "DISCREPANCY",
                "discrepancy_note": "synthetic normalized NAV mismatch",
            },
        )
        result = build_degraded_state_watchdog(cfg, as_of=f"2026-07-{10 + harvest}T03:00:01Z")

    assert result["status"] == "incident"
    wallet_eval = next(row for row in result["evaluations"] if row["registration_id"] == WALLET_REGISTRATION_ID)
    assert wallet_eval["observed_state"] == "DISCREPANCY"
    assert wallet_eval["healthy_reachable_states"] == sorted(WALLET_HEALTHY_STATES)
    assert result["active_incidents"][0]["reason"] == "synthetic normalized NAV mismatch"

    _write_healthy_slo(cfg, "2026-07-14T03:05:00Z")
    write_json(
        wallet_path,
        {
            "generated_at_utc": "2026-07-14T03:05:00Z",
            "reconciliation_status": "clean",
        },
    )
    assert build_degraded_state_watchdog(cfg, as_of="2026-07-14T03:05:01Z")["status"] == "ok"

    write_json(
        wallet_path,
        {
            "generated_at_utc": "2026-07-14T03:10:00Z",
            "reconciliation_status": "future_unknown_terminal_state",
        },
    )
    unknown = build_degraded_state_watchdog(cfg, as_of="2026-07-14T03:10:01Z")
    wallet_eval = next(row for row in unknown["evaluations"] if row["registration_id"] == WALLET_REGISTRATION_ID)
    assert wallet_eval["state"] == "degraded_within_tolerance"
    assert wallet_eval["consecutive_degraded_observations"] == 1


def test_wallet_counter_migrates_from_legacy_partial_registration(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    write_json(
        cfg.output_root / "ops_scheduler" / "degraded_state_watchdog_state.json",
        {
            "counters": {
                LEGACY_WALLET_REGISTRATION_ID: {
                    "last_observation_token": "2026-07-13T02:00:00Z",
                    "consecutive_degraded_observations": 1,
                    "episode_start_token": "2026-07-13T02:00:00Z",
                    "currently_degraded": True,
                }
            }
        },
    )
    write_json(
        cfg.output_root / "performance" / "wallet_reconciliation.json",
        {
            "generated_at_utc": "2026-07-14T02:00:00Z",
            "reconciliation_status": "error",
        },
    )

    result = build_degraded_state_watchdog(cfg, as_of="2026-07-14T02:00:01Z")

    wallet_eval = next(row for row in result["evaluations"] if row["registration_id"] == WALLET_REGISTRATION_ID)
    assert wallet_eval["migrated_legacy_counter"] is True
    assert wallet_eval["consecutive_degraded_observations"] == 2
    state = read_json(cfg.output_root / "ops_scheduler" / "degraded_state_watchdog_state.json")
    assert WALLET_REGISTRATION_ID in state["counters"]
    assert LEGACY_WALLET_REGISTRATION_ID not in state["counters"]


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
    operating_eval = next(row for row in regressed["evaluations"] if row["registration_id"] == "operating_state_unknown_regression")
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


def test_training_harvest_completion_older_than_25_hours_is_incident(
    tmp_path: Path,
) -> None:
    cfg = _cfg(tmp_path)
    write_json(
        cfg.output_root / "ops_scheduler" / "status.json",
        {
            "generated_at_utc": "2026-07-15T01:00:01Z",
            "jobs": {
                "training_harvest": {
                    "last_exit_code": 1,
                    "last_run_utc": "2026-07-15T00:30:00Z",
                    "last_success_utc": "2026-07-14T00:00:00Z",
                }
            },
        },
    )

    result = build_degraded_state_watchdog(cfg, as_of="2026-07-15T01:00:01Z")

    freshness = next(row for row in result["evaluations"] if row["registration_id"] == "scheduler_completion_freshness")
    harvest = next(row for row in freshness["jobs"] if row["job"] == "training_harvest")
    assert harvest["age_seconds"] == 90_001.0
    assert harvest["maximum_age_seconds"] == 90_000
    assert harvest["state"] == "stale"
    incident = next(
        row for row in result["active_incidents"] if row["registration_id"] == "scheduler_completion_freshness" and row["entity"] == "training_harvest"
    )
    assert incident["owner_notification_eligible"] is True
    assert result["status"] == "incident"


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


def test_wo121_fail_closed_producers_and_slo_are_incidents(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    write_json(cfg.output_root / "performance" / "ledger_anchor_verification.json", {"status": "broken", "generated_at_utc": "2026-07-29T00:00:00Z"})
    write_json(
        cfg.output_root / "performance" / "disaster_recovery_status.json",
        {"status": "ok", "remote_push_status": "ok", "rpo": {"compliant": False}, "generated_at_utc": "2026-07-29T00:00:00Z"},
    )
    write_json(cfg.output_root / "maker_carry" / "maker_carry_study.json", {"status": "failed", "generated_at_utc": "2026-07-29T00:00:00Z"})
    write_json(
        cfg.output_root / "performance" / "operating_state.json",
        {"generated_at_utc": "2026-07-29T00:00:00Z", "slo": {"rows": [{"id": "quote_sheet_age", "breach": True}]}},
    )
    result = build_degraded_state_watchdog(cfg, as_of="2026-07-29T00:00:01Z")
    ids = {row["registration_id"] for row in result["active_incidents"]}
    assert {"ledger_chain_integrity", "disaster_recovery_not_recoverable", "maker_study_run_failed", "operating_state_slo_breach"} <= ids


def test_wo121_job_record_missing_exit_code_reads_degraded_not_keyerror(tmp_path: Path) -> None:
    # A torn status.json write (the OPS-6 case this program was chartered on) can
    # drop the last_exit_code field entirely. That must read as a FAILED job, not
    # raise KeyError out of the watchdog - a crashed watchdog is the most
    # fail-open outcome there is.
    cfg = _cfg(tmp_path)
    write_json(
        cfg.output_root / "ops_scheduler" / "status.json",
        {
            "generated_at_utc": "2026-07-29T00:00:00Z",
            "jobs": {"governance_refresh": {"last_run_utc": "2026-07-29T00:00:00Z"}},
        },
    )
    result = build_degraded_state_watchdog(cfg, as_of="2026-07-29T00:00:01Z")
    scheduler_eval = next(
        row for row in result["evaluations"] if row["registration_id"] == "scheduler_nonzero_exit"
    )
    assert scheduler_eval["failing_jobs"], "an absent exit code must count as a failure"


def test_wo121_missing_operating_state_is_an_slo_incident_after_grace(tmp_path: Path) -> None:
    # No operating_state.json at all: every required SLO row is unproven. Before
    # the fix, an empty rows mapping short-circuited to healthy FOREVER -
    # silencing the SLO registration and the bridge bootstrap carve-out at the
    # same time. The corrected behaviour mirrors the publication bridges: a
    # fixed two-hour bootstrap grace, then every required id reports failing.
    cfg = _cfg(tmp_path)
    first = build_degraded_state_watchdog(cfg, as_of="2026-07-29T00:00:00Z")
    first_eval = next(
        row for row in first["evaluations"] if row["registration_id"] == "operating_state_slo_breach"
    )
    assert first_eval["state"] == "healthy", "bootstrap absence is healthy only inside the grace"

    late = build_degraded_state_watchdog(cfg, as_of="2026-07-29T02:00:01Z")
    slo_eval = next(
        row for row in late["evaluations"] if row["registration_id"] == "operating_state_slo_breach"
    )
    assert slo_eval["state"] == "incident"
    assert "quote_sheet_age" in slo_eval["failed_rows"]
    assert any(
        row["registration_id"] == "operating_state_slo_breach" for row in late["active_incidents"]
    )


def test_wo121_partial_slo_block_is_strict_with_no_grace(tmp_path: Path) -> None:
    # A PRESENT operating state whose SLO block lacks required rows is torn or
    # partial evidence, not a bootstrap - it fails immediately, no grace.
    cfg = _cfg(tmp_path)
    write_json(
        cfg.output_root / "performance" / "operating_state.json",
        {
            "generated_at_utc": "2026-07-29T00:00:00Z",
            "slo": {"rows": [{"id": "quote_sheet_age", "breach": False}]},
        },
    )
    result = build_degraded_state_watchdog(cfg, as_of="2026-07-29T00:00:01Z")
    slo_eval = next(
        row for row in result["evaluations"] if row["registration_id"] == "operating_state_slo_breach"
    )
    assert slo_eval["state"] == "incident"
    assert "quote_sheet_age" not in slo_eval["failed_rows"]
    assert "websocket_gap" in slo_eval["failed_rows"]


def test_wo121_missing_publication_status_trips_after_grace(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    write_json(cfg.output_root / "performance" / "operating_state.json", {"generated_at_utc": "2026-07-29T00:00:00Z", "slo": {"rows": []}})
    first = build_degraded_state_watchdog(cfg, as_of="2026-07-29T00:00:00Z")
    assert not any(row["registration_id"] == "publication_bridge_unhealthy" for row in first["active_incidents"])
    late = build_degraded_state_watchdog(cfg, as_of="2026-07-29T02:00:01Z")
    assert {row["entity"] for row in late["active_incidents"] if row["registration_id"] == "publication_bridge_unhealthy"} == {"anchor", "telemetry"}

def test_wo121fix_dr_not_due_is_healthy_not_an_incident(tmp_path: Path) -> None:
    # The exact payload the 2026-07-28 deploy log showed firing a
    # disaster_recovery_not_recoverable incident: archive simply not due yet,
    # remote push ok, RPO compliant. "not_due" is the DR producer's ROUTINE
    # healthy state between daily archive windows; the pre-fix allowlist
    # {"ok", "recoverable"} named a status the producer never writes and
    # missed the one it writes most.
    cfg = _cfg(tmp_path)
    write_json(
        cfg.output_root / "performance" / "disaster_recovery_status.json",
        {
            "status": "not_due",
            "remote_push_status": "ok",
            "rpo": {"compliant": True},
            "generated_at_utc": "2026-07-29T00:00:00Z",
        },
    )
    result = build_degraded_state_watchdog(cfg, as_of="2026-07-29T00:00:01Z")
    dr_eval = next(
        row for row in result["evaluations"]
        if row["registration_id"] == "disaster_recovery_not_recoverable"
    )
    assert dr_eval["state"] == "healthy"
    assert not any(
        row["registration_id"] == "disaster_recovery_not_recoverable"
        for row in result["active_incidents"]
    )


def test_wo121fix_dr_not_due_with_bad_rpo_or_push_still_incidents(tmp_path: Path) -> None:
    # Adding not_due to the allowlist must not blunt the real health clauses:
    # a not_due payload whose RPO is non-compliant or whose remote push failed
    # is still an incident.
    cfg = _cfg(tmp_path)
    path = cfg.output_root / "performance" / "disaster_recovery_status.json"
    write_json(
        path,
        {"status": "not_due", "remote_push_status": "ok", "rpo": {"compliant": False},
         "generated_at_utc": "2026-07-29T00:00:00Z"},
    )
    bad_rpo = build_degraded_state_watchdog(cfg, as_of="2026-07-29T00:00:01Z")
    assert any(
        row["registration_id"] == "disaster_recovery_not_recoverable"
        for row in bad_rpo["active_incidents"]
    )
    write_json(
        path,
        {"status": "not_due", "remote_push_status": "failed", "rpo": {"compliant": True},
         "generated_at_utc": "2026-07-29T00:10:00Z"},
    )
    bad_push = build_degraded_state_watchdog(cfg, as_of="2026-07-29T00:10:01Z")
    assert any(
        row["registration_id"] == "disaster_recovery_not_recoverable"
        for row in bad_push["active_incidents"]
    )


def test_wo121fix_dr_allowlist_names_only_producer_reachable_statuses() -> None:
    # Registration honesty: every allowlisted status must be one the DR
    # producer actually writes, or the registration documents a fiction. The
    # pre-fix list contained "recoverable", which no code path ever produces,
    # while the reachable healthy state "not_due" was missing - the recipe for
    # a permanently lit false incident. "skipped_locked" is reachable but
    # deliberately NOT allowlisted: a held DR lock must alarm.
    import re

    from polymarket_predictive_engine.degraded_state_watchdog import PRODUCER_REGISTRATIONS

    source = Path("src/polymarket_predictive_engine/disaster_recovery.py").read_text(encoding="utf-8")
    reachable = set(re.findall(r'payload\["status"\]\s*=\s*"(\w+)"', source))
    reachable |= set(re.findall(r'"status":\s*"(\w+)"', source))

    allowlist = next(
        healthy for registration_id, _, healthy in PRODUCER_REGISTRATIONS
        if registration_id == "disaster_recovery_not_recoverable"
    )
    assert allowlist == {"ok", "not_due"}
    assert allowlist <= reachable, f"allowlisted statuses not reachable: {allowlist - reachable}"
    assert "recoverable" not in reachable
    assert "skipped_locked" in reachable and "skipped_locked" not in allowlist


def test_wo129_failed_official_book_and_stale_dr_fail_closed(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    write_json(
        cfg.output_root / "maker_carry" / "official_book_snapshot.json",
        {"status": "failed", "generated_at_utc": "2026-07-29T00:00:00Z"},
    )
    write_json(
        cfg.output_root / "performance" / "disaster_recovery_status.json",
        {"status": "ok", "remote_push_status": "ok", "rpo": {"compliant": True},
         "generated_at_utc": "2026-07-28T00:00:00Z"},
    )
    result = build_degraded_state_watchdog(cfg, as_of="2026-07-29T00:00:00Z")
    dr = next(row for row in result["evaluations"] if row["registration_id"] == "disaster_recovery_not_recoverable")
    books = next(row for row in result["evaluations"] if row["registration_id"] == "official_book_snapshot_partial")
    assert dr["state"] == "incident"
    assert dr["status_artifact_age_seconds"] == 86400
    assert books["observed_state"] == "failed"
    assert books["state"] == "degraded_within_tolerance"


def test_wo129_missing_producer_artifacts_fail_closed(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    result = _build_degraded_state_watchdog(cfg, as_of="2026-07-29T00:00:00Z")
    by_id = {row["registration_id"]: row for row in result["evaluations"]}
    assert by_id["disaster_recovery_not_recoverable"]["state"] == "incident"
    assert by_id["official_book_snapshot_partial"]["state"] == "degraded_within_tolerance"


def test_wo129_failed_ntfy_delivery_is_retried_and_cleared(tmp_path: Path, monkeypatch) -> None:
    cfg = _cfg(tmp_path)
    monkeypatch.setenv("OPS_OWNER_NTFY_TOPIC_URL", "https://ntfy.invalid/topic")
    write_json(
        cfg.output_root / "maker_carry" / "maker_carry_study.json",
        {"status": "failed", "generated_at_utc": "2026-07-29T00:00:00Z"},
    )

    class Response:
        status_code = 503

    def post(*args, **kwargs):
        durable = read_json(cfg.output_root / "ops_scheduler" / "degraded_state_watchdog_state.json")
        assert durable["undelivered_incident_ids"]
        assert durable["undelivered_incident_registrations"]
        assert len(durable["undelivered_incident_ids"]) <= 20
        return Response()

    monkeypatch.setattr("polymarket_predictive_engine.degraded_state_watchdog.requests.post", post)
    first = build_degraded_state_watchdog(cfg, as_of="2026-07-29T00:00:01Z")
    assert first["notification"]["delivery"]["attempted"] is True
    assert first["notification"]["undelivered_incident_ids"]

    Response.status_code = 200
    second = build_degraded_state_watchdog(cfg, as_of="2026-07-29T00:01:01Z")
    assert second["new_incidents"] == []
    assert second["notification"]["delivery"]["delivered"] is True
    assert second["notification"]["undelivered_incident_ids"] == []


def test_wo129_lock_held_carries_incidents_then_alarms_and_recovers(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    write_json(
        cfg.output_root / "maker_carry" / "maker_carry_study.json",
        {"status": "failed", "generated_at_utc": "2026-07-29T00:00:00Z"},
    )
    observed = build_degraded_state_watchdog(cfg, as_of="2026-07-29T00:00:01Z")
    original_ids = {row["incident_id"] for row in observed["active_incidents"]}
    lock = acquire_runtime_lock(cfg, "degraded_state_watchdog", stale_after_seconds=999999)
    try:
        carried = [
            build_degraded_state_watchdog(cfg, as_of=f"2026-07-29T00:0{cycle}:01Z")
            for cycle in range(1, 5)
        ]
    finally:
        release_runtime_lock(lock)

    assert original_ids <= {row["incident_id"] for row in carried[0]["active_incidents"]}
    assert carried[0]["evaluations"] == observed["evaluations"]
    assert carried[-1]["carry_forward_cycles"] == 4
    assert any(row["registration_id"] == "degraded_state_watchdog_wedged" for row in carried[-1]["active_incidents"])

    recovered = build_degraded_state_watchdog(cfg, as_of="2026-07-29T00:05:01Z")
    state = read_json(cfg.output_root / "ops_scheduler" / "degraded_state_watchdog_state.json")
    assert recovered["status"] == "incident"
    assert state["carry_forward_cycles"] == 0
    assert state["carry_forward_started_at"] is None
