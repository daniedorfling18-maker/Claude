from __future__ import annotations

import csv
import json
import time
import requests
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import quote

FILES = [
    Path("outputs/polymarket/ml/raw_market_snapshots.csv"),
    Path("outputs/polymarket_wide/worldcup/ml/raw_market_snapshots.csv"),
    Path("outputs/polymarket_wide/sports/ml/raw_market_snapshots.csv"),
    Path("outputs/polymarket_wide/all/ml/raw_market_snapshots.csv"),
]

OUT = Path("outputs/polymarket_model_governance/snapshot_market_gamma_status.csv")
BASE = "https://gamma-api.polymarket.com/markets"

def read_csv(path):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def first(row, names):
    for n in names:
        v = row.get(n)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""

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

# Build unique snapshot-market universe
markets = {}

for path in FILES:
    if not path.exists():
        continue

    print(f"Reading {path}")
    for r in read_csv(path):
        slug = first(r, ["market_slug", "slug", "market"])
        token = first(r, ["token_id", "asset_id", "clob_token_id", "token"])
        outcome = first(r, ["outcome", "outcome_name", "name"])
        question = first(r, ["question", "title", "description"])

        if not slug or not token:
            continue

        item = markets.setdefault(slug, {
            "market_slug": slug,
            "question_snapshot": question,
            "snapshot_tokens": set(),
            "snapshot_outcomes": set(),
            "snapshot_rows": 0,
            "files": set(),
        })

        item["snapshot_tokens"].add(token)
        if outcome:
            item["snapshot_outcomes"].add(outcome)
        item["snapshot_rows"] += 1
        item["files"].add(str(path))

print(f"\nUnique snapshot markets: {len(markets)}")

def fetch_by_slug(slug):
    # Primary path: markets?slug=
    for params in [
        {"slug": slug},
        {"market_slug": slug},
    ]:
        try:
            r = requests.get(BASE, params=params, timeout=30)
            if r.status_code == 422:
                continue
            r.raise_for_status()
            payload = r.json()
            batch = payload if isinstance(payload, list) else payload.get("data", [])
            if batch:
                return batch[0], f"{BASE}?{params}"
        except Exception:
            pass

    return None, ""

records = []

for i, (slug, snap) in enumerate(markets.items(), 1):
    if i % 25 == 0:
        print(f"Checked {i}/{len(markets)}")

    m, source = fetch_by_slug(slug)

    if not m:
        records.append({
            "market_slug": slug,
            "question_snapshot": snap["question_snapshot"],
            "question_gamma": "",
            "gamma_found": False,
            "closed": "",
            "active": "",
            "archived": "",
            "resolved_by": "",
            "end_time": "",
            "past_end": "",
            "snapshot_token_count": len(snap["snapshot_tokens"]),
            "gamma_token_count": "",
            "outcomes": "|".join(sorted(snap["snapshot_outcomes"])),
            "gamma_outcomes": "",
            "snapshot_rows": snap["snapshot_rows"],
            "liquidity": "",
            "volume": "",
            "source": source,
        })
        continue

    outcomes = [str(x) for x in parse_list(m.get("outcomes"))]
    tokens = [str(x) for x in parse_list(m.get("clobTokenIds"))]

    end = parse_dt(m.get("endDate") or m.get("end_date") or m.get("close_time") or m.get("closedTime"))
    past_end = bool(end and end <= now)

    records.append({
        "market_slug": slug,
        "question_snapshot": snap["question_snapshot"],
        "question_gamma": m.get("question") or m.get("title") or "",
        "gamma_found": True,
        "closed": m.get("closed"),
        "active": m.get("active"),
        "archived": m.get("archived"),
        "resolved_by": m.get("resolvedBy") or m.get("resolved_by") or "",
        "end_time": end.isoformat().replace("+00:00", "Z") if end else "",
        "past_end": past_end,
        "snapshot_token_count": len(snap["snapshot_tokens"]),
        "gamma_token_count": len(tokens),
        "outcomes": "|".join(sorted(snap["snapshot_outcomes"])),
        "gamma_outcomes": "|".join(outcomes),
        "snapshot_rows": snap["snapshot_rows"],
        "liquidity": fnum(m.get("liquidity") or m.get("liquidityNum")),
        "volume": fnum(m.get("volume") or m.get("volumeNum")),
        "source": source,
    })

    time.sleep(0.03)

OUT.parent.mkdir(parents=True, exist_ok=True)

fieldnames = [
    "market_slug",
    "question_snapshot",
    "question_gamma",
    "gamma_found",
    "closed",
    "active",
    "archived",
    "resolved_by",
    "end_time",
    "past_end",
    "snapshot_token_count",
    "gamma_token_count",
    "outcomes",
    "gamma_outcomes",
    "snapshot_rows",
    "liquidity",
    "volume",
    "source",
]

with OUT.open("w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(records)

print("\nSUMMARY")
print("-" * 80)
print(f"records: {len(records)}")
print("gamma_found:", Counter(str(r["gamma_found"]) for r in records))
print("closed:", Counter(str(r["closed"]) for r in records))
print("active:", Counter(str(r["active"]) for r in records))
print("past_end:", Counter(str(r["past_end"]) for r in records))
print(f"wrote: {OUT}")

print("\nClosed or past-end examples:")
for r in records:
    if str(r["closed"]).lower() == "true" or str(r["past_end"]).lower() == "true":
        print(f"{r['market_slug']} | closed={r['closed']} | active={r['active']} | end={r['end_time']} | rows={r['snapshot_rows']}")
