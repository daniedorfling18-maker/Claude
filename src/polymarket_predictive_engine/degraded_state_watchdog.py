"""WO-78 semantic-health watchdog for persistent degraded runtime states.

The watchdog is deliberately reporting-only.  It observes already-generated
artifacts, counts distinct producer observations, appends incident evidence,
and emits the existing owner-notification artifact contract.  It never
changes a gate, quote state, broker, sizing rule, or order path.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import requests

from .config import EngineConfig, load_config
from .runtime_lock import runtime_lock
from .utils import append_csv_rows, now_utc, parse_timestamp, read_csv_rows, read_json, write_json, write_text_atomic


WORK_ORDER = "WO-78+WO-83+WO-84+WO-85+WO-86+WO-121+WO-129"
OUTPUT_FILE = "ops_scheduler/degraded_state_watchdog.json"
STATE_FILE = "ops_scheduler/degraded_state_watchdog_state.json"
INCIDENT_LEDGER = "performance/degraded_state_incidents.csv"
NOTIFICATION_BODY = "ops_scheduler/degraded_state_notification.md"
WALLET_REGISTRATION_ID = "wallet_reconciliation_not_clean"
LEGACY_WALLET_REGISTRATION_ID = "wallet_reconciliation_partial"
WALLET_HEALTHY_STATES = frozenset({"clean", "explained"})
OFFICIAL_BOOK_HEALTHY_STATES = frozenset({"ok", "disabled", "no_portfolio"})
DR_STATUS_MAX_AGE_SECONDS = 6 * 60 * 60
NTFY_ENV_VAR = "OPS_OWNER_NTFY_TOPIC_URL"
MAX_NOTIFICATION_IDS = 20

# A maximum is the number of consecutive degraded observations tolerated.
# Therefore max=3 trips on observation four, exactly matching "> 3 cycles".
REGISTERED_MAXIMA: dict[str, int] = {
    "requote_missing_input_max_consecutive_cycles": 3,
    "scheduler_nonzero_max_consecutive_cycles": 0,
    "wallet_not_clean_max_consecutive_harvests": 2,
    "operating_unknown_max_consecutive_cycles": 0,
    "maker_replay_insufficient_coverage_max_consecutive_cycles": 3,
    "official_book_snapshot_partial_max_consecutive_cycles": 3,
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
    "ledger_anchor": 26 * 60 * 60,
    "maker_safety_refresh": 60 * 60,
}

PUSH_STATUS_MAX_SECONDS = 2 * 60 * 60
PRODUCER_REGISTRATIONS = (
    ("ledger_chain_integrity", "performance/ledger_anchor_verification.json", {"ok"}),
    # The DR producer's reachable statuses are exactly {ok, not_due, skipped_locked,
    # error}; "not_due" is its routine healthy state (archive not due this cycle) and
    # still passes through the remote-push/RPO clauses below. "skipped_locked" stays
    # OUT deliberately: a held DR lock must alarm, not read healthy.
    ("disaster_recovery_not_recoverable", "performance/disaster_recovery_status.json", {"ok", "not_due"}),
    ("maker_study_run_failed", "maker_carry/maker_carry_study.json", {"ok", "no_candidates", "disabled"}),
)

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
        # WO-144: push-cooldown floor is fixed at 3600s in code; configuration
        # may SHORTEN it (more pushes), never lengthen it, so the config
        # surface cannot quiet the channel below the registered floor.
        "notification_cooldown_seconds": min(3600, _nonnegative_int(raw.get("notification_cooldown_seconds"), 3600)),
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
            "id": "ledger_chain_integrity",
            "artifact": "performance/ledger_anchor_verification.json",
            "healthy_reachable_states": ["ok and verified"],
            "degraded_condition": "chain is absent, broken, or unverified",
            "max_consecutive_degraded_observations": 0,
            "incident_on_observation": 1,
            "observation_unit": "verification",
            "evaluation_policy": "explicit verified healthy allowlist",
        },
        {
            "id": "disaster_recovery_not_recoverable",
            "artifact": "performance/disaster_recovery_status.json",
            "healthy_reachable_states": ["ok", "not_due"],
            "degraded_condition": "archive/push/RPO is not recoverable",
            "max_consecutive_degraded_observations": 0,
            "incident_on_observation": 1,
            "observation_unit": "DR run",
            "evaluation_policy": "explicit healthy allowlists and compliant RPO",
        },
        {
            "id": "maker_study_run_failed",
            "artifact": "maker_carry/maker_carry_study.json",
            "healthy_reachable_states": ["ok", "no_candidates", "disabled"],
            "degraded_condition": "missing or failed status",
            "max_consecutive_degraded_observations": 0,
            "incident_on_observation": 1,
            "observation_unit": "study run",
            "evaluation_policy": "explicit healthy status allowlist",
        },
        {
            "id": "official_book_snapshot_partial",
            "artifact": "maker_carry/official_book_snapshot.json",
            "healthy_reachable_states": sorted(OFFICIAL_BOOK_HEALTHY_STATES),
            "degraded_condition": "persistent status outside the explicit healthy allowlist",
            "max_consecutive_degraded_observations": settings["official_book_snapshot_partial_max_consecutive_cycles"],
            "incident_on_observation": settings["official_book_snapshot_partial_max_consecutive_cycles"] + 1,
            "observation_unit": "snapshot cycle",
            "evaluation_policy": "distinct-cycle persistence",
        },
        {
            "id": "operating_state_slo_breach",
            "artifact": "performance/operating_state.json",
            "healthy_reachable_states": ["all registered SLO rows present and OK"],
            "degraded_condition": "breached, unknown, or omitted SLO",
            "max_consecutive_degraded_observations": 0,
            "incident_on_observation": 1,
            "observation_unit": "operating-state run",
            "evaluation_policy": "registered row completeness and explicit OK",
        },
        {
            "id": "publication_bridge_unhealthy",
            "artifact": "performance/*_push_status.json",
            "healthy_reachable_states": ["ok within fixed grace"],
            "degraded_condition": "missing, failed, or stale host publication",
            "max_consecutive_degraded_observations": 0,
            "incident_on_observation": 1,
            "observation_unit": "wall-clock observation",
            "evaluation_policy": "fixed two-hour grace; configuration cannot widen",
        },
        {
            "id": "requote_missing_inputs",
            "artifact": "maker_carry/requote_alerts.json",
            "healthy_reachable_states": [
                "quotes_ok",
                "requote_advised",
                "pull_quotes_now_or_STOP_with_risk_reason_only",
            ],
            "degraded_condition": "pull_quotes_now/STOP with a registered missing-input rule",
            "max_consecutive_degraded_observations": settings["requote_missing_input_max_consecutive_cycles"],
            "incident_on_observation": settings["requote_missing_input_max_consecutive_cycles"] + 1,
            "observation_unit": "producer cycle",
            "evaluation_policy": "registered missing-input predicate with legitimate risk-state exemption",
        },
        {
            "id": "maker_replay_insufficient_coverage",
            "artifact": "maker_carry/maker_fill_replay.json",
            "healthy_reachable_states": ["covered", "partial", "no_simulated_fill_opportunities"],
            "degraded_condition": "nonzero simulated fill opportunities with zero 5m replay coverage",
            "max_consecutive_degraded_observations": settings["maker_replay_insufficient_coverage_max_consecutive_cycles"],
            "incident_on_observation": settings["maker_replay_insufficient_coverage_max_consecutive_cycles"] + 1,
            "observation_unit": "distinct maker replay",
            "evaluation_policy": "registered zero-coverage predicate with no-opportunity exemption",
        },
        {
            "id": "scheduler_nonzero_exit",
            "artifact": "ops_scheduler/status.json",
            "healthy_reachable_states": ["last_exit_code=0"],
            "degraded_condition": "any job last_exit_code != 0",
            "max_consecutive_degraded_observations": settings["scheduler_nonzero_max_consecutive_cycles"],
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
            "max_consecutive_degraded_observations": settings["wallet_not_clean_max_consecutive_harvests"],
            "incident_on_observation": settings["wallet_not_clean_max_consecutive_harvests"] + 1,
            "observation_unit": "distinct harvest",
            "evaluation_policy": "healthy-status allowlist; unknown sibling states fail closed",
            "legacy_registration_id": LEGACY_WALLET_REGISTRATION_ID,
        },
        {
            "id": "operating_state_unknown_regression",
            "artifact": "performance/operating_state.json",
            "healthy_reachable_states": ["row state is known", "UNKNOWN before any known observation"],
            "degraded_condition": "previously known row becomes UNKNOWN",
            "max_consecutive_degraded_observations": settings["operating_unknown_max_consecutive_cycles"],
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


def _episode_anchor(
    state: dict[str, Any], registration_id: str, entity: str, token: str, degraded: bool
) -> str:
    """WO-144: stable episode-start anchor for (registration, entity).

    The anchor is the token of the FIRST degraded observation of the current
    contiguous episode, persisted in the watchdog state, so a persisting
    condition keeps ONE incident identity across cycles instead of minting a
    new id from each cycle's observation token (the 2026-08-01 notification
    storm). A healthy observation ends the episode and clears the anchor.
    Absent or malformed state falls back to the per-cycle token: noisy,
    never blind.
    """
    try:
        episodes = state.get("episode_anchors")
        if not isinstance(episodes, dict):
            episodes = {}
            state["episode_anchors"] = episodes
        key = f"{registration_id}|{entity}"
        if not degraded:
            episodes.pop(key, None)
            return str(token)
        anchor = str(episodes.get(key) or "") or str(token)
        episodes[key] = anchor
        return anchor
    except Exception:
        return str(token)


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
        try:
            # .get, not [..]: a job record with the field entirely ABSENT (a torn
            # status.json write - the exact OPS-6 case) must read as a failure,
            # not raise KeyError out of the watchdog.
            exit_code = int(job.get("last_exit_code"))
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
            reason=(f"scheduler job {job_name} has no successful completion within {maximum} seconds; measured age {round(age_seconds, 3)} seconds"),
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
                "maker replay has simulated fill opportunities but no covered 5m official-book window; a zero realism ratio must not be interpreted as evidence"
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
    previous_counter = _mapping(counters.get(WALLET_REGISTRATION_ID, counters.get(LEGACY_WALLET_REGISTRATION_ID)))
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
                or (f"wallet reconciliation status {status!r} is outside healthy allowlist " + ", ".join(sorted(WALLET_HEALTHY_STATES)))
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


def _immediate_producer_evaluations(
    cfg: EngineConfig, state: dict[str, Any], settings: Mapping[str, Any], generated_at: str
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Evaluate WO-121 producers with explicit, fail-closed allowlists."""
    del settings
    evaluations: list[dict[str, Any]] = []
    incidents: dict[str, dict[str, Any]] = {}
    for registration_id, relative, healthy in PRODUCER_REGISTRATIONS:
        payload = _mapping(read_json(cfg.output_root / relative, default={}) or {})
        token = _stamp(payload) or "unobserved"
        status = str(payload.get("status") or "unobserved").strip().lower()
        degraded = bool(payload) and status not in healthy
        reasons = [f"status={status}"]
        if registration_id == "ledger_chain_integrity":
            verified = payload.get("verified") is True or bool(payload.get("verified_through_date"))
            degraded = bool(payload) and (degraded or not verified)
            reasons.append(f"verified={verified}")
        elif registration_id == "disaster_recovery_not_recoverable":
            remote = str(payload.get("remote_push_status") or _mapping(payload.get("remote_push")).get("status") or "unobserved").lower()
            compliant = _mapping(payload.get("rpo")).get("compliant") is True
            generated = parse_timestamp(payload.get("generated_at_utc"))
            observed_at = parse_timestamp(generated_at)
            artifact_age = (
                (observed_at - generated).total_seconds()
                if generated is not None and observed_at is not None
                else None
            )
            stale = artifact_age is None or artifact_age < 0 or artifact_age > DR_STATUS_MAX_AGE_SECONDS
            degraded = degraded or not payload or remote not in {"ok", "pending"} or not compliant or stale
            reasons += [
                f"remote_push_status={remote}",
                f"rpo_compliant={compliant}",
                f"status_artifact_age_seconds={artifact_age}",
            ]
        evaluations.append(
            {
                "registration_id": registration_id,
                "artifact": relative,
                "observation_token": None if token == "unobserved" else token,
                "observed_state": status,
                "state": "incident" if degraded else "healthy",
                **(
                    {
                        "status_artifact_age_seconds": artifact_age,
                        "status_artifact_max_age_seconds": DR_STATUS_MAX_AGE_SECONDS,
                    }
                    if registration_id == "disaster_recovery_not_recoverable"
                    else {}
                ),
            }
        )
        anchor = _episode_anchor(state, registration_id, registration_id, token, degraded)
        if degraded:
            row = _incident(
                generated_at=generated_at,
                registration_id=registration_id,
                entity=registration_id,
                source_artifact=relative,
                observation_token=token,
                episode_start=anchor,
                degraded_state=status,
                reason="; ".join(reasons),
                count=1,
                maximum=0,
            )
            incidents[row["incident_id"]] = row

    relative = "maker_carry/official_book_snapshot.json"
    payload = _mapping(read_json(cfg.output_root / relative, default={}) or {})
    token = _stamp(payload)
    status = str(payload.get("status") or "unobserved").lower()
    degraded = not payload or status not in OFFICIAL_BOOK_HEALTHY_STATES
    counters = state.setdefault("counters", {})
    counter = _advance_counter(_mapping(counters.get("official_book_snapshot_partial")), token=token or generated_at, degraded=degraded)
    counters["official_book_snapshot_partial"] = counter
    maximum = REGISTERED_MAXIMA["official_book_snapshot_partial_max_consecutive_cycles"]
    count = int(counter["consecutive_degraded_observations"])
    if degraded and count > maximum:
        row = _incident(
            generated_at=generated_at,
            registration_id="official_book_snapshot_partial",
            entity="official_book_snapshot",
            source_artifact=relative,
            observation_token=token or "unobserved",
            episode_start=str(counter["episode_start_token"]),
            degraded_state=status,
            reason=f"official-book status {status} remained outside the healthy allowlist",
            count=count,
            maximum=maximum,
        )
        incidents[row["incident_id"]] = row
    evaluations.append(
        {
            "registration_id": "official_book_snapshot_partial",
            "artifact": relative,
            "observation_token": token or None,
            "observed_state": status,
            "consecutive_degraded_observations": count,
            "max_consecutive_degraded_observations": maximum,
            "state": "incident" if degraded and count > maximum else ("degraded_within_tolerance" if degraded else "healthy"),
        }
    )
    return evaluations, incidents


