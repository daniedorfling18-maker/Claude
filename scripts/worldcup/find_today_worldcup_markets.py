from __future__ import annotations

import json
import re
import requests
from datetime import datetime, timezone

BASE = "https://gamma-api.polymarket.com/markets"

TODAY_TERMS = [
    "switzerland", "canada",
    "bosnia", "bosnia and herzegovina", "qatar",
    "scotland", "brazil",
    "morocco", "haiti",
    "czechia", "mexico",
    "south africa", "south korea", "korea",
]

MATCH_PAIRS = [
    ("switzerland", "canada"),
    ("bosnia", "qatar"),
    ("scotland", "brazil"),
    ("morocco", "haiti"),
    ("czechia", "mexico"),
    ("south africa", "south korea"),
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

def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None

def fnum(value):
    try:
        return float(value or 0)
    except Exception:
        return 0.0

def text_hit(text: str):
    t = text.lower()
    term_hits = [x for x in TODAY_TERMS if x in t]
    pair_hits = []
    for a, b in MATCH_PAIRS:
        if a in t and b in t:
            pair_hits.append(f"{a} vs {b}")
    return term_hits, pair_hits

now = datetime.now(timezone.utc)
markets = []

for offset in range(0, 5000, 100):
    r = requests.get(
        BASE,
        params={
            "limit": 100,
            "offset": offset,
            "active": "true",
            "closed": "false",
        },
        timeout=30,
    )

    if r.status_code == 422:
        print(f"Pagination stopped at offset={offset}.")
        break

    r.raise_for_status()
    payload = r.json()
    batch = payload if isinstance(payload, list) else payload.get("data", [])

    if not batch:
        break

    markets.extend(batch)

candidates = []

for m in markets:
    slug = str(m.get("slug") or "")
    question = str(m.get("question") or m.get("title") or "")
    text = f"{slug} {question}"

    term_hits, pair_hits = text_hit(text)

    if not term_hits:
        continue

    outcomes = [str(x) for x in parse_list(m.get("outcomes"))]
    tokens = [str(x) for x in parse_list(m.get("clobTokenIds"))]

    if not outcomes or not tokens or len(outcomes) != len(tokens):
        continue

    end = parse_dt(m.get("endDate") or m.get("end_date") or m.get("closedTime") or m.get("close_time"))
    if not end or end <= now:
        continue

    is_binary_yes_no = len(outcomes) == 2 and set(outcomes) == {"Yes", "No"}

    candidates.append({
        "market_slug": slug,
        "question": question,
        "hours_to_close": round((end - now).total_seconds() / 3600, 2),
        "end_time": end.isoformat().replace("+00:00", "Z"),
        "liquidity": fnum(m.get("liquidity") or m.get("liquidityNum")),
        "volume": fnum(m.get("volume") or m.get("volumeNum")),
        "outcomes": outcomes,
        "tokens": tokens,
        "is_binary_yes_no": is_binary_yes_no,
        "term_hits": term_hits,
        "pair_hits": pair_hits,
    })

candidates.sort(
    key=lambda x: (
        not x["is_binary_yes_no"],
        -len(x["pair_hits"]),
        x["hours_to_close"],
        -x["liquidity"],
        -x["volume"],
    )
)

print(f"\nFetched active markets: {len(markets)}")
print(f"Today-team candidates: {len(candidates)}")

print("\nTOP TODAY-GAME CANDIDATES")
print("-" * 160)

for i, c in enumerate(candidates[:40], 1):
    binary = "YESNO" if c["is_binary_yes_no"] else f"{len(c['outcomes'])}OUT"
    pair = ",".join(c["pair_hits"]) if c["pair_hits"] else "-"
    print(
        f"{i:>2}. {binary:5} "
        f"hrs={c['hours_to_close']:>7} "
        f"liq={c['liquidity']:>11.2f} "
        f"vol={c['volume']:>11.2f} "
        f"pair={pair[:30]:30} "
        f"{c['market_slug'][:65]}"
    )

print("\nBINARY YES/NO TOKEN PAIRS")
print("-" * 120)

binary = [c for c in candidates if c["is_binary_yes_no"]]

for i, c in enumerate(binary[:20], 1):
    token_by_outcome = dict(zip(c["outcomes"], c["tokens"]))
    print(f"\n{i}. {c['market_slug']}")
    print(f"   question: {c['question']}")
    print(f"   end_time: {c['end_time']} | hours_to_close: {c['hours_to_close']}")
    print(f"   liquidity: {c['liquidity']} | volume: {c['volume']}")
    print(f"   YES: {token_by_outcome.get('Yes')}")
    print(f"   NO : {token_by_outcome.get('No')}")

if not binary:
    print("\nNo binary Yes/No markets found for today's team terms. The match markets may be 3-outcome markets.")
