from __future__ import annotations

import csv
import json
import time
from pathlib import Path

import requests

SEED = Path("outputs/polymarket_training/worldcup_historical_completed_fixture_seed.csv")

DISCOVERY_OUT = Path("outputs/polymarket_model_governance/worldcup_historical_fixture_page_discovery.csv")
FOUND_OUT = Path("outputs/polymarket_model_governance/worldcup_historical_discovered_sports_page_fixtures.csv")

MAIN_FIXTURES = Path("outputs/polymarket_model_governance/worldcup_sports_page_fixtures.csv")
BACKUP_FIXTURES = Path("outputs/polymarket_model_governance/worldcup_sports_page_fixtures.before_historical_backfill.csv")

SUMMARY = Path("outputs/polymarket_model_governance/worldcup_historical_fixture_discovery_summary.json")

BASE = "https://polymarket.com/sports/world-cup/games"

ALIASES = {
    "Mexico": ["mex"],
    "South Africa": ["rsa"],
    "South Korea": ["kr", "kor"],
    "Czechia": ["cze"],
    "Canada": ["can"],
    "Bosnia and Herzegovina": ["bih"],
    "Qatar": ["qat"],
    "Switzerland": ["che", "sui"],
    "Brazil": ["bra"],
    "Morocco": ["mar"],
    "Haiti": ["hai", "hti"],
    "Scotland": ["sco"],
    "United States": ["usa"],
    "Paraguay": ["par"],
    "Australia": ["aus"],
    "Turkey": ["tur"],
    "Türkiye": ["tur"],
    "Germany": ["ger"],
    "Curacao": ["kor", "cuw"],
    "Curaçao": ["kor", "cuw"],
    "Ivory Coast": ["civ"],
    "Côte d'Ivoire": ["civ"],
    "Ecuador": ["ecu"],
    "Netherlands": ["nld", "ned"],
    "Japan": ["jpn"],
    "Sweden": ["swe"],
    "Tunisia": ["tun"],
    "Spain": ["esp"],
    "Cape Verde": ["cvi", "cpv"],
    "Cabo Verde": ["cvi", "cpv"],
    "Saudi Arabia": ["ksa"],
    "Uruguay": ["ury", "uru"],
    "Belgium": ["bel"],
    "Egypt": ["egy"],
    "Iran": ["irn", "iri"],
    "IR Iran": ["irn", "iri"],
    "New Zealand": ["nzl"],
    "France": ["fra"],
    "Senegal": ["sen"],
    "Iraq": ["irq"],
    "Norway": ["nor"],
    "Argentina": ["arg"],
    "Algeria": ["dza", "alg"],
    "Austria": ["aut"],
    "Jordan": ["jor"],
    "Portugal": ["por"],
    "DR Congo": ["cod", "drc"],
    "Uzbekistan": ["uzb"],
    "Colombia": ["col"],
    "England": ["eng"],
    "Croatia": ["cro"],
    "Ghana": ["gha"],
    "Panama": ["pan"],
}

def read_csv(path):
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        return list(csv.DictReader(f))

def write_csv(path, rows, fieldnames):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

def candidate_slugs(team_a, team_b, fixture_date):
    a_codes = ALIASES.get(team_a, [])
    b_codes = ALIASES.get(team_b, [])
    out = []

    for a in a_codes:
        for b in b_codes:
            out.append(f"fifwc-{a}-{b}-{fixture_date}")
            out.append(f"fifwc-{b}-{a}-{fixture_date}")

    # Deduplicate while preserving order.
    seen = set()
    final = []
    for s in out:
        if s not in seen:
            final.append(s)
            seen.add(s)
    return final

def looks_like_fixture_market(html):
    tokens = [
        "clobTokenIds",
        "conditionId",
        "outcomePrices",
        "Will ",
        "end in a draw",
    ]
    return any(t in html for t in tokens)

seed_rows = read_csv(SEED)
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 PolymarketHistoricalBackfill/1.0",
})

discovery = []
found = []

