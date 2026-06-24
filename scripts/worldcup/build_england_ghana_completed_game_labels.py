from __future__ import annotations

import csv
import json
from pathlib import Path

OUT = Path("outputs/polymarket_training/completed_game_labels_2026_06_23.csv")
SUMMARY = Path("outputs/polymarket_model_governance/completed_game_labels_2026_06_23_summary.json")

fixture_slug = "fifwc-eng-gha-2026-06-23"
fixture = "england_ghana"
final_score = "England 0-0 Ghana"
home_team = "England"
away_team = "Ghana"
home_goals = 0
away_goals = 0

markets = [
    {
        "condition_id": "0xff1ec56e0e47033727e33deab740896fbf1c6bb8a2b0068efdba3d900c7ba7a2",
        "market_question": "Will England win on 2026-06-23?",
        "market_type": "home_win_yes_no",
        "outcomes": ["Yes", "No"],
        "outcome_prices": [0.0, 1.0],
        "token_ids": [
            "63611572432092712507452794046269856873324193737948513078896856667745770559698",
            "81750077792759682980090397689070964226412046195389028523769479713426985687572",
        ],
        "targets": [0, 1],
        "resolution_rule": "Yes resolves to 1 if England wins in regular time; otherwise No resolves to 1.",
    },
    {
        "condition_id": "0x17d821a9197f8de3acbb8a0140e70cea9461e95871c58ae7aa9c608d53ad00d0",
        "market_question": "Will England vs. Ghana end in a draw?",
        "market_type": "draw_yes_no",
        "outcomes": ["Yes", "No"],
        "outcome_prices": [1.0, 0.0],
        "token_ids": [
            "113575947344702991323223447752604645066262694843576298874524902811228579544185",
            "100271816376241251014968205943425335239986161152505983067018696271142538695180",
        ],
        "targets": [1, 0],
        "resolution_rule": "Yes resolves to 1 if the match is drawn in regular time; otherwise No resolves to 1.",
    },
    {
        "condition_id": "0x66b7ad48448f8846395b8df8edb5f20f99bed8e79928b1b9d2cf3a19d70dbdf8",
        "market_question": "Will Ghana win on 2026-06-23?",
        "market_type": "away_win_yes_no",
        "outcomes": ["Yes", "No"],
        "outcome_prices": [0.0, 1.0],
        "token_ids": [
            "111239697803361919523803068759725643725914563280609261384832780529311817077235",
            "55378084779279061925670956253014301111841794660392042624001683483413192852659",
        ],
        "targets": [0, 1],
        "resolution_rule": "Yes resolves to 1 if Ghana wins in regular time; otherwise No resolves to 1.",
    },
]

rows = []
mismatches = []

for market in markets:
    for outcome, price, token_id, target in zip(
        market["outcomes"],
        market["outcome_prices"],
        market["token_ids"],
        market["targets"],
    ):
        if float(price) != float(target):
            mismatches.append({
                "condition_id": market["condition_id"],
                "market_question": market["market_question"],
                "outcome": outcome,
                "outcome_price": price,
                "target": target,
            })

        rows.append({
            "label_source": "completed_fixture_final_score",
            "fixture": fixture,
            "fixture_slug": fixture_slug,
            "home_team": home_team,
            "away_team": away_team,
            "home_goals": home_goals,
            "away_goals": away_goals,
            "final_score": final_score,
            "condition_id": market["condition_id"],
            "market_question": market["market_question"],
            "market_type": market["market_type"],
            "outcome": outcome,
            "token_id": token_id,
            "target": target,
            "resolved_outcome_price": price,
            "resolution_rule": market["resolution_rule"],
            "label_quality": "clean_completed_game_label",
        })

OUT.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.parent.mkdir(parents=True, exist_ok=True)

with OUT.open("w", encoding="utf-8", newline="") as f:
    fieldnames = [
        "label_source",
        "fixture",
        "fixture_slug",
        "home_team",
        "away_team",
        "home_goals",
        "away_goals",
        "final_score",
        "condition_id",
        "market_question",
        "market_type",
        "outcome",
        "token_id",
        "target",
        "resolved_outcome_price",
        "resolution_rule",
        "label_quality",
    ]
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

summary = {
    "fixture": fixture,
    "fixture_slug": fixture_slug,
    "final_score": final_score,
    "markets_labelled": len(markets),
    "label_rows": len(rows),
    "unique_tokens": len({r["token_id"] for r in rows}),
    "target_counts": {
        "0": sum(1 for r in rows if int(r["target"]) == 0),
        "1": sum(1 for r in rows if int(r["target"]) == 1),
    },
    "price_target_mismatches": mismatches,
    "status": "ok" if not mismatches else "mismatch_warning",
    "output": str(OUT),
    "next_step": "Join these completed-game labels to available pre/post snapshot rows for the same token IDs.",
}

SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
