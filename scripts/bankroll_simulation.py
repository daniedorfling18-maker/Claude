#!/usr/bin/env python3
"""R3,000 bankroll simulation: bet the model's pick at the prices we actually had.

Answers "if we'd put in R3k and bet our picks, where would the ROI be?" using the
model's match-result (1X2) pick and the REAL pre-kickoff prices captured for each
completed WC 2026 fixture. One bet per match, level-staked (total turnover = the
bankroll). A compounding variant reinvests a fixed fraction sequentially.

Prices, best realistic first:
  - prediction_log best obtainable odds across all books (scripts/log_prediction_snapshots.py)
  - Polymarket actual pre-kickoff price (1 / raw favourite price)
  - The Odds API de-vigged consensus with a 5% margin haircut (approx real price)

This is a small, favourably-priced subset, so the ROI is descriptive of what already
happened, not a forecast. A bootstrap gives the plausible bankroll range.
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
REALISED = "outputs/backtesting/superbru_realised_submitted_performance.csv"
OUT = "outputs/backtesting/wc_predictive_power/bankroll_simulation.csv"
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


def main() -> int:
    ap = argparse.ArgumentParser(description="R3k bankroll simulation on the model's betting picks")
    ap.add_argument("--bankroll", type=float, default=3000.0)
    ap.add_argument("--compound-fraction", type=float, default=0.10, help="Stake fraction of running bankroll for the compounding view")
    ap.add_argument("--n-boot", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=20260622)
    ap.add_argument("--out-csv", default=OUT)
    args = ap.parse_args()

    res = load_results(RESULTS)
    plog, poly, mh = load_prices()
    universe = [k for k in res if k in plog or k in poly or k in mh]

    bets = []
    for k in universe:
        pick, odds, src, ct = plog.get(k) or poly.get(k) or mh.get(k)
        won = pick == res[k]["outcome"]
        bets.append({"match_key": k, "label": res[k]["label"], "pick": pick, "odds": round(odds, 3),
                     "price_source": src, "commence_time": ct, "won": won})
    bets.sort(key=lambda b: b["commence_time"])
    n = len(bets)
    if n == 0:
        print("No completed matches with a bettable price yet.")
        return 0

    # Level staking: total turnover = bankroll, one equal stake per bet.
    stake = args.bankroll / n
    net = [(stake * (b["odds"] - 1) if b["won"] else -stake) for b in bets]
    profit = sum(net)
    ending = args.bankroll + profit
    roi = profit / args.bankroll
    lo, hi = boot_ci(net, args.n_boot, args.seed)

    # Compounding: reinvest a fixed fraction of the running bankroll, in kickoff order.
    cbank = args.bankroll
    for b in bets:
        s = cbank * args.compound_fraction
        cbank += s * (b["odds"] - 1) if b["won"] else -s

    Path(args.out_csv).parent.mkdir(parents=True, exist_ok=True)
    running = args.bankroll
    with open(args.out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["#", "match", "pick", "odds", "price_source", "result", "stake", "return", "bankroll_after"])
        for i, b in enumerate(bets, 1):
            ret = stake * b["odds"] if b["won"] else 0.0
            running += ret - stake
            w.writerow([i, b["label"], b["pick"], b["odds"], b["price_source"],
                        "WIN" if b["won"] else "loss", round(stake, 2), round(ret, 2), round(running, 2)])

    wins = sum(b["won"] for b in bets)
    print("=" * 78)
    print(f"R{args.bankroll:,.0f} BANKROLL - betting the model's 1X2 pick at real captured prices")
    print("=" * 78)
    print(f"Bets (completed matches with a price): {n}   |   winners: {wins} ({wins/n:.0%})")
    print(f"Avg odds taken: {mean(b['odds'] for b in bets):.2f}   "
          f"price mix: best_obtainable {sum(b['price_source']=='best_obtainable' for b in bets)}, "
          f"polymarket {sum(b['price_source']=='polymarket' for b in bets)}, "
          f"consensus_haircut {sum(b['price_source']=='consensus_haircut' for b in bets)}")
    print("-" * 78)
    print("LEVEL STAKING (put the whole bankroll in, one equal stake per pick):")
    print(f"  stake/bet R{stake:,.2f}  ->  ending bankroll  R{ending:,.0f}   "
          f"profit R{profit:+,.0f}   ROI {roi:+.1%}")
    print(f"  bootstrap 95% bankroll range: R{args.bankroll+lo:,.0f} .. R{args.bankroll+hi:,.0f}  "
          f"(ROI {lo/args.bankroll:+.0%} .. {hi/args.bankroll:+.0%})")
    print(f"COMPOUNDING ({args.compound_fraction:.0%} of running bankroll/bet, kickoff order):")
    print(f"  ending bankroll  R{cbank:,.0f}   ROI {(cbank/args.bankroll-1):+.1%}")
    print("-" * 78)
    print("Ledger ->", args.out_csv)
    print("\nCONTEXT / caveats:")
    print(f"  - This is {n} matches with a captured price (favourable subset); not a forecast.")
    print("  - Picks are match-result (1X2). Betting the exact correct-score picks is far harsher:")
    print("    only 5/40 exact hits, so 40 level-staked correct-score bets need avg winning odds")
    print("    > 8.0 just to break even - typical low-score favourite CS odds (~6-9) make it")
    print("    roughly breakeven-to-negative. Real CS prices are not captured to simulate exactly.")
    print("  - SuperBru pool is the separate, primary venue (see wc_predictive_power_validation.py).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
