from __future__ import annotations

import csv
import json
from pathlib import Path

INP = Path("outputs/polymarket_training/worldcup_superbru_market_baseline_picks.csv")

OUT = Path("outputs/polymarket_training/worldcup_superbru_model_feature_template.csv")
SUMMARY = Path("outputs/polymarket_model_governance/worldcup_superbru_model_feature_template_summary.json")

def read_csv(path):
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        return list(csv.DictReader(f))

rows = read_csv(INP)
out = []

for r in rows:
    out.append({
        "fixture_slug": r.get("fixture_slug", ""),
        "fixture_date": r.get("fixture_date", ""),
        "market_pick": r.get("market_pick", ""),
        "market_confidence": r.get("market_confidence", ""),
        "market_risk_band": r.get("risk_band", ""),
        "market_scoreline": r.get("baseline_scoreline", ""),

        # To be filled by our modelling layer.
        "model_home_strength": "",
        "model_away_strength": "",
        "model_draw_propensity": "",
        "model_form_edge": "",
        "model_group_incentive_edge": "",
        "model_injury_or_rotation_flag": "",
        "model_recommended_pick": "",
        "model_recommended_scoreline": "",
        "model_confidence": "",
        "override_market_pick": "",
        "override_reason": "",

        # Final Superbru output.
        "final_pick": "",
        "final_scoreline": "",
        "final_confidence": "",
        "final_reason": "",
    })

OUT.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.parent.mkdir(parents=True, exist_ok=True)

fieldnames = list(out[0].keys()) if out else []

with OUT.open("w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(out)

summary = {
    "input_rows": len(rows),
    "template_rows": len(out),
    "output": str(OUT),
    "note": "Template keeps the Polymarket baseline separate from the model override and final Superbru pick.",
}

SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
