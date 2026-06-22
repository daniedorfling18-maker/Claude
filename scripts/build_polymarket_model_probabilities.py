#!/usr/bin/env python3
"""Convert the model prediction log into the bot's model_probabilities.csv.

Joins our per-match model probabilities (outputs/backtesting/prediction_log.csv) to the
live Polymarket tokens in the bot's market_snapshot.csv by parsing each market question,
applies the learned calibration (edge_calibration.json) if present, and writes a
model_probabilities.csv keyed by token_id (the bot's most reliable match key) plus a
question|outcome fallback. This gives the directional long/short engine live model fairs.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from superbru_score_engine.betting.edge_learning import Calibration  # noqa: E402

PLOG = "outputs/backtesting/prediction_log.csv"
SNAP = "outputs/polymarket/market_snapshot.csv"
OUT = "inputs/polymarket/model_probabilities.csv"
CAL = "outputs/backtesting/edge_calibration.json"
ALIASES = {"czechia": "czech republic", "ir iran": "iran", "bosnia and herzegovina": "bosnia herzegovina",
           "congo dr": "dr congo", "cote d ivoire": "ivory coast", "cabo verde": "cape verde",
           "turkiye": "turkey", "korea republic": "south korea", "usa": "united states", "usmnt": "united states"}


def tk(n):
    t = unicodedata.normalize("NFKD", str(n or "")).encode("ascii", "ignore").decode().lower().replace("&", " and ")
    t = re.sub(r"[^a-z0-9]+", " ", t).strip()
    return ALIASES.get(t, t)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_plog(path):
    """match_key -> {p:{home,draw,away}, home, away, date}; plus team_key -> [(match_key, side, date)]."""
    latest = {}
    for r in csv.DictReader(open(path, encoding="utf-8-sig")):
        k = r["match_key"]
        if k not in latest or r.get("snapshot_utc", "") > latest[k].get("snapshot_utc", ""):
            latest[k] = r
    by_match, team_index = {}, defaultdict(list)
    for k, r in latest.items():
        p = {"home": _f(r.get("p_home")), "draw": _f(r.get("p_draw")), "away": _f(r.get("p_away"))}
        if None in p.values():
            continue
        date = (r.get("commence_time", "") or "")[:10]
        by_match[k] = {"p": p, "home": r["home_team"], "away": r["away_team"], "date": date}
        team_index[tk(r["home_team"])].append((k, "home", date))
        team_index[tk(r["away_team"])].append((k, "away", date))
    return by_match, team_index


def parse_question(q):
    """Return (kind, team_a, team_b, date). kind in {'win','draw',None}."""
    ql = str(q or "").strip().lower()
    if "draw" in ql and (" vs" in ql or " v " in ql):
        m = re.search(r"will (.+?) (?:vs\.?|v) (.+?) (?:end|finish) in a draw", ql)
        if m:
            return ("draw", tk(m.group(1)), tk(m.group(2)), None)
    m = re.search(r"will (.+?) win", ql)
    if m:
        dm = re.search(r"on (\d{4}-\d{2}-\d{2})", ql)
        return ("win", tk(m.group(1)), None, dm.group(1) if dm else None)
    return (None, None, None, None)


def resolve_yes_prob(kind, t1, t2, date, by_match, team_index):
    """Model probability that the YES side of the market occurs."""
    if kind == "draw":
        for m in by_match.values():
            if {tk(m["home"]), tk(m["away"])} == {t1, t2}:
                return m["p"]["draw"]
        return None
    if kind == "win":
        cands = team_index.get(t1, [])
        if len(cands) == 1:
            k, side, _ = cands[0]
        elif len(cands) > 1 and date:
            dated = [c for c in cands if c[2] == date]
            if len(dated) != 1:
                return None
            k, side, _ = dated[0]
        else:
            return None
        return by_match[k]["p"][side]
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Build Polymarket model_probabilities.csv from the prediction log")
    ap.add_argument("--prediction-log", default=PLOG)
    ap.add_argument("--market-snapshot", default=SNAP)
    ap.add_argument("--calibration", default=CAL, help="edge_calibration.json (identity if missing)")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    cal_path = Path(args.calibration)
    cal = Calibration.from_json(cal_path.read_text(encoding="utf-8")) if cal_path.exists() else Calibration()

    if not Path(args.prediction_log).exists():
        print(f"No prediction log at {args.prediction_log}; nothing to convert.")
        return 0
    if not Path(args.market_snapshot).exists():
        print(f"No market snapshot at {args.market_snapshot}; run a bot scan first.")
        return 0

    by_match, team_index = load_plog(args.prediction_log)
    rows, matched, unmatched = [], 0, 0
    for r in csv.DictReader(open(args.market_snapshot, encoding="utf-8-sig")):
        kind, t1, t2, date = parse_question(r.get("question"))
        yes = resolve_yes_prob(kind, t1, t2, date, by_match, team_index)
        if yes is None:
            unmatched += 1
            continue
        cal_yes = cal.apply(yes)
        outcome = str(r.get("outcome", "")).strip().lower()
        if outcome in {"yes", "true", "1"}:
            prob = cal_yes
        elif outcome in {"no", "false", "0"}:
            prob = 1.0 - cal_yes
        else:
            unmatched += 1
            continue
        rows.append({
            "token_id": r.get("token_id", ""), "market_slug": r.get("market_slug", ""),
            "question": r.get("question", ""), "outcome": r.get("outcome", ""),
            "probability": round(prob, 4),
        })
        matched += 1

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["token_id", "market_slug", "question", "outcome", "probability"])
        w.writeheader()
        w.writerows(rows)

    cal_note = "identity" if (cal.a == 1.0 and cal.b == 0.0) else f"a={cal.a:.3f} b={cal.b:.3f} (n={cal.n})"
    print(f"model_probabilities: matched {matched} tokens, {unmatched} unmatched -> {out_path}")
    print(f"calibration applied: {cal_note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
