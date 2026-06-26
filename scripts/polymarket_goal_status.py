"""Print a lightweight status summary for the local Polymarket paper bot.

This intentionally avoids Docker, network calls, model scans, and imports from
the main package so it can be used when the machine is under memory pressure.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "outputs" / "polymarket_dashboard"
GOVERNANCE = ROOT / "outputs" / "polymarket_model_governance"


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def money(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except Exception:
        return "n/a"


def pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except Exception:
        return "n/a"


def local_ip_hint() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except Exception:
        return "YOUR_COMPUTER_IP"


def first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def top_watchlist_rows(payload: dict[str, Any], key: str, limit: int = 5) -> list[dict[str, Any]]:
    rows = payload.get(key)
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)][:limit]


def print_watchlist(title: str, rows: list[dict[str, Any]], *, shadow: bool = False) -> None:
    print(f"\n{title}")
    if not rows:
        print("  No watchlist rows yet. Need the bot to collect more forward evidence.")
        return
    for row in rows:
        score = f"{row.get('promotion_ready_score', 0)}/{row.get('promotion_ready_checks', '?')}"
        cohort = row.get("signal_cohort", "unknown")
        if shadow:
            pnl = money(row.get("shadow_total_pnl_usdc"))
            roi = pct(row.get("shadow_roi"))
            run_rate = money(row.get("shadow_monthly_run_rate_usdc"))
            fills = row.get("shadow_fills", 0)
            settled = row.get("shadow_sell_fills", 0)
        else:
            pnl = money(row.get("total_pnl_usdc"))
            roi = pct(row.get("roi"))
            run_rate = money(row.get("monthly_run_rate_usdc"))
            fills = row.get("buy_fills", 0)
            settled = row.get("settled_fills", row.get("sell_fills", 0))
        print(f"  - {cohort}: score {score}, pnl {pnl}, roi {roi}, run-rate {run_rate}, fills {fills}, settled {settled}")


def main() -> int:
    dashboard_data = read_json(DASHBOARD / "dashboard_data.json")
    target = first_dict(
        dashboard_data.get("actual_profit_target"),
        first_dict(dashboard_data.get("forward_paper_cycle")).get("actual_profit_target"),
        read_json(GOVERNANCE / "paper_profit_target_tracker.json"),
    )
    signal_cohorts = first_dict(
        dashboard_data.get("signal_cohort_pnl"),
        read_json(GOVERNANCE / "signal_cohort_pnl.json"),
    )
    shadow_cohorts = first_dict(
        dashboard_data.get("shadow_signal_cohort_pnl"),
        read_json(GOVERNANCE / "shadow_signal_cohort_pnl.json"),
    )
    heartbeat = first_dict(
        dashboard_data.get("heartbeat"),
        read_json(GOVERNANCE / "live_paper_loop_heartbeat.json"),
    )

    print("Polymarket paper bot goal status")
    print(f"Dashboard file: {DASHBOARD / 'index.html'}")
    print(f"Computer URL:   http://127.0.0.1:8765/")
    print(f"Phone URL:      http://{local_ip_hint()}:8765/")
    print(f"Data updated:   {dashboard_data.get('generated_at_utc') or 'n/a'}")
    print(f"Loop heartbeat: {heartbeat.get('status') or 'n/a'}")
    scan_plan = first_dict(first_dict(heartbeat.get("scan")).get("scan_plan"))
    adaptive = first_dict(scan_plan.get("adaptive_priority"))
    if scan_plan:
        selected = scan_plan.get("selected_queries") or []
        ordered = scan_plan.get("ordered_queries") or scan_plan.get("all_queries") or []
        priority = adaptive.get("priority_queries") or []
        print(f"Scan mode:      {scan_plan.get('mode') or 'n/a'}")
        print(f"Scanning now:   {', '.join(selected) if isinstance(selected, list) else selected}")
        print(f"Priority queue: {', '.join(priority) if isinstance(priority, list) and priority else ', '.join(ordered) if isinstance(ordered, list) else 'n/a'}")

    target_monthly = target.get("target_monthly_profit_usdc", 100)
    actual_pnl = target.get("actual_pnl_since_baseline_usdc")
    run_rate = target.get("monthly_run_rate_usdc")
    gap = None
    try:
        gap = float(target_monthly) - float(run_rate)
    except Exception:
        pass

    print("\n$100/month target")
    print(f"  Status:            {target.get('status') or 'n/a'}")
    print(f"  Current actual P&L: {money(actual_pnl)}")
    print(f"  Monthly target:    {money(target_monthly)}")
    print(f"  Monthly run-rate:  {money(run_rate)}")
    print(f"  Run-rate gap:      {money(gap) if gap is not None else 'n/a'}")

    promoted = signal_cohorts.get("promoted_cohorts")
    if not isinstance(promoted, list):
        promoted = []
    shadow_promoted = shadow_cohorts.get("promoted_cohorts")
    if not isinstance(shadow_promoted, list):
        shadow_promoted = []

    print("\nPromotion state")
    print(f"  Paper promoted cohorts:  {len(promoted)}")
    print(f"  Shadow promoted cohorts: {len(shadow_promoted)}")
    if not promoted and not shadow_promoted:
        print("  Trading should remain gated until a cohort proves positive forward evidence.")

    print_watchlist(
        "Nearest paper cohorts",
        top_watchlist_rows(signal_cohorts, "promotion_watchlist"),
        shadow=False,
    )
    print_watchlist(
        "Nearest shadow cohorts",
        top_watchlist_rows(shadow_cohorts, "promotion_watchlist"),
        shadow=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
