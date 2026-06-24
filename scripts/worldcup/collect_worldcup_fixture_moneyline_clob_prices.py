from __future__ import annotations

import csv
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from datetime import datetime, timezone
from statistics import mean

TOKENS = Path("outputs/polymarket_training/worldcup_fixture_moneyline_tokens.csv")

OUT = Path("outputs/polymarket_training/worldcup_fixture_moneyline_clob_prices.csv")
SUMMARY = Path("outputs/polymarket_model_governance/worldcup_fixture_moneyline_clob_prices_summary.json")

CLOB = "https://clob.polymarket.com"

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def read_csv(path: Path):
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        return list(csv.DictReader(f))

def get_json(url: str):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "polymarket-worldcup-moneyline-clob-prices"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))

def try_get(url: str):
    try:
        return get_json(url), ""
    except Exception as e:
        return None, str(e)

def fnum(x):
    try:
        if x is None or x == "":
            return None
        return float(x)
    except Exception:
        return None

def best_book_prices(book_payload):
    best_bid = ""
    best_ask = ""

    if not isinstance(book_payload, dict):
        return best_bid, best_ask

    bids = book_payload.get("bids") or []
    asks = book_payload.get("asks") or []

    try:
        if bids:
            best_bid = max(float(b.get("price", 0)) for b in bids)
    except Exception:
        best_bid = ""

    try:
        if asks:
            best_ask = min(float(a.get("price", 1)) for a in asks)
    except Exception:
        best_ask = ""

    return best_bid, best_ask

rows = read_csv(TOKENS)
out = []

for r in rows:
    token = r.get("token_id", "")
    if not token:
        continue

    page_price = fnum(r.get("current_outcome_price"))

    midpoint_url = f"{CLOB}/midpoint?{urllib.parse.urlencode({'token_id': token})}"
    buy_url = f"{CLOB}/price?{urllib.parse.urlencode({'token_id': token, 'side': 'BUY'})}"
    sell_url = f"{CLOB}/price?{urllib.parse.urlencode({'token_id': token, 'side': 'SELL'})}"
    book_url = f"{CLOB}/book?{urllib.parse.urlencode({'token_id': token})}"

    midpoint_payload, midpoint_error = try_get(midpoint_url)
    buy_payload, buy_error = try_get(buy_url)
    sell_payload, sell_error = try_get(sell_url)
    book_payload, book_error = try_get(book_url)

    clob_midpoint = ""
    if isinstance(midpoint_payload, dict):
        clob_midpoint = midpoint_payload.get("mid") or midpoint_payload.get("midpoint") or midpoint_payload.get("price") or ""

    buy_price = ""
    sell_price = ""

    if isinstance(buy_payload, dict):
        buy_price = buy_payload.get("price", "")
    if isinstance(sell_payload, dict):
        sell_price = sell_payload.get("price", "")

    best_bid, best_ask = best_book_prices(book_payload)

    effective_price = page_price

    if effective_price is None:
        effective_price = fnum(clob_midpoint)

    if effective_price is None and fnum(best_bid) is not None and fnum(best_ask) is not None:
        effective_price = (fnum(best_bid) + fnum(best_ask)) / 2

    if effective_price is None:
        effective_price = fnum(buy_price)

    out.append({
        "collected_at_utc": now_iso(),
        "fixture_slug": r.get("fixture_slug", ""),
        "fixture": r.get("fixture", ""),
        "fixture_date": r.get("fixture_date", ""),
        "market_type": r.get("market_type", ""),
        "team_or_draw": r.get("team_or_draw", ""),
        "question": r.get("question", ""),
        "outcome": r.get("outcome", ""),
        "token_id": token,
        "page_price": "" if page_price is None else page_price,
        "clob_midpoint": clob_midpoint,
        "buy_price": buy_price,
        "sell_price": sell_price,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "effective_price": "" if effective_price is None else effective_price,
        "extraction_quality": r.get("extraction_quality", ""),
        "midpoint_error": midpoint_error,
        "buy_error": buy_error,
        "sell_error": sell_error,
        "book_error": book_error,
    })

    time.sleep(0.05)

OUT.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.parent.mkdir(parents=True, exist_ok=True)

fieldnames = [
    "collected_at_utc",
    "fixture_slug",
    "fixture",
    "fixture_date",
    "market_type",
    "team_or_draw",
    "question",
    "outcome",
    "token_id",
    "page_price",
    "clob_midpoint",
    "buy_price",
    "sell_price",
    "best_bid",
    "best_ask",
    "effective_price",
    "extraction_quality",
    "midpoint_error",
    "buy_error",
    "sell_error",
    "book_error",
]

with OUT.open("w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(out)

missing_effective = [r for r in out if r["effective_price"] == ""]
missing_page_price = [r for r in out if r["page_price"] == ""]

summary = {
    "input_token_rows": len(rows),
    "price_rows": len(out),
    "fixtures": len({r["fixture_slug"] for r in out}),
    "missing_page_price_rows": len(missing_page_price),
    "missing_effective_price_rows": len(missing_effective),
    "missing_effective_price_tokens": [r["token_id"] for r in missing_effective],
    "output": str(OUT),
    "status": "ok" if not missing_effective else "missing_effective_prices",
    "note": "effective_price uses page price first, then CLOB midpoint, then book midpoint, then buy price fallback.",
}

SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
