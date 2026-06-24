"""Live dutch-book arbitrage scanner for Polymarket multi-outcome (negRisk) events.

For a mutually-exclusive-and-exhaustive event (exactly one outcome resolves YES), if the
best YES asks across all outcomes sum below 1 you can buy one share of each and lock
``1 - sum`` per set at resolution. Polymarket currently charges no taker fee, so a positive
lock is near-pure profit (bounded by the thinnest leg's depth). This scans live order books
and reports any lock, its executable size, and the dollar profit.

Market-neutral edge: requires no forecasting skill (the audit showed there is none). The
caveat is exhaustiveness - if the listed outcomes are not collectively exhaustive (an
"other"/none wins), the lock is not risk-free; negRisk events are designed to be exhaustive.

Run from the repo root:  python scripts/polymarket_dutch_arb_scanner.py --max-groups 25
"""
from __future__ import annotations

import argparse
import collections
import http.client
import json
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from superbru_score_engine.betting.long_short import dutch_arb  # noqa: E402

GAMMA = "https://gamma-api.polymarket.com/markets"
CLOB_BOOK = "https://clob.polymarket.com/book"
_CA = "/root/.ccr/ca-bundle.crt"
_CTX = ssl.create_default_context(cafile=_CA) if os.path.exists(_CA) else ssl.create_default_context()


def _get(url: str, timeout: int = 60):
    req = urllib.request.Request(url, headers={"User-Agent": "polymarket-arb-scanner/1.0"})
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
        try:
            data = r.read()
        except http.client.IncompleteRead as exc:
            data = exc.partial
    return json.loads(data.decode())


def _jl(value):
    try:
        return json.loads(value) if isinstance(value, str) else (value or [])
    except Exception:
        return []


def _best(levels, side):
    """Best (price, size) for a book side: lowest ask / highest bid."""
    parsed = [(float(x["price"]), float(x["size"])) for x in levels if x.get("price") and x.get("size")]
    if not parsed:
        return None, 0.0
    chosen = min(parsed, key=lambda ps: ps[0]) if side == "ask" else max(parsed, key=lambda ps: ps[0])
    return chosen


def discover_groups(max_pages: int, min_outcomes: int) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for off in range(0, max_pages * 100, 100):
        page = _get(f"{GAMMA}?" + urllib.parse.urlencode(
            {"closed": "false", "active": "true", "limit": "100", "offset": str(off),
             "order": "volumeNum", "ascending": "false"}))
        if not isinstance(page, list) or not page:
            break
        for m in page:
            if str(m.get("negRisk")).lower() in ("true", "1"):
                gid = m.get("negRiskMarketID") or m.get("negRiskRequestID") or ""
                if gid:
                    groups[gid].append(m)
    return {g: ms for g, ms in groups.items() if len(ms) >= min_outcomes}


