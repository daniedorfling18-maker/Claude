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
from polymarket_predictive_engine.dutch_arb_monitor import run_dutch_arb_monitor  # noqa: E402
from polymarket_predictive_engine.mispricing_alpha import train_mispricing_alpha_model  # noqa: E402
from polymarket_predictive_engine.models.optimized import train_optimized_model  # noqa: E402
from polymarket_predictive_engine.paper_cycle import run_paper_cycle  # noqa: E402
from polymarket_predictive_engine.sharp_anchor import build_sharp_anchor  # noqa: E402
from polymarket_predictive_engine.sharp_odds_fetch import fetch_sharp_odds  # noqa: E402
from polymarket_predictive_engine.shadow_cohort import update_shadow_cohort_evidence  # noqa: E402
from polymarket_predictive_engine.snapshot_ingest import ingest_scanner_snapshot  # noqa: E402
from polymarket_predictive_engine.storage import connect_db  # noqa: E402
from polymarket_predictive_engine.strategy_search import run_edge_strategy_search  # noqa: E402
from polymarket_predictive_engine.utils import now_utc, parse_timestamp, read_json, safe_float, write_json  # noqa: E402
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


def _cgroup_memory_percent(base: Path = Path("/sys/fs/cgroup")) -> float | None:
    """Container memory load against the cgroup limit, or None when unlimited/absent.

    Inside Docker, /proc/meminfo reports the HOST, so a host at 69% can hide a
    container thrashing at its own mem_limit (observed 2026-07-07: paper-live at 78%
    of its 4GiB cap while the host guard at 99% never fired). Mirrors `docker stats`:
    usage minus inactive file cache, over the hard limit. Supports cgroup v2 and v1.
    """
    try:
        # cgroup v2
        max_path = base / "memory.max"
        current_path = base / "memory.current"
        stat_path = base / "memory.stat"
        if not max_path.exists():
            # cgroup v1 fallback
            max_path = base / "memory" / "memory.limit_in_bytes"
            current_path = base / "memory" / "memory.usage_in_bytes"
            stat_path = base / "memory" / "memory.stat"
        if not (max_path.exists() and current_path.exists()):
            return None
        raw_limit = max_path.read_text(encoding="utf-8").strip()
        if raw_limit == "max":
            return None
        limit = float(raw_limit)
        # v1 reports an enormous sentinel instead of "max" when unlimited.
        if limit <= 0 or limit >= float(1 << 60):
            return None
        usage = float(current_path.read_text(encoding="utf-8").strip())
        if stat_path.exists():
            for line in stat_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[0] in {"inactive_file", "total_inactive_file"}:
                    usage = max(0.0, usage - float(parts[1]))
                    break
        return max(0.0, min(100.0, 100.0 * usage / limit))
    except Exception:
        return None


def _current_memory_percent() -> float | None:
    """Return memory load using only stdlib, or None if unavailable.

    Reports the WORSE of host memory load and container cgroup load, so the
    resource guard degrades before either constraint starts swap-thrashing.
    """
    cgroup_percent = _cgroup_memory_percent()
    host_percent = _host_memory_percent()
    candidates = [value for value in (cgroup_percent, host_percent) if value is not None]
    return max(candidates) if candidates else None


def _host_memory_percent() -> float | None:
    """Host-level memory load using only stdlib, or None if unavailable."""
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
    host_percent = _host_memory_percent()
    cgroup_percent = _cgroup_memory_percent()
    candidates = [value for value in (cgroup_percent, host_percent) if value is not None]
    memory_percent = max(candidates) if candidates else None
    max_memory = float(settings.get("max_memory_percent", 92.0))
    enabled = _truthy(settings.get("enabled", True), default=True)
    skip = bool(enabled and memory_percent is not None and memory_percent >= max_memory)
    return {
        "enabled": enabled,
        "skip_cycle": skip,
        "memory_percent": memory_percent,
        "host_memory_percent": host_percent,
        "container_memory_percent": cgroup_percent,
        "max_memory_percent": max_memory,
        "reason": "memory_above_limit" if skip else "ok",
    }


