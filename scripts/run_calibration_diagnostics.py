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


PREDICTED_OUTCOME_PROB_COLUMNS = ["p_outcome", "P(outcome)", "prob_outcome", "outcome_probability"]
P_EXACT_COLUMNS = ["p_exact", "P(exact)", "prob_exact", "exact_probability"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Calculate calibration diagnostics for prediction outputs once actual results are known."
    )
    parser.add_argument("--predictions-csv", default="outputs/latest/predictions.csv")
    parser.add_argument("--results-csv", required=True, help="CSV with match_id or team names plus actual goals.")
    parser.add_argument("--out-dir", default="outputs/calibration_diagnostics")
    parser.add_argument("--bucket-width", type=float, default=0.10)
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


def outcome(home: int, away: int) -> str:
    if home > away:
        return "home"
    if home < away:
        return "away"
    return "draw"


def first_existing_column(frame: pd.DataFrame, candidates: list[str]) -> str:
    for col in candidates:
        if col in frame.columns:
            return col
    return ""


def actual_goal_columns(results: pd.DataFrame) -> tuple[str, str]:
    options = [
        ("home_goals", "away_goals"),
        ("actual_home_goals", "actual_away_goals"),
        ("home_score", "away_score"),
        ("hscore", "ascore"),
    ]
    for home_col, away_col in options:
        if home_col in results.columns and away_col in results.columns:
            return home_col, away_col
    raise ValueError("Could not find actual goal columns. Expected home_goals/away_goals or similar.")


def match_key(row: pd.Series) -> tuple[str, str, str]:
    return (
        txt(row.get("match_id")),
        canonical_team_key(txt(row.get("home_team"))),
        canonical_team_key(txt(row.get("away_team"))),
    )


def merge_results(predictions: pd.DataFrame, results: pd.DataFrame) -> pd.DataFrame:
    home_goals_col, away_goals_col = actual_goal_columns(results)
    results = results.copy()
    results["_key"] = results.apply(match_key, axis=1)
    predictions = predictions.copy()
    predictions["_key"] = predictions.apply(match_key, axis=1)

    keep_cols = ["_key", home_goals_col, away_goals_col]
    for col in ["match_id", "home_team", "away_team"]:
        if col in results.columns and col not in keep_cols:
            keep_cols.append(col)

    merged = predictions.merge(results[keep_cols], on="_key", how="inner", suffixes=("", "_actual"))
    merged = merged.rename(columns={home_goals_col: "actual_home_goals", away_goals_col: "actual_away_goals"})
    return merged


def brier(values: pd.Series, actual: pd.Series) -> float:
    if len(values) == 0:
        return float("nan")
    return float(((values.astype(float) - actual.astype(float)) ** 2).mean())


def safe_log_loss(values: pd.Series, actual: pd.Series) -> float:
    if len(values) == 0:
        return float("nan")
    probs = values.astype(float).clip(1e-9, 1.0 - 1e-9)
    actual = actual.astype(float)
    return float((-(actual * probs.map(math.log) + (1.0 - actual) * (1.0 - probs).map(math.log))).mean())


def bucket_summary(frame: pd.DataFrame, prob_col: str, actual_col: str, bucket_width: float) -> pd.DataFrame:
    if frame.empty or prob_col not in frame.columns:
        return pd.DataFrame()
    data = frame.copy()
    data[prob_col] = pd.to_numeric(data[prob_col], errors="coerce")
    data = data.dropna(subset=[prob_col])
    if data.empty:
        return pd.DataFrame()
    width = max(0.01, float(bucket_width))
    data["bucket"] = (data[prob_col] / width).clip(0, int(1 / width)).astype(int) * width
    grouped = (
        data.groupby("bucket")
        .agg(
            n=(prob_col, "size"),
            mean_predicted=(prob_col, "mean"),
            actual_rate=(actual_col, "mean"),
        )
        .reset_index()
    )
    grouped["calibration_error"] = (grouped["mean_predicted"] - grouped["actual_rate"]).abs()
    return grouped


def main() -> int:
    args = build_parser().parse_args()
    predictions = pd.read_csv(args.predictions_csv).fillna("")
    results = pd.read_csv(args.results_csv).fillna("")
    merged = merge_results(predictions, results)

    if merged.empty:
        raise ValueError("No prediction rows could be matched to actual results.")

    exact_prob_col = first_existing_column(merged, P_EXACT_COLUMNS)
    outcome_prob_col = first_existing_column(merged, PREDICTED_OUTCOME_PROB_COLUMNS)

    rows: list[dict[str, Any]] = []
    for _, row in merged.iterrows():
        pick = parse_score(row.get("recommended_scoreline"))
        if pick is None:
            continue
        actual_home = int(float(row["actual_home_goals"]))
        actual_away = int(float(row["actual_away_goals"]))
        predicted_outcome = outcome(pick[0], pick[1])
        actual_outcome = outcome(actual_home, actual_away)
        rows.append(
            {
                "commence_time": txt(row.get("commence_time")),
                "home_team": txt(row.get("home_team")),
                "away_team": txt(row.get("away_team")),
                "recommended_scoreline": txt(row.get("recommended_scoreline")),
                "actual_scoreline": f"{actual_home}-{actual_away}",
                "exact_hit": int(pick == (actual_home, actual_away)),
                "outcome_hit": int(predicted_outcome == actual_outcome),
                "predicted_outcome": predicted_outcome,
                "actual_outcome": actual_outcome,
                "p_exact": float(row[exact_prob_col]) if exact_prob_col else float("nan"),
                "p_outcome": float(row[outcome_prob_col]) if outcome_prob_col else float("nan"),
            }
        )

    detail = pd.DataFrame(rows)
    if detail.empty:
        raise ValueError("No usable predicted scorelines were available for calibration.")

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "matched_matches": int(len(detail)),
        "exact_hit_rate": float(detail["exact_hit"].mean()),
        "outcome_hit_rate": float(detail["outcome_hit"].mean()),
        "p_exact_column_found": bool(exact_prob_col),
        "p_outcome_column_found": bool(outcome_prob_col),
        "exact_brier": brier(detail["p_exact"], detail["exact_hit"]) if exact_prob_col else None,
        "outcome_brier": brier(detail["p_outcome"], detail["outcome_hit"]) if outcome_prob_col else None,
        "outcome_log_loss": safe_log_loss(detail["p_outcome"], detail["outcome_hit"]) if outcome_prob_col else None,
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    detail_path = out_dir / "calibration_detail.csv"
    outcome_bucket_path = out_dir / "outcome_calibration_by_bucket.csv"
    exact_bucket_path = out_dir / "exact_calibration_by_bucket.csv"
    summary_path = out_dir / "calibration_summary.json"

    detail.to_csv(detail_path, index=False)
    if outcome_prob_col:
        bucket_summary(detail, "p_outcome", "outcome_hit", args.bucket_width).to_csv(outcome_bucket_path, index=False)
    if exact_prob_col:
        bucket_summary(detail, "p_exact", "exact_hit", args.bucket_width).to_csv(exact_bucket_path, index=False)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))
    print(f"Wrote {detail_path}")
    print(f"Wrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
