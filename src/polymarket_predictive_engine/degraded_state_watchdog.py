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
# WO-152: write-only attribution sidecar. Strictly additive to the main ledger
# above - it never gates, suppresses, delays, or de-duplicates an incident.
ATTRIBUTION_LEDGER = "performance/scheduler_attribution_v1.csv"
NOTIFICATION_BODY = "ops_scheduler/degraded_state_notification.md"
WALLET_REGISTRATION_ID = "wallet_reconciliation_not_clean"
LEGACY_WALLET_REGISTRATION_ID = "wallet_reconciliation_partial"
WALLET_HEALTHY_STATES = frozenset({"clean", "explained"})
OFFICIAL_BOOK_HEALTHY_STATES = frozenset({"ok", "disabled", "no_portfolio"})
DR_STATUS_MAX_AGE_SECONDS = 6 * 60 * 60
NTFY_ENV_VAR = "OPS_OWNER_NTFY_TOPIC_URL"
MAX_NOTIFICATION_IDS = 20
# WO-144 (F8): the per-entity cooldown stamp map is bounded independently of
# the notification debt lists above - it grows by one entry per distinct
# (registration_id, entity) that has ever been pushed, not per incident.
MAX_NOTIFIED_ENTITIES = 64

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
    # WO-149: two consecutive missed pulses tolerated at the measured
    # 1.16-1.18x scheduler drag (2 x ~354s = ~708s) with ~192s headroom; the
    # third is an incident. Looser (as a fraction of its own 300s interval)
    # than trade_prints' 20/15 ratio because the measured drag is a larger
    # fraction of a 300s interval than of a 900s one.
    "book_pulse": 15 * 60,
    "executor_ops_monitor": 15 * 60,
    "degraded_state_watchdog": 15 * 60,
    "ledger_anchor": 26 * 60 * 60,
    "maker_safety_refresh": 60 * 60,
    # WO-143: the scheduled scoring-only paper cycle's 4h default cadence,
    # +1h at the house ratio (6h->7h, 8h->9h, 12h->13h, 24h->25h).
    "paper_cycle": 5 * 60 * 60,
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

# WO-152: the attribution sidecar's header, registered explicitly rather than
# left implicit. append_csv_rows compares the on-disk header against these
# fieldnames and raises ValueError on a mismatch, so this list is the schema.
# occupancy_error earns its slot: INCIDENT_FIELDS is frozen at 14 and
# csv.DictWriter(..., extrasaction="ignore") silently drops anything outside
# the requested fieldnames, so without this entry a contained attribution
# failure would leave no append-only trace at all.
SCHEDULER_ATTRIBUTION_FIELDS = [
    "incident_id",
    "detected_at_utc",
    "entity",
    "observation_token",
    "attribution_state",
    "attributed_to",
    "drag_budget_seconds",
    "occupants",
    "occupancy_unmeasurable",
    "occupancy_error",
]

