"""WO-75 read-only executor live-ops control plane.

The future executor owns the execution ledger and heartbeat.  This module is
an independent consumer run by the existing VPS ops scheduler.  It never
writes either input and has no credential, signer, venue client, cancellation,
or order path.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .a1_controls import build_a1_sweep_advisory
from .config import EngineConfig, load_config
from .utils import ensure_dir, parse_timestamp, read_csv_rows, read_json, safe_float, safe_int, write_json


WORK_ORDER = "WO-75"
EXECUTION_LEDGER = "execution/execution_ledger.csv"
EXECUTOR_HEARTBEAT = "execution/executor_heartbeat.json"
OUTPUT_FILE = "execution/executor_status.json"
NOTIFICATION_STATE = "execution/executor_ops_notification_state.json"
NOTIFICATION_BODY = "execution/executor_ops_notification.md"
REGISTERED_DEAD_MAN_SECONDS = 1800
REGISTERED_FRESHNESS_SLO_SECONDS = 600
REGISTERED_RECONCILIATION_THRESHOLD_USD = 1.0
ALLOWED_MODES = frozenset({"replay", "canary", "portfolio"})

# The future writer must emit these fields.  Keeping the contract here allows
# WO-74 and PR-gate fixtures to certify it without implementing that writer.
EXECUTION_LEDGER_CONTRACT_FIELDS = (
    "recorded_at_utc",
    "mode",
    "action_type",
    "open_orders_after",
    "exposure_usd_after",
)
HEARTBEAT_CONTRACT_FIELDS = (
    "heartbeat_at_utc",
    "mode",
    "cycle_id",
    "executor_build_id",
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _positive(value: Any, default: float) -> float:
    parsed = safe_float(value)
    return parsed if parsed is not None and parsed > 0 else default


def _settings(cfg: EngineConfig) -> dict[str, float | bool]:
    raw = _mapping(cfg.raw.get("executor_ops"))
    reconciliation = _mapping(cfg.raw.get("wallet_reconciliation"))
    return {
        # These are maxima: configuration can alarm sooner, never later.
        "dead_man_gap_seconds": min(
            REGISTERED_DEAD_MAN_SECONDS,
            _positive(raw.get("dead_man_gap_seconds"), REGISTERED_DEAD_MAN_SECONDS),
        ),
        "executor_freshness_slo_seconds": min(
            REGISTERED_FRESHNESS_SLO_SECONDS,
            _positive(raw.get("executor_freshness_slo_seconds"), REGISTERED_FRESHNESS_SLO_SECONDS),
        ),
        "reconciliation_discrepancy_threshold_usd": min(
            REGISTERED_RECONCILIATION_THRESHOLD_USD,
            _positive(
                reconciliation.get("discrepancy_threshold_usd"),
                REGISTERED_RECONCILIATION_THRESHOLD_USD,
            ),
        ),
        # Owner alerts use the existing state-digest/body-file email wrapper.
        "notification_enabled": True,
    }


def _as_of(value: datetime | str | None) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    parsed = parse_timestamp(value) if value else None
    return parsed.astimezone(timezone.utc) if parsed is not None else datetime.now(timezone.utc)


def _age_seconds(value: Any, as_of: datetime) -> float | None:
    parsed = parse_timestamp(value)
    return max(0.0, (as_of - parsed).total_seconds()) if parsed is not None else None


def _row_timestamp(row: Mapping[str, Any]) -> datetime | None:
    for field in ("recorded_at_utc", "created_at_utc", "timestamp", "action_at_utc"):
        parsed = parse_timestamp(row.get(field))
        if parsed is not None:
            return parsed
    return None


def _latest_row(rows: list[dict[str, str]]) -> dict[str, str]:
    ranked = [(stamp, index, row) for index, row in enumerate(rows) if (stamp := _row_timestamp(row)) is not None]
    if ranked:
        return max(ranked, key=lambda item: (item[0], item[1]))[2]
    return rows[-1] if rows else {}


def _first_number(row: Mapping[str, Any], fields: tuple[str, ...]) -> float | None:
    for field in fields:
        parsed = safe_float(row.get(field))
        if parsed is not None:
            return parsed
    return None


def _first_integer(row: Mapping[str, Any], fields: tuple[str, ...]) -> int | None:
    for field in fields:
        parsed = safe_int(row.get(field))
        if parsed is not None:
            return parsed
    return None


def _policy_cap(cfg: EngineConfig, latest: Mapping[str, Any]) -> tuple[float, str]:
    policy = _mapping(read_json(cfg.output_root / "maker_carry" / "decision_policy.json", default={}) or {})
    configured = _mapping(cfg.raw.get("decision_policy"))
    candidates: list[tuple[float, str]] = []
    for value, source in (
        (_mapping(policy.get("sizing")).get("binding_capital_usd"), "decision_policy.sizing.binding_capital_usd"),
        (_mapping(policy.get("ladder")).get("ladder_capital_usd"), "decision_policy.ladder.ladder_capital_usd"),
        (configured.get("stage0_capital_usd"), "effective_config.stage0_capital_usd"),
        (latest.get("stage_cap_usd"), "execution_ledger.stage_cap_usd"),
    ):
        parsed = safe_float(value)
        if parsed is not None and parsed > 0:
            candidates.append((parsed, source))
    if not candidates:
        return 100.0, "registered_stage0_default"
    value, source = min(candidates, key=lambda item: item[0])
    return round(value, 6), source


def _kill_scoreboard(cfg: EngineConfig) -> dict[str, Any]:
    policy = _mapping(read_json(cfg.output_root / "maker_carry" / "decision_policy.json", default={}) or {})
    kill = _mapping(policy.get("kill_criteria_status"))
    criteria = _mapping(kill.get("criteria"))
    rows = []
    for name, raw in sorted(criteria.items()):
        item = _mapping(raw)
        rows.append(
            {
                "criterion": name,
                "triggered": bool(item.get("triggered")),
                "observed": item.get("observed"),
                "threshold": item.get("threshold", item.get("threshold_days")),
            }
        )
    triggered = [str(value) for value in kill.get("triggered", []) if str(value)]
    if not triggered:
        triggered = [str(row["criterion"]) for row in rows if row["triggered"]]
    status = str(kill.get("status") or ("triggered" if triggered else ("clear" if policy else "unobserved")))
    return {
        "status": status,
        "triggered": triggered,
        "criteria": rows,
        "generated_at_utc": policy.get("generated_at_utc"),
    }


def _executor_discrepancy(cfg: EngineConfig) -> dict[str, Any]:
    payload = _mapping(read_json(cfg.output_root / "performance" / "wallet_reconciliation.json", default={}) or {})
    wallets = _mapping(payload.get("wallets"))
    # Custody Amendment A1 uses the operator/project wallet in a distinct
    # executor mode/time window. The old executor-wallet branch is retained
    # only as a clearly labelled legacy fallback for historical fixtures.
    executor = _mapping(wallets.get("operator"))
    source_role = "operator_project_wallet_A1"
    if (
        not executor
        and str(payload.get("primary_wallet_role") or "operator") == "operator"
        and any(key in payload for key in ("reconciliation_status", "unexplained_discrepancy_usd", "internal_nav_usd"))
    ):
        executor = payload
    if not executor:
        executor = _mapping(wallets.get("executor"))
        source_role = "legacy_pre_A1_executor_wallet"
    return {
        "status": str(executor.get("reconciliation_status") or executor.get("status") or "unobserved"),
        "unexplained_discrepancy_usd": safe_float(executor.get("unexplained_discrepancy_usd")),
        "generated_at_utc": executor.get("generated_at_utc") or payload.get("generated_at_utc"),
        "executor_wallet_present": bool(executor),
        "wallet_source_role": source_role,
        "account_structure": "single_project_account_under_custody_amendment_A1",
    }


def _alert(identifier: str, title: str, reason: str, *, severity: str = "critical") -> dict[str, str]:
    return {"id": identifier, "title": title, "reason": reason, "severity": severity}


def _notification(
    cfg: EngineConfig,
    *,
    generated_at: str,
    mode: str,
    alerts: list[dict[str, str]],
    enabled: bool,
) -> dict[str, Any]:
    state_path = cfg.output_root / NOTIFICATION_STATE
    body_path = cfg.output_root / NOTIFICATION_BODY
    previous = _mapping(read_json(state_path, default={}) or {})
    digest_input = [{"id": row["id"], "severity": row["severity"]} for row in sorted(alerts, key=lambda item: item["id"])]
    digest = hashlib.sha256(json.dumps(digest_input, sort_keys=True).encode("utf-8")).hexdigest()
    state_changed = digest != str(previous.get("digest") or "")
    eligible = bool(alerts)
    notify = bool(enabled and eligible and state_changed)
    lines = [
        "# Polymarket A1 / executor operations alert",
        "",
        f"Generated: `{generated_at}`",
        f"Executor mode: **{mode}**",
        "",
    ]
    if alerts:
        lines.extend(f"- **{row['title']}**: {row['reason']}" for row in alerts)
    else:
        lines.append("- No active executor live-ops alerts.")
    lines += [
        "",
        "Human operations alert only. This monitor cannot place, amend, cancel, sign, or authenticate orders.",
    ]
    ensure_dir(body_path.parent)
    body_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_json(
        state_path,
        {
            "generated_at_utc": generated_at,
            "digest": digest,
            "active_alert_ids": [row["id"] for row in alerts],
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )
    return {
        "enabled": enabled,
        "eligible": eligible,
        "notify": notify,
        "state_changed": state_changed,
        "subject": f"Polymarket executor ops: {len(alerts)} active alert(s)",
        "body_file": str(body_path),
        "pattern": "superbru_score_change_state_digest",
        "channel": "existing_email_wrapper",
        "digest": digest,
    }


def build_executor_ops_status(
    cfg: EngineConfig,
    *,
    as_of: datetime | str | None = None,
) -> dict[str, Any]:
    """Build executor status without invoking or controlling an executor."""
    observed_at = _as_of(as_of)
    generated_at = observed_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    settings = _settings(cfg)
    ledger_path = cfg.output_root / EXECUTION_LEDGER
    heartbeat_path = cfg.output_root / EXECUTOR_HEARTBEAT
    rows = read_csv_rows(ledger_path)
    latest = _latest_row(rows)
    executor_present = bool(rows)
    raw_mode = str(latest.get("mode") or latest.get("executor_mode") or "").strip().lower()
    mode = raw_mode.upper() if raw_mode in ALLOWED_MODES else ("UNKNOWN" if executor_present else "ABSENT")
    heartbeat = _mapping(read_json(heartbeat_path, default={}) or {})
    heartbeat_at = str(
        heartbeat.get("heartbeat_at_utc")
        or heartbeat.get("generated_at_utc")
        or heartbeat.get("updated_at_utc")
        or ""
    )
    heartbeat_age = _age_seconds(heartbeat_at, observed_at)
    dead_man_limit = float(settings["dead_man_gap_seconds"])
    freshness_limit = float(settings["executor_freshness_slo_seconds"])
    last_action_at = str(
        latest.get("recorded_at_utc")
        or latest.get("created_at_utc")
        or latest.get("timestamp")
        or latest.get("action_at_utc")
        or ""
    )
    last_action_age = _age_seconds(last_action_at, observed_at)
    open_orders = _first_integer(latest, ("open_orders_after", "open_orders")) if executor_present else None
    exposure = (
        _first_number(latest, ("exposure_usd_after", "exposure_usd", "capital_at_risk_usd"))
        if executor_present
        else None
    )
    if exposure is not None:
        exposure = abs(exposure)
    stage_cap, cap_source = _policy_cap(cfg, latest)
    exposure_state = (
        "ABSENT"
        if not executor_present
        else ("UNKNOWN" if exposure is None else ("BREACH" if exposure > stage_cap else "OK"))
    )
    dead_man_triggered = bool(executor_present and (heartbeat_age is None or heartbeat_age > dead_man_limit))
    freshness_breach = bool(executor_present and (heartbeat_age is None or heartbeat_age > freshness_limit))
    dead_man = {
        "state": "ABSENT" if not executor_present else ("TRIGGERED" if dead_man_triggered else "ARMED"),
        "heartbeat_at_utc": heartbeat_at or None,
        "heartbeat_age_seconds": None if heartbeat_age is None else round(heartbeat_age, 3),
        "gap_threshold_seconds": dead_man_limit,
        "countdown_seconds": (
            None
            if not executor_present or heartbeat_age is None
            else round(max(0.0, dead_man_limit - heartbeat_age), 3)
        ),
        "independent_monitor": "vps_ops_scheduler",
    }
    freshness = {
        "state": "ABSENT" if not executor_present else ("BREACH" if freshness_breach else "OK"),
        "heartbeat_age_seconds": None if heartbeat_age is None else round(heartbeat_age, 3),
        "slo_seconds": freshness_limit,
    }
    kill = _kill_scoreboard(cfg)
    reconciliation = _executor_discrepancy(cfg)
    sweep_advisory = build_a1_sweep_advisory(cfg, as_of=observed_at)
    alerts: list[dict[str, str]] = []
    if executor_present and kill["triggered"]:
        alerts.append(
            _alert(
                "kill_criteria_triggered",
                "Executor kill criterion triggered",
                ", ".join(kill["triggered"]),
            )
        )
    if dead_man_triggered:
        reason = "executor heartbeat is missing" if heartbeat_age is None else f"heartbeat age {heartbeat_age:.1f}s exceeds {dead_man_limit:.0f}s"
        alerts.append(_alert("dead_man_triggered", "Executor dead-man triggered", reason))
    discrepancy = safe_float(reconciliation.get("unexplained_discrepancy_usd"))
    discrepancy_limit = float(settings["reconciliation_discrepancy_threshold_usd"])
    if discrepancy is not None:
        discrepancy = abs(discrepancy)
        reconciliation["unexplained_discrepancy_usd"] = discrepancy
    if executor_present and discrepancy is not None and discrepancy > discrepancy_limit:
        alerts.append(
            _alert(
                "executor_reconciliation_discrepancy",
                "Executor reconciliation discrepancy",
                f"${discrepancy:.2f} unexplained exceeds ${discrepancy_limit:.2f}",
            )
        )
    if freshness_breach:
        reason = "executor heartbeat is missing" if heartbeat_age is None else f"heartbeat age {heartbeat_age:.1f}s exceeds freshness SLO {freshness_limit:.0f}s"
        alerts.append(
            _alert(
                "executor_freshness_slo_breach",
                "Executor freshness SLO breached",
                reason,
                severity="warning" if not dead_man_triggered else "critical",
            )
        )
    if executor_present and exposure_state == "BREACH":
        alerts.append(
            _alert(
                "executor_stage_cap_breach",
                "Executor exposure exceeds stage cap",
                f"${exposure:.2f} exposure exceeds ${stage_cap:.2f}",
            )
        )
    if executor_present and raw_mode not in ALLOWED_MODES:
        alerts.append(
            _alert(
                "executor_mode_invalid",
                "Executor mode is not registered",
                f"ledger mode {raw_mode or 'missing'} is outside replay/canary/portfolio",
            )
        )
    if sweep_advisory.get("notification_eligible") is True:
        alerts.append(
            _alert(
                "a1_sweep_advisory",
                "Custody A1 excess-balance sweep advised",
                str(sweep_advisory.get("advice") or "review the A1 sweep advisory"),
                severity="advisory",
            )
        )
    notification = _notification(
        cfg,
        generated_at=generated_at,
        mode=mode,
        alerts=alerts,
        enabled=bool(settings["notification_enabled"]),
    )
    executor_alerts = [row for row in alerts if row.get("id") != "a1_sweep_advisory"]
    status = (
        "A1_ADVISORY"
        if not executor_present and sweep_advisory.get("notification_eligible") is True
        else ("ABSENT" if not executor_present else ("ALERT" if executor_alerts else "OK"))
    )
    payload = {
        "work_order": WORK_ORDER,
        "generated_at_utc": generated_at,
        "status": status,
        "mode": mode,
        "executor_present": executor_present,
        "execution_ledger_path": str(ledger_path),
        "execution_ledger_rows": len(rows),
        "heartbeat_path": str(heartbeat_path),
        "open_orders": open_orders,
        "exposure_usd": None if exposure is None else round(exposure, 6),
        "stage_cap_usd": stage_cap,
        "stage_cap_source": cap_source,
        "exposure_vs_stage_cap_state": exposure_state,
        "last_action_at_utc": last_action_at or None,
        "last_action_age_seconds": None if last_action_age is None else round(last_action_age, 3),
        "dead_man": dead_man,
        "freshness_slo": freshness,
        "kill_criteria_scoreboard": kill,
        "executor_reconciliation": {
            **reconciliation,
            "threshold_usd": discrepancy_limit,
        },
        "a1_sweep_advisory": sweep_advisory,
        "owner_alerts": alerts,
        "owner_alert_count": len(alerts),
        "notification": notification,
        "notify": notification["notify"],
        "body_file": notification["body_file"],
        "state_changed": notification["state_changed"],
        "stop_propagation_binding_implemented": False,
        "post_amendment_item_2_implemented": False,
        "read_only": True,
        "credential_loading_invoked": False,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
        "order_placement_invoked": False,
        "order_amendment_invoked": False,
        "order_cancellation_invoked": False,
    }
    write_json(cfg.output_root / OUTPUT_FILE, payload)
    return payload


def main(config_path: str) -> dict[str, Any]:
    return build_executor_ops_status(load_config(config_path))
