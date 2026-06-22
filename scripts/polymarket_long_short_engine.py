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


SNAPSHOT_FIELDS = FIELDS + ["token_id", "tick_size", "order_price", "order_size", "exec_status", "exec_detail"]


def load_market_snapshot(path):
    """Read the bot's market_snapshot.csv: one row per outcome token with live bid/ask + fair."""
    rows = []
    if not Path(path).exists():
        return rows
    for r in csv.DictReader(open(path, encoding="utf-8-sig")):
        bid, ask, fair = _f(r.get("best_bid")), _f(r.get("best_ask")), _f(r.get("fair_probability"))
        if bid is None or ask is None or not (0 < bid < ask < 1):
            continue
        rows.append({
            "token_id": r.get("token_id", ""), "question": r.get("question", ""),
            "market_slug": r.get("market_slug", ""), "outcome": r.get("outcome", ""),
            "fair": fair, "bid": bid, "ask": ask,
            "tick_size": _f(r.get("tick_size"), 0.01) or 0.01,
            "neg_risk": str(r.get("neg_risk", "")).strip().lower() in {"true", "1", "yes"},
        })
    return rows


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def build_live_book_intents(tokens, stake, half_spread, min_edge):
    """Directional long/short (vs fair) plus a passive maker bid per token against the live book."""
    intents, mm_quotes = [], []
    for t in tokens:
        bid, ask, tick = t["bid"], t["ask"], t["tick_size"]
        mid = (bid + ask) / 2.0
        fair = t["fair"] if (t["fair"] and 0 < t["fair"] < 1) else mid
        match = t["question"] or t["market_slug"]

        sig = directional_signal(fair, bid, ask, stake, min_edge=min_edge, fees_enabled=True)
        if sig.action != "NONE" and sig.position is not None:
            intents.append({
                "intent_id": f"DIR-{len(intents)+1:04d}", "mode": "DIRECTIONAL",
                "action": "BUY_YES_LIMIT" if sig.action == "LONG" else "BUY_NO_LIMIT", "venue": "polymarket",
                "commence_time": "", "match": match, "outcome": t["outcome"], "model_prob": _r(fair),
                "market_price": _r(mid), "bid": _r(bid), "ask": _r(ask), "edge": _r(sig.raw_edge),
                "stake_usdc": _r(sig.position.stake_usdc), "expected_pnl_usdc": _r(sig.position.expected_pnl(fair)),
                "status": "DRY_RUN", "token_id": t["token_id"], "tick_size": _r(tick),
                "order_price": "", "order_size": "", "exec_status": "", "exec_detail": "",
            })

        # Passive maker bid strictly inside the spread so it never crosses (always maker).
        our_bid = round(_clamp(fair - half_spread, tick, ask - tick), 4)
        if not (0 < our_bid < ask):
            continue
        q = market_make_quote(fair, half_spread, max(1.0, stake / our_bid))
        mm_quotes.append({**t, "our_bid": our_bid, "spread": q.spread, "stake": stake, "fair": fair})
        intents.append({
            "intent_id": f"MM-{len(intents)+1:04d}", "mode": "MARKET_MAKE", "action": "MAKER_BUY_LIMIT",
            "venue": "polymarket", "commence_time": "", "match": match, "outcome": t["outcome"],
            "model_prob": _r(fair), "market_price": _r(mid), "bid": _r(bid), "ask": _r(ask),
            "edge": _r(q.spread), "stake_usdc": _r(stake), "expected_pnl_usdc": _r(q.expected_capture_usdc),
            "status": "DRY_RUN", "token_id": t["token_id"], "tick_size": _r(tick),
            "order_price": _r(our_bid), "order_size": _r(stake / our_bid), "exec_status": "", "exec_detail": "",
        })
    return intents, mm_quotes