# WO-152: REGISTERED_JOB_FRESHNESS_MAX_SECONDS' 11 keys minus the 3 safety-lane
# entities. Safety-lane starvation carries no attribution keys and produces no
# sidecar row; the `job_name in MARKED_JOBS` call-site guard is load-bearing,
# because the two tables below are keyed on these members and raise KeyError
# for anything else.
MARKED_JOBS = frozenset(
    {
        "governance_refresh",
        "clv_snapshot",
        "locked_card_refresh",
        "training_harvest",
        "maker_study_intraday",
        "trade_prints",
        "book_pulse",
        "ledger_anchor",
        "paper_cycle",
    }
)
UNBOUNDED_MARKED_JOBS = frozenset({"maker_study_intraday", "locked_card_refresh", "ledger_anchor"})
# Runtime bounds: worst-case in-flight duration per job, derived from that
# job's own timeout-wrapped children (env DEFAULTS, safe in one direction only
# because the relevant knobs are clamped downward-only).
IN_FLIGHT_STALE_AFTER_SECONDS = {
    "governance_refresh": 4200,
    "clv_snapshot": 5400,
    "training_harvest": 27600,
    "trade_prints": 3000,
    "book_pulse": 480,
    # 2x PAPER_CYCLE_TIMEOUT, which run_vps_ops_scheduler.sh:105-114 clamps
    # two-sided to a 1800s MAXIMUM, so the default is also the worst case. The
    # 2x follows book_pulse, the closest precedent: a job wrapping ONE timed
    # child gets double it, leaving room for interpreter start and teardown.
    "paper_cycle": 3600,
}
# An ORPHAN bound for the 3 jobs above with no scheduler-enforced due-cadence to
# overrun WHILE RUNNING. Each value is that job's OWN existing
# REGISTERED_JOB_FRESHNESS_MAX_SECONDS ceiling (:59, :61, :71) - READ here,
# never changed. The bound's purpose is orphan DETECTION (a run that has
# outlived its own freshness ceiling has already raised its own incident, so
# treating its marker as still-live evidence past that point is never
# justified), not runtime modelling.
ORPHAN_BOUND_SECONDS = {
    "locked_card_refresh": 46800,
    "maker_study_intraday": 90000,
    "ledger_anchor": 93600,
}
# ceiling - interval, per job.
DRAG_BUDGET_SECONDS = {
    "governance_refresh": 3600,
    "clv_snapshot": 3600,
    "locked_card_refresh": 3600,
    "training_harvest": 3600,
    "maker_study_intraday": 3600,
    "trade_prints": 300,
    "book_pulse": 600,
    "ledger_anchor": 50400,
    # ceiling - interval, as for every entry here: 5h - 4h.
    "paper_cycle": 3600,
}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _boolish(value: Any, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _nonnegative_int(value: Any, default: int) -> int:
    try:
        candidate = int(value)
    # WO-144 (F6): `.inf`/overflow YAML values (e.g. a hand-edited
    # `notification_cooldown_seconds: .inf`) raise OverflowError, not
    # ValueError, when coerced with int(). The watchdog dying in _settings is
    # the most fail-open outcome there is, so this falls back to the
    # registered default exactly like a non-numeric value does.
    except (TypeError, ValueError, OverflowError):
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
) -> tuple[str, int]:
    """WO-144: stable episode-start anchor for (registration, entity).

    The anchor is the token of the FIRST degraded observation of the current
    contiguous episode, persisted in the watchdog state, so a persisting
    condition keeps ONE incident identity across cycles instead of minting a
    new id from each cycle's observation token (the 2026-08-01 notification
    storm). A healthy observation ends the episode and clears the anchor.
    Absent or malformed state falls back to the per-cycle token: noisy,
    never blind.

    WO-144 amendment (F2): also returns the episode's observation depth, a
    count persisted alongside the anchor and keyed the same way, so callers
    that anchor identity here (unlike the `_advance_counter` evaluators)
    report a real `consecutive_degraded_observations` instead of a hardcoded
    1. It increments exactly once per call because each anchoring call site
    runs at most once per acquired watchdog cycle - the lock-held carry
    path replays the prior cycle's `active_incidents` wholesale rather than
    re-invoking the evaluators, so a wedged cycle can never double-count.
    """
    try:
        episodes = state.get("episode_anchors")
        if not isinstance(episodes, dict):
            episodes = {}
            state["episode_anchors"] = episodes
        counts = state.get("episode_observation_counts")
        if not isinstance(counts, dict):
            counts = {}
            state["episode_observation_counts"] = counts
        key = f"{registration_id}|{entity}"
        if not degraded:
            episodes.pop(key, None)
            counts.pop(key, None)
            return str(token), 0
        anchor = str(episodes.get(key) or "") or str(token)
        episodes[key] = anchor
        count = _nonnegative_int(counts.get(key), 0) + 1
        counts[key] = count
        return anchor, count
    except Exception:
        return str(token), 1


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


