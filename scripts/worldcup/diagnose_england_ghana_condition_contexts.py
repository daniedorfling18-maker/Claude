from __future__ import annotations

import csv
import json
import re
import urllib.request
from pathlib import Path

SLUG = "fifwc-eng-gha-2026-06-23"
URL = f"https://polymarket.com/sports/world-cup/{SLUG}"

OUT = Path("outputs/polymarket_model_governance/england_ghana_condition_contexts.csv")
SUMMARY = Path("outputs/polymarket_model_governance/england_ghana_condition_contexts_summary.json")

def fetch(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 polymarket-condition-diagnostic"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")

def compact(x: str) -> str:
    x = x.replace("\\u0026", "&")
    x = x.replace("\\/", "/")
    x = re.sub(r"\s+", " ", x)
    return x

def nearby(pattern: str, text: str, fallback: str = "") -> str:
    m = re.search(pattern, text)
    if not m:
        return fallback
    return m.group(1)

html = fetch(URL)

condition_ids = []
for m in re.finditer(r'"conditionId":"([^"]+)"', html):
    condition_ids.append((m.group(1), m.start()))

# preserve order but dedupe
seen = set()
unique = []
for cid, pos in condition_ids:
    if cid not in seen:
        seen.add(cid)
        unique.append((cid, pos))

records = []

for idx, (cid, pos) in enumerate(unique):
    start = max(0, pos - 12000)
    end = min(len(html), pos + 16000)
    window = html[start:end]

    questions = re.findall(r'"question":"((?:\\.|[^"\\])*)"', window)
    titles = re.findall(r'"title":"((?:\\.|[^"\\])*)"', window)
    slugs = re.findall(r'"slug":"([^"]+)"', window)
    outcomes = re.findall(r'"outcomes":(?:"(\[[^\]]+\])"|(\[[^\]]+\]))', window)
    prices = re.findall(r'"outcomePrices":(?:"(\[[^\]]+\])"|(\[[^\]]+\]))', window)
    clobs = re.findall(r'"clobTokenIds":(?:"(\[[^\]]+\])"|(\[[^\]]+\]))', window)

    def flatten(matches):
        vals = []
        for m in matches:
            if isinstance(m, tuple):
                vals.append(next((x for x in m if x), ""))
            else:
                vals.append(m)
        return vals

    q_vals = [compact(q) for q in questions]
    t_vals = [compact(t) for t in titles]
    s_vals = [compact(s) for s in slugs]
    o_vals = flatten(outcomes)
    p_vals = flatten(prices)
    c_vals = flatten(clobs)

    text_probe = compact(window)

    records.append({
        "index": idx,
        "condition_id": cid,
        "question_candidates": " || ".join(q_vals[:12]),
        "title_candidates": " || ".join(t_vals[:12]),
        "slug_candidates": " || ".join(s_vals[:12]),
        "outcomes_candidates": " || ".join(o_vals[:12]),
        "outcome_prices_candidates": " || ".join(p_vals[:12]),
        "clob_token_ids_candidates": " || ".join(c_vals[:12]),
        "contains_england": "england" in text_probe.lower(),
        "contains_ghana": "ghana" in text_probe.lower(),
        "contains_draw": "draw" in text_probe.lower(),
        "contains_spread": "spread" in text_probe.lower(),
        "contains_total": "total" in text_probe.lower(),
        "contains_over": "over" in text_probe.lower(),
        "contains_under": "under" in text_probe.lower(),
        "context_excerpt": text_probe[:3000],
    })

OUT.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.parent.mkdir(parents=True, exist_ok=True)

with OUT.open("w", encoding="utf-8", newline="") as f:
    fieldnames = list(records[0].keys()) if records else ["index", "condition_id"]
    writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(records)

summary = {
    "fixture_slug": SLUG,
    "url": URL,
    "html_length": len(html),
    "condition_id_occurrences": len(condition_ids),
    "unique_condition_ids": len(unique),
    "output": str(OUT),
}

SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
