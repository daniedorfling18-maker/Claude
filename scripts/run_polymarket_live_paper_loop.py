#!/usr/bin/env python3
"""Run a strictly paper Polymarket loop on live market snapshots.

Each iteration:
1. forces scanner-only/no-live-execution environment flags;
2. scans live Polymarket order books into ``market_snapshot.csv``;
3. ingests that snapshot into the canonical raw snapshot stream;
4. optionally refreshes the optimized ML artifact;
5. runs the canonical forward paper cycle: features -> predictions -> signals ->
   persistent paper broker;
6. writes a heartbeat JSON for monitoring.

No private key, wallet, CLOB client, or live executor is used here.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import polymarket_mispricing_bot as scanner  # noqa: E402
from polymarket_predictive_engine.cohort_validation import write_signal_cohort_pnl  # noqa: E402
from polymarket_predictive_engine.config import load_config  # noqa: E402
from polymarket_predictive_engine.crypto_fundamental import build_crypto_fundamental  # noqa: E402
from polymarket_predictive_engine.dashboard import render_dashboard  # noqa: E402
from polymarket_predictive_engine.mispricing_alpha import train_mispricing_alpha_model  # noqa: E402
from polymarket_predictive_engine.models.optimized import train_optimized_model  # noqa: E402
from polymarket_predictive_engine.paper_cycle import run_paper_cycle  # noqa: E402
from polymarket_predictive_engine.sharp_anchor import build_sharp_anchor  # noqa: E402
from polymarket_predictive_engine.sharp_odds_fetch import fetch_sharp_odds  # noqa: E402
from polymarket_predictive_engine.shadow_cohort import update_shadow_cohort_evidence  # noqa: E402
from polymarket_predictive_engine.snapshot_ingest import ingest_scanner_snapshot  # noqa: E402
from polymarket_predictive_engine.storage import connect_db  # noqa: E402
from polymarket_predictive_engine.strategy_search import run_edge_strategy_search  # noqa: E402
from polymarket_predictive_engine.utils import now_utc, read_json, safe_float, write_json  # noqa: E402
from run_polymarket_liquidity_discovery import run_liquidity_discovery  # noqa: E402
from run_promoted_rule_shadow_scan import run_promoted_rule_shadow_scan  # noqa: E402


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_override(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _current_memory_percent() -> float | None:
    """Return host/container memory load using only stdlib, or None if unavailable."""
    if os.name == "nt":
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return float(status.dwMemoryLoad)
        except Exception:
            return None
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        try:
            values: dict[str, float] = {}
            for line in meminfo.read_text(encoding="utf-8", errors="ignore").splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    values[parts[0].rstrip(":")] = float(parts[1])
            total = values.get("MemTotal")
            available = values.get("MemAvailable")
            if total and available is not None:
                return max(0.0, min(100.0, 100.0 * (1.0 - available / total)))
        except Exception:
            return None
    return None


def _resource_guard(cfg) -> dict[str, Any]:
    settings = cfg.raw.get("runtime_resource_guard", {}) or {}
    memory_percent = _current_memory_percent()
    max_memory = float(settings.get("max_memory_percent", 92.0))
    enabled = _truthy(settings.get("enabled", True), default=True)
    skip = bool(enabled and memory_percent is not None and memory_percent >= max_memory)
    return {
        "enabled": enabled,
        "skip_cycle": skip,
        "memory_percent": memory_percent,
        "max_memory_percent": max_memory,
        "reason": "memory_above_limit" if skip else "ok",
    }


def _scheduled(settings: dict[str, Any], key: str, iteration: int, *, default: int = 1) -> bool:
    every = int(settings.get(key, default) or default)
    if every <= 0:
        return False
    return iteration == 1 or iteration % every == 0


def _run_settlement_only_cycle(cfg) -> dict[str, Any]:
    """Cheap maintenance path: settle expired shadow positions without a full market scan."""
    shadow = update_shadow_cohort_evidence(cfg, [])
    cohort: dict[str, Any]
    try:
        con = connect_db(cfg.database_path)
        try:
            cohort = write_signal_cohort_pnl(con, cfg)
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001 - settlement-only must not kill the loop
        cohort = {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "status": "settlement_only",
        "shadow": {
            "status": shadow.get("status"),
            "open_positions": shadow.get("open_positions"),
            "closed_this_cycle": shadow.get("closed_this_cycle"),
            "settlement_checks": shadow.get("settlement_checks"),
            "settled_positions": shadow.get("settled_positions"),
            "settlement_errors": shadow.get("settlement_errors"),
            "promoted_cohorts": shadow.get("promoted_cohorts", []),
        },
        "cohort_pnl": {
            "status": cohort.get("status"),
            "promoted_cohorts": cohort.get("promoted_cohorts", []),
        },
    }


def _abs(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def force_paper_environment() -> None:
    os.environ["PM_MODE"] = "scan"
    os.environ["POLYMARKET_MODE"] = "scan"
    os.environ["POLYMARKET_EXECUTE_LIVE"] = "false"
    os.environ["POLYMARKET_LIVE_TRADING"] = "0"
    os.environ.setdefault("POLYMARKET_QUERY", "world cup")
    os.environ.setdefault("POLYMARKET_MODEL_PROBABILITIES_CSV", "inputs/polymarket/model_probabilities.csv")
    os.environ.setdefault("POLYMARKET_POSITIONS_CSV", "inputs/polymarket/positions.csv")
    os.environ.setdefault("POLYMARKET_OUTPUT_DIR", "outputs/polymarket")
    os.environ.setdefault("POLYMARKET_MIN_EDGE", "0.04")


def ensure_scanner_runtime_files() -> None:
    model_csv = _abs(Path(os.environ["POLYMARKET_MODEL_PROBABILITIES_CSV"]))
    positions_csv = _abs(Path(os.environ["POLYMARKET_POSITIONS_CSV"]))
    output_dir = _abs(Path(os.environ["POLYMARKET_OUTPUT_DIR"]))
    model_csv.parent.mkdir(parents=True, exist_ok=True)
    positions_csv.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    if not model_csv.exists():
        model_csv.write_text("token_id,probability\n", encoding="utf-8")
    if not positions_csv.exists():
        positions_csv.write_text("token_id,shares\n", encoding="utf-8")


SNAPSHOT_FIELDS = [
    "timestamp",
    "event_slug",
    "event_title",
    "market_slug",
    "condition_id",
    "close_time",
    "question",
    "outcome",
    "token_id",
    "gamma_price",
    "fair_probability",
    "best_bid",
    "best_ask",
    "spread",
    "bid_size",
    "ask_size",
    "tick_size",
    "neg_risk",
]

OPPORTUNITY_FIELDS = [
    "timestamp",
    "action",
    "event_slug",
    "event_title",
    "market_slug",
    "question",
    "outcome",
    "token_id",
    "fair_probability",
    "executable_price",
    "edge",
    "best_bid",
    "best_ask",
    "spread",
    "size_usd",
    "shares",
    "reason",
]


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_rows(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _configured_scan_queries(cfg, default_query: str) -> tuple[list[str], str]:
    settings = cfg.raw.get("paper_market_scan", {}) or {}
    raw = _env_override("POLYMARKET_QUERIES") or ""
    configured = settings.get("queries") or []
    if raw:
        queries = [item.strip() for item in raw.split(",") if item.strip()]
    elif isinstance(configured, list) and configured:
        queries = [str(item).strip() for item in configured if str(item).strip()]
    else:
        queries = [default_query]
    seen: set[str] = set()
    unique: list[str] = []
    for query in queries:
        key = query.lower()
        if key and key not in seen:
            unique.append(query)
            seen.add(key)
    mode = (_env_override("POLYMARKET_SCAN_QUERY_MODE") or str(settings.get("mode", "single"))).strip().lower()
    if mode not in {"single", "batch", "rotate"}:
        mode = "single"
    return unique, mode


def _truthy_setting(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _query_key(value: str) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


def _cohort_to_query_keys(cohort: str) -> list[str]:
    text = str(cohort or "").lower()
    keys: list[str] = []
    if "crypto_btc" in text or "|btc" in text or "bitcoin" in text:
        if "updown" in text:
            keys.extend(["btc updown", "bitcoin updown"])
        keys.extend(["bitcoin", "btc"])
    if "crypto_eth" in text or "|eth" in text or "ethereum" in text:
        if "updown" in text:
            keys.extend(["eth updown", "ethereum updown"])
        keys.extend(["ethereum", "eth"])
    if "crypto_sol" in text or "|sol" in text or "solana" in text:
        if "updown" in text:
            keys.extend(["solana updown", "sol updown"])
        keys.extend(["solana", "sol"])
    if "crypto_xrp" in text or "|xrp" in text or "ripple" in text:
        if "updown" in text:
            keys.extend(["xrp updown", "ripple updown"])
        keys.extend(["xrp", "ripple"])
    if "tennis" in text:
        keys.append("tennis")
    if "worldcup" in text or "world_cup" in text or "world cup" in text:
        keys.extend(["worldcup", "world cup"])
    if "crypto_updown" in text:
        keys.extend(["bitcoin", "ethereum", "solana", "xrp"])
    seen: set[str] = set()
    deduped: list[str] = []
    for key in keys:
        normalised = _query_key(key)
        if normalised and normalised not in seen:
            deduped.append(normalised)
            seen.add(normalised)
    return deduped


QUARANTINED_COHORT_FRAGMENTS = (
    "crypto_btc_updown_5m",
    "crypto_sol_updown_5m",
    "crypto_xrp_updown_5m",
    "crypto_updown_5m",
)


def _is_quarantined_fast_crypto_cohort(cohort: str) -> bool:
    text = cohort.lower()
    return any(fragment in text for fragment in QUARANTINED_COHORT_FRAGMENTS)


def _cohort_priority_value(row: dict[str, Any]) -> float:
    score = safe_float(row.get("promotion_ready_score")) or 0.0
    checks = max(1.0, safe_float(row.get("promotion_ready_checks")) or 6.0)
    pnl = safe_float(row.get("total_pnl_usdc")) or safe_float(row.get("shadow_total_pnl_usdc")) or 0.0
    roi = safe_float(row.get("roi")) or safe_float(row.get("shadow_roi")) or 0.0
    run_rate = safe_float(row.get("monthly_run_rate_usdc")) or safe_float(row.get("shadow_monthly_run_rate_usdc")) or 0.0
    fills = safe_float(row.get("buy_fills")) or safe_float(row.get("shadow_fills")) or 0.0
    settled = safe_float(row.get("settled_fills")) or safe_float(row.get("shadow_sell_fills")) or safe_float(row.get("sell_fills")) or 0.0
    value = 10.0 * (score / checks)
    if _truthy_setting(row.get("promoted"), default=False):
        value += 25.0
    if _truthy_setting(row.get("probationary"), default=False):
        value += 12.0
    if pnl > 0:
        value += min(5.0, pnl / 5.0)
    if roi > 0:
        value += min(4.0, roi * 4.0)
    if run_rate > 0:
        value += min(4.0, run_rate / 100.0)
    value += min(2.0, fills / 3.0)
    value += min(2.0, settled / 3.0)
    if pnl <= 0 and roi <= 0:
        value *= 0.35
    return value


def _load_cohort_rows(cfg) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for filename in ["signal_cohort_pnl.json", "shadow_signal_cohort_pnl.json"]:
        payload = read_json(cfg.governance_root / filename, default={}) or {}
        if not isinstance(payload, dict):
            continue
        cohorts = payload.get("cohorts", [])
        if isinstance(cohorts, list):
            rows.extend([row for row in cohorts if isinstance(row, dict)])
    return rows


def _load_research_focus(cfg) -> dict[str, Any]:
    payload = read_json(cfg.governance_root / "research_focus.json", default={}) or {}
    return payload if isinstance(payload, dict) else {}


def _load_price_action_feedback(cfg) -> dict[str, Any]:
    payload = read_json(cfg.governance_root / "price_action_feedback.json", default={}) or {}
    return payload if isinstance(payload, dict) else {}


def _adaptive_query_order(cfg, queries: list[str]) -> tuple[list[str], dict[str, Any]]:
    settings = cfg.raw.get("paper_market_scan", {}) or {}
    adaptive_override = _env_override("POLYMARKET_ADAPTIVE_SCAN_PRIORITY")
    enabled = _truthy_setting(
        adaptive_override if adaptive_override is not None else settings.get("prioritize_near_promoted", True),
        default=True,
    )
    if not enabled or not queries:
        return queries, {"enabled": enabled, "priority_queries": [], "top_cohorts": []}
    query_by_key = {_query_key(query): query for query in queries}
    priority_by_key: dict[str, float] = {}
    reason_by_key: dict[str, dict[str, Any]] = {}
    min_value = float(settings.get("adaptive_priority_min_value", 3.0))
    require_positive = _truthy_setting(settings.get("adaptive_priority_require_positive_evidence", True), default=True)
    for row in _load_cohort_rows(cfg):
        cohort = str(row.get("signal_cohort") or row.get("cohort") or "")
        if _is_quarantined_fast_crypto_cohort(cohort):
            continue
        pnl = safe_float(row.get("total_pnl_usdc")) or safe_float(row.get("shadow_total_pnl_usdc")) or 0.0
        roi = safe_float(row.get("roi")) or safe_float(row.get("shadow_roi")) or 0.0
        run_rate = safe_float(row.get("monthly_run_rate_usdc")) or safe_float(row.get("shadow_monthly_run_rate_usdc")) or 0.0
        if require_positive and not (pnl > 0 and roi > 0 and run_rate > 0):
            continue
        value = _cohort_priority_value(row)
        if value < min_value:
            continue
        for key in _cohort_to_query_keys(cohort):
            if key not in query_by_key:
                continue
            adjusted_value = value
            if "updown" in cohort and "updown" not in key:
                adjusted_value = max(0.0, value - 100.0)
            if adjusted_value > priority_by_key.get(key, -1.0):
                priority_by_key[key] = adjusted_value
                reason_by_key[key] = {
                    "query": query_by_key[key],
                    "cohort": cohort,
                    "priority_value": round(adjusted_value, 4),
                    "cohort_priority_value": round(value, 4),
                    "promotion_ready_score": row.get("promotion_ready_score"),
                    "promotion_ready_checks": row.get("promotion_ready_checks"),
                    "promoted": row.get("promoted"),
                    "probationary": row.get("probationary"),
                    "pnl_usdc": row.get("total_pnl_usdc", row.get("shadow_total_pnl_usdc")),
                    "roi": row.get("roi", row.get("shadow_roi")),
                    "monthly_run_rate_usdc": row.get("monthly_run_rate_usdc", row.get("shadow_monthly_run_rate_usdc")),
                }
    focus_payload = _load_research_focus(cfg)
    focus_enabled = _truthy_setting(settings.get("use_research_focus_priority", True), default=True)
    focus_queries: list[str] = []
    suppressed_keys: set[str] = set()
    price_action_feedback = _load_price_action_feedback(cfg)
    feedback_learning_state = str(
        price_action_feedback.get("learning_state")
        or (focus_payload.get("price_action_feedback") or {}).get("learning_state")
        or ""
    )
    feedback_broaden_queries: list[str] = []
    if focus_enabled and focus_payload:
        focus_value = float(settings.get("research_focus_priority_value", min_value + 1.0))
        for raw_query in focus_payload.get("collection_queries", []):
            key = _query_key(str(raw_query or ""))
            if not key or key not in query_by_key:
                continue
            focus_queries.append(query_by_key[key])
            if focus_value > priority_by_key.get(key, -1.0):
                priority_by_key[key] = focus_value
                reason_by_key[key] = {
                    "query": query_by_key[key],
                    "cohort": "research_focus",
                    "priority_value": round(focus_value, 4),
                    "cohort_priority_value": round(focus_value, 4),
                    "source": "research_focus",
                    "learning_state": (focus_payload.get("price_action_feedback") or {}).get("learning_state"),
                    "summary": focus_payload.get("summary"),
                }
        for raw_query in focus_payload.get("suppressed_queries", []):
            key = _query_key(str(raw_query or ""))
            if key and key in query_by_key:
                suppressed_keys.add(key)
    if feedback_learning_state == "suppress_negative_price_action_and_broaden":
        for raw_query in price_action_feedback.get("collection_queries", []):
            key = _query_key(str(raw_query or ""))
            if not key or key not in query_by_key:
                continue
            query = query_by_key[key]
            if query not in feedback_broaden_queries:
                feedback_broaden_queries.append(query)
    priority_keys = sorted(priority_by_key, key=lambda key: priority_by_key[key], reverse=True)
    priority_queries = [query_by_key[key] for key in priority_keys]
    priority_set = {query.lower() for query in priority_queries}
    suppressed_tail = [
        query
        for query in queries
        if query.lower() not in priority_set and _query_key(query) in suppressed_keys
    ]
    ordered = (
        priority_queries
        + [
            query
            for query in queries
            if query.lower() not in priority_set and _query_key(query) not in suppressed_keys
        ]
        + suppressed_tail
    )
    return ordered, {
        "enabled": enabled,
        "priority_queries": priority_queries,
        "top_cohorts": [reason_by_key[key] for key in priority_keys[:6]],
        "min_priority_value": min_value,
        "require_positive_evidence": require_positive,
        "research_focus_enabled": focus_enabled,
        "research_focus_queries": focus_queries,
        "suppressed_queries": suppressed_tail,
        "feedback_learning_state": feedback_learning_state,
        "feedback_broaden_queries": feedback_broaden_queries,
    }


def _apply_feedback_broaden_reserve(
    *,
    selected: list[str],
    ordered_queries: list[str],
    adaptive_priority: dict[str, Any],
    max_queries: int,
    settings: dict[str, Any],
) -> list[str]:
    if max_queries <= 1:
        return selected
    if adaptive_priority.get("feedback_learning_state") != "suppress_negative_price_action_and_broaden":
        return selected
    reserve_enabled = _truthy_setting(settings.get("feedback_broaden_reserve_enabled", True), default=True)
    if not reserve_enabled:
        return selected
    slots = int(safe_float(settings.get("feedback_broaden_reserve_slots")) or 1)
    slots = max(0, min(slots, max_queries - 1))
    if slots <= 0:
        return selected
    broaden_queries = [
        query
        for query in adaptive_priority.get("feedback_broaden_queries", [])
        if query in ordered_queries
    ]
    reserved: list[str] = []
    for query in broaden_queries:
        if query not in reserved:
            reserved.append(query)
        if len(reserved) >= slots:
            break
    if not reserved:
        return selected
    merged = [query for query in selected if query not in reserved][: max_queries - len(reserved)] + reserved
    for query in ordered_queries:
        if len(merged) >= max_queries:
            break
        if query not in merged:
            merged.append(query)
    return merged[:max_queries]


def _select_scan_queries(cfg, default_query: str, *, scan_sequence: int) -> tuple[list[str], dict[str, Any]]:
    all_queries, mode = _configured_scan_queries(cfg, default_query)
    settings = cfg.raw.get("paper_market_scan", {}) or {}
    max_queries = int(
        _env_override("POLYMARKET_MAX_SCAN_QUERIES")
        or str(settings.get("max_queries_per_cycle", 0) or 0)
    )
    if not all_queries:
        all_queries = [default_query]
    ordered_queries, adaptive_priority = _adaptive_query_order(cfg, all_queries)
    if mode == "batch":
        selected = ordered_queries[:max_queries] if max_queries > 0 else ordered_queries
        if max_queries > 0:
            selected = _apply_feedback_broaden_reserve(
                selected=selected,
                ordered_queries=ordered_queries,
                adaptive_priority=adaptive_priority,
                max_queries=max_queries,
                settings=settings,
            )
    elif mode == "rotate":
        width = max(1, max_queries) if max_queries > 0 else 1
        start = max(0, scan_sequence - 1) % len(ordered_queries)
        selected = [
            ordered_queries[(start + offset) % len(ordered_queries)]
            for offset in range(min(width, len(ordered_queries)))
        ]
    else:
        selected = [ordered_queries[0]]
    return selected, {
        "mode": mode,
        "all_queries": all_queries,
        "ordered_queries": ordered_queries,
        "selected_queries": selected,
        "scan_sequence": scan_sequence,
        "max_queries_per_cycle": max_queries,
        "adaptive_priority": adaptive_priority,
    }


def _query_slug(query: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in query).strip("-")
    return slug or "query"


def _query_attempts(cfg, query: str) -> list[str]:
    settings = cfg.raw.get("paper_market_scan", {}) or {}
    aliases = settings.get("query_aliases", {}) or {}
    attempts = [query]
    if isinstance(aliases, dict):
        for alias in aliases.get(query, aliases.get(query.lower(), [])) or []:
            alias_text = str(alias).strip()
            if alias_text and alias_text.lower() not in {item.lower() for item in attempts}:
                attempts.append(alias_text)
    return attempts


def _merge_by_token(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        token = str(row.get("token_id") or "").strip()
        key = token or "|".join(str(row.get(part, "")) for part in ["market_slug", "outcome", "question"])
        if key:
            merged[key] = row
    return list(merged.values())


def scan_once(cfg, *, scan_sequence: int = 1) -> dict[str, Any]:
    base_config = scanner.BotConfig.from_env()
    base_output_dir = _abs(base_config.output_dir)
    queries, scan_plan = _select_scan_queries(cfg, base_config.query, scan_sequence=scan_sequence)
    if base_config.event_slug:
        queries = [base_config.query]
        scan_plan["selected_queries"] = queries
        scan_plan["event_slug_override"] = True

    scan_rows: list[dict[str, Any]] = []
    opportunity_rows: list[dict[str, Any]] = []
    per_query: list[dict[str, Any]] = []
    used_temp_outputs = len(queries) > 1

    for query in queries:
        attempts = _query_attempts(cfg, query)
        final_attempt: dict[str, Any] | None = None
        for index, actual_query in enumerate(attempts):
            query_output_dir = base_output_dir
            if len(queries) > 1 or len(attempts) > 1:
                used_temp_outputs = True
                query_output_dir = base_output_dir / "query_scans" / _query_slug(f"{query}-{actual_query}")
            config = dataclasses.replace(
                base_config,
                query=actual_query,
                output_dir=query_output_dir,
                mode="scan",
                execute_live=False,
                model_csv=_abs(base_config.model_csv),
                positions_csv=_abs(base_config.positions_csv),
            )
            token_count, opportunity_count = scanner.run_once(config, live_executor=None)
            final_attempt = {
                "query": query,
                "actual_query": actual_query,
                "attempt": index + 1,
                "attempts": attempts,
                "tokens": token_count,
                "scanner_opportunities": opportunity_count,
                "snapshot_path": str(query_output_dir / "market_snapshot.csv"),
            }
            if token_count > 0 or index == len(attempts) - 1:
                scan_rows.extend(_read_rows(query_output_dir / "market_snapshot.csv"))
                opportunity_rows.extend(_read_rows(query_output_dir / "opportunities.csv"))
                break
        if final_attempt is not None:
            per_query.append(final_attempt)

    if used_temp_outputs:
        scan_rows = _merge_by_token(scan_rows)
        _write_rows(base_output_dir / "market_snapshot.csv", scan_rows, SNAPSHOT_FIELDS)
        _write_rows(base_output_dir / "opportunities.csv", opportunity_rows, OPPORTUNITY_FIELDS)

    config = dataclasses.replace(
        base_config,
        mode="scan",
        execute_live=False,
        model_csv=_abs(base_config.model_csv),
        positions_csv=_abs(base_config.positions_csv),
        output_dir=base_output_dir,
    )
    return {
        "scan_plan": scan_plan,
        "queries": queries,
        "per_query": per_query,
        "tokens": len(scan_rows) if len(queries) > 1 else (per_query[0]["tokens"] if per_query else 0),
        "scanner_opportunities": len(opportunity_rows) if len(queries) > 1 else (
            per_query[0]["scanner_opportunities"] if per_query else 0
        ),
        "snapshot_path": str(config.output_dir / "market_snapshot.csv"),
    }


def refresh_repo_worldcup_fundamentals(cfg) -> dict[str, Any]:
    settings = cfg.raw.get("mispricing_alpha", {})
    if not settings.get("use_fundamental_probabilities", True):
        return {"status": "disabled"}
    script = ROOT / "scripts" / "build_repo_worldcup_winner_probabilities.py"
    if not script.exists():
        return {"status": "missing_script", "script": str(script)}
    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(ROOT),
            check=False,
            capture_output=True,
            text=True,
            timeout=float(settings.get("fundamental_refresh_timeout_seconds", 90)),
        )
    except Exception as exc:  # noqa: BLE001 - fundamentals are additive, not a reason to stop paper collection
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    return {
        "status": "ran" if result.returncode == 0 else "error",
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-2000:],
        "stderr_tail": result.stderr[-2000:],
    }


def refresh_independent_fundamentals(cfg, schedule: dict[str, Any], iteration: int) -> dict[str, Any]:
    """Refresh independent anchors used by the mispricing-alpha fundamental slot.

    Each sub-step is additive and fail-soft: missing Odds API credentials, absent
    crypto target files, or network errors should reduce available edge evidence,
    not stop the local paper loop.
    """
    result: dict[str, Any] = {
        "status": "skipped",
        "sharp_odds_fetch": {"status": "skipped"},
        "sharp_anchor": {"status": "skipped"},
        "crypto_fundamental": {"status": "skipped"},
    }
    if not bool(cfg.raw.get("mispricing_alpha", {}).get("use_fundamental_probabilities", True)):
        result["status"] = "disabled"
        return result

    ran = False
    if _scheduled(schedule, "sharp_odds_fetch_every_iterations", iteration, default=12):
        ran = True
        try:
            result["sharp_odds_fetch"] = fetch_sharp_odds(cfg)
        except Exception as exc:  # noqa: BLE001 - independent anchor fetch is additive
            result["sharp_odds_fetch"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    if _scheduled(schedule, "sharp_anchor_build_every_iterations", iteration, default=12):
        ran = True
        try:
            result["sharp_anchor"] = build_sharp_anchor(cfg)
        except Exception as exc:  # noqa: BLE001 - independent anchor build is additive
            result["sharp_anchor"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    if _scheduled(schedule, "crypto_fundamental_every_iterations", iteration, default=12):
        ran = True
        try:
            result["crypto_fundamental"] = build_crypto_fundamental(cfg)
        except Exception as exc:  # noqa: BLE001 - independent crypto anchor is additive
            result["crypto_fundamental"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    result["status"] = "ran" if ran else "skipped_by_schedule"
    return result


def run_iteration(*, config_path: Path, optimize_model: bool, iteration: int, paper_source: str = "raw_snapshot") -> dict[str, Any]:
    force_paper_environment()
    ensure_scanner_runtime_files()
    cfg = load_config(config_path)
    if cfg.trading_mode not in {"paper", "backtest"}:
        raise RuntimeError(f"trading.mode is {cfg.trading_mode}; expected paper or backtest")

    guard = _resource_guard(cfg)
    write_json(cfg.governance_root / "runtime_resource_guard.json", {**guard, "generated_at_utc": now_utc()})
    if guard.get("skip_cycle"):
        heartbeat = {
            "status": "skipped_resource_guard",
            "generated_at_utc": now_utc(),
            "iteration": iteration,
            "live_data": False,
            "live_trading": False,
            "resource_guard": guard,
            "message": "Heavy paper cycle skipped to protect local machine resources.",
        }
        write_json(cfg.governance_root / "live_paper_loop_heartbeat.json", heartbeat)
        if _truthy((cfg.raw.get("runtime_resource_guard", {}) or {}).get("render_dashboard_on_skip", False)):
            heartbeat["dashboard"] = render_dashboard(cfg)
        return heartbeat

    schedule = cfg.raw.get("runtime_schedule", {}) or {}
    if not _scheduled(schedule, "full_scan_every_iterations", iteration, default=1):
        settlement = _run_settlement_only_cycle(cfg)
        heartbeat = {
            "status": "settlement_only",
            "generated_at_utc": now_utc(),
            "iteration": iteration,
            "live_data": False,
            "live_trading": False,
            "resource_guard": guard,
            "runtime_schedule": {
                "full_scan_ran": False,
                "settlement_only_ran": True,
            },
            "settlement": settlement,
        }
        write_json(cfg.governance_root / "live_paper_loop_heartbeat.json", heartbeat)
        return heartbeat

    full_scan_every = int(schedule.get("full_scan_every_iterations", 1) or 1)
    scan_sequence = iteration if full_scan_every <= 1 else (1 if iteration == 1 else (iteration // full_scan_every) + 1)
    scan = scan_once(cfg, scan_sequence=scan_sequence)
    fundamentals = refresh_repo_worldcup_fundamentals(cfg)
    independent_fundamentals = refresh_independent_fundamentals(cfg, schedule, iteration)
    ingest = ingest_scanner_snapshot(cfg, snapshot_path=scan["snapshot_path"])
    optimization: dict[str, Any] = {"status": "skipped"}
    if (
        optimize_model
        and bool(cfg.raw.get("ml_optimization", {}).get("enabled", True))
        and _scheduled(schedule, "optimize_model_every_iterations", iteration, default=12)
    ):
        optimization = train_optimized_model(cfg)
    alpha: dict[str, Any] = {"status": "skipped"}
    if bool(cfg.raw.get("mispricing_alpha", {}).get("enabled", True)) and _scheduled(
        schedule,
        "mispricing_alpha_every_iterations",
        iteration,
        default=12,
    ):
        alpha = train_mispricing_alpha_model(cfg)
    strategy_search = (
        run_edge_strategy_search(cfg)
        if bool(cfg.raw.get("edge_strategy_search", {}).get("enabled", True))
        and _scheduled(schedule, "edge_strategy_search_every_iterations", iteration, default=12)
        else {"status": "skipped"}
    )
    paper = run_paper_cycle(cfg, source=paper_source)
    promoted_rule_shadow = (
        run_promoted_rule_shadow_scan(cfg)
        if bool(cfg.raw.get("promoted_rule_shadow", {}).get("enabled", True))
        and _scheduled(schedule, "promoted_rule_shadow_every_iterations", iteration, default=3)
        else {"status": "skipped"}
    )
    liquidity_discovery = (
        run_liquidity_discovery(cfg)
        if bool(cfg.raw.get("liquidity_discovery", {}).get("enabled", True))
        and _scheduled(schedule, "liquidity_discovery_every_iterations", iteration, default=12)
        else {"status": "skipped"}
    )
    heartbeat = {
        "status": "ran",
        "generated_at_utc": now_utc(),
        "iteration": iteration,
        "live_data": True,
        "live_trading": False,
        "scan": scan,
        "fundamentals": fundamentals,
        "independent_fundamentals": independent_fundamentals,
        "ingest": ingest,
        "optimization": {
            "status": optimization.get("status"),
            "deployment_mode": optimization.get("deployment_mode"),
            "model_version": optimization.get("model_version"),
            "holdout": optimization.get("holdout", {}),
        },
        "mispricing_alpha": {
            "status": alpha.get("status"),
            "deployment_mode": alpha.get("deployment_mode"),
            "model_version": alpha.get("model_version"),
            "training_rows": alpha.get("training_rows"),
            "training_markets": alpha.get("training_markets"),
        },
        "edge_strategy_search": {
            "status": strategy_search.get("status"),
            "promotable_rules": strategy_search.get("promotable_rules"),
            "top_rules": strategy_search.get("top_rules", [])[:3],
        },
        "promoted_rule_shadow": {
            "status": promoted_rule_shadow.get("status"),
            "promoted_rules": promoted_rule_shadow.get("promoted_rules"),
            "exploratory_rules": promoted_rule_shadow.get("exploratory_rules"),
            "rules_scanned": promoted_rule_shadow.get("rules_scanned"),
            "candidates": promoted_rule_shadow.get("candidates"),
            "rejected": promoted_rule_shadow.get("rejected"),
            "scans": promoted_rule_shadow.get("scans", [])[:3],
        },
        "liquidity_discovery": {
            "status": liquidity_discovery.get("status"),
            "tokens_scanned": liquidity_discovery.get("tokens_scanned"),
            "tradable_tokens": liquidity_discovery.get("tradable_tokens"),
            "top_tradable": liquidity_discovery.get("top_tradable", [])[:5],
            "family_summary": liquidity_discovery.get("family_summary", [])[:10],
        },
        "resource_guard": guard,
        "runtime_schedule": {
            "full_scan_ran": True,
            "settlement_only_ran": False,
            "optimize_model_ran": optimization.get("status") != "skipped",
            "independent_fundamentals_ran": independent_fundamentals.get("status") == "ran",
            "mispricing_alpha_ran": alpha.get("status") != "skipped",
            "edge_strategy_search_ran": strategy_search.get("status") != "skipped",
            "promoted_rule_shadow_ran": promoted_rule_shadow.get("status") != "skipped",
            "liquidity_discovery_ran": liquidity_discovery.get("status") != "skipped",
        },
        "paper": paper,
    }
    write_json(cfg.governance_root / "live_paper_loop_heartbeat.json", heartbeat)
    if _scheduled(schedule, "dashboard_render_every_iterations", iteration, default=2):
        heartbeat["dashboard"] = render_dashboard(cfg)
    else:
        heartbeat["dashboard"] = {"status": "skipped_by_runtime_schedule"}
    return heartbeat


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_polymarket_live_paper_loop.py")
    parser.add_argument("--config", default="polymarket_predictive_config.example.yaml")
    parser.add_argument("--iterations", type=int, default=1, help="Number of cycles to run; 0 means run forever.")
    parser.add_argument("--sleep-seconds", type=float, default=60.0)
    parser.add_argument("--optimize-model", action="store_true")
    parser.add_argument("--paper-source", choices=["raw_snapshot", "websocket"], default="raw_snapshot")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = _abs(Path(args.config))
    failures = 0
    iteration = 0
    while True:
        iteration += 1
        try:
            heartbeat = run_iteration(
                config_path=config_path,
                optimize_model=args.optimize_model,
                iteration=iteration,
                paper_source=args.paper_source,
            )
            print(f"live-paper-loop iteration={iteration} status={heartbeat['status']}", flush=True)
        except Exception as exc:
            failures += 1
            traceback.print_exc()
            try:
                cfg = load_config(config_path)
                write_json(
                    cfg.governance_root / "live_paper_loop_heartbeat.json",
                    {
                        "status": "error",
                        "generated_at_utc": now_utc(),
                        "iteration": iteration,
                        "live_trading": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
            except Exception:
                pass
        if args.iterations > 0 and iteration >= args.iterations:
            break
        if args.iterations == 0 or iteration < args.iterations:
            time.sleep(max(0.0, args.sleep_seconds))
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
