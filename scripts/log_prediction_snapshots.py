#!/usr/bin/env python3
"""Append a pre-match prediction snapshot per fixture to a rolling log.

The betting-validation gap surfaced by wc_predictive_power_validation.py is that
the engine never recorded, per fixture, (a) the model's pre-match probability and
(b) the price you could actually have bet. Without that you cannot test edge or
closing-line value after the fact. This script closes that gap.

For every fixture in the flattened market-odds file it records, at snapshot time:

  - the de-vigged consensus 1X2 probability (this IS the model's probability, since
    the engine is anchored to the same market consensus);
  - the BEST obtainable decimal odds per outcome across all books, and which book
    offers them (the price a backer could actually take);
  - the sharp reference (Pinnacle, and Betfair Exchange if present) de-vigged 1X2,
    so closing-line value can be measured against a genuinely independent anchor;
  - the implied edge of backing the model pick at the best price, measured against
    BOTH the consensus and the sharp reference.

The log is idempotent per day: re-running on the same date replaces that date's
rows for a match rather than duplicating them. wc_predictive_power_validation.py
joins it to trusted results once fixtures complete.
"""
from __future__ import annotations

import argparse
import csv
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

FLAT_ODDS_DEFAULT = "outputs/market_odds/worldcup_market_odds_flat.csv"
LOG_DEFAULT = "outputs/backtesting/prediction_log.csv"
SHARP_BOOKS = ("pinnacle",)
EXCHANGE_BOOKS = ("betfair_ex_uk", "betfair_ex_eu", "betfair_ex_au", "smarkets", "matchbook")
OUTCOMES = ("home", "draw", "away")

ALIASES = {
    "usa": "united states", "us": "united states", "korea republic": "south korea",
    "ir iran": "iran", "czechia": "czech republic",
    "bosnia and herzegovina": "bosnia herzegovina", "congo dr": "dr congo",
    "cote d ivoire": "ivory coast", "cabo verde": "cape verde", "turkiye": "turkey",
}

FIELDNAMES = [
    "snapshot_utc", "snapshot_date", "event_id", "commence_time", "home_team", "away_team",
    "match_key", "n_books_h2h",
    "p_home", "p_draw", "p_away",
    "best_home_odds", "best_draw_odds", "best_away_odds",
    "best_home_book", "best_draw_book", "best_away_book",
    "sharp_book", "sharp_p_home", "sharp_p_draw", "sharp_p_away",
    "exchange_book", "exchange_p_home", "exchange_p_draw", "exchange_p_away",
    "model_pick", "fav_prob", "fair_odds", "best_pick_odds",
    "edge_pick_vs_consensus", "edge_pick_vs_sharp",
]


def team_key(name: str) -> str:
    text = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", " ", text.lower().replace("&", " and "))
    text = re.sub(r"\s+", " ", text).strip()
    return ALIASES.get(text, text)


def match_key(home: str, away: str) -> str:
    return f"{team_key(home)}__{team_key(away)}"


def devig_multiplicative(home: float, draw: float, away: float) -> Optional[list[float]]:
    if min(home, draw, away) <= 1.0:
        return None
    implied = [1.0 / home, 1.0 / draw, 1.0 / away]
    total = sum(implied)
    if total <= 0:
        return None
    return [x / total for x in implied]


def book_probs(prices: dict[str, float]) -> Optional[list[float]]:
    if not {"home", "draw", "away"} <= prices.keys():
        return None
    return devig_multiplicative(prices["home"], prices["draw"], prices["away"])


def collect_events(flat_path: Path) -> dict[str, dict]:
    """event_id -> {meta, per-book h2h prices}."""
    events: dict[str, dict] = {}
    for row in csv.DictReader(flat_path.open(encoding="utf-8-sig")):
        if row.get("market") != "h2h":  # exclude totals/spreads/h2h_lay
            continue
        try:
            price = float(row["price"])
        except (ValueError, KeyError):
            continue
        ev = events.setdefault(row["event_id"], {
            "event_id": row["event_id"],
            "commence_time": row.get("commence_time", ""),
            "home_team": row.get("home_team", ""),
            "away_team": row.get("away_team", ""),
            "books": defaultdict(dict),
        })
        name = row.get("outcome_name", "").strip()
        if name.lower() == "draw":
            side = "draw"
        elif team_key(name) == team_key(ev["home_team"]):
            side = "home"
        elif team_key(name) == team_key(ev["away_team"]):
            side = "away"
        else:
            continue
        ev["books"][row.get("bookmaker", "")][side] = price
    return events


