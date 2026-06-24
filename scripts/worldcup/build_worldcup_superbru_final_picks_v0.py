from __future__ import annotations

import csv
import json
from pathlib import Path
from datetime import datetime, timezone

INP = Path("outputs/polymarket_training/worldcup_superbru_model_feature_template.csv")

OUT = Path("outputs/polymarket_training/worldcup_superbru_final_picks_v0_market_baseline.csv")
SUMMARY = Path("outputs/polymarket_model_governance/worldcup_superbru_final_picks_v0_market_baseline_summary.json")

def read_csv(path):
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        return list(csv.DictReader(f))

rows = read_csv(INP)
out = []

for r in rows:
    final = dict(r)
    final["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    final["model_recommended_pick"] = ""
    final["model_recommended_scoreline"] = ""
    final["model_confidence"] = ""
    final["override_market_pick"] = "False"
    final["override_reason"] = ""
    final["final_pick"] = r.get("market_pick", "")
    final["final_scoreline"] = r.get("market_scoreline", "")
    final["final_confidence"] = r.get("market_confidence", "")
    final["final_reason"] = "Market-implied baseline; no model override applied."
    final["version"] = "v0_market_baseline"
    out.append(final)

OUT.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.parent.mkdir(parents=True, exist_ok=True)

fieldnames = [
    "generated_at_utc",
    "version",
    "fixture_slug",
    "fixture_date",
    "market_pick",
    "market_confidence",
    "market_risk_band",
    "market_scoreline",
    "model_home_strength",
    "model_away_strength",
    "model_draw_propensity",
    "model_form_edge",
    "model_group_incentive_edge",
    "model_injury_or_rotation_flag",
    "model_recommended_pick",
    "model_recommended_scoreline",
    "model_confidence",
    "override_market_pick",
    "override_reason",
    "final_pick",
    "final_scoreline",
    "final_confidence",
    "final_reason",
]

with OUT.open("w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(out)

summary = {
    "input_rows": len(rows),
    "final_pick_rows": len(out),
    "overrides_applied": 0,
    "output": str(OUT),
    "warning": "v0 uses Polymarket market-implied baseline only. It is not yet a proprietary model or edge layer.",
}

SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
