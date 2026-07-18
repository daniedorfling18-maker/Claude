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
NTFY_REVOKE_MESSAGE = "Polymarket stage ticket NO LONGER eligible - do not act on the prior notice."
HEALTHY_RECONCILIATION = {"clean", "explained"}

# WO-103 (2026-07-17): the experiment registry H1 requires exact-token
# sharp-anchor qualification (M-gates "necessary but not sufficient"), while
# the WO-93-revert record states generic reward carry may be funded. These
# are contradictory governing instructions. Per fail-closed policy, the owner
# notification cannot fire until ONE dated owner decision reconciles them.
# The owner flips this to True as part of that reconciliation; it is the only
# lever, and it holds even an otherwise-eligible ticket.
FUNDING_GOVERNANCE_RECONCILED = False
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

    rows.append(
        _condition(
            "funding_governance_reconciled",
            bool(FUNDING_GOVERNANCE_RECONCILED),
            "registry H1 sharp-anchor requirement vs WO-93-revert generic-carry "
            "record must be reconciled by one dated owner decision before funding",
        )
    )

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

    # WO-104 item 2: same-run coherence. The decision policy records the study
    # run it consumed; require it to match the current study so a portfolio
    # that churned between the policy and study writes (the observed ~18-min
    # skew) cannot produce an eligible read from mismatched artifacts.
    study_generated = str(study.get("generated_at_utc") or "").strip()
    policy_study_ref = str(
        (policy.get("inputs_snapshot") or {}).get("maker_carry_study_generated_at_utc") or ""
    ).strip()
    coherent = bool(study_generated) and study_generated == policy_study_ref
    rows.append(
        _condition(
            "policy_and_study_same_run",
            coherent,
            f"study_generated={study_generated or 'missing'}; policy_consumed={policy_study_ref or 'missing'}",
        )
    )

    # WO-104 item 6: resolve the study-vs-safety contradiction at the funding
    # decision. The requote system may flag a candidate market pull_quotes_now
    # (e.g. past/imminent event start) while the study still assigns it carry.
    # Never fund a market safety is actively telling the human to pull; require
    # a present, non-pull requote state for the exact candidate (fail-closed).
    requote = read_json(out_root / "requote_alerts.json", default={}) or {}
    requote_state = None
    for row in requote.get("markets") or []:
        if isinstance(row, dict) and str(row.get("condition_id") or "").strip() == candidate_id:
            requote_state = str(row.get("alert_state") or "").strip()
            break
    rows.append(
        _condition(
            "requote_not_pulling_candidate",
            requote_state is not None and requote_state not in {"pull_quotes_now", "STOP"},
            f"candidate_requote_state={requote_state or 'missing'}",
        )
    )

    state = "eligible" if rows and all(row["passed"] for row in rows) else "not_eligible"
    return state, rows, summary


def _notify(url: str, message: str) -> dict[str, Any]:
    try:
        response = requests.post(url, data=message, timeout=10)
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
    previous_candidate = str(previous.get("candidate_condition_id") or "")
    state, rows, summary = evaluate_stage_ticket_eligibility(cfg, as_of=as_of)
    first_failing = next((row["condition"] for row in rows if not row["passed"]), None)
    candidate_id = str(summary.get("candidate_condition_id") or "")

    # WO-104 item 1: key transitions on (state, candidate) so a candidate
    # change while still "eligible" (e.g. Iran -> WTI) is not silently
    # swallowed, and send a revocation when a previously-eligible ticket
    # stops being eligible so the owner is never left holding a stale "go".
    became_eligible = previous_state != "eligible" and state == "eligible"
    candidate_changed_while_eligible = (
        previous_state == "eligible" and state == "eligible" and candidate_id != previous_candidate
    )
    eligible_event = became_eligible or candidate_changed_while_eligible
    revoked_event = previous_state == "eligible" and state != "eligible"

    url = str(os.environ.get(NTFY_ENV_VAR) or "").strip()
    notification: dict[str, Any] = {"attempted": False, "channel_configured": bool(url)}
    if url and (eligible_event or revoked_event or send_test):
        message = NTFY_REVOKE_MESSAGE if (revoked_event and not send_test) else NTFY_MESSAGE
        notification = {
            **_notify(url, message),
            "channel_configured": True,
            "test": bool(send_test),
            "kind": "revocation" if (revoked_event and not send_test) else "eligible",
        }

    payload = {
        "work_order": WORK_ORDER,
        "generated_at_utc": now_utc(),
        "state": state,
        "previous_state": previous_state,
        "candidate_condition_id": candidate_id or None,
        "previous_candidate_condition_id": previous_candidate or None,
        "transitioned_to_eligible": eligible_event,
        "candidate_changed_while_eligible": candidate_changed_while_eligible,
        "revoked": revoked_event,
        "first_failing_condition": first_failing,
        "conditions": rows,
        "candidate": summary,
        "notification": notification,
        "note": "reporting only; no gate, sizing, or order surface reads this artifact",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    write_json(path, payload)
    if eligible_event or revoked_event:
        alert_path = cfg.output_root / ALERT_RELATIVE
        alert_path.parent.mkdir(parents=True, exist_ok=True)
        if revoked_event:
            body = (
                "# Stage ticket NO LONGER eligible\n\n"
                f"Generated: `{payload['generated_at_utc']}`\n\n"
                f"A previously-eligible ticket is now not_eligible "
                f"(first failing: {first_failing}). Do not act on the prior notice.\n"
            )
        else:
            body = (
                "# Stage ticket eligible\n\n"
                f"Generated: `{payload['generated_at_utc']}`\n\n"
                "All registered WO-99 conditions passed. Read the quote sheet before acting.\n"
                "Human decision only; the system never trades.\n"
            )
        alert_path.write_text(body, encoding="utf-8")
    return payload


def main(config_path: str) -> dict[str, Any]:
    return run_stage_ticket_eligibility(load_config(config_path))
