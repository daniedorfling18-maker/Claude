from __future__ import annotations

import argparse
import json
import math
import re
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
POINT_COLUMNS = ["points", "point", "score", "score_points", "points_earned"]
ACTUAL_SCORE_COLUMNS = ["actual_scoreline", "result", "scoreline", "final_scoreline"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Infer chaser behavioural profile weights from historical Superbru points instead of exact picks. "
            "This is useful when screenshots show only 0/1/1.5/3 points per player per match."
        )
    )
    parser.add_argument("--historical-points-csv", required=True)
    parser.add_argument("--predictions-csv", required=True)
    parser.add_argument(
        "--match-order-csv",
        default="",
        help=(
            "Optional CSV mapping round/match_order to match_id, teams and actual_scoreline. "
            "Required for wide screenshot-style input unless the points CSV already includes match metadata."
        ),
    )
    parser.add_argument("--out-dir", default="outputs/chaser_profile_points_fit")
    parser.add_argument("--ci-cutoff", type=float, default=1.5)
    parser.add_argument("--laplace", type=float, default=0.5)
    parser.add_argument("--min-rows", type=int, default=8)
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def fnum(value: Any, default: float = 0.0) -> float:
    text = txt(value).replace(",", ".")
    if not text:
        return default
    try:
        return float(text)
    except Exception:
        return default


def parse_score(value: Any) -> tuple[int, int] | None:
    text = txt(value)
    if "-" not in text:
        return None
    left, right = text.split("-", 1)
    if not left.strip().isdigit() or not right.strip().isdigit():
        return None
    return int(left.strip()), int(right.strip())


def scoreline(home: int, away: int) -> str:
    return f"{home}-{away}"


def outcome(home: int, away: int) -> str:
    if home > away:
        return "home"
    if home < away:
        return "away"
    return "draw"


def outcome_from_scoreline(value: Any) -> str:
    parsed = parse_score(value)
    if parsed is None:
        return ""
    return outcome(parsed[0], parsed[1])


def superbru_points(pred_home: int, pred_away: int, actual_home: int, actual_away: int, ci_cutoff: float) -> float:
    if pred_home == actual_home and pred_away == actual_away:
        return 3.0

    pred_outcome = outcome(pred_home, pred_away)
    actual_outcome = outcome(actual_home, actual_away)
    if pred_outcome != actual_outcome:
        return 0.0

    pred_gd = pred_home - pred_away
    actual_gd = actual_home - actual_away
    pred_total = pred_home + pred_away
    actual_total = actual_home + actual_away
    closeness_index = abs(pred_gd - actual_gd) + abs(pred_total - actual_total) / 2.0
    return 1.5 if closeness_index <= ci_cutoff else 1.0


def chalk_scores(raw_scoreline: str) -> list[str]:
    result = outcome_from_scoreline(raw_scoreline)
    if result == "home":
        return ["1-0", "2-0", "2-1", "3-0", "3-1"]
    if result == "away":
        return ["0-1", "0-2", "1-2", "0-3", "1-3"]
    return ["0-0", "1-1", "2-2"]


def key(row: pd.Series) -> tuple[str, str, str]:
    return (
        txt(row.get("match_id")),
        canonical_team_key(txt(row.get("home_team"))),
        canonical_team_key(txt(row.get("away_team"))),
    )


def first_existing(columns: list[str], candidates: list[str]) -> str:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return ""


def is_match_column(column: str) -> bool:
    return re.fullmatch(r"match[_ ]?\d+", column.strip().lower()) is not None


def match_order_from_column(column: str) -> int:
    digits = re.findall(r"\d+", column)
    if not digits:
        raise ValueError(f"Cannot infer match order from column {column!r}")
    return int(digits[-1])


