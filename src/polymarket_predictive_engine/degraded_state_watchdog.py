"""WO-78 semantic-health watchdog for persistent degraded runtime states.

The watchdog is deliberately reporting-only.  It observes already-generated
artifacts, counts distinct producer observations, appends incident evidence,
and emits the existing owner-notification artifact contract.  It never
changes a gate, quote state, broker, sizing rule, or order path.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import requests

from .config import EngineConfig, load_config
from .runtime_lock import runtime_lock
from .utils import ensure_dir, now_utc, read_csv_rows, read_json, serialize_value, write_json


WORK_ORDER = "WO-78+WO-83+WO-84+WO-85+WO-86+WO-121"
OUTPUT_FILE = "ops_scheduler/degraded_state_watchdog.json"
STATE_FILE = "ops_scheduler/degraded_state_watchdog_state.json"
INCIDENT_LEDGER = "performance/degraded_state_incidents.csv"
NOTIFICATION_BODY = "ops_scheduler/degraded_state_notification.md"
WALLET_REGISTRATION_ID = "wallet_reconciliation_not_clean"
LEGACY_WALLET_REGISTRATION_ID = "wallet_reconciliation_partial"
WALLET_HEALTHY_STATES = frozenset({"clean", "explained"})
# WO-121: the same environment-only push channel WO-99 registered. Absent
# variable means no delivery, never a crash and never a fabricated success.
NTFY_ENV_VAR = "OPS_OWNER_NTFY_TOPIC_URL"

# A maximum is the number of consecutive degraded observations tolerated.
# Therefore max=3 trips on observation four, exactly matching "> 3 cycles".
REGISTERED_MAXIMA: dict[str, int] = {
    "requote_missing_input_max_consecutive_cycles": 3,
    "scheduler_nonzero_max_consecutive_cycles": 0,
    "wallet_not_clean_max_consecutive_harvests": 2,
    "operating_unknown_max_consecutive_cycles": 0,
    "maker_replay_insufficient_coverage_max_consecutive_cycles": 3,
    # WO-121 (2026-07-27): the operating-state SLO block had NO consumer - all
    # seven rows were dashboard-only, so a breach produced no incident, no exit
    # code, and no notification. A measured breach alarms on the second
    # consecutive observation (~30 minutes at the 15-minute watchdog cadence),
    # which survives a deploy window without swallowing a real breach.
    "slo_breach_max_consecutive_cycles": 1,
    # An SLO that cannot be measured is a blind SLO, not a healthy one. It gets
    # a longer grace period than a measured breach purely so a booting or
    # freshly restored host is not an incident storm.
    "slo_unknown_max_consecutive_cycles": 3,
    "official_book_partial_max_consecutive_cycles": 3,
}

# WO-85 (registered 2026-07-15): every recurring scheduler lane has a
# completion-freshness ceiling. These are fixed maxima; there is no config
# path that can widen them. The daily harvest's exact SLO is 25 hours.
REGISTERED_JOB_FRESHNESS_MAX_SECONDS: dict[str, int] = {
    "governance_refresh": 7 * 60 * 60,
    "clv_snapshot": 9 * 60 * 60,
    "locked_card_refresh": 13 * 60 * 60,
    "training_harvest": 25 * 60 * 60,
    "maker_study_intraday": 25 * 60 * 60,
    "trade_prints": 20 * 60,
    "executor_ops_monitor": 15 * 60,
    "degraded_state_watchdog": 15 * 60,
    # WO-121 (2026-07-27): these two lanes were the gap. `ledger_anchor` runs
    # every 12h and owns the tamper chain - it stopped producing for nine days
    # in 2026-07 with nothing measuring it. `maker_safety_refresh` runs every
    # 15 minutes and owns decision_policy / requote / kill artifacts; if it
    # stops being scheduled, five safety artifacts freeze at their last value
    # and every downstream freshness check reads the producer's own stale
    # self-report as current.
    "ledger_anchor": 26 * 60 * 60,
    "maker_safety_refresh": 60 * 60,
}

# WO-121: producers whose failure state was published but never observed.
CHAIN_VERIFICATION_ARTIFACT = "performance/ledger_anchor_verification.json"
CHAIN_SUMMARY_ARTIFACT = "performance/ledger_anchor_summary.json"
DISASTER_RECOVERY_ARTIFACT = "performance/disaster_recovery_status.json"
MAKER_STUDY_ARTIFACT = "maker_carry/maker_carry_study.json"
OFFICIAL_BOOK_ARTIFACT = "maker_carry/official_book_snapshot.json"
OPERATING_STATE_ARTIFACT = "performance/operating_state.json"

# A study run that fails still commits its history row (registered fail-closed
# behaviour, owner decision 2026-07-26), so a silent failure erases that day
# from the M-A streak. These are the only statuses that are not a failure.
MAKER_STUDY_HEALTHY_STATES = frozenset({"ok", "no_candidates", "disabled"})
DISASTER_RECOVERY_HEALTHY_STATES = frozenset({"ok", "not_due", "disabled", "skipped_locked"})

# WO-121 (OPS-1/2): the two publication bridges now stamp their own outcome, so
# their age is measurable. Ceilings are fixed; configuration cannot widen them.
# Telemetry cron is every 30 minutes and the anchor lane is twice daily.
REGISTERED_PUSH_LANES: dict[str, dict[str, Any]] = {
    "telemetry_push": {
        "artifact": "ops_scheduler/telemetry_push_status.json",
        "maximum_age_seconds": 2 * 60 * 60,
        "healthy_states": ("ok", "skipped_locked"),
    },
    "external_anchor_push": {
        "artifact": "performance/anchor_push_status.json",
        "maximum_age_seconds": 26 * 60 * 60,
        "healthy_states": ("ok", "already_present", "skipped_locked", "skipped_no_head"),
    },
}

MISSING_INPUT_RULES = frozenset(
    {
        "incomplete_order_ticket",
        "missing_live_bid_ask",
        "stale_live_bid_ask",
    }
)

INCIDENT_FIELDS = [
    "incident_id",
    "detected_at_utc",
    "registration_id",
    "entity",
    "source_artifact",
    "observation_token",
    "degraded_state",
    "reason",
    "consecutive_degraded_observations",
    "max_consecutive_degraded_observations",
    "event_type",
    "owner_notification_eligible",
    "paper_trading_invoked",
    "live_trading_invoked",
]


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _boolish(value: Any, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _nonnegative_int(value: Any, default: int) -> int:
    try:
        candidate = int(value)
    except (TypeError, ValueError):
        return default
    return candidate if candidate >= 0 else default


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = _mapping(cfg.raw.get("degraded_state_watchdog"))
    settings: dict[str, Any] = {
        "enabled": _boolish(raw.get("enabled"), True),
        "notification_enabled": _boolish(raw.get("notification_enabled"), True),
        "lock_stale_seconds": max(60, _nonnegative_int(raw.get("lock_stale_seconds"), 900)),
    }
    for key, registered in REGISTERED_MAXIMA.items():
        # Configuration can alarm sooner, never later.
        configured = raw.get(key)
        if key == "wallet_not_clean_max_consecutive_harvests" and configured is None:
            configured = raw.get("wallet_partial_max_consecutive_harvests")
        settings[key] = min(registered, _nonnegative_int(configured, registered))
    return settings


def _registrations(settings: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": "requote_missing_inputs",
            "artifact": "maker_carry/requote_alerts.json",
            "healthy_reachable_states": [
                "quotes_ok",
                "requote_advised",
                "pull_quotes_now_or_STOP_with_risk_reason_only",
            ],
            "degraded_condition": "pull_quotes_now/STOP with a registered missing-input rule",
            "max_consecutive_degraded_observations": settings[
                "requote_missing_input_max_consecutive_cycles"
            ],
            "incident_on_observation": settings["requote_missing_input_max_consecutive_cycles"] + 1,
            "observation_unit": "producer cycle",
            "evaluation_policy": "registered missing-input predicate with legitimate risk-state exemption",
        },
        {
            "id": "maker_replay_insufficient_coverage",
            "artifact": "maker_carry/maker_fill_replay.json",
            "healthy_reachable_states": ["covered", "partial", "no_simulated_fill_opportunities"],
            "degraded_condition": "nonzero simulated fill opportunities with zero 5m replay coverage",
            "max_consecutive_degraded_observations": settings[
                "maker_replay_insufficient_coverage_max_consecutive_cycles"
            ],
            "incident_on_observation": settings[
                "maker_replay_insufficient_coverage_max_consecutive_cycles"
            ]
            + 1,
            "observation_unit": "distinct maker replay",
            "evaluation_policy": "registered zero-coverage predicate with no-opportunity exemption",
        },
        {
            "id": "scheduler_nonzero_exit",
            "artifact": "ops_scheduler/status.json",
            "healthy_reachable_states": ["last_exit_code=0"],
            "degraded_condition": "any job last_exit_code != 0",
            "max_consecutive_degraded_observations": settings[
                "scheduler_nonzero_max_consecutive_cycles"
            ],
            "incident_on_observation": settings["scheduler_nonzero_max_consecutive_cycles"] + 1,
            "observation_unit": "job completion",
            "evaluation_policy": "zero-exit allowlist",
        },
        {
            "id": "scheduler_completion_freshness",
            "artifact": "ops_scheduler/status.json",
            "healthy_reachable_states": ["last successful completion is within the registered job ceiling"],
            "degraded_condition": "a periodic job has no successful completion inside its registered ceiling",
            "max_consecutive_degraded_observations": 0,
            "incident_on_observation": 1,
            "observation_unit": "watchdog wall-clock observation",
            "evaluation_policy": "immediate incident once completion age exceeds the fixed per-job maximum",
            "registered_job_maximum_seconds": REGISTERED_JOB_FRESHNESS_MAX_SECONDS,
        },
        {
            "id": "kill_input_stale_live_stage",
            "artifact": "maker_carry/decision_policy.json",
            "healthy_reachable_states": ["inactive_pre_live", "fresh"],
            "degraded_condition": "decision policy reports kill_data_stale while a live-stage guard is active",
            "max_consecutive_degraded_observations": 0,
            "incident_on_observation": 1,
            "observation_unit": "decision-policy refresh",
            "evaluation_policy": "immediate incident and owner alert; policy action must already be STOP",
        },
        {
            "id": WALLET_REGISTRATION_ID,
            "artifact": "performance/wallet_reconciliation.json",
            "healthy_reachable_states": sorted(WALLET_HEALTHY_STATES),
            "degraded_condition": "any observed reconciliation status outside the registered healthy allowlist",
            "max_consecutive_degraded_observations": settings[
                "wallet_not_clean_max_consecutive_harvests"
            ],
            "incident_on_observation": settings["wallet_not_clean_max_consecutive_harvests"] + 1,
            "observation_unit": "distinct harvest",
            "evaluation_policy": "healthy-status allowlist; unknown sibling states fail closed",
            "legacy_registration_id": LEGACY_WALLET_REGISTRATION_ID,
        },
        {
            "id": "ledger_chain_integrity",
            "artifact": CHAIN_VERIFICATION_ARTIFACT,
            "healthy_reachable_states": ["ok"],
            "degraded_condition": "chain verification is not ok, or the anchor run is blocked_broken_chain",
            "max_consecutive_degraded_observations": 0,
            "incident_on_observation": 1,
            "observation_unit": "anchor verification",
            "evaluation_policy": "immediate incident; a broken chain freezes the tamper lane until an owner re-genesis",
        },
        {
            "id": "disaster_recovery_not_recoverable",
            "artifact": DISASTER_RECOVERY_ARTIFACT,
            "healthy_reachable_states": sorted(DISASTER_RECOVERY_HEALTHY_STATES),
            "degraded_condition": "archive build/push failed, or the observed archive age is outside the active RPO",
            "max_consecutive_degraded_observations": 0,
            "incident_on_observation": 1,
            "observation_unit": "archive attempt",
            "evaluation_policy": "immediate incident; measured against the OBSERVED archive age, never the configured RPO",
        },
        {
            "id": "maker_study_run_failed",
            "artifact": MAKER_STUDY_ARTIFACT,
            "healthy_reachable_states": sorted(MAKER_STUDY_HEALTHY_STATES),
            "degraded_condition": "study status outside the healthy allowlist (a failed run still commits its history row)",
            "max_consecutive_degraded_observations": 0,
            "incident_on_observation": 1,
            "observation_unit": "study run",
            "evaluation_policy": "immediate incident; a silent failure erases that UTC day from the M-A streak",
        },
        {
            "id": "official_book_snapshot_partial",
            "artifact": OFFICIAL_BOOK_ARTIFACT,
            "healthy_reachable_states": ["ok", "disabled", "no_portfolio"],
            "degraded_condition": "persistent partial book collection starves markout measurement",
            "max_consecutive_degraded_observations": settings[
                "official_book_partial_max_consecutive_cycles"
            ],
            "incident_on_observation": settings["official_book_partial_max_consecutive_cycles"] + 1,
            "observation_unit": "snapshot cycle",
            "evaluation_policy": "consecutive-partial predicate; a single partial cycle is normal churn",
        },
        {
            "id": "publication_bridge_stale",
            "artifact": ", ".join(lane["artifact"] for lane in REGISTERED_PUSH_LANES.values()),
            "healthy_reachable_states": ["last successful push inside the registered lane ceiling"],
            "degraded_condition": "a publication bridge reports an error, or its last success is older than its ceiling",
            "max_consecutive_degraded_observations": 0,
            "incident_on_observation": 1,
            "observation_unit": "watchdog wall-clock observation",
            "evaluation_policy": "immediate incident; a bridge that stops publishing is invisible by definition",
            "registered_lane_maximum_seconds": {
                name: lane["maximum_age_seconds"] for name, lane in REGISTERED_PUSH_LANES.items()
            },
        },
        {
            "id": "operating_state_slo_breach",
            "artifact": OPERATING_STATE_ARTIFACT,
            "healthy_reachable_states": ["every SLO row measured and within target"],
            "degraded_condition": "an SLO row is BREACH, or stays UNMEASURABLE past its grace period",
            "max_consecutive_degraded_observations": settings["slo_breach_max_consecutive_cycles"],
            "incident_on_observation": settings["slo_breach_max_consecutive_cycles"] + 1,
            "observation_unit": "operating-state run",
            "evaluation_policy": (
                "per-row consecutive predicate; UNKNOWN is treated as unmeasurable rather than "
                "healthy and alarms on its own longer maximum"
            ),
            "unknown_max_consecutive_observations": settings["slo_unknown_max_consecutive_cycles"],
        },
        {
            "id": "operating_state_unknown_regression",
            "artifact": "performance/operating_state.json",
            "healthy_reachable_states": ["row state is known", "UNKNOWN before any known observation"],
            "degraded_condition": "previously known row becomes UNKNOWN",
            "max_consecutive_degraded_observations": settings[
                "operating_unknown_max_consecutive_cycles"
            ],
            "incident_on_observation": settings["operating_unknown_max_consecutive_cycles"] + 1,
            "observation_unit": "generated operating-state run",
            "evaluation_policy": "known-to-UNKNOWN transition predicate",
        },
    ]


def _stamp(payload: Mapping[str, Any]) -> str:
    for field in ("generated_at_utc", "updated_at_utc", "collected_at_utc", "snapshot_date"):
        value = str(payload.get(field) or "").strip()
        if value:
            return value
    if not payload:
        return ""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _as_of_stamp(as_of: datetime | str | None) -> str:
    if as_of is None:
        return now_utc()
    if isinstance(as_of, str):
        return as_of
    return as_of.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_stamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _incident_id(registration_id: str, entity: str, episode_start: str) -> str:
    material = f"{registration_id}|{entity}|{episode_start}"
    return "degraded_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _incident(
    *,
    generated_at: str,
    registration_id: str,
    entity: str,
    source_artifact: str,
    observation_token: str,
    episode_start: str,
    degraded_state: str,
    reason: str,
    count: int,
    maximum: int,
) -> dict[str, Any]:
    return {
        "incident_id": _incident_id(registration_id, entity, episode_start or observation_token),
        "detected_at_utc": generated_at,
        "registration_id": registration_id,
        "entity": entity,
        "source_artifact": source_artifact,
        "observation_token": observation_token,
        "degraded_state": degraded_state,
        "reason": reason,
        "consecutive_degraded_observations": count,
        "max_consecutive_degraded_observations": maximum,
        "event_type": "incident_open",
        "owner_notification_eligible": True,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }


def _advance_counter(
    previous: Mapping[str, Any],
    *,
    token: str,
    degraded: bool,
) -> dict[str, Any]:
    last_token = str(previous.get("last_observation_token") or "")
    count = _nonnegative_int(previous.get("consecutive_degraded_observations"), 0)
    episode_start = str(previous.get("episode_start_token") or "")
    if token and token != last_token:
        if degraded:
            if count == 0:
                episode_start = token
            count += 1
        else:
            count = 0
            episode_start = ""
        last_token = token
    return {
        "last_observation_token": last_token,
        "consecutive_degraded_observations": count,
        "episode_start_token": episode_start,
        "currently_degraded": bool(degraded),
    }


def _requote_rules(payload: Mapping[str, Any]) -> set[str]:
    rules: set[str] = set()
    markets = payload.get("markets") if isinstance(payload.get("markets"), list) else []
    for market in markets:
        if not isinstance(market, Mapping):
            continue
        alerts = market.get("alerts") if isinstance(market.get("alerts"), list) else []
        for alert in alerts:
            if isinstance(alert, Mapping):
                rule = str(alert.get("rule") or "").strip()
                if rule:
                    rules.add(rule)
    return rules


def _requote_market_rules(payload: Mapping[str, Any]) -> list[tuple[str, set[str]]]:
    market_rules: list[tuple[str, set[str]]] = []
    markets = payload.get("markets") if isinstance(payload.get("markets"), list) else []
    for market in markets:
        if not isinstance(market, Mapping):
            continue
        rules: set[str] = set()
        alerts = market.get("alerts") if isinstance(market.get("alerts"), list) else []
        for alert in alerts:
            if not isinstance(alert, Mapping):
                continue
            rule = str(alert.get("rule") or "").strip()
            if rule:
                rules.add(rule)
        market_rules.append((str(market.get("alert_state") or "unobserved"), rules))
    return market_rules


def _evaluate_requote(
    cfg: EngineConfig,
    state: dict[str, Any],
    settings: Mapping[str, Any],
    generated_at: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    relative = "maker_carry/requote_alerts.json"
    payload = _mapping(read_json(cfg.output_root / relative, default={}) or {})
    token = _stamp(payload)
    alert_state = str(payload.get("alert_state") or "unobserved")
    rules = _requote_rules(payload)
    missing_rules = sorted(rules & MISSING_INPUT_RULES)
    market_rules = _requote_market_rules(payload)
    triggering_missing_rules = sorted(
        {
            rule
            for market_state, market_rule_set in market_rules
            if market_state in {"pull_quotes_now", "STOP"}
            for rule in market_rule_set & MISSING_INPUT_RULES
        }
    )
    degraded = bool(triggering_missing_rules)
    counters = state.setdefault("counters", {})
    counter = _advance_counter(_mapping(counters.get("requote_missing_inputs")), token=token, degraded=degraded)
    counters["requote_missing_inputs"] = counter
    maximum = int(settings["requote_missing_input_max_consecutive_cycles"])
    count = int(counter["consecutive_degraded_observations"])
    incidents: dict[str, dict[str, Any]] = {}
    if token and degraded and count > maximum:
        row = _incident(
            generated_at=generated_at,
            registration_id="requote_missing_inputs",
            entity="requote_alerts",
            source_artifact=relative,
            observation_token=token,
            episode_start=str(counter["episode_start_token"]),
            degraded_state=alert_state,
            reason="persistent missing-input rules: " + ", ".join(triggering_missing_rules),
            count=count,
            maximum=maximum,
        )
        incidents[row["incident_id"]] = row
    risk_rules = sorted(rules - MISSING_INPUT_RULES)
    evaluation = {
        "registration_id": "requote_missing_inputs",
        "artifact": relative,
        "observed_state": alert_state,
        "observation_token": token or None,
        "consecutive_degraded_observations": count,
        "max_consecutive_degraded_observations": maximum,
        "state": "incident" if incidents else ("degraded_within_tolerance" if degraded else "healthy_or_valid_risk_state"),
        "missing_input_rules": missing_rules,
        "triggering_missing_input_rules": triggering_missing_rules,
        "risk_rules_ignored_by_watchdog": risk_rules,
    }
    return evaluation, incidents


def _evaluate_scheduler(
    cfg: EngineConfig,
    state: dict[str, Any],
    settings: Mapping[str, Any],
    generated_at: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    relative = "ops_scheduler/status.json"
    payload = _mapping(read_json(cfg.output_root / relative, default={}) or {})
    jobs = _mapping(payload.get("jobs"))
    previous_jobs = _mapping(state.get("scheduler_jobs"))
    next_jobs: dict[str, Any] = {}
    failing: list[dict[str, Any]] = []
    incidents: dict[str, dict[str, Any]] = {}
    maximum = int(settings["scheduler_nonzero_max_consecutive_cycles"])
    for job_name, raw_job in sorted(jobs.items()):
        job = _mapping(raw_job)
        # WO-121: a job entry with no last_exit_code used to default to 0, so a
        # truncated or partially written job record read as a clean success.
        # Every writer stamps the field; its absence means the record is not
        # trustworthy, which is a degraded observation, not a passing one.
        try:
            exit_code = int(job.get("last_exit_code", 1))
        except (TypeError, ValueError):
            exit_code = 1
        token = str(job.get("last_run_utc") or job.get("started_at_utc") or _stamp(job))
        previous = _mapping(previous_jobs.get(job_name))
        was_degraded = bool(previous.get("currently_degraded"))
        degraded = exit_code != 0
        episode_start = str(previous.get("episode_start_token") or "") if was_degraded else ""
        if degraded and not episode_start:
            episode_start = token
        next_jobs[job_name] = {
            "last_observation_token": token,
            "episode_start_token": episode_start if degraded else "",
            "currently_degraded": degraded,
            "last_exit_code": exit_code,
        }
        if not degraded:
            continue
        failing.append({"job": job_name, "last_exit_code": exit_code, "last_run_utc": token})
        row = _incident(
            generated_at=generated_at,
            registration_id="scheduler_nonzero_exit",
            entity=job_name,
            source_artifact=relative,
            observation_token=token,
            episode_start=episode_start,
            degraded_state=f"last_exit_code={exit_code}",
            reason=f"scheduler job {job_name} exited with {exit_code}",
            count=1,
            maximum=maximum,
        )
        incidents[row["incident_id"]] = row
    state["scheduler_jobs"] = next_jobs
    return (
        {
            "registration_id": "scheduler_nonzero_exit",
            "artifact": relative,
            "observed_jobs": len(jobs),
            "state": "incident" if failing else ("healthy" if jobs else "unobserved"),
            "max_consecutive_degraded_observations": maximum,
            "failing_jobs": failing,
        },
        incidents,
    )


def _evaluate_scheduler_freshness(
    cfg: EngineConfig,
    state: dict[str, Any],
    settings: Mapping[str, Any],
    generated_at: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Trip immediately when a periodic job stops completing successfully."""

    del settings  # fixed registration; configuration cannot widen freshness
    relative = "ops_scheduler/status.json"
    payload = _mapping(read_json(cfg.output_root / relative, default={}) or {})
    jobs = _mapping(payload.get("jobs"))
    if not jobs:
        # A missing scheduler artifact is already UNKNOWN in WO-68. Do not
        # manufacture wall-clock ages from unrelated watchdog-only fixtures;
        # completion freshness activates once the scheduler has evidenced at
        # least one real periodic job.
        return (
            {
                "registration_id": "scheduler_completion_freshness",
                "artifact": relative,
                "state": "unobserved",
                "jobs": [],
                "stale_jobs": [],
            },
            {},
        )
    observed_at = _parse_stamp(generated_at) or datetime.now(timezone.utc)
    previous_jobs = _mapping(state.get("scheduler_freshness"))
    next_jobs: dict[str, Any] = {}
    evaluations: list[dict[str, Any]] = []
    incidents: dict[str, dict[str, Any]] = {}

    for job_name, maximum in REGISTERED_JOB_FRESHNESS_MAX_SECONDS.items():
        job = _mapping(jobs.get(job_name))
        previous = _mapping(previous_jobs.get(job_name))
        last_success = str(job.get("last_success_utc") or "").strip()
        if not last_success:
            try:
                exit_code = int(job.get("last_exit_code", 1))
            except (TypeError, ValueError):
                exit_code = 1
            if exit_code == 0:
                # Backward-compatible migration for status artifacts written
                # before WO-85 added the durable last-success field.
                last_success = str(job.get("last_run_utc") or "").strip()
        if not last_success:
            last_success = str(previous.get("last_success_utc") or "").strip()

        success_at = _parse_stamp(last_success)
        first_unobserved = str(previous.get("first_unobserved_at_utc") or "").strip()
        if success_at is not None:
            first_unobserved = ""
            age_seconds = max(0.0, (observed_at - success_at).total_seconds())
            observation_token = last_success
            observed_state = "fresh" if age_seconds <= maximum else "stale"
        else:
            if not first_unobserved:
                first_unobserved = generated_at
            first_at = _parse_stamp(first_unobserved) or observed_at
            age_seconds = max(0.0, (observed_at - first_at).total_seconds())
            observation_token = first_unobserved
            observed_state = "unobserved" if age_seconds <= maximum else "stale_unobserved"

        stale = age_seconds > maximum
        next_jobs[job_name] = {
            "last_success_utc": last_success,
            "first_unobserved_at_utc": first_unobserved,
            "age_seconds": round(age_seconds, 3),
            "maximum_age_seconds": maximum,
            "currently_stale": stale,
        }
        evaluations.append(
            {
                "job": job_name,
                "state": observed_state,
                "last_success_utc": last_success or None,
                "age_seconds": round(age_seconds, 3),
                "maximum_age_seconds": maximum,
            }
        )
        if not stale:
            continue
        row = _incident(
            generated_at=generated_at,
            registration_id="scheduler_completion_freshness",
            entity=job_name,
            source_artifact=relative,
            observation_token=observation_token,
            episode_start=observation_token,
            degraded_state=observed_state,
            reason=(
                f"scheduler job {job_name} has no successful completion within "
                f"{maximum} seconds; measured age {round(age_seconds, 3)} seconds"
            ),
            count=1,
            maximum=0,
        )
        incidents[row["incident_id"]] = row

    state["scheduler_freshness"] = next_jobs
    stale_jobs = [row for row in evaluations if row["state"].startswith("stale")]
    return (
        {
            "registration_id": "scheduler_completion_freshness",
            "artifact": relative,
            "state": "incident" if stale_jobs else "healthy_or_initializing",
            "jobs": evaluations,
            "stale_jobs": stale_jobs,
        },
        incidents,
    )


