from __future__ import annotations

import csv
import json
import re
import urllib.request
from pathlib import Path
from urllib.error import HTTPError, URLError

BASE = "https://polymarket.com/sports/world-cup"

SLUGS = [
    "fifwc-por-uzb-2026-06-23",
    "fifwc-eng-gha-2026-06-23",
    "fifwc-pan-cro-2026-06-23",
    "fifwc-col-cod-2026-06-23",
    "fifwc-col-drc-2026-06-23",
    "fifwc-col-cgo-2026-06-23",
]

OUT = Path("outputs/polymarket_model_governance/worldcup_yesterday_fixture_page_probe.csv")
SUMMARY = Path("outputs/polymarket_model_governance/worldcup_yesterday_fixture_page_probe_summary.json")

def fetch(url: str):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 polymarket-fixture-probe"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body, ""
    except HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return e.code, body, str(e)
    except URLError as e:
        return "", "", str(e)

records = []

for slug in SLUGS:
    url = f"{BASE}/{slug}"
    status, html, error = fetch(url)

    title_match = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
    title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""

    has_moneyline = "Moneyline" in html
    has_spread = "Spread" in html or "Spreads" in html
    has_total = "Total" in html or "Totals" in html
    has_resolution = "resolution" in html.lower()
    has_orderbook = "outcomePrices" in html or "clobTokenIds" in html or "conditionId" in html

    condition_ids = sorted(set(re.findall(r'"conditionId":"([^"]+)"', html)))
    clob_ids = sorted(set(re.findall(r'"clobTokenIds":(\[[^\]]+\])', html)))
    market_slugs = sorted(set(re.findall(r'"slug":"([^"]+)"', html)))[:50]

    records.append({
        "slug": slug,
        "url": url,
        "http_status": status,
        "title": title,
        "html_length": len(html),
        "has_moneyline": has_moneyline,
        "has_spread": has_spread,
        "has_total": has_total,
        "has_resolution_text": has_resolution,
        "has_orderbook_markers": has_orderbook,
        "condition_id_count": len(condition_ids),
        "clob_token_array_count": len(clob_ids),
        "market_slug_count": len(market_slugs),
        "sample_market_slugs": "|".join(market_slugs[:10]),
        "error": error,
    })

OUT.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.parent.mkdir(parents=True, exist_ok=True)

with OUT.open("w", encoding="utf-8", newline="") as f:
    fieldnames = list(records[0].keys())
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(records)

summary = {
    "probed_slugs": len(SLUGS),
    "existing_pages": sum(1 for r in records if str(r["http_status"]) == "200" and int(r["html_length"]) > 10000),
    "pages_with_orderbook_markers": sum(1 for r in records if r["has_orderbook_markers"]),
    "pages_with_moneyline": sum(1 for r in records if r["has_moneyline"]),
    "output": str(OUT),
    "records": records,
}

SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
