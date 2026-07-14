"""WO-82 human Stage-1 operating page and append-only action record.

This module is deliberately incapable of execution.  It renders the current
human decision surface from existing evidence and records actions that the
operator says they performed in the venue UI.  It has no CLOB client, signer,
credential reader, broker, or order mutation path.
"""

from __future__ import annotations

import hashlib
import os
from datetime import date, timedelta, timezone
from pathlib import Path
import time
from typing import Any, Mapping
from uuid import uuid4

from .a1_controls import build_a1_sweep_advisory
from .config import EngineConfig, load_config
from .cost_ledger import aggregate_costs
from .runtime_lock import runtime_lock
from .utils import (
    append_csv_rows,
    ensure_dir,
    now_utc,
    parse_timestamp,
    read_csv_rows,
    read_json,
    replace_with_retry,
    safe_float,
    write_json,
)


WORK_ORDER = "WO-82"
UNKNOWN = "UNKNOWN"
ACTION_TYPES = (
    "quote_placed",
    "quote_pulled",
    "quote_repriced",
    "size_changed",
    "kill_acknowledged",
    "inventory_observed",
    "no_action",
    "note",
)
ACTION_SIDES = ("bid", "ask", "both", "n/a")
ACTION_FIELDS = [
    "entry_id",
    "logged_at_utc",
    "stage_date",
    "actor",
    "action_type",
    "market_id",
    "side",
    "price",
    "size_shares",
    "note",
    "page_path",
    "page_sha256",
    "human_action_recorded",
    "system_order_action_invoked",
    "paper_trading_invoked",
    "live_trading_invoked",
]

A1_REMINDERS = [
    "Use the single project account under custody Amendment A1.",
    "Complete the human Stage-1 window before any future executor canary; human and executor windows never overlap.",
    "Keep the balance at or near the active stage cap and sweep registered excess to VALR manually.",
    "The system only reports and records human actions; it never places, amends, cancels, signs, or authenticates an order.",
]

KILL_RESPONSE_STEPS = [
    "Stop entering or replacing quotes. Leave the generated stage page open as the evidence reference.",
    "In Polymarket, open Portfolio, then Open Orders; choose Cancel all and confirm the venue prompt.",
    "Refresh Open Orders and verify the count is zero. If any order remains, do not replace it; retry the venue cancellation and record the unresolved order ID.",
    "Open Positions and record every remaining inventory position. Do not improvise a taker exit outside the registered policy.",
    "Record `kill_acknowledged` and each `quote_pulled` action with this CLI, including UTC time and market ID.",
    "Capture the resulting requote, reconciliation, and cost evidence; resume only after a documented human review clears the registered trigger.",
]


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _enabled(value: Any) -> bool:
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = cfg.raw.get("stage_operator", {}) if isinstance(cfg.raw.get("stage_operator"), dict) else {}
    merged: dict[str, Any] = {
        "enabled": True,
        "page_directory": "execution/stage_days",
        "summary_file": "execution/stage_day.json",
        "action_ledger_file": "execution/stage_operator_log.csv",
        "lock_stale_seconds": 1800,
    }
    merged.update({key: value for key, value in raw.items() if value is not None})
    return merged


def _output_path(cfg: EngineConfig, value: Any) -> Path:
    text = str(value or "").strip().replace("\\", "/")
    path = Path(text)
    if not text or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"stage-operator path must stay below paths.output_root: {value!r}")
    return cfg.output_root / path


def _target_date(value: str | None) -> date:
    text = str(value or now_utc()[:10]).strip()
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("stage date must be an ISO date (YYYY-MM-DD)") from exc


def _atomic_write_text(path: Path, text: str) -> None:
    ensure_dir(path.parent)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    replace_with_retry(temp_path, path)


