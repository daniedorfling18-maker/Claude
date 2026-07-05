from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any
from zoneinfo import ZoneInfo

from .config import EngineConfig, load_config
from .utils import now_utc, read_csv_rows, safe_float, write_csv, write_json


_UPDOWN_SLUG_RE = re.compile(r"(?P<asset>btc|eth|sol|xrp)-updown-(?P<interval>5m|15m|4h|daily)-(?P<timestamp>\d{10})")
_INTERVAL_MINUTES = {
    "5m": 5,
    "15m": 15,
    "4h": 4 * 60,
    "daily": 24 * 60,
}
SAST = ZoneInfo("Africa/Johannesburg")


def _parse_updown_window(row: dict[str, Any]) -> tuple[datetime | None, datetime | None, str]:
    slug = str(row.get("market_slug") or "").strip().lower()
    match = _UPDOWN_SLUG_RE.search(slug)
    if not match:
        return None, None, "market_slug_not_parseable"
    start = datetime.fromtimestamp(int(match.group("timestamp")), tz=timezone.utc)
    minutes = _INTERVAL_MINUTES.get(match.group("interval"), 0)
    if minutes <= 0:
        return start, None, "interval_not_supported"
    return start, start + timedelta(minutes=minutes), "ok"


def _iso(dt: datetime | None, tz: ZoneInfo | timezone = timezone.utc) -> str:
    if dt is None:
        return ""
    return dt.astimezone(tz).replace(microsecond=0).isoformat()


def _planner_row(row: dict[str, Any], *, now_dt: datetime, rescore_delay_seconds: int) -> dict[str, Any]:
    start, end, parse_status = _parse_updown_window(row)
    run_at = start + timedelta(seconds=rescore_delay_seconds) if start is not None else None
    if start is None or end is None or run_at is None:
        status = "UNSCHEDULED"
        seconds_until_run = ""
    else:
        seconds = (run_at - now_dt).total_seconds()
        seconds_until_run = round(seconds, 3)
        if now_dt < run_at:
            status = "WAIT"
        elif start <= now_dt <= end:
            status = "RUN_NOW"
        else:
            status = "EXPIRED"
    command = (
        "polymarket-engine collect-websocket --config polymarket_predictive_config.example.yaml --websocket-seconds 30; "
        "polymarket-engine paper-cycle --config polymarket_predictive_config.example.yaml --paper-source websocket; "
        "polymarket-engine profit-sprint --config polymarket_predictive_config.example.yaml"
    )
    return {
        "status": status,
        "parse_status": parse_status,
        "target_action": row.get("target_action", ""),
        "rank": row.get("rank", ""),
        "market_slug": row.get("market_slug", ""),
        "outcome": row.get("outcome", ""),
        "category": row.get("category", ""),
        "signal_cohort": row.get("signal_cohort", ""),
        "question": row.get("question", ""),
        "edge_lower_bound": row.get("edge_lower_bound", ""),
        "liquidity": row.get("liquidity", ""),
        "spread": row.get("spread", ""),
        "window_start_utc": _iso(start),
        "window_end_utc": _iso(end),
        "run_at_utc": _iso(run_at),
        "window_start_sast": _iso(start, SAST),
        "window_end_sast": _iso(end, SAST),
        "run_at_sast": _iso(run_at, SAST),
        "seconds_until_run": seconds_until_run,
        "minutes_until_run": "" if seconds_until_run == "" else round(float(seconds_until_run) / 60.0, 3),
        "recommended_query": row.get("recommended_query", ""),
        "reason": row.get("reason", ""),
        "next_command": command,
    }


def build_active_window_plan(cfg: EngineConfig) -> dict[str, Any]:
    settings = cfg.raw.get("active_window_planner", {}) or {}
    rescore_delay_seconds = int(settings.get("rescore_delay_seconds", 60) or 60)
    now_dt = datetime.now(timezone.utc)
    targets = [
        row
        for row in read_csv_rows(cfg.governance_root / "profit_sprint_targets.csv")
        if str(row.get("target_action") or "").strip() == "WAIT_ACTIVE_WINDOW"
    ]
    rows = [_planner_row(row, now_dt=now_dt, rescore_delay_seconds=rescore_delay_seconds) for row in targets]
    rows.sort(
        key=lambda row: (
            row.get("status") != "RUN_NOW",
            row.get("status") != "WAIT",
            safe_float(row.get("seconds_until_run")) if safe_float(row.get("seconds_until_run")) is not None else 1e18,
            safe_float(row.get("rank")) if safe_float(row.get("rank")) is not None else 1e18,
        )
    )
    runnable = [row for row in rows if row.get("status") == "RUN_NOW"]
    waiting = [row for row in rows if row.get("status") == "WAIT"]
    next_target = runnable[0] if runnable else waiting[0] if waiting else rows[0] if rows else {}

    out_json = cfg.governance_root / "active_window_plan.json"
    out_csv = cfg.governance_root / "active_window_plan.csv"
    write_csv(out_csv, rows)
    payload = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "planner_timezone": "Africa/Johannesburg",
        "rescore_delay_seconds": rescore_delay_seconds,
        "targets": len(rows),
        "runnable_now": len(runnable),
        "waiting": len(waiting),
        "next_target": next_target,
        "rows": rows,
        "plan_file": str(out_json),
        "csv_file": str(out_csv),
        "notes": [
            "This planner does not trade; it only schedules when WAIT_ACTIVE_WINDOW targets should be rescored.",
            "Use the existing paper-cycle and profit-sprint gates after each active-window rescore.",
        ],
    }
    write_json(out_json, payload)
    return payload


def main(config_path: str) -> dict[str, Any]:
    return build_active_window_plan(load_config(config_path))
