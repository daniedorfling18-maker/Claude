from __future__ import annotations

import csv
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

LABELS = Path("outputs/polymarket_training/completed_game_labels_2026_06_23.csv")

OUT_HISTORY = Path("outputs/polymarket_training/completed_game_clob_price_history_2026_06_23.csv")
OUT_CURRENT = Path("outputs/polymarket_training/completed_game_clob_current_prices_2026_06_23.csv")
SUMMARY = Path("outputs/polymarket_model_governance/completed_game_clob_collection_2026_06_23_summary.json")

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
        headers={"User-Agent": "polymarket-completed-game-price-collector"}
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

labels = read_csv(LABELS)
tokens = sorted({r["token_id"] for r in labels})
label_by_token = {r["token_id"]: r for r in labels}

history_rows = []
current_rows = []
errors = []

for token in tokens:
    label = label_by_token[token]

    # 1. Current midpoint
    midpoint_url = f"{CLOB}/midpoint?{urllib.parse.urlencode({'token_id': token})}"
    midpoint_payload, midpoint_error = try_get(midpoint_url)

    midpoint = ""
    if isinstance(midpoint_payload, dict):
        midpoint = midpoint_payload.get("mid") or midpoint_payload.get("midpoint") or midpoint_payload.get("price") or ""

    # 2. BUY / SELL price fallback
    buy_url = f"{CLOB}/price?{urllib.parse.urlencode({'token_id': token, 'side': 'BUY'})}"
    sell_url = f"{CLOB}/price?{urllib.parse.urlencode({'token_id': token, 'side': 'SELL'})}"

    buy_payload, buy_error = try_get(buy_url)
    sell_payload, sell_error = try_get(sell_url)

    buy_price = ""
    sell_price = ""

    if isinstance(buy_payload, dict):
        buy_price = buy_payload.get("price", "")
    if isinstance(sell_payload, dict):
        sell_price = sell_payload.get("price", "")

    # 3. Order book fallback
    book_url = f"{CLOB}/book?{urllib.parse.urlencode({'token_id': token})}"
    book_payload, book_error = try_get(book_url)

    best_bid = ""
    best_ask = ""

    if isinstance(book_payload, dict):
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

    current_rows.append({
        "collected_at": now_iso(),
        "token_id": token,
        "fixture": label.get("fixture", ""),
        "fixture_slug": label.get("fixture_slug", ""),
        "market_type": label.get("market_type", ""),
        "market_question": label.get("market_question", ""),
        "outcome": label.get("outcome", ""),
        "target": label.get("target", ""),
        "resolved_outcome_price": label.get("resolved_outcome_price", ""),
        "midpoint": midpoint,
        "buy_price": buy_price,
        "sell_price": sell_price,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "midpoint_error": midpoint_error,
        "buy_error": buy_error,
        "sell_error": sell_error,
        "book_error": book_error,
    })

    # 4. Historical price series.
    # Try several variants because Polymarket CLOB history parameter naming has differed across tools.
    history_urls = [
        f"{CLOB}/prices-history?{urllib.parse.urlencode({'market': token, 'interval': 'max'})}",
        f"{CLOB}/prices-history?{urllib.parse.urlencode({'market': token, 'fidelity': '1'})}",
        f"{CLOB}/prices-history?{urllib.parse.urlencode({'token_id': token, 'interval': 'max'})}",
        f"{CLOB}/prices-history?{urllib.parse.urlencode({'token_id': token, 'fidelity': '1'})}",
    ]

    token_history_found = False
    token_history_errors = []

    for url in history_urls:
        payload, err = try_get(url)

        if err:
            token_history_errors.append({"url": url, "error": err})
            continue

        history = []
        if isinstance(payload, dict):
            history = payload.get("history") or payload.get("prices") or payload.get("data") or []
        elif isinstance(payload, list):
            history = payload

        if not isinstance(history, list) or not history:
            token_history_errors.append({"url": url, "error": "empty_history"})
            continue

        token_history_found = True

        for point in history:
            if not isinstance(point, dict):
                continue

            ts = point.get("t") or point.get("timestamp") or point.get("time")
            price = point.get("p") or point.get("price") or point.get("value")

            history_rows.append({
                "token_id": token,
                "fixture": label.get("fixture", ""),
                "fixture_slug": label.get("fixture_slug", ""),
                "market_type": label.get("market_type", ""),
                "market_question": label.get("market_question", ""),
                "outcome": label.get("outcome", ""),
                "target": label.get("target", ""),
                "resolved_outcome_price": label.get("resolved_outcome_price", ""),
                "history_timestamp": ts,
                "history_price": price,
                "history_url_used": url,
            })

        # Stop after first successful history endpoint for this token.
        break

    if not token_history_found:
        errors.append({
            "token_id": token,
            "history_errors": token_history_errors,
        })

    time.sleep(0.2)

OUT_HISTORY.parent.mkdir(parents=True, exist_ok=True)
OUT_CURRENT.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.parent.mkdir(parents=True, exist_ok=True)

current_fields = [
    "collected_at",
    "token_id",
    "fixture",
    "fixture_slug",
    "market_type",
    "market_question",
    "outcome",
    "target",
    "resolved_outcome_price",
    "midpoint",
    "buy_price",
    "sell_price",
    "best_bid",
    "best_ask",
    "midpoint_error",
    "buy_error",
    "sell_error",
    "book_error",
]

with OUT_CURRENT.open("w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=current_fields, extrasaction="ignore")
    w.writeheader()
    w.writerows(current_rows)

history_fields = [
    "token_id",
    "fixture",
    "fixture_slug",
    "market_type",
    "market_question",
    "outcome",
    "target",
    "resolved_outcome_price",
    "history_timestamp",
    "history_price",
    "history_url_used",
]

with OUT_HISTORY.open("w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=history_fields, extrasaction="ignore")
    w.writeheader()
    w.writerows(history_rows)

summary = {
    "label_tokens": len(tokens),
    "current_price_rows": len(current_rows),
    "history_rows": len(history_rows),
    "history_tokens": len({r["token_id"] for r in history_rows}),
    "tokens_without_history": [e["token_id"] for e in errors],
    "current_output": str(OUT_CURRENT),
    "history_output": str(OUT_HISTORY),
    "status": "ok" if history_rows else "no_history_rows",
    "warning": "Current prices on completed markets are terminal. Predictive validation requires historical/pre-resolution rows.",
}

SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