def _ticket_view(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw_rows = payload.get("portfolio") if isinstance(payload.get("portfolio"), list) else []
    tickets: list[dict[str, Any]] = []
    required = ("condition_id", "market_url", "outcome", "quote_bid_price", "quote_ask_price", "quote_size_shares")
    for raw in raw_rows:
        if not isinstance(raw, Mapping):
            continue
        ticket = {
            "question": str(raw.get("question") or "").strip(),
            "condition_id": str(raw.get("condition_id") or "").strip(),
            "market_url": str(raw.get("market_url") or "").strip(),
            "outcome": str(raw.get("outcome") or "").strip(),
            "quote_bid_price": safe_float(raw.get("quote_bid_price")),
            "quote_ask_price": safe_float(raw.get("quote_ask_price")),
            "quote_size_shares": safe_float(raw.get("quote_size_shares")),
            "order_ticket_status": str(raw.get("order_ticket_status") or "").strip(),
        }
        ticket["complete"] = all(ticket.get(field) not in {None, ""} for field in required)
        tickets.append(ticket)
    if not payload:
        state = UNKNOWN
    elif not tickets:
        state = "NO_TICKETS"
    elif all(row["complete"] and row["order_ticket_status"] == "exact" for row in tickets):
        state = "EXACT_HUMAN_TICKETS"
    else:
        state = "INCOMPLETE_FAIL_CLOSED"
    return {
        "state": state,
        "count": len(tickets),
        "generated_at_utc": payload.get("generated_at_utc") or UNKNOWN,
        "tickets": tickets,
    }


def _requote_view(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not payload:
        return {"state": UNKNOWN, "headline": UNKNOWN, "markets_requiring_action": None, "generated_at_utc": UNKNOWN}
    markets = payload.get("markets") if isinstance(payload.get("markets"), list) else []
    requiring_action = sum(
        1
        for row in markets
        if isinstance(row, Mapping) and str(row.get("action") or row.get("state") or "").lower() not in {"", "keep", "quotes_ok"}
    )
    return {
        "state": str(payload.get("alert_state") or payload.get("status") or UNKNOWN),
        "headline": str(payload.get("headline") or payload.get("operator_action") or UNKNOWN),
        "markets_checked": payload.get("markets_checked", len(markets)),
        "markets_requiring_action": payload.get("markets_requiring_action", requiring_action),
        "kill_criteria_triggered": payload.get("kill_criteria_triggered") or [],
        "generated_at_utc": payload.get("generated_at_utc") or UNKNOWN,
    }


def _kill_view(payload: Mapping[str, Any]) -> dict[str, Any]:
    kill = _mapping(payload.get("kill_criteria_status"))
    if not payload or not kill:
        return {"status": UNKNOWN, "triggered": [], "criteria": [], "generated_at_utc": UNKNOWN}
    criteria: list[dict[str, Any]] = []
    for name, raw in sorted(_mapping(kill.get("criteria")).items()):
        item = _mapping(raw)
        criteria.append(
            {
                "criterion": name,
                "triggered": bool(item.get("triggered")),
                "observed": item.get("value", item.get("observed", UNKNOWN)),
                "threshold": item.get("threshold", item.get("threshold_days", UNKNOWN)),
            }
        )
    triggered = [str(value) for value in kill.get("triggered", []) if str(value)]
    if not triggered:
        triggered = [str(row["criterion"]) for row in criteria if row["triggered"]]
    return {
        "status": str(kill.get("status") or ("triggered" if triggered else "clear")),
        "triggered": triggered,
        "criteria": criteria,
        "generated_at_utc": payload.get("generated_at_utc") or UNKNOWN,
    }


def _yesterday_reconciliation(cfg: EngineConfig, target: date) -> dict[str, Any]:
    yesterday = (target - timedelta(days=1)).isoformat()
    candidates: list[dict[str, Any]] = []
    wallet_path = cfg.output_root / "performance" / "wallet_reconciliation_wallet_history.csv"
    legacy_path = cfg.output_root / "performance" / "wallet_reconciliation_history.csv"
    source = legacy_path
    for candidate_path in (wallet_path, legacy_path):
        rows = read_csv_rows(candidate_path)
        matched = []
        for row in rows:
            if str(row.get("snapshot_date") or "") != yesterday:
                continue
            role = str(row.get("wallet_role") or "operator").strip().lower()
            if role == "operator":
                matched.append(row)
        if matched:
            candidates = matched
            source = candidate_path
            break
    if not candidates:
        return {
            "snapshot_date": yesterday,
            "state": UNKNOWN,
            "unexplained_discrepancy_usd": None,
            "generated_at_utc": UNKNOWN,
            "source": str(source),
        }
    candidates.sort(key=lambda row: str(row.get("generated_at_utc") or ""))
    row = candidates[-1]
    return {
        "snapshot_date": yesterday,
        "state": str(row.get("reconciliation_status") or UNKNOWN),
        "internal_nav_usd": safe_float(row.get("internal_nav_usd")),
        "data_api_nav_usd": safe_float(row.get("data_api_nav_usd")),
        "onchain_nav_usd": safe_float(row.get("onchain_nav_usd")),
        "unexplained_discrepancy_usd": safe_float(row.get("unexplained_discrepancy_usd")),
        "generated_at_utc": row.get("generated_at_utc") or UNKNOWN,
        "source": str(source),
    }


def _cost_delta(cfg: EngineConfig, target: date) -> dict[str, Any]:
    yesterday = target - timedelta(days=1)
    today_summary = aggregate_costs(cfg, start_date=target.isoformat(), end_date=target.isoformat())
    prior_summary = aggregate_costs(cfg, start_date=yesterday.isoformat(), end_date=yesterday.isoformat())
    ledger_exists = Path(str(today_summary["ledger_path"])).exists()
    today_total = float(today_summary["total_usd"])
    prior_total = float(prior_summary["total_usd"])
    return {
        "state": "AVAILABLE" if ledger_exists else UNKNOWN,
        "today_date": target.isoformat(),
        "today_usd": round(today_total, 8) if ledger_exists else None,
        "yesterday_date": yesterday.isoformat(),
        "yesterday_usd": round(prior_total, 8) if ledger_exists else None,
        "delta_vs_yesterday_usd": round(today_total - prior_total, 8) if ledger_exists else None,
        "today_by_category_usd": today_summary["by_category_usd"] if ledger_exists else {},
        "ledger_path": today_summary["ledger_path"],
    }


def _actions_for_date(path: Path, target: date) -> list[dict[str, Any]]:
    return [row for row in read_csv_rows(path) if str(row.get("stage_date") or "") == target.isoformat()]


def _md(value: Any) -> str:
    if value is None or value == "":
        return UNKNOWN
    return str(value).replace("|", "\\|").replace("\n", " ")


def _money(value: Any) -> str:
    parsed = safe_float(value)
    return UNKNOWN if parsed is None else f"${parsed:,.2f}"


def _render_markdown(payload: Mapping[str, Any]) -> str:
    tickets = _mapping(payload.get("order_tickets"))
    requote = _mapping(payload.get("requote"))
    kill = _mapping(payload.get("kill_scoreboard"))
    reconciliation = _mapping(payload.get("yesterday_reconciliation"))
    costs = _mapping(payload.get("cost_delta"))
    sweep = _mapping(payload.get("a1_sweep_advisory"))
    lines = [
        "# Human Stage-1 operating page",
        "",
        f"Stage date: **{_md(payload.get('stage_date'))}**  ",
        f"Generated: `{_md(payload.get('generated_at_utc'))}`  ",
        f"Evidence state: **{_md(payload.get('status'))}**",
        "",
        "> HUMAN ACTIONS ONLY. This page and CLI cannot place, amend, cancel, sign, or authenticate an order.",
        "",
        "## Order tickets (WO-66)",
        "",
        f"Ticket state: **{_md(tickets.get('state'))}** ({_md(tickets.get('count'))} ticket(s)).",
        "",
        "| Market | Outcome | Bid | Ask | Size | Ticket |",
        "|---|---:|---:|---:|---:|---|",
    ]
    ticket_rows = tickets.get("tickets") if isinstance(tickets.get("tickets"), list) else []
    if ticket_rows:
        for row in ticket_rows:
            market = f"[{_md(row.get('question') or row.get('condition_id'))}]({_md(row.get('market_url'))})" if row.get("market_url") else _md(row.get("question") or row.get("condition_id"))
            lines.append(
                f"| {market} | {_md(row.get('outcome'))} | {_md(row.get('quote_bid_price'))} | "
                f"{_md(row.get('quote_ask_price'))} | {_md(row.get('quote_size_shares'))} | "
                f"{_md(row.get('order_ticket_status'))} |"
            )
    else:
        lines.append(f"| {UNKNOWN} | {UNKNOWN} | {UNKNOWN} | {UNKNOWN} | {UNKNOWN} | {UNKNOWN} |")

    lines += [
        "",
        "## Current requote / alert state",
        "",
        f"- State: **{_md(requote.get('state'))}**",
        f"- Headline: {_md(requote.get('headline'))}",
        f"- Markets requiring human action: {_md(requote.get('markets_requiring_action'))}",
        f"- Kill criteria named by requote evaluator: {_md(requote.get('kill_criteria_triggered') or 'none')}",
        "",
        "## Kill scoreboard",
        "",
        f"Overall: **{_md(kill.get('status'))}**; triggered: {_md(kill.get('triggered') or 'none')}",
        "",
        "| Criterion | Triggered | Observed | Threshold |",
        "|---|---:|---:|---:|",
    ]
    criteria = kill.get("criteria") if isinstance(kill.get("criteria"), list) else []
    if criteria:
        for row in criteria:
            lines.append(
                f"| {_md(row.get('criterion'))} | {_md(row.get('triggered'))} | "
                f"{_md(row.get('observed'))} | {_md(row.get('threshold'))} |"
            )
    else:
        lines.append(f"| {UNKNOWN} | {UNKNOWN} | {UNKNOWN} | {UNKNOWN} |")

    lines += [
        "",
        "## Yesterday's reconciliation",
        "",
        f"- Date: {_md(reconciliation.get('snapshot_date'))}",
        f"- State: **{_md(reconciliation.get('state'))}**",
        f"- Internal / data API / on-chain NAV: {_money(reconciliation.get('internal_nav_usd'))} / "
        f"{_money(reconciliation.get('data_api_nav_usd'))} / {_money(reconciliation.get('onchain_nav_usd'))}",
        f"- Unexplained discrepancy: {_money(reconciliation.get('unexplained_discrepancy_usd'))}",
        "",
        "## Cost ledger delta",
        "",
        f"- State: **{_md(costs.get('state'))}**",
        f"- Today ({_md(costs.get('today_date'))}): {_money(costs.get('today_usd'))}",
        f"- Yesterday ({_md(costs.get('yesterday_date'))}): {_money(costs.get('yesterday_usd'))}",
        f"- Delta versus yesterday: {_money(costs.get('delta_vs_yesterday_usd'))}",
        "",
        "## A1 excess-balance sweep advisory",
        "",
        f"- State: **{_md(sweep.get('status'))}**",
        f"- Reconciled NAV / active stage cap: {_money(sweep.get('reconciled_nav_usd'))} / "
        f"{_money(sweep.get('active_stage_cap_usd'))}",
        f"- Registered trigger buffer: {_money(sweep.get('sweep_threshold_usd'))}",
        f"- Human advice: **{_md(sweep.get('advice'))}**",
        "- This is reporting only; the system cannot initiate the withdrawal.",
        "",
        "## Actions recorded today",
        "",
        "| UTC | Action | Market | Side | Price | Size | Note |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    actions = payload.get("actions_today") if isinstance(payload.get("actions_today"), list) else []
    if actions:
        for row in actions:
            lines.append(
                f"| {_md(row.get('logged_at_utc'))} | {_md(row.get('action_type'))} | {_md(row.get('market_id'))} | "
                f"{_md(row.get('side'))} | {_md(row.get('price'))} | {_md(row.get('size_shares'))} | {_md(row.get('note'))} |"
            )
    else:
        lines.append(f"| {UNKNOWN} | no actions recorded | {UNKNOWN} | {UNKNOWN} | {UNKNOWN} | {UNKNOWN} | {UNKNOWN} |")

    lines += ["", "## A1 sequencing and balance discipline", ""]
    lines.extend(f"- {item}" for item in A1_REMINDERS)
    lines += ["", "## Kill-criteria response", ""]
    lines.extend(f"{index}. {item}" for index, item in enumerate(KILL_RESPONSE_STEPS, start=1))
    lines += [
        "",
        "Full operator procedure: `docs/HUMAN_STAGE1_OPERATOR_RUNBOOK.md`.",
        "",
    ]
    return "\n".join(lines)


def build_stage_day(cfg: EngineConfig, *, stage_date: str | None = None) -> dict[str, Any]:
    settings = _settings(cfg)
    target = _target_date(stage_date)
    generated_at = now_utc()
    summary_path = _output_path(cfg, settings["summary_file"])
    page_dir = _output_path(cfg, settings["page_directory"])
    page_path = page_dir / f"{target.isoformat()}.md"
    ledger_path = _output_path(cfg, settings["action_ledger_file"])
    payload: dict[str, Any] = {
        "work_order": WORK_ORDER,
        "generated_at_utc": generated_at,
        "stage_date": target.isoformat(),
        "status": "disabled",
        "page_path": str(page_path),
        "action_ledger_path": str(ledger_path),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
        "order_placement_invoked": False,
        "order_amendment_invoked": False,
        "order_cancellation_invoked": False,
        "credential_loading_invoked": False,
    }
    if not _enabled(settings.get("enabled", True)):
        write_json(summary_path, payload)
        _atomic_write_text(page_path, _render_markdown(payload))
        return payload

    maker_path = cfg.output_root / "maker_carry" / "maker_carry_study.json"
    requote_path = cfg.output_root / "maker_carry" / "requote_alerts.json"
    policy_path = cfg.output_root / "maker_carry" / "decision_policy.json"
    maker = _mapping(read_json(maker_path, default={}) or {})
    requote = _mapping(read_json(requote_path, default={}) or {})
    policy = _mapping(read_json(policy_path, default={}) or {})
    missing_inputs = [
        name
        for name, value in (("maker_carry_study", maker), ("requote_alerts", requote), ("decision_policy", policy))
        if not value
    ]
    payload.update(
        {
            "status": "ok" if not missing_inputs else "incomplete_unknown_inputs",
            "missing_inputs": missing_inputs,
            "order_tickets": _ticket_view(maker),
            "requote": _requote_view(requote),
            "kill_scoreboard": _kill_view(policy),
            "yesterday_reconciliation": _yesterday_reconciliation(cfg, target),
            "cost_delta": _cost_delta(cfg, target),
            "a1_sweep_advisory": build_a1_sweep_advisory(cfg, as_of=generated_at),
            "actions_today": _actions_for_date(ledger_path, target),
            "a1_reminders": A1_REMINDERS,
            "kill_response_steps": KILL_RESPONSE_STEPS,
            "action_log_append_only": True,
            "action_log_anchor_registry_path": "execution/stage_operator_log.csv",
        }
    )
    write_json(summary_path, payload)
    _atomic_write_text(page_path, _render_markdown(payload))
    return payload


def record_stage_action(
    cfg: EngineConfig,
    *,
    action_type: str,
    stage_date: str | None = None,
    action_at_utc: str | None = None,
    market_id: str = "",
    side: str = "n/a",
    price: float | None = None,
    size_shares: float | None = None,
    note: str = "",
) -> dict[str, Any]:
    settings = _settings(cfg)
    if not _enabled(settings.get("enabled", True)):
        raise ValueError("stage_operator is disabled")
    target = _target_date(stage_date)
    action = str(action_type or "").strip().lower()
    if action not in ACTION_TYPES:
        raise ValueError(f"action_type must be one of: {', '.join(ACTION_TYPES)}")
    side_text = str(side or "n/a").strip().lower()
    if side_text not in ACTION_SIDES:
        raise ValueError(f"side must be one of: {', '.join(ACTION_SIDES)}")
    market_text = str(market_id or "").strip()
    if action in {"quote_placed", "quote_pulled", "quote_repriced", "size_changed"} and not market_text:
        raise ValueError(f"{action} requires market_id")
    parsed_price = safe_float(price)
    parsed_size = safe_float(size_shares)
    if parsed_price is not None and not 0 < parsed_price < 1:
        raise ValueError("price must be strictly between 0 and 1")
    if parsed_size is not None and parsed_size <= 0:
        raise ValueError("size_shares must be positive")
    if action in {"quote_placed", "quote_repriced"} and (parsed_price is None or parsed_size is None):
        raise ValueError(f"{action} requires price and size_shares")
    if action in {"quote_placed", "quote_repriced"} and side_text == "n/a":
        raise ValueError(f"{action} requires bid, ask, or both side")
    if action == "size_changed" and parsed_size is None:
        raise ValueError("size_changed requires size_shares")
    if action == "note" and not str(note or "").strip():
        raise ValueError("note action requires note text")

    logged_at = str(action_at_utc or now_utc()).strip()
    parsed_at = parse_timestamp(logged_at)
    if parsed_at is None:
        raise ValueError("action_at_utc must be an ISO UTC timestamp")
    parsed_at = parsed_at.astimezone(timezone.utc)
    if parsed_at.date() != target:
        raise ValueError("action timestamp date must match stage_date")

    page_dir = _output_path(cfg, settings["page_directory"])
    page_path = page_dir / f"{target.isoformat()}.md"
    if not page_path.exists():
        raise ValueError("generate the stage-day page before recording an action")
    page_sha = hashlib.sha256(page_path.read_bytes()).hexdigest()
    ledger_path = _output_path(cfg, settings["action_ledger_file"])
    row = {
        "entry_id": str(uuid4()),
        "logged_at_utc": parsed_at.isoformat().replace("+00:00", "Z"),
        "stage_date": target.isoformat(),
        "actor": "human_operator",
        "action_type": action,
        "market_id": market_text,
        "side": side_text,
        "price": parsed_price,
        "size_shares": parsed_size,
        "note": str(note or "").strip(),
        "page_path": str(page_path),
        "page_sha256": page_sha,
        "human_action_recorded": True,
        "system_order_action_invoked": False,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    with runtime_lock(cfg, "stage_operator_log", stale_after_seconds=float(settings["lock_stale_seconds"])) as lock:
        if not lock.acquired:
            raise RuntimeError(f"stage operator log is locked: {lock.as_dict()}")
        append_csv_rows(ledger_path, [row], fieldnames=ACTION_FIELDS)
    return row


def run_stage_day(
    cfg: EngineConfig,
    *,
    stage_date: str | None = None,
    action_type: str | None = None,
    action_at_utc: str | None = None,
    market_id: str = "",
    side: str = "n/a",
    price: float | None = None,
    size_shares: float | None = None,
    note: str = "",
) -> dict[str, Any]:
    payload = build_stage_day(cfg, stage_date=stage_date)
    if not action_type:
        return payload
    recorded = record_stage_action(
        cfg,
        action_type=action_type,
        stage_date=stage_date,
        action_at_utc=action_at_utc,
        market_id=market_id,
        side=side,
        price=price,
        size_shares=size_shares,
        note=note,
    )
    payload = build_stage_day(cfg, stage_date=stage_date)
    payload["recorded_action"] = recorded
    write_json(_output_path(cfg, _settings(cfg)["summary_file"]), payload)
    return payload


def main(config_path: str) -> dict[str, Any]:
    return run_stage_day(load_config(config_path))