def scan_group(markets: list[dict], pause: float, min_ask_sum: float = 0.85) -> dict | None:
    asks, ask_sizes, bids, bid_sizes = [], [], [], []
    for m in markets:
        toks = _jl(m.get("clobTokenIds"))
        if not toks:
            return None
        book = _get(f"{CLOB_BOOK}?" + urllib.parse.urlencode({"token_id": toks[0]}))
        ap, asz = _best(book.get("asks", []), "ask")
        bp, bsz = _best(book.get("bids", []), "bid")
        if ap is None:
            return None
        asks.append(ap)
        ask_sizes.append(asz)
        bids.append(bp if bp is not None else 0.0)
        bid_sizes.append(bsz)
        if pause:
            time.sleep(pause)
    arb = dutch_arb(asks)
    buy_size = min(ask_sizes) if ask_sizes else 0.0
    bid_sum = sum(bids)
    sell_lock = max(0.0, bid_sum - 1.0)
    sell_size = min(bid_sizes) if bid_sizes else 0.0
    # A complete exhaustive event's asks sum to ~1. A sum far below 1 means we only captured
    # part of the outcome set (low-volume legs missing), which is a false "lock", not an arb.
    likely_complete = arb.total_cost >= min_ask_sum
    return {
        "event": (markets[0].get("question") or markets[0].get("groupItemTitle") or "")[:80],
        "outcomes": len(markets),
        "ask_sum": round(arb.total_cost, 4),
        "buy_lock_per_set": round(arb.locked_profit_per_set, 4),
        "buy_executable_size": round(buy_size, 2),
        "buy_profit_usdc": round(arb.locked_profit_per_set * buy_size, 2),
        "bid_sum": round(bid_sum, 4),
        "sell_lock_per_set": round(sell_lock, 4),
        "sell_executable_size": round(sell_size, 2),
        "sell_profit_usdc": round(sell_lock * sell_size, 2),
        "likely_complete": likely_complete,
        "arbitrage": bool(likely_complete and (arb.is_arb or sell_lock > 0)),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-pages", type=int, default=4)
    ap.add_argument("--min-outcomes", type=int, default=3)
    ap.add_argument("--max-groups", type=int, default=25)
    ap.add_argument("--min-ask-sum", type=float, default=0.85,
                    help="reject groups whose ask sum is below this as incomplete outcome capture")
    ap.add_argument("--pause", type=float, default=0.03)
    ap.add_argument("--out", default="outputs/polymarket_arbitrage")
    args = ap.parse_args()

    print("Discovering live negRisk multi-outcome groups ...", flush=True)
    groups = discover_groups(args.max_pages, args.min_outcomes)
    selected = sorted(groups.items(), key=lambda kv: -len(kv[1]))[:args.max_groups]
    print(f"Scanning {len(selected)} of {len(groups)} groups ...", flush=True)

    results = []
    for i, (gid, markets) in enumerate(selected, 1):
        try:
            row = scan_group(markets, args.pause, min_ask_sum=args.min_ask_sum)
        except Exception as exc:
            print(f"  group {i} error: {exc}", flush=True)
            continue
        if row:
            results.append(row)
            tag = "  *** ARB ***" if row["arbitrage"] else ""
            print(f"  [{i}/{len(selected)}] {row['outcomes']:>2} outcomes | ask_sum={row['ask_sum']:.4f} "
                  f"buy_lock={row['buy_lock_per_set']:.4f} bid_sum={row['bid_sum']:.4f}{tag} | {row['event'][:48]}", flush=True)

    results.sort(key=lambda r: -max(r["buy_lock_per_set"], r["sell_lock_per_set"]))
    arbs = [r for r in results if r["arbitrage"]]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    import csv as _csv
    if results:
        with (out_dir / "dutch_arb_scan.csv").open("w", newline="", encoding="utf-8") as f:
            w = _csv.DictWriter(f, fieldnames=list(results[0].keys()))
            w.writeheader()
            w.writerows(results)
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "groups_scanned": len(results),
        "incomplete_groups_filtered": sum(1 for r in results if not r["likely_complete"]),
        "arbitrage_opportunities": len(arbs),
        "best_buy_lock_per_set": max((r["buy_lock_per_set"] for r in arbs), default=0.0),
        "best_sell_lock_per_set": max((r["sell_lock_per_set"] for r in arbs), default=0.0),
        "total_lockable_profit_usdc": round(sum(max(r["buy_profit_usdc"], r["sell_profit_usdc"]) for r in arbs), 2),
        "mean_ask_sum_complete": round(
            sum(r["ask_sum"] for r in results if r["likely_complete"]) / max(1, sum(1 for r in results if r["likely_complete"])), 4),
        "note": "Only complete outcome sets (ask sum >= min_ask_sum) count as arbs; a far-below-1 sum means "
                "missing legs, not a lock. Polymarket charges no taker fee, but locks are bounded by the "
                "thinnest leg's depth and by capital lock-up to resolution; risk-free only if exhaustive.",
    }
    with (out_dir / "dutch_arb_summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print("\n" + json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