for row in seed_rows:
    fixture_date = row["fixture_date"]
    team_a = row["team_a"]
    team_b = row["team_b"]

    matched = False
    matched_slug = ""
    matched_url = ""
    matched_status = ""

    for slug in candidate_slugs(team_a, team_b, fixture_date):
        url = f"{BASE}/{slug}"

        try:
            resp = session.get(url, timeout=20)
            status = resp.status_code
            html = resp.text or ""
            valid = status == 200 and looks_like_fixture_market(html)
        except Exception as exc:
            status = "error"
            valid = False
            html = ""
            matched_status = repr(exc)

        discovery.append({
            "fixture_date": fixture_date,
            "team_a": team_a,
            "team_b": team_b,
            "candidate_slug": slug,
            "candidate_url": url,
            "http_status": status,
            "valid_fixture_page": valid,
        })

        if valid:
            matched = True
            matched_slug = slug
            matched_url = url
            matched_status = "found"
            found.append({
                "fixture_slug": matched_slug,
                "fixture_date": fixture_date,
                "fixture": f"{team_a} vs {team_b}",
                "team_a": team_a,
                "team_b": team_b,
                "team_a_goals": row["team_a_goals"],
                "team_b_goals": row["team_b_goals"],
                "final_score_status": row["final_score_status"],
                "source": row["source"],
                "fixture_url": matched_url,
                "href": matched_url,
                "url": matched_url,
                "discovery_source": "historical_slug_probe",
            })
            break

        time.sleep(0.10)

    if not matched:
        found.append({
            "fixture_slug": "",
            "fixture_date": fixture_date,
            "fixture": f"{team_a} vs {team_b}",
            "team_a": team_a,
            "team_b": team_b,
            "team_a_goals": row["team_a_goals"],
            "team_b_goals": row["team_b_goals"],
            "final_score_status": row["final_score_status"],
            "source": row["source"],
            "fixture_url": "",
            "href": "",
            "url": "",
            "discovery_source": "not_found",
        })

discovery_fields = [
    "fixture_date",
    "team_a",
    "team_b",
    "candidate_slug",
    "candidate_url",
    "http_status",
    "valid_fixture_page",
]

found_fields = [
    "fixture_slug",
    "fixture_date",
    "fixture",
    "team_a",
    "team_b",
    "team_a_goals",
    "team_b_goals",
    "final_score_status",
    "source",
    "fixture_url",
    "href",
    "url",
    "discovery_source",
]

write_csv(DISCOVERY_OUT, discovery, discovery_fields)
write_csv(FOUND_OUT, found, found_fields)

# Merge discovered historical fixtures into the main fixture input used by the extractor.
existing = read_csv(MAIN_FIXTURES)
if MAIN_FIXTURES.exists() and not BACKUP_FIXTURES.exists():
    BACKUP_FIXTURES.write_text(MAIN_FIXTURES.read_text(encoding="utf-8-sig"), encoding="utf-8")

merged_by_slug = {}

for r in existing:
    slug = r.get("fixture_slug") or r.get("slug") or ""
    if slug:
        merged_by_slug[slug] = r

for r in found:
    if r.get("fixture_slug"):
        merged_by_slug[r["fixture_slug"]] = r

merged = list(merged_by_slug.values())

# Use broad field union so the existing extractor has all likely columns.
field_union = []
for r in merged:
    for k in r.keys():
        if k not in field_union:
            field_union.append(k)

write_csv(MAIN_FIXTURES, merged, field_union)

summary = {
    "seed_fixtures": len(seed_rows),
    "found_fixture_pages": len([r for r in found if r.get("fixture_slug")]),
    "not_found_fixtures": len([r for r in found if not r.get("fixture_slug")]),
    "discovery_output": str(DISCOVERY_OUT),
    "found_output": str(FOUND_OUT),
    "merged_fixture_input": str(MAIN_FIXTURES),
    "backup_fixture_input": str(BACKUP_FIXTURES),
}

SUMMARY.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