def _attribute_starvation(
    job_name: str,
    jobs: Mapping[str, Any],
    window_start: datetime,
    window_end: datetime,
) -> dict[str, Any]:
    """Name the job that held the serial ops loop across a victim's stale window.

    WO-152. Strictly additive evidence: the caller has already decided the
    incident. Four outcomes only - see the registered attribution states.
    """

    # The threshold is the VICTIM's drag budget, keyed on job_name - never the
    # holder's. This is the WO's single registered definition of the candidate
    # predicate.
    drag_budget = DRAG_BUDGET_SECONDS[job_name]
    occupants: list[dict[str, Any]] = []
    unmeasurable: list[str] = []
    candidates: list[str] = []
    for other in sorted(MARKED_JOBS - {job_name}):
        marker_raw = str(_mapping(jobs.get(other)).get("in_flight_since_utc") or "").strip()
        if not marker_raw:
            continue
        marker_dt = _parse_stamp(marker_raw)
        if marker_dt is None:
            unmeasurable.append(other)
            continue
        # An explicit future-dated branch BEFORE the clamp. A marker after
        # window_end is evidence of a clock or write defect, not evidence about
        # occupancy - it must fold into occupancy_unmeasurable, not silently
        # vanish via max(0.0, ...) and read as "evidence complete".
        if marker_dt > window_end:
            unmeasurable.append(other)
            continue
        # EVERY marked job has a bound to survive past: bounded jobs from
        # IN_FLIGHT_STALE_AFTER_SECONDS, the 3 previously-unbounded jobs from
        # their own ORPHAN_BOUND_SECONDS ceiling.
        bound = IN_FLIGHT_STALE_AFTER_SECONDS.get(other, ORPHAN_BOUND_SECONDS.get(other))
        marker_age = (window_end - marker_dt).total_seconds()
        if marker_age > bound:
            unmeasurable.append(other)
            continue
        overlap = max(0.0, (window_end - max(marker_dt, window_start)).total_seconds())
        source = "in_flight_unbounded" if other in UNBOUNDED_MARKED_JOBS else "in_flight"
        if overlap >= 2.0:
            occupants.append({"job": other, "overlap_seconds": round(overlap, 3), "source": source})
        if overlap >= drag_budget:
            candidates.append(other)
    occupants.sort(key=lambda row: (-row["overlap_seconds"], row["job"]))
    unmeasurable = sorted(unmeasurable)
    if unmeasurable:
        state, attributed_to = "holder_unmeasurable", ""
    elif len(candidates) == 1:
        state, attributed_to = "attributed", candidates[0]
    elif not candidates:
        state, attributed_to = "insufficient_to_explain", ""
    else:
        state, attributed_to = "multiple_candidates", ""
    return {
        "attribution_state": state,
        "attributed_to": attributed_to,
        "drag_budget_seconds": drag_budget,
        "occupants": occupants,
        "occupancy_unmeasurable": unmeasurable,
    }


def _starved_job_self_state(
    job_name: str,
    jobs: Mapping[str, Any],
    previous: Mapping[str, Any],
) -> tuple[str, int | None]:
    """Is the starved job itself showing crash evidence? WO-152.

    Missing or malformed failed_cycles_total folds into crash_evident via
    advanced = True - matching last_exit_code's existing fail-alarming
    treatment, not a fail-silent default.
    """

    record = _mapping(jobs.get(job_name))
    try:
        exit_code = int(record.get("last_exit_code", 1))
    except (TypeError, ValueError):
        exit_code = 1
    raw_failed = record.get("failed_cycles_total")
    try:
        current_failed = int(raw_failed) if raw_failed is not None else None
    except (TypeError, ValueError):
        current_failed = None
    if current_failed is None:
        advanced = True
    else:
        try:
            previous_failed = int(previous.get("failed_cycles_total_observed", current_failed))
        except (TypeError, ValueError):
            previous_failed = current_failed
        advanced = current_failed > previous_failed
    return ("crash_evident" if (exit_code != 0 or advanced) else "no_self_failure_evidence"), current_failed


