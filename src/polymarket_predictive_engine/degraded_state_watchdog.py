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

from .config import EngineConfig, load_config
from .runtime_lock import runtime_lock
from .utils import ensure_dir, now_utc, read_csv_rows, read_json, serialize_value, write_json


WORK_ORDER = "WO-78"
OUTPUT_FILE = "ops_scheduler/degraded_state_watchdog.json"
STATE_FILE = "ops_scheduler/degraded_state_watchdog_state.json"
INCIDENT_LEDGER = "performance/degraded_state_incidents.csv"
NOTIFICATION_BODY = "ops_scheduler/degraded_state_notification.md"

# A maximum is the number of consecutive degraded observations tolerated.
# Therefore max=3 trips on observation four, exactly matching "> 3 cycles".
REGISTERED_MAXIMA: dict[str, int] = {
    "requote_missing_input_max_consecutive_cycles": 3,
    "scheduler_nonzero_max_consecutive_cycles": 0,
    "wallet_partial_max_consecutive_harvests": 2,
    "operating_unknown_max_consecutive_cycles": 0,
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
        settings[key] = min(registered, _nonnegative_int(raw.get(key), registered))
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
        },
        {
            "id": "wallet_reconciliation_partial",
            "artifact": "performance/wallet_reconciliation.json",
            "healthy_reachable_states": ["clean", "explained"],
            "degraded_condition": "reconciliation_status=partial",
            "max_consecutive_degraded_observations": settings[
                "wallet_partial_max_consecutive_harvests"
            ],
            "incident_on_observation": settings["wallet_partial_max_consecutive_harvests"] + 1,
            "observation_unit": "distinct harvest",
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
    degraded = alert_state in {"pull_quotes_now", "STOP"} and bool(missing_rules)
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
            reason="persistent missing-input rules: " + ", ".join(missing_rules),
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
            exit_code = int(job.get("last_exit_code", 0))
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
    degraded = status.lower() == "partial"
    counters = state.setdefault("counters", {})
    counter = _advance_counter(
        _mapping(counters.get("wallet_reconciliation_partial")),
        token=token,
        degraded=degraded,
    )
    counters["wallet_reconciliation_partial"] = counter
    maximum = int(settings["wallet_partial_max_consecutive_harvests"])
    count = int(counter["consecutive_degraded_observations"])
    incidents: dict[str, dict[str, Any]] = {}
    if token and degraded and count > maximum:
        row = _incident(
            generated_at=generated_at,
            registration_id="wallet_reconciliation_partial",
            entity="wallet_reconciliation",
            source_artifact=relative,
            observation_token=token,
            episode_start=str(counter["episode_start_token"]),
            degraded_state=status,
            reason=str(payload.get("note") or "wallet reconciliation remained partial"),
            count=count,
            maximum=maximum,
        )
        incidents[row["incident_id"]] = row
    return (
        {
            "registration_id": "wallet_reconciliation_partial",
            "artifact": relative,
            "observed_state": status,
            "observation_token": token or None,
            "consecutive_degraded_observations": count,
            "max_consecutive_degraded_observations": maximum,
            "state": "incident" if incidents else ("degraded_within_tolerance" if degraded else ("healthy_or_out_of_scope" if token else "unobserved")),
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
    return {
        "enabled": enabled,
        "eligible": bool(new),
        "notify": notify,
        "state_changed": bool(new),
        "subject": f"Polymarket degraded-state incidents: {len(active)} active",
        "body_file": str(body_path),
        "pattern": "superbru_score_change_state_digest",
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
            payload = {
                **base,
                "status": "skipped_lock_held",
                "registrations": _registrations(settings),
                "evaluations": [],
                "active_incidents": [],
                "new_incidents": [],
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
            _evaluate_scheduler,
            _evaluate_wallet,
            _evaluate_operating_state,
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