def _evaluate_kill_input_staleness(
    cfg: EngineConfig,
    state: dict[str, Any],
    settings: Mapping[str, Any],
    generated_at: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Open an immediate owner-alert incident when live safety data is stale."""

    del settings  # policy owns the registered tighten-only freshness maximum
    relative = "maker_carry/decision_policy.json"
    payload = _mapping(read_json(cfg.output_root / relative, default={}) or {})
    kill = _mapping(payload.get("kill_criteria_status"))
    freshness = _mapping(kill.get("kill_input_freshness"))
    guard_active = _boolish(freshness.get("guard_active"), False)
    stale = _boolish(kill.get("kill_data_stale"), False) and guard_active
    latest_observation = str(freshness.get("latest_observation_utc") or "").strip()
    observation_token = latest_observation or str(payload.get("generated_at_utc") or "").strip()
    episode_key = "kill_input_staleness_episode_start"
    episode_start = str(state.get(episode_key) or "").strip()
    if stale:
        if not episode_start:
            episode_start = latest_observation or generated_at
        state[episode_key] = episode_start
    else:
        state.pop(episode_key, None)

    incidents: dict[str, dict[str, Any]] = {}
    if stale:
        age = freshness.get("age_seconds")
        maximum = freshness.get("maximum_age_seconds")
        row = _incident(
            generated_at=generated_at,
            registration_id="kill_input_stale_live_stage",
            entity="maker_live_test_kill_inputs",
            source_artifact=relative,
            observation_token=observation_token or episode_start,
            episode_start=episode_start,
            degraded_state="kill_data_stale",
            reason=(
                "live-stage kill inputs are stale or missing; measured age "
                f"{age} seconds against maximum {maximum} seconds; decision policy "
                "must remain stop_quoting_review_before_resume"
            ),
            count=1,
            maximum=0,
        )
        incidents[row["incident_id"]] = row

    if not payload:
        observed_state = "unobserved"
    elif stale:
        observed_state = "incident"
    else:
        observed_state = str(freshness.get("state") or "unknown")
    return (
        {
            "registration_id": "kill_input_stale_live_stage",
            "artifact": relative,
            "state": observed_state,
            "guard_active": guard_active,
            "kill_data_stale": stale,
            "latest_observation_utc": latest_observation or None,
            "age_seconds": freshness.get("age_seconds"),
            "maximum_age_seconds": freshness.get("maximum_age_seconds"),
            "indicated_action": payload.get("indicated_action"),
        },
        incidents,
    )


def _evaluate_maker_replay(
    cfg: EngineConfig,
    state: dict[str, Any],
    settings: Mapping[str, Any],
    generated_at: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    relative = "maker_carry/maker_fill_replay.json"
    payload = _mapping(read_json(cfg.output_root / relative, default={}) or {})
    token = _stamp(payload)
    observed = str(payload.get("coverage_status") or payload.get("status") or "unobserved")
    degraded = observed == "insufficient_coverage" or str(payload.get("status") or "") == "insufficient_coverage"
    counters = state.setdefault("counters", {})
    counter = _advance_counter(
        _mapping(counters.get("maker_replay_insufficient_coverage")),
        token=token,
        degraded=degraded,
    )
    counters["maker_replay_insufficient_coverage"] = counter
    maximum = int(settings["maker_replay_insufficient_coverage_max_consecutive_cycles"])
    count = int(counter["consecutive_degraded_observations"])
    incidents: dict[str, dict[str, Any]] = {}
    if token and degraded and count > maximum:
        row = _incident(
            generated_at=generated_at,
            registration_id="maker_replay_insufficient_coverage",
            entity="maker_fill_replay",
            source_artifact=relative,
            observation_token=token,
            episode_start=str(counter["episode_start_token"]),
            degraded_state=observed,
            reason=(
                "maker replay has simulated fill opportunities but no covered 5m official-book window; "
                "a zero realism ratio must not be interpreted as evidence"
            ),
            count=count,
            maximum=maximum,
        )
        incidents[row["incident_id"]] = row
    return (
        {
            "registration_id": "maker_replay_insufficient_coverage",
            "artifact": relative,
            "observed_state": observed,
            "observation_token": token or None,
            "consecutive_degraded_observations": count,
            "max_consecutive_degraded_observations": maximum,
            "state": "incident" if incidents else ("degraded_within_tolerance" if degraded else ("healthy" if token else "unobserved")),
        },
        incidents,
    )


def _evaluate_wallet(
    cfg: EngineConfig,
    state: dict[str, Any],
    settings: Mapping[str, Any],
    generated_at: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    relative = "performance/wallet_reconciliation.json"
    payload = _mapping(read_json(cfg.output_root / relative, default={}) or {})
    token = _stamp(payload)
    status = str(payload.get("reconciliation_status") or payload.get("status") or "unobserved")
    normalized_status = status.strip().lower()
    degraded = bool(token) and normalized_status not in WALLET_HEALTHY_STATES
    counters = state.setdefault("counters", {})
    migrated_legacy_counter = WALLET_REGISTRATION_ID not in counters and LEGACY_WALLET_REGISTRATION_ID in counters
    previous_counter = _mapping(
        counters.get(WALLET_REGISTRATION_ID, counters.get(LEGACY_WALLET_REGISTRATION_ID))
    )
    counter = _advance_counter(
        previous_counter,
        token=token,
        degraded=degraded,
    )
    counters[WALLET_REGISTRATION_ID] = counter
    counters.pop(LEGACY_WALLET_REGISTRATION_ID, None)
    maximum = int(settings["wallet_not_clean_max_consecutive_harvests"])
    count = int(counter["consecutive_degraded_observations"])
    incidents: dict[str, dict[str, Any]] = {}
    if token and degraded and count > maximum:
        row = _incident(
            generated_at=generated_at,
            registration_id=WALLET_REGISTRATION_ID,
            entity="wallet_reconciliation",
            source_artifact=relative,
            observation_token=token,
            episode_start=str(counter["episode_start_token"]),
            degraded_state=status,
            reason=str(
                payload.get("discrepancy_note")
                or payload.get("note")
                or (
                    f"wallet reconciliation status {status!r} is outside healthy allowlist "
                    + ", ".join(sorted(WALLET_HEALTHY_STATES))
                )
            ),
            count=count,
            maximum=maximum,
        )
        incidents[row["incident_id"]] = row
    return (
        {
            "registration_id": WALLET_REGISTRATION_ID,
            "artifact": relative,
            "observed_state": status,
            "healthy_reachable_states": sorted(WALLET_HEALTHY_STATES),
            "observation_token": token or None,
            "consecutive_degraded_observations": count,
            "max_consecutive_degraded_observations": maximum,
            "state": "incident" if incidents else ("degraded_within_tolerance" if degraded else ("healthy" if token else "unobserved")),
            "migrated_legacy_counter": migrated_legacy_counter,
        },
        incidents,
    )


def _evaluate_chain_integrity(
    cfg: EngineConfig,
    state: dict[str, Any],
    settings: Mapping[str, Any],
    generated_at: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Open an immediate incident while the WO-61 tamper chain is broken.

    2026-07-12..25 precedent: the chain broke, `anchor_ledgers` short-circuited
    `blocked_broken_chain` every run, and the CLI exited 0 - so the head stayed
    frozen for nine days with every dashboard green. WO-115 made the exit code
    loud; this makes the STATE observed even if the exit code is missed.
    """

    del settings  # fixed registration; a broken chain is never within tolerance
    verification = _mapping(read_json(cfg.output_root / CHAIN_VERIFICATION_ARTIFACT, default={}) or {})
    summary = _mapping(read_json(cfg.output_root / CHAIN_SUMMARY_ARTIFACT, default={}) or {})
    verification_status = str(verification.get("status") or "").strip()
    summary_status = str(summary.get("status") or "").strip()
    token = _stamp(verification) or _stamp(summary)

    broken_reasons: list[str] = []
    if verification_status and verification_status != "ok":
        first_broken = verification.get("first_broken_date")
        issues = verification.get("issues") if isinstance(verification.get("issues"), list) else []
        broken_reasons.append(
            f"chain verification status={verification_status}"
            + (f" first_broken_date={first_broken}" if first_broken else "")
            + (f" issues={issues[:3]}" if issues else "")
        )
    if summary_status == "blocked_broken_chain":
        broken_reasons.append("anchor run is blocked_broken_chain; the head cannot advance")
    # Fail-closed: once the chain has ever been anchored, a MISSING verification
    # artifact is an unverified chain, not a healthy one.
    if summary_status and summary_status not in {"disabled"} and not verification_status:
        broken_reasons.append("chain verification artifact is missing while the anchor lane is active")

    episode_key = "chain_integrity_episode_start"
    episode_start = str(state.get(episode_key) or "").strip()
    if broken_reasons:
        episode_start = episode_start or token or generated_at
        state[episode_key] = episode_start
    else:
        state.pop(episode_key, None)

    incidents: dict[str, dict[str, Any]] = {}
    if broken_reasons:
        row = _incident(
            generated_at=generated_at,
            registration_id="ledger_chain_integrity",
            entity="ledger_anchor_chain",
            source_artifact=CHAIN_VERIFICATION_ARTIFACT,
            observation_token=token or episode_start,
            episode_start=episode_start,
            degraded_state=verification_status or summary_status or "unverified",
            reason="; ".join(broken_reasons),
            count=1,
            maximum=0,
        )
        incidents[row["incident_id"]] = row

    if not verification and not summary:
        observed_state = "unobserved"
    elif broken_reasons:
        observed_state = "incident"
    else:
        observed_state = "healthy"
    return (
        {
            "registration_id": "ledger_chain_integrity",
            "artifact": CHAIN_VERIFICATION_ARTIFACT,
            "state": observed_state,
            "verification_status": verification_status or None,
            "anchor_summary_status": summary_status or None,
            "verified_through_date": verification.get("verified_through_date"),
            "first_broken_date": verification.get("first_broken_date"),
            "observation_token": token or None,
        },
        incidents,
    )


def _evaluate_disaster_recovery(
    cfg: EngineConfig,
    state: dict[str, Any],
    settings: Mapping[str, Any],
    generated_at: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Observe the recovery lane, which no gate, report, or human ever opened.

    2026-07-16..26 precedent: the archive builder failed for ten consecutive
    days while `disaster_recovery_status.json` published the failure to nobody.
    """

    del settings  # fixed registration; an unrecoverable system is never tolerated
    payload = _mapping(read_json(cfg.output_root / DISASTER_RECOVERY_ARTIFACT, default={}) or {})
    token = _stamp(payload)
    status = str(payload.get("status") or "").strip()
    push_status = str(payload.get("remote_push_status") or "").strip()
    rpo = _mapping(payload.get("rpo"))
    age_hours = payload.get("last_remote_archive_age_hours")
    active_rpo = rpo.get("active_rpo_hours")

    reasons: list[str] = []
    if status and status not in DISASTER_RECOVERY_HEALTHY_STATES:
        reasons.append(
            f"archive status={status}: " + str(payload.get("error") or payload.get("remote_error") or "no detail")
        )
    if push_status and push_status not in {"ok", "pending"}:
        reasons.append(
            f"remote push status={push_status}: " + str(payload.get("remote_error") or "no detail")
        )
    # The honest predicate (WO-122a) compares the OBSERVED archive age against
    # the active RPO. `compliant` used to be hardcoded true beside a 233-hour
    # age; trust it now, but only as a False signal.
    if token and rpo.get("compliant") is False:
        reasons.append(
            f"observed archive age {age_hours}h is outside the active RPO of {active_rpo}h"
        )

    episode_key = "disaster_recovery_episode_start"
    episode_start = str(state.get(episode_key) or "").strip()
    if reasons:
        episode_start = episode_start or token or generated_at
        state[episode_key] = episode_start
    else:
        state.pop(episode_key, None)

    incidents: dict[str, dict[str, Any]] = {}
    if reasons:
        row = _incident(
            generated_at=generated_at,
            registration_id="disaster_recovery_not_recoverable",
            entity="ledger_state_archive",
            source_artifact=DISASTER_RECOVERY_ARTIFACT,
            observation_token=token or episode_start,
            episode_start=episode_start,
            degraded_state=status or push_status or "unknown",
            reason="; ".join(reasons),
            count=1,
            maximum=0,
        )
        incidents[row["incident_id"]] = row

    return (
        {
            "registration_id": "disaster_recovery_not_recoverable",
            "artifact": DISASTER_RECOVERY_ARTIFACT,
            "state": "unobserved" if not payload else ("incident" if reasons else "healthy"),
            "archive_status": status or None,
            "remote_push_status": push_status or None,
            "observed_archive_age_hours": age_hours,
            "active_rpo_hours": active_rpo,
            "rpo_compliant": rpo.get("compliant"),
            "archive_excluded_paths": payload.get("archive_excluded_paths"),
            "observation_token": token or None,
        },
        incidents,
    )


def _evaluate_maker_study_status(
    cfg: EngineConfig,
    state: dict[str, Any],
    settings: Mapping[str, Any],
    generated_at: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """A failed study run must not pass as a banked day."""

    del settings  # fixed registration; a failed measurement day is not tolerable
    payload = _mapping(read_json(cfg.output_root / MAKER_STUDY_ARTIFACT, default={}) or {})
    token = _stamp(payload)
    status = str(payload.get("status") or "").strip()
    degraded = bool(token) and bool(status) and status not in MAKER_STUDY_HEALTHY_STATES

    episode_key = "maker_study_failure_episode_start"
    episode_start = str(state.get(episode_key) or "").strip()
    if degraded:
        episode_start = episode_start or token
        state[episode_key] = episode_start
    else:
        state.pop(episode_key, None)

    incidents: dict[str, dict[str, Any]] = {}
    if degraded:
        errors = payload.get("errors") if isinstance(payload.get("errors"), list) else []
        row = _incident(
            generated_at=generated_at,
            registration_id="maker_study_run_failed",
            entity="maker_carry_study",
            source_artifact=MAKER_STUDY_ARTIFACT,
            observation_token=token,
            episode_start=episode_start,
            degraded_state=status,
            reason=(
                f"maker-carry study status={status}; the run still committed its history row, so "
                "this UTC day is banked with failed inputs unless it is fixed same-day"
                + (f"; first errors={errors[:3]}" if errors else "")
            ),
            count=1,
            maximum=0,
        )
        incidents[row["incident_id"]] = row

    return (
        {
            "registration_id": "maker_study_run_failed",
            "artifact": MAKER_STUDY_ARTIFACT,
            "state": "unobserved" if not token else ("incident" if degraded else "healthy"),
            "observed_state": status or None,
            "healthy_reachable_states": sorted(MAKER_STUDY_HEALTHY_STATES),
            "observation_token": token or None,
        },
        incidents,
    )


def _evaluate_official_books(
    cfg: EngineConfig,
    state: dict[str, Any],
    settings: Mapping[str, Any],
    generated_at: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Persistent partial book collection starves markout measurement."""

    relative = OFFICIAL_BOOK_ARTIFACT
    payload = _mapping(read_json(cfg.output_root / relative, default={}) or {})
    token = _stamp(payload)
    status = str(payload.get("status") or "unobserved").strip()
    degraded = status == "partial"
    counters = state.setdefault("counters", {})
    counter = _advance_counter(
        _mapping(counters.get("official_book_snapshot_partial")),
        token=token,
        degraded=degraded,
    )
    counters["official_book_snapshot_partial"] = counter
    maximum = int(settings["official_book_partial_max_consecutive_cycles"])
    count = int(counter["consecutive_degraded_observations"])
    incidents: dict[str, dict[str, Any]] = {}
    if token and degraded and count > maximum:
        row = _incident(
            generated_at=generated_at,
            registration_id="official_book_snapshot_partial",
            entity="official_book_snapshot",
            source_artifact=relative,
            observation_token=token,
            episode_start=str(counter["episode_start_token"]),
            degraded_state=status,
            reason=(
                "official-book collection has been partial for "
                f"{count} consecutive cycles; book seasoning and markout measurement "
                "degrade silently while this persists"
            ),
            count=count,
            maximum=maximum,
        )
        incidents[row["incident_id"]] = row
    return (
        {
            "registration_id": "official_book_snapshot_partial",
            "artifact": relative,
            "observed_state": status,
            "observation_token": token or None,
            "markets_polled": payload.get("markets_polled"),
            "consecutive_degraded_observations": count,
            "max_consecutive_degraded_observations": maximum,
            "state": "incident"
            if incidents
            else ("degraded_within_tolerance" if degraded else ("healthy" if token else "unobserved")),
        },
        incidents,
    )


def _evaluate_push_lanes(
    cfg: EngineConfig,
    state: dict[str, Any],
    settings: Mapping[str, Any],
    generated_at: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Measure the bridges that make everything else remotely visible.

    OPS-1/2: neither push could fail observably and NOTHING measured either
    one's age, so the 2026-07-25..26 telemetry blackout presented as silence.
    """

    del settings  # fixed registration; a dark bridge is never within tolerance
    observed_at = _parse_stamp(generated_at) or datetime.now(timezone.utc)
    lanes: list[dict[str, Any]] = []
    incidents: dict[str, dict[str, Any]] = {}

    for lane_name, lane in REGISTERED_PUSH_LANES.items():
        relative = str(lane["artifact"])
        maximum = int(lane["maximum_age_seconds"])
        healthy_states = tuple(lane["healthy_states"])
        payload = _mapping(read_json(cfg.output_root / relative, default={}) or {})
        status = str(payload.get("status") or "").strip()
        last_success = str(payload.get("last_success_at_utc") or "").strip()
        success_at = _parse_stamp(last_success)
        age_seconds = None if success_at is None else max(0.0, (observed_at - success_at).total_seconds())

        reasons: list[str] = []
        if status and status not in healthy_states:
            reasons.append(f"{lane_name} status={status}: " + str(payload.get("detail") or "no detail"))
        if age_seconds is not None and age_seconds > maximum:
            reasons.append(
                f"{lane_name} last succeeded {round(age_seconds)}s ago, above its {maximum}s ceiling"
            )
        # A payload that exists but has never recorded a success is a bridge that
        # has never worked. Absent artifact stays unobserved: the producer lane's
        # own freshness ceiling covers a host where the push was never installed.
        if payload and success_at is None:
            reasons.append(f"{lane_name} has no recorded successful push")

        lanes.append(
            {
                "lane": lane_name,
                "artifact": relative,
                "status": status or None,
                "last_success_at_utc": last_success or None,
                "age_seconds": None if age_seconds is None else round(age_seconds, 3),
                "maximum_age_seconds": maximum,
                "state": "unobserved" if not payload else ("incident" if reasons else "healthy"),
            }
        )
        if not reasons:
            continue
        episode_key = f"push_lane_episode_start_{lane_name}"
        episode_start = str(state.get(episode_key) or "").strip() or last_success or generated_at
        state[episode_key] = episode_start
        row = _incident(
            generated_at=generated_at,
            registration_id="publication_bridge_stale",
            entity=lane_name,
            source_artifact=relative,
            observation_token=_stamp(payload) or generated_at,
            episode_start=episode_start,
            degraded_state=status or "stale",
            reason="; ".join(reasons),
            count=1,
            maximum=0,
        )
        incidents[row["incident_id"]] = row

    for lane_name in REGISTERED_PUSH_LANES:
        if all(row["lane"] != lane_name or row["state"] != "incident" for row in lanes):
            state.pop(f"push_lane_episode_start_{lane_name}", None)

    return (
        {
            "registration_id": "publication_bridge_stale",
            "artifact": ", ".join(lane["artifact"] for lane in REGISTERED_PUSH_LANES.values()),
            "state": "incident"
            if incidents
            else ("healthy" if any(row["state"] == "healthy" for row in lanes) else "unobserved"),
            "lanes": lanes,
        },
        incidents,
    )


def _evaluate_operating_slo(
    cfg: EngineConfig,
    state: dict[str, Any],
    settings: Mapping[str, Any],
    generated_at: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Give the operating-state SLO block a consumer.

    OPS-3: all seven rows were dashboard-only. A breach produced no incident, no
    exit code, and no notification. UNKNOWN rows are treated as unmeasurable
    (fail-closed on their own longer maximum), never as healthy - "no data" was
    the dominant way this system reported health it did not have.
    """

    relative = OPERATING_STATE_ARTIFACT
    payload = _mapping(read_json(cfg.output_root / relative, default={}) or {})
    token = _stamp(payload)
    slo = _mapping(payload.get("slo"))
    rows = slo.get("rows") if isinstance(slo.get("rows"), list) else []
    breach_maximum = int(settings["slo_breach_max_consecutive_cycles"])
    unknown_maximum = int(settings["slo_unknown_max_consecutive_cycles"])
    previous_rows = _mapping(state.get("slo_rows"))
    next_rows: dict[str, Any] = {}
    evaluations: list[dict[str, Any]] = []
    incidents: dict[str, dict[str, Any]] = {}

    for row in rows:
        if not isinstance(row, Mapping):
            continue
        identifier = str(row.get("id") or "").strip()
        if not identifier:
            continue
        row_state = str(row.get("state") or "").strip().upper()
        breach = row_state == "BREACH"
        unknown = row.get("breach") is None
        kind = "breach" if breach else ("unknown" if unknown else "ok")
        maximum = breach_maximum if breach else unknown_maximum
        counter = _advance_counter(
            _mapping(previous_rows.get(identifier)),
            token=token,
            degraded=breach or unknown,
        )
        # A row that flips breach <-> unknown starts a fresh episode: the two
        # states have different registered maxima and different meanings.
        previous_kind = str(_mapping(previous_rows.get(identifier)).get("kind") or "")
        if kind != previous_kind and kind in {"breach", "unknown"}:
            counter["consecutive_degraded_observations"] = 1
            counter["episode_start_token"] = token
        counter["kind"] = kind
        next_rows[identifier] = counter
        count = int(counter["consecutive_degraded_observations"])
        evaluations.append(
            {
                "id": identifier,
                "metric": row.get("metric"),
                "row_state": row_state or None,
                "measured": row.get("measured"),
                "target": row.get("target"),
                "unit": row.get("unit"),
                "kind": kind,
                "consecutive_degraded_observations": count,
                "max_consecutive_degraded_observations": maximum,
            }
        )
        if kind == "ok" or not token or count <= maximum:
            continue
        reason = (
            f"SLO {identifier} measured {row.get('measured')} {row.get('unit')} against target "
            f"{row.get('target')} for {count} consecutive observations"
            if breach
            else (
                f"SLO {identifier} has been unmeasurable for {count} consecutive observations "
                f"(source {row.get('source')}); an unmeasured SLO is not a passing one"
            )
        )
        incident_row = _incident(
            generated_at=generated_at,
            registration_id="operating_state_slo_breach",
            entity=identifier,
            source_artifact=relative,
            observation_token=token,
            episode_start=str(counter["episode_start_token"]),
            degraded_state=row_state or "UNKNOWN",
            reason=reason,
            count=count,
            maximum=maximum,
        )
        incidents[incident_row["incident_id"]] = incident_row

    state["slo_rows"] = next_rows
    breached = [row for row in evaluations if row["kind"] == "breach"]
    unmeasured = [row for row in evaluations if row["kind"] == "unknown"]
    return (
        {
            "registration_id": "operating_state_slo_breach",
            "artifact": relative,
            "state": "incident" if incidents else ("healthy" if rows else "unobserved"),
            "slo_block_status": slo.get("status"),
            "observation_token": token or None,
            "observed_rows": len(evaluations),
            "breaching_rows": [row["id"] for row in breached],
            "unmeasurable_rows": [row["id"] for row in unmeasured],
            "max_consecutive_degraded_observations": breach_maximum,
            "unknown_max_consecutive_observations": unknown_maximum,
            "rows": evaluations,
        },
        incidents,
    )


def _evaluate_operating_state(
    cfg: EngineConfig,
    state: dict[str, Any],
    settings: Mapping[str, Any],
    generated_at: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    relative = "performance/operating_state.json"
    payload = _mapping(read_json(cfg.output_root / relative, default={}) or {})
    token = _stamp(payload)
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    previous_states = _mapping(state.get("operating_rows"))
    previous_episodes = _mapping(state.get("operating_unknown_episodes"))
    current_states: dict[str, str] = {}
    current_episodes: dict[str, str] = {}
    regressed: list[str] = []
    incidents: dict[str, dict[str, Any]] = {}
    maximum = int(settings["operating_unknown_max_consecutive_cycles"])
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        key = str(row.get("key") or row.get("question") or "").strip()
        if not key:
            continue
        current = str(row.get("state") or "UNKNOWN").strip() or "UNKNOWN"
        previous = str(previous_states.get(key) or "").strip()
        current_states[key] = current
        is_unknown = current.upper() == "UNKNOWN"
        had_known_value = bool(previous and previous.upper() != "UNKNOWN")
        episode_start = str(previous_episodes.get(key) or "")
        if is_unknown and (had_known_value or episode_start):
            episode_start = episode_start or token
            current_episodes[key] = episode_start
            regressed.append(key)
            row_incident = _incident(
                generated_at=generated_at,
                registration_id="operating_state_unknown_regression",
                entity=key,
                source_artifact=relative,
                observation_token=token,
                episode_start=episode_start,
                degraded_state="UNKNOWN",
                reason=f"operating-state row {key} regressed from {previous or 'known'} to UNKNOWN",
                count=1,
                maximum=maximum,
            )
            incidents[row_incident["incident_id"]] = row_incident
    state["operating_rows"] = current_states
    state["operating_unknown_episodes"] = current_episodes
    return (
        {
            "registration_id": "operating_state_unknown_regression",
            "artifact": relative,
            "observation_token": token or None,
            "observed_rows": len(current_states),
            "state": "incident" if regressed else ("healthy" if token else "unobserved"),
            "max_consecutive_degraded_observations": maximum,
            "regressed_rows": regressed,
        },
        incidents,
    )


def _append_incidents(path: Path, incidents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not incidents:
        return []
    existing_ids = {row.get("incident_id", "") for row in read_csv_rows(path)}
    new_rows = [row for row in incidents if str(row.get("incident_id") or "") not in existing_ids]
    if not new_rows:
        return []
    ensure_dir(path.parent)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=INCIDENT_FIELDS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        for row in new_rows:
            writer.writerow({key: serialize_value(row.get(key, "")) for key in INCIDENT_FIELDS})
        handle.flush()
        os.fsync(handle.fileno())
    return new_rows


def _push(url: str, message: str) -> dict[str, Any]:
    """Deliver one owner push. A send failure must never break the watchdog."""

    try:
        response = requests.post(url, data=message.encode("utf-8"), timeout=10)
        return {
            "attempted": True,
            "delivered": response.status_code < 300,
            "status_code": response.status_code,
        }
    except Exception as exc:  # noqa: BLE001 - reporting path; never raise
        return {"attempted": True, "delivered": False, "error": f"{type(exc).__name__}"}


def _notification(
    cfg: EngineConfig,
    *,
    generated_at: str,
    enabled: bool,
    active: list[dict[str, Any]],
    new: list[dict[str, Any]],
) -> dict[str, Any]:
    body_path = cfg.output_root / NOTIFICATION_BODY
    lines = [
        "# Polymarket degraded-state incident",
        "",
        f"Generated: `{generated_at}`",
        f"Active incidents: **{len(active)}**",
        "",
    ]
    for row in active:
        lines.append(
            f"- **{row.get('registration_id')} / {row.get('entity')}**: {row.get('reason')} "
            f"(source `{row.get('source_artifact')}`)"
        )
    if not active:
        lines.append("- No active semantic-health incidents.")
    lines += [
        "",
        "Human review only. Fail-closed and risk states remain unchanged; this watchdog cannot trade or cancel orders.",
    ]
    ensure_dir(body_path.parent)
    body_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    notify = bool(enabled and new)
    subject = f"Polymarket degraded-state incidents: {len(active)} active"
    # WO-121 (OPS-3): `notify: true` had no delivery channel, so every incident
    # and SLO breach waited for someone to open a JSON file. Deliver on the
    # state CHANGE only (a new incident id), reusing the WO-99 ntfy contract:
    # topic URL from the VPS environment only, never config/telemetry/repo, and
    # a bounded message carrying registration ids only - no market, wallet,
    # amount, or artifact contents leave the host.
    url = str(os.environ.get(NTFY_ENV_VAR) or "").strip()
    push: dict[str, Any] = {"attempted": False, "channel_configured": bool(url)}
    if notify and url:
        registrations = sorted({str(row.get("registration_id") or "") for row in new if row})
        message = (
            f"{subject}\n"
            f"New: {len(new)} ({', '.join(registrations) or 'unclassified'})\n"
            "Read degraded_state_watchdog.json on the VPS. Reporting only; nothing trades."
        )
        push = {**_push(url, message), "channel_configured": True}
    return {
        "enabled": enabled,
        "eligible": bool(new),
        "notify": notify,
        "state_changed": bool(new),
        "subject": subject,
        "body_file": str(body_path),
        "pattern": "superbru_score_change_state_digest",
        "push": push,
    }


def build_degraded_state_watchdog(
    cfg: EngineConfig,
    *,
    as_of: datetime | str | None = None,
) -> dict[str, Any]:
    """Evaluate registered semantic-health states and persist incident evidence."""
    settings = _settings(cfg)
    generated_at = _as_of_stamp(as_of)
    output_path = cfg.output_root / OUTPUT_FILE
    state_path = cfg.output_root / STATE_FILE
    ledger_path = cfg.output_root / INCIDENT_LEDGER
    base = {
        "work_order": WORK_ORDER,
        "generated_at_utc": generated_at,
        "read_only": True,
        "decision_use": "owner alerting only; fail-closed, risk, gate, broker, sizing, and order states are unchanged",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
        "order_placement_invoked": False,
        "order_amendment_invoked": False,
        "order_cancellation_invoked": False,
    }
    if not settings["enabled"]:
        payload = {
            **base,
            "status": "disabled",
            "registrations": _registrations(settings),
            "evaluations": [],
            "active_incidents": [],
            "new_incidents": [],
            "notification": {"enabled": settings["notification_enabled"], "eligible": False, "notify": False},
        }
        write_json(output_path, payload)
        return payload

    with runtime_lock(
        cfg,
        "degraded_state_watchdog",
        stale_after_seconds=float(settings["lock_stale_seconds"]),
    ) as lock:
        if not lock.acquired:
            # WO-121 (OPS-4): this path used to overwrite the artifact with
            # empty incident lists at exit 0, so a held lock published "no
            # incidents" over a real, active incident set. A skipped cycle
            # observes nothing, so it must not claim anything either: carry the
            # previous evaluation forward verbatim and label it stale.
            previous = _mapping(read_json(output_path, default={}) or {})
            carried = [row for row in (previous.get("active_incidents") or []) if isinstance(row, Mapping)]
            payload = {
                **base,
                "status": "skipped_lock_held",
                "registrations": _registrations(settings),
                "evaluations": previous.get("evaluations") or [],
                "active_incident_count": len(carried),
                "active_incidents": carried,
                "new_incidents": [],
                "carried_forward_from_utc": previous.get("generated_at_utc"),
                "carried_forward_reason": "lock held by a concurrent watchdog run; nothing was re-observed",
                "lock": lock.as_dict(),
                "notification": {"enabled": settings["notification_enabled"], "eligible": False, "notify": False},
            }
            write_json(output_path, payload)
            return payload

        state = _mapping(read_json(state_path, default={}) or {})
        evaluations: list[dict[str, Any]] = []
        active_by_id: dict[str, dict[str, Any]] = {}
        for evaluator in (
            _evaluate_requote,
            _evaluate_maker_replay,
            _evaluate_scheduler,
            _evaluate_scheduler_freshness,
            _evaluate_kill_input_staleness,
            _evaluate_wallet,
            _evaluate_operating_state,
            _evaluate_chain_integrity,
            _evaluate_disaster_recovery,
            _evaluate_maker_study_status,
            _evaluate_official_books,
            _evaluate_push_lanes,
            _evaluate_operating_slo,
        ):
            evaluation, incidents = evaluator(cfg, state, settings, generated_at)
            evaluations.append(evaluation)
            active_by_id.update(incidents)

        active = sorted(active_by_id.values(), key=lambda row: (row["registration_id"], row["entity"]))
        new = _append_incidents(ledger_path, active)
        notification = _notification(
            cfg,
            generated_at=generated_at,
            enabled=bool(settings["notification_enabled"]),
            active=active,
            new=new,
        )
        state.update(
            {
                "generated_at_utc": generated_at,
                "active_incident_ids": [row["incident_id"] for row in active],
                "paper_trading_invoked": False,
                "live_trading_invoked": False,
            }
        )
        write_json(state_path, state)
        payload = {
            **base,
            "status": "incident" if active else "ok",
            "registrations": _registrations(settings),
            "evaluations": evaluations,
            "active_incident_count": len(active),
            "new_incident_count": len(new),
            "active_incidents": active,
            "new_incidents": new,
            "incident_ledger": str(ledger_path),
            "notification": notification,
            "notify": notification["notify"],
            "body_file": notification["body_file"],
            "state_changed": notification["state_changed"],
        }
        write_json(output_path, payload)
        return payload


def main(config_path: str) -> dict[str, Any]:
    return build_degraded_state_watchdog(load_config(config_path))