def _append_attribution(path: Path, incidents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Append WO-152 attribution rows for the freshness incidents in `new`.

    Both registered predicates apply. The caller passes the SAME `new` list
    _append_incidents returned - that is the dedup rule (once per episode,
    ever). This function applies the row-SELECTION rule itself, so neither call
    site has to: `new` is type-mixed by construction, and without the
    registration filter every incident type in the cycle would land here with
    every attribution column blank.
    """

    rows = [row for row in incidents if str(row.get("registration_id") or "") == "scheduler_completion_freshness"]
    if not rows:
        return []
    append_csv_rows(path, rows, fieldnames=SCHEDULER_ATTRIBUTION_FIELDS)
    return rows


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
            # WO-152: the attribution window opens at the victim's own
            # observation_token and closes at the watchdog's observed_at.
            window_start = success_at
        else:
            if not first_unobserved:
                first_unobserved = generated_at
            first_at = _parse_stamp(first_unobserved) or observed_at
            age_seconds = max(0.0, (observed_at - first_at).total_seconds())
            observation_token = first_unobserved
            observed_state = "unobserved" if age_seconds <= maximum else "stale_unobserved"
            window_start = first_at

        stale = age_seconds > maximum
        # WO-152: persisted for every registered entity, every cycle - stale,
        # fresh, marked or not - using the same int-or-None coercion
        # _starved_job_self_state's current_failed uses, and decoupled from
        # whether that enum is computed this cycle.
        raw_failed = job.get("failed_cycles_total")
        try:
            failed_cycles_observed = int(raw_failed) if raw_failed is not None else None
        except (TypeError, ValueError):
            failed_cycles_observed = None
        next_jobs[job_name] = {
            "last_success_utc": last_success,
            "first_unobserved_at_utc": first_unobserved,
            "age_seconds": round(age_seconds, 3),
            "maximum_age_seconds": maximum,
            "currently_stale": stale,
            "failed_cycles_total_observed": failed_cycles_observed,
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
        # WO-152: strictly additive. The stale decision, age arithmetic,
        # maximum comparison, incident id and episode anchor are all computed
        # above and are byte-identical to their pre-WO values. If attribution
        # raises for any reason the incident is STILL emitted, with
        # attributed_to null and occupancy_error carrying the exception class
        # name - a watchdog that cannot explain an incident must still raise
        # it. Safety-lane entities are excluded by this guard: the constant
        # tables are keyed on MARKED_JOBS and raise KeyError for anything else.
        attribution: dict[str, Any] = {}
        if job_name in MARKED_JOBS:
            try:
                attribution = _attribute_starvation(job_name, jobs, window_start, observed_at)
            except Exception as exc:  # noqa: BLE001 - containment is the point
                attribution = {"attributed_to": None, "occupancy_error": type(exc).__name__}
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
        # INCIDENT_FIELDS stays frozen at 14, so these additive keys ride the
        # row into degraded_state.json and the WO-152 sidecar and are dropped
        # from the main ledger CSV by DictWriter's extrasaction="ignore".
        row.update(attribution)
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
        anchor, count = _episode_anchor(state, registration_id, registration_id, token, degraded)
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
                count=count,
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
    anchor, count = _episode_anchor(state, "operating_state_slo_breach", "operating_state_slo", token, bool(bad))
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
            count=count,
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
        anchor, count = _episode_anchor(state, "publication_bridge_unhealthy", name, stamp or "unobserved", bad_bridge)
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
                count=count,
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


def _prune_notified_entities(
    notified: Mapping[str, Any], now_dt: datetime, horizon_seconds: float
) -> dict[str, str]:
    """WO-144 (F1/F8): drop expired and future-dated cooldown stamps, then
    keep only the newest MAX_NOTIFIED_ENTITIES entries.

    F1: a stamp with NEGATIVE elapsed time (the recorded push is dated in the
    future - a clock artifact) is dropped here rather than kept "cooling
    forever"; combined with the cooldown check reading a since-pruned key as
    never-notified, a future-dated stamp can never extend suppression beyond
    the registered floor.

    F8: this runs on every `_notification` call, not only when a push is
    actually delivered, so a channel that is down (or a burst of distinct
    entities) cannot grow this map unboundedly between deliveries.
    """
    parsed: list[tuple[str, str, datetime]] = []
    for key, stamp in notified.items():
        when = _parse_stamp(str(stamp))
        if when is None:
            continue
        elapsed = (now_dt - when).total_seconds()
        if elapsed < 0 or elapsed > horizon_seconds:
            continue
        parsed.append((key, str(stamp), when))
    parsed.sort(key=lambda item: item[2], reverse=True)
    return {key: stamp for key, stamp, _ in parsed[:MAX_NOTIFIED_ENTITIES]}


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
    undelivered_entities: list[str],
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
    now_dt = _parse_stamp(generated_at) or datetime.now(timezone.utc)
    horizon = max(float(cooldown_seconds), 86400.0)
    # F1/F8: prune on every call (not only a delivered push) so an unreachable
    # channel cannot let the map grow unboundedly, and so a future-dated
    # (clock-artifact) stamp is dropped before it can be read as "still
    # cooling" by the check below.
    notified = _prune_notified_entities(notified, now_dt, horizon)
    state["notified_entities"] = notified
    pushable_new: list[dict[str, Any]] = []
    suppressed_entities: list[str] = []
    next_eligible: list[str] = []
    for row in new:
        key = f"{row.get('registration_id')}|{row.get('entity')}"
        last = _parse_stamp(str(notified.get(key) or ""))
        # F1: a stamp already pruned above (including a dropped future-dated
        # one) reads as `last is None`, i.e. never-notified/expired-now -
        # never as "still cooling forever".
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
    # F3: an episode whose first push succeeds only on retry must still arm
    # the cooldown. By the retry cycle the originating incident is no longer
    # in ``new`` (it is already in the ledger), so its entity key would
    # otherwise be unrecoverable once dropped from ``active`` - persist it
    # here, parallel to (and bounded the same as) the ids/registrations debt
    # above, written in the same pre-send durable write.
    entities = sorted(
        {
            *[str(item) for item in undelivered_entities if str(item)],
            *[
                f"{active_by_id[item].get('registration_id')}|{active_by_id[item].get('entity')}"
                for item in pending
                if item not in undelivered_ids and item in active_by_id
            ],
        }
    )[:MAX_NOTIFICATION_IDS]
    if attempted:
        # Crash safety: the bounded debt and the registration/entity metadata
        # needed to retry it are durable before the network side effect
        # begins.
        state["undelivered_incident_ids"] = pending
        state["undelivered_incident_registrations"] = registrations
        state["undelivered_incident_entities"] = entities
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
        remaining_entities = entities if remaining else []
    else:
        # A temporarily missing/disabled channel must not erase durable debt
        # from an earlier failed attempt. Conversely, new incidents do not
        # become debt until a configured channel actually attempts delivery.
        remaining = list(dict.fromkeys(undelivered_ids))[-MAX_NOTIFICATION_IDS:]
        remaining_registrations = list(dict.fromkeys(str(item) for item in undelivered_registrations))[-MAX_NOTIFICATION_IDS:]
        remaining_entities = list(dict.fromkeys(str(item) for item in undelivered_entities))[-MAX_NOTIFICATION_IDS:]
    if attempted and delivery["delivered"]:
        # Stamp this cycle's freshly pushed entities AND any entity whose
        # debt this delivery just cleared (F3) - the latter may no longer be
        # in ``pushable_new`` (or even ``active``) by the time delivery
        # finally succeeds.
        for row in pushable_new:
            notified[f"{row.get('registration_id')}|{row.get('entity')}"] = generated_at
        for key in entities:
            notified[key] = generated_at
        notified = _prune_notified_entities(notified, now_dt, horizon)
        state["notified_entities"] = notified
        state["undelivered_incident_ids"] = []
        state["undelivered_incident_registrations"] = []
        state["undelivered_incident_entities"] = []
        write_json(state_path, state)
    # WO-144 (F5, amended): ``notify`` means "a push will be attempted this
    # cycle" - an incident suppressed by the per-entity cooldown does not
    # count, so a cycle whose only new incident is suppressed reports
    # ``notify=False`` even though ``eligible``/``state_changed`` (both
    # bool(new)) stay True. See docs/POLYMARKET_CODEX_WORK_ORDERS.md WO-144
    # amendment (2026-08-01).
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
        "undelivered_incident_entities": remaining_entities,
        "notified_entities": notified,
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
    sidecar_path = cfg.output_root / ATTRIBUTION_LEDGER
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
            try:
                _append_attribution(sidecar_path, new)
            except Exception:  # noqa: BLE001 - containment is the point
                pass  # the sidecar is additive evidence; its failure must never
                # suppress the owner notification for incidents already
                # appended to the main ledger by the call above.
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
                undelivered_entities=list(state.get("undelivered_incident_entities") or []),
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
                "undelivered_incident_entities": notification["undelivered_incident_entities"],
                "notified_entities": notification["notified_entities"],
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
        try:
            _append_attribution(sidecar_path, new)
        except Exception:  # noqa: BLE001 - containment is the point
            pass  # the sidecar is additive evidence; its failure must never
            # suppress the owner notification for incidents already appended
            # to the main ledger by the call above.
        notification = _notification(
            cfg,
            generated_at=generated_at,
            enabled=bool(settings["notification_enabled"]),
            cooldown_seconds=float(settings["notification_cooldown_seconds"]),
            active=active,
            new=new,
            undelivered_ids=list(state.get("undelivered_incident_ids") or []),
            undelivered_registrations=list(state.get("undelivered_incident_registrations") or []),
            undelivered_entities=list(state.get("undelivered_incident_entities") or []),
            state_path=state_path,
            state=state,
        )
        state.update(
            {
                "generated_at_utc": generated_at,
                "active_incident_ids": [row["incident_id"] for row in active],
                "undelivered_incident_ids": notification["undelivered_incident_ids"],
                "undelivered_incident_registrations": notification["undelivered_incident_registrations"],
                "undelivered_incident_entities": notification["undelivered_incident_entities"],
                "notified_entities": notification["notified_entities"],
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
