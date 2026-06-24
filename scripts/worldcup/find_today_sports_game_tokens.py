from __future__ import annotations

import json
import re
import requests
from pathlib import Path
from urllib.parse import quote

SLUGS = [
    "fifwc-che-can-2026-06-24",
    "fifwc-bih-qat-2026-06-24",
    "fifwc-sco-bra-2026-06-24",
    "fifwc-mar-hai-2026-06-24",
    "fifwc-cze-mex-2026-06-24",
    "fifwc-rsa-kor-2026-06-24",
]

BASES = [
    "https://gamma-api.polymarket.com/events?slug={slug}",
    "https://gamma-api.polymarket.com/events/slug/{slug}",
    "https://gamma-api.polymarket.com/markets?slug={slug}",
    "https://gamma-api.polymarket.com/markets?event_slug={slug}",
    "https://gamma-api.polymarket.com/markets?eventSlug={slug}",
    "https://polymarket.com/sports/world-cup/{slug}",
]

def parse_list(value):
    if isinstance(value, list):
        return value
    if value is None:
        return []
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []

def walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for x in obj:
            yield from walk(x)

def extract_markets_from_json(obj, source):
    found = []
    for d in walk(obj):
        tokens = parse_list(d.get("clobTokenIds") or d.get("clob_token_ids") or d.get("tokenIds") or d.get("token_ids"))
        outcomes = parse_list(d.get("outcomes") or d.get("outcomeNames") or d.get("outcome_names"))

        if tokens and outcomes and len(tokens) == len(outcomes):
            found.append({
                "source": source,
                "slug": d.get("slug") or d.get("market_slug") or d.get("eventSlug") or "",
                "question": d.get("question") or d.get("title") or d.get("description") or "",
                "outcomes": outcomes,
                "tokens": tokens,
                "liquidity": d.get("liquidity") or d.get("liquidityNum") or "",
                "volume": d.get("volume") or d.get("volumeNum") or "",
            })
    return found

def extract_json_blobs_from_html(html):
    blobs = []

    # Look for raw clobTokenIds-bearing JSON fragments.
    for m in re.finditer(r'\{[^{}]{0,2000}clobTokenIds[^{}]{0,5000}\}', html):
        txt = m.group(0)
        try:
            blobs.append(json.loads(txt))
        except Exception:
            pass

    # Look for Next-style script JSON.
    for m in re.finditer(r'<script[^>]*>(.*?)</script>', html, re.S | re.I):
        txt = m.group(1).strip()
        if "clobTokenIds" not in txt and "tokenIds" not in txt:
            continue
        try:
            blobs.append(json.loads(txt))
        except Exception:
            # Keep searching; many scripts are not plain JSON.
            pass

    return blobs

all_found = []

for slug in SLUGS:
    print(f"\n=== {slug} ===")

    for template in BASES:
        url = template.format(slug=quote(slug))
        try:
            r = requests.get(url, timeout=30)
        except Exception as e:
            print(f"ERR {url}: {e}")
            continue

        print(f"{r.status_code} {url}")

        if r.status_code >= 400:
            continue

        ctype = r.headers.get("content-type", "")

        if "application/json" in ctype:
            try:
                payload = r.json()
            except Exception:
                continue
            all_found.extend(extract_markets_from_json(payload, url))
        else:
            html = r.text
            if "clobTokenIds" in html or "tokenIds" in html:
                for blob in extract_json_blobs_from_html(html):
                    all_found.extend(extract_markets_from_json(blob, url))

# De-duplicate
dedup = {}
for m in all_found:
    key = (m["slug"], m["question"], tuple(m["tokens"]))
    dedup[key] = m

markets = list(dedup.values())

print("\n\nFOUND MARKETS WITH TOKEN IDS")
print("=" * 120)

if not markets:
    print("No token IDs found from probed endpoints.")
else:
    for i, m in enumerate(markets, 1):
        print(f"\n{i}. {m['slug']}")
        print(f"   question: {m['question']}")
        print(f"   liquidity: {m['liquidity']} | volume: {m['volume']}")
        for outcome, token in zip(m["outcomes"], m["tokens"]):
            print(f"   {outcome}: {token}")

    out = Path("outputs/polymarket_training/today_sports_game_tokens.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(markets, indent=2), encoding="utf-8")
    print(f"\nSaved: {out}")