def _scheduled(settings: dict[str, Any], key: str, iteration: int, *, default: int = 1) -> bool:
    every = int(settings.get(key, default) or default)
    if every <= 0:
        return False
    return iteration == 1 or iteration % every == 0


def _minutes_since(timestamp: Any) -> float | None:
    parsed = parse_timestamp(timestamp)
    current = parse_timestamp(now_utc())
    if parsed is None or current is None:
        return None
    return max(0.0, (current - parsed).total_seconds() / 60.0)


def _setting_float(settings: dict[str, Any], key: str, default: float) -> float:
    value = settings.get(key, default)
    if value is None or value == "":
        return default
    return float(value)


def _setting_int(settings: dict[str, Any], key: str, default: int) -> int:
    value = settings.get(key, default)
    if value is None or value == "":
        return default
    return int(value)


def run_dutch_arb_loop_pass(cfg) -> dict[str, Any]:
    """Run at most one Dutch-book arb scan for the VPS loop.

    This is a scanner-only edge monitor. It never calls a broker and never places an order.
    """
    settings = cfg.raw.get("dutch_arb", {}) or {}
    enabled = _truthy(settings.get("enabled", True), default=True)
    base = {
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
        "live_trading": False,
        "dry_run": True,
    }
    if not enabled:
        return {**base, "status": "disabled"}

    out_dir = cfg.output_root / "polymarket_arbitrage"
    latest = read_json(out_dir / "dutch_arb_monitor_summary.json", default={}) or {}
    if not isinstance(latest, dict):
        latest = {}

    interval = _setting_float(settings, "pass_interval_minutes", 15)
    age_minutes = _minutes_since(latest.get("generated_at_utc"))
    if age_minutes is not None and age_minutes < interval:
        return {
            **base,
            "status": "skipped_interval",
            "last_scan_at_utc": latest.get("generated_at_utc"),
            "last_scan_age_minutes": round(age_minutes, 3),
            "next_due_minutes": round(max(0.0, interval - age_minutes), 3),
            "pass_interval_minutes": interval,
        }

    summary = run_dutch_arb_monitor(
        cfg,
        polls=1,
        poll_seconds=0,
        max_events=_setting_int(settings, "max_events_per_pass", 20),
        max_pages=_setting_int(settings, "max_pages_per_pass", _setting_int(settings, "max_pages", 4)),
        max_outcomes=_setting_int(settings, "max_outcomes", 80),
        min_ask_sum=_setting_float(settings, "min_ask_sum", 0.85),
        min_annualised=_setting_float(settings, "min_annualised", 0.0),
        alert_annualised=_setting_float(settings, "alert_annualised", 0.10),
        pause=_setting_float(settings, "request_pause_seconds", 0.02),
        timeout=_setting_int(settings, "request_timeout_seconds", 20),
    )
    return {
        **base,
        **summary,
        "status": summary.get("status", "paper_analysis"),
        "pass_interval_minutes": interval,
    }


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


def _is_updown_query(value: str) -> bool:
    text = f" {str(value or '').strip().lower().replace('-', ' ')} "
    compact = _query_key(text)
    return "updown" in compact or " up or down " in text or " up/down " in text


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
        keys.extend(["fifa world cup", "world cup winner", "worldcup", "world cup"])
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


def _research_focus_gap_needs_collection(focus_payload: dict[str, Any]) -> bool:
    price_action_model = focus_payload.get("price_action_model")
    if not isinstance(price_action_model, dict):
        return False
    return _truthy_setting(price_action_model.get("validation_gap_needs_collection"), default=False)


