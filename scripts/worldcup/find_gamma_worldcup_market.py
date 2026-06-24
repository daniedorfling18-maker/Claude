from __future__ import annotations

import json
import re
import requests
from datetime import datetime, timezone

BASE = "https://gamma-api.polymarket.com/markets"

KEYWORDS = re.compile(
    r"world cup|fifa|soccer|football|brazil|scotland|mexico|korea|canada|switzerland|qatar|"
    r"morocco|haiti|england|ghana|portugal|colombia|germany|ecuador|netherlands|japan|"
    r"sweden|tunisia|spain|uruguay|france|norway|senegal|argentina|croatia|paraguay|"
    r"australia|turkey|united states|usa|world-cup",
    re.I,
)

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

now = datetime.now(timezone.utc)

markets = []

for offset in range(0, 5000, 100):
    params = {
        "limit": 100,
        "offset": offset,
        "active": "true",
        "closed": "false",
    }

    r = requests.get(BASE, params=params, timeout=30)

    if r.status_code == 422:
        print(f"Pagination stopped at offset={offset} due to 422 pagination limit.")
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

    if not KEYWORDS.search(text):
        continue

    outcomes = [str(x) for x in parse_list(m.get("outcomes"))]
    tokens = [str(x) for x in parse_list(m.get("clobTokenIds"))]

    if len(outcomes) != 2 or len(tokens) != 2:
        continue

    if set(outcomes) != {"Yes", "No"}:
        continue

    end = parse_dt(m.get("endDate") or m.get("end_date") or m.get("closedTime") or m.get("close_time"))
    if not end or end <= now:
        continue

    token_by_outcome = dict(zip(outcomes, tokens))

    candidates.append({
        "market_slug": slug,
        "question": question,
        "hours_to_close": round((end - now).total_seconds() / 3600, 2),
        "end_time": end.isoformat().replace("+00:00", "Z"),
        "liquidity": fnum(m.get("liquidity") or m.get("liquidityNum")),
        "volume": fnum(m.get("volume") or m.get("volumeNum")),
        "yes_token": token_by_outcome["Yes"],
        "no_token": token_by_outcome["No"],
    })

candidates.sort(key=lambda x: (x["hours_to_close"], -x["liquidity"], -x["volume"]))

print(f"\nFetched active markets: {len(markets)}")
print(f"Binary football/World Cup candidates: {len(candidates)}")

print("\nTOP GAMMA CANDIDATES")
print("-" * 140)

for i, c in enumerate(candidates[:30], 1):
    print(
        f"{i:>2}. {c['market_slug'][:65]:65} "
        f"hrs={c['hours_to_close']:>8} "
        f"liq={c['liquidity']:>12.2f} "
        f"vol={c['volume']:>12.2f}"
    )

if candidates:
    best = candidates[0]
    print("\nBEST PICK")
    print("-" * 80)
    for k, v in best.items():
        print(f"{k}: {v}")

    print("\nUse these tokens:")
    print(f"YES token: {best['yes_token']}")
    print(f"NO token : {best['no_token']}")
else:
    print("\nNo active binary Yes/No World Cup-style candidates found in scanned active Gamma markets.")
