from __future__ import annotations

import csv
import json
import re
import urllib.request
from pathlib import Path

SLUG = "fifwc-eng-gha-2026-06-23"
URL = f"https://polymarket.com/sports/world-cup/{SLUG}"

OUT = Path("outputs/polymarket_model_governance/england_ghana_fixture_market_inventory.csv")
RAW = Path("outputs/polymarket_model_governance/england_ghana_fixture_page_raw_markets.json")
SUMMARY = Path("outputs/polymarket_model_governance/england_ghana_fixture_market_inventory_summary.json")

def fetch(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 polymarket-fixture-market-extractor"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")

html = fetch(URL)

# The page stores JSON-like fragments inside the app payload.
# We first extract all compact JSON objects that look like market objects.
objects = []

for match in re.finditer(r'\{[^{}]*"conditionId":"[^"]+"[^{}]*\}', html):
    raw = match.group(0)
    try:
        obj = json.loads(raw)
        objects.append(obj)
    except Exception:
        pass

# If the above is too strict, extract surrounding windows around conditionId and parse useful fields by regex.
windows = []
for m in re.finditer(r'"conditionId":"([^"]+)"', html):
    start = max(0, m.start() - 5000)
    end = min(len(html), m.end() + 8000)
    windows.append(html[start:end])

records = []
seen_conditions = set()

for idx, window in enumerate(windows):
    condition_match = re.search(r'"conditionId":"([^"]+)"', window)
    if not condition_match:
        continue

    condition_id = condition_match.group(1)
    if condition_id in seen_conditions:
        continue
    seen_conditions.add(condition_id)

    slug_match = re.search(r'"slug":"([^"]+)"', window)
    question_match = re.search(r'"question":"((?:\\.|[^"\\])*)"', window)
    title_match = re.search(r'"title":"((?:\\.|[^"\\])*)"', window)
    outcomes_match = re.search(r'"outcomes":"(\[[^\]]+\])"', window)
    prices_match = re.search(r'"outcomePrices":"(\[[^\]]+\])"', window)
    clob_match = re.search(r'"clobTokenIds":"(\[[^\]]+\])"', window)

    # Sometimes arrays are embedded directly rather than stringified.
    if not outcomes_match:
        outcomes_match = re.search(r'"outcomes":(\[[^\]]+\])', window)
    if not prices_match:
        prices_match = re.search(r'"outcomePrices":(\[[^\]]+\])', window)
    if not clob_match:
        clob_match = re.search(r'"clobTokenIds":(\[[^\]]+\])', window)

    def clean_json_string(x):
        if not x:
            return ""
        try:
            return json.loads(f'"{x}"')
        except Exception:
            return x

    slug = slug_match.group(1) if slug_match else ""
    question = clean_json_string(question_match.group(1)) if question_match else ""
    title = clean_json_string(title_match.group(1)) if title_match else ""

    outcomes_raw = outcomes_match.group(1) if outcomes_match else ""
    prices_raw = prices_match.group(1) if prices_match else ""
    clob_raw = clob_match.group(1) if clob_match else ""

    market_text = f"{slug} {question} {title}".lower()

    if "moneyline" in market_text:
        market_type = "moneyline"
    elif "spread" in market_text or "handicap" in market_text:
        market_type = "spread"
    elif "total" in market_text or "over" in market_text or "under" in market_text:
        market_type = "total"
    elif "draw" in market_text or "winner" in market_text:
        market_type = "moneyline_or_winner"
    else:
        market_type = "unknown_fixture_market"

    records.append({
        "fixture_slug": SLUG,
        "fixture_url": URL,
        "condition_id": condition_id,
        "market_slug": slug,
        "question": question or title,
        "market_type_guess": market_type,
        "outcomes_raw": outcomes_raw,
        "outcome_prices_raw": prices_raw,
        "clob_token_ids_raw": clob_raw,
        "window_index": idx,
    })

OUT.parent.mkdir(parents=True, exist_ok=True)
RAW.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.parent.mkdir(parents=True, exist_ok=True)

with OUT.open("w", encoding="utf-8", newline="") as f:
    fieldnames = [
        "fixture_slug",
        "fixture_url",
        "condition_id",
        "market_slug",
        "question",
        "market_type_guess",
        "outcomes_raw",
        "outcome_prices_raw",
        "clob_token_ids_raw",
        "window_index",
    ]
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(records)

RAW.write_text(json.dumps({
    "url": URL,
    "objects_strict_count": len(objects),
    "condition_windows": len(windows),
    "records": records,
}, indent=2), encoding="utf-8")

summary = {
    "fixture_slug": SLUG,
    "url": URL,
    "html_length": len(html),
    "condition_windows": len(windows),
    "market_records": len(records),
    "output": str(OUT),
    "raw_output": str(RAW),
    "next_step": "Inspect extracted markets. If moneyline/spread/total are present, build England-Ghana game labels.",
}

SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
