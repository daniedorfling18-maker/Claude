from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


PICK_COLUMNS = [
    "locked_pick",
    "daily_robust_pick",
    "robust_locked_pick",
    "private_chase_scoreline",
    "final_pick",
    "recommended_scoreline",
    "pick",
    "scoreline",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Join Oddspedia final results to Superbru picks and produce backtest metrics."
    )
    parser.add_argument("--results-csv", default="outputs/backtesting/oddspedia_results_backfill.csv")
    parser.add_argument("--picks-csv", action="append", default=["outputs/final_locked_picks/superbru_final_card.csv"])
    parser.add_argument("--oddspedia-comparison-csv", default="outputs/oddspedia_pick_validation/oddspedia_pick_comparison.csv")
    parser.add_argument("--out-csv", default="outputs/backtesting/superbru_pick_backtest.csv")
    parser.add_argument("--out-summary-json", default="outputs/backtesting/backtest_summary.json")
    parser.add_argument("--exact-points", type=float, default=3.0)
    parser.add_argument("--margin-points", type=float, default=1.5)
    parser.add_argument("--result-points", type=float, default=1.0)
    parser.add_argument("--only-completed", action="store_true", default=True)
    parser.add_argument("--include-incomplete", dest="only_completed", action="store_false")
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def slugify(value: Any) -> str:
    text = txt(value).lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def match_key(home: Any, away: Any) -> str:
    return f"{slugify(home)}-{slugify(away)}"


def parse_score(score: Any) -> tuple[int | None, int | None]:
    m = re.search(r"(\d+)\s*-\s*(\d+)", txt(score))
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def outcome_from_goals(home_goals: int | None, away_goals: int | None) -> str:
    if home_goals is None or away_goals is None:
        return "unknown"
    if home_goals > away_goals:
        return "home"
    if home_goals < away_goals:
        return "away"
    return "draw"


def outcome_from_score(score: Any) -> str:
    h, a = parse_score(score)
    return outcome_from_goals(h, a)


def goal_diff(home_goals: int | None, away_goals: int | None) -> int | None:
    if home_goals is None or away_goals is None:
        return None
    return home_goals - away_goals


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return txt(value).lower() in {"1", "true", "yes", "y"}


def find_pick_column(frame: pd.DataFrame) -> str:
    for col in PICK_COLUMNS:
        if col in frame.columns:
            return col
    raise ValueError(f"Could not find pick column. Expected one of: {', '.join(PICK_COLUMNS)}")


