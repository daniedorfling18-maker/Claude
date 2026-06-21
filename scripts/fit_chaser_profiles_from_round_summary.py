from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PROFILE_COLUMNS = ["raw_weight", "top1_weight", "top2_weight", "top3_weight", "modal_weight", "chase_weight", "chalk_weight"]
POINT_VALUES = {"exact_count": 3.0, "close_count": 1.5, "result_count": 1.0}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate player behaviour between Superbru rounds from aggregate round summaries and infer "
            "lightweight chaser-profile priors."
        )
    )
    parser.add_argument("--round-summary-csv", required=True)
    parser.add_argument("--out-dir", default="outputs/round_summary_behaviour")
    parser.add_argument("--min-rounds", type=int, default=2)
    parser.add_argument("--min-matches", type=int, default=8)
    parser.add_argument("--laplace", type=float, default=0.25)
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def fnum(value: Any, default: float = 0.0) -> float:
    try:
        text = txt(value).replace(",", ".")
        if text == "":
            return default
        return float(text)
    except Exception:
        return default


def infer_missing_counts(row: pd.Series) -> dict[str, float]:
    total_matches = fnum(row.get("total_matches"), 0.0)
    exact = fnum(row.get("exact_count"), 0.0)
    close = fnum(row.get("close_count"), 0.0)
    result = fnum(row.get("result_count"), 0.0)
    wrong = fnum(row.get("wrong_count"), total_matches - exact - close - result)
    wrong = max(0.0, wrong)

    round_points = fnum(row.get("round_points"), exact * 3.0 + close * 1.5 + result)
    if "exact_count" not in row or "close_count" not in row or "result_count" not in row:
        # If only round_points is available, preserve it but avoid inventing category counts.
        exact = fnum(row.get("exact_count"), exact)
        close = fnum(row.get("close_count"), close)
        result = fnum(row.get("result_count"), result)
    return {
        "total_matches": total_matches,
        "exact_count": exact,
        "close_count": close,
        "result_count": result,
        "wrong_count": wrong,
        "round_points": round_points,
    }


