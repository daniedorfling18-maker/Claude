from __future__ import annotations

import csv
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("outputs")
KEYWORDS = re.compile(
    r"world|cup|fifa|soccer|football|brazil|scotland|mexico|korea|south-korea|south-africa|"
    r"canada|switzerland|qatar|morocco|haiti|england|ghana|portugal|colombia|germany|"
    r"ecuador|netherlands|japan|sweden|tunisia|spain|uruguay|france|norway|senegal|"
    r"argentina|croatia|paraguay|australia|turkey|usa|united-states",
    re.I,
)

def parse_dt(value: str):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None

def fnum(value: str) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0

now = datetime.now(timezone.utc)

files = sorted(
    ROOT.rglob("raw_market_snapshots.csv"),
    key=lambda p: p.stat().st_mtime,
    reverse=True,
)[:8]

print("Scanning files:")
for p in files:
    print(f" - {p}")

rows = []
for path in files:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            token = (r.get("token_id") or "").strip()
            outcome = (r.get("outcome") or "").strip()
            slug = (r.get("market_slug") or r.get("slug") or "").strip()
            question = (r.get("question") or "").strip()

            if not token:
                continue
            if outcome not in {"Yes", "No"}:
                continue
            if not (KEYWORDS.search(slug) or KEYWORDS.search(question)):
                continue

            close_raw = r.get("close_time") or r.get("end_time") or r.get("endDate") or ""
            close_dt = parse_dt(close_raw)
            if not close_dt or close_dt <= now:
                continue

            rows.append({
                "file": str(path),
                "market_slug": slug,
                "question": question,
                "outcome": outcome,
                "token_id": token,
                "best_bid": r.get("best_bid") or r.get("bestBid") or "",
                "best_ask": r.get("best_ask") or r.get("bestAsk") or "",
                "midpoint": r.get("midpoint") or "",
                "liquidity": fnum(r.get("liquidity") or r.get("liquidityNum") or r.get("liquidityClob")),
                "volume": fnum(r.get("volume") or r.get("volumeNum") or r.get("volumeClob")),
                "close_time": close_dt.isoformat().replace("+00:00", "Z"),
                "hours_to_close": round((close_dt - now).total_seconds() / 3600, 2),
            })

groups = defaultdict(list)
for r in rows:
    groups[r["market_slug"]].append(r)

ranked = []
for slug, group in groups.items():
    yes_rows = [r for r in group if r["outcome"] == "Yes"]
    no_rows = [r for r in group if r["outcome"] == "No"]
    if not yes_rows or not no_rows:
        continue

    yes = max(yes_rows, key=lambda r: r["liquidity"])
    no = max(no_rows, key=lambda r: r["liquidity"])
    soonest = min(group, key=lambda r: r["hours_to_close"])

    ranked.append({
        "market_slug": slug,
        "question": soonest["question"],
        "hours_to_close": soonest["hours_to_close"],
        "close_time": soonest["close_time"],
        "total_liquidity": round(sum(r["liquidity"] for r in group), 2),
        "total_volume": round(sum(r["volume"] for r in group), 2),
        "yes_token": yes["token_id"],
        "no_token": no["token_id"],
        "yes_midpoint": yes["midpoint"],
        "no_midpoint": no["midpoint"],
    })

ranked.sort(key=lambda r: (r["hours_to_close"], -r["total_liquidity"], -r["total_volume"]))

print("\nTOP CANDIDATES")
print("-" * 140)
for i, r in enumerate(ranked[:25], start=1):
    print(
        f"{i:>2}. {r['market_slug'][:55]:55} "
        f"hrs={r['hours_to_close']:>7} "
        f"liq={r['total_liquidity']:>10} "
        f"vol={r['total_volume']:>12} "
        f"yes_mid={str(r['yes_midpoint'])[:6]:>6} "
        f"no_mid={str(r['no_midpoint'])[:6]:>6}"
    )

if ranked:
    best = ranked[0]
    print("\nBEST PICK")
    print("-" * 80)
    for k, v in best.items():
        print(f"{k}: {v}")

    print("\nUse these tokens in polymarket_predictive_config.example.yaml:")
    print(f"YES token: {best['yes_token']}")
    print(f"NO token : {best['no_token']}")
else:
    print("\nNo suitable binary World Cup / soccer candidates found in the latest 8 raw snapshot files.")