def _extend_with_research_focus_queries(
    cfg,
    queries: list[str],
    *,
    focus_payload: dict[str, Any],
) -> tuple[list[str], list[str]]:
    settings = cfg.raw.get("paper_market_scan", {}) or {}
    enabled = _truthy_setting(settings.get("use_research_focus_priority", True), default=True)
    inject_enabled = _truthy_setting(settings.get("inject_research_focus_queries", True), default=True)
    if not enabled or not inject_enabled or not focus_payload:
        return queries, []

    seen = {_query_key(query) for query in queries if _query_key(query)}
    expanded = list(queries)
    injected: list[str] = []
    focus_queries = list(focus_payload.get("collection_queries", []) or [])
    guard = focus_payload.get("collection_query_guard")
    if isinstance(guard, dict):
        focus_queries.extend(guard.get("raw_collection_queries", []) or [])
        rejected = guard.get("rejected_queries", []) or []
        if isinstance(rejected, list):
            focus_queries.extend(
                row.get("query")
                for row in rejected
                if isinstance(row, dict) and str(row.get("reason") or "") == "max_updown_queries"
            )
    price_action_model = focus_payload.get("price_action_model")
    if isinstance(price_action_model, dict):
        focus_queries.extend(price_action_model.get("historical_breadth_queries", []) or [])
        focus_queries.extend(price_action_model.get("paper_confirmation_blocker_queries", []) or [])
        focus_queries.extend(price_action_model.get("validation_gap_queries", []) or [])
    breadth = focus_payload.get("price_action_historical_breadth")
    if isinstance(breadth, dict):
        focus_queries.extend(breadth.get("recommended_collection_queries", []) or [])
        near_positive = breadth.get("top_near_positive_buckets") or []
        if isinstance(near_positive, list):
            focus_queries.extend(
                row.get("recommended_collection_query")
                for row in near_positive
                if isinstance(row, dict)
            )

    for raw_query in focus_queries:
        query = str(raw_query or "").strip()
        key = _query_key(query)
        if not query or not key or key in seen:
            continue
        expanded.append(query)
        injected.append(query)
        seen.add(key)
    return expanded, injected


def _broad_repricing_queries(settings: dict[str, Any]) -> list[str]:
    raw = settings.get("broad_repricing_queries") or []
    if not isinstance(raw, list):
        raw = [raw]
    seen: set[str] = set()
    queries: list[str] = []
    for item in raw:
        query = str(item or "").strip()
        key = _query_key(query)
        if not query or not key or key in seen:
            continue
        queries.append(query)
        seen.add(key)
    return queries


def _extend_with_broad_repricing_queries(
    queries: list[str],
    *,
    settings: dict[str, Any],
) -> tuple[list[str], list[str]]:
    if not _truthy_setting(settings.get("broad_repricing_reserve_enabled", False), default=False):
        return queries, []
    seen = {_query_key(query) for query in queries if _query_key(query)}
    expanded = list(queries)
    injected: list[str] = []
    for query in _broad_repricing_queries(settings):
        key = _query_key(query)
        if key in seen:
            continue
        expanded.append(query)
        injected.append(query)
        seen.add(key)
    return expanded, injected


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
    evidence_updown_queries: list[str] = []
    if focus_enabled and focus_payload:
        validation_gap_needs_collection = _research_focus_gap_needs_collection(focus_payload)
        focus_value = float(settings.get("research_focus_priority_value", min_value + 1.0))
        if validation_gap_needs_collection:
            focus_value = max(
                focus_value,
                float(settings.get("research_focus_validation_gap_priority_value", min_value + 47.0)),
            )
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
                    "validation_gap_needs_collection": validation_gap_needs_collection,
                    "summary": focus_payload.get("summary"),
                }
        for raw_query in focus_payload.get("suppressed_queries", []):
            key = _query_key(str(raw_query or ""))
            if key and key in query_by_key:
                suppressed_keys.add(key)
        guard = focus_payload.get("collection_query_guard")
        guarded_updown = []
        if isinstance(guard, dict):
            guarded_updown = [
                str(query or "").strip()
                for query in guard.get("updown_queries", []) or []
                if str(query or "").strip()
            ]
            raw_updown = [
                str(query or "").strip()
                for query in guard.get("raw_collection_queries", []) or []
                if str(query or "").strip() and _is_updown_query(str(query or ""))
            ]
            rejected_updown = [
                str(row.get("query") or "").strip()
                for row in guard.get("rejected_queries", []) or []
                if isinstance(row, dict)
                and str(row.get("query") or "").strip()
                and str(row.get("reason") or "") == "max_updown_queries"
            ]
            for raw_query in [*guarded_updown, *raw_updown, *rejected_updown]:
                key = _query_key(raw_query)
                if key and key in query_by_key and query_by_key[key] not in evidence_updown_queries:
                    evidence_updown_queries.append(query_by_key[key])
        price_action_model = focus_payload.get("price_action_model")
        if isinstance(price_action_model, dict):
            for raw_query in [
                *(price_action_model.get("historical_breadth_queries", []) or []),
                *(price_action_model.get("paper_confirmation_blocker_queries", []) or []),
            ]:
                query = str(raw_query or "").strip()
                key = _query_key(query)
                if _is_updown_query(query) and key in query_by_key and query_by_key[key] not in evidence_updown_queries:
                    evidence_updown_queries.append(query_by_key[key])
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
        "research_focus_validation_gap_needs_collection": _research_focus_gap_needs_collection(focus_payload),
        "suppressed_queries": suppressed_tail,
        "feedback_learning_state": feedback_learning_state,
        "feedback_broaden_queries": feedback_broaden_queries,
        "evidence_updown_queries": evidence_updown_queries,
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


