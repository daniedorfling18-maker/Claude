"""
run_pregame_watcher.py

Continuous background watcher. Polls every 60 seconds and fires
run_pregame_check.py for any match kicking off within the next
5–15 minutes. Each match is only processed once per session.

Run this once at the start of a match day and leave it running until
all matches have kicked off.

Usage:
    python scripts/run_pregame_watcher.py
    python scripts/run_pregame_watcher.py --cdp-url http://127.0.0.1:9222
    python scripts/run_pregame_watcher.py --dry-run
    python scripts/run_pregame_watcher.py --poll-seconds 30

Stop with Ctrl+C.
"""
from __future__ import annotations

import argparse
import json
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CHECKS_DIR = ROOT / "outputs" / "pregame_checks"
LOG_FILE = CHECKS_DIR / "watcher.log"

WINDOW_EARLY_MIN = 5    # window opens this many minutes before kickoff
WINDOW_LATE_MIN = 15    # window closes this many minutes before kickoff
DEFAULT_POLL_SECONDS = 60


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Pre-kickoff watcher: polls and auto-submits pick changes.")
    p.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    p.add_argument("--pool-url", default="https://www.superbru.com/worldcup_predictor/pool_view.php?t=1296&p=13236623&g=32&view=matches")
    p.add_argument("--locked-picks-csv", default="outputs/final_locked_picks/superbru_final_card.csv")
    p.add_argument("--urls-csv", default="inputs/oddspedia_match_urls.csv")
    p.add_argument("--ev-threshold", type=float, default=0.15)
    p.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    p.add_argument("--impersonate", default="chrome124")
    p.add_argument("--dry-run", action="store_true")
    return p


def norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s or "").lower()).strip("-")


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    CHECKS_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_all_kickoffs(picks_csv: Path) -> list[dict[str, Any]]:
    if not picks_csv.exists():
        return []
    picks = pd.read_csv(picks_csv).fillna("")
    out = []
    for _, row in picks.iterrows():
        ct = str(row.get("commence_time", ""))
        if not ct:
            continue
        try:
            if ct.endswith("Z"):
                ct = ct[:-1] + "+00:00"
            kickoff = datetime.fromisoformat(ct)
            if kickoff.tzinfo is None:
                kickoff = kickoff.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        mid = f"{norm(row.get('home_team',''))}-{norm(row.get('away_team',''))}"
        out.append({
            "match_id": mid,
            "home_team": str(row.get("home_team", "")),
            "away_team": str(row.get("away_team", "")),
            "locked_pick": str(row.get("locked_pick", "")),
            "kickoff": kickoff,
        })
    return sorted(out, key=lambda x: x["kickoff"])


def in_window(kickoff: datetime, now: datetime, early: int, late: int) -> bool:
    delta = (kickoff - now).total_seconds() / 60
    return late >= delta >= early


def fire_check(match: dict[str, Any], args: argparse.Namespace) -> None:
    log(f"FIRING check: {match['home_team']} vs {match['away_team']}  locked={match['locked_pick']}  kickoff={match['kickoff'].isoformat()}")
    cmd = [
        sys.executable, "scripts/run_pregame_check.py",
        "--cdp-url", args.cdp_url,
        "--pool-url", args.pool_url,
        "--locked-picks-csv", args.locked_picks_csv,
        "--urls-csv", args.urls_csv,
        "--ev-threshold", str(args.ev_threshold),
        "--window-early-min", str(WINDOW_EARLY_MIN),
        "--window-late-min", str(WINDOW_LATE_MIN),
        "--match-id", match["match_id"],
        "--impersonate", args.impersonate,
    ]
    if args.dry_run:
        cmd.append("--dry-run")
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode == 0:
        log(f"Check complete: {match['match_id']}")
    else:
        log(f"Check FAILED (exit {result.returncode}): {match['match_id']}")


def main() -> int:
    args = build_parser().parse_args()
    CHECKS_DIR.mkdir(parents=True, exist_ok=True)
    fired: set[str] = set()
    running = True

    def _stop(sig: int, frame: Any) -> None:
        nonlocal running
        log("Watcher stopping (signal received).")
        running = False

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    picks_csv = ROOT / args.locked_picks_csv

    log(f"Watcher started. poll={args.poll_seconds}s  threshold={args.ev_threshold}  dry_run={args.dry_run}")
    log(f"Window: [{WINDOW_EARLY_MIN}–{WINDOW_LATE_MIN}] minutes before kickoff.")

    kickoffs = load_all_kickoffs(picks_csv)
    if not kickoffs:
        log("No fixtures found in locked picks CSV. Exiting.")
        return 1

    # Show today's upcoming fixtures
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")
    upcoming = [m for m in kickoffs if m["kickoff"].strftime("%Y-%m-%d") == today_str and m["kickoff"] > now]
    log(f"Today's remaining fixtures ({len(upcoming)}):")
    for m in upcoming:
        mins = round((m["kickoff"] - now).total_seconds() / 60)
        log(f"  {m['home_team']} vs {m['away_team']}  kickoff in {mins}min")

    while running:
        now = datetime.now(timezone.utc)
        kickoffs = load_all_kickoffs(picks_csv)  # reload in case picks updated

        for match in kickoffs:
            mid = match["match_id"]
            if mid in fired:
                continue
            if in_window(match["kickoff"], now, WINDOW_EARLY_MIN, WINDOW_LATE_MIN):
                fired.add(mid)
                fire_check(match, args)

        # Check if all today's matches are past or fired
        today_str = now.strftime("%Y-%m-%d")
        today_matches = [m for m in kickoffs if m["kickoff"].strftime("%Y-%m-%d") == today_str]
        all_done = all(m["match_id"] in fired or m["kickoff"] < now for m in today_matches)
        if today_matches and all_done:
            log("All today's matches have passed. Watcher shutting down.")
            break

        if running:
            time.sleep(args.poll_seconds)

    log("Watcher stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
