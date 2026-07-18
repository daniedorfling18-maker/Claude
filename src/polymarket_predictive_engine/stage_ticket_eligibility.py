"""WO-99 owner notification when a stage ticket becomes executable.

Evaluates the registered eligibility conditions from existing artifacts only
and, on the not_eligible -> eligible TRANSITION, sends one fixed push message
to the ntfy topic named by the OPS_OWNER_NTFY_TOPIC_URL environment variable
(VPS .env only; never config, telemetry, or repo). Reporting only: no gate,
sizing, or order surface reads this artifact. Missing, stale, or malformed
inputs evaluate not_eligible (fail-closed).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from .config import EngineConfig, load_config
from .utils import now_utc, parse_timestamp, read_csv_rows, read_json, safe_float, write_json

WORK_ORDER = "WO-99"
OUTPUT_RELATIVE = "maker_carry/stage_ticket_eligibility.json"
ALERT_RELATIVE = "ops_scheduler/stage_ticket_notification.md"
STAGE_BUDGET_USD = 100.0
MAX_TOXICITY = 0.9
MAX_RAW_IMBALANCE = 0.9
EVENT_START_BUFFER_HOURS = 48.0
NTFY_ENV_VAR = "OPS_OWNER_NTFY_TOPIC_URL"
NTFY_MESSAGE = "Polymarket stage ticket eligible - read the quote sheet."
HEALTHY_RECONCILIATION = {"clean", "explained"}
HEALTHY_FRESHNESS = {"fresh", "inactive_pre_live"}


def _condition(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"condition": name, "passed": bool(passed), "detail": detail}


def evaluate_stage_ticket_eligibility(
    cfg: EngineConfig,
    *,
    as_of: datetime | None = None,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Return (state, per-condition rows, candidate summary)."""

    now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    out_root = cfg.output_root / "maker_carry"
    policy = read_json(out_root / "decision_policy.json", default={}) or {}
    study = read_json(out_root / "maker_carry_study.json", default={}) or {}
    reconciliation = read_json(
        cfg.output_root / "performance" / "wallet_reconciliation.json", default={}
    ) or {}
    toxicity_rows = {
        str(row.get("market") or ""): row
        for row in read_csv_rows(out_root / "flow_toxicity.csv")
        if row.get("market")
    }

    rows: list[dict[str, Any]] = []

    indicated = str(policy.get("indicated_action") or "")
    kill_status = str((policy.get("kill_criteria_status") or {}).get("status") or "")
    rows.append(
        _condition(
            "fund_action_indicated_and_kill_clear",
            indicated.startswith("fund_") and kill_status == "clear",
            f"indicated_action={indicated or 'missing'}; kill={kill_status or 'missing'}",
        )
    )

    candidate_id = str(
        (policy.get("composition_stability") or {}).get("most_recurrent_market") or ""
    ).strip()
    portfolio = study.get("portfolio") if isinstance(study.get("portfolio"), list) else []
    candidate = next(
        (
            row
            for row in portfolio
            if isinstance(row, dict) and str(row.get("condition_id") or "").strip() == candidate_id
        ),
        None,
    )
    rows.append(
        _condition(
            "candidate_in_current_portfolio",
            bool(candidate_id) and candidate is not None,
            f"candidate={candidate_id or 'missing'}; in_portfolio={candidate is not None}",
        )
    )

    summary: dict[str, Any] = {"candidate_condition_id": candidate_id or None}
    if candidate is not None:
        # WO-102 composite screen (tighten-only): block on the absolute
        # raw-imbalance floor OR the universe-relative percentile, and treat
        # an unmeasured market as blocked (fail-closed). Reading the flow
        # module's own `toxic_blocked` keeps the veto in lockstep with the
        # instrument, but the percentile is also re-checked here directly so a
        # stale/partial row cannot silently clear a candidate.
        tox_row = toxicity_rows.get(candidate_id) or {}
        percentile = safe_float(tox_row.get("toxicity_score"))
        raw_imbalance = safe_float(tox_row.get("vpin_raw"))
        flagged = str(tox_row.get("toxic_blocked") or "").strip().lower() in {"true", "1", "yes"}
        measured = bool(tox_row) and percentile is not None
        clean = (
            measured
            and not flagged
            and percentile <= MAX_TOXICITY
            and (raw_imbalance is None or raw_imbalance < MAX_RAW_IMBALANCE)
        )
        rows.append(
            _condition(
                "toxicity_screen_clear",
                clean,
                f"measured={measured}; toxic_blocked={flagged}; percentile="
                f"{percentile if percentile is not None else 'unmeasured'} (max {MAX_TOXICITY}); "
                f"raw_imbalance={raw_imbalance if raw_imbalance is not None else 'unmeasured'} "
                f"(floor {MAX_RAW_IMBALANCE})",
            )
        )
        resolution_risk = str(candidate.get("resolution_risk") or "").lower()
        rows.append(
            _condition(
                "resolution_risk_not_high",
                bool(resolution_risk) and resolution_risk != "high",
                f"resolution_risk={resolution_risk or 'missing'}",
            )
        )
        event_start = parse_timestamp(candidate.get("event_start_time_utc"))
        imminent = (
            event_start is not None
            and now < event_start <= now + timedelta(hours=EVENT_START_BUFFER_HOURS)
        )
        rows.append(
            _condition(
                "no_event_start_within_48h",
                not imminent,
                f"event_start={candidate.get('event_start_time_utc') or 'none'}",
            )
        )
        size_multiple = safe_float(candidate.get("size_multiple"))
        capital = safe_float(candidate.get("capital_usd"))
        min_capital = (
            round(capital / size_multiple, 2)
            if capital is not None and size_multiple is not None and size_multiple > 0
            else None
        )
        summary["minimum_quote_capital_usd"] = min_capital
        summary["question"] = str(candidate.get("question") or "")
        rows.append(
            _condition(
                "minimum_quote_within_stage_budget",
                min_capital is not None and min_capital <= STAGE_BUDGET_USD,
                f"min_quote_capital={min_capital if min_capital is not None else 'unknown'} vs ${STAGE_BUDGET_USD:g}",
            )
        )

    freshness = str(
        ((policy.get("kill_criteria_status") or {}).get("kill_input_freshness") or {}).get("state")
        or ""
    )
    rows.append(
        _condition(
            "kill_inputs_fresh",
            freshness in HEALTHY_FRESHNESS,
            f"kill_input_freshness={freshness or 'missing'}",
        )
    )
    reconciliation_state = str(reconciliation.get("reconciliation_status") or "").lower()
    rows.append(
        _condition(
            "reconciliation_clean_or_explained",
            reconciliation_state in HEALTHY_RECONCILIATION,
            f"reconciliation_status={reconciliation_state or 'missing'}",
        )
    )

    state = "eligible" if rows and all(row["passed"] for row in rows) else "not_eligible"
    return state, rows, summary


