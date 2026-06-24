from __future__ import annotations

import csv
import json
import re
import time
from pathlib import Path
from datetime import datetime, timezone

import requests

REGISTRY = Path("outputs/polymarket_model_governance/worldcup_local_condition_registry.csv")

ENRICHED = Path("outputs/polymarket_model_governance/worldcup_historical_clob_enriched_condition_registry.csv")
LABELS = Path("outputs/polymarket_training/worldcup_historical_clob_moneyline_labels.csv")
PRICE_HISTORY = Path("outputs/polymarket_training/worldcup_historical_clob_moneyline_price_history.csv")
SUMMARY = Path("outputs/polymarket_model_governance/worldcup_historical_clob_registry_backfill_summary.json")

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def read_csv(path):
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        return list(csv.DictReader(f))

def write_csv(path, rows, fieldnames):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

def fetch_clob_market(session, condition_id):
    url = f"https://clob.polymarket.com/markets/{condition_id}"
    try:
        resp = session.get(url, timeout=30)
        if resp.status_code != 200:
            return {}, f"http_{resp.status_code}"
        return resp.json(), ""
    except Exception as exc:
        return {}, repr(exc)

def fetch_price_history(session, token_id):
    url = "https://clob.polymarket.com/prices-history"
    params = {"market": token_id, "interval": "max", "fidelity": 60}
    try:
        resp = session.get(url, params=params, timeout=30)
        if resp.status_code != 200:
            return [], f"http_{resp.status_code}"
        data = resp.json()
        hist = data.get("history") or []
        return hist, ""
    except Exception as exc:
        return [], repr(exc)

def infer_fixture_slug_from_market_slug(market_slug):
    # Example: fifwc-eng-gha-2026-06-23-eng -> fifwc-eng-gha-2026-06-23
    m = re.match(r"^(fifwc-[a-z0-9-]+-\d{4}-\d{2}-\d{2})(?:-[a-z0-9]+)?$", market_slug or "")
    if m:
        return m.group(1)
    return ""

def infer_market_index(question, market_slug):
    q = (question or "").lower()
    s = (market_slug or "").lower()

    if "end in a draw" in q or s.endswith("-draw"):
        return "1"
    if "win" in q:
        # If slug suffix looks like the team code, we cannot always know home/away from metadata.
        # We keep unknown as blank unless registry already supplied it.
        return ""
    return ""

def infer_team_or_draw(question):
    q = question or ""
    if "end in a draw" in q:
        return "Draw"

    m = re.match(r"Will (.*?) win on \d{4}-\d{2}-\d{2}\?", q)
    if m:
        return m.group(1)

    return ""

def iso_from_unix(t):
    try:
        return datetime.fromtimestamp(int(t), tz=timezone.utc).isoformat()
    except Exception:
        return ""

registry = read_csv(REGISTRY)

# Keep only rows that look relevant to the World Cup backfill.
registry = [
    r for r in registry
    if r.get("condition_id")
    and (
        "fifwc" in (r.get("fixture_slug", "") + r.get("market_slug", "") + r.get("source_files", "")).lower()
        or "england" in (r.get("question", "") + r.get("fixture", "")).lower()
        or "ghana" in (r.get("question", "") + r.get("fixture", "")).lower()
    )
]

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 PolymarketRegistryBackfill/1.0",
})

enriched_rows = []
label_rows = []
history_rows = []
errors = []

seen_conditions = set()