def _evaluate_slos_and_pushes(cfg: EngineConfig, state: dict[str, Any], generated_at: str) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    evaluations: list[dict[str, Any]] = []
    incidents: dict[str, dict[str, Any]] = {}
    relative = "performance/operating_state.json"
    payload = _mapping(read_json(cfg.output_root / relative, default={}) or {})
    token = _stamp(payload) or "unobserved"
    rows = {str(r.get("id")): r for r in _mapping(payload.get("slo")).get("rows", []) if isinstance(r, Mapping)}
    required = {
        "quote_sheet_age",
        "governance_refresh_duration",
        "scheduler_overrun_cycles",
        "websocket_gap",
        "dashboard_staleness",
        "reconciliation_age",
        "ledger_anchor_age",
    }
    # A missing operating-state artifact (or an empty SLO block) means every
    # required row is unproven. The registered fail-safe direction is "absent,
    # stale, or unparseable input is an incident, never health" - but a freshly
    # bootstrapped host has legitimately not produced operating state yet, so
    # absence gets the SAME fixed two-hour grace the publication bridges use
    # below (config cannot widen it), and then every required id reports as
    # failing. Non-empty rows are evaluated strictly with no grace: a torn or
    # partial block is missing evidence, not a bootstrap.
    slo_first_unobserved = state.setdefault("slo_first_unobserved", {})
    if rows:
        slo_first_unobserved.pop("operating_state", None)
        bad = sorted(
            identifier
            for identifier in required
            if identifier not in rows or rows[identifier].get("breach") is not False
        )
    else:
        now_for_slo = _parse_stamp(generated_at) or datetime.now(timezone.utc)
        first = str(slo_first_unobserved.get("operating_state") or generated_at)
        slo_first_unobserved["operating_state"] = first
        missing_age = max(0.0, (now_for_slo - (_parse_stamp(first) or now_for_slo)).total_seconds())
        bad = sorted(required) if missing_age > PUSH_STATUS_MAX_SECONDS else []
    anchor = _episode_anchor(state, "operating_state_slo_breach", "operating_state_slo", token, bool(bad))
    if bad:
        row = _incident(
            generated_at=generated_at,
            registration_id="operating_state_slo_breach",
            entity="operating_state_slo",
            source_artifact=relative,
            observation_token=token,
            episode_start=anchor,
            degraded_state="breach_or_missing",
            reason="SLO rows breached, unknown, or missing: " + ", ".join(bad),
            count=1,
            maximum=0,
        )
        incidents[row["incident_id"]] = row
    evaluations.append(
        {
            "registration_id": "operating_state_slo_breach",
            "artifact": relative,
            "observation_token": None if token == "unobserved" else token,
            "state": "incident" if bad else "healthy",
            "failed_rows": bad,
        }
    )

    now = _parse_stamp(generated_at) or datetime.now(timezone.utc)
    first_unobserved = state.setdefault("publication_bridge_first_unobserved", {})
    bridge_rows = []
    for name in ("anchor", "telemetry"):
        rel = f"performance/{name}_push_status.json"
        status_payload = _mapping(read_json(cfg.output_root / rel, default={}) or {})
        stamp = _stamp(status_payload)
        parsed = _parse_stamp(stamp)
        age = (now - parsed).total_seconds() if parsed else None
        status = str(status_payload.get("status") or "unobserved").lower()
        if age is None and not payload:
            # The host-bridge grace clock begins only once operating-state
            # evidence proves this installation is active.
            bad_bridge = False
        elif age is None:
            first = str(first_unobserved.get(name) or generated_at)
            first_unobserved[name] = first
            missing_age = max(0.0, (now - (_parse_stamp(first) or now)).total_seconds())
            bad_bridge = missing_age > PUSH_STATUS_MAX_SECONDS
        else:
            first_unobserved.pop(name, None)
            bad_bridge = age > PUSH_STATUS_MAX_SECONDS or status != "ok"
        anchor = _episode_anchor(state, "publication_bridge_unhealthy", name, stamp or "unobserved", bad_bridge)
        bridge_rows.append(
            {"bridge": name, "status": status, "observation_token": stamp or None, "age_seconds": age, "state": "incident" if bad_bridge else "healthy"}
        )
        if bad_bridge:
            observation = stamp or "unobserved"
            row = _incident(
                generated_at=generated_at,
                registration_id="publication_bridge_unhealthy",
                entity=name,
                source_artifact=rel,
                observation_token=observation,
                episode_start=anchor,
                degraded_state=status,
                reason=f"{name} publication status is missing, failed, or older than {PUSH_STATUS_MAX_SECONDS} seconds",
                count=1,
                maximum=0,
            )
            incidents[row["incident_id"]] = row
    evaluations.append(
        {
            "registration_id": "publication_bridge_unhealthy",
            "artifact": "performance/*_push_status.json",
            "state": "incident" if any(r["state"] == "incident" for r in bridge_rows) else "healthy",
            "bridges": bridge_rows,
        }
    )
    return evaluations, incidents


