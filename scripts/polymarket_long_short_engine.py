#!/usr/bin/env python3
"""Long/short trading engine (dry-run intent generator).

Turns the model into a long/short trading engine on Polymarket using the primitives in
``superbru_score_engine.betting.long_short``. For each fixture/outcome it evaluates:

- DIRECTIONAL: long when the independent model (Pinnacle sharp, else consensus) beats the
  ask, short (buy NO) when it is below the bid.
- MARKET_MAKE: a bid/ask quote around fair to earn the spread (maker fee = 0 on Polymarket).
- ARB: locked profit when the best obtainable prices across outcomes (best bookmaker line
  per outcome, and Polymarket where a snapshot is supplied) sum below 1.

Inputs come from the rolling prediction log (model + sharp + best bookmaker odds). A live
Polymarket snapshot (``--polymarket-snapshot`` token,outcome,bid,ask) enables true
cross-venue arbitrage and real long/short prices; without it the engine synthesises a
Polymarket bid/ask from the captured 1X2 retrospective and an assumed half-spread.

This NEVER submits orders. It writes dry-run intents only.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from superbru_score_engine.betting.long_short import (  # noqa: E402
    directional_signal,
    dutch_arb,
    market_make_quote,
)

PLOG = "outputs/backtesting/prediction_log.csv"
POLY = "outputs/polymarket-wc-retrospective/polymarket_wc_1x2_summary.csv"
OUT = "outputs/polymarket/long_short_intents.csv"
OUTCOMES = ("home", "draw", "away")
FIELDS = ["intent_id", "mode", "action", "venue", "commence_time", "match", "outcome",
          "model_prob", "market_price", "bid", "ask", "edge", "stake_usdc", "expected_pnl_usdc", "status"]


def _f(v, d=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def load_prediction_log(path):
    if not Path(path).exists():
        return {}
    latest = {}
    for r in csv.DictReader(open(path, encoding="utf-8-sig")):
        k = r["match_key"]
        if k not in latest or r.get("snapshot_utc", "") > latest[k].get("snapshot_utc", ""):
            latest[k] = r
    out = {}
    for k, r in latest.items():
        cons = [_f(r.get(f"p_{o}")) for o in OUTCOMES]
        sharp = [_f(r.get(f"sharp_p_{o}")) for o in OUTCOMES]
        odds = [_f(r.get(f"best_{o}_odds")) for o in OUTCOMES]
        if any(c is None for c in cons):
            continue
        out[k] = {"match": f"{r['home_team']} v {r['away_team']}", "commence_time": r.get("commence_time", ""),
                  "consensus": cons, "sharp": sharp, "book_odds": odds}
    return out


def load_polymarket_prices(path):
    """match_key -> [yes_price_home, draw, away] from the retrospective summary (no live book)."""
    import unicodedata
    import re

    def tk(n):
        t = unicodedata.normalize("NFKD", str(n or "")).encode("ascii", "ignore").decode().lower().replace("&", " and ")
        return re.sub(r"[^a-z0-9]+", " ", t).strip()

    if not Path(path).exists():
        return {}
    out = {}
    for r in csv.DictReader(open(path, encoding="utf-8-sig")):
        out[f"{tk(r['home'])}__{tk(r['away'])}"] = [_f(r["raw_home"]), _f(r["raw_draw"]), _f(r["raw_away"])]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Polymarket long/short trading engine (dry-run)")
    ap.add_argument("--prediction-log", default=PLOG)
    ap.add_argument("--polymarket-summary", default=POLY)
    ap.add_argument("--out-csv", default=OUT)
    ap.add_argument("--stake-usdc", type=float, default=50.0, help="Per-leg notional for directional/MM sizing")
    ap.add_argument("--half-spread", type=float, default=0.01, help="Synthesised Polymarket half-spread when no live book")
    ap.add_argument("--min-edge", type=float, default=0.02, help="Price-space edge required for a directional trade")
    ap.add_argument("--arb-cost", type=float, default=0.02, help="Per-set cost (exchange commission + slippage) an arb must clear")
    ap.add_argument("--mm-target-shares", type=float, default=100.0)
    args = ap.parse_args()

    plog = load_prediction_log(args.prediction_log)
    pm = load_polymarket_prices(args.polymarket_summary)

    intents = []
    counts = {"DIRECTIONAL": 0, "MARKET_MAKE": 0, "ARB": 0}
    paper_arbs = []

    def add(mode, action, venue, m, outcome, model, price, bid, ask, edge, stake, epnl):
        intents.append({
            "intent_id": f"{mode[:3]}-{len(intents)+1:04d}", "mode": mode, "action": action, "venue": venue,
            "commence_time": m["commence_time"], "match": m["match"], "outcome": outcome,
            "model_prob": _r(model), "market_price": _r(price), "bid": _r(bid), "ask": _r(ask),
            "edge": _r(edge), "stake_usdc": _r(stake), "expected_pnl_usdc": _r(epnl), "status": "DRY_RUN",
        })
        counts[mode] += 1

    for k, m in plog.items():
        cons, sharp, odds = m["consensus"], m["sharp"], m["book_odds"]
        pm_prices = pm.get(k)

        # ARB: cheapest obtainable price per outcome across venues (bookmaker best + Polymarket).
        per_outcome_best = []
        for i in range(3):
            candidates = []
            if odds[i]:
                candidates.append(1.0 / odds[i])
            if pm_prices and pm_prices[i]:
                candidates.append(pm_prices[i])
            per_outcome_best.append(min(candidates) if candidates else None)
        if all(p is not None and 0 < p < 1 for p in per_outcome_best):
            arb = dutch_arb(per_outcome_best)
            if arb.is_arb:
                net = arb.locked_profit_per_set - args.arb_cost
                paper_arbs.append(arb.locked_profit_per_set)
                if net > 0:  # only emit arbs that clear realistic execution cost
                    add("ARB", "ARB_LOCK", "cross_venue", m, "home/draw/away", None,
                        arb.total_cost, None, None, net, 1.0, net)

        for i, outcome in enumerate(OUTCOMES):
            fair = cons[i]
            model = sharp[i] if sharp[i] else cons[i]
            if not (model and 0 < model < 1 and 0 < fair < 1):
                continue
            # Market we trade on: live Polymarket price if supplied, else synth from consensus.
            mid = pm_prices[i] if (pm_prices and pm_prices[i]) else fair
            venue = "polymarket" if (pm_prices and pm_prices[i]) else "synthetic"
            bid = max(0.01, round(mid - args.half_spread, 4))
            ask = min(0.99, round(mid + args.half_spread, 4))

            # DIRECTIONAL long/short vs the independent model.
            sig = directional_signal(model, bid, ask, args.stake_usdc, min_edge=args.min_edge, fees_enabled=True)
            if sig.action != "NONE" and sig.position is not None:
                add("DIRECTIONAL", "BUY_YES_LIMIT" if sig.action == "LONG" else "BUY_NO_LIMIT", venue, m, outcome,
                    model, mid, bid, ask, sig.raw_edge, sig.position.stake_usdc, sig.position.expected_pnl(model))

            # MARKET_MAKE quote around fair (informational; maker fee = 0).
            q = market_make_quote(fair, args.half_spread, args.mm_target_shares)
            add("MARKET_MAKE", "QUOTE", "polymarket", m, outcome, model, fair, q.bid, q.ask,
                q.spread, q.bid * args.mm_target_shares, q.expected_capture_usdc)

    Path(args.out_csv).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(intents)

    print("=" * 74)
    print("POLYMARKET LONG/SHORT ENGINE (dry-run)")
    print("=" * 74)
    print(f"fixtures evaluated: {len(plog)}   polymarket prices joined: "
          f"{sum(1 for k in plog if k in pm)}")
    print(f"intents: {len(intents)}  ->  directional {counts['DIRECTIONAL']}, "
          f"market-make {counts['MARKET_MAKE']}, arb {counts['ARB']}")
    directional = [i for i in intents if i["mode"] == "DIRECTIONAL"]
    if directional:
        print("\nDIRECTIONAL signals (long/short vs model):")
        for i in directional[:12]:
            print(f"  {i['action']:14} {i['match'][:30]:30} {i['outcome']:5} "
                  f"model {i['model_prob']} vs px {i['market_price']}  edge {i['edge']:+}")
    arbs = [i for i in intents if i["mode"] == "ARB"]
    if paper_arbs:
        pa = sorted(paper_arbs, reverse=True)
        print(f"\narbitrage: {len(pa)} paper locks (median {pa[len(pa)//2]:.2%}, max {pa[0]:.2%}); "
              f"{len(arbs)} clear the {args.arb_cost:.0%} cost bar")
        if not arbs:
            print("  => paper arbs are sub-commission; not harvestable after exchange fees/slippage.")
    else:
        print("\narbitrage: best cross-venue prices sum >= 1 -> no lock available.")
    print(f"\nintents -> {args.out_csv}")
    print("NOTE: dry-run only; no orders placed. Directional pre-match drift is ~nil, so the")
    print("market-neutral engines (market-making spread + rebates, cross-venue arb) are where")
    print("capital is generated. Supply --polymarket-snapshot live bid/ask to trade real books.")
    return 0


def _r(v):
    return "" if v is None else round(float(v), 4)


if __name__ == "__main__":
    raise SystemExit(main())