def _apply_research_focus_reserve(
    *,
    selected: list[str],
    ordered_queries: list[str],
    adaptive_priority: dict[str, Any],
    max_queries: int,
    settings: dict[str, Any],
    scan_sequence: int,
) -> tuple[list[str], list[str]]:
    """Reserve scan slots for the governed research-focus collection target.

    This is observation routing only.  It prevents older high-priority cohorts
    from crowding out the current evidence request; downstream model,
    governance, and paper gates still decide whether anything is tradable.
    """
    if max_queries <= 1:
        return selected, []
    enabled = _truthy_setting(settings.get("research_focus_reserve_enabled", True), default=True)
    if not enabled:
        return selected, []
    focus_queries = [
        str(query or "").strip()
        for query in adaptive_priority.get("research_focus_queries", []) or []
        if str(query or "").strip()
    ]
    if not focus_queries:
        return selected, []

    slots = int(safe_float(settings.get("research_focus_reserve_slots")) or 3)
    slots = max(0, min(slots, max_queries - 1))
    if slots <= 0:
        return selected, []

    ordered_by_key = {_query_key(query): query for query in ordered_queries if _query_key(query)}
    selected_keys = {_query_key(query) for query in selected if _query_key(query)}
    missing_focus = [
        ordered_by_key.get(_query_key(query), query)
        for query in focus_queries
        if _query_key(query) and _query_key(query) in ordered_by_key and _query_key(query) not in selected_keys
    ]
    if not missing_focus:
        return selected, []

    start = max(0, scan_sequence - 1) % len(missing_focus)
    rotated = [missing_focus[(start + offset) % len(missing_focus)] for offset in range(len(missing_focus))]
    focus_keys = {_query_key(query) for query in focus_queries if _query_key(query)}
    protected_selected = [query for query in selected if _query_key(query) in focus_keys]
    remaining_capacity = max(0, max_queries - len(protected_selected))
    reserved = rotated[: min(slots, remaining_capacity)]
    if not reserved:
        return selected, []

    reserved_keys = {_query_key(query) for query in reserved}
    merged = list(protected_selected) + reserved
    for query in selected:
        if len(merged) >= max_queries:
            break
        key = _query_key(query)
        if key in reserved_keys or query in merged:
            continue
        merged.append(query)
    for query in ordered_queries:
        if len(merged) >= max_queries:
            break
        key = _query_key(query)
        if key in reserved_keys or query in merged:
            continue
        merged.append(query)
    return merged[:max_queries], reserved