def _append_incidents(path: Path, incidents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not incidents:
        return []
    existing_ids = {row.get("incident_id", "") for row in read_csv_rows(path)}
    new_rows = [row for row in incidents if str(row.get("incident_id") or "") not in existing_ids]
    if not new_rows:
        return []
    append_csv_rows(path, new_rows, fieldnames=INCIDENT_FIELDS)
    return new_rows


def _notification(
    cfg: EngineConfig,
    *,
    generated_at: str,
    enabled: bool,
    cooldown_seconds: float,
    active: list[dict[str, Any]],
    new: list[dict[str, Any]],
    undelivered_ids: list[str],
    undelivered_registrations: list[str],
    state_path: Path,
    state: dict[str, Any],
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
        lines.append(f"- **{row.get('registration_id')} / {row.get('entity')}**: {row.get('reason')} (source `{row.get('source_artifact')}`)")
    if not active:
        lines.append("- No active semantic-health incidents.")
    lines += [
        "",
        "Human review only. Fail-closed and risk states remain unchanged; this watchdog cannot trade or cancel orders.",
    ]
    write_text_atomic(body_path, "\n".join(lines) + "\n")
    active_by_id = {str(row.get("incident_id") or ""): row for row in active}
    # WO-144: even distinct episodes of the same entity cannot page more often
    # than the cooldown. Retries of already-authorized (undelivered) pushes
    # bypass the cooldown; the incident artifact and ledger record every event
    # regardless - only the push transport is rate-bounded, and suppression is
    # itself recorded in the returned block.
    notified = state.get("notified_entities")
    if not isinstance(notified, dict):
        notified = {}
        state["notified_entities"] = notified
    now_dt = _parse_stamp(generated_at) or datetime.now(timezone.utc)
    pushable_new: list[dict[str, Any]] = []
    suppressed_entities: list[str] = []
    next_eligible: list[str] = []
    for row in new:
        key = f"{row.get('registration_id')}|{row.get('entity')}"
        last = _parse_stamp(str(notified.get(key) or ""))
        if last is not None and (now_dt - last).total_seconds() < float(cooldown_seconds):
            suppressed_entities.append(key)
            next_eligible.append((last + timedelta(seconds=float(cooldown_seconds))).strftime("%Y-%m-%dT%H:%M:%SZ"))
            continue
        pushable_new.append(row)
    pending = list(dict.fromkeys([*undelivered_ids, *[str(row["incident_id"]) for row in pushable_new]]))[-MAX_NOTIFICATION_IDS:]
    url = str(os.environ.get(NTFY_ENV_VAR) or "").strip()
    attempted = bool(enabled and url and pending)
    delivery: dict[str, Any] = {
        "attempted": attempted,
        "delivered": False,
        "channel_configured": bool(url),
        "error": "",
    }
    # Registration ids are persisted alongside delivery debt because an
    # incident may recover before its retry succeeds.  A retry must retain the
    # original bounded registration metadata rather than degrading to the
    # unhelpful (and lossy) ``unknown`` label.
    registrations = sorted(
        {
            *[str(item) for item in undelivered_registrations if str(item)],
            *[
                str(active_by_id.get(item, {}).get("registration_id") or "unknown")
                for item in pending
                if item not in undelivered_ids
            ],
        }
    )[:MAX_NOTIFICATION_IDS]
    if attempted:
        # Crash safety: the bounded debt and the registration metadata needed
        # to retry it are durable before the network side effect begins.
        state["undelivered_incident_ids"] = pending
        state["undelivered_incident_registrations"] = registrations
        write_json(state_path, state)
        message = "Polymarket watchdog incidents: " + ", ".join(registrations)
        try:
            response = requests.post(url, data=message, timeout=10)
            delivery["delivered"] = response.status_code < 300
            if not delivery["delivered"]:
                delivery["error"] = f"http_status_{response.status_code}"
        except Exception as exc:  # noqa: BLE001 - alert failure must not block watchdog evidence
            delivery["error"] = type(exc).__name__
    if attempted:
        remaining = pending if not delivery["delivered"] else []
        remaining_registrations = registrations if remaining else []
    else:
        # A temporarily missing/disabled channel must not erase durable debt
        # from an earlier failed attempt. Conversely, new incidents do not
        # become debt until a configured channel actually attempts delivery.
        remaining = list(dict.fromkeys(undelivered_ids))[-MAX_NOTIFICATION_IDS:]
        remaining_registrations = list(dict.fromkeys(str(item) for item in undelivered_registrations))[-MAX_NOTIFICATION_IDS:]
    if attempted and delivery["delivered"]:
        for row in pushable_new:
            notified[f"{row.get('registration_id')}|{row.get('entity')}"] = generated_at
        horizon = max(float(cooldown_seconds), 86400.0)
        state["notified_entities"] = {
            key: stamp
            for key, stamp in notified.items()
            if _parse_stamp(str(stamp)) is not None
            and (now_dt - _parse_stamp(str(stamp))).total_seconds() <= horizon
        }
        state["undelivered_incident_ids"] = []
        state["undelivered_incident_registrations"] = []
        write_json(state_path, state)
    # ``notify`` remains the state-change/retry signal consumed by existing
    # artifact readers; ``delivery.attempted`` records whether a real channel
    # was configured and a transport call actually occurred.
    notify = bool(enabled and pending)
    return {
        "enabled": enabled,
        "eligible": bool(new),
        "notify": notify,
        "state_changed": bool(new),
        "pushes_suppressed_by_cooldown": len(suppressed_entities),
        "suppressed_entities": suppressed_entities,
        "next_eligible_push_utc": min(next_eligible) if next_eligible else "",
        "cooldown_seconds": float(cooldown_seconds),
        "subject": f"Polymarket degraded-state incidents: {len(active)} active",
        "body_file": str(body_path),
        "pattern": "superbru_score_change_state_digest",
        "delivery": delivery,
        "undelivered_incident_ids": remaining,
        "undelivered_incident_registrations": remaining_registrations,
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
            previous = _mapping(read_json(output_path, default={}) or {})
            state = _mapping(read_json(state_path, default={}) or {})
            carry_cycles = _nonnegative_int(state.get("carry_forward_cycles"), 0) + 1
            carry_started = str(state.get("carry_forward_started_at") or generated_at)
            active = [dict(row) for row in previous.get("active_incidents", []) if isinstance(row, Mapping)]
            if carry_cycles > 3:
                wedge = _incident(
                    generated_at=generated_at,
                    registration_id="degraded_state_watchdog_wedged",
                    entity="degraded_state_watchdog",
                    source_artifact=OUTPUT_FILE,
                    observation_token=generated_at,
                    episode_start=carry_started,
                    degraded_state="lock_held",
                    reason="watchdog evaluation carried forward for more than 3 consecutive cycles",
                    count=carry_cycles,
                    maximum=3,
                )
                active = [row for row in active if row.get("registration_id") != "degraded_state_watchdog_wedged"] + [wedge]
            new = _append_incidents(ledger_path, active)
            # Minimize the race with the actual lock holder before the one
            # unavoidable pre-send debt write.
            state = _mapping(read_json(state_path, default={}) or {})
            notification = _notification(
                cfg,
                generated_at=generated_at,
                enabled=bool(settings["notification_enabled"]),
                cooldown_seconds=float(settings["notification_cooldown_seconds"]),
                active=active,
                new=new,
                undelivered_ids=list(state.get("undelivered_incident_ids") or []),
                undelivered_registrations=list(state.get("undelivered_incident_registrations") or []),
                state_path=state_path,
                state=state,
            )
            # The lock holder may have published fresh evaluator state while
            # this skipped cycle notified. Re-read immediately before writing
            # and merge only the carry/notification keys this path owns.
            latest_state = _mapping(read_json(state_path, default={}) or {})
            state = latest_state
            state.update({
                "carry_forward_cycles": carry_cycles,
                "carry_forward_started_at": carry_started,
                "undelivered_incident_ids": notification["undelivered_incident_ids"],
                "undelivered_incident_registrations": notification["undelivered_incident_registrations"],
            })
            write_json(state_path, state)
            payload = {
                **base,
                "status": "skipped_lock_held",
                "registrations": _registrations(settings),
                "evaluations": previous.get("evaluations", []),
                "active_incidents": active,
                "new_incidents": new,
                "active_incident_count": len(active),
                "new_incident_count": len(new),
                "carry_forward_cycles": carry_cycles,
                "carried_forward_from_utc": previous.get("carried_forward_from_utc")
                or previous.get("generated_at_utc"),
                "carry_forward_reason": "runtime_lock_held",
                "lock": lock.as_dict(),
                "notification": notification,
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
        ):
            evaluation, incidents = evaluator(cfg, state, settings, generated_at)
            evaluations.append(evaluation)
            active_by_id.update(incidents)

        producer_evaluations, producer_incidents = _immediate_producer_evaluations(cfg, state, settings, generated_at)
        evaluations.extend(producer_evaluations)
        active_by_id.update(producer_incidents)
        slo_evaluations, slo_incidents = _evaluate_slos_and_pushes(cfg, state, generated_at)
        evaluations.extend(slo_evaluations)
        active_by_id.update(slo_incidents)

        active = sorted(active_by_id.values(), key=lambda row: (row["registration_id"], row["entity"]))
        new = _append_incidents(ledger_path, active)
        notification = _notification(
            cfg,
            generated_at=generated_at,
            enabled=bool(settings["notification_enabled"]),
            cooldown_seconds=float(settings["notification_cooldown_seconds"]),
            active=active,
            new=new,
            undelivered_ids=list(state.get("undelivered_incident_ids") or []),
            undelivered_registrations=list(state.get("undelivered_incident_registrations") or []),
            state_path=state_path,
            state=state,
        )
        state.update(
            {
                "generated_at_utc": generated_at,
                "active_incident_ids": [row["incident_id"] for row in active],
                "undelivered_incident_ids": notification["undelivered_incident_ids"],
                "undelivered_incident_registrations": notification["undelivered_incident_registrations"],
                "carry_forward_cycles": 0,
                "carry_forward_started_at": None,
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
