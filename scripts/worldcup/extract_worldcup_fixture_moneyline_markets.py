from __future__ import annotations

import csv
import json
import re
import time
import urllib.request
from pathlib import Path

INP = Path("outputs/polymarket_model_governance/worldcup_sports_page_fixtures.csv")

OUT_MARKETS = Path("outputs/polymarket_model_governance/worldcup_fixture_moneyline_market_inventory.csv")
OUT_TOKENS = Path("outputs/polymarket_training/worldcup_fixture_moneyline_tokens.csv")
SUMMARY = Path("outputs/polymarket_model_governance/worldcup_fixture_moneyline_market_inventory_summary.json")

def fetch(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 polymarket-worldcup-fixture-moneyline-extractor-v3"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")

def read_csv(path: Path):
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        return list(csv.DictReader(f))

def unique_preserve(values):
    seen = set()
    out = []
    for v in values:
        if not v:
            continue
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out

def clean_json_string(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except Exception:
        return value.replace("\\u0026", "&").replace("\\/", "/")

def parse_json_array(value: str):
    if not value:
        return []
    try:
        return json.loads(value)
    except Exception:
        try:
            return json.loads(clean_json_string(value))
        except Exception:
            return []

def extract_arrays_no_dedupe(pattern_quoted: str, pattern_direct: str, html: str):
    vals = []

    for m in re.finditer(pattern_quoted, html):
        vals.append(clean_json_string(m.group(1)))

    for m in re.finditer(pattern_direct, html):
        vals.append(m.group(1))

    return vals

def classify_question(question: str) -> str:
    q = question.lower()

    if "end in a draw" in q:
        return "draw_yes_no"

    if " win on " in q:
        return "team_win_yes_no"

    return "unknown"

def extract_team_from_question(question: str) -> str:
    m = re.match(r"Will (.*?) win on \d{4}-\d{2}-\d{2}\?", question)
    if m:
        return m.group(1).strip()

    if "end in a draw" in question:
        return "Draw"

    return ""

def slug_date(slug: str) -> str:
    m = re.search(r"(\d{4}-\d{2}-\d{2})$", slug)
    return m.group(1) if m else ""

def fixture_from_slug(slug: str) -> str:
    parts = slug.split("-")
    if len(parts) >= 4:
        return f"{parts[1]}_{parts[2]}"
    return slug

fixtures = read_csv(INP)

market_rows = []
token_rows = []
page_summaries = []

for fx in fixtures:
    slug = fx.get("slug", "")
    url = fx.get("url", "")

    if not slug or not url:
        continue

    try:
        html = fetch(url)
        error = ""
    except Exception as e:
        html = ""
        error = str(e)

    condition_ids_all = unique_preserve(re.findall(r'"conditionId":"([^"]+)"', html))

    questions_all = unique_preserve([
        clean_json_string(q)
        for q in re.findall(r'"question":"((?:\\.|[^"\\])*)"', html)
    ])

    moneyline_questions = [
        q for q in questions_all
        if " end in a draw" in q.lower() or re.search(r"Will .* win on \d{4}-\d{2}-\d{2}\?", q)
    ]

    prices_raw_all = extract_arrays_no_dedupe(
        r'"outcomePrices":"(\[[^\]]+\])"',
        r'"outcomePrices":(\[[^\]]+\])',
        html,
    )

    clob_raw_all = extract_arrays_no_dedupe(
        r'"clobTokenIds":"(\[[^\]]+\])"',
        r'"clobTokenIds":(\[[^\]]+\])',
        html,
    )

    prices_raw = unique_preserve(prices_raw_all)[:3]
    clob_raw = unique_preserve(clob_raw_all)[:3]
    condition_ids = condition_ids_all[:3]

    # Core fix:
    # Do not require all three price arrays. Prices can be fetched from CLOB later.
    record_count = min(
        len(moneyline_questions),
        len(condition_ids),
        len(clob_raw),
        3,
    )

    for i in range(record_count):
        question = moneyline_questions[i]
        market_type = classify_question(question)
        team_or_draw = extract_team_from_question(question)

        outcomes = ["Yes", "No"]
        prices = parse_json_array(prices_raw[i]) if i < len(prices_raw) else ["", ""]
        token_ids = parse_json_array(clob_raw[i])
        condition_id = condition_ids[i]

        quality = "ok"
        if len(token_ids) != 2:
            quality = "token_array_length_warning"
        elif len(prices) != 2 or prices == ["", ""]:
            quality = "missing_page_price_clob_required"

        market_rows.append({
            "fixture_slug": slug,
            "fixture": fixture_from_slug(slug),
            "fixture_date": slug_date(slug),
            "fixture_url": url,
            "market_index": i,
            "condition_id": condition_id,
            "market_type": market_type,
            "team_or_draw": team_or_draw,
            "question": question,
            "outcomes_raw": json.dumps(outcomes, ensure_ascii=False),
            "outcome_prices_raw": json.dumps(prices, ensure_ascii=False),
            "clob_token_ids_raw": json.dumps(token_ids, ensure_ascii=False),
            "extraction_quality": quality,
        })

        for outcome, price, token_id in zip(outcomes, prices, token_ids):
            token_rows.append({
                "fixture_slug": slug,
                "fixture": fixture_from_slug(slug),
                "fixture_date": slug_date(slug),
                "fixture_url": url,
                "condition_id": condition_id,
                "market_type": market_type,
                "team_or_draw": team_or_draw,
                "question": question,
                "outcome": outcome,
                "current_outcome_price": price,
                "token_id": token_id,
                "extraction_quality": quality,
            })

    page_summaries.append({
        "fixture_slug": slug,
        "url": url,
        "html_length": len(html),
        "error": error,
        "condition_ids_found": len(condition_ids_all),
        "moneyline_questions_found": len(moneyline_questions),
        "price_arrays_found": len(unique_preserve(prices_raw_all)),
        "clob_arrays_found": len(unique_preserve(clob_raw_all)),
        "moneyline_records_extracted": record_count,
    })

    time.sleep(0.2)

OUT_MARKETS.parent.mkdir(parents=True, exist_ok=True)
OUT_TOKENS.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.parent.mkdir(parents=True, exist_ok=True)

with OUT_MARKETS.open("w", encoding="utf-8", newline="") as f:
    fieldnames = [
        "fixture_slug",
        "fixture",
        "fixture_date",
        "fixture_url",
        "market_index",
        "condition_id",
        "market_type",
        "team_or_draw",
        "question",
        "outcomes_raw",
        "outcome_prices_raw",
        "clob_token_ids_raw",
        "extraction_quality",
    ]
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(market_rows)

with OUT_TOKENS.open("w", encoding="utf-8", newline="") as f:
    fieldnames = [
        "fixture_slug",
        "fixture",
        "fixture_date",
        "fixture_url",
        "condition_id",
        "market_type",
        "team_or_draw",
        "question",
        "outcome",
        "current_outcome_price",
        "token_id",
        "extraction_quality",
    ]
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(token_rows)

summary = {
    "fixture_pages_input": len(fixtures),
    "fixture_pages_processed": len(page_summaries),
    "market_rows": len(market_rows),
    "token_rows": len(token_rows),
    "fixtures_with_full_moneyline": sum(1 for p in page_summaries if p["moneyline_records_extracted"] == 3),
    "fixtures_missing_moneyline": [
        p for p in page_summaries
        if p["moneyline_records_extracted"] != 3 or p["error"]
    ],
    "quality_counts": {
        q: sum(1 for r in market_rows if r["extraction_quality"] == q)
        for q in sorted({r["extraction_quality"] for r in market_rows})
    },
    "market_output": str(OUT_MARKETS),
    "token_output": str(OUT_TOKENS),
    "note": "Extracts three binary moneyline markets per fixture: team A win, draw, team B win. Missing page prices are allowed because CLOB can fill prices.",
}

SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