for r in registry:
    condition_id = r.get("condition_id", "").strip()
    if not condition_id or condition_id in seen_conditions:
        continue
    seen_conditions.add(condition_id)

    market, err = fetch_clob_market(session, condition_id)
    if err:
        errors.append({
            "condition_id": condition_id,
            "stage": "fetch_market",
            "error": err,
        })
        continue

    question = market.get("question") or r.get("question", "")
    market_slug = market.get("market_slug") or r.get("market_slug", "")
    fixture_slug = r.get("fixture_slug") or infer_fixture_slug_from_market_slug(market_slug)
    fixture = r.get("fixture", "")
    market_index = r.get("market_index") or infer_market_index(question, market_slug)
    team_or_draw = r.get("team_or_draw") or infer_team_or_draw(question)

    tokens = market.get("tokens") or []
    if not isinstance(tokens, list):
        tokens = []

    enriched_rows.append({
        "enriched_at_utc": now_iso(),
        "condition_id": condition_id,
        "question": question,
        "fixture_slug": fixture_slug,
        "fixture": fixture,
        "market_slug": market_slug,
        "market_index": market_index,
        "market_type": r.get("market_type", ""),
        "team_or_draw": team_or_draw,
        "active": market.get("active", ""),
        "closed": market.get("closed", ""),
        "archived": market.get("archived", ""),
        "accepting_orders": market.get("accepting_orders", ""),
        "enable_order_book": market.get("enable_order_book", ""),
        "game_start_time": market.get("game_start_time", ""),
        "end_date_iso": market.get("end_date_iso", ""),
        "tokens_raw": json.dumps(tokens, ensure_ascii=False),
        "source_files": r.get("source_files", ""),
    })

    for token in tokens:
        token_id = str(token.get("token_id", "")).strip()
        outcome = token.get("outcome", "")
        winner = token.get("winner", "")

        if not token_id:
            continue

        # Closed CLOB market has winner boolean. Use it as final settlement label.
        target = 1 if str(winner).lower() == "true" else 0

        label_rows.append({
            "label_generated_at_utc": now_iso(),
            "condition_id": condition_id,
            "question": question,
            "fixture_slug": fixture_slug,
            "fixture": fixture,
            "market_slug": market_slug,
            "market_index": market_index,
            "team_or_draw": team_or_draw,
            "outcome": outcome,
            "target": target,
            "winner": winner,
            "token_id": token_id,
            "closed": market.get("closed", ""),
            "active": market.get("active", ""),
        })

        hist, hist_err = fetch_price_history(session, token_id)
        if hist_err:
            errors.append({
                "condition_id": condition_id,
                "token_id": token_id,
                "stage": "fetch_price_history",
                "error": hist_err,
            })
            continue

        for point in hist:
            t = point.get("t")
            p = point.get("p")
            if t is None or p is None:
                continue

            history_rows.append({
                "condition_id": condition_id,
                "question": question,
                "fixture_slug": fixture_slug,
                "fixture": fixture,
                "market_slug": market_slug,
                "market_index": market_index,
                "team_or_draw": team_or_draw,
                "outcome": outcome,
                "target": target,
                "winner": winner,
                "token_id": token_id,
                "history_timestamp": t,
                "history_timestamp_utc": iso_from_unix(t),
                "price": p,
            })

        time.sleep(0.10)

    time.sleep(0.10)

enriched_fields = [
    "enriched_at_utc",
    "condition_id",
    "question",
    "fixture_slug",
    "fixture",
    "market_slug",
    "market_index",
    "market_type",
    "team_or_draw",
    "active",
    "closed",
    "archived",
    "accepting_orders",
    "enable_order_book",
    "game_start_time",
    "end_date_iso",
    "tokens_raw",
    "source_files",
]

label_fields = [
    "label_generated_at_utc",
    "condition_id",
    "question",
    "fixture_slug",
    "fixture",
    "market_slug",
    "market_index",
    "team_or_draw",
    "outcome",
    "target",
    "winner",
    "token_id",
    "closed",
    "active",
]

history_fields = [
    "condition_id",
    "question",
    "fixture_slug",
    "fixture",
    "market_slug",
    "market_index",
    "team_or_draw",
    "outcome",
    "target",
    "winner",
    "token_id",
    "history_timestamp",
    "history_timestamp_utc",
    "price",
]

write_csv(ENRICHED, enriched_rows, enriched_fields)
write_csv(LABELS, label_rows, label_fields)
write_csv(PRICE_HISTORY, history_rows, history_fields)

summary = {
    "registry_rows_input": len(registry),
    "conditions_enriched": len(enriched_rows),
    "label_rows": len(label_rows),
    "price_history_rows": len(history_rows),
    "unique_conditions": len({r["condition_id"] for r in enriched_rows}),
    "unique_tokens": len({r["token_id"] for r in label_rows}),
    "unique_fixtures": len({r["fixture_slug"] for r in enriched_rows if r["fixture_slug"]}),
    "errors": len(errors),
    "error_samples": errors[:20],
    "enriched_output": str(ENRICHED),
    "labels_output": str(LABELS),
    "price_history_output": str(PRICE_HISTORY),
}

SUMMARY.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