def prepare_rounds(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"round", "player"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Round summary CSV missing required columns: {sorted(missing)}")

    rows: list[dict[str, Any]] = []
    for _, row in frame.fillna("").iterrows():
        counts = infer_missing_counts(row)
        total_matches = counts["total_matches"]
        round_points = counts["round_points"]
        max_points = total_matches * 3.0 if total_matches > 0 else 0.0
        non_wrong = counts["exact_count"] + counts["close_count"] + counts["result_count"]
        rows.append(
            {
                "round": txt(row.get("round")),
                "player": txt(row.get("player")),
                "round_points": round_points,
                "total_matches": total_matches,
                "points_per_match": round_points / total_matches if total_matches else 0.0,
                "max_points": max_points,
                "weighted_accuracy": round_points / max_points if max_points else 0.0,
                "exact_count": counts["exact_count"],
                "close_count": counts["close_count"],
                "result_count": counts["result_count"],
                "wrong_count": counts["wrong_count"],
                "exact_rate": counts["exact_count"] / total_matches if total_matches else 0.0,
                "close_rate": counts["close_count"] / total_matches if total_matches else 0.0,
                "result_rate": counts["result_count"] / total_matches if total_matches else 0.0,
                "wrong_rate": counts["wrong_count"] / total_matches if total_matches else 0.0,
                "non_wrong_rate": non_wrong / total_matches if total_matches else 0.0,
                "exact_points_share": (counts["exact_count"] * 3.0 / round_points) if round_points else 0.0,
                "close_points_share": (counts["close_count"] * 1.5 / round_points) if round_points else 0.0,
                "result_points_share": (counts["result_count"] / round_points) if round_points else 0.0,
            }
        )
    return pd.DataFrame(rows)


def trend_label(delta: float, tolerance: float = 0.05) -> str:
    if delta > tolerance:
        return "improved"
    if delta < -tolerance:
        return "regressed"
    return "stable"


def behaviour_between_rounds(rounds: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    sortable = rounds.copy()
    sortable["_round_num"] = sortable["round"].map(lambda value: fnum(value, 0.0))
    for player, group in sortable.sort_values("_round_num").groupby("player"):
        group = group.sort_values("_round_num")
        previous = None
        for _, current in group.iterrows():
            record = current.drop(labels=["_round_num"], errors="ignore").to_dict()
            if previous is None:
                record.update(
                    {
                        "previous_round": "",
                        "delta_points_per_match": 0.0,
                        "delta_weighted_accuracy": 0.0,
                        "delta_exact_rate": 0.0,
                        "delta_wrong_rate": 0.0,
                        "trend": "baseline",
                        "behaviour_note": "first observed round",
                    }
                )
            else:
                d_ppm = float(current["points_per_match"] - previous["points_per_match"])
                d_acc = float(current["weighted_accuracy"] - previous["weighted_accuracy"])
                d_exact = float(current["exact_rate"] - previous["exact_rate"])
                d_wrong = float(current["wrong_rate"] - previous["wrong_rate"])
                trend = trend_label(d_ppm)
                notes = []
                if d_exact > 0.05:
                    notes.append("more exacts")
                elif d_exact < -0.05:
                    notes.append("fewer exacts")
                if d_wrong < -0.05:
                    notes.append("fewer wrong outcomes")
                elif d_wrong > 0.05:
                    notes.append("more wrong outcomes")
                if not notes:
                    notes.append("mix broadly stable")
                record.update(
                    {
                        "previous_round": previous["round"],
                        "delta_points_per_match": round(d_ppm, 6),
                        "delta_weighted_accuracy": round(d_acc, 6),
                        "delta_exact_rate": round(d_exact, 6),
                        "delta_wrong_rate": round(d_wrong, 6),
                        "trend": trend,
                        "behaviour_note": "; ".join(notes),
                    }
                )
            rows.append(record)
            previous = current
    return pd.DataFrame(rows)


def fit_round_summary_profiles(rounds: pd.DataFrame, laplace: float, min_rounds: int, min_matches: int) -> pd.DataFrame:
    profile_rows: list[dict[str, Any]] = []
    for player, group in rounds.groupby("player"):
        total_matches = float(group["total_matches"].sum())
        exact = float(group["exact_count"].sum())
        close = float(group["close_count"].sum())
        result = float(group["result_count"].sum())
        wrong = float(group["wrong_count"].sum())
        points = float(group["round_points"].sum())
        max_points = total_matches * 3.0 if total_matches else 0.0

        exact_rate = exact / total_matches if total_matches else 0.0
        close_rate = close / total_matches if total_matches else 0.0
        result_rate = result / total_matches if total_matches else 0.0
        wrong_rate = wrong / total_matches if total_matches else 0.0
        weighted_accuracy = points / max_points if max_points else 0.0

        # These are priors for the existing MC chaser buckets. They are intentionally conservative:
        # exact-heavy players get more top/chase weight, close-heavy players get more top2/top3 weight,
        # result-only players get more chalk/modal weight, and wrong-heavy players are spread wider.
        weights = {
            "raw_weight": laplace + 0.20 + weighted_accuracy * 0.25,
            "top1_weight": laplace + 0.12 + exact_rate * 0.55 + weighted_accuracy * 0.10,
            "top2_weight": laplace + 0.10 + close_rate * 0.35 + result_rate * 0.10,
            "top3_weight": laplace + 0.08 + close_rate * 0.25 + wrong_rate * 0.05,
            "modal_weight": laplace + 0.08 + result_rate * 0.25 + wrong_rate * 0.05,
            "chase_weight": laplace + 0.06 + exact_rate * 0.35 + wrong_rate * 0.10,
            "chalk_weight": laplace + 0.10 + result_rate * 0.30 + close_rate * 0.15,
        }
        total_weight = sum(weights.values())
        normalised = {key: round(value / total_weight, 6) for key, value in weights.items()}
        profile_rows.append(
            {
                "player": player,
                **normalised,
                "observed_rounds": int(group["round"].nunique()),
                "observed_matches": int(total_matches),
                "observed_points": round(points, 6),
                "points_per_match": round(points / total_matches, 6) if total_matches else 0.0,
                "weighted_accuracy": round(weighted_accuracy, 6),
                "exact_rate": round(exact_rate, 6),
                "close_rate": round(close_rate, 6),
                "result_rate": round(result_rate, 6),
                "wrong_rate": round(wrong_rate, 6),
                "low_sample_flag": int(group["round"].nunique() < min_rounds or total_matches < min_matches),
            }
        )
    return pd.DataFrame(profile_rows).sort_values("weighted_accuracy", ascending=False)


def main() -> int:
    args = build_parser().parse_args()
    raw = pd.read_csv(args.round_summary_csv).fillna("")
    rounds = prepare_rounds(raw)
    trends = behaviour_between_rounds(rounds)
    profiles = fit_round_summary_profiles(
        rounds=rounds,
        laplace=max(0.0, float(args.laplace)),
        min_rounds=int(args.min_rounds),
        min_matches=int(args.min_matches),
    )

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "players": sorted(rounds["player"].unique().tolist()),
        "rounds": sorted(rounds["round"].unique().tolist()),
        "rows": int(len(rounds)),
        "notes": [
            "This is aggregate behavioural evidence, not exact pick reconstruction.",
            "Use the profile CSV as a conservative prior when exact historical picks are unavailable.",
            "Round-to-round comparison is normalised per match because rounds may contain different numbers of completed games.",
        ],
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rounds_path = out_dir / "round_behaviour_summary.csv"
    trends_path = out_dir / "round_behaviour_trends.csv"
    profiles_path = out_dir / "chaser_profiles_from_round_summary.csv"
    summary_path = out_dir / "round_summary_behaviour.json"

    rounds.to_csv(rounds_path, index=False)
    trends.to_csv(trends_path, index=False)
    profiles.to_csv(profiles_path, index=False)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("Round behaviour trends")
    cols = ["round", "player", "points_per_match", "weighted_accuracy", "exact_rate", "wrong_rate", "trend", "behaviour_note"]
    print(trends[cols].to_string(index=False))
    print()
    print("Inferred round-summary profiles")
    print(profiles[["player", "points_per_match", "weighted_accuracy", "exact_rate", "close_rate", "result_rate", "wrong_rate", "low_sample_flag"]].to_string(index=False))
    print(f"Wrote {rounds_path}")
    print(f"Wrote {trends_path}")
    print(f"Wrote {profiles_path}")
    print(f"Wrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
