from __future__ import annotations

import argparse
import glob
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

REQUIRED_GRID_COLUMNS = {
    "home_name",
    "away_name",
    "home_goals",
    "away_goals",
    "probability_pct",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Convert Oddspedia SmartBet correct-score probability grids into calibration baseline "
            "and actual-results CSV files."
        )
    )
    parser.add_argument(
        "--smartbet-grid-csv",
        action="append",
        default=[],
        help="SmartBet grid CSV. Can be passed multiple times.",
    )
    parser.add_argument(
        "--smartbet-grid-glob",
        action="append",
        default=[],
        help="Glob pattern for SmartBet grid CSVs, for example inputs/smartbet_grids/*.csv.",
    )
    parser.add_argument("--out-dir", default="outputs/smartbet_grid_calibration")
    parser.add_argument(
        "--dedupe",
        choices=["latest", "completed_first", "none"],
        default="completed_first",
        help="How to dedupe duplicate match grids across screenshots/files.",
    )
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def fnum(value: Any, default: float = 0.0) -> float:
    text = txt(value).replace("%", "").replace(",", ".")
    if not text:
        return default
    try:
        return float(text)
    except Exception:
        return default


def parse_score(value: Any) -> tuple[int, int] | None:
    text = txt(value)
    if text == "" or text.lower() in {"nan", "none"}:
        return None
    if "-" not in text:
        return None
    left, right = text.split("-", 1)
    left = left.strip()
    right = right.strip()
    if not left.isdigit() or not right.isdigit():
        return None
    return int(left), int(right)


def scoreline(home: int, away: int) -> str:
    return f"{home}-{away}"


def outcome_from_score(home: int, away: int) -> str:
    if home > away:
        return "home"
    if home < away:
        return "away"
    return "draw"


def probability_decimal(value: Any) -> float:
    pct = fnum(value, 0.0)
    if pct > 1.0:
        return pct / 100.0
    return pct


def norm_team(value: Any) -> str:
    return "".join(ch.lower() for ch in txt(value) if ch.isalnum())


def match_key(row: pd.Series) -> tuple[str, str]:
    return norm_team(row.get("home_name") or row.get("home_team")), norm_team(row.get("away_name") or row.get("away_team"))


def collect_files(explicit: list[str], patterns: list[str]) -> list[Path]:
    files: list[Path] = []
    for item in explicit:
        path = Path(item)
        if path.exists():
            files.append(path)
    for pattern in patterns:
        for match in glob.glob(pattern, recursive=True):
            path = Path(match)
            if path.exists() and path.is_file():
                files.append(path)
    unique: list[Path] = []
    seen: set[str] = set()
    for path in files:
        resolved = str(path.resolve())
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return unique


def validate_grid(frame: pd.DataFrame, path: Path) -> None:
    missing = REQUIRED_GRID_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"{path} is missing SmartBet grid columns: {sorted(missing)}")


