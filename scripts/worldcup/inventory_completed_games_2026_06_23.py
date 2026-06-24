from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from collections import Counter, defaultdict

SNAPSHOT_FILES = [
    Path("outputs/polymarket/ml/raw_market_snapshots.csv"),
    Path("outputs/polymarket_wide/worldcup/ml/raw_market_snapshots.csv"),
    Path("outputs/polymarket_wide/sports/ml/raw_market_snapshots.csv"),
    Path("outputs/polymarket_wide/all/ml/raw_market_snapshots.csv"),
]

OUT = Path("outputs/polymarket_model_governance/completed_game_market_inventory_2026_06_23.csv")
SUMMARY = Path("outputs/polymarket_model_governance/completed_game_market_inventory_2026_06_23_summary.json")

FIXTURES = {
    "portugal_uzbekistan": {
        "home": "Portugal",
        "away": "Uzbekistan",
        "aliases_home": ["portugal", "por"],
        "aliases_away": ["uzbekistan", "uzb"],
        "final_score": "Portugal 5-0 Uzbekistan",
    },
    "england_ghana": {
        "home": "England",
        "away": "Ghana",
        "aliases_home": ["england", "eng"],
        "aliases_away": ["ghana", "gha"],
        "final_score": "England 0-0 Ghana",
    },
    "panama_croatia": {
        "home": "Panama",
        "away": "Croatia",
        "aliases_home": ["panama", "pan"],
        "aliases_away": ["croatia", "cro"],
        "final_score": "Panama 0-1 Croatia",
    },
    "colombia_dr_congo": {
        "home": "Colombia",
        "away": "DR Congo",
        "aliases_home": ["colombia", "col"],
        "aliases_away": ["dr congo", "congo", "cod", "drc"],
        "final_score": "Colombia 1-0 DR Congo",
    },
}

def read_rows(path: Path):
    if not path.exists() or path.stat().st_size == 0:
        return
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        yield from csv.DictReader(f)

def first(row, names):
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""

def normalise(text: str) -> str:
    text = text.lower()
    text = text.replace("_", "-")
    text = re.sub(r"\s+", " ", text)
    return text

def has_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)

def matched_fixtures(text: str) -> list[str]:
    hits = []
    for fixture_id, fx in FIXTURES.items():
        if has_any(text, fx["aliases_home"]) and has_any(text, fx["aliases_away"]):
            hits.append(fixture_id)
    return hits

def classify_market(slug: str, question: str, outcomes: set[str]) -> str:
    text = normalise(f"{slug} {question} {' '.join(sorted(outcomes))}")

    if "win-the-2026-fifa-world-cup" in text:
        return "tournament_outright"

    if "win the 2026 fifa world cup" in text:
        return "tournament_outright"

    if "win group" in text or "group winner" in text or "to win group" in text:
        return "group_market"

    game_words = [
        "vs",
        " v ",
        "moneyline",
        "h2h",
        "head-to-head",
        "match winner",
        "winner",
        "draw",
        "correct score",
        "total goals",
        "over",
        "under",
        "both teams",
        "btts",
        "spread",
        "handicap",
        "asian handicap",
        "first half",
        "second half",
        "clean sheet",
    ]

    if any(w in text for w in game_words):
        return "specific_game_candidate"

    return "fixture_mentioned_unclear"

markets = {}

for path in SNAPSHOT_FILES:
    if not path.exists():
        continue

    for row in read_rows(path) or []:
        slug = first(row, ["market_slug", "slug", "market"])
        question = first(row, ["question", "title", "description"])
        token = first(row, ["token_id", "asset_id", "clob_token_id", "token"])
        outcome = first(row, ["outcome", "outcome_name", "name"])
        timestamp = first(row, ["snapshot_timestamp", "timestamp", "collected_at_utc", "source_timestamp", "updated_at", "created_at"])

        searchable = normalise(f"{slug} {question} {outcome}")
        fixtures = matched_fixtures(searchable)

        if not fixtures:
            continue

        key = slug or question
        item = markets.setdefault(key, {
            "fixtures": set(),
            "market_slug": slug,
            "question": question,
            "tokens": set(),
            "outcomes": set(),
            "rows": 0,
            "files": set(),
            "first_timestamp": timestamp,
            "last_timestamp": timestamp,
        })

        item["fixtures"].update(fixtures)
        if token:
            item["tokens"].add(token)
        if outcome:
            item["outcomes"].add(outcome)
        item["rows"] += 1
        item["files"].add(str(path))

        if timestamp:
            if not item["first_timestamp"] or timestamp < item["first_timestamp"]:
                item["first_timestamp"] = timestamp
            if not item["last_timestamp"] or timestamp > item["last_timestamp"]:
                item["last_timestamp"] = timestamp

records = []

for item in markets.values():
    market_type = classify_market(item["market_slug"], item["question"], item["outcomes"])

    records.append({
        "fixtures": "|".join(sorted(item["fixtures"])),
        "market_type": market_type,
        "market_slug": item["market_slug"],
        "question": item["question"],
        "token_count": len(item["tokens"]),
        "outcomes": "|".join(sorted(item["outcomes"])),
        "snapshot_rows": item["rows"],
        "first_timestamp": item["first_timestamp"],
        "last_timestamp": item["last_timestamp"],
        "files": "|".join(sorted(item["files"])),
    })

records.sort(key=lambda r: (r["fixtures"], r["market_type"], -int(r["snapshot_rows"])))

OUT.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.parent.mkdir(parents=True, exist_ok=True)

with OUT.open("w", encoding="utf-8", newline="") as f:
    fieldnames = [
        "fixtures",
        "market_type",
        "market_slug",
        "question",
        "token_count",
        "outcomes",
        "snapshot_rows",
        "first_timestamp",
        "last_timestamp",
        "files",
    ]
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(records)

summary = {
    "snapshot_files_checked": [str(p) for p in SNAPSHOT_FILES],
    "markets_found": len(records),
    "fixture_counts": dict(Counter(r["fixtures"] for r in records)),
    "market_type_counts": dict(Counter(r["market_type"] for r in records)),
    "specific_game_candidate_count": sum(1 for r in records if r["market_type"] == "specific_game_candidate"),
    "tournament_outright_count": sum(1 for r in records if r["market_type"] == "tournament_outright"),
    "output": str(OUT),
    "next_step": "If specific_game_candidate_count > 0, build completed-game labels. If 0, build dedicated game-market collector.",
}

SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

print(json.dumps(summary, indent=2, sort_keys=True))