def _apply_evidence_updown_rotation(
    *,
    selected: list[str],
    ordered_queries: list[str],
    adaptive_priority: dict[str, Any],
    max_queries: int,
    settings: dict[str, Any],
    scan_sequence: int,
) -> tuple[list[str], list[str]]:
    """Rotate the single guarded up/down evidence slot across assets.

    The research-focus guard deliberately caps up/down concentration.  Without
    this second-stage rotation, the first guarded query (often BTC) can occupy
    that slot forever and starve SOL/ETH/XRP of fresh bid/ask examples.  This
    keeps the cap intact while rotating which up/down market gets observed.
    """
    if max_queries <= 0:
        return selected, []
    if not _truthy_setting(settings.get("evidence_updown_rotation_enabled", True), default=True):
        return selected, []
    slots = int(safe_float(settings.get("evidence_updown_rotation_slots")) or 1)
    slots = max(0, min(slots, max_queries))
    if slots <= 0:
        return selected, []

    ordered_by_key = {_query_key(query): query for query in ordered_queries if _query_key(query)}
    candidates: list[str] = []
    for raw_query in adaptive_priority.get("evidence_updown_queries", []) or []:
        query = str(raw_query or "").strip()
        key = _query_key(query)
        if not key or key not in ordered_by_key or not _is_updown_query(query):
            continue
        query = ordered_by_key[key]
        if query not in candidates:
            candidates.append(query)
    if not candidates:
        return selected, []

    start = max(0, scan_sequence - 1) % len(candidates)
    desired = [candidates[(start + offset) % len(candidates)] for offset in range(min(slots, len(candidates)))]
    desired_keys = {_query_key(query) for query in desired if _query_key(query)}
    if not desired_keys:
        return selected, []

    merged: list[str] = []
    used_keys: set[str] = set()
    for query in desired:
        key = _query_key(query)
        if key and key not in used_keys:
            merged.append(query)
            used_keys.add(key)

    for query in selected:
        key = _query_key(query)
        if not key or key in used_keys:
            continue
        if _is_updown_query(query) and key not in desired_keys:
            continue
        merged.append(query)
        used_keys.add(key)
        if len(merged) >= max_queries:
            break

    for query in ordered_queries:
        if len(merged) >= max_queries:
            break
        key = _query_key(query)
        if not key or key in used_keys:
            continue
        if _is_updown_query(query) and key not in desired_keys:
            continue
        merged.append(query)
        used_keys.add(key)

    return merged[:max_queries], desired


def _apply_broad_repricing_reserve(
    *,
    selected: list[str],
    ordered_queries: list[str],
    max_queries: int,
    settings: dict[str, Any],
    scan_sequence: int,
) -> tuple[list[str], list[str]]:
    """Keep at least one scan slot for broad event repricing discovery.

    Evidence-priority queries are valuable, but if they fill every batch slot the
    model quietly becomes a crypto-only system. This reserve is for observation
    breadth only; trade promotion still requires cohort-specific bid/ask
    validation downstream.
    """
    if max_queries <= 1:
        return selected, []
    if not _truthy_setting(settings.get("broad_repricing_reserve_enabled", False), default=False):
        return selected, []
    slots = int(safe_float(settings.get("broad_repricing_reserve_slots")) or 1)
    slots = max(0, min(slots, max_queries - 1))
    if slots <= 0:
        return selected, []

    broad_keys = {_query_key(query) for query in _broad_repricing_queries(settings)}
    if not broad_keys:
        return selected, []
    ordered_broad = [query for query in ordered_queries if _query_key(query) in broad_keys]
    if not ordered_broad:
        return selected, []

    selected_broad = [query for query in selected if _query_key(query) in broad_keys]
    missing_slots = max(0, slots - len(selected_broad))
    if missing_slots <= 0:
        return selected, []

    start = max(0, scan_sequence - 1) % len(ordered_broad)
    rotated = [ordered_broad[(start + offset) % len(ordered_broad)] for offset in range(len(ordered_broad))]
    reserved: list[str] = []
    selected_keys = {_query_key(query) for query in selected}
    for query in rotated:
        key = _query_key(query)
        if not key or key in selected_keys:
            continue
        reserved.append(query)
        if len(reserved) >= missing_slots:
            break
    if not reserved:
        return selected, []

    reserved_keys = {_query_key(query) for query in reserved}
    merged = [query for query in selected if _query_key(query) not in reserved_keys][: max_queries - len(reserved)] + reserved
    for query in ordered_queries:
        if len(merged) >= max_queries:
            break
        if query not in merged:
            merged.append(query)
    return merged[:max_queries], reserved