def load_picks(paths: list[str]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path_text in paths:
        path = Path(path_text)
        if not path.exists():
            print(f"Skipping missing picks file: {path}")
            continue
        frame = pd.read_csv(path).fillna("")
        pick_col = find_pick_column(frame)
        if "match_id" not in frame.columns:
            frame["match_id"] = frame.apply(lambda r: match_key(r.get("home_team"), r.get("away_team")), axis=1)
        frame["pick_source_file"] = str(path)
        frame["pick_column"] = pick_col
        frame["locked_pick"] = frame[pick_col].map(txt)
        frames.append(frame)
    if not frames:
        raise FileNotFoundError("No readable picks files were found.")
    picks = pd.concat(frames, ignore_index=True, sort=False).fillna("")
    return picks


def load_results(path: Path, only_completed: bool) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing results CSV: {path}")
    results = pd.read_csv(path).fillna("")
    if "match_id" not in results.columns:
        results["match_id"] = results.apply(lambda r: match_key(r.get("home_team"), r.get("away_team")), axis=1)
    if only_completed and "is_completed" in results.columns:
        results = results[results["is_completed"].map(to_bool)].copy()
    return results


def score_points(pick: str, actual: str, exact_points: float, margin_points: float, result_points: float) -> tuple[float, bool, bool, bool]:
    ph, pa = parse_score(pick)
    ah, aa = parse_score(actual)
    if ph is None or pa is None or ah is None or aa is None:
        return 0.0, False, False, False
    exact_hit = ph == ah and pa == aa
    pick_outcome = outcome_from_goals(ph, pa)
    actual_outcome = outcome_from_goals(ah, aa)
    outcome_hit = pick_outcome == actual_outcome and actual_outcome != "unknown"
    margin_hit = outcome_hit and goal_diff(ph, pa) == goal_diff(ah, aa)
    if exact_hit:
        return exact_points, True, True, True
    if margin_hit:
        return margin_points, False, True, True
    if outcome_hit:
        return result_points, False, True, False
    return 0.0, False, False, False


def add_oddspedia_columns(out: pd.DataFrame, comparison_csv: str, args: argparse.Namespace) -> pd.DataFrame:
    path = Path(comparison_csv)
    if not path.exists() or out.empty:
        out["oddspedia_modal_pick"] = ""
        out["oddspedia_modal_points"] = None
        out["oddspedia_modal_exact_hit"] = None
        out["oddspedia_modal_outcome_hit"] = None
        out["oddspedia_modal_margin_hit"] = None
        return out
    comp = pd.read_csv(path).fillna("")
    if "match_id" not in comp.columns:
        comp["match_id"] = comp.apply(lambda r: match_key(r.get("home_team"), r.get("away_team")), axis=1)
    comp_cols = [c for c in ["match_id", "oddspedia_top1_score", "oddspedia_top1_pct", "modal_correct_score", "modal_correct_score_pct"] if c in comp.columns]
    comp = comp[comp_cols].drop_duplicates("match_id")
    out = out.merge(comp, on="match_id", how="left")
    modal = []
    modal_points = []
    modal_exact = []
    modal_outcome = []
    modal_margin = []
    for _, row in out.iterrows():
        pick = txt(row.get("oddspedia_top1_score")) or txt(row.get("modal_correct_score"))
        actual = txt(row.get("actual_score"))
        points, exact, outcome_hit, margin = score_points(
            pick, actual, args.exact_points, args.margin_points, args.result_points
        )
        modal.append(pick)
        modal_points.append(points if pick and actual else None)
        modal_exact.append(exact if pick and actual else None)
        modal_outcome.append(outcome_hit if pick and actual else None)
        modal_margin.append(margin if pick and actual else None)
    out["oddspedia_modal_pick"] = modal
    out["oddspedia_modal_points"] = modal_points
    out["oddspedia_modal_exact_hit"] = modal_exact
    out["oddspedia_modal_outcome_hit"] = modal_outcome
    out["oddspedia_modal_margin_hit"] = modal_margin
    return out


def build_backtest(args: argparse.Namespace) -> tuple[pd.DataFrame, dict[str, Any]]:
    results = load_results(Path(args.results_csv), args.only_completed)
    picks = load_picks(args.picks_csv)

    rows: list[dict[str, Any]] = []
    for _, pick in picks.iterrows():
        mid = txt(pick.get("match_id")) or match_key(pick.get("home_team"), pick.get("away_team"))
        home = txt(pick.get("home_team"))
        away = txt(pick.get("away_team"))
        result_hits = results[results["match_id"].astype(str).eq(mid)]
        if result_hits.empty and home and away:
            result_hits = results[(results.get("home_team", "").astype(str).eq(home)) & (results.get("away_team", "").astype(str).eq(away))]
        if result_hits.empty:
            continue
        result = result_hits.iloc[0]
        actual_score = txt(result.get("actual_score"))
        locked_pick = txt(pick.get("locked_pick"))
        points, exact_hit, outcome_hit, margin_hit = score_points(
            locked_pick, actual_score, args.exact_points, args.margin_points, args.result_points
        )
        ph, pa = parse_score(locked_pick)
        ah, aa = parse_score(actual_score)
        rows.append({
            "match_id": mid,
            "commence_time": txt(pick.get("commence_time")) or txt(result.get("commence_time")),
            "home_team": home or txt(result.get("home_team")),
            "away_team": away or txt(result.get("away_team")),
            "pick_source_file": txt(pick.get("pick_source_file")),
            "pick_column": txt(pick.get("pick_column")),
            "locked_pick": locked_pick,
            "actual_score": actual_score,
            "actual_home_goals": ah,
            "actual_away_goals": aa,
            "pick_home_goals": ph,
            "pick_away_goals": pa,
            "actual_outcome": outcome_from_score(actual_score),
            "pick_outcome": outcome_from_score(locked_pick),
            "exact_hit": exact_hit,
            "outcome_hit": outcome_hit,
            "margin_hit": margin_hit,
            "superbru_points_estimate": points,
            "result_status": txt(result.get("status")),
            "result_score_source": txt(result.get("score_source")),
            "result_source_url": txt(result.get("source_url")),
        })

    out = pd.DataFrame(rows)
    out = add_oddspedia_columns(out, args.oddspedia_comparison_csv, args)

    summary: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "results_csv": args.results_csv,
        "picks_csv": args.picks_csv,
        "oddspedia_comparison_csv": args.oddspedia_comparison_csv,
        "scoring": {
            "exact_points": args.exact_points,
            "margin_points": args.margin_points,
            "result_points": args.result_points,
            "note": "Configurable Superbru-style estimate: exact > same goal-difference outcome > outcome.",
        },
        "result_rows_available": int(len(results)),
        "pick_rows_available": int(len(picks)),
        "completed_matches_with_picks": int(len(out)),
    }
    if not out.empty:
        summary.update({
            "total_points_estimate": float(out["superbru_points_estimate"].sum()),
            "average_points_estimate": float(out["superbru_points_estimate"].mean()),
            "exact_hit_rate": float(out["exact_hit"].mean()),
            "outcome_hit_rate": float(out["outcome_hit"].mean()),
            "margin_hit_rate": float(out["margin_hit"].mean()),
        })
        if "oddspedia_modal_points" in out.columns:
            modal_points = pd.to_numeric(out["oddspedia_modal_points"], errors="coerce")
            if modal_points.notna().any():
                summary["oddspedia_modal_total_points_estimate"] = float(modal_points.sum())
                summary["oddspedia_modal_average_points_estimate"] = float(modal_points.mean())
                summary["points_delta_vs_oddspedia_modal"] = float(out["superbru_points_estimate"].sum() - modal_points.sum())
    return out, summary


def main() -> int:
    args = build_parser().parse_args()
    out, summary = build_backtest(args)
    out_csv = Path(args.out_csv)
    out_json = Path(args.out_summary_json)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_csv, index=False)
    summary["out_csv"] = str(out_csv)
    out_json.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
