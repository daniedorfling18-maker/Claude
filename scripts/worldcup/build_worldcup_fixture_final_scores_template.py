from __future__ import annotations

import csv
import json
from pathlib import Path

INP = Path("outputs/polymarket_model_governance/worldcup_fixture_moneyline_market_inventory.csv")

OUT = Path("outputs/polymarket_training/worldcup_fixture_final_scores_template.csv")
SUMMARY = Path("outputs/polymarket_model_governance/worldcup_fixture_final_scores_template_summary.json")

def read_csv(path):
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        return list(csv.DictReader(f))

rows = read_csv(INP)

fixtures = {}
for r in rows:
    slug = r.get("fixture_slug", "")
    if not slug:
        continue

    fixtures.setdefault(slug, {
        "fixture_slug": slug,
        "fixture_date": r.get("fixture_date", ""),
        "fixture_url": r.get("fixture_url", ""),
        "team_a": "",
        "team_b": "",
        "team_a_goals": "",
        "team_b_goals": "",
        "final_score_status": "pending",
        "source": "",
        "notes": "",
    })

    if r.get("market_index") == "0":
        fixtures[slug]["team_a"] = r.get("team_or_draw", "")
    elif r.get("market_index") == "2":
        fixtures[slug]["team_b"] = r.get("team_or_draw", "")

OUT.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.parent.mkdir(parents=True, exist_ok=True)

fieldnames = [
    "fixture_slug",
    "fixture_date",
    "fixture_url",
    "team_a",
    "team_b",
    "team_a_goals",
    "team_b_goals",
    "final_score_status",
    "source",
    "notes",
]

with OUT.open("w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(fixtures.values())

summary = {
    "fixtures": len(fixtures),
    "output": str(OUT),
    "next_step": "Copy this to worldcup_fixture_final_scores.csv and fill completed scores after matches finish.",
}

SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
