"""
check_superbru_fixtures.py

Automated Superbru fixture checker.

Scans the Superbru pool for upcoming fixtures, then verifies that every fixture
kicking off inside the horizon is covered by an auto-pick cron entry in
.github/workflows/auto_pick.yml. A fixture is "covered" when some cron fires
1..COVER_MAX_LEAD minutes before its kickoff.

For any uncovered fixture it proposes a cron that fires LEAD_MINUTES before
kickoff. With --apply it edits auto_pick.yml in place (de-duplicated); the
calling workflow then opens a pull request with the additions.

The script never submits picks and never modifies anything other than the
workflow file (only with --apply) and its own report outputs.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import auto_pick_match_scoped as ap  # noqa: E402

WORKFLOW_PATH = ROOT / ".github/workflows/auto_pick.yml"
LEAD_MINUTES = 25       # proposed crons fire this many minutes before kickoff
COVER_MAX_LEAD = 45     # a fixture is covered if a cron fires 1..45 min before kickoff


def parse_workflow_crons(path: Path) -> list[dict[str, Any]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    on = {}
    if isinstance(raw, dict):
        on = raw.get(True, raw.get("on", {})) or {}
    schedule = on.get("schedule", []) if isinstance(on, dict) else []
    crons: list[dict[str, Any]] = []
    for item in schedule or []:
        expr = str(item.get("cron", "")).strip()
        parts = expr.split()
        if len(parts) != 5:
            continue
        crons.append(
            {"expr": expr, "minute": parts[0], "hour": parts[1], "dom": parts[2], "month": parts[3], "dow": parts[4]}
        )
    return crons


def _field_matches(field: str, value: int) -> bool:
    if field == "*":
        return True
    for token in field.split(","):
        token = token.strip()
        if "-" in token:
            lo, hi = token.split("-", 1)
            if lo.isdigit() and hi.isdigit() and int(lo) <= value <= int(hi):
                return True
        elif token.isdigit() and int(token) == value:
            return True
    return False


def covered_by(crons: list[dict[str, Any]], kickoff: datetime) -> str | None:
    """Return the cron expression that covers this kickoff, or None."""
    for cron in crons:
        if not cron["minute"].isdigit() or not cron["hour"].isdigit():
            continue
        minute, hour = int(cron["minute"]), int(cron["hour"])
        # A cron's day-of-month is the day it fires, which may be the day before a
        # post-midnight kickoff, so check both the kickoff date and the day before.
        for delta_days in (0, 1):
            day = (kickoff - timedelta(days=delta_days)).date()
            if not _field_matches(cron["month"], day.month):
                continue
            if not _field_matches(cron["dom"], day.day):
                continue
            fire = datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)
            lead = (kickoff - fire).total_seconds() / 60.0
            if 0 < lead <= COVER_MAX_LEAD:
                return cron["expr"]
    return None


def suggest_cron(kickoff: datetime) -> str:
    fire = kickoff - timedelta(minutes=LEAD_MINUTES)
    return f"{fire.minute:02d} {fire.hour} {fire.day} {fire.month} *"


def _normalise_expr(expr: str) -> str:
    return " ".join(expr.split())


def insert_crons(path: Path, additions: list[tuple[str, str]]) -> list[str]:
    """Insert `(expr, comment)` cron lines into the schedule block. Returns applied exprs."""
    lines = path.read_text(encoding="utf-8").splitlines()
    existing = {_normalise_expr(c["expr"]) for c in parse_workflow_crons(path)}

    insert_at: int | None = None
    for index, line in enumerate(lines):
        if line.strip().startswith("workflow_dispatch:"):
            break
        if line.strip().startswith("- cron:"):
            insert_at = index
    if insert_at is None:
        return []

    applied: list[str] = []
    new_lines: list[str] = []
    for expr, comment in additions:
        if _normalise_expr(expr) in existing:
            continue
        existing.add(_normalise_expr(expr))
        new_lines.append(f"    - cron: '{expr}'   # {comment}")
        applied.append(expr)
    if not new_lines:
        return []

    lines[insert_at + 1 : insert_at + 1] = new_lines
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return applied


async def run(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)

    scan_args = SimpleNamespace(
        email=args.email,
        password=args.password,
        login_url=args.login_url,
        pool_url=args.pool_url,
        window_minutes=100000,
        headless=args.headless,
    )
    scan_result = await ap.scan_superbru_matches(scan_args, out_dir)
    scan_results, _queued, scan_status = scan_result[:3]
    pool_standing = scan_result[3] if len(scan_result) > 3 else {"status": "unavailable"}

    crons = parse_workflow_crons(WORKFLOW_PATH)
    fixtures: list[dict[str, Any]] = []
    gaps: list[tuple[str, str]] = []
    seen_exprs: set[str] = set()

    for result in scan_results:
        record = dict(result)
        kickoff = ap.parse_iso_datetime(result.get("kickoff_utc"))
        if kickoff is None:
            record["coverage"] = "unknown_kickoff"
            fixtures.append(record)
            continue
        within = now <= kickoff <= now + timedelta(hours=args.horizon_hours)
        record["kickoff_utc_parsed"] = ap.iso_z(kickoff)
        record["within_horizon"] = within
        cover = covered_by(crons, kickoff)
        record["covered_by"] = cover
        record["coverage"] = "covered" if cover else "uncovered"
        if within and not result.get("locked") and cover is None:
            expr = suggest_cron(kickoff)
            comment = f"AUTO {kickoff:%b %d %H:%M} UTC – {result.get('home_team')}/{result.get('away_team')}"
            record["suggested_cron"] = expr
            if _normalise_expr(expr) not in seen_exprs:
                seen_exprs.add(_normalise_expr(expr))
                gaps.append((expr, comment))
        fixtures.append(record)

    applied: list[str] = []
    if gaps and args.apply:
        applied = insert_crons(WORKFLOW_PATH, gaps)

    report = {
        "checked_at_utc": now.isoformat(),
        "scan_status": scan_status,
        "pool_standing": pool_standing,
        "horizon_hours": args.horizon_hours,
        "fixtures_scanned": len(scan_results),
        "fixtures_within_horizon": sum(1 for f in fixtures if f.get("within_horizon")),
        "uncovered_fixtures": sum(1 for f in fixtures if f.get("coverage") == "uncovered" and f.get("within_horizon")),
        "suggested_crons": [{"cron": expr, "comment": comment} for expr, comment in gaps],
        "applied_crons": applied,
        "applied_to_workflow": bool(applied),
        "fixtures": fixtures,
    }
    (out_dir / "superbru_fixture_check.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    _write_pr_body(out_dir / "pr_body.md", gaps, now)

    print(json.dumps({k: v for k, v in report.items() if k != "fixtures"}, indent=2, default=str))
    return report


def _write_pr_body(path: Path, gaps: list[tuple[str, str]], now: datetime) -> None:
    if not gaps:
        path.write_text("No uncovered Superbru fixtures found; no cron changes proposed.\n", encoding="utf-8")
        return
    lines = [
        "## Auto-pick cron update",
        "",
        f"The Superbru fixture checker ({now:%Y-%m-%d %H:%M} UTC) found fixtures with no auto-pick cron coverage.",
        f"It added the following entries to `.github/workflows/auto_pick.yml`, each firing {LEAD_MINUTES} minutes before kickoff:",
        "",
        "| Cron (UTC) | Fixture |",
        "| --- | --- |",
    ]
    for expr, comment in gaps:
        lines.append(f"| `{expr}` | {comment.replace('AUTO ', '')} |")
    lines += [
        "",
        "Review the kickoff times against the official schedule before merging.",
        "",
        "🤖 Generated with [Claude Code](https://claude.com/claude-code)",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check Superbru fixtures against auto-pick cron coverage.")
    parser.add_argument("--email", default=os.environ.get("SUPERBRU_EMAIL", ""))
    parser.add_argument("--password", default=os.environ.get("SUPERBRU_PASSWORD", ""))
    parser.add_argument("--login-url", default="https://www.superbru.com/login")
    parser.add_argument("--pool-url", default="https://www.superbru.com/worldcup_predictor/pool_view.php?t=1296&p=13236623&view=matches")
    parser.add_argument("--horizon-hours", type=int, default=48)
    parser.add_argument("--out-dir", default="outputs/fixture_check")
    parser.add_argument("--headless", dest="headless", action="store_true", default=True)
    parser.add_argument("--headed", dest="headless", action="store_false")
    parser.add_argument("--apply", action="store_true", help="Edit auto_pick.yml in place with suggested cron entries.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.email or not args.password:
        print("ERROR: SUPERBRU_EMAIL and SUPERBRU_PASSWORD must be set", file=sys.stderr)
        return 1
    report = asyncio.run(run(args))
    return 0 if report.get("scan_status") != "login_failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
