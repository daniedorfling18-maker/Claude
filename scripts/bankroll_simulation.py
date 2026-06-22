#!/usr/bin/env python3
"""R3,000 flat-stake outcome-betting backtest (Kelly removed).

Descriptive only: equal stake on the model's 1X2 pick at the real prices captured per
completed match. This is the betting baseline; capital generation has moved to the
long/short trading engine (scripts/polymarket_long_short_engine.py).
"""
from __future__ import annotations

import argparse
import csv
import random
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from statistics import mean

RESULTS = "outputs/superbru_pool/superbru_match_results_auto.csv"
POLY = "outputs/polymarket-wc-retrospective/polymarket_wc_1x2_summary.csv"
PLOG = "outputs/backtesting/prediction_log.csv"
MH = "outputs/market_odds_history/market_odds_history.csv"
ALIASES = {"czechia": "czech republic", "ir iran": "iran", "bosnia and herzegovina": "bosnia herzegovina",
           "congo dr": "dr congo", "cote d ivoire": "ivory coast", "cabo verde": "cape verde",
           "turkiye": "turkey", "korea republic": "south korea", "usa": "united states"}


def tk(n: str) -> str:
    t = unicodedata.normalize("NFKD", str(n or "")).encode("ascii", "ignore").decode().lower().replace("&", " and ")
    t = re.sub(r"[^a-z0-9]+", " ", t).strip()
    return ALIASES.get(t, t)


def mk(h, a): return f"{tk(h)}__{tk(a)}"
def oc(h, a): return "home" if h > a else ("away" if h < a else "draw")


def load_results(path):
    res = {}
    for r in csv.DictReader(open(path, encoding="utf-8-sig")):
        if str(r.get("is_completed")).lower() == "true" and r["home_goals"] and r["away_goals"]:
            res[mk(r["home_team"], r["away_team"])] = {
                "outcome": oc(int(r["home_goals"]), int(r["away_goals"])),
                "label": f"{r['home_team']} {r['home_goals']}-{r['away_goals']} {r['away_team']}",
            }
    return res


def load_prices():
    poly, plog, mh = {}, {}, {}
    if Path(POLY).exists():
        for r in csv.DictReader(open(POLY, encoding="utf-8-sig")):
            raw = {"home": float(r["raw_home"]), "draw": float(r["raw_draw"]), "away": float(r["raw_away"])}
            fav = max(raw, key=raw.get)
            poly[mk(r["home"], r["away"])] = (fav, 1 / raw[fav], "polymarket", r.get("commence_time", ""))
    if Path(PLOG).exists():
        for r in csv.DictReader(open(PLOG, encoding="utf-8-sig")):
            if r.get("best_pick_odds"):
                plog[r["match_key"]] = (r["model_pick"], float(r["best_pick_odds"]), "best_obtainable", r.get("commence_time", ""))
    if Path(MH).exists():
        rows = defaultdict(list)
        for r in csv.DictReader(open(MH, encoding="utf-8-sig")):
            if r["market_p_home"]:
                rows[mk(r["home_team"], r["away_team"])].append(r)
        for k, rs in rows.items():
            r = rs[-1]
            p = {"home": float(r["market_p_home"]), "draw": float(r["market_p_draw"]), "away": float(r["market_p_away"])}
            fav = max(p, key=p.get)
            mh[k] = (fav, (1 / p[fav]) * 0.95, "consensus_haircut", r.get("commence_time", ""))
    return plog, poly, mh


def boot_ci(net, n_boot, seed):
    rng = random.Random(seed)
    n = len(net)
    tot = sorted(sum(net[rng.randrange(n)] for _ in range(n)) for _ in range(n_boot))
    return tot[int(0.025 * n_boot)], tot[int(0.975 * n_boot)]


def flat_backtest(bankroll, n_boot, seed):
    res = load_results(RESULTS)
    plog, poly, mh = load_prices()
    bets = []
    for k in (k for k in res if k in plog or k in poly or k in mh):
        pick, odds, src, ct = plog.get(k) or poly.get(k) or mh.get(k)
        bets.append({"label": res[k]["label"], "pick": pick, "odds": odds, "src": src,
                     "ct": ct, "won": pick == res[k]["outcome"]})
    bets.sort(key=lambda b: b["ct"])
    n = len(bets)
    if n == 0:
        return None
    stake = bankroll / n
    net = [(stake * (b["odds"] - 1) if b["won"] else -stake) for b in bets]
    lo, hi = boot_ci(net, n_boot, seed)
    return {"n": n, "wins": sum(b["won"] for b in bets), "stake": stake, "avg_odds": mean(b["odds"] for b in bets),
            "ending": bankroll + sum(net), "profit": sum(net), "roi": sum(net) / bankroll,
            "ci_lo": bankroll + lo, "ci_hi": bankroll + hi}


def main() -> int:
    ap = argparse.ArgumentParser(description="R3k flat-stake outcome-betting backtest")
    ap.add_argument("--bankroll", type=float, default=3000.0)
    ap.add_argument("--n-boot", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=20260622)
    args = ap.parse_args()

    flat = flat_backtest(args.bankroll, args.n_boot, args.seed)
    print("=" * 72)
    print(f"R{args.bankroll:,.0f} FLAT-STAKE BACKTEST (model 1X2 pick at captured prices)")
    print("=" * 72)
    if not flat:
        print("No completed matches with a captured price yet.")
        return 0
    print(f"{flat['n']} bets, {flat['wins']} won ({flat['wins']/flat['n']:.0%}), avg odds {flat['avg_odds']:.2f}, "
          f"R{flat['stake']:,.0f}/bet")
    print(f"ending R{flat['ending']:,.0f}  (profit R{flat['profit']:+,.0f}, ROI {flat['roi']:+.1%})  "
          f"95% range R{flat['ci_lo']:,.0f}..R{flat['ci_hi']:,.0f}")
    print("NOTE: flat staking ignores edge; these picks have ~no edge vs the price, so this is")
    print("variance on winning favourites, not a repeatable expectation. Capital generation now")
    print("lives in the long/short trading engine (scripts/polymarket_long_short_engine.py).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
