from __future__ import annotations

import csv
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter, defaultdict

ROOT = Path("outputs")

def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None

def read_csv(path):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

now = datetime.now(timezone.utc)

files = [
    Path("outputs/polymarket/ml/raw_market_snapshots.csv"),
    Path("outputs/polymarket_wide/worldcup/ml/raw_market_snapshots.csv"),
    Path("outputs/polymarket_wide/sports/ml/raw_market_snapshots.csv"),
    Path("outputs/polymarket_wide/all/ml/raw_market_snapshots.csv"),
]

market_summary = {}

for path in files:
    if not path.exists():
        continue

    print(f"Scanning {path}")
    rows = read_csv(path)

    for r in rows:
        slug = (r.get("market_slug") or r.get("slug") or r.get("market") or "").strip()
        token = (r.get("token_id") or r.get("asset_id") or "").strip()
        outcome = (r.get("outcome") or "").strip()

        if not slug or not token:
            continue

        end = parse_dt(
            r.get("close_time")
            or r.get("end_time")
            or r.get("endDate")
            or r.get("end_date")
        )

        closed_raw = str(r.get("closed") or r.get("is_closed") or "").lower()
        active_raw = str(r.get("active") or r.get("is_active") or "").lower()

        is_closed_flag = closed_raw in {"true", "1", "yes"}
        is_past_end = bool(end and end <= now)

        key = slug
        item = market_summary.setdefault(key, {
            "market_slug": slug,
            "question": r.get("question") or "",
            "tokens": set(),
            "outcomes": set(),
            "files": set(),
            "end_time": "",
            "is_closed_flag": False,
            "is_past_end": False,
            "rows": 0,
        })

        item["tokens"].add(token)
        item["outcomes"].add(outcome)
        item["files"].add(str(path))
        item["rows"] += 1
        item["is_closed_flag"] = item["is_closed_flag"] or is_closed_flag
        item["is_past_end"] = item["is_past_end"] or is_past_end

        if end and not item["end_time"]:
            item["end_time"] = end.isoformat().replace("+00:00", "Z")

candidates = []

for m in market_summary.values():
    if m["is_closed_flag"] or m["is_past_end"]:
        candidates.append({
            "market_slug": m["market_slug"],
            "question": m["question"],
            "token_count": len(m["tokens"]),
            "outcome_count": len(m["outcomes"]),
            "outcomes": "|".join(sorted(x for x in m["outcomes"] if x)),
            "end_time": m["end_time"],
            "rows": m["rows"],
            "is_closed_flag": m["is_closed_flag"],
            "is_past_end": m["is_past_end"],
            "files": "|".join(sorted(m["files"])),
        })

out = Path("outputs/polymarket_model_governance/snapshot_markets_needing_resolution.csv")
out.parent.mkdir(parents=True, exist_ok=True)

with out.open("w", encoding="utf-8", newline="") as f:
    fieldnames = [
        "market_slug", "question", "token_count", "outcome_count", "outcomes",
        "end_time", "rows", "is_closed_flag", "is_past_end", "files"
    ]
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(candidates)

print("\nSUMMARY")
print("-" * 80)
print(f"unique_snapshot_markets: {len(market_summary)}")
print(f"closed_or_past_end_candidates: {len(candidates)}")
print(f"wrote: {out}")

print("\nTop candidates:")
for c in sorted(candidates, key=lambda x: -x["rows"])[:30]:
    print(f"{c['rows']:>6} rows | tokens={c['token_count']:>2} | outcomes={c['outcomes'][:30]:30} | {c['market_slug'][:80]}")
