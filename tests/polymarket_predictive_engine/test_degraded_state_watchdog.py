"""WO-78 persistent degraded-state watchdog tests."""

from __future__ import annotations

import json
from pathlib import Path

from polymarket_predictive_engine.cli import COMMANDS
from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.degraded_state_watchdog import (
    INCIDENT_LEDGER,
    LEGACY_WALLET_REGISTRATION_ID,
    REGISTERED_MAXIMA,
    REGISTERED_PUSH_LANES,
    WALLET_HEALTHY_STATES,
    WALLET_REGISTRATION_ID,
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


def _wallet_incidents(result: dict) -> list[dict]:
    return [
        row
        for row in result["active_incidents"]
        if row["registration_id"] == WALLET_REGISTRATION_ID
    ]


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

    assert settings["requote_missing_input_max_consecutive_cycles"] == REGISTERED_MAXIMA[
        "requote_missing_input_max_consecutive_cycles"
    ]
    assert settings["wallet_not_clean_max_consecutive_harvests"] == 1
    assert settings["maker_replay_insufficient_coverage_max_consecutive_cycles"] == REGISTERED_MAXIMA[
        "maker_replay_insufficient_coverage_max_consecutive_cycles"
    ]

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
    evaluation = next(
        row
        for row in result["evaluations"]
        if row["registration_id"] == "kill_input_stale_live_stage"
    )
    incident = next(
        row
        for row in result["active_incidents"]
        if row["registration_id"] == "kill_input_stale_live_stage"
    )

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
            # Scoped to the registration under test: this fixture stages only a
            # wallet artifact, so unrelated registrations (the WO-121 publication
            # bridges, absent here) legitimately have their own opinion.
            assert not _wallet_incidents(result)
            polled = build_degraded_state_watchdog(cfg, as_of=f"2026-07-{10 + harvest}T02:04:01Z")
            wallet_eval = next(
                row for row in polled["evaluations"] if row["registration_id"] == WALLET_REGISTRATION_ID
            )
            assert wallet_eval["consecutive_degraded_observations"] == harvest

    assert result["status"] == "incident"
    assert len(_wallet_incidents(result)) == 1
    wallet_eval = next(row for row in result["evaluations"] if row["registration_id"] == WALLET_REGISTRATION_ID)
    assert wallet_eval["consecutive_degraded_observations"] == 3


def test_wallet_discrepancy_and_unknown_status_are_outside_healthy_allowlist(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    wallet_path = cfg.output_root / "performance" / "wallet_reconciliation.json"
    for harvest in range(1, 4):
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
    assert _wallet_incidents(result)[0]["reason"] == "synthetic normalized NAV mismatch"

    write_json(
        wallet_path,
        {
            "generated_at_utc": "2026-07-14T03:05:00Z",
            "reconciliation_status": "clean",
        },
    )
    assert not _wallet_incidents(build_degraded_state_watchdog(cfg, as_of="2026-07-14T03:05:01Z"))

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

    freshness = next(
        row
        for row in result["evaluations"]
        if row["registration_id"] == "scheduler_completion_freshness"
    )
    harvest = next(row for row in freshness["jobs"] if row["job"] == "training_harvest")
    assert harvest["age_seconds"] == 90_001.0
    assert harvest["maximum_age_seconds"] == 90_000
    assert harvest["state"] == "stale"
    incident = next(
        row
        for row in result["active_incidents"]
        if row["registration_id"] == "scheduler_completion_freshness"
        and row["entity"] == "training_harvest"
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


# --- WO-121 (2026-07-27): coverage for producers that published failure to nobody ---


def _evaluation(result: dict, registration_id: str) -> dict:
    return next(
        row for row in result["evaluations"] if row["registration_id"] == registration_id
    )


def _incidents_for(result: dict, registration_id: str) -> list[dict]:
    return [
        row
        for row in result["active_incidents"]
        if row["registration_id"] == registration_id
    ]


def test_wo121_registered_freshness_covers_the_anchor_and_safety_lanes() -> None:
    # 2026-07: the anchor lane stopped producing for nine days and the 15-minute
    # safety lane owns five decision artifacts. Neither had a freshness ceiling.
    from polymarket_predictive_engine.degraded_state_watchdog import (
        REGISTERED_JOB_FRESHNESS_MAX_SECONDS as ceilings,
    )

    assert ceilings["ledger_anchor"] == 26 * 60 * 60
    assert ceilings["maker_safety_refresh"] == 60 * 60
    scheduler = Path("scripts/run_vps_ops_scheduler.sh").read_text(encoding="utf-8")
    for job in ceilings:
        # Every ceiling must name a lane the scheduler actually stamps, or the
        # registration is measuring nothing.
        assert f"stamp_status {job} " in scheduler or f"stamp_status {job}\n" in scheduler, job


def test_wo121_broken_ledger_chain_opens_an_immediate_incident(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    verification = cfg.output_root / "performance" / "ledger_anchor_verification.json"
    summary = cfg.output_root / "performance" / "ledger_anchor_summary.json"
    write_json(
        verification,
        {
            "generated_at_utc": "2026-07-16T00:00:00Z",
            "status": "broken",
            "first_broken_date": "2026-07-12",
            "issues": ["maker_carry/maker_carry_history.csv: anchored prefix digest changed"],
        },
    )
    write_json(
        summary,
        {"generated_at_utc": "2026-07-16T00:00:00Z", "status": "blocked_broken_chain"},
    )

    broken = build_degraded_state_watchdog(cfg, as_of="2026-07-16T00:05:00Z")

    assert broken["status"] == "incident"
    evaluation = _evaluation(broken, "ledger_chain_integrity")
    assert evaluation["state"] == "incident"
    assert evaluation["first_broken_date"] == "2026-07-12"
    incident = _incidents_for(broken, "ledger_chain_integrity")[0]
    assert "first_broken_date=2026-07-12" in incident["reason"]
    assert "blocked_broken_chain" in incident["reason"]

    # A repaired chain clears without needing a counter to decay.
    write_json(verification, {"generated_at_utc": "2026-07-17T00:00:00Z", "status": "ok"})
    write_json(summary, {"generated_at_utc": "2026-07-17T00:00:00Z", "status": "ok"})
    repaired = build_degraded_state_watchdog(cfg, as_of="2026-07-17T00:05:00Z")
    assert _evaluation(repaired, "ledger_chain_integrity")["state"] == "healthy"
    assert _incidents_for(repaired, "ledger_chain_integrity") == []


def test_wo121_unverified_chain_is_not_a_healthy_chain(tmp_path: Path) -> None:
    # Fail-closed: an active anchor lane with no verification artifact is an
    # UNVERIFIED chain. "No evidence of tampering" requires evidence.
    cfg = _cfg(tmp_path)
    write_json(
        cfg.output_root / "performance" / "ledger_anchor_summary.json",
        {"generated_at_utc": "2026-07-20T00:00:00Z", "status": "ok"},
    )

    result = build_degraded_state_watchdog(cfg, as_of="2026-07-20T00:05:00Z")

    assert _evaluation(result, "ledger_chain_integrity")["state"] == "incident"
    assert "verification artifact is missing" in _incidents_for(result, "ledger_chain_integrity")[0][
        "reason"
    ]


def test_wo121_disaster_recovery_failure_and_rpo_breach_are_observed(tmp_path: Path) -> None:
    # 2026-07-16..26: the archive builder failed for ten consecutive days and
    # published that failure to a file nothing opened.
    cfg = _cfg(tmp_path)
    path = cfg.output_root / "performance" / "disaster_recovery_status.json"
    write_json(
        path,
        {
            "generated_at_utc": "2026-07-20T00:00:00Z",
            "status": "error",
            "error": "ValueError: archive expands above the configured size cap",
            "remote_push_status": "error",
        },
    )

    failing = build_degraded_state_watchdog(cfg, as_of="2026-07-20T00:05:00Z")
    assert failing["status"] == "incident"
    assert "size cap" in _incidents_for(failing, "disaster_recovery_not_recoverable")[0]["reason"]

    # A build that succeeds but leaves the archive older than the active RPO is
    # still not recoverable - the honest predicate uses the OBSERVED age.
    write_json(
        path,
        {
            "generated_at_utc": "2026-07-21T00:00:00Z",
            "status": "ok",
            "remote_push_status": "ok",
            "last_remote_archive_age_hours": 233.5,
            "rpo": {"active_rpo_hours": 24, "compliant": False},
        },
    )
    stale = build_degraded_state_watchdog(cfg, as_of="2026-07-21T00:05:00Z")
    reason = _incidents_for(stale, "disaster_recovery_not_recoverable")[0]["reason"]
    assert "233.5h is outside the active RPO of 24h" in reason

    write_json(
        path,
        {
            "generated_at_utc": "2026-07-22T00:00:00Z",
            "status": "ok",
            "remote_push_status": "ok",
            "last_remote_archive_age_hours": 1.0,
            "rpo": {"active_rpo_hours": 24, "compliant": True},
        },
    )
    healthy = build_degraded_state_watchdog(cfg, as_of="2026-07-22T00:05:00Z")
    assert _evaluation(healthy, "disaster_recovery_not_recoverable")["state"] == "healthy"


def test_wo121_failed_maker_study_run_is_an_incident(tmp_path: Path) -> None:
    # Owner decision 2026-07-26: a failed run still commits its history row
    # (fail-closed), so it must be loud - it silently banks a failed UTC day.
    cfg = _cfg(tmp_path)
    path = cfg.output_root / "maker_carry" / "maker_carry_study.json"
    write_json(
        path,
        {
            "generated_at_utc": "2026-07-22T09:00:00Z",
            "status": "failed",
            "errors": ["books fetch failed for 0xabc"],
        },
    )

    failed = build_degraded_state_watchdog(cfg, as_of="2026-07-22T09:05:00Z")
    assert failed["status"] == "incident"
    reason = _incidents_for(failed, "maker_study_run_failed")[0]["reason"]
    assert "still committed its history row" in reason

    write_json(
        path,
        {"generated_at_utc": "2026-07-23T09:00:00Z", "status": "no_candidates"},
    )
    benign = build_degraded_state_watchdog(cfg, as_of="2026-07-23T09:05:00Z")
    assert _evaluation(benign, "maker_study_run_failed")["state"] == "healthy"
    assert _incidents_for(benign, "maker_study_run_failed") == []


def test_wo121_persistent_partial_book_collection_trips_after_its_maximum(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    path = cfg.output_root / "maker_carry" / "official_book_snapshot.json"
    maximum = REGISTERED_MAXIMA["official_book_partial_max_consecutive_cycles"]
    states = []
    for cycle in range(1, maximum + 2):
        write_json(
            path,
            {
                "generated_at_utc": f"2026-07-24T0{cycle}:00:00Z",
                "status": "partial",
                "markets_polled": 3,
            },
        )
        result = build_degraded_state_watchdog(cfg, as_of=f"2026-07-24T0{cycle}:05:00Z")
        states.append(_evaluation(result, "official_book_snapshot_partial")["state"])

    assert states[:-1] == ["degraded_within_tolerance"] * maximum
    assert states[-1] == "incident"


def test_wo121_slo_breach_and_unmeasurable_rows_become_incidents(tmp_path: Path) -> None:
    # OPS-3: the operating-state SLO block had NO consumer. Seven rows could sit
    # in BREACH indefinitely with no incident, no exit code, no notification.
    cfg = _cfg(tmp_path)
    path = cfg.output_root / "performance" / "operating_state.json"

    def _state(cycle: int) -> dict:
        return {
            "generated_at_utc": f"2026-07-25T0{cycle}:00:00Z",
            "slo": {
                "status": "breach",
                "rows": [
                    {
                        "id": "ledger_anchor_age",
                        "metric": "Ledger-anchor age",
                        "target": 129600,
                        "measured": 800000,
                        "unit": "seconds",
                        "breach": True,
                        "state": "BREACH",
                        "source": "ledger_anchor_head.json",
                    },
                    {
                        "id": "websocket_gap",
                        "metric": "Websocket observation gap",
                        "target": 300,
                        "measured": None,
                        "unit": "seconds",
                        "breach": None,
                        "state": "UNKNOWN",
                        "source": "websocket_messages_latest.json",
                    },
                    {
                        "id": "dashboard_staleness",
                        "metric": "Dashboard staleness",
                        "target": 300,
                        "measured": 12.0,
                        "unit": "seconds",
                        "breach": False,
                        "state": "OK",
                        "source": "dashboard_data.json",
                    },
                ],
            },
        }

    breach_max = REGISTERED_MAXIMA["slo_breach_max_consecutive_cycles"]
    unknown_max = REGISTERED_MAXIMA["slo_unknown_max_consecutive_cycles"]
    assert breach_max < unknown_max  # a measured breach alarms sooner than a blind one

    entities: list[list[str]] = []
    for cycle in range(1, unknown_max + 2):
        write_json(path, _state(cycle))
        result = build_degraded_state_watchdog(cfg, as_of=f"2026-07-25T0{cycle}:05:00Z")
        entities.append(sorted(row["entity"] for row in _incidents_for(result, "operating_state_slo_breach")))

    # The measured breach fires first; the unmeasurable row gets its longer grace.
    assert entities[0] == []
    assert entities[breach_max] == ["ledger_anchor_age"]
    # A passing row is never an incident.
    assert all("dashboard_staleness" not in row for row in entities)
    evaluation = _evaluation(result, "operating_state_slo_breach")
    assert evaluation["breaching_rows"] == ["ledger_anchor_age"]
    # WO-126 (Codex #363 P1): the registered row set is evaluated, not just what
    # the artifact published, so this fixture's four omitted rows are unmeasurable
    # observations rather than rows that silently vanish - and after the UNKNOWN
    # grace they alarm alongside the row the fixture did publish as UNKNOWN.
    from polymarket_predictive_engine.operating_state import REGISTERED_SLO_ROW_IDS

    assert evaluation["registered_rows"] == list(REGISTERED_SLO_ROW_IDS)
    assert "websocket_gap" in evaluation["unmeasurable_rows"]
    assert set(evaluation["rows_absent_from_artifact"]) == set(REGISTERED_SLO_ROW_IDS) - {
        "ledger_anchor_age",
        "websocket_gap",
        "dashboard_staleness",
    }
    assert "websocket_gap" in entities[unknown_max]
    assert "ledger_anchor_age" in entities[unknown_max]


def test_wo126_omitted_slo_row_cannot_silence_a_live_breach(tmp_path: Path) -> None:
    # Codex #363 P1: reading only the rows the producer published meant an omitted
    # row was never evaluated AND its counter was dropped from state, so a partial
    # artifact cleared an active breach instead of counting the lost measurement.
    cfg = _cfg(tmp_path)
    path = cfg.output_root / "performance" / "operating_state.json"
    breach_row = {
        "id": "ledger_anchor_age",
        "metric": "Ledger-anchor age",
        "target": 129600,
        "measured": 800000,
        "unit": "seconds",
        "breach": True,
        "state": "BREACH",
        "source": "ledger_anchor_head.json",
    }
    maximum = REGISTERED_MAXIMA["slo_breach_max_consecutive_cycles"]

    for cycle in range(1, maximum + 2):
        write_json(
            path,
            {"generated_at_utc": f"2026-07-28T0{cycle}:00:00Z", "slo": {"rows": [breach_row]}},
        )
        result = build_degraded_state_watchdog(cfg, as_of=f"2026-07-28T0{cycle}:05:00Z")
    assert [row["entity"] for row in _incidents_for(result, "operating_state_slo_breach")] == [
        "ledger_anchor_age"
    ]

    # The producer now omits the breaching row entirely. That must NOT read as
    # resolved: the measurement is missing, which is an unknown observation.
    write_json(path, {"generated_at_utc": "2026-07-28T09:00:00Z", "slo": {"rows": []}})
    partial = build_degraded_state_watchdog(cfg, as_of="2026-07-28T09:05:00Z")

    evaluation = _evaluation(partial, "operating_state_slo_breach")
    row = next(item for item in evaluation["rows"] if item["id"] == "ledger_anchor_age")
    assert row["kind"] == "unknown"
    assert "ledger_anchor_age" in evaluation["rows_absent_from_artifact"]


def test_wo121_missing_job_exit_code_fails_closed(tmp_path: Path) -> None:
    # A truncated job record used to default to last_exit_code=0 and read as a
    # clean success.
    cfg = _cfg(tmp_path)
    write_json(
        cfg.output_root / "ops_scheduler" / "status.json",
        {
            "generated_at_utc": "2026-07-26T00:00:00Z",
            "jobs": {"trade_prints": {"last_run_utc": "2026-07-26T00:00:00Z"}},
        },
    )

    result = build_degraded_state_watchdog(cfg, as_of="2026-07-26T00:01:00Z")

    failing = _evaluation(result, "scheduler_nonzero_exit")["failing_jobs"]
    assert [row["job"] for row in failing] == ["trade_prints"]
    assert failing[0]["last_exit_code"] == 1


def test_wo121_lock_held_cycle_never_publishes_empty_incidents(tmp_path: Path) -> None:
    # OPS-4: the lock-held path overwrote the artifact with empty incident lists
    # at exit 0, so a concurrent run published "all clear" over a live incident.
    from polymarket_predictive_engine.runtime_lock import runtime_lock

    cfg = _cfg(tmp_path)
    write_json(
        cfg.output_root / "maker_carry" / "maker_carry_study.json",
        {"generated_at_utc": "2026-07-26T09:00:00Z", "status": "failed"},
    )
    first = build_degraded_state_watchdog(cfg, as_of="2026-07-26T09:05:00Z")
    assert first["active_incident_count"] == 1

    with runtime_lock(cfg, "degraded_state_watchdog", stale_after_seconds=900) as held:
        assert held.acquired
        skipped = build_degraded_state_watchdog(cfg, as_of="2026-07-26T09:20:00Z")

    assert skipped["status"] == "skipped_lock_held"
    assert skipped["active_incident_count"] == 1
    assert skipped["active_incidents"][0]["registration_id"] == "maker_study_run_failed"
    assert skipped["carried_forward_from_utc"] == first["generated_at_utc"]
    persisted = read_json(cfg.output_root / "ops_scheduler" / "degraded_state_watchdog.json")
    assert persisted["active_incidents"] == skipped["active_incidents"]


def test_wo121_incident_push_is_state_change_gated_and_carries_no_payload(
    tmp_path: Path, monkeypatch
) -> None:
    # Owner-approved delivery channel. The push must fire on the state CHANGE
    # only, and must carry registration ids only - no market, wallet, amount, or
    # artifact contents leave the host.
    from polymarket_predictive_engine import degraded_state_watchdog as module

    sent: list[tuple[str, bytes]] = []

    class _Response:
        status_code = 200

    def _fake_post(url, data=None, timeout=None):
        sent.append((url, data))
        return _Response()

    monkeypatch.setattr(module.requests, "post", _fake_post)
    monkeypatch.setenv(module.NTFY_ENV_VAR, "https://ntfy.example/polymarket-owner")

    cfg = _cfg(tmp_path)
    write_json(
        cfg.output_root / "maker_carry" / "maker_carry_study.json",
        {
            "generated_at_utc": "2026-07-26T10:00:00Z",
            "status": "failed",
            "errors": ["books fetch failed for 0xSECRETMARKET"],
        },
    )

    opened = build_degraded_state_watchdog(cfg, as_of="2026-07-26T10:05:00Z")
    assert opened["notification"]["push"] == {
        "attempted": True,
        "delivered": True,
        "status_code": 200,
        "channel_configured": True,
    }
    assert len(sent) == 1
    body = sent[0][1].decode("utf-8")
    assert "maker_study_run_failed" in body
    assert "0xSECRETMARKET" not in body

    # Same incident on the next cycle: no new id, so no second push.
    repeat = build_degraded_state_watchdog(cfg, as_of="2026-07-26T10:20:00Z")
    assert repeat["new_incident_count"] == 0
    assert repeat["notification"]["push"]["attempted"] is False
    assert len(sent) == 1


def test_wo121_push_channel_absent_is_reported_not_faked(tmp_path: Path, monkeypatch) -> None:
    from polymarket_predictive_engine import degraded_state_watchdog as module

    monkeypatch.delenv(module.NTFY_ENV_VAR, raising=False)
    cfg = _cfg(tmp_path)
    write_json(
        cfg.output_root / "maker_carry" / "maker_carry_study.json",
        {"generated_at_utc": "2026-07-26T11:00:00Z", "status": "failed"},
    )

    result = build_degraded_state_watchdog(cfg, as_of="2026-07-26T11:05:00Z")

    assert result["notification"]["notify"] is True
    assert result["notification"]["push"] == {"attempted": False, "channel_configured": False}


def test_wo121_publication_bridge_staleness_is_measured(tmp_path: Path) -> None:
    # OPS-1/2: neither push could fail observably and nothing measured either
    # one's age, so a >22h telemetry blackout presented as silence.
    from polymarket_predictive_engine.degraded_state_watchdog import REGISTERED_PUSH_LANES

    cfg = _cfg(tmp_path)
    telemetry = cfg.output_root / REGISTERED_PUSH_LANES["telemetry_push"]["artifact"]

    # WO-126 (Codex #363 P1): an absent artifact gets a bounded grace period and
    # then alarms. Both bridges run from host cron with no scheduler lane behind
    # them, so nothing else would ever notice that they were never installed.
    absent = build_degraded_state_watchdog(cfg, as_of="2026-07-26T12:00:00Z")
    assert _evaluation(absent, "publication_bridge_stale")["state"] == "unobserved"
    assert _incidents_for(absent, "publication_bridge_stale") == []

    grace = REGISTERED_PUSH_LANES["telemetry_push"]["absent_grace_seconds"]
    assert grace > 0
    beyond_grace = build_degraded_state_watchdog(cfg, as_of="2026-07-26T15:30:00Z")
    never_installed = _incidents_for(beyond_grace, "publication_bridge_stale")
    assert "telemetry_push" in [row["entity"] for row in never_installed]
    assert "no status artifact" in never_installed[0]["reason"]

    write_json(
        telemetry,
        {
            "generated_at_utc": "2026-07-26T12:00:00Z",
            "status": "ok",
            "last_success_at_utc": "2026-07-26T11:40:00Z",
        },
    )
    # Only the anchor lane is still absent now, and a healthy rollup requires
    # EVERY lane: one working bridge must not mask another that never published.
    fresh = build_degraded_state_watchdog(cfg, as_of="2026-07-26T12:00:00Z")
    telemetry_lane = next(
        row
        for row in _evaluation(fresh, "publication_bridge_stale")["lanes"]
        if row["lane"] == "telemetry_push"
    )
    assert telemetry_lane["state"] == "healthy"

    # 22 hours without a successful push is the exact production blackout.
    write_json(
        telemetry,
        {
            "generated_at_utc": "2026-07-26T12:00:00Z",
            "status": "error",
            "detail": "WO-73 credential guard failed; telemetry and archive push refused",
            "last_success_at_utc": "2026-07-25T12:30:00Z",
        },
    )
    dark = build_degraded_state_watchdog(cfg, as_of="2026-07-26T12:00:00Z")
    incident = _incidents_for(dark, "publication_bridge_stale")[0]
    assert incident["entity"] == "telemetry_push"
    assert "credential guard failed" in incident["reason"]
    assert "above its 7200s ceiling" in incident["reason"]

    # A bridge that exists but never succeeded is not healthy either.
    write_json(telemetry, {"generated_at_utc": "2026-07-26T12:00:00Z", "status": "ok"})
    never = build_degraded_state_watchdog(cfg, as_of="2026-07-26T12:00:00Z")
    assert "no recorded successful push" in _incidents_for(never, "publication_bridge_stale")[0]["reason"]


def test_wo121_push_scripts_stamp_every_outcome() -> None:
    telemetry = Path("scripts/push_vps_telemetry.sh").read_text(encoding="utf-8")
    anchor = Path("scripts/push_vps_anchor.sh").read_text(encoding="utf-8")

    # Both bridges must record success AND failure, and a real failure must exit
    # nonzero rather than returning 0 from every path.
    assert "telemetry_push_status.json" in telemetry
    assert 'stamp_push "ok"' in telemetry and 'stamp_push "error"' in telemetry
    assert "anchor_push_status.json" in anchor
    assert 'stamp_push "ok"' in anchor and 'stamp_push "error"' in anchor
    assert 'stamp_push "already_present"' in anchor
    # The pending flag is cleared by a durable success, which nothing did before.
    assert '"external_anchor_pending"] = False' in anchor
    for script in (telemetry, anchor):
        assert "last_success_at_utc" in script


def test_wo126_maker_study_artifact_without_a_status_is_not_healthy(tmp_path: Path) -> None:
    # Codex #363 P2: an artifact that is present and timestamped but carries NO
    # status used to skip the allowlist entirely (the predicate required a
    # non-empty status before evaluating), so a malformed producer artifact read as
    # healthy and cleared any previous failure episode.
    cfg = _cfg(tmp_path)
    path = cfg.output_root / "maker_carry" / "maker_carry_study.json"

    write_json(path, {"generated_at_utc": "2026-07-29T09:00:00Z", "status": "failed"})
    failed = build_degraded_state_watchdog(cfg, as_of="2026-07-29T09:05:00Z")
    assert _incidents_for(failed, "maker_study_run_failed")

    # Status disappears while the artifact stays fresh: still not healthy.
    write_json(path, {"generated_at_utc": "2026-07-29T10:00:00Z", "portfolio": []})
    malformed = build_degraded_state_watchdog(cfg, as_of="2026-07-29T10:05:00Z")
    evaluation = _evaluation(malformed, "maker_study_run_failed")
    assert evaluation["state"] == "incident"
    assert evaluation["observed_state"] is None
    assert _incidents_for(malformed, "maker_study_run_failed")

    # An explicitly benign status clears it.
    write_json(path, {"generated_at_utc": "2026-07-29T11:00:00Z", "status": "no_candidates"})
    healthy = build_degraded_state_watchdog(cfg, as_of="2026-07-29T11:05:00Z")
    assert _evaluation(healthy, "maker_study_run_failed")["state"] == "healthy"


def test_wo126_unparseable_observation_stamp_never_fabricates_an_age(tmp_path: Path) -> None:
    # Clock hygiene: substituting wall-clock time for an unparseable observation
    # stamp mixes two clocks and invented a 14-day "absence" against a same-minute
    # observation. If the observation cannot be placed in time, say so.
    cfg = _cfg(tmp_path)

    result = build_degraded_state_watchdog(cfg, as_of="2026-07-13T00:7:30Z")

    evaluation = _evaluation(result, "publication_bridge_stale")
    assert evaluation["state"] == "age_unmeasurable"
    assert evaluation["lanes"] == []
    assert _incidents_for(result, "publication_bridge_stale") == []