def normalize_points(points: pd.DataFrame, match_order: pd.DataFrame | None) -> pd.DataFrame:
    point_col = first_existing(list(points.columns), POINT_COLUMNS)
    if point_col and "player" in points.columns:
        normalized = points.copy().rename(columns={point_col: "points"})
    else:
        match_cols = [col for col in points.columns if is_match_column(col)]
        if "player" not in points.columns or not match_cols:
            raise ValueError(
                "Historical points CSV must either be long format with player + points, "
                "or wide format with player + match_1, match_2, ... columns."
            )
        rows: list[dict[str, Any]] = []
        for _, row in points.iterrows():
            for column in match_cols:
                value = txt(row.get(column))
                if value == "":
                    continue
                rows.append(
                    {
                        "round": row.get("round", ""),
                        "player": row.get("player", ""),
                        "match_order": match_order_from_column(column),
                        "points": value,
                    }
                )
        normalized = pd.DataFrame(rows)

    if "match_order" in normalized.columns:
        normalized["match_order"] = normalized["match_order"].map(lambda value: int(fnum(value, 0)))
    if "round" in normalized.columns:
        normalized["round"] = normalized["round"].map(txt)

    if match_order is not None and not match_order.empty:
        join_cols = ["match_order"]
        if "round" in normalized.columns and "round" in match_order.columns:
            join_cols = ["round", "match_order"]
            match_order = match_order.copy()
            match_order["round"] = match_order["round"].map(txt)
        normalized = normalized.merge(match_order, on=join_cols, how="left", suffixes=("", "_match"))

    if "actual_scoreline" not in normalized.columns:
        actual_col = first_existing(list(normalized.columns), ACTUAL_SCORE_COLUMNS)
        if actual_col:
            normalized = normalized.rename(columns={actual_col: "actual_scoreline"})

    if "actual_scoreline" not in normalized.columns:
        if "actual_home_goals" in normalized.columns and "actual_away_goals" in normalized.columns:
            normalized["actual_scoreline"] = normalized.apply(
                lambda row: scoreline(int(fnum(row.get("actual_home_goals"))), int(fnum(row.get("actual_away_goals")))), axis=1
            )
        elif "home_goals" in normalized.columns and "away_goals" in normalized.columns:
            normalized["actual_scoreline"] = normalized.apply(
                lambda row: scoreline(int(fnum(row.get("home_goals"))), int(fnum(row.get("away_goals")))), axis=1
            )

    required = ["player", "points", "actual_scoreline"]
    missing = [col for col in required if col not in normalized.columns]
    if missing:
        raise ValueError(f"Normalized points data missing required columns: {missing}")

    return normalized.fillna("")


def candidate_bucket_scores(row: pd.Series) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {col: [] for col in PROFILE_COLUMNS}

    def add(bucket: str, value: Any) -> None:
        score = txt(value)
        if parse_score(score) is not None and score not in buckets[bucket]:
            buckets[bucket].append(score)

    add("raw_weight", row.get("recommended_scoreline"))
    add("raw_weight", row.get("raw_ev_scoreline"))
    add("top1_weight", row.get("top1_scoreline"))
    add("top2_weight", row.get("top2_scoreline"))
    add("top3_weight", row.get("top3_scoreline"))
    add("modal_weight", row.get("modal_scoreline"))
    add("chase_weight", row.get("private_chase_scoreline"))
    add("chase_weight", row.get("exact_chase_scoreline"))
    for score in chalk_scores(txt(row.get("recommended_scoreline"))):
        add("chalk_weight", score)

    return buckets


def bucket_likelihoods(row: pd.Series, actual_scoreline: str, observed_points: float, ci_cutoff: float) -> dict[str, float]:
    actual = parse_score(actual_scoreline)
    if actual is None:
        return {col: 0.0 for col in PROFILE_COLUMNS}

    likelihoods: dict[str, float] = {}
    for bucket, scores in candidate_bucket_scores(row).items():
        if not scores:
            likelihoods[bucket] = 0.0
            continue
        hits = 0.0
        for candidate in scores:
            parsed = parse_score(candidate)
            if parsed is None:
                continue
            earned = superbru_points(parsed[0], parsed[1], actual[0], actual[1], ci_cutoff)
            if abs(earned - observed_points) < 1e-9:
                hits += 1.0
        likelihoods[bucket] = hits / len(scores)
    return likelihoods


def merge_predictions(points: pd.DataFrame, predictions: pd.DataFrame) -> pd.DataFrame:
    points = points.copy()
    predictions = predictions.copy()
    points["_key"] = points.apply(key, axis=1)
    predictions["_key"] = predictions.apply(key, axis=1)

    merged = points.merge(predictions, on="_key", how="inner", suffixes=("_history", ""))
    return merged


