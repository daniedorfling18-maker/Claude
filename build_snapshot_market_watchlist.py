from __future__ import annotations

import csv
from pathlib import Path
from collections import defaultdict, Counter

FILES = [
    Path("outputs/polymarket/ml/raw_market_snapshots.csv"),
    Path("outputs/polymarket_wide/worldcup/ml/raw_market_snapshots.csv"),
    Path("outputs/polymarket_wide/sports/ml/raw_market_snapshots.csv"),
    Path("outputs/polymarket_wide/all/ml/raw_market_snapshots.csv"),
]

def read_csv(path):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def first_present(row, names):
    for n in names:
        if n in row and str(row.get(n) or "").strip():
            return str(row.get(n)).strip()
    return ""

market_map = {}

for path in FILES:
    if not path.exists():
        continue

    print(f"Scanning {path}")
    rows = read_csv(path)

    for r in rows:
        slug = first_present(r, [
            "market_slug", "slug", "market", "condition_id",
            "gamma_market_id", "question_id"
        ])
        token = first_present(r, [
            "token_id", "asset_id", "clob_token_id", "token"
        ])
        outcome = first_present(r, [
            "outcome", "outcome_name", "name"
        ])
        question = first_present(r, [
            "question", "title", "description"
        ])

        if not slug or not token:
            continue

        item = market_map.setdefault(slug, {
            "market_slug": slug,
            "question": question,
            "tokens": set(),
            "outcomes": set(),
            "files": set(),
            "rows": 0,
            "sample_columns": list(r.keys()),
        })

        item["tokens"].add(token)
        if outcome:
            item["outcomes"].add(outcome)
        item["files"].add(str(path))
        item["rows"] += 1

out = Path("outputs/polymarket_model_governance/snapshot_market_watchlist.csv")
out.parent.mkdir(parents=True, exist_ok=True)

records = []
for m in market_map.values():
    records.append({
        "market_slug": m["market_slug"],
        "question": m["question"],
        "token_count": len(m["tokens"]),
        "outcome_count": len(m["outcomes"]),
        "outcomes": "|".join(sorted(m["outcomes"])),
        "rows": m["rows"],
        "files": "|".join(sorted(m["files"])),
        "sample_tokens": "|".join(list(sorted(m["tokens"]))[:6]),
    })

records.sort(key=lambda x: -x["rows"])

with out.open("w", encoding="utf-8", newline="") as f:
    fieldnames = [
        "market_slug", "question", "token_count", "outcome_count",
        "outcomes", "rows", "files", "sample_tokens"
    ]
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(records)

print("\nSUMMARY")
print("-" * 80)
print(f"unique_snapshot_markets: {len(records)}")
print("token_count_distribution:", Counter(r["token_count"] for r in records))
print("outcome_count_distribution:", Counter(r["outcome_count"] for r in records))
print(f"wrote: {out}")

print("\nTop 30 markets by rows:")
for r in records[:30]:
    print(f"{r['rows']:>6} rows | tokens={r['token_count']:>2} | outcomes={r['outcomes'][:30]:30} | {r['market_slug'][:80]}")
