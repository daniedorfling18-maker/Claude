from __future__ import annotations

import csv
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from collections import Counter

OUT = Path("outputs/polymarket_model_governance/gamma_completed_game_market_search_2026_06_23.csv")
SUMMARY = Path("outputs/polymarket_model_governance/gamma_completed_game_market_search_2026_06_23_summary.json")

GAMMA = "https://gamma-api.polymarket.com/markets"

FIXTURES = {
    "portugal_uzbekistan": {
        "home": "Portugal",
        "away": "Uzbekistan",
        "queries": [
            "Portugal Uzbekistan",
            "Portugal vs Uzbekistan",
            "POR UZB",
        ],
        "final_score": "Portugal 5-0 Uzbekistan",
    },
    "england_ghana": {
        "home": "England",
        "away": "Ghana",
        "queries": [
            "England Ghana",
            "England vs Ghana",
            "ENG GHA",
        ],
        "final_score": "England 0-0 Ghana",
    },
    "panama_croatia": {
        "home": "Panama",
        "away": "Croatia",
        "queries": [
            "Panama Croatia",
            "Panama vs Croatia",
            "PAN CRO",
        ],
        "final_score": "Panama 0-1 Croatia",
    },
    "colombia_dr_congo": {
        "home": "Colombia",
        "away": "DR Congo",
        "queries": [
            "Colombia DR Congo",
            "Colombia Congo",
            "Colombia vs DR Congo",
            "COL COD",
        ],
        "final_score": "Colombia 1-0 DR Congo",
    },
}

def get_json(url: str):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "polymarket-predictive-engine/fixture-search"
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))

def as_text(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)

def classify(question: str, slug: str, outcomes: str) -> str:
    text = f"{question} {slug} {outcomes}".lower()

    if "win the 2026 fifa world cup" in text or "win-the-2026-fifa-world-cup" in text:
        return "tournament_outright"

    if "group" in text and ("winner" in text or "win" in text):
        return "group_market"

    if any(x in text for x in [
        " vs ",
        "-vs-",
        " v ",
        "moneyline",
        "match winner",
        "winner",
        "draw",
        "correct score",
        "total goals",
        "over",
        "under",
        "both teams",
        "btts",
        "handicap",
        "spread",
        "clean sheet",
    ]):
        return "specific_game_candidate"

    return "unclear_candidate"

def contains_both_sides(text: str, home: str, away: str) -> bool:
    t = text.lower()
    h = home.lower()
    a = away.lower()

    if away.lower() == "dr congo":
        away_hits = ["dr congo", "congo", "cod", "drc"]
    else:
        away_hits = [a]

    return h in t and any(x in t for x in away_hits)

records = []
seen = set()

for fixture_id, fx in FIXTURES.items():
    for query in fx["queries"]:
        params = {
            "search": query,
            "limit": "100",
        }
        url = f"{GAMMA}?{urllib.parse.urlencode(params)}"

        try:
            payload = get_json(url)
        except Exception as e:
            records.append({
                "fixture": fixture_id,
                "query": query,
                "market_id": "",
                "condition_id": "",
                "slug": "",
                "question": "",
                "market_type": "api_error",
                "active": "",
                "closed": "",
                "archived": "",
                "accepting_orders": "",
                "end_date": "",
                "closed_time": "",
                "outcomes": "",
                "outcome_prices": "",
                "token_ids": "",
                "final_score": fx["final_score"],
                "error": str(e),
            })
            continue

        if isinstance(payload, dict):
            markets = payload.get("data") or payload.get("markets") or []
        elif isinstance(payload, list):
            markets = payload
        else:
            markets = []

        for m in markets:
            slug = as_text(m.get("slug"))
            question = as_text(m.get("question") or m.get("title"))
            outcomes = as_text(m.get("outcomes"))
            outcome_prices = as_text(m.get("outcomePrices"))
            token_ids = as_text(m.get("clobTokenIds") or m.get("tokenIds"))

            combined = f"{slug} {question} {outcomes}"

            # Keep only markets that genuinely mention both teams.
            if not contains_both_sides(combined, fx["home"], fx["away"]):
                continue

            key = (fixture_id, m.get("id"), slug)
            if key in seen:
                continue
            seen.add(key)

            records.append({
                "fixture": fixture_id,
                "query": query,
                "market_id": as_text(m.get("id")),
                "condition_id": as_text(m.get("conditionId")),
                "slug": slug,
                "question": question,
                "market_type": classify(question, slug, outcomes),
                "active": as_text(m.get("active")),
                "closed": as_text(m.get("closed")),
                "archived": as_text(m.get("archived")),
                "accepting_orders": as_text(m.get("acceptingOrders")),
                "end_date": as_text(m.get("endDate") or m.get("endDateIso")),
                "closed_time": as_text(m.get("closedTime")),
                "outcomes": outcomes,
                "outcome_prices": outcome_prices,
                "token_ids": token_ids,
                "final_score": fx["final_score"],
                "error": "",
            })

        time.sleep(0.2)

OUT.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.parent.mkdir(parents=True, exist_ok=True)

fieldnames = [
    "fixture",
    "query",
    "market_id",
    "condition_id",
    "slug",
    "question",
    "market_type",
    "active",
    "closed",
    "archived",
    "accepting_orders",
    "end_date",
    "closed_time",
    "outcomes",
    "outcome_prices",
    "token_ids",
    "final_score",
    "error",
]

with OUT.open("w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(records)

summary = {
    "fixtures_searched": list(FIXTURES),
    "records": len(records),
    "fixture_counts": dict(Counter(r["fixture"] for r in records)),
    "market_type_counts": dict(Counter(r["market_type"] for r in records)),
    "specific_game_candidate_count": sum(1 for r in records if r["market_type"] == "specific_game_candidate"),
    "output": str(OUT),
    "next_step": "If specific_game_candidate_count > 0, inspect candidates and build completed-game labels.",
}

SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
