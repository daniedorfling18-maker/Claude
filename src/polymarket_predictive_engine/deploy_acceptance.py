"""WO-79 post-deploy acceptance on real, freshly produced VPS artifacts.

The evaluator is reporting-only.  The deploy runner invokes the existing
read-only producers, then this module checks their cross-component contracts
and emits an owner-alert artifact.  It never places, amends, or cancels an
order and never changes a model, gate, or sizing rule.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from .artifact_contracts import build_contract_registry, validate_contract_fixture
from .config import EngineConfig, load_config
from .degraded_state_watchdog import MISSING_INPUT_RULES
from .utils import ensure_dir, parse_timestamp, read_csv_rows, read_json, safe_float, write_json


WORK_ORDER = "WO-79"
OUTPUT_FILE = "ops_scheduler/deploy_acceptance.json"
BASELINE_FILE = "ops_scheduler/deploy_acceptance_baseline.json"
PRODUCER_CYCLE_FILE = "ops_scheduler/deploy_acceptance_cycle.json"
NOTIFICATION_BODY = "ops_scheduler/deploy_acceptance_notification.md"
REQUIRED_GOVERNANCE_DOCS: tuple[tuple[str, str], ...] = (
    ("AGENTS.md", "outputs/performance/operating_state.md"),
    ("CLAUDE.md", "AGENTS.md"),
    ("docs/KEY_CUSTODY_DESIGN_WO67_P5.md", "Amendment A1"),
)

LEGITIMATE_REQUOTE_RULES = frozenset(
    {
        "mid_outside_registered_band",
        "mid_drifted_beyond_quote_band",
        "scheduled_event_within_window",
        "flow_toxicity_above_limit",
        "resolution_proposal_or_resolution_detected",
    }
)
LEGITIMATE_INERT_STATUSES = frozenset({"inert_no_quote_sheet", "inert_empty_quote_sheet"})
MAKER_ATTRIBUTION_FIELDS = (
    "fills_last_24h_raw",
    "owner_activity_fills_last_24h",
    "maker_test_fills_last_24h",
    "owner_activity_only",
    "fill_attribution",
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = _mapping(cfg.raw.get("deploy_acceptance"))
    enabled = str(raw.get("notification_enabled", True)).strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
        "disabled",
    }
    return {"notification_enabled": enabled}


def _stamp(payload: Mapping[str, Any]) -> datetime | None:
    for key in ("generated_at_utc", "updated_at_utc", "collected_at_utc"):
        parsed = parse_timestamp(payload.get(key))
        if parsed is not None:
            return parsed.astimezone(timezone.utc)
    return None


def _fresh_after(payload: Mapping[str, Any], baseline_at: datetime | None) -> tuple[bool, str]:
    observed = _stamp(payload)
    if observed is None:
        return False, "producer timestamp missing"
    if baseline_at is None:
        return False, "deploy baseline timestamp missing"
    # Host and container clocks share the VPS, but tolerate whole-second
    # serialization around the capture boundary.
    threshold = baseline_at.astimezone(timezone.utc) - timedelta(seconds=2)
    return observed >= threshold, observed.strftime("%Y-%m-%dT%H:%M:%SZ")


def _quote_ticket_check(
    study: Mapping[str, Any],
    requote: Mapping[str, Any],
    *,
    generated_at: datetime,
) -> dict[str, Any]:
    source_contract = validate_contract_fixture("quote_sheet_to_requote_alerts", study)
    portfolio = study.get("portfolio") if isinstance(study.get("portfolio"), list) else []
    markets = requote.get("markets") if isinstance(requote.get("markets"), list) else []
    by_condition = {
        str(row.get("condition_id") or "").strip(): _mapping(row)
        for row in markets
        if isinstance(row, Mapping) and str(row.get("condition_id") or "").strip()
    }
    missing_conditions: list[str] = []
    incomplete: list[dict[str, Any]] = []
    required = (
        "market_url",
        "token_id",
        "order_price_min_tick_size",
        "quote_bid_price",
        "quote_ask_price",
    )
    for index, raw in enumerate(portfolio):
        source = _mapping(raw)
        condition_id = str(source.get("condition_id") or "").strip()
        evaluated = by_condition.get(condition_id)
        if not condition_id or evaluated is None:
            missing_conditions.append(condition_id or f"row:{index}")
            continue
        missing = [
            field
            for field in required
            if field not in evaluated
            or evaluated.get(field) is None
            or (isinstance(evaluated.get(field), str) and not str(evaluated.get(field)).strip())
        ]
        tick = safe_float(evaluated.get("order_price_min_tick_size"))
        bid = safe_float(evaluated.get("quote_bid_price"))
        ask = safe_float(evaluated.get("quote_ask_price"))
        if tick is not None and not 0 < tick < 1:
            missing.append("valid_tick")
        if bid is not None and ask is not None and not 0 < bid < ask < 1:
            missing.append("valid_bid_lt_ask")
        if str(evaluated.get("order_ticket_status") or "") != "exact":
            missing.append("order_ticket_status=exact")
        if missing:
            incomplete.append({"condition_id": condition_id, "missing_or_invalid": sorted(set(missing))})

    study_stamp = _stamp(study)
    age_seconds = max(0.0, (generated_at - study_stamp).total_seconds()) if study_stamp is not None else None
    freshness_ok = age_seconds is not None and age_seconds <= 93600
    defects = list(source_contract["defects"])
    if missing_conditions:
        defects.append({"missing_requote_rows": sorted(missing_conditions)})
    if incomplete:
        defects.append({"incomplete_evaluated_tickets": incomplete})
    if not freshness_ok:
        defects.append({"quote_sheet_freshness_seconds": age_seconds, "maximum": 93600})
    return {
        "id": "quote_sheet_ticket_completeness",
        "status": "PASS" if not defects else "FAIL",
        "portfolio_tickets": len(portfolio),
        "requote_rows": len(markets),
        "coverage": "not_applicable_empty_portfolio" if not portfolio else "every portfolio condition_id",
        "quote_sheet_age_seconds": round(age_seconds, 3) if age_seconds is not None else None,
        "defects": defects,
    }


def _maker_replay_collection_check(
    study: Mapping[str, Any],
    collection: Mapping[str, Any],
    baseline_at: datetime | None,
) -> dict[str, Any]:
    portfolio = study.get("portfolio") if isinstance(study.get("portfolio"), list) else []
    windows = collection.get("market_windows") if isinstance(collection.get("market_windows"), list) else []
    contract = validate_contract_fixture(
        "maker_replay_collection_to_fill_replay",
        {"portfolio": portfolio, "market_windows": windows},
    )
    fresh, observed_at = _fresh_after(collection, baseline_at)
    defects: list[Any] = []
    if contract["status"] != "PASS":
        defects.append({"contract_defects": contract["defects"]})
    if not fresh:
        defects.append({"reason": observed_at})
    if portfolio:
        uncovered = [
            str(row.get("condition_id") or "")
            for row in windows
            if isinstance(row, Mapping) and row.get("covered") is not True
        ]
        if str(collection.get("status") or "") != "ok":
            defects.append({"collection_status": collection.get("status") or "missing"})
        if uncovered:
            defects.append({"uncovered_conditions": sorted(uncovered)})
    return {
        "id": "maker_replay_exact_portfolio_collection",
        "status": "PASS" if not defects else "FAIL",
        "observed_at_utc": observed_at,
        "portfolio_markets": len(portfolio),
        "collection_windows": len(windows),
        "windows_covered": sum(
            bool(row.get("covered")) for row in windows if isinstance(row, Mapping)
        ),
        "defects": defects,
    }


def _requote_rules(requote: Mapping[str, Any]) -> set[str]:
    rules: set[str] = set()
    rows = requote.get("markets") if isinstance(requote.get("markets"), list) else []
    for raw in rows:
        row = _mapping(raw)
        alerts = row.get("alerts") if isinstance(row.get("alerts"), list) else []
        for raw_alert in alerts:
            alert = _mapping(raw_alert)
            rule = str(alert.get("rule") or "").strip()
            if rule:
                rules.add(rule)
    return rules


def _requote_state_check(requote: Mapping[str, Any], baseline_at: datetime | None) -> dict[str, Any]:
    status = str(requote.get("status") or "missing")
    state = str(requote.get("alert_state") or "missing")
    rules = _requote_rules(requote)
    missing_rules = sorted(rules & MISSING_INPUT_RULES)
    unknown_rules = sorted(rules - MISSING_INPUT_RULES - LEGITIMATE_REQUOTE_RULES)
    legitimate_rules = sorted(rules & LEGITIMATE_REQUOTE_RULES)
    kill = requote.get("kill_criteria_triggered") if isinstance(requote.get("kill_criteria_triggered"), list) else []
    fresh, observed_at = _fresh_after(requote, baseline_at)

    reason = ""
    acceptable = False
    if status in LEGITIMATE_INERT_STATUSES:
        acceptable = True
        reason = status
    elif state in {"quotes_ok", "requote_advised"} and not missing_rules and not unknown_rules:
        acceptable = True
        reason = "non_fail_closed_state"
    elif state in {"pull_quotes_now", "STOP"} and not missing_rules and not unknown_rules and (legitimate_rules or kill):
        acceptable = True
        reason = "specific_registered_risk_blocker"
    defects: list[str] = []
    if not fresh:
        defects.append(observed_at)
    if missing_rules:
        defects.append("missing-input rule(s): " + ", ".join(missing_rules))
    if unknown_rules:
        defects.append("unregistered blocker rule(s): " + ", ".join(unknown_rules))
    if not acceptable:
        defects.append(f"requote state is not accepted: status={status}, alert_state={state}")
    return {
        "id": "requote_evaluator_state",
        "status": "PASS" if not defects else "FAIL",
        "producer_status": status,
        "alert_state": state,
        "reason": reason,
        "legitimate_blocker_rules": legitimate_rules,
        "missing_input_rules": missing_rules,
        "unknown_rules": unknown_rules,
        "kill_criteria_triggered": kill,
        "observed_at_utc": observed_at,
        "defects": defects,
    }


def _reconciliation_check(payload: Mapping[str, Any], baseline_at: datetime | None) -> dict[str, Any]:
    contract = validate_contract_fixture("reconciliation_legs_to_nav", payload)
    fresh, observed_at = _fresh_after(payload, baseline_at)
    defects = list(contract["defects"])
    legs: dict[str, Any] = {}
    for name in ("internal", "data_api", "onchain"):
        row = _mapping(payload.get(name))
        legs[name] = {"status": row.get("status"), "nav_usd": row.get("nav_usd")}
        if not str(row.get("status") or "").strip():
            defects.append({"leg": name, "reason": "leg_not_attempted_or_status_missing"})
    data_api = _mapping(payload.get("data_api"))
    if data_api.get("status") == "reconstruction_incomplete_external_deposit":
        if not str(data_api.get("reconstruction_note") or "").strip():
            defects.append({"leg": "data_api", "reason": "known_incomplete_reason_missing"})
        if (safe_float(data_api.get("external_deposit_baseline_applied_usd")) or 0.0) <= 0:
            defects.append({"leg": "data_api", "reason": "external_deposit_baseline_not_applied"})
        if data_api.get("activity_complete") is not False:
            defects.append({"leg": "data_api", "reason": "known_incomplete_activity_claimed_complete"})
        if not str(payload.get("discrepancy_note") or "").strip():
            defects.append({"reason": "known_incomplete_discrepancy_note_missing"})
    if not fresh:
        defects.append({"reason": observed_at})
    return {
        "id": "three_way_wallet_reconciliation",
        "status": "PASS" if not defects else "FAIL",
        "reconciliation_status": payload.get("reconciliation_status"),
        "observed_at_utc": observed_at,
        "legs": legs,
        "defects": defects,
    }


def _row_states(payload: Mapping[str, Any]) -> dict[str, str]:
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    return {
        str(row.get("key") or "").strip(): str(row.get("state") or "UNKNOWN").strip()
        for row in rows
        if isinstance(row, Mapping) and str(row.get("key") or "").strip()
    }


def _operating_unknown_check(
    baseline: Mapping[str, Any],
    current: Mapping[str, Any],
    baseline_at: datetime | None,
) -> dict[str, Any]:
    baseline_state = _mapping(baseline.get("operating_state"))
    before = _row_states(baseline_state)
    after = _row_states(current)
    fresh, observed_at = _fresh_after(current, baseline_at)
    ignored = {"deploy_acceptance"}  # self-referential until this acceptance artifact exists
    new_unknowns = sorted(
        key
        for key, state in after.items()
        if key not in ignored and state.upper() == "UNKNOWN" and before.get(key, "").upper() != "UNKNOWN"
    )
    missing_previously_known = sorted(
        key
        for key, state in before.items()
        if key not in ignored and state.upper() != "UNKNOWN" and key not in after
    )
    defects: list[Any] = []
    if not baseline_state:
        defects.append("pre-deploy operating-state baseline missing")
    if not fresh:
        defects.append(observed_at)
    if new_unknowns:
        defects.append({"new_unknown_rows": new_unknowns})
    if missing_previously_known:
        defects.append({"missing_previously_known_rows": missing_previously_known})
    return {
        "id": "operating_state_unknown_regression",
        "status": "PASS" if not defects else "FAIL",
        "observed_at_utc": observed_at,
        "pre_deploy_rows": len(before),
        "post_deploy_rows": len(after),
        "new_unknown_rows": new_unknowns,
        "missing_previously_known_rows": missing_previously_known,
        "ignored_self_referential_rows": sorted(ignored),
        "defects": defects,
    }


def _producer_cycle_check(cfg: EngineConfig, baseline_at: datetime | None) -> dict[str, Any]:
    payload = _mapping(read_json(cfg.output_root / PRODUCER_CYCLE_FILE, default={}) or {})
    commands = _mapping(payload.get("commands"))
    required = (
        "maker_carry_study",
        "collect_maker_replay_data",
        "maker_fill_replay",
        "maker_live_test",
        "decision_policy",
        "requote_alerts",
        "reconcile_wallet",
        "executor_ops_monitor",
        "operating_state",
    )
    defects: list[Any] = []
    missing = sorted(name for name in required if name not in commands)
    nonzero: dict[str, Any] = {}
    for name in required:
        raw = _mapping(commands.get(name))
        try:
            code = int(raw.get("exit_code"))
        except (TypeError, ValueError):
            continue
        if code != 0:
            nonzero[name] = code
    fresh, observed_at = _fresh_after(payload, baseline_at)
    if missing:
        defects.append({"missing_producer_commands": missing})
    if nonzero:
        defects.append({"nonzero_producer_commands": nonzero})
    if not fresh:
        defects.append({"reason": observed_at})
    return {
        "id": "real_current_data_producer_cycle",
        "status": "PASS" if not defects else "FAIL",
        "observed_at_utc": observed_at,
        "commands": commands,
        "defects": defects,
    }


def _maker_attribution_check(
    cfg: EngineConfig,
    payload: Mapping[str, Any],
    baseline_at: datetime | None,
) -> dict[str, Any]:
    settings = _mapping(cfg.raw.get("maker_live_test"))
    enabled = str(settings.get("enabled", True)).strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
        "disabled",
    }
    configured = enabled and bool(
        str(settings.get("wallet_address") or "").strip()
        or str(settings.get("executor_wallet_address") or "").strip()
    )
    fresh, observed_at = _fresh_after(payload, baseline_at)
    status = str(payload.get("status") or "missing")
    defects: list[Any] = []
    missing_fields = sorted(field for field in MAKER_ATTRIBUTION_FIELDS if field not in payload)

    if not fresh:
        defects.append({"reason": observed_at})
    if "WO-88" not in str(payload.get("work_order") or ""):
        defects.append({"reason": "WO-88 producer marker missing"})
    if payload.get("paper_trading_invoked") is not False or payload.get("live_trading_invoked") is not False:
        defects.append({"reason": "maker scoreboard must remain read-only"})

    raw_fills = owner_fills = maker_fills = None
    history_fresh = False
    history_rows = read_csv_rows(
        cfg.output_root / "maker_carry" / "maker_live_test_attribution_history.csv"
    )
    if configured:
        if status != "ok":
            defects.append({"producer_status": status, "expected": "ok"})
        if missing_fields:
            defects.append({"missing_attribution_fields": missing_fields})
        else:
            raw_fills = safe_float(payload.get("fills_last_24h_raw"))
            owner_fills = safe_float(payload.get("owner_activity_fills_last_24h"))
            maker_fills = safe_float(payload.get("maker_test_fills_last_24h"))
            if None in {raw_fills, owner_fills, maker_fills}:
                defects.append({"reason": "fill attribution counts are not numeric"})
            elif raw_fills != owner_fills + maker_fills:
                defects.append(
                    {
                        "reason": "raw fills must equal owner plus maker-test fills",
                        "raw": raw_fills,
                        "owner": owner_fills,
                        "maker_test": maker_fills,
                    }
                )
            if safe_float(payload.get("fills_last_24h")) != maker_fills:
                defects.append({"reason": "legacy kill count must equal maker-test fills"})
            if not isinstance(payload.get("owner_activity_only"), bool):
                defects.append({"reason": "owner_activity_only must be boolean"})
            if not isinstance(payload.get("fill_attribution"), Mapping):
                defects.append({"reason": "fill_attribution detail must be an object"})

        primary_role = str(payload.get("primary_wallet_role") or payload.get("wallet_role") or "").strip()
        history_fresh = any(
            (not primary_role or str(row.get("wallet_role") or "").strip() == primary_role)
            and _fresh_after(row, baseline_at)[0]
            for row in history_rows
        )
        if not history_fresh:
            defects.append({"reason": "fresh WO-88 attribution history row missing"})
    elif status not in {"awaiting_wallet_address", "disabled", "ok"}:
        defects.append({"producer_status": status, "expected": "configured or explicitly inert"})

    return {
        "id": "maker_live_test_attribution_freshness",
        "status": "PASS" if not defects else "FAIL",
        "producer_status": status,
        "configured": configured,
        "observed_at_utc": observed_at,
        "fills_last_24h_raw": raw_fills,
        "owner_activity_fills_last_24h": owner_fills,
        "maker_test_fills_last_24h": maker_fills,
        "attribution_history_rows": len(history_rows),
        "attribution_history_fresh": history_fresh if configured else None,
        "defects": defects,
    }


def _governance_docs_readable_check(cfg: EngineConfig) -> dict[str, Any]:
    repo_root = cfg.path.resolve().parent
    defects: list[dict[str, str]] = []
    observed: list[dict[str, Any]] = []
    for relative, required_text in REQUIRED_GOVERNANCE_DOCS:
        path = repo_root / relative
        row = {"path": relative, "readable": False, "required_text_present": False}
        try:
            text = path.read_text(encoding="utf-8-sig", errors="strict")
            row["readable"] = True
            row["required_text_present"] = required_text in text
            if required_text not in text:
                defects.append({"path": relative, "reason": f"required marker missing: {required_text}"})
        except (OSError, UnicodeError) as exc:
            defects.append({"path": relative, "reason": f"unreadable: {type(exc).__name__}: {exc}"})
        observed.append(row)
    return {
        "id": "governance_documents_mounted_read_only",
        "status": "PASS" if not defects else "FAIL",
        "documents": observed,
        "expected_container_mode": "read_only_bind_mount",
        "defects": defects,
    }


def _notification(
    cfg: EngineConfig,
    *,
    payload: Mapping[str, Any],
    enabled: bool,
    previous: Mapping[str, Any],
) -> dict[str, Any]:
    body_path = cfg.output_root / NOTIFICATION_BODY
    failures = [row for row in payload.get("checks", []) if isinstance(row, Mapping) and row.get("status") == "FAIL"]
    lines = [
        "# Polymarket deploy acceptance",
        "",
        f"Status: **{payload.get('status')}**",
        f"Target deploy: `{payload.get('target_deploy_sha') or 'unknown'}`",
        f"Rollback ref: `{payload.get('rollback_ref') or 'unknown'}`",
        "",
    ]
    for row in failures:
        lines.append(f"- **{row.get('id')}**: {json.dumps(row.get('defects'), sort_keys=True, default=str)}")
    if not failures:
        lines.append("- All registered post-deploy acceptance checks passed.")
    lines += [
        "",
        "Human operations alert only. This acceptance pass cannot trade or alter a gate.",
    ]
    ensure_dir(body_path.parent)
    body_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    digest_material = json.dumps(
        {"status": payload.get("status"), "failures": failures, "target": payload.get("target_deploy_sha")},
        sort_keys=True,
        default=str,
    )
    digest = hashlib.sha256(digest_material.encode("utf-8")).hexdigest()
    previous_notification = _mapping(previous.get("notification"))
    state_changed = digest != str(previous_notification.get("digest") or "")
    eligible = payload.get("status") == "FAIL"
    return {
        "enabled": enabled,
        "eligible": eligible,
        "notify": bool(enabled and eligible),
        "state_changed": state_changed,
        "subject": f"Polymarket deploy acceptance: {payload.get('status')}",
        "body_file": str(body_path),
        "pattern": "superbru_score_change_state_digest",
        "digest": digest,
    }


def build_deploy_acceptance(
    cfg: EngineConfig,
    *,
    baseline_path: str | Path | None = None,
    expected_deploy_sha: str | None = None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    generated_dt = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    generated_at = generated_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    output_path = cfg.output_root / OUTPUT_FILE
    previous = _mapping(read_json(output_path, default={}) or {})
    baseline_file = Path(baseline_path) if baseline_path is not None else cfg.output_root / BASELINE_FILE
    baseline = _mapping(read_json(baseline_file, default={}) or {})
    baseline_at = parse_timestamp(baseline.get("captured_at_utc"))

    study = _mapping(read_json(cfg.output_root / "maker_carry" / "maker_carry_study.json", default={}) or {})
    maker_collection = _mapping(
        read_json(cfg.output_root / "maker_carry" / "maker_replay_collection.json", default={}) or {}
    )
    requote = _mapping(read_json(cfg.output_root / "maker_carry" / "requote_alerts.json", default={}) or {})
    maker_live_test = _mapping(
        read_json(cfg.output_root / "maker_carry" / "maker_live_test.json", default={}) or {}
    )
    reconciliation = _mapping(read_json(cfg.output_root / "performance" / "wallet_reconciliation.json", default={}) or {})
    operating = _mapping(read_json(cfg.output_root / "performance" / "operating_state.json", default={}) or {})
    registry = build_contract_registry(cfg)

    checks = [
        _governance_docs_readable_check(cfg),
        _producer_cycle_check(cfg, baseline_at),
        _maker_attribution_check(cfg, maker_live_test, baseline_at),
        _quote_ticket_check(study, requote, generated_at=generated_dt),
        _maker_replay_collection_check(study, maker_collection, baseline_at),
        _requote_state_check(requote, baseline_at),
        _reconciliation_check(reconciliation, baseline_at),
        _operating_unknown_check(baseline, operating, baseline_at),
    ]
    if registry.get("status") != "PASS":
        checks.append(
            {
                "id": "producer_consumer_contract_registry",
                "status": "FAIL",
                "defects": registry.get("declaration_defects", []),
            }
        )
    status = "PASS" if all(row.get("status") == "PASS" for row in checks) else "FAIL"
    target = str(expected_deploy_sha or os.getenv("PM_VPS_DEPLOYED_SHA") or baseline.get("target_deploy_sha") or "").strip()
    payload: dict[str, Any] = {
        "work_order": WORK_ORDER,
        "status": status,
        "generated_at_utc": generated_at,
        "baseline_captured_at_utc": baseline.get("captured_at_utc"),
        "baseline_path": str(baseline_file),
        "target_deploy_sha": target,
        "rollback_ref": baseline.get("previous_deployed_sha") or baseline.get("previous_checkout_sha"),
        "checks": checks,
        "diffs": {str(row.get("id")): row.get("defects", []) for row in checks},
        "contract_registry_status": registry.get("status"),
        "real_current_data": True,
        "read_only": True,
        "decision_use": "deployment acceptance and owner alerting only",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
        "order_placement_invoked": False,
        "order_amendment_invoked": False,
        "order_cancellation_invoked": False,
    }
    notification = _notification(
        cfg,
        payload=payload,
        enabled=bool(_settings(cfg)["notification_enabled"]),
        previous=previous,
    )
    payload["notification"] = notification
    payload["notify"] = notification["notify"]
    payload["body_file"] = notification["body_file"]
    payload["state_changed"] = notification["state_changed"]
    write_json(output_path, payload)
    return payload


def main(config_path: str) -> dict[str, Any]:
    return build_deploy_acceptance(load_config(config_path))
