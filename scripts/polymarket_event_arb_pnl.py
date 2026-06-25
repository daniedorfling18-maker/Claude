"""Complete-set dutch-book arbitrage P&L scanner for Polymarket negRisk events.

Unlike a paging scan (which captures only an event's high-volume outcomes and so undercounts
the basket), this fetches the COMPLETE outcome set per event via Gamma ``/events/{id}`` and
only treats a basket as an arb when every outcome has a live ask. It then ranks by the real
profitability metric - annualised return on capital - because a lock ties capital up until the
event resolves (a 3% lock resolving in two years is ~1.5%/yr, not 3%).

Lock per 1-USDC set = ``1 - sum(best_ask_i)``; executable sets = thinnest leg's ask depth;
capital = ``sum(ask) x sets``; profit = ``lock x sets``; annualised = ``(lock/sum) x 365/days``.

Run:  python scripts/polymarket_event_arb_pnl.py --max-events 12  (paper analysis; never trades)
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

GAMMA_MARKETS = "https://gamma-api.polymarket.com/markets"
GAMMA_EVENT = "https://gamma-api.polymarket.com/events/"
CLOB_BOOK = "https://clob.polymarket.com/book"
_CA = "/root/.ccr/ca-bundle.crt"
_CTX = ssl.create_default_context(cafile=_CA) if os.path.exists(_CA) else ssl.create_default_context()


def _get(url: str, timeout: int = 60):
    req = urllib.request.Request(url, headers={"User-Agent": "polymarket-event-arb/1.0"})
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


def _best_ask(token: str) -> tuple[float, float] | None:
    book = _get(f"{CLOB_BOOK}?" + urllib.parse.urlencode({"token_id": token}))
    asks = [(float(a["price"]), float(a["size"])) for a in book.get("asks", []) if a.get("price") and a.get("size")]
    return min(asks, key=lambda ps: ps[0]) if asks else None


def discover_event_ids(max_pages: int) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for off in range(0, max_pages * 100, 100):
        page = _get(f"{GAMMA_MARKETS}?" + urllib.parse.urlencode(
            {"closed": "false", "active": "true", "limit": "100", "offset": str(off),
             "order": "volumeNum", "ascending": "false"}))
        if not isinstance(page, list) or not page:
            break
        for m in page:
            if str(m.get("negRisk")).lower() not in ("true", "1"):
                continue
            for ev in (m.get("events") or []):
                eid = str(ev.get("id") or "")
                if eid and eid not in seen:
                    seen.add(eid)
                    ids.append(eid)
    return ids


def scan_event(event_id: str, pause: float, max_outcomes: int) -> dict | None:
    event = _get(f"{GAMMA_EVENT}{event_id}")
    markets = event.get("markets", []) or []
    if str(event.get("negRisk")).lower() not in ("true", "1") or not (3 <= len(markets) <= max_outcomes):
        return None
    asks, sizes = [], []
    for m in markets:
        toks = _jl(m.get("clobTokenIds"))
        if not toks:
            return None  # cannot price every leg -> not a complete tradeable basket
        best = _best_ask(toks[0])
        if best is None:
            return None
        asks.append(best[0])
        sizes.append(best[1])
        if pause:
            time.sleep(pause)
    arb = dutch_arb(asks)
    sets = min(sizes) if sizes else 0.0
    end = event.get("endDate") or event.get("endDateIso") or ""
    try:
        days = max(1, (datetime.fromisoformat(end.replace("Z", "+00:00")) - datetime.now(timezone.utc)).days)
    except Exception:
        days = None
    roi = (arb.locked_profit_per_set / arb.total_cost) if arb.total_cost else 0.0
    annualised = round(roi * 365 / days, 4) if (days and arb.is_arb) else (0.0 if not arb.is_arb else None)
    return {
        "event": (event.get("title") or "")[:70],
        "outcomes": len(markets),
        "ask_sum": round(arb.total_cost, 4),
        "lock_per_set": round(arb.locked_profit_per_set, 4),
        "is_arb": arb.is_arb,
        "executable_sets": round(sets, 2),
        "capital_usdc": round(arb.total_cost * sets, 2),
        "profit_usdc": round(arb.locked_profit_per_set * sets, 2),
        "days_to_resolution": days,
        "annualised_return_on_capital": annualised,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-pages", type=int, default=4)
    ap.add_argument("--max-events", type=int, default=12)
    ap.add_argument("--max-outcomes", type=int, default=80)
    ap.add_argument("--pause", type=float, default=0.02)
    ap.add_argument("--out", default="outputs/polymarket_arbitrage")
    args = ap.parse_args()

    print("Discovering negRisk event ids ...", flush=True)
    ids = discover_event_ids(args.max_pages)[: args.max_events]
    print(f"Scanning {len(ids)} complete events ...", flush=True)
    results = []
    for i, eid in enumerate(ids, 1):
        try:
            row = scan_event(eid, args.pause, args.max_outcomes)
        except Exception as exc:
            print(f"  event {eid} error: {exc}", flush=True)
            continue
        if not row:
            continue
        results.append(row)
        tag = "  *** ARB ***" if row["is_arb"] else ""
        print(f"  [{i}/{len(ids)}] {row['outcomes']:>2} legs | ask_sum={row['ask_sum']:.4f} "
              f"lock={row['lock_per_set']:.4f} ann={row['annualised_return_on_capital']}{tag} | {row['event'][:44]}", flush=True)

    arbs = [r for r in results if r["is_arb"]]
    arbs.sort(key=lambda r: -(r["annualised_return_on_capital"] or 0))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    import csv as _csv
    if results:
        with (out_dir / "event_arb_pnl.csv").open("w", newline="", encoding="utf-8") as f:
            w = _csv.DictWriter(f, fieldnames=list(results[0].keys()))
            w.writeheader()
            w.writerows(results)
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "events_scanned": len(results),
        "complete_arbs": len(arbs),
        "best_annualised_return_on_capital": arbs[0]["annualised_return_on_capital"] if arbs else 0.0,
        "total_lockable_profit_usdc": round(sum(r["profit_usdc"] for r in arbs), 2),
        "total_capital_required_usdc": round(sum(r["capital_usdc"] for r in arbs), 2),
        "top_arbs": arbs[:5],
        "note": "Complete outcome sets only (every leg priced). Ranked by annualised return on capital, "
                "since locks tie capital up until resolution. Polymarket has no taker fee.",
    }
    with (out_dir / "event_arb_pnl_summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print("\n" + json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