def _notify(url: str) -> dict[str, Any]:
    try:
        response = requests.post(url, data=NTFY_MESSAGE, timeout=10)
        return {"attempted": True, "delivered": response.status_code < 300, "status_code": response.status_code}
    except Exception as exc:  # noqa: BLE001 - send failure must never block the policy step
        return {"attempted": True, "delivered": False, "error": f"{type(exc).__name__}"}


def run_stage_ticket_eligibility(
    cfg: EngineConfig,
    *,
    as_of: datetime | None = None,
    send_test: bool = False,
) -> dict[str, Any]:
    path = cfg.output_root / OUTPUT_RELATIVE
    previous = read_json(path, default={}) or {}
    previous_state = str(previous.get("state") or "not_eligible")
    state, rows, summary = evaluate_stage_ticket_eligibility(cfg, as_of=as_of)
    first_failing = next((row["condition"] for row in rows if not row["passed"]), None)
    transitioned = previous_state != "eligible" and state == "eligible"

    url = str(os.environ.get(NTFY_ENV_VAR) or "").strip()
    notification: dict[str, Any] = {"attempted": False, "channel_configured": bool(url)}
    if url and (transitioned or send_test):
        notification = {**_notify(url), "channel_configured": True, "test": bool(send_test)}

    payload = {
        "work_order": WORK_ORDER,
        "generated_at_utc": now_utc(),
        "state": state,
        "previous_state": previous_state,
        "transitioned_to_eligible": transitioned,
        "first_failing_condition": first_failing,
        "conditions": rows,
        "candidate": summary,
        "notification": notification,
        "note": "reporting only; no gate, sizing, or order surface reads this artifact",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    write_json(path, payload)
    if transitioned:
        alert_path = cfg.output_root / ALERT_RELATIVE
        alert_path.parent.mkdir(parents=True, exist_ok=True)
        alert_path.write_text(
            "# Stage ticket eligible\n\n"
            f"Generated: `{payload['generated_at_utc']}`\n\n"
            "All registered WO-99 conditions passed. Read the quote sheet before acting.\n"
            "Human decision only; the system never trades.\n",
            encoding="utf-8",
        )
    return payload


def main(config_path: str) -> dict[str, Any]:
    return run_stage_ticket_eligibility(load_config(config_path))
