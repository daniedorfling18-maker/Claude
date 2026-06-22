#!/usr/bin/env python3
"""R3,000 bankroll: flat-stake backtest + honest sharp-anchor fractional-Kelly staking.

Part A (descriptive) - what flat staking the model's 1X2 pick already returned on the
completed matches we had a price for.

Part B (the method to bet with) - sharp-anchor fractional Kelly. For each fixture the
EDGE is the best obtainable price measured against an independent sharp line (Pinnacle,
else Betfair exchange), NOT against the model's own market-derived probability. Kelly
then sizes the stake by that real edge:

    edge   = p_sharp * best_odds - 1            (expected ROI on the bet)
    f_full = edge / (best_odds - 1)             (full Kelly fraction of bankroll)
    stake  = min(kelly_fraction * f_full, cap) * bankroll
    edge <= min_edge  ->  NO BET (stake 0)

This refuses -EV bets by construction: when the best price does not beat the sharp line
the stake is zero. It produces (1) a Kelly backtest on completed fixtures and (2) a
forward staking card for upcoming fixtures - the bets to actually place, each with its
expected ROI and the book offering the price.
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
OUT_DIR = "outputs/backtesting/wc_predictive_power"
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


# --------------------------------------------------------------------------- Part A
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


# --------------------------------------------------------------------------- Part B
def kelly_fraction(edge, odds, frac, cap):
    """Fractional Kelly fraction of bankroll; 0 when there is no positive edge."""
    if edge <= 0 or odds <= 1:
        return 0.0
    return min(frac * (edge / (odds - 1)), cap)


def load_kelly_candidates(results):
    """Latest pre-kickoff prediction-log row per match with a sharp anchor and best price."""
    if not Path(PLOG).exists():
        return []
    latest = {}
    for r in csv.DictReader(open(PLOG, encoding="utf-8-sig")):
        k = r["match_key"]
        if not r.get("best_pick_odds") or r.get("edge_pick_vs_sharp") in ("", None):
            continue
        if k not in latest or r.get("snapshot_utc", "") > latest[k].get("snapshot_utc", ""):
            latest[k] = r
    out = []
    for k, r in latest.items():
        pick = r["model_pick"]
        outcome_name = {"home": r["home_team"], "away": r["away_team"], "draw": "Draw"}[pick]
        book = {"home": r.get("best_home_book"), "draw": r.get("best_draw_book"), "away": r.get("best_away_book")}[pick]
        completed = k in results
        out.append({
            "match_key": k, "match": f"{r['home_team']} v {r['away_team']}", "commence_time": r.get("commence_time", ""),
            "selection": outcome_name, "pick": pick, "best_odds": float(r["best_pick_odds"]),
            "book": book or "", "sharp_book": r.get("sharp_book", ""),
            "edge": float(r["edge_pick_vs_sharp"]), "completed": completed,
            "won": (pick == results[k]["outcome"]) if completed else None,
            "result": results[k]["label"] if completed else "",
        })
    out.sort(key=lambda c: c["commence_time"])
    return out


def kelly_backtest(candidates, bankroll, frac, cap, min_edge):
    bank = bankroll
    placed = []
    for c in (c for c in candidates if c["completed"]):
        f = kelly_fraction(c["edge"], c["best_odds"], frac, cap)
        if c["edge"] <= min_edge or f <= 0:
            continue
        stake = f * bank
        bank += stake * (c["best_odds"] - 1) if c["won"] else -stake
        placed.append({**c, "stake": stake, "bank_after": bank})
    return placed, bank


def kelly_forward_card(candidates, bankroll, frac, cap, min_edge):
    card = []
    for c in (c for c in candidates if not c["completed"]):
        f = kelly_fraction(c["edge"], c["best_odds"], frac, cap)
        if c["edge"] <= min_edge or f <= 0:
            continue
        stake = f * bankroll
        card.append({**c, "kelly_fraction": f, "stake": stake, "expected_roi": c["edge"],
                     "expected_profit": stake * c["edge"]})
    card.sort(key=lambda x: x["edge"], reverse=True)
    return card


def main() -> int:
    ap = argparse.ArgumentParser(description="R3k bankroll: flat backtest + sharp-anchor Kelly staking")
    ap.add_argument("--bankroll", type=float, default=3000.0)
    ap.add_argument("--kelly-fraction", type=float, default=0.5, help="Fraction of full Kelly (0.5 = half Kelly)")
    ap.add_argument("--max-bet-fraction", type=float, default=0.05, help="Per-bet cap as a fraction of bankroll")
    ap.add_argument("--min-edge", type=float, default=0.0, help="Minimum edge vs sharp to place a bet (e.g. 0.02 = 2%)")
    ap.add_argument("--n-boot", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=20260622)
    ap.add_argument("--out-dir", default=OUT_DIR)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = load_results(RESULTS)

    print("=" * 80)
    print(f"R{args.bankroll:,.0f} BANKROLL  -  WC 2026 model picks")
    print("=" * 80)

    # PART A
    flat = flat_backtest(args.bankroll, args.n_boot, args.seed)
    print("\n[A] FLAT-STAKE BACKTEST (descriptive: equal stake on every completed priced pick)")
    if flat:
        print(f"    {flat['n']} bets, {flat['wins']} won ({flat['wins']/flat['n']:.0%}), avg odds {flat['avg_odds']:.2f}, "
              f"R{flat['stake']:,.0f}/bet")
        print(f"    ending R{flat['ending']:,.0f}  (profit R{flat['profit']:+,.0f}, ROI {flat['roi']:+.1%})  "
              f"95% range R{flat['ci_lo']:,.0f}..R{flat['ci_hi']:,.0f}")
        print("    NOTE: flat staking ignores edge; these picks have ~no edge vs the price, so this")
        print("    return is variance on winning favourites, not a repeatable expectation.")

    # PART B
    candidates = load_kelly_candidates(results)
    cand_done = [c for c in candidates if c["completed"]]
    cand_up = [c for c in candidates if not c["completed"]]
    placed, bank = kelly_backtest(candidates, args.bankroll, args.kelly_fraction, args.max_bet_fraction, args.min_edge)
    card = kelly_forward_card(candidates, args.bankroll, args.kelly_fraction, args.max_bet_fraction, args.min_edge)

    print(f"\n[B] SHARP-ANCHOR {args.kelly_fraction:g}-KELLY  (edge vs Pinnacle/Betfair; cap "
          f"{args.max_bet_fraction:.0%}/bet; min edge {args.min_edge:.1%})")
    pos_done = sum(c["edge"] > args.min_edge for c in cand_done)
    pos_up = sum(c["edge"] > args.min_edge for c in cand_up)
    print(f"    fixtures with a sharp anchor + best price: {len(candidates)} "
          f"({len(cand_done)} completed, {len(cand_up)} upcoming)")
    print(f"    of those, edge > {args.min_edge:.1%}: {pos_done} completed, {pos_up} upcoming")
    print(f"    Kelly backtest on completed: {len(placed)} bet(s) cleared the bar -> ending R{bank:,.0f} "
          f"(ROI {(bank/args.bankroll-1):+.1%})")
    if not placed:
        print("    => no completed fixture's best price beat the sharp line, so Kelly staked R0.")

    # Forward staking card
    card_path = out_dir / "kelly_staking_card.csv"
    with card_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["commence_time", "match", "selection", "best_odds", "book",
                                           "sharp_book", "edge", "kelly_fraction", "stake", "expected_roi",
                                           "expected_profit"])
        w.writeheader()
        for c in card:
            w.writerow({k: (round(c[k], 4) if isinstance(c[k], float) else c[k]) for k in w.fieldnames})

    print(f"\n    FORWARD STAKING CARD (upcoming, +EV at sharp anchor)  ->  {card_path}")
    if card:
        print(f"    {'kickoff':17}{'selection':22}{'odds':6}{'edge':7}{'stake':9}{'exp.ROI'}")
        tot_stake = 0.0
        for c in card:
            tot_stake += c["stake"]
            print(f"    {c['commence_time'][:16]:17}{c['selection'][:21]:22}{c['best_odds']:<6.2f}"
                  f"{c['edge']:+6.1%} R{c['stake']:>6.0f}  {c['expected_roi']:+.1%} @ {c['book']}")
        print(f"    total recommended exposure: R{tot_stake:,.0f} of R{args.bankroll:,.0f} "
              f"({tot_stake/args.bankroll:.0%} of bankroll)")
    else:
        print("    No upcoming fixture's best price beats the sharp line by > "
              f"{args.min_edge:.1%}. Recommended action: NO BET (hold bankroll).")

    print("\n" + "=" * 80)
    print("HOW TO USE: re-run after each odds refresh; place only the carded bets at the named")
    print("book and odds (or better). Expected ROI per bet = edge vs the sharp line; bets are")
    print(f"{args.kelly_fraction:g}-Kelly sized and capped at {args.max_bet_fraction:.0%}. A positive long-run ROI")
    print("requires real soft prices - if the card is empty, the honest expectation is ~0, so")
    print("hold. Raise --min-edge (e.g. 0.02) to demand a margin for model/execution error.")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