def _select_scan_queries(cfg, default_query: str, *, scan_sequence: int) -> tuple[list[str], dict[str, Any]]:
    all_queries, mode = _configured_scan_queries(cfg, default_query)
    settings = cfg.raw.get("paper_market_scan", {}) or {}
    max_queries = int(
        _env_override("POLYMARKET_MAX_SCAN_QUERIES")
        or str(settings.get("max_queries_per_cycle", 0) or 0)
    )
    if not all_queries:
        all_queries = [default_query]
    configured_queries = list(all_queries)
    focus_payload = _load_research_focus(cfg)
    all_queries, injected_queries = _extend_with_research_focus_queries(
        cfg,
        all_queries,
        focus_payload=focus_payload,
    )
    all_queries, injected_broad_queries = _extend_with_broad_repricing_queries(
        all_queries,
        settings=settings,
    )
    ordered_queries, adaptive_priority = _adaptive_query_order(cfg, all_queries)
    research_focus_reserved_queries: list[str] = []
    evidence_updown_rotated_queries: list[str] = []
    broad_reserved_queries: list[str] = []
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
            selected, research_focus_reserved_queries = _apply_research_focus_reserve(
                selected=selected,
                ordered_queries=ordered_queries,
                adaptive_priority=adaptive_priority,
                max_queries=max_queries,
                settings=settings,
                scan_sequence=scan_sequence,
            )
            selected, evidence_updown_rotated_queries = _apply_evidence_updown_rotation(
                selected=selected,
                ordered_queries=ordered_queries,
                adaptive_priority=adaptive_priority,
                max_queries=max_queries,
                settings=settings,
                scan_sequence=scan_sequence,
            )
            selected, broad_reserved_queries = _apply_broad_repricing_reserve(
                selected=selected,
                ordered_queries=ordered_queries,
                max_queries=max_queries,
                settings=settings,
                scan_sequence=scan_sequence,
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
        "configured_queries": configured_queries,
        "injected_research_focus_queries": injected_queries,
        "injected_broad_repricing_queries": injected_broad_queries,
        "all_queries": all_queries,
        "ordered_queries": ordered_queries,
        "selected_queries": selected,
        "research_focus_reserved_queries": research_focus_reserved_queries,
        "evidence_updown_rotated_queries": evidence_updown_rotated_queries,
        "evidence_updown_rotation_enabled": _truthy_setting(
            settings.get("evidence_updown_rotation_enabled", True),
            default=True,
        ),
        "research_focus_reserve_enabled": _truthy_setting(
            settings.get("research_focus_reserve_enabled", True),
            default=True,
        ),
        "broad_repricing_reserved_queries": broad_reserved_queries,
        "broad_repricing_queries": _broad_repricing_queries(settings),
        "broad_repricing_reserve_enabled": _truthy_setting(
            settings.get("broad_repricing_reserve_enabled", False),
            default=False,
        ),
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

    Sharp-anchor refresh is a first-class edge input: expected states such as
    missing Odds API credentials or budget skips are returned as artifacts, but
    coding/build errors should fail loud instead of silently hiding a dead edge
    path. Crypto target files remain additive/fail-soft.
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
        result["sharp_odds_fetch"] = fetch_sharp_odds(cfg)

    if _scheduled(schedule, "sharp_anchor_build_every_iterations", iteration, default=12):
        ran = True
        result["sharp_anchor"] = build_sharp_anchor(cfg)

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
    dutch_arb = run_dutch_arb_loop_pass(cfg)
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
        "dutch_arb": {
            "status": dutch_arb.get("status"),
            "generated_at_utc": dutch_arb.get("generated_at_utc"),
            "last_scan_at_utc": dutch_arb.get("last_scan_at_utc"),
            "events_scanned": (dutch_arb.get("scan_stats_latest_poll") or {}).get("discovered"),
            "events_priced_complete": dutch_arb.get("events_priced_complete_latest_poll"),
            "complete_arbs": dutch_arb.get("complete_arbs_latest_poll"),
            "alerts_total": dutch_arb.get("alerts_total"),
            "persistent_alert_count": dutch_arb.get("persistent_alert_count"),
            "best_annualised_return_on_capital": dutch_arb.get("best_annualised_return_on_capital"),
            "best_opportunity": dutch_arb.get("best_opportunity"),
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
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
            "dutch_arb_ran": dutch_arb.get("status") == "paper_analysis",
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