def fit_profiles(merged: pd.DataFrame, ci_cutoff: float, laplace: float, min_rows: int) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    detail_rows: list[dict[str, Any]] = []
    contribution_rows: list[dict[str, Any]] = []

    for _, row in merged.iterrows():
        player = txt(row.get("player"))
        observed = fnum(row.get("points"))
        likelihoods = bucket_likelihoods(row, txt(row.get("actual_scoreline")), observed, ci_cutoff)
        total_likelihood = sum(likelihoods.values())
        unexplained = total_likelihood <= 0

        allocations = {bucket: (likelihood / total_likelihood if total_likelihood > 0 else 0.0) for bucket, likelihood in likelihoods.items()}
        for bucket, allocation in allocations.items():
            contribution_rows.append({"player": player, "bucket": bucket, "allocation": allocation})

        actual = parse_score(row.get("actual_scoreline"))
        detail_rows.append(
            {
                "round": txt(row.get("round")),
                "match_order": txt(row.get("match_order")),
                "player": player,
                "home_team": txt(row.get("home_team")),
                "away_team": txt(row.get("away_team")),
                "actual_scoreline": txt(row.get("actual_scoreline")),
                "observed_points": observed,
                "actual_outcome": outcome(actual[0], actual[1]) if actual else "",
                "unexplained": unexplained,
                **{f"like_{bucket}": round(value, 6) for bucket, value in likelihoods.items()},
                **{f"alloc_{bucket}": round(value, 6) for bucket, value in allocations.items()},
            }
        )

    detail = pd.DataFrame(detail_rows)
    contributions = pd.DataFrame(contribution_rows)
    profile_rows: list[dict[str, Any]] = []

    for player, group in contributions.groupby("player"):
        player_detail = detail[detail["player"] == player]
        bucket_totals = {bucket: float(laplace) for bucket in PROFILE_COLUMNS}
        for bucket, bucket_group in group.groupby("bucket"):
            if bucket in bucket_totals:
                bucket_totals[bucket] += float(bucket_group["allocation"].sum())
        total = sum(bucket_totals.values())
        profile = {bucket: round(bucket_totals[bucket] / total, 6) if total > 0 else 0.0 for bucket in PROFILE_COLUMNS}
        profile.update(
            {
                "player": player,
                "historical_point_rows": int(len(player_detail)),
                "unexplained_rows": int(player_detail["unexplained"].sum()),
                "unexplained_rate": round(float(player_detail["unexplained"].mean()), 6) if len(player_detail) else 0.0,
                "exact_rate": round(float((player_detail["observed_points"] == 3.0).mean()), 6) if len(player_detail) else 0.0,
                "close_rate": round(float((player_detail["observed_points"] == 1.5).mean()), 6) if len(player_detail) else 0.0,
                "result_rate": round(float((player_detail["observed_points"] == 1.0).mean()), 6) if len(player_detail) else 0.0,
                "wrong_rate": round(float((player_detail["observed_points"] == 0.0).mean()), 6) if len(player_detail) else 0.0,
                "low_sample_flag": int(len(player_detail) < int(min_rows)),
            }
        )
        profile_rows.append(profile)

    profiles = pd.DataFrame(profile_rows)
    if not profiles.empty:
        profiles = profiles[["player"] + PROFILE_COLUMNS + [
            "historical_point_rows",
            "unexplained_rows",
            "unexplained_rate",
            "exact_rate",
            "close_rate",
            "result_rate",
            "wrong_rate",
            "low_sample_flag",
        ]].sort_values("player")

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "matched_point_rows": int(len(detail)),
        "players": sorted(detail["player"].dropna().unique().tolist()) if not detail.empty else [],
        "ci_cutoff": ci_cutoff,
        "laplace": laplace,
        "min_rows": min_rows,
        "unexplained_rows": int(detail["unexplained"].sum()) if not detail.empty else 0,
        "notes": [
            "Profiles are inferred probabilistically from observed points, not exact historical picks.",
            "Use as behavioural priors for chaser simulation, especially when exact scoreline picks are unavailable.",
            "High unexplained rates suggest missing candidate scorelines or an unmatched match order.",
        ],
    }
    return profiles, detail, summary


def main() -> int:
    args = build_parser().parse_args()
    historical_points = pd.read_csv(args.historical_points_csv).fillna("")
    match_order = pd.read_csv(args.match_order_csv).fillna("") if args.match_order_csv else None
    predictions = pd.read_csv(args.predictions_csv).fillna("")

    normalized = normalize_points(historical_points, match_order)
    merged = merge_predictions(normalized, predictions)
    if merged.empty:
        raise ValueError("No historical point rows matched predictions by match_id/team pair.")

    profiles, detail, summary = fit_profiles(
        merged=merged,
        ci_cutoff=float(args.ci_cutoff),
        laplace=max(0.0, float(args.laplace)),
        min_rows=int(args.min_rows),
    )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    profiles_path = out_dir / "chaser_profiles_from_points.csv"
    detail_path = out_dir / "chaser_points_inference_detail.csv"
    normalized_path = out_dir / "normalized_historical_points.csv"
    summary_path = out_dir / "chaser_points_inference_summary.json"

    profiles.to_csv(profiles_path, index=False)
    detail.to_csv(detail_path, index=False)
    normalized.to_csv(normalized_path, index=False)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(profiles.to_string(index=False))
    print(f"Wrote {profiles_path}")
    print(f"Wrote {detail_path}")
    print(f"Wrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
