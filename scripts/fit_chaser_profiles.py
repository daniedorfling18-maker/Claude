from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from superbru_score_engine.model.team_names import canonical_team_key

PROFILE_COLUMNS = ["raw_weight", "top1_weight", "top2_weight", "top3_weight", "modal_weight", "chase_weight", "chalk_weight"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fit chaser behavioural weights from historical pool picks and model candidate columns."
    )
    parser.add_argument("--historical-picks-csv", required=True, help="CSV with player, picked_scoreline, and match_id or teams.")
    parser.add_argument("--predictions-csv", required=True, help="Prediction CSV with candidate columns for the same matches.")
    parser.add_argument("--out-dir", default="outputs/chaser_profile_fit")
    parser.add_argument("--laplace", type=float, default=0.5, help="Smoothing added to each weight bucket.")
    parser.add_argument("--min-picks", type=int, default=3, help="Flag players with fewer historical picks as low sample.")
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def parse_score(value: Any) -> tuple[int, int] | None:
    text = txt(value)
    if "-" not in text:
        return None
    left, right = text.split("-", 1)
    if not left.strip().isdigit() or not right.strip().isdigit():
        return None
    return int(left.strip()), int(right.strip())


def outcome_from_scoreline(value: Any) -> str:
    parsed = parse_score(value)
    if parsed is None:
        return ""
    home, away = parsed
    if home > away:
        return "home"
    if home < away:
        return "away"
    return "draw"


def chalk_scores(raw_scoreline: str) -> set[str]:
    outcome = outcome_from_scoreline(raw_scoreline)
    if outcome == "home":
        return {"1-0", "2-0", "2-1", "3-0", "3-1"}
    if outcome == "away":
        return {"0-1", "0-2", "1-2", "0-3", "1-3"}
    return {"0-0", "1-1", "2-2"}


def key(row: pd.Series) -> tuple[str, str, str]:
    return (
        txt(row.get("match_id")),
        canonical_team_key(txt(row.get("home_team"))),
        canonical_team_key(txt(row.get("away_team"))),
    )


def classify_pick(pick: str, row: pd.Series) -> str:
    pick = txt(pick)
    if parse_score(pick) is None:
        return "other"
    if pick == txt(row.get("recommended_scoreline")) or pick == txt(row.get("raw_ev_scoreline")):
        return "raw_weight"
    if pick == txt(row.get("top1_scoreline")):
        return "top1_weight"
    if pick == txt(row.get("top2_scoreline")):
        return "top2_weight"
    if pick == txt(row.get("top3_scoreline")):
        return "top3_weight"
    if pick == txt(row.get("modal_scoreline")):
        return "modal_weight"
    if pick == txt(row.get("private_chase_scoreline")):
        return "chase_weight"
    if pick in chalk_scores(txt(row.get("recommended_scoreline"))):
        return "chalk_weight"
    return "other"


def main() -> int:
    args = build_parser().parse_args()
    picks = pd.read_csv(args.historical_picks_csv).fillna("")
    predictions = pd.read_csv(args.predictions_csv).fillna("")

    required_pick_cols = {"player", "picked_scoreline"}
    missing = required_pick_cols - set(picks.columns)
    if missing:
        raise ValueError(f"Historical picks CSV missing required columns: {sorted(missing)}")

    predictions = predictions.copy()
    predictions["_key"] = predictions.apply(key, axis=1)
    picks = picks.copy()
    picks["_key"] = picks.apply(key, axis=1)

    merged = picks.merge(predictions, on="_key", how="inner", suffixes=("_pick", ""))
    if merged.empty:
        raise ValueError("No historical picks matched predictions by match_id/team pair.")

    rows = []
    for _, row in merged.iterrows():
        bucket = classify_pick(row.get("picked_scoreline"), row)
        rows.append(
            {
                "player": txt(row.get("player")),
                "picked_scoreline": txt(row.get("picked_scoreline")),
                "home_team": txt(row.get("home_team")),
                "away_team": txt(row.get("away_team")),
                "bucket": bucket,
            }
        )
    detail = pd.DataFrame(rows)

    profile_rows = []
    laplace = max(0.0, float(args.laplace))
    for player, group in detail.groupby("player"):
        counts = {col: laplace for col in PROFILE_COLUMNS}
        other_count = 0.0
        for bucket, count in group["bucket"].value_counts().items():
            if bucket in counts:
                counts[bucket] += float(count)
            else:
                other_count += float(count)
        total = sum(counts.values())
        profile = {col: round(counts[col] / total, 6) if total > 0 else 0.0 for col in PROFILE_COLUMNS}
        profile.update(
            {
                "player": player,
                "historical_picks": int(len(group)),
                "other_picks": int(other_count),
                "low_sample_flag": int(len(group) < int(args.min_picks)),
            }
        )
        profile_rows.append(profile)

    profiles = pd.DataFrame(profile_rows)
    ordered_cols = ["player"] + PROFILE_COLUMNS + ["historical_picks", "other_picks", "low_sample_flag"]
    profiles = profiles[ordered_cols].sort_values("player")

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "matched_pick_rows": int(len(detail)),
        "players": sorted(detail["player"].unique().tolist()),
        "laplace": laplace,
        "min_picks": int(args.min_picks),
        "notes": [
            "Weights are descriptive behavioural priors, not causal proof.",
            "Low-sample players should be blended with defaults before production use.",
        ],
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    profiles_path = out_dir / "chaser_profiles_fit.csv"
    detail_path = out_dir / "chaser_pick_classification_detail.csv"
    summary_path = out_dir / "chaser_profile_fit_summary.json"

    profiles.to_csv(profiles_path, index=False)
    detail.to_csv(detail_path, index=False)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(profiles.to_string(index=False))
    print(f"Wrote {profiles_path}")
    print(f"Wrote {detail_path}")
    print(f"Wrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
