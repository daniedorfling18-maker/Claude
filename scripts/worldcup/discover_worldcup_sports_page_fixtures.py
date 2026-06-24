from __future__ import annotations

import csv
import json
import re
import urllib.request
from pathlib import Path
from urllib.parse import urljoin

BASE = "https://polymarket.com"
URL = "https://polymarket.com/sports/world-cup/games"

OUT = Path("outputs/polymarket_model_governance/worldcup_sports_page_fixtures.csv")
SUMMARY = Path("outputs/polymarket_model_governance/worldcup_sports_page_fixtures_summary.json")

def fetch(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 polymarket-fixture-discovery"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")

html = fetch(URL)

# Extract fixture slugs from hrefs.
paths = sorted(set(re.findall(r'href="([^"]*/sports/world-cup/fifwc-[^"]+)"', html)))
paths += sorted(set(re.findall(r'"(/sports/world-cup/fifwc-[^"]+)"', html)))
paths = sorted(set(p.split("?")[0] for p in paths))

records = []
for path in paths:
    full_url = urljoin(BASE, path)
    slug = path.rstrip("/").split("/")[-1]
    records.append({
        "slug": slug,
        "path": path,
        "url": full_url,
    })

OUT.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.parent.mkdir(parents=True, exist_ok=True)

with OUT.open("w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["slug", "path", "url"])
    writer.writeheader()
    writer.writerows(records)

summary = {
    "source_url": URL,
    "fixture_links_found": len(records),
    "output": str(OUT),
    "sample_slugs": [r["slug"] for r in records[:20]],
}

SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