def execute_live_market_making(mm_quotes, max_orders):
    """Graduate market-making to live behind PM_MODE=live + POLYMARKET_EXECUTE_LIVE.

    Reuses the bot's BotConfig / check_geoblock / LiveExecutor so every existing guard applies,
    and forces order_type=GTC so only passive maker BUY limits (never takers) are placed.
    Returns (status, detail, {token_id: (exec_status, detail)}). Default path is dry-run.
    """
    import dataclasses

    scripts_dir = str(ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    try:
        import polymarket_mispricing_bot as bot
    except Exception as exc:  # noqa: BLE001
        return "dry_run", f"could not load bot for live execution: {exc}", {}

    config = bot.BotConfig.from_env()
    if config.mode != "live" or not config.execute_live:
        return "dry_run", "PM_MODE=live and POLYMARKET_EXECUTE_LIVE=true required for live market-making", {}
    try:
        bot.check_geoblock(config)  # raises if this IP is blocked
        config = dataclasses.replace(config, order_type="GTC")  # resting maker limit, never taker
        executor = bot.LiveExecutor(config)
    except Exception as exc:  # noqa: BLE001 - geoblock / credentials / client missing
        return "live_error", f"live setup failed (no orders placed): {exc}", {}

    results = {}
    for q in mm_quotes[:max_orders]:
        size_usd = min(config.max_order_usd, q["stake"])
        opp = bot.Opportunity(
            timestamp=bot.utc_now(), action="BUY", event_slug="", event_title="", market_slug=q["market_slug"],
            question=q["question"], outcome=q["outcome"], token_id=q["token_id"], fair_probability=q["fair"],
            executable_price=q["our_bid"], edge=q["spread"], best_bid=q["bid"], best_ask=q["ask"],
            spread=q["ask"] - q["bid"], size_usd=size_usd, shares=size_usd / q["our_bid"], reason="market_make_bid",
        )
        book = bot.Book(token_id=q["token_id"], best_bid=q["bid"], best_ask=q["ask"], bid_size=0.0, ask_size=0.0,
                        spread=q["ask"] - q["bid"], tick_size=str(q["tick_size"]), min_order_size=0.0,
                        neg_risk=q["neg_risk"])
        try:
            resp = executor.place(opp, book)
            results[q["token_id"]] = ("LIVE_PLACED", str(resp)[:160])
        except Exception as exc:  # noqa: BLE001 - record and continue
            results[q["token_id"]] = ("LIVE_ERROR", str(exc)[:160])
    return "live", f"placed {len(results)} maker bid(s) (cap ${config.max_order_usd:g}/order)", results


def run_live_book(args) -> int:
    tokens = load_market_snapshot(args.market_snapshot)
    intents, mm_quotes = build_live_book_intents(tokens, args.stake_usdc, args.half_spread, args.min_edge)
    exec_status, exec_detail, results = execute_live_market_making(mm_quotes, args.max_live_orders)
    for it in intents:
        if it["mode"] == "MARKET_MAKE" and it["token_id"] in results:
            it["exec_status"], it["exec_detail"] = results[it["token_id"]]
            it["status"] = results[it["token_id"]][0]

    Path(args.out_csv).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=SNAPSHOT_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(intents)

    d = sum(1 for i in intents if i["mode"] == "DIRECTIONAL")
    mm = sum(1 for i in intents if i["mode"] == "MARKET_MAKE")
    placed = sum(1 for v in results.values() if v[0] == "LIVE_PLACED")
    print("=" * 74)
    print("POLYMARKET LONG/SHORT ENGINE - live book")
    print("=" * 74)
    print(f"snapshot tokens: {len(tokens)}   intents: {len(intents)}  (directional {d}, market-make {mm})")
    print(f"market-making execution: {exec_status} - {exec_detail}")
    if results:
        print(f"  live maker bids placed: {placed}/{len(results)}")
    print(f"\nintents -> {args.out_csv}")
    if exec_status == "dry_run":
        print("DRY-RUN: set PM_MODE=live and POLYMARKET_EXECUTE_LIVE=true (+ credentials) to place")
        print("passive maker bids. Directional long/short stays paper; only market-making graduates.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Polymarket long/short trading engine (dry-run)")
    ap.add_argument("--prediction-log", default=PLOG)
    ap.add_argument("--polymarket-summary", default=POLY)
    ap.add_argument("--market-snapshot", default="",
                    help="Bot market_snapshot.csv (live bid/ask + fair). Enables live-book mode.")
    ap.add_argument("--max-live-orders", type=int, default=3,
                    help="Safety cap on live maker orders placed per run")
    ap.add_argument("--out-csv", default=OUT)
    ap.add_argument("--stake-usdc", type=float, default=50.0, help="Per-leg notional for directional/MM sizing")
    ap.add_argument("--half-spread", type=float, default=0.01, help="Synthesised Polymarket half-spread when no live book")
    ap.add_argument("--min-edge", type=float, default=0.02, help="Price-space edge required for a directional trade")
    ap.add_argument("--arb-cost", type=float, default=0.02, help="Per-set cost (exchange commission + slippage) an arb must clear")
    ap.add_argument("--mm-target-shares", type=float, default=100.0)
    args = ap.parse_args()

    if args.market_snapshot:
        return run_live_book(args)

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
