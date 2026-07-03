#!/usr/bin/env python3
"""CLV-vs-close experiment: is the SuperBru engine's edge market-grade?

The decisive question: do the locked SuperBru picks systematically beat the
sharp closing line? Winning a pool proves we beat other hobby pickers; beating
the de-vigged close proves market-grade forecasting skill worth real stakes.

Three subcommands:

```text
extract-picks  rebuild pick history from the git commits of the locked card
snapshot       fetch + de-vig current World Cup h2h odds (budget-guarded);
               append to the odds snapshot series
report         join picks x snapshots; CLV per pick = P_close - P_pick of the
               picked outcome; fail-closed verdict with bootstrap CI
```

Fail-closed: below the minimum sample the verdict is `insufficient_samples`.
Diagnostic only — this writes evidence artifacts; it stakes nothing anywhere.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from superbru_score_engine.model.devig import devig_implied_probabilities  # noqa: E402
from superbru_score_engine.model.team_names import canonical_team_key  # noqa: E402

CARD_PATH = "outputs/final_locked_picks/superbru_final_card.csv"
OUT_DIR = ROOT / "outputs" / "superbru_clv"
PICKS_CSV = OUT_DIR / "locked_pick_history.csv"
SNAPSHOTS_CSV = OUT_DIR / "odds_snapshots.csv"
REPORT_JSON = OUT_DIR / "clv_report.json"
REPORT_CSV = OUT_DIR / "clv_per_pick.csv"

SPORT_KEY = "soccer_fifa_world_cup"
OUTCOMES = ("home", "draw", "away")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(text: str) -> datetime | None:
    text = str(text or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _match_key(home: str, away: str, commence: str) -> str:
    day = str(commence or "")[:10]
    return f"{canonical_team_key(home)}|{canonical_team_key(away)}|{day}"


def _pick_outcome(pick: str) -> str | None:
    parts = str(pick or "").strip().split("-")
    if len(parts) != 2:
        return None
    try:
        home_goals, away_goals = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if home_goals > away_goals:
        return "home"
    if home_goals < away_goals:
        return "away"
    return "draw"


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------- extract-picks

def extract_picks() -> dict:
    """Rebuild the locked-pick history from git commits of the card file."""
    log = subprocess.run(
        ["git", "log", "--all", "--format=%H %cI", "--", CARD_PATH],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()
    rows: dict[tuple[str, str], dict] = {}
    for line in reversed(log):  # oldest first so later locks overwrite earlier
        commit, _, committed_at = line.partition(" ")
        show = subprocess.run(
            ["git", "show", f"{commit}:{CARD_PATH}"],
            cwd=ROOT, capture_output=True, text=True,
        )
        if show.returncode != 0:
            continue
        for row in csv.DictReader(show.stdout.splitlines()):
            home = row.get("home_team", "")
            away = row.get("away_team", "")
            commence = row.get("commence_time", "")
            pick = row.get("locked_pick", "")
            if not (home and away and commence and pick):
                continue
            locked_at = _parse_ts(committed_at)
            kickoff = _parse_ts(commence)
            # The pick that counts is the last one locked BEFORE kickoff.
            if locked_at is None or (kickoff is not None and locked_at >= kickoff):
                continue
            key = (_match_key(home, away, commence), commence)
            rows[key] = {
                "locked_at_utc": _fmt(locked_at),
                "commence_time": commence,
                "home_team": home,
                "away_team": away,
                "locked_pick": pick,
                "pick_outcome": _pick_outcome(pick) or "",
                "match_key": key[0],
            }
    out = sorted(rows.values(), key=lambda r: (r["commence_time"], r["match_key"]))
    _write_csv(PICKS_CSV, out, ["locked_at_utc", "commence_time", "home_team", "away_team", "locked_pick", "pick_outcome", "match_key"])
    return {"status": "ok", "picks": len(out), "path": str(PICKS_CSV)}


# ------------------------------------------------------------------- snapshot

def _devig_h2h(event: dict, bookmaker_priority: list[str]) -> dict | None:
    books = {str(b.get("key")): b for b in event.get("bookmakers", [])}
    for book_key in bookmaker_priority:
        book = books.get(book_key)
        if not book:
            continue
        for market in book.get("markets", []):
            if market.get("key") != "h2h":
                continue
            prices: dict[str, float] = {}
            for outcome in market.get("outcomes", []):
                name, price = str(outcome.get("name", "")), outcome.get("price")
                if not isinstance(price, (int, float)) or price <= 1:
                    continue
                if canonical_team_key(name) == canonical_team_key(event.get("home_team", "")):
                    prices["home"] = float(price)
                elif canonical_team_key(name) == canonical_team_key(event.get("away_team", "")):
                    prices["away"] = float(price)
                elif name.strip().lower() == "draw":
                    prices["draw"] = float(price)
            if len(prices) == 3:
                implied = np.array([1.0 / prices[o] for o in OUTCOMES])
                fair = devig_implied_probabilities(implied, method="power")
                return {"bookmaker": book_key, **{f"p_{o}": round(float(fair[i]), 6) for i, o in enumerate(OUTCOMES)}}
    return None


def snapshot(input_path: str | None, minimum_interval_minutes: float, bookmaker_priority: list[str]) -> dict:
    now = _utc_now()
    existing = _read_csv(SNAPSHOTS_CSV)
    if existing and minimum_interval_minutes > 0:
        last = max((_parse_ts(r.get("snapshot_at_utc", "")) for r in existing if _parse_ts(r.get("snapshot_at_utc", ""))), default=None)
        if last is not None and (now - last) < timedelta(minutes=minimum_interval_minutes):
            return {"status": "skipped_interval", "last_snapshot_utc": _fmt(last), "minimum_interval_minutes": minimum_interval_minutes}

    if input_path:
        events = json.loads(Path(input_path).read_text(encoding="utf-8"))
    else:
        api_key = os.environ.get("THE_ODDS_API_KEY", "").strip()
        if not api_key:
            return {"status": "skipped_missing_api_key"}
        query = urllib.parse.urlencode({"apiKey": api_key, "regions": "eu", "markets": "h2h", "oddsFormat": "decimal"})
        url = f"https://api.the-odds-api.com/v4/sports/{SPORT_KEY}/odds?{query}"
        with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - fixed https host
            events = json.loads(response.read().decode("utf-8"))

    stamp = _fmt(now)
    added = 0
    for event in events if isinstance(events, list) else []:
        fair = _devig_h2h(event, bookmaker_priority)
        if fair is None:
            continue
        existing.append(
            {
                "snapshot_at_utc": stamp,
                "commence_time": str(event.get("commence_time", "")),
                "home_team": str(event.get("home_team", "")),
                "away_team": str(event.get("away_team", "")),
                "match_key": _match_key(event.get("home_team", ""), event.get("away_team", ""), event.get("commence_time", "")),
                **fair,
            }
        )
        added += 1
    _write_csv(
        SNAPSHOTS_CSV, existing,
        ["snapshot_at_utc", "commence_time", "home_team", "away_team", "match_key", "bookmaker", "p_home", "p_draw", "p_away"],
    )
    return {"status": "ok", "snapshot_at_utc": stamp, "events_priced": added, "total_rows": len(existing)}


# --------------------------------------------------------------------- report

def _bootstrap_ci(values: list[float], iterations: int = 2000, seed: int = 20260703) -> tuple[float | None, float | None]:
    if len(values) < 2:
        return None, None
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(iterations))
    return round(means[int(0.025 * iterations)], 6), round(means[min(iterations - 1, int(math.ceil(0.975 * iterations)) - 1)], 6)


def report(minimum_samples: int, max_close_age_minutes: float) -> dict:
    picks = _read_csv(PICKS_CSV)
    snapshots = _read_csv(SNAPSHOTS_CSV)
    by_match: dict[str, list[dict]] = {}
    for row in snapshots:
        ts = _parse_ts(row.get("snapshot_at_utc", ""))
        if ts is None:
            continue
        row["_ts"] = ts
        by_match.setdefault(str(row.get("match_key", "")), []).append(row)
    for series in by_match.values():
        series.sort(key=lambda r: r["_ts"])

    per_pick: list[dict] = []
    skipped = {"no_outcome": 0, "no_snapshots": 0, "no_pick_time_line": 0, "no_close_line": 0}
    for pick in picks:
        outcome = pick.get("pick_outcome", "")
        if outcome not in OUTCOMES:
            skipped["no_outcome"] += 1
            continue
        series = by_match.get(str(pick.get("match_key", "")), [])
        if not series:
            skipped["no_snapshots"] += 1
            continue
        locked_at = _parse_ts(pick.get("locked_at_utc", ""))
        kickoff = _parse_ts(pick.get("commence_time", ""))
        if locked_at is None or kickoff is None:
            skipped["no_outcome"] += 1
            continue
        at_pick = [r for r in series if r["_ts"] <= locked_at]
        # A pick locked before our snapshot series began still counts if a
        # snapshot exists shortly after the lock (line barely moves in minutes).
        if not at_pick:
            soon_after = [r for r in series if r["_ts"] <= locked_at + timedelta(minutes=90)]
            if not soon_after:
                skipped["no_pick_time_line"] += 1
                continue
            at_pick = soon_after[:1]
        pre_close = [r for r in series if r["_ts"] <= kickoff]
        if not pre_close:
            skipped["no_close_line"] += 1
            continue
        close_row = pre_close[-1]
        close_age_minutes = (kickoff - close_row["_ts"]).total_seconds() / 60.0
        if max_close_age_minutes > 0 and close_age_minutes > max_close_age_minutes:
            skipped["no_close_line"] += 1
            continue
        p_pick = float(at_pick[-1].get(f"p_{outcome}", 0) or 0)
        p_close = float(close_row.get(f"p_{outcome}", 0) or 0)
        if not (0 < p_pick < 1 and 0 < p_close < 1):
            skipped["no_close_line"] += 1
            continue
        per_pick.append(
            {
                "match_key": pick.get("match_key", ""),
                "home_team": pick.get("home_team", ""),
                "away_team": pick.get("away_team", ""),
                "commence_time": pick.get("commence_time", ""),
                "locked_pick": pick.get("locked_pick", ""),
                "pick_outcome": outcome,
                "p_pick_time": round(p_pick, 6),
                "p_close": round(p_close, 6),
                "clv": round(p_close - p_pick, 6),
                "beat_close": p_close > p_pick,
                "close_snapshot_age_minutes": round(close_age_minutes, 1),
            }
        )

    clvs = [row["clv"] for row in per_pick]
    ci_low, ci_high = _bootstrap_ci(clvs)
    n = len(per_pick)
    if n < minimum_samples:
        verdict = "insufficient_samples"
    elif ci_low is not None and ci_low > 0:
        verdict = "market_grade_edge_confirmed"
    elif ci_high is not None and ci_high < 0:
        verdict = "picks_lag_the_close"
    else:
        verdict = "no_detectable_edge_vs_close"
    payload = {
        "status": "ok",
        "generated_at_utc": _fmt(_utc_now()),
        "picks_seen": len(picks),
        "picks_scored": n,
        "skipped": skipped,
        "minimum_samples": minimum_samples,
        "mean_clv": round(sum(clvs) / n, 6) if n else None,
        "beat_close_rate": round(sum(1 for r in per_pick if r["beat_close"]) / n, 4) if n else None,
        "clv_ci_low": ci_low,
        "clv_ci_high": ci_high,
        "verdict": verdict,
        "interpretation": {
            "market_grade_edge_confirmed": "picks systematically beat the close: the SuperBru edge is market-grade and worth staking research",
            "no_detectable_edge_vs_close": "picks track the market: the SuperBru edge is pool-grade (beating hobby pickers), not market-grade",
            "picks_lag_the_close": "the market moves against picks after lock: do not stake this signal anywhere",
            "insufficient_samples": "keep the snapshot workflow running; verdict needs more matched picks",
        }[verdict],
        "stakes_placed": False,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _write_csv(
        REPORT_CSV, per_pick,
        ["match_key", "home_team", "away_team", "commence_time", "locked_pick", "pick_outcome", "p_pick_time", "p_close", "clv", "beat_close", "close_snapshot_age_minutes"],
    )
    REPORT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("extract-picks")
    snap = sub.add_parser("snapshot")
    snap.add_argument("--input", default=None, help="offline JSON events file (tests); omit to fetch live")
    snap.add_argument("--min-interval-minutes", type=float, default=150.0)
    snap.add_argument("--bookmakers", default="pinnacle,betfair_ex_eu,marathonbet,onexbet")
    rep = sub.add_parser("report")
    rep.add_argument("--minimum-samples", type=int, default=15)
    rep.add_argument("--max-close-age-minutes", type=float, default=360.0)
    args = parser.parse_args()
    if args.command == "extract-picks":
        result = extract_picks()
    elif args.command == "snapshot":
        result = snapshot(args.input, args.min_interval_minutes, [b.strip() for b in args.bookmakers.split(",") if b.strip()])
    else:
        result = report(args.minimum_samples, args.max_close_age_minutes)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