def read_grids(files: list[Path]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in files:
        frame = pd.read_csv(path).fillna("")
        validate_grid(frame, path)
        frame["source_file"] = str(path)
        frames.append(frame)
    if not frames:
        raise ValueError("No SmartBet grid CSV files were found. Pass --smartbet-grid-csv or --smartbet-grid-glob.")
    data = pd.concat(frames, ignore_index=True).fillna("")
    data["home_team"] = data.get("home_name", "").map(txt)
    data["away_team"] = data.get("away_name", "").map(txt)
    data["_home_key"] = data["home_team"].map(norm_team)
    data["_away_key"] = data["away_team"].map(norm_team)
    data["_source_date"] = data.get("source_date", "").map(txt) if "source_date" in data.columns else ""
    data["_source_file"] = data["source_file"].map(txt)
    data["_actual_present"] = data.get("actual_score", "").map(lambda value: parse_score(value) is not None) if "actual_score" in data.columns else False
    data["_probability_pct"] = data["probability_pct"].map(fnum)
    return data


def dedupe_grid_rows(data: pd.DataFrame, mode: str) -> pd.DataFrame:
    if mode == "none":
        return data.copy()

    group_cols = ["_home_key", "_away_key"]
    selected_frames: list[pd.DataFrame] = []
    for _, group in data.groupby(group_cols, sort=False):
        candidates = group.copy()
        if mode == "completed_first" and candidates["_actual_present"].any():
            candidates = candidates[candidates["_actual_present"]]
        # Prefer the lexicographically latest source_date/source_file if multiple grid copies exist.
        candidates = candidates.sort_values(["_source_date", "_source_file"], ascending=[False, False])
        chosen_date = candidates.iloc[0]["_source_date"]
        chosen_file = candidates.iloc[0]["_source_file"]
        chosen = candidates[(candidates["_source_date"] == chosen_date) & (candidates["_source_file"] == chosen_file)]
        selected_frames.append(chosen)
    return pd.concat(selected_frames, ignore_index=True).fillna("") if selected_frames else pd.DataFrame()


def grid_scoreline(row: pd.Series) -> str:
    home = txt(row.get("home_goals"))
    away = txt(row.get("away_goals"))
    if not home.isdigit() or not away.isdigit():
        return ""
    return f"{int(home)}-{int(away)}"


def score_probability_lookup(group: pd.DataFrame) -> dict[str, float]:
    lookup: dict[str, float] = {}
    for _, row in group.iterrows():
        s = grid_scoreline(row)
        if s:
            lookup[s] = fnum(row.get("probability_pct"), 0.0)
    return lookup


def choose_recommended_scoreline(group: pd.DataFrame) -> tuple[str, float, str, float]:
    lookup = score_probability_lookup(group)
    modal = txt(group.iloc[0].get("modal_score")) if "modal_score" in group.columns else ""
    modal_score = modal if parse_score(modal) is not None else ""

    sorted_rows = group.copy()
    sorted_rows["_scoreline"] = sorted_rows.apply(grid_scoreline, axis=1)
    sorted_rows = sorted_rows[sorted_rows["_scoreline"] != ""].sort_values("_probability_pct", ascending=False)
    if sorted_rows.empty:
        return modal_score, lookup.get(modal_score, 0.0), modal_score, lookup.get(modal_score, 0.0)

    top_score = txt(sorted_rows.iloc[0]["_scoreline"])
    top_prob = fnum(sorted_rows.iloc[0]["probability_pct"), 0.0)

    # Use the SmartBet modal score when it is parseable and appears in the grid; otherwise use the highest cell.
    if modal_score and modal_score in lookup:
        return modal_score, lookup[modal_score], top_score, top_prob
    return top_score, top_prob, top_score, top_prob


def outcome_probability(row: pd.Series, recommended_scoreline: str) -> float:
    parsed = parse_score(recommended_scoreline)
    if parsed is None:
        return 0.0
    result = outcome_from_score(parsed[0], parsed[1])
    if result == "home":
        return probability_decimal(row.get("winner_home_pct"))
    if result == "away":
        return probability_decimal(row.get("winner_away_pct"))
    return probability_decimal(row.get("winner_draw_pct"))


def build_outputs(data: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    baseline_rows: list[dict[str, Any]] = []
    result_rows: list[dict[str, Any]] = []
    detail_rows: list[dict[str, Any]] = []

    for (_, _), group in data.groupby(["_home_key", "_away_key"], sort=False):
        group = group.copy()
        first = group.iloc[0]
        home_team = txt(first.get("home_name")) or txt(first.get("home_team"))
        away_team = txt(first.get("away_name")) or txt(first.get("away_team"))
        home_code = txt(first.get("home"))
        away_code = txt(first.get("away"))
        recommended, recommended_pct, grid_top, grid_top_pct = choose_recommended_scoreline(group)
        p_outcome = outcome_probability(first, recommended)
        p_home = probability_decimal(first.get("winner_home_pct"))
        p_draw = probability_decimal(first.get("winner_draw_pct"))
        p_away = probability_decimal(first.get("winner_away_pct"))
        actual = parse_score(first.get("actual_score"))
        actual_scoreline = scoreline(actual[0], actual[1]) if actual else ""
        lookup = score_probability_lookup(group)
        actual_probability_pct = lookup.get(actual_scoreline, float("nan")) if actual_scoreline else float("nan")

        baseline_rows.append(
            {
                "match_id": "",
                "commence_time": "",
                "home_team": home_team,
                "away_team": away_team,
                "recommended_scoreline": recommended,
                "p_exact": round(recommended_pct / 100.0, 6),
                "p_outcome": round(p_outcome, 6),
                "p_home": round(p_home, 6),
                "p_draw": round(p_draw, 6),
                "p_away": round(p_away, 6),
                "source": txt(first.get("source")),
                "source_date": txt(first.get("source_date")),
                "source_file": txt(first.get("source_file")),
                "screenshot_file": txt(first.get("screenshot_file")),
                "home_code": home_code,
                "away_code": away_code,
                "smartbet_modal_scoreline": txt(first.get("modal_score")),
                "grid_top_scoreline": grid_top,
                "grid_top_probability_pct": round(grid_top_pct, 6),
                "recommended_probability_pct": round(recommended_pct, 6),
            }
        )

        if actual:
            result_rows.append(
                {
                    "match_id": "",
                    "home_team": home_team,
                    "away_team": away_team,
                    "home_goals": actual[0],
                    "away_goals": actual[1],
                    "actual_scoreline": actual_scoreline,
                    "source": txt(first.get("source")),
                    "source_date": txt(first.get("source_date")),
                    "source_file": txt(first.get("source_file")),
                    "screenshot_file": txt(first.get("screenshot_file")),
                }
            )

        detail_rows.append(
            {
                "home_team": home_team,
                "away_team": away_team,
                "recommended_scoreline": recommended,
                "recommended_probability_pct": round(recommended_pct, 6),
                "grid_top_scoreline": grid_top,
                "grid_top_probability_pct": round(grid_top_pct, 6),
                "actual_scoreline": actual_scoreline,
                "actual_probability_pct": None if math.isnan(actual_probability_pct) else round(actual_probability_pct, 6),
                "actual_in_grid": bool(actual_scoreline and not math.isnan(actual_probability_pct)),
                "exact_hit": int(actual_scoreline == recommended) if actual_scoreline else "",
                "outcome_hit": int(
                    parse_score(recommended) is not None
                    and actual is not None
                    and outcome_from_score(*parse_score(recommended)) == outcome_from_score(actual[0], actual[1])
                )
                if actual_scoreline
                else "",
                "completed": bool(actual_scoreline),
                "source_file": txt(first.get("source_file")),
                "screenshot_file": txt(first.get("screenshot_file")),
            }
        )

    baseline = pd.DataFrame(baseline_rows).sort_values(["source_date", "home_team", "away_team"])
    results = pd.DataFrame(result_rows).sort_values(["home_team", "away_team"]) if result_rows else pd.DataFrame(columns=["match_id", "home_team", "away_team", "home_goals", "away_goals", "actual_scoreline"])
    detail = pd.DataFrame(detail_rows).sort_values(["completed", "home_team", "away_team"], ascending=[False, True, True])
    completed_detail = detail[detail["completed"] == True] if not detail.empty else pd.DataFrame()
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_match_count": int(len(baseline)),
        "completed_match_count": int(len(results)),
        "incomplete_match_count": int(len(baseline) - len(results)),
        "completed_exact_hit_rate": float(pd.to_numeric(completed_detail["exact_hit"], errors="coerce").mean()) if not completed_detail.empty else None,
        "completed_outcome_hit_rate": float(pd.to_numeric(completed_detail["outcome_hit"], errors="coerce").mean()) if not completed_detail.empty else None,
        "notes": [
            "Baseline rows are SmartBet modal-score predictions with p_exact and p_outcome converted to decimals.",
            "Results rows are only written where actual_score is present in the SmartBet grid CSV.",
            "Use the baseline/results files with scripts/run_calibration_diagnostics.py for calibration.",
        ],
    }
    return baseline, results, detail, summary


def main() -> int:
    args = build_parser().parse_args()
    patterns = args.smartbet_grid_glob or ["inputs/smartbet_grids/*.csv", "inputs/*smartbet*.csv"]
    files = collect_files(args.smartbet_grid_csv, patterns)
    data = read_grids(files)
    data = dedupe_grid_rows(data, args.dedupe)
    baseline, results, detail, summary = build_outputs(data)
    summary["input_files"] = [str(path) for path in files]
    summary["dedupe"] = args.dedupe

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    baseline_path = out_dir / "smartbet_predictions_baseline.csv"
    results_path = out_dir / "smartbet_results_to_date.csv"
    detail_path = out_dir / "smartbet_grid_calibration_detail.csv"
    summary_path = out_dir / "smartbet_grid_calibration_summary.json"

    baseline.to_csv(baseline_path, index=False)
    results.to_csv(results_path, index=False)
    detail.to_csv(detail_path, index=False)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))
    print(f"Wrote {baseline_path}")
    print(f"Wrote {results_path}")
    print(f"Wrote {detail_path}")
    print(f"Wrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
