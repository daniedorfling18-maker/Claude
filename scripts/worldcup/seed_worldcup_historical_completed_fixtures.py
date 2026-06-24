from __future__ import annotations

import csv
from pathlib import Path

OUT = Path("outputs/polymarket_training/worldcup_historical_completed_fixture_seed.csv")

rows = [
    ["2026-06-11", "Mexico", "South Africa", 2, 0],
    ["2026-06-11", "South Korea", "Czechia", 2, 1],
    ["2026-06-12", "Canada", "Bosnia and Herzegovina", 1, 1],
    ["2026-06-12", "United States", "Paraguay", 4, 1],
    ["2026-06-13", "Qatar", "Switzerland", 1, 1],
    ["2026-06-13", "Brazil", "Morocco", 1, 1],
    ["2026-06-13", "Haiti", "Scotland", 0, 1],
    ["2026-06-14", "Australia", "Turkey", 2, 0],
    ["2026-06-14", "Germany", "Curacao", 7, 1],
    ["2026-06-14", "Netherlands", "Japan", 2, 2],
    ["2026-06-14", "Ivory Coast", "Ecuador", 1, 0],
    ["2026-06-14", "Sweden", "Tunisia", 5, 1],
    ["2026-06-15", "Spain", "Cape Verde", 0, 0],
    ["2026-06-15", "Belgium", "Egypt", 1, 1],
    ["2026-06-15", "Saudi Arabia", "Uruguay", 1, 1],
    ["2026-06-15", "Iran", "New Zealand", 2, 2],
    ["2026-06-16", "France", "Senegal", 3, 1],
    ["2026-06-16", "Iraq", "Norway", 1, 4],
    ["2026-06-16", "Argentina", "Algeria", 3, 0],
    ["2026-06-17", "Austria", "Jordan", 3, 1],
    ["2026-06-17", "Portugal", "DR Congo", 1, 1],
    ["2026-06-17", "England", "Croatia", 4, 2],
    ["2026-06-17", "Ghana", "Panama", 1, 0],
    ["2026-06-17", "Uzbekistan", "Colombia", 1, 3],
    ["2026-06-18", "Czechia", "South Africa", 1, 1],
    ["2026-06-18", "Switzerland", "Bosnia and Herzegovina", 4, 1],
    ["2026-06-18", "Canada", "Qatar", 6, 0],
    ["2026-06-18", "Mexico", "South Korea", 1, 0],
    ["2026-06-19", "United States", "Australia", 2, 0],
    ["2026-06-19", "Scotland", "Morocco", 0, 1],
    ["2026-06-19", "Brazil", "Haiti", 3, 0],
    ["2026-06-19", "Turkey", "Paraguay", 0, 1],
    ["2026-06-20", "Netherlands", "Sweden", 5, 1],
    ["2026-06-20", "Germany", "Ivory Coast", 2, 1],
    ["2026-06-20", "Ecuador", "Curacao", 0, 0],
    ["2026-06-21", "Tunisia", "Japan", 0, 4],
    ["2026-06-21", "Spain", "Saudi Arabia", 4, 0],
    ["2026-06-21", "Belgium", "Iran", 0, 0],
    ["2026-06-21", "Uruguay", "Cape Verde", 2, 2],
    ["2026-06-21", "New Zealand", "Egypt", 1, 3],
    ["2026-06-22", "Argentina", "Austria", 2, 0],
    ["2026-06-22", "France", "Iraq", 3, 0],
    ["2026-06-22", "Norway", "Senegal", 3, 2],
    ["2026-06-22", "Jordan", "Algeria", 1, 2],
    ["2026-06-23", "Portugal", "Uzbekistan", 5, 0],
    ["2026-06-23", "England", "Ghana", 0, 0],
    ["2026-06-23", "Panama", "Croatia", 0, 1],
    ["2026-06-23", "Colombia", "DR Congo", 1, 0],
]

OUT.parent.mkdir(parents=True, exist_ok=True)

with OUT.open("w", encoding="utf-8", newline="") as f:
    writer = csv.writer(f)
    writer.writerow([
        "fixture_date",
        "team_a",
        "team_b",
        "team_a_goals",
        "team_b_goals",
        "final_score_status",
        "source",
    ])
    for r in rows:
        writer.writerow([*r, "complete", "historical_seed"])

print(f"wrote {len(rows)} completed fixtures to {OUT}")