def snapshot_row(ev: dict, now: datetime) -> Optional[dict]:
    books = ev["books"]
    consensus_samples = [p for p in (book_probs(pr) for pr in books.values()) if p]
    if not consensus_samples:
        return None
    p = [sum(s[i] for s in consensus_samples) / len(consensus_samples) for i in range(3)]
    p = [x / sum(p) for x in p]

    best_odds, best_book = {}, {}
    for side in OUTCOMES:
        offers = [(pr[side], bk) for bk, pr in books.items() if side in pr]
        if offers:
            odds, bk = max(offers, key=lambda t: t[0])
            best_odds[side], best_book[side] = odds, bk

    def sharp_for(candidates):
        for bk in candidates:
            if bk in books and (probs := book_probs(books[bk])):
                return bk, probs
        return "", None

    sharp_book, sharp = sharp_for(SHARP_BOOKS)
    exch_book, exch = sharp_for(EXCHANGE_BOOKS)

    pick_idx = max(range(3), key=lambda i: p[i])
    pick = OUTCOMES[pick_idx]
    fav_prob = p[pick_idx]
    best_pick = best_odds.get(pick)
    ref = sharp or exch  # prefer Pinnacle, else exchange, for the independent edge
    return {
        "snapshot_utc": now.isoformat(),
        "snapshot_date": now.date().isoformat(),
        "event_id": ev["event_id"],
        "commence_time": ev["commence_time"],
        "home_team": ev["home_team"],
        "away_team": ev["away_team"],
        "match_key": match_key(ev["home_team"], ev["away_team"]),
        "n_books_h2h": len(consensus_samples),
        "p_home": round(p[0], 4), "p_draw": round(p[1], 4), "p_away": round(p[2], 4),
        "best_home_odds": best_odds.get("home", ""), "best_draw_odds": best_odds.get("draw", ""),
        "best_away_odds": best_odds.get("away", ""),
        "best_home_book": best_book.get("home", ""), "best_draw_book": best_book.get("draw", ""),
        "best_away_book": best_book.get("away", ""),
        "sharp_book": sharp_book,
        "sharp_p_home": round(sharp[0], 4) if sharp else "",
        "sharp_p_draw": round(sharp[1], 4) if sharp else "",
        "sharp_p_away": round(sharp[2], 4) if sharp else "",
        "exchange_book": exch_book,
        "exchange_p_home": round(exch[0], 4) if exch else "",
        "exchange_p_draw": round(exch[1], 4) if exch else "",
        "exchange_p_away": round(exch[2], 4) if exch else "",
        "model_pick": pick,
        "fav_prob": round(fav_prob, 4),
        "fair_odds": round(1.0 / fav_prob, 3),
        "best_pick_odds": best_pick if best_pick else "",
        "edge_pick_vs_consensus": round(fav_prob * best_pick - 1.0, 4) if best_pick else "",
        "edge_pick_vs_sharp": round(ref[pick_idx] * best_pick - 1.0, 4) if (ref and best_pick) else "",
    }


def merge_log(existing: list[dict], new_rows: list[dict]) -> list[dict]:
    """Idempotent per (snapshot_date, match_key): today's rows replace prior same-day rows."""
    today_keys = {(r["snapshot_date"], r["match_key"]) for r in new_rows}
    kept = [r for r in existing if (r.get("snapshot_date"), r.get("match_key")) not in today_keys]
    return kept + new_rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Append pre-match prediction snapshots to a rolling log")
    ap.add_argument("--flat-odds-csv", default=FLAT_ODDS_DEFAULT)
    ap.add_argument("--log-csv", default=LOG_DEFAULT)
    args = ap.parse_args()

    flat_path = Path(args.flat_odds_csv)
    if not flat_path.exists():
        print(f"No flat odds file at {flat_path}; nothing to log.")
        return 0

    now = datetime.now(timezone.utc)
    events = collect_events(flat_path)
    new_rows = [r for ev in events.values() if (r := snapshot_row(ev, now))]
    if not new_rows:
        print("No usable h2h markets found; nothing logged.")
        return 0

    log_path = Path(args.log_csv)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    existing = list(csv.DictReader(log_path.open(encoding="utf-8-sig"))) if log_path.exists() else []
    merged = merge_log(existing, new_rows)

    with log_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in merged:
            writer.writerow({k: row.get(k, "") for k in FIELDNAMES})

    with_sharp = sum(1 for r in new_rows if r["sharp_book"])
    print(f"Logged {len(new_rows)} fixtures ({with_sharp} with a Pinnacle sharp anchor) "
          f"for {now.date().isoformat()}; log now holds {len(merged)} rows -> {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
