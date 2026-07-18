"""WO-99 executable stage-ticket eligibility and optional owner push.

This module is reporting-only.  It evaluates the four owner-registered
conditions from artifacts that already exist, writes one atomic current-state
artifact, and may deliver one fixed ntfy message on a verified
``not_eligible -> eligible`` transition.  No gate, sizing, broker, signer, or
order path reads this output.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import math
import os
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit
import urllib.request

from .config import EngineConfig
from .utils import now_utc, parse_timestamp, read_csv_rows, read_json, safe_float, write_json


WORK_ORDER = "WO-99"
OUTPUT_FILE = "maker_carry/stage_ticket_eligibility.json"
OWNER_ALERT_FILE = "maker_carry/stage_ticket_owner_alert.json"
NTFY_ENVIRONMENT_VARIABLE = "OPS_OWNER_NTFY_TOPIC_URL"
NTFY_MESSAGE = "Polymarket stage ticket eligible - read the quote sheet."
STAGE_CAPITAL_LIMIT_USD = 100.0
TOXICITY_LIMIT = 0.9
EVENT_START_GUARD_HOURS = 48.0
NTFY_TIMEOUT_SECONDS = 10.0

CONDITION_ORDER = (
    "funding_action_and_kill_clear",
    "current_candidate_risk_controls",
    "minimum_quote_capital_within_stage",
    "fresh_kill_inputs_and_reconciliation",
)

NtfySender = Callable[[str, str, float], int]
LOGGER = logging.getLogger(__name__)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _finite(value: Any) -> float | None:
    parsed = safe_float(value)
    if parsed is None or not math.isfinite(parsed):
        return None
    return parsed


def _utc(value: Any) -> datetime | None:
    parsed = parse_timestamp(value)
    return parsed.astimezone(timezone.utc) if parsed is not None else None


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _identifiers(row: Mapping[str, Any]) -> set[str]:
    return {
        str(row.get(key) or "").strip()
        for key in (
            "condition_id",
            "market_id",
            "market_slug",
            "question",
            "token_id",
            "complement_token_id",
        )
        if str(row.get(key) or "").strip()
    }


def _named_current_candidate(
    study: Mapping[str, Any],
    named_market: str,
) -> tuple[dict[str, Any], int]:
    portfolio = study.get("portfolio") if isinstance(study.get("portfolio"), list) else []
    matches = [
        _mapping(row)
        for row in portfolio
        if isinstance(row, Mapping) and named_market and named_market in _identifiers(row)
    ]
    return (matches[0] if len(matches) == 1 else {}), len(matches)


def _toxicity_observation(
    cfg: EngineConfig,
    candidate: Mapping[str, Any],
    *,
    as_of: datetime,
) -> dict[str, Any]:
    direct_score = _finite(candidate.get("toxicity_score"))
    direct_at = _utc(candidate.get("toxicity_generated_at_utc"))
    direct_declared = any(
        candidate.get(key) is not None and str(candidate.get(key)).strip() != ""
        for key in ("toxicity_score", "toxicity_generated_at_utc")
    )
    if direct_declared:
        timestamp_valid = bool(direct_at is not None and direct_at <= as_of)
        current_utc_day = bool(direct_at is not None and direct_at.date() == as_of.date())
        return {
            "available": bool(
                direct_score is not None
                and 0.0 <= direct_score <= 1.0
                and timestamp_valid
                and current_utc_day
            ),
            "score": direct_score,
            "generated_at_utc": _stamp(direct_at) if direct_at is not None else None,
            "source": "maker_carry_study.portfolio",
            "future_dated": bool(direct_at is not None and direct_at > as_of),
            "current_utc_day": current_utc_day,
        }

    condition_id = str(candidate.get("condition_id") or "").strip()
    token_ids = {
        str(candidate.get(key) or "").strip()
        for key in ("token_id", "complement_token_id")
        if str(candidate.get(key) or "").strip()
    }
    observations: list[tuple[datetime, float]] = []
    future_dated = False
    for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity.csv"):
        market = str(row.get("market") or row.get("condition_id") or "").strip()
        asset = str(row.get("asset_id") or "").strip()
        if not ((condition_id and market == condition_id) or (asset and asset in token_ids)):
            continue
        score = _finite(row.get("toxicity_score"))
        generated_at = _utc(row.get("generated_at_utc"))
        if score is None or generated_at is None or not 0.0 <= score <= 1.0:
            continue
        if generated_at > as_of:
            future_dated = True
            continue
        observations.append((generated_at, score))
    if not observations:
        return {
            "available": False,
            "score": None,
            "generated_at_utc": None,
            "source": "maker_carry/flow_toxicity.csv",
            "future_dated": future_dated,
            "current_utc_day": False,
        }
    latest_at = max(item[0] for item in observations)
    latest_scores = [score for stamp, score in observations if stamp == latest_at]
    current_utc_day = latest_at.date() == as_of.date()
    return {
        "available": current_utc_day,
        # Multiple token rows at the same observation time resolve in the
        # conservative direction: the highest measured toxicity governs.
        "score": max(latest_scores),
        "generated_at_utc": _stamp(latest_at),
        "source": "maker_carry/flow_toxicity.csv",
        "future_dated": future_dated,
        "current_utc_day": current_utc_day,
    }


def _scheduled_risk(candidate: Mapping[str, Any], *, as_of: datetime) -> dict[str, Any]:
    source = "event_start_time_utc"
    raw = candidate.get(source)
    if not str(raw or "").strip():
        # Existing WO-66 safety semantics use market close as a conservative
        # fallback when Gamma exposes no separate gameStartTime.
        source = "end_date_utc"
        raw = candidate.get(source)
    scheduled_at = _utc(raw)
    hours_until = (
        (scheduled_at - as_of).total_seconds() / 3600.0
        if scheduled_at is not None
        else None
    )
    return {
        "available": scheduled_at is not None,
        "source_field": source if scheduled_at is not None else None,
        "scheduled_at_utc": _stamp(scheduled_at) if scheduled_at is not None else None,
        "hours_until": round(hours_until, 6) if hours_until is not None else None,
        # Exactly 48h is inside the registered guard. Past events also fail.
        "outside_guard": bool(hours_until is not None and hours_until > EVENT_START_GUARD_HOURS),
    }


def _minimum_quote_capital(candidate: Mapping[str, Any]) -> dict[str, Any]:
    minimum_size = _finite(candidate.get("rewards_min_size_shares"))
    minimum_raw = candidate.get("rewards_min_size_shares")
    minimum_declared = minimum_raw is not None and str(minimum_raw).strip() != ""
    if not minimum_declared:
        quote_size = _finite(candidate.get("quote_size_shares"))
        size_multiple = _finite(candidate.get("size_multiple"))
        if quote_size is not None and size_multiple is not None and size_multiple > 0:
            minimum_size = quote_size / size_multiple
    midpoint = _finite(candidate.get("mid_price"))
    midpoint_raw = candidate.get("mid_price")
    midpoint_declared = midpoint_raw is not None and str(midpoint_raw).strip() != ""
    if not midpoint_declared:
        midpoint = _finite(candidate.get("reference_mid_price"))
    inputs_complete = bool(
        minimum_size is not None
        and minimum_size > 0
        and midpoint is not None
        and 0 < midpoint < 1
    )
    capital = minimum_size * 2.0 * midpoint if inputs_complete else None
    return {
        "inputs_complete": inputs_complete,
        "rewards_min_size_shares": round(minimum_size, 8) if minimum_size is not None else None,
        "mid_price": round(midpoint, 8) if midpoint is not None else None,
        "minimum_quote_capital_usd": round(capital, 8) if capital is not None else None,
        "stage_capital_limit_usd": STAGE_CAPITAL_LIMIT_USD,
        "within_stage": bool(capital is not None and capital <= STAGE_CAPITAL_LIMIT_USD),
    }


def _reconciliation(cfg: EngineConfig, *, as_of: datetime) -> dict[str, Any]:
    payload = read_json(
        cfg.output_root / "performance" / "wallet_reconciliation.json",
        default={},
    ) or {}
    if not isinstance(payload, Mapping):
        payload = {}
    state = str(payload.get("reconciliation_status") or "").strip().lower()
    generated_at = _utc(payload.get("generated_at_utc"))
    timestamp_valid = bool(generated_at is not None and generated_at <= as_of)
    current_utc_day = bool(generated_at is not None and generated_at.date() == as_of.date())
    return {
        "state": state or "missing",
        "allowed": bool(
            state in {"clean", "explained"}
            and timestamp_valid
            and current_utc_day
        ),
        "timestamp_valid": timestamp_valid,
        "current_utc_day": current_utc_day,
        "generated_at_utc": _stamp(generated_at) if generated_at is not None else None,
        "source": "performance/wallet_reconciliation.json",
    }


def _post_ntfy(topic_url: str, message: str, timeout_seconds: float) -> int:
    parsed = urlsplit(topic_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("invalid_ntfy_topic_url")
    request = urllib.request.Request(
        topic_url,
        data=message.encode("utf-8"),
        headers={
            "Content-Type": "text/plain; charset=utf-8",
            "User-Agent": "polymarket-stage-ticket/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # nosec B310 - owner-provided ntfy topic
        raw_status = getattr(response, "status", None)
        if raw_status is None:
            raw_status = response.getcode()
        status = int(raw_status)
    if not 200 <= status < 300:
        raise RuntimeError("ntfy_non_success_status")
    return status


def _notification_result(*, transition: bool, topic_configured: bool) -> dict[str, Any]:
    if not transition:
        state = "unchanged_no_push"
    elif not topic_configured:
        state = "dashboard_only_env_unset"
    else:
        state = "delivery_pending"
    return {
        "transition": "not_eligible_to_eligible" if transition else "none",
        "topic_configured": topic_configured,
        "attempted": False,
        "delivered": False,
        "delivery_state": state,
        "http_status": None,
        "error_type": None,
        "message": NTFY_MESSAGE,
    }


def evaluate_stage_ticket_eligibility(
    cfg: EngineConfig,
    *,
    decision_policy: Mapping[str, Any],
    study: Mapping[str, Any] | None = None,
    as_of: datetime | str | None = None,
    ntfy_sender: NtfySender | None = None,
) -> dict[str, Any]:
    """Evaluate and persist the registered reporting-only eligibility state."""

    decision = _mapping(decision_policy)
    study_payload = _mapping(
        study
        if study is not None
        else read_json(
            cfg.output_root / "maker_carry" / "maker_carry_study.json",
            default={},
        )
    )
    clock = _utc(as_of) or _utc(decision.get("generated_at_utc")) or _utc(now_utc())
    if clock is None:  # pragma: no cover - now_utc is guaranteed parseable
        raise RuntimeError("stage-ticket clock did not produce a valid UTC timestamp")
    generated_at = _stamp(clock)
    output_path = cfg.output_root / OUTPUT_FILE
    alert_path = cfg.output_root / OWNER_ALERT_FILE
    previous = read_json(output_path, default={}) or {}
    previous_state = (
        str(previous.get("state") or "missing").strip().lower()
        if isinstance(previous, Mapping)
        else "malformed"
    )

    policy_stamp = _utc(decision.get("generated_at_utc"))
    action = str(decision.get("indicated_action") or "").strip()
    kill = _mapping(decision.get("kill_criteria_status"))
    kill_status = str(kill.get("status") or "").strip().lower()
    policy_condition = {
        "passed": bool(
            str(decision.get("status") or "").lower() == "ok"
            and policy_stamp is not None
            and policy_stamp == clock
            and action.startswith("fund_")
            and kill_status == "clear"
        ),
        "decision_status_ok": str(decision.get("status") or "").lower() == "ok",
        "policy_clock_valid": bool(policy_stamp is not None and policy_stamp == clock),
        "funding_action": action.startswith("fund_"),
        "kill_clear": kill_status == "clear",
    }

    snapshot = _mapping(decision.get("inputs_snapshot"))
    study_stamp_raw = study_payload.get("generated_at_utc")
    study_stamp = _utc(study_stamp_raw)
    snapshot_study_stamp = str(
        snapshot.get("maker_carry_study_generated_at_utc") or ""
    ).strip()
    study_current = bool(
        study_stamp is not None
        and study_stamp <= clock
        and study_stamp.date() == clock.date()
        and snapshot_study_stamp
        and snapshot_study_stamp == str(study_stamp_raw or "").strip()
    )
    composition = _mapping(decision.get("composition_stability"))
    named_market = str(composition.get("most_recurrent_market") or "").strip()
    candidate, match_count = _named_current_candidate(study_payload, named_market)
    toxicity = _toxicity_observation(cfg, candidate, as_of=clock) if candidate else {
        "available": False,
        "score": None,
        "generated_at_utc": None,
        "source": "maker_carry/flow_toxicity.csv",
        "future_dated": False,
    }
    scheduled = _scheduled_risk(candidate, as_of=clock) if candidate else {
        "available": False,
        "source_field": None,
        "scheduled_at_utc": None,
        "hours_until": None,
        "outside_guard": False,
    }
    resolution_risk = str(candidate.get("resolution_risk") or "").strip().lower()
    toxicity_score = _finite(toxicity.get("score"))
    candidate_condition = {
        "passed": bool(
            study_current
            and match_count == 1
            and toxicity.get("available")
            and toxicity_score is not None
            and toxicity_score <= TOXICITY_LIMIT
            and resolution_risk in {"low", "medium"}
            and scheduled.get("available")
            and scheduled.get("outside_guard")
        ),
        "study_snapshot_matches_policy_run": study_current,
        "named_candidate_matches_in_current_portfolio": match_count == 1,
        "candidate_match_count": match_count,
        "toxicity_available": bool(toxicity.get("available")),
        "toxicity_within_limit": bool(
            toxicity_score is not None and toxicity_score <= TOXICITY_LIMIT
        ),
        "toxicity_score": toxicity_score,
        "toxicity_limit": TOXICITY_LIMIT,
        "toxicity_generated_at_utc": toxicity.get("generated_at_utc"),
        "toxicity_source": toxicity.get("source"),
        "toxicity_current_utc_day": bool(toxicity.get("current_utc_day")),
        "resolution_risk_acceptable": resolution_risk in {"low", "medium"},
        "resolution_risk": resolution_risk or "missing",
        "scheduled_time_available": bool(scheduled.get("available")),
        "outside_48h_guard": bool(scheduled.get("outside_guard")),
        "scheduled_time_source": scheduled.get("source_field"),
        "scheduled_at_utc": scheduled.get("scheduled_at_utc"),
        "hours_until_scheduled_time": scheduled.get("hours_until"),
    }

    capital = _minimum_quote_capital(candidate) if candidate else {
        "inputs_complete": False,
        "rewards_min_size_shares": None,
        "mid_price": None,
        "minimum_quote_capital_usd": None,
        "stage_capital_limit_usd": STAGE_CAPITAL_LIMIT_USD,
        "within_stage": False,
    }
    capital_condition = {
        "passed": bool(match_count == 1 and capital.get("inputs_complete") and capital.get("within_stage")),
        **capital,
    }

    freshness = _mapping(kill.get("kill_input_freshness"))
    freshness_state = str(freshness.get("state") or "").strip().lower()
    reconciliation = _reconciliation(cfg, as_of=clock)
    safety_condition = {
        "passed": bool(freshness_state == "fresh" and reconciliation["allowed"]),
        "kill_input_fresh": freshness_state == "fresh",
        "kill_input_freshness_state": freshness_state or "missing",
        "reconciliation_allowed": reconciliation["allowed"],
        "reconciliation_state": reconciliation["state"],
        "reconciliation_timestamp_valid": reconciliation["timestamp_valid"],
        "reconciliation_current_utc_day": reconciliation["current_utc_day"],
        "reconciliation_generated_at_utc": reconciliation["generated_at_utc"],
        "reconciliation_source": reconciliation["source"],
    }

    conditions = {
        CONDITION_ORDER[0]: policy_condition,
        CONDITION_ORDER[1]: candidate_condition,
        CONDITION_ORDER[2]: capital_condition,
        CONDITION_ORDER[3]: safety_condition,
    }
    first_failure = next(
        (name for name in CONDITION_ORDER if not conditions[name]["passed"]),
        None,
    )
    state = "eligible" if first_failure is None else "not_eligible"
    transition = previous_state == "not_eligible" and state == "eligible"
    topic_url = str(os.environ.get(NTFY_ENVIRONMENT_VARIABLE) or "").strip()
    notification = _notification_result(
        transition=transition,
        topic_configured=bool(topic_url),
    )
    owner_alert_written = False
    if transition:
        write_json(
            alert_path,
            {
                "work_order": WORK_ORDER,
                "generated_at_utc": generated_at,
                "event": "stage_ticket_became_eligible",
                "transition": "not_eligible_to_eligible",
                "message": NTFY_MESSAGE,
                "decision_policy_generated_at_utc": decision.get("generated_at_utc"),
                "read_only": True,
                "paper_trading_invoked": False,
                "live_trading_invoked": False,
                "order_placement_invoked": False,
            },
        )
        owner_alert_written = True

    payload = {
        "work_order": WORK_ORDER,
        "status": "ok",
        "state": state,
        "generated_at_utc": generated_at,
        "previous_state": previous_state,
        "state_transition": "not_eligible_to_eligible" if transition else "none",
        "decision_policy_generated_at_utc": decision.get("generated_at_utc"),
        "maker_carry_study_generated_at_utc": study_payload.get("generated_at_utc"),
        "conditions": conditions,
        "first_failing_condition": first_failure,
        "notification": notification,
        "owner_alert_written": owner_alert_written,
        "owner_alert_artifact": OWNER_ALERT_FILE if alert_path.exists() else None,
        "decision_use": "owner notification and dashboard reporting only; never gate, size, fund, or trade",
        "fail_safe": "missing, stale, future-dated, or malformed inputs evaluate not_eligible",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
        "order_placement_invoked": False,
        "order_amendment_invoked": False,
        "order_cancellation_invoked": False,
    }

    # Persist the new state before the external attempt. A process failure can
    # therefore miss a push but cannot repeat one on the next policy cycle.
    # This artifact and the owner alert are disjoint new paths; all writes are
    # atomic, and no existing writer touches either path.
    write_json(output_path, payload)
    if transition and topic_url:
        notification["attempted"] = True
        notification["delivery_state"] = "delivery_attempted"
        write_json(output_path, payload)
        try:
            status = (ntfy_sender or _post_ntfy)(
                topic_url,
                NTFY_MESSAGE,
                NTFY_TIMEOUT_SECONDS,
            )
            notification.update(
                {
                    "delivered": True,
                    "delivery_state": "delivered",
                    "http_status": int(status),
                }
            )
        except Exception as exc:  # notification is deliberately fail-soft
            notification.update(
                {
                    "delivered": False,
                    "delivery_state": "failed_non_blocking",
                    # Never persist exception text: URL-bearing transport
                    # errors can echo the private ntfy topic.
                    "error_type": type(exc).__name__,
                }
            )
            LOGGER.warning(
                "WO-99 stage-ticket owner push failed non-blocking (%s)",
                type(exc).__name__,
            )
        write_json(output_path, payload)
    return payload
