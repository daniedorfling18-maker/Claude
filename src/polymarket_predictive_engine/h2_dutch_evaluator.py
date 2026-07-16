"""WO-98 exact prospective H2 Dutch-consistency evaluator.

The live producer stores complete top-of-book basket plans, including scans
where the all-in lock is not positive.  This module reconstructs independent
opportunity episodes without treating missing data as a clear, freezes the
registered stopping sample, and evaluates that sample as shadow research
only.  Legacy gross Dutch-monitor artifacts are deliberately not inputs.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import random
from typing import Any, Iterable

from polymarket_common.fees import TAKER_FEE_MODEL_VERSION

from .config import EngineConfig, load_config
from .runtime_lock import runtime_lock
from .utils import (
    append_csv_rows,
    parse_timestamp,
    read_csv_rows,
    read_json,
    safe_float,
    write_csv,
    write_json,
)


WORK_ORDER = "WO-98"
SCHEMA_VERSION = "h2-dutch-exact-v1"
REGISTRATION_AT = datetime(2026, 7, 12, 13, 38, 47, tzinfo=timezone.utc)
REGISTERED_STOP_AT = REGISTRATION_AT + timedelta(days=60)
REGISTERED_STOP_EPISODES = 100
REGISTERED_MIN_EPISODES = 30
REGISTERED_MIN_CALENDAR_SPAN_DAYS = 14
REGISTERED_MIN_PERSISTENT_EPISODES = 10
REGISTERED_MIN_LEGS = 3
REGISTERED_MAX_LEGS = 80
REGISTERED_PERSISTENCE_MIN_GAP_SECONDS = 10 * 60
REGISTERED_PERSISTENCE_MAX_GAP_SECONDS = 20 * 60
REGISTERED_COST_RESERVE_PER_SET = 0.002
REGISTERED_MAX_EVENT_PROFIT_SHARE = 0.35
REGISTERED_BOOTSTRAP_ITERATIONS = 1000
REGISTERED_BOOTSTRAP_SEED = 20260716

OBSERVATION_RELATIVE_PATH = "h2_dutch/h2_scan_observations_v1.csv"
FINAL_RELATIVE_PATH = "h2_dutch/h2_final_episodes_v1.csv"
FINAL_MANIFEST_RELATIVE_PATH = "h2_dutch/h2_final_sample_manifest.json"
SUMMARY_RELATIVE_PATH = "h2_dutch/h2_evaluation.json"
EPISODE_STATE_RELATIVE_PATH = "h2_dutch/h2_episode_state.csv"
OBSERVATION_LOCK = "h2_scan_observations_v1_append"
FINAL_LOCK = "h2_final_episodes_v1_append"

OBSERVATION_FIELDS = [
    "work_order",
    "schema_version",
    "observation_id",
    "scan_id",
    "observed_at_utc",
    "event_id",
    "event_title",
    "leg_count",
    "token_ids_json",
    "legs_json",
    "ask_sum",
    "common_size",
    "gross_cost_per_set",
    "entry_taker_fees_per_set",
    "entry_taker_fees_usdc",
    "registered_cost_reserve_per_set",
    "all_in_cost_per_set",
    "gross_profit_per_set",
    "net_profit_per_set",
    "gross_capital_usdc",
    "all_in_capital_usdc",
    "gross_profit_usdc",
    "net_profit_usdc",
    "net_return_on_capital",
    "holding_days",
    "annualised_net_return_on_capital",
    "resolution_at_utc",
    "complete_common_size_plan",
    "qualifying",
    "qualification_reason",
    "fee_model_versions_json",
    "paper_trading_invoked",
    "live_trading_invoked",
]

EPISODE_FIELDS = [
    "episode_id",
    "event_id",
    "event_title",
    "episode_start_at_utc",
    "episode_day_utc",
    "first_observation_id",
    "first_scan_id",
    "leg_count",
    "token_ids_json",
    "common_size",
    "ask_sum",
    "entry_taker_fees_per_set",
    "entry_taker_fees_usdc",
    "registered_cost_reserve_per_set",
    "all_in_cost_per_set",
    "net_profit_per_set",
    "all_in_capital_usdc",
    "net_profit_usdc",
    "net_return_on_capital",
    "holding_days",
    "annualised_net_return_on_capital",
    "resolution_at_utc",
    "complete_common_size_plan",
    "persistent_three_scans",
    "persistence_scan_count",
    "persistence_qualified_at_utc",
    "paper_trading_invoked",
    "live_trading_invoked",
]


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc(value: Any) -> datetime | None:
    parsed = parse_timestamp(value)
    return parsed.astimezone(timezone.utc) if parsed is not None else None


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def _finite(value: Any) -> float | None:
    number = safe_float(value)
    return number if number is not None and math.isfinite(number) else None


def _same(left: float, right: float, *, tolerance: float = 1e-7) -> bool:
    return math.isclose(left, right, rel_tol=tolerance, abs_tol=tolerance)


def _json_list(value: Any) -> list[Any] | None:
    if isinstance(value, list):
        return value
    try:
        decoded = json.loads(str(value or ""))
    except (TypeError, ValueError):
        return None
    return decoded if isinstance(decoded, list) else None


def _scan_id(observed_at: str) -> str:
    return hashlib.sha256(f"{SCHEMA_VERSION}|{observed_at}".encode("utf-8")).hexdigest()


def _observation_id(scan_id: str, event_id: str, tokens: list[str]) -> str:
    material = "|".join((SCHEMA_VERSION, scan_id, event_id, *tokens))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _episode_id(row: dict[str, Any]) -> str:
    material = "|".join(
        (
            SCHEMA_VERSION,
            str(row["event_id"]),
            str(row["observed_at_utc"]),
            str(row["observation_id"]),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def build_h2_scan_observation(
    plan: Any,
    *,
    observed_at: datetime,
) -> tuple[dict[str, Any] | None, str]:
    """Convert a complete fee-bearing live plan into an exact H2 row."""

    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    observed_at = observed_at.astimezone(timezone.utc).replace(microsecond=0)
    observed_at_utc = _iso(observed_at)
    if not bool(getattr(plan, "complete", False)):
        return None, "incomplete_plan"
    legs = list(getattr(plan, "legs", ()) or ())
    if not REGISTERED_MIN_LEGS <= len(legs) <= REGISTERED_MAX_LEGS:
        return None, "invalid_leg_count"

    token_ids = [str(getattr(leg, "token_id", "") or "").strip() for leg in legs]
    if any(not token for token in token_ids) or len(set(token_ids)) != len(token_ids):
        return None, "missing_or_duplicate_token"

    resolution_at = _utc(getattr(plan, "resolution_at_utc", ""))
    if resolution_at is None:
        return None, "missing_resolution_time"
    holding_seconds = (resolution_at - observed_at).total_seconds()
    if holding_seconds <= 0:
        return None, "nonpositive_holding_time"

    exact_legs: list[dict[str, Any]] = []
    for leg in legs:
        ask = _finite(getattr(leg, "best_ask", None))
        size = _finite(getattr(leg, "ask_size", None))
        fee = _finite(getattr(leg, "taker_fee_per_share", None))
        rate = _finite(getattr(leg, "taker_fee_rate", None))
        category = str(getattr(leg, "taker_fee_category", "") or "").strip()
        source = str(getattr(leg, "taker_fee_source", "") or "").strip()
        enabled = getattr(leg, "taker_fee_enabled", None)
        model = str(getattr(leg, "taker_fee_model_version", "") or "").strip()
        if ask is None or not 0 < ask < 1 or size is None or size <= 0:
            return None, "invalid_top_ask"
        if (
            fee is None
            or fee < 0
            or rate is None
            or not 0 <= rate <= 1
            or not category
            or not source
            or not isinstance(enabled, bool)
            or model != TAKER_FEE_MODEL_VERSION
        ):
            return None, "missing_fee_provenance"
        expected_fee = rate * ask * (1.0 - ask) if enabled and rate > 0 else 0.0
        if not _same(fee, expected_fee, tolerance=1e-9):
            return None, "inconsistent_fee"
        exact_legs.append(
            {
                "outcome": str(getattr(leg, "outcome", "") or ""),
                "token_id": str(getattr(leg, "token_id", "") or ""),
                "best_ask": ask,
                "ask_size": size,
                "taker_fee_per_share": fee,
                "taker_fee_rate": rate,
                "taker_fee_category": category,
                "taker_fee_source": source,
                "taker_fee_enabled": enabled,
                "taker_fee_model_version": model,
            }
        )

    ask_sum = sum(float(leg["best_ask"]) for leg in exact_legs)
    plan_ask_sum = _finite(getattr(plan, "ask_sum", None))
    if plan_ask_sum is None or not _same(plan_ask_sum, ask_sum):
        return None, "inconsistent_ask_sum"
    common_size = min(float(leg["ask_size"]) for leg in exact_legs)
    entry_fees_usdc = sum(
        round(float(leg["taker_fee_per_share"]) * common_size, 5)
        for leg in exact_legs
    )
    fees_per_set = entry_fees_usdc / common_size
    all_in_cost = ask_sum + fees_per_set + REGISTERED_COST_RESERVE_PER_SET
    if all_in_cost <= 0:
        return None, "invalid_all_in_cost"
    gross_profit_per_set = 1.0 - ask_sum
    net_profit_per_set = 1.0 - all_in_cost
    all_in_capital = all_in_cost * common_size
    net_profit = net_profit_per_set * common_size
    net_return = net_profit_per_set / all_in_cost
    holding_days = holding_seconds / 86_400.0
    annualised = net_return * 365.0 / holding_days
    scan_id = _scan_id(observed_at_utc)
    event_id = str(getattr(plan, "event_id", "") or "").strip()
    if not event_id:
        return None, "missing_event_id"
    qualifying = net_profit_per_set > 0
    row = {
        "work_order": WORK_ORDER,
        "schema_version": SCHEMA_VERSION,
        "observation_id": _observation_id(scan_id, event_id, token_ids),
        "scan_id": scan_id,
        "observed_at_utc": observed_at_utc,
        "event_id": event_id,
        "event_title": str(getattr(plan, "event_title", "") or ""),
        "leg_count": len(exact_legs),
        "token_ids_json": token_ids,
        "legs_json": exact_legs,
        "ask_sum": round(ask_sum, 10),
        "common_size": round(common_size, 8),
        "gross_cost_per_set": round(ask_sum, 10),
        "entry_taker_fees_per_set": round(fees_per_set, 10),
        "entry_taker_fees_usdc": round(entry_fees_usdc, 8),
        "registered_cost_reserve_per_set": REGISTERED_COST_RESERVE_PER_SET,
        "all_in_cost_per_set": round(all_in_cost, 10),
        "gross_profit_per_set": round(gross_profit_per_set, 10),
        "net_profit_per_set": round(net_profit_per_set, 10),
        "gross_capital_usdc": round(ask_sum * common_size, 8),
        "all_in_capital_usdc": round(all_in_capital, 8),
        "gross_profit_usdc": round(gross_profit_per_set * common_size, 8),
        "net_profit_usdc": round(net_profit, 8),
        "net_return_on_capital": round(net_return, 10),
        "holding_days": round(holding_days, 8),
        "annualised_net_return_on_capital": round(annualised, 10),
        "resolution_at_utc": _iso(resolution_at),
        "complete_common_size_plan": True,
        "qualifying": qualifying,
        "qualification_reason": (
            "positive_all_in_net_profit" if qualifying else "nonpositive_all_in_net_profit"
        ),
        "fee_model_versions_json": sorted(
            {str(leg["taker_fee_model_version"]) for leg in exact_legs}
        ),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    return row, ""


def record_h2_scan_observations(
    cfg: EngineConfig,
    plans: Iterable[Any],
    *,
    observed_at: datetime,
) -> dict[str, Any]:
    """Append exact complete scan rows under the producer/evaluator lock."""

    rows: list[dict[str, Any]] = []
    excluded: Counter[str] = Counter()
    for plan in plans:
        row, reason = build_h2_scan_observation(plan, observed_at=observed_at)
        if row is None:
            excluded[reason or "unknown"] += 1
        else:
            rows.append(row)
    path = cfg.output_root / OBSERVATION_RELATIVE_PATH
    base = {
        "work_order": WORK_ORDER,
        "schema_version": SCHEMA_VERSION,
        "observed_at_utc": _iso(
            observed_at.replace(tzinfo=timezone.utc)
            if observed_at.tzinfo is None
            else observed_at.astimezone(timezone.utc)
        ),
        "plans_seen": len(rows) + sum(excluded.values()),
        "exact_rows_built": len(rows),
        "excluded_reasons": dict(sorted(excluded.items())),
        "ledger_path": str(path),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    if not rows:
        return {**base, "status": "no_exact_complete_rows", "rows_appended": 0}
    with runtime_lock(cfg, OBSERVATION_LOCK, stale_after_seconds=300) as lock:
        if not lock.acquired:
            return {
                **base,
                "status": "skipped_locked",
                "rows_appended": 0,
                "runtime_lock": lock.as_dict(),
            }
        existing = read_csv_rows(path)
        seen = {str(row.get("observation_id") or "") for row in existing}
        new_rows = [row for row in rows if row["observation_id"] not in seen]
        append_csv_rows(path, new_rows, fieldnames=OBSERVATION_FIELDS)
    return {**base, "status": "ok", "rows_appended": len(new_rows)}


def _parse_observation(raw: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    if str(raw.get("schema_version") or "") != SCHEMA_VERSION:
        return None, "wrong_schema_version"
    if _as_bool(raw.get("paper_trading_invoked")) is not False or _as_bool(
        raw.get("live_trading_invoked")
    ) is not False:
        return None, "invalid_trading_invocation_flags"
    observed_at = _utc(raw.get("observed_at_utc"))
    resolution_at = _utc(raw.get("resolution_at_utc"))
    event_id = str(raw.get("event_id") or "").strip()
    observation_id = str(raw.get("observation_id") or "").strip()
    scan_id = str(raw.get("scan_id") or "").strip()
    if observed_at is None or resolution_at is None:
        return None, "missing_or_invalid_timestamp"
    if not event_id or not observation_id or not scan_id:
        return None, "missing_identity"
    observed_at_utc = _iso(observed_at)
    if scan_id != _scan_id(observed_at_utc):
        return None, "inconsistent_scan_id"
    legs = _json_list(raw.get("legs_json"))
    tokens = _json_list(raw.get("token_ids_json"))
    if legs is None or tokens is None or not REGISTERED_MIN_LEGS <= len(legs) <= REGISTERED_MAX_LEGS:
        return None, "invalid_legs"
    token_ids = [str(token or "").strip() for token in tokens]
    leg_token_ids = [str(leg.get("token_id") or "").strip() for leg in legs if isinstance(leg, dict)]
    if (
        len(leg_token_ids) != len(legs)
        or token_ids != leg_token_ids
        or any(not token for token in token_ids)
        or len(set(token_ids)) != len(token_ids)
        or observation_id != _observation_id(scan_id, event_id, token_ids)
    ):
        return None, "inconsistent_token_identity"
    if _as_bool(raw.get("complete_common_size_plan")) is not True:
        return None, "incomplete_plan"

    asks: list[float] = []
    sizes: list[float] = []
    fees: list[float] = []
    for leg in legs:
        ask = _finite(leg.get("best_ask"))
        size = _finite(leg.get("ask_size"))
        fee = _finite(leg.get("taker_fee_per_share"))
        rate = _finite(leg.get("taker_fee_rate"))
        enabled = _as_bool(leg.get("taker_fee_enabled"))
        if (
            ask is None
            or not 0 < ask < 1
            or size is None
            or size <= 0
            or fee is None
            or fee < 0
            or rate is None
            or not 0 <= rate <= 1
            or enabled is None
            or not str(leg.get("taker_fee_category") or "").strip()
            or not str(leg.get("taker_fee_source") or "").strip()
            or str(leg.get("taker_fee_model_version") or "") != TAKER_FEE_MODEL_VERSION
        ):
            return None, "invalid_leg_economics"
        expected_fee = rate * ask * (1.0 - ask) if enabled and rate > 0 else 0.0
        if not _same(fee, expected_fee, tolerance=1e-8):
            return None, "inconsistent_fee"
        asks.append(ask)
        sizes.append(size)
        fees.append(fee)

    ask_sum = sum(asks)
    common_size = min(sizes)
    entry_fees_usdc = sum(round(fee * common_size, 5) for fee in fees)
    fees_per_set = entry_fees_usdc / common_size
    reserve = _finite(raw.get("registered_cost_reserve_per_set"))
    all_in_cost = ask_sum + fees_per_set + REGISTERED_COST_RESERVE_PER_SET
    holding_seconds = (resolution_at - observed_at).total_seconds()
    if reserve is None or not _same(reserve, REGISTERED_COST_RESERVE_PER_SET):
        return None, "invalid_registered_cost_reserve"
    if holding_seconds <= 0 or all_in_cost <= 0:
        return None, "nonpositive_horizon_or_cost"
    net_profit_per_set = 1.0 - all_in_cost
    net_profit = net_profit_per_set * common_size
    all_in_capital = all_in_cost * common_size
    net_return = net_profit_per_set / all_in_cost
    holding_days = holding_seconds / 86_400.0
    annualised = net_return * 365.0 / holding_days
    expected = {
        "leg_count": float(len(legs)),
        "ask_sum": ask_sum,
        "common_size": common_size,
        "gross_cost_per_set": ask_sum,
        "entry_taker_fees_per_set": fees_per_set,
        "entry_taker_fees_usdc": entry_fees_usdc,
        "all_in_cost_per_set": all_in_cost,
        "gross_profit_per_set": 1.0 - ask_sum,
        "net_profit_per_set": net_profit_per_set,
        "gross_capital_usdc": ask_sum * common_size,
        "all_in_capital_usdc": all_in_capital,
        "gross_profit_usdc": (1.0 - ask_sum) * common_size,
        "net_profit_usdc": net_profit,
        "net_return_on_capital": net_return,
        "holding_days": holding_days,
        "annualised_net_return_on_capital": annualised,
    }
    for key, expected_value in expected.items():
        actual = _finite(raw.get(key))
        if actual is None or not _same(actual, expected_value, tolerance=1e-6):
            return None, f"inconsistent_{key}"
    qualifying = net_profit_per_set > 0
    if _as_bool(raw.get("qualifying")) is not qualifying:
        return None, "inconsistent_qualification"
    versions = _json_list(raw.get("fee_model_versions_json"))
    if versions != [TAKER_FEE_MODEL_VERSION]:
        return None, "inconsistent_fee_model_versions"
    return (
        {
            **raw,
            "event_id": event_id,
            "observation_id": observation_id,
            "scan_id": scan_id,
            "observed_at_utc": observed_at_utc,
            "observed_dt": observed_at,
            "resolution_at_utc": _iso(resolution_at),
            "resolution_dt": resolution_at,
            "token_ids_json": json.dumps(token_ids, sort_keys=True),
            "token_identity": tuple(sorted(token_ids)),
            "leg_count": len(legs),
            "common_size": common_size,
            "ask_sum": ask_sum,
            "entry_taker_fees_per_set": fees_per_set,
            "entry_taker_fees_usdc": entry_fees_usdc,
            "all_in_cost_per_set": all_in_cost,
            "net_profit_per_set": net_profit_per_set,
            "all_in_capital_usdc": all_in_capital,
            "net_profit_usdc": net_profit,
            "net_return_on_capital": net_return,
            "holding_days": holding_days,
            "annualised_net_return_on_capital": annualised,
            "qualifying": qualifying,
            "complete_common_size_plan": True,
        },
        "",
    )


def _eligible_observations(
    rows: list[dict[str, Any]],
    *,
    as_of: datetime,
    coverage: Counter[str],
) -> list[dict[str, Any]]:
    coverage["rows_seen"] = len(rows)
    id_counts = Counter(str(row.get("observation_id") or "") for row in rows)
    parsed: list[dict[str, Any]] = []
    for raw in rows:
        observation_id = str(raw.get("observation_id") or "")
        if observation_id and id_counts[observation_id] > 1:
            coverage["duplicate_observation_id_rows"] += 1
            continue
        row, reason = _parse_observation(raw)
        if row is None:
            coverage[reason or "malformed"] += 1
            continue
        observed_at = row["observed_dt"]
        if observed_at <= REGISTRATION_AT:
            coverage["pre_registration"] += 1
            continue
        if observed_at > as_of:
            coverage["future_observation"] += 1
            continue
        if observed_at > REGISTERED_STOP_AT:
            coverage["after_registered_window"] += 1
            continue
        parsed.append(row)
    scan_event_counts = Counter((row["scan_id"], row["event_id"]) for row in parsed)
    eligible = [
        row
        for row in parsed
        if scan_event_counts[(row["scan_id"], row["event_id"])] == 1
    ]
    coverage["duplicate_scan_event_rows"] += len(parsed) - len(eligible)
    event_signatures: dict[str, set[tuple[Any, ...]]] = defaultdict(set)
    for row in eligible:
        event_signatures[row["event_id"]].add(
            (
                row["token_identity"],
                row["leg_count"],
                row["resolution_at_utc"],
            )
        )
    ambiguous_events = {
        event_id for event_id, signatures in event_signatures.items() if len(signatures) != 1
    }
    if ambiguous_events:
        retained = [row for row in eligible if row["event_id"] not in ambiguous_events]
        coverage["inconsistent_event_identity_rows"] += len(eligible) - len(retained)
        eligible = retained
    eligible.sort(
        key=lambda row: (
            row["observed_dt"],
            row["event_id"],
            row["observation_id"],
        )
    )
    coverage["eligible_exact_rows"] = len(eligible)
    coverage["qualifying_exact_rows"] = sum(bool(row["qualifying"]) for row in eligible)
    coverage["complete_nonqualifying_clear_rows"] = sum(
        not bool(row["qualifying"]) for row in eligible
    )
    return eligible


def _new_episode(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "episode_id": _episode_id(row),
        "event_id": row["event_id"],
        "event_title": str(row.get("event_title") or ""),
        "episode_start_at_utc": row["observed_at_utc"],
        "episode_day_utc": row["observed_dt"].date().isoformat(),
        "first_observation_id": row["observation_id"],
        "first_scan_id": row["scan_id"],
        "leg_count": row["leg_count"],
        "token_ids_json": row["token_ids_json"],
        "common_size": round(float(row["common_size"]), 8),
        "ask_sum": round(float(row["ask_sum"]), 10),
        "entry_taker_fees_per_set": round(float(row["entry_taker_fees_per_set"]), 10),
        "entry_taker_fees_usdc": round(float(row["entry_taker_fees_usdc"]), 8),
        "registered_cost_reserve_per_set": REGISTERED_COST_RESERVE_PER_SET,
        "all_in_cost_per_set": round(float(row["all_in_cost_per_set"]), 10),
        "net_profit_per_set": round(float(row["net_profit_per_set"]), 10),
        "all_in_capital_usdc": round(float(row["all_in_capital_usdc"]), 8),
        "net_profit_usdc": round(float(row["net_profit_usdc"]), 8),
        "net_return_on_capital": round(float(row["net_return_on_capital"]), 10),
        "holding_days": round(float(row["holding_days"]), 8),
        "annualised_net_return_on_capital": round(
            float(row["annualised_net_return_on_capital"]), 10
        ),
        "resolution_at_utc": row["resolution_at_utc"],
        "complete_common_size_plan": True,
        "persistent_three_scans": False,
        "persistence_scan_count": 1,
        "persistence_qualified_at_utc": "",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }


def _reconstruct_episodes(
    rows: list[dict[str, Any]],
    *,
    coverage: Counter[str] | None = None,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["event_id"]].append(row)
    episodes: list[dict[str, Any]] = []
    for event_id in sorted(grouped):
        active: dict[str, Any] | None = None
        active_counted = False
        used_days: set[str] = set()
        last_qualifying_at: datetime | None = None
        persistence_run = 0
        for row in sorted(
            grouped[event_id],
            key=lambda item: (item["observed_dt"], item["observation_id"]),
        ):
            if not row["qualifying"]:
                active = None
                active_counted = False
                last_qualifying_at = None
                persistence_run = 0
                continue
            if active is None:
                day = row["observed_dt"].date().isoformat()
                if day in used_days:
                    active = {}
                    active_counted = False
                    if coverage is not None:
                        coverage["same_event_day_recurrences_excluded"] += 1
                else:
                    active = _new_episode(row)
                    active_counted = True
                    used_days.add(day)
                    episodes.append(active)
                persistence_run = 1
                last_qualifying_at = row["observed_dt"]
                continue
            gap = (
                (row["observed_dt"] - last_qualifying_at).total_seconds()
                if last_qualifying_at is not None
                else None
            )
            if (
                gap is not None
                and REGISTERED_PERSISTENCE_MIN_GAP_SECONDS
                <= gap
                <= REGISTERED_PERSISTENCE_MAX_GAP_SECONDS
            ):
                persistence_run += 1
            else:
                persistence_run = 1
                if coverage is not None:
                    coverage["persistence_gap_resets"] += 1
            last_qualifying_at = row["observed_dt"]
            if active_counted and active is not None:
                active["persistence_scan_count"] = max(
                    int(active["persistence_scan_count"]), persistence_run
                )
                if persistence_run >= 3 and not active["persistent_three_scans"]:
                    active["persistent_three_scans"] = True
                    active["persistence_qualified_at_utc"] = row["observed_at_utc"]
    episodes.sort(
        key=lambda row: (
            str(row["episode_start_at_utc"]),
            str(row["event_id"]),
            str(row["episode_id"]),
        )
    )
    return episodes


def _cluster_bootstrap(
    rows: list[dict[str, Any]],
) -> tuple[float | None, float | None, float]:
    by_event: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        value = _finite(row.get("annualised_net_return_on_capital"))
        event_id = str(row.get("event_id") or "")
        if value is not None and event_id:
            by_event[event_id].append(value)
    clusters = [values for _, values in sorted(by_event.items())]
    if len(clusters) < 2:
        return None, None, 1.0
    rng = random.Random(REGISTERED_BOOTSTRAP_SEED)
    draws: list[float] = []
    for _ in range(REGISTERED_BOOTSTRAP_ITERATIONS):
        sampled = [clusters[rng.randrange(len(clusters))] for _ in clusters]
        values = [value for cluster in sampled for value in cluster]
        draws.append(sum(values) / len(values))
    draws.sort()
    low = draws[max(0, math.ceil(0.05 * len(draws)) - 1)]
    high = draws[min(len(draws) - 1, math.ceil(0.95 * len(draws)) - 1)]
    p_value = (1 + sum(value <= 0 for value in draws)) / (len(draws) + 1)
    return round(low, 10), round(high, 10), round(p_value, 10)


def _maximum_positive_event_share(rows: list[dict[str, Any]]) -> float:
    positive: dict[str, float] = defaultdict(float)
    for row in rows:
        value = _finite(row.get("net_profit_usdc")) or 0.0
        if value > 0:
            positive[str(row.get("event_id") or "unknown")] += value
    total = sum(positive.values())
    return max(positive.values()) / total if total > 0 else 1.0


def _stop_sample(
    rows: list[dict[str, Any]],
    episodes: list[dict[str, Any]],
    *,
    as_of: datetime,
) -> tuple[bool, str, list[dict[str, Any]], str | None]:
    if len(episodes) >= REGISTERED_STOP_EPISODES:
        stop_at = _utc(episodes[REGISTERED_STOP_EPISODES - 1]["episode_start_at_utc"])
        assert stop_at is not None
        stopped_rows = [row for row in rows if row["observed_dt"] <= stop_at]
        stopped_episodes = _reconstruct_episodes(stopped_rows)[:REGISTERED_STOP_EPISODES]
        return True, "first_100_independent_episodes", stopped_episodes, _iso(stop_at)
    if as_of >= REGISTERED_STOP_AT:
        return True, "calendar_deadline", episodes, _iso(REGISTERED_STOP_AT)
    return False, "", [], None


def _freeze_sample(
    cfg: EngineConfig,
    selected: list[dict[str, Any]],
    *,
    stop_reason: str,
    stop_at_utc: str,
    generated_at: str,
) -> tuple[list[dict[str, str]], bool, str]:
    final_path = cfg.output_root / FINAL_RELATIVE_PATH
    manifest_path = cfg.output_root / FINAL_MANIFEST_RELATIVE_PATH
    with runtime_lock(cfg, FINAL_LOCK, stale_after_seconds=300) as lock:
        if not lock.acquired:
            return read_csv_rows(final_path), True, "final_sample_lock_contended"
        manifest = read_json(manifest_path, default={}) or {}
        if not isinstance(manifest, dict):
            return read_csv_rows(final_path), False, "invalid_final_sample_manifest"
        if manifest:
            selected_ids = [str(value) for value in manifest.get("episode_ids", [])]
        else:
            selected_ids = [str(row["episode_id"]) for row in selected]
            manifest = {
                "work_order": WORK_ORDER,
                "schema_version": SCHEMA_VERSION,
                "frozen_at_utc": generated_at,
                "stop_reason": stop_reason,
                "stop_at_utc": stop_at_utc,
                "episode_ids": selected_ids,
                "episode_count": len(selected_ids),
                "paper_trading_invoked": False,
                "live_trading_invoked": False,
            }
            write_json(manifest_path, manifest)
        selected_by_id = {str(row["episode_id"]): row for row in selected}
        if any(episode_id not in selected_by_id for episode_id in selected_ids):
            return read_csv_rows(final_path), False, "frozen_episode_missing_from_exact_source"
        existing = read_csv_rows(final_path)
        existing_ids = [str(row.get("episode_id") or "") for row in existing]
        existing_id_set = set(existing_ids)
        if any(episode_id not in selected_ids for episode_id in existing_ids):
            return existing, False, "final_ledger_contains_unregistered_episode"
        missing = [
            selected_by_id[episode_id]
            for episode_id in selected_ids
            if episode_id not in existing_id_set
        ]
        append_csv_rows(final_path, missing, fieldnames=EPISODE_FIELDS)
        final_rows = read_csv_rows(final_path)
        final_ids = [str(row.get("episode_id") or "") for row in final_rows]
        if final_ids != selected_ids:
            return final_rows, False, "final_ledger_order_or_completeness_mismatch"
        return final_rows, False, ""


def _anchor_state(cfg: EngineConfig, final_path: Path) -> tuple[bool, str]:
    if not final_path.is_file() or final_path.stat().st_size <= 0:
        return False, "final_episode_ledger_missing"
    verification = read_json(
        cfg.output_root / "performance" / "ledger_anchor_verification.json",
        default={},
    ) or {}
    head = read_json(
        cfg.output_root / "performance" / "ledger_anchor_head.json",
        default={},
    ) or {}
    if not isinstance(verification, dict) or verification.get("status") != "ok":
        return False, "ledger_chain_not_verified"
    manifest = head.get("ledger_manifest") if isinstance(head, dict) else None
    if not isinstance(manifest, list):
        return False, "ledger_anchor_head_missing_manifest"
    for item in manifest:
        if not isinstance(item, dict) or item.get("status") != "present":
            continue
        if str(item.get("path") or "") != FINAL_RELATIVE_PATH:
            continue
        try:
            anchored_length = int(item.get("byte_length"))
        except (TypeError, ValueError):
            return False, "invalid_anchored_byte_length"
        if anchored_length < final_path.stat().st_size:
            return False, "final_episode_ledger_has_unanchored_suffix"
        return True, ""
    return False, "final_episode_ledger_not_in_anchor_manifest"


def _support_summary(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    values = [float(row["annualised_net_return_on_capital"]) for row in rows]
    aggregate_profit = sum(float(row["net_profit_usdc"]) for row in rows)
    episode_dates = [
        datetime.fromisoformat(str(row["episode_day_utc"])).date() for row in rows
    ]
    span_days = (max(episode_dates) - min(episode_dates)).days + 1 if episode_dates else 0
    persistent = sum(_as_bool(row.get("persistent_three_scans")) is True for row in rows)
    low, high, p_value = _cluster_bootstrap(rows)
    concentration = _maximum_positive_event_share(rows)
    checks = {
        "episode_floor_pass": len(rows) >= REGISTERED_MIN_EPISODES,
        "calendar_span_pass": span_days >= REGISTERED_MIN_CALENDAR_SPAN_DAYS,
        "persistence_floor_pass": persistent >= REGISTERED_MIN_PERSISTENT_EPISODES,
        "complete_plan_pass": all(
            _as_bool(row.get("complete_common_size_plan")) is True for row in rows
        ),
        "aggregate_net_profit_pass": aggregate_profit > 0,
        "clustered_ci_pass": low is not None and low > 0,
        "event_concentration_pass": concentration <= REGISTERED_MAX_EVENT_PROFIT_SHARE,
    }
    return {
        "episodes": len(rows),
        "events": len({str(row.get("event_id") or "") for row in rows}),
        "calendar_span_days": span_days,
        "distinct_episode_days": len(set(episode_dates)),
        "persistent_three_scan_episodes": persistent,
        "aggregate_net_profit_usdc": round(aggregate_profit, 8),
        "mean_annualised_net_return_on_capital": (
            round(sum(values) / len(values), 10) if values else None
        ),
        "event_clustered_ci_low_90": low,
        "event_clustered_ci_high_90": high,
        "event_clustered_p_one_sided": p_value,
        "maximum_positive_event_profit_share": round(concentration, 8),
        "fdr_status": "not_applicable_single_registered_primary_test",
        "support_checks": checks,
        "statistical_support_gate_pass": all(checks.values()),
    }


def _next_condition(
    *,
    stopped: bool,
    episode_count: int,
    support: dict[str, Any] | None,
    anchor_pass: bool,
    failure: str,
) -> str:
    if failure:
        return failure
    if not stopped:
        return (
            f"collect exact complete scans until {REGISTERED_STOP_EPISODES} independent episodes "
            f"or {_iso(REGISTERED_STOP_AT)}; current episodes={episode_count}"
        )
    checks = (support or {}).get("support_checks", {})
    labels = {
        "episode_floor_pass": f"reach {REGISTERED_MIN_EPISODES} independent episodes",
        "calendar_span_pass": f"span at least {REGISTERED_MIN_CALENDAR_SPAN_DAYS} calendar days",
        "persistence_floor_pass": (
            f"reach {REGISTERED_MIN_PERSISTENT_EPISODES} three-scan-persistent episodes"
        ),
        "complete_plan_pass": "remove incomplete or inconsistent final execution plans",
        "aggregate_net_profit_pass": "aggregate exact net profit must be positive",
        "clustered_ci_pass": "event-clustered 90% lower bound must be above zero",
        "event_concentration_pass": "reduce maximum positive-profit event share to 35% or less",
    }
    for key, label in labels.items():
        if not bool(checks.get(key)):
            return label
    if not anchor_pass:
        return "anchor and verify the frozen H2 final-episode ledger"
    return "registered H2 support satisfied; retain shadow-only status"


def evaluate_h2_dutch(
    cfg: EngineConfig,
    *,
    as_of: datetime | None = None,
    observations_input: str | Path | None = None,
) -> dict[str, Any]:
    """Evaluate the frozen H2 contract without touching any trading path."""

    as_of = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    generated_at = _iso(as_of)
    observation_path = Path(
        observations_input or cfg.output_root / OBSERVATION_RELATIVE_PATH
    )
    summary_path = cfg.output_root / SUMMARY_RELATIVE_PATH
    episode_path = cfg.output_root / EPISODE_STATE_RELATIVE_PATH
    final_path = cfg.output_root / FINAL_RELATIVE_PATH
    critical_settings = {
        "registration_at_utc": _iso(REGISTRATION_AT),
        "stop_at_utc": _iso(REGISTERED_STOP_AT),
        "stop_episodes": REGISTERED_STOP_EPISODES,
        "minimum_episodes": REGISTERED_MIN_EPISODES,
        "minimum_calendar_span_days": REGISTERED_MIN_CALENDAR_SPAN_DAYS,
        "minimum_persistent_episodes": REGISTERED_MIN_PERSISTENT_EPISODES,
        "persistence_min_gap_seconds": REGISTERED_PERSISTENCE_MIN_GAP_SECONDS,
        "persistence_max_gap_seconds": REGISTERED_PERSISTENCE_MAX_GAP_SECONDS,
        "registered_cost_reserve_per_set": REGISTERED_COST_RESERVE_PER_SET,
        "maximum_positive_event_profit_share": REGISTERED_MAX_EVENT_PROFIT_SHARE,
        "bootstrap_iterations": REGISTERED_BOOTSTRAP_ITERATIONS,
        "bootstrap_seed": REGISTERED_BOOTSTRAP_SEED,
    }
    base: dict[str, Any] = {
        "work_order": WORK_ORDER,
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "registration_at_utc": _iso(REGISTRATION_AT),
        "registered_stop_at_utc": _iso(REGISTERED_STOP_AT),
        "critical_settings": critical_settings,
        "observation_ledger_path": str(observation_path),
        "episode_state_path": str(episode_path),
        "final_episode_ledger_path": str(final_path),
        "final_sample_manifest_path": str(cfg.output_root / FINAL_MANIFEST_RELATIVE_PATH),
        "legacy_gross_scanner_is_diagnostic": True,
        "primary_metric": "mean_annualised_net_return_on_capital",
        "fdr_status": "not_applicable_single_registered_primary_test",
        "recommended_action": "collect",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }

    with runtime_lock(cfg, OBSERVATION_LOCK, stale_after_seconds=300) as lock:
        if not lock.acquired:
            payload = {
                **base,
                "status": "insufficient_lock_contended",
                "formal_evaluation_started": False,
                "stop_reason": "",
                "coverage": {},
                "independent_episodes": 0,
                "next_unmet_condition": "exact observation ledger lock is contended",
                "runtime_lock": lock.as_dict(),
            }
            write_json(summary_path, payload)
            return payload
        raw_rows = read_csv_rows(observation_path)

    coverage: Counter[str] = Counter()
    eligible = _eligible_observations(raw_rows, as_of=as_of, coverage=coverage)
    episodes = _reconstruct_episodes(eligible, coverage=coverage)
    write_csv(episode_path, episodes, fieldnames=EPISODE_FIELDS)
    stopped, stop_reason, selected, stop_at_utc = _stop_sample(
        eligible,
        episodes,
        as_of=as_of,
    )
    failure = ""
    lock_contended = False
    formal_rows: list[dict[str, Any]] = []
    if stopped:
        frozen, lock_contended, failure = _freeze_sample(
            cfg,
            selected,
            stop_reason=stop_reason,
            stop_at_utc=str(stop_at_utc or ""),
            generated_at=generated_at,
        )
        formal_rows = list(frozen)
    support = _support_summary(formal_rows) if stopped and not failure else None
    anchor_pass, anchor_reason = (
        _anchor_state(cfg, final_path) if stopped and support and support["statistical_support_gate_pass"]
        else (False, "not_required_until_statistical_support")
    )
    candidate = bool(
        stopped
        and not failure
        and support
        and support["statistical_support_gate_pass"]
        and anchor_pass
    )
    if failure or lock_contended:
        status = "insufficient_evidence_integrity"
        action = "collect"
    elif not stopped:
        status = "collecting"
        action = "collect"
    elif support and support["statistical_support_gate_pass"] and not anchor_pass:
        status = "awaiting_verified_anchor"
        action = "collect"
    elif candidate:
        status = "shadow_research_candidate"
        action = "shadow_research_candidate"
    else:
        status = "suppressed"
        action = "suppress"

    payload = {
        **base,
        "status": status,
        "recommended_action": action,
        "formal_evaluation_started": stopped,
        "stop_reason": stop_reason,
        "stop_at_utc": stop_at_utc,
        "coverage": dict(sorted(coverage.items())),
        "eligible_exact_rows": len(eligible),
        "independent_episodes": len(episodes),
        "persistent_episodes_current": sum(
            bool(row["persistent_three_scans"]) for row in episodes
        ),
        "formal_sample_episodes": len(formal_rows),
        "support": support or {},
        "evidence_anchor_pass": anchor_pass,
        "evidence_anchor_reason": anchor_reason,
        "integrity_failure": failure,
        "next_unmet_condition": _next_condition(
            stopped=stopped,
            episode_count=len(episodes),
            support=support,
            anchor_pass=anchor_pass,
            failure=failure,
        ),
        "sample_stop_progress": min(1.0, len(episodes) / REGISTERED_STOP_EPISODES),
        "calendar_stop_progress": min(
            1.0,
            max(0.0, (as_of - REGISTRATION_AT).total_seconds())
            / (REGISTERED_STOP_AT - REGISTRATION_AT).total_seconds(),
        ),
        "latest_exact_observation_at_utc": (
            eligible[-1]["observed_at_utc"] if eligible else None
        ),
    }
    write_json(summary_path, payload)
    return payload


def main(config_path: str) -> dict[str, Any]:
    return evaluate_h2_dutch(load_config(config_path))
