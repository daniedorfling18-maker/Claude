"""
run_pregame_check.py

Pre-kickoff check for a single match or all matches kicking off within
a time window. Scrapes fresh Oddspedia odds, re-evaluates EV against the
locked pick, and submits a corrected score to SuperBru via CDP if the EV
gap exceeds the threshold.

Called by run_pregame_watcher.py (automated) or directly for a single match.

Usage:
    # Check all matches kicking off in the next 5-15 minutes:
    python scripts/run_pregame_check.py \
        --cdp-url http://127.0.0.1:9222

    # Check a specific match by ID:
    python scripts/run_pregame_check.py \
        --cdp-url http://127.0.0.1:9222 \
        --match-id france-iraq

    # Dry run (no submission):
    python scripts/run_pregame_check.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CHECKS_DIR = ROOT / "outputs" / "pregame_checks"

EV_THRESHOLD = 0.15          # minimum EV gap to trigger a pick switch
WINDOW_MINUTES_EARLY = 5     # check window opens N minutes before kickoff
WINDOW_MINUTES_LATE = 15     # check window closes N minutes before kickoff


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Pre-kickoff odds refresh and auto-submit.")
    p.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    p.add_argument("--pool-url", default="https://www.superbru.com/worldcup_predictor/pool_view.php?t=1296&p=13236623&g=32&view=matches")
    p.add_argument("--locked-picks-csv", default="outputs/final_locked_picks/superbru_final_card.csv")
    p.add_argument("--urls-csv", default="inputs/oddspedia_match_urls.csv")
    p.add_argument("--ev-threshold", type=float, default=EV_THRESHOLD)
    p.add_argument("--window-early-min", type=int, default=WINDOW_MINUTES_EARLY, help="Trigger window opens N min before kickoff")
    p.add_argument("--window-late-min", type=int, default=WINDOW_MINUTES_LATE, help="Trigger window closes N min before kickoff")
    p.add_argument("--match-id", default="", help="Check a specific match ID only (skips time window check)")
    p.add_argument("--impersonate", default="chrome124")
    p.add_argument("--dry-run", action="store_true", help="Scrape + decide but don't submit to SuperBru")
    p.add_argument("--skip-scrape", action="store_true", help="Use existing grid CSVs")
    return p


def norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s or "").lower().strip()).strip("-")


def make_match_id(home: str, away: str) -> str:
    return f"{norm(home)}-{norm(away)}"


def load_upcoming_matches(picks_csv: Path, urls_csv: Path, now: datetime,
                           early_min: int, late_min: int) -> list[dict[str, Any]]:
    """Return locked picks whose kickoff falls in [now+late_min, now+early_min] (descending urgency)."""
    picks = pd.read_csv(picks_csv).fillna("") if picks_csv.exists() else pd.DataFrame()
    urls = pd.read_csv(urls_csv).fillna("") if urls_csv.exists() else pd.DataFrame()

    if picks.empty:
        return []

    picks["match_id"] = picks.apply(lambda r: make_match_id(r["home_team"], r["away_team"]), axis=1)

    window_open = now + timedelta(minutes=late_min)
    window_close = now + timedelta(minutes=early_min)

    upcoming = []
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

        if window_open <= kickoff <= window_close:
            mid = str(row["match_id"])
            url_row = urls[urls["match_id"] == mid] if not urls.empty and "match_id" in urls.columns else pd.DataFrame()
            upcoming.append({
                "match_id": mid,
                "home_team": str(row["home_team"]),
                "away_team": str(row["away_team"]),
                "locked_pick": str(row["locked_pick"]),
                "commence_time": kickoff.isoformat(),
                "url": str(url_row.iloc[0]["url"]) if not url_row.empty else "",
                "minutes_to_kickoff": round((kickoff - now).total_seconds() / 60, 1),
            })

    return sorted(upcoming, key=lambda x: x["minutes_to_kickoff"])


def scrape_match(url: str, impersonate: str, urls_csv: Path, match_id: str) -> bool:
    """Targeted single-match scrape using scrape_oddspedia_curl.py."""
    if not url:
        print(f"  No URL found for {match_id} — cannot scrape.")
        return False

    # Write a temporary single-row URL CSV for the scraper
    with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False, encoding="utf-8") as f:
        f.write("match_id,commence_time,home_team,away_team,url,alt_url,match_key,match_score\n")
        f.write(f"{match_id},,,, {url},,, \n")
        tmp_csv = f.name
    # Read the real row from the urls csv
    try:
        urls_df = pd.read_csv(urls_csv).fillna("")
        row = urls_df[urls_df["match_id"] == match_id]
        if not row.empty:
            row.to_csv(tmp_csv, index=False)
        else:
            os.unlink(tmp_csv)
            # Write minimal csv
            with open(tmp_csv, "w", encoding="utf-8") as f:
                f.write("match_id,commence_time,home_team,away_team,url,alt_url,match_key,match_score\n")
                f.write(f"{match_id},,,,{url},,0,0\n")
    except Exception:
        pass

    cmd = [
        sys.executable, "scripts/scrape_oddspedia_curl.py",
        "--urls-csv", tmp_csv,
        "--max-workers", "1",
        "--impersonate", impersonate,
    ]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=False)
    try:
        os.unlink(tmp_csv)
    except OSError:
        pass
    return result.returncode == 0


def run_ev_analysis(locked_picks_csv: Path) -> pd.DataFrame:
    """Re-run EV analysis and return recommendations."""
    steps = [
        [sys.executable, "scripts/build_oddspedia_score_shape_features.py"],
        [sys.executable, "scripts/build_oddspedia_superbru_ev.py", "--locked-picks-csv", str(locked_picks_csv)],
    ]
    for cmd in steps:
        subprocess.run(cmd, cwd=ROOT, capture_output=True)

    ev_csv = ROOT / "outputs/oddspedia_pick_validation/oddspedia_ev_recommendations.csv"
    if ev_csv.exists():
        return pd.read_csv(ev_csv).fillna("")
    return pd.DataFrame()


def should_switch(match_id: str, ev_df: pd.DataFrame, threshold: float) -> dict[str, Any] | None:
    """Return switch decision dict if EV gap exceeds threshold, else None."""
    row = ev_df[ev_df["match_id"] == match_id] if not ev_df.empty and "match_id" in ev_df.columns else pd.DataFrame()
    if row.empty:
        return None
    r = row.iloc[0]
    ev_gap = float(r.get("ev_gap_vs_locked", 0) or 0)
    if ev_gap >= threshold:
        return {
            "match_id": match_id,
            "current_pick": str(r.get("locked_pick", "")),
            "best_ev_scoreline": str(r.get("best_ev_scoreline", "")),
            "ev_gap": ev_gap,
            "current_ev": float(r.get("current_locked_pick_ev", 0) or 0),
            "best_ev": float(r.get("best_ev_expected_points", 0) or 0),
            "review_level": str(r.get("review_level", "")),
        }
    return None


def submit_pick(match: dict[str, Any], new_pick: str, cdp_url: str,
                pool_url: str, dry_run: bool) -> dict[str, Any]:
    cmd = [
        sys.executable, "scripts/submit_superbru_pick_cdp.py",
        "--cdp-url", cdp_url,
        "--pool-url", pool_url,
        "--home-team", match["home_team"],
        "--away-team", match["away_team"],
        "--new-pick", new_pick,
    ]
    if dry_run:
        cmd.append("--dry-run")
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    try:
        lines = result.stdout.strip().split("\n")
        # last JSON block in output
        for line in reversed(lines):
            line = line.strip()
            if line.startswith("{"):
                return json.loads(line)
    except Exception:
        pass
    return {"status": "error", "stdout": result.stdout[-500:], "stderr": result.stderr[-500:]}


def main() -> int:
    args = build_parser().parse_args()
    CHECKS_DIR.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    picks_csv = ROOT / args.locked_picks_csv
    urls_csv = ROOT / args.urls_csv

    print(f"\n[pregame-check] {now.isoformat()}  threshold={args.ev_threshold}  dry_run={args.dry_run}")

    # Resolve which matches to check
    if args.match_id:
        picks = pd.read_csv(picks_csv).fillna("") if picks_csv.exists() else pd.DataFrame()
        picks["match_id"] = picks.apply(lambda r: make_match_id(r["home_team"], r["away_team"]), axis=1)
        row = picks[picks["match_id"] == args.match_id]
        if row.empty:
            print(f"Match ID '{args.match_id}' not found in locked picks.")
            return 1
        r = row.iloc[0]
        urls_df = pd.read_csv(urls_csv).fillna("") if urls_csv.exists() else pd.DataFrame()
        url_row = urls_df[urls_df["match_id"] == args.match_id] if not urls_df.empty and "match_id" in urls_df.columns else pd.DataFrame()
        matches_to_check = [{
            "match_id": args.match_id,
            "home_team": str(r["home_team"]),
            "away_team": str(r["away_team"]),
            "locked_pick": str(r["locked_pick"]),
            "commence_time": str(r.get("commence_time", "")),
            "url": str(url_row.iloc[0]["url"]) if not url_row.empty else "",
            "minutes_to_kickoff": None,
        }]
    else:
        matches_to_check = load_upcoming_matches(
            picks_csv, urls_csv, now, args.window_early_min, args.window_late_min
        )

    if not matches_to_check:
        print("No matches in the pre-kickoff window. Nothing to do.")
        return 0

    print(f"Matches to check ({len(matches_to_check)}):")
    for m in matches_to_check:
        mins = f"{m['minutes_to_kickoff']}min" if m["minutes_to_kickoff"] is not None else "manual"
        print(f"  {m['home_team']} vs {m['away_team']}  (kickoff in {mins})  locked: {m['locked_pick']}")

    outcomes: list[dict[str, Any]] = []

    for match in matches_to_check:
        mid = match["match_id"]
        print(f"\n--- {match['home_team']} vs {match['away_team']} ---")

        # Scrape fresh odds
        if not args.skip_scrape:
            print("  Scraping fresh odds from Oddspedia...")
            ok = scrape_match(match["url"], args.impersonate, urls_csv, mid)
            if not ok:
                print("  WARNING: scrape failed. Using cached grid if available.")
        else:
            print("  Skipping scrape (--skip-scrape).")

        # Re-run EV analysis
        ev_df = run_ev_analysis(picks_csv)
        decision = should_switch(mid, ev_df, args.ev_threshold)

        if decision is None:
            ev_row = ev_df[ev_df["match_id"] == mid] if not ev_df.empty and "match_id" in ev_df.columns else pd.DataFrame()
            if ev_row.empty:
                print(f"  No EV data available. Keeping locked pick: {match['locked_pick']}")
            else:
                gap = float(ev_row.iloc[0].get("ev_gap_vs_locked", 0) or 0)
                best = ev_row.iloc[0].get("best_ev_scoreline", "")
                print(f"  EV gap {gap:.3f} < threshold {args.ev_threshold}. Keep: {match['locked_pick']}  (best alt: {best})")
            outcomes.append({"match_id": mid, "action": "keep", "pick": match["locked_pick"]})
            continue

        new_pick = decision["best_ev_scoreline"]
        print(f"  SWITCH RECOMMENDED: {match['locked_pick']} → {new_pick}  (EV gap {decision['ev_gap']:.3f})")

        submit_result = submit_pick(match, new_pick, args.cdp_url, args.pool_url, args.dry_run)
        print(f"  Submit result: {submit_result.get('status')}  {submit_result.get('reason','')}")
        outcomes.append({
            "match_id": mid,
            "action": "switch" if not args.dry_run else "switch_dry_run",
            "from_pick": match["locked_pick"],
            "to_pick": new_pick,
            "ev_gap": decision["ev_gap"],
            "submit_status": submit_result.get("status"),
        })

    # Write check log
    log = {
        "generated_at_utc": now.isoformat(),
        "dry_run": args.dry_run,
        "matches_checked": len(matches_to_check),
        "outcomes": outcomes,
    }
    ts = now.strftime("%Y%m%dT%H%M%SZ")
    log_path = CHECKS_DIR / f"pregame_check_{ts}.json"
    log_path.write_text(json.dumps(log, indent=2, default=str), encoding="utf-8")
    print(f"\nCheck log: {log_path}")
    print(json.dumps(log, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
