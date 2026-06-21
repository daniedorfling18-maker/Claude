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
    "daily_robust_pick",
    "robust_locked_pick",
    "locked_pick",
    "private_chase_scoreline",
    "final_pick",
    "recommended_scoreline",
    "pick",
    "scoreline",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Calculate Superbru expected points for Oddspedia correct-score candidates.")
    parser.add_argument("--locked-picks-csv", default="outputs/final_locked_picks/superbru_final_card.csv")
    parser.add_argument("--oddspedia-grid-csv", default="inputs/smartbet_grids/oddspedia_probability_grids_auto.csv")
    parser.add_argument("--markets-summary-csv", default="inputs/smartbet_grids/oddspedia_markets_summary_auto.csv")
    parser.add_argument("--out-by-score-csv", default="outputs/oddspedia_pick_validation/oddspedia_superbru_ev_by_score.csv")
    parser.add_argument("--out-recommendations-csv", default="outputs/oddspedia_pick_validation/oddspedia_ev_recommendations.csv")
    parser.add_argument("--out-summary-json", default="outputs/oddspedia_pick_validation/oddspedia_superbru_ev_summary.json")
    parser.add_argument("--min-ev-gap-review", type=float, default=0.15)
    parser.add_argument("--min-ev-gap-strong", type=float, default=0.30)
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def to_float(value: Any) -> float | None:
    try:
        s = txt(value).replace("%", "")
        if not s:
            return None
        return float(s)
    except Exception:
        return None


def to_int(value: Any) -> int | None:
    s = txt(value)
    if re.fullmatch(r"\d+", s):
        return int(s)
    # Handle float strings like "1.0" written by pandas when ints mix with NaN
    try:
        f = float(s)
        if f == int(f) and f >= 0:
            return int(f)
    except (ValueError, OverflowError):
        pass
    return None


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


def outcome_from_goals(h: int | None, a: int | None) -> str:
    if h is None or a is None:
        return "unknown"
    if h > a:
        return "home"
    if h < a:
        return "away"
    return "draw"


def outcome_from_score(score: Any) -> str:
    h, a = parse_score(score)
    return outcome_from_goals(h, a)


def outcome_from_row(row: pd.Series) -> str:
    bucket = txt(row.get("score_bucket"))
    key = txt(row.get("score_key"))
    if bucket == "other_home_win" or key == "Other_1":
        return "home"
    if bucket == "other_draw" or key == "Other_X":
        return "draw"
    if bucket == "other_away_win" or key == "Other_2":
        return "away"
    h = to_int(row.get("home_goals"))
    a = to_int(row.get("away_goals"))
    return outcome_from_goals(h, a)


def is_exact_row(row: pd.Series) -> bool:
    return txt(row.get("score_bucket")) == "exact" or bool(re.fullmatch(r"\d+-\d+", txt(row.get("score_key"))))


def is_close_score(candidate_h: int, candidate_a: int, actual_h: int, actual_a: int) -> bool:
    if candidate_h == actual_h and candidate_a == actual_a:
        return False
    if outcome_from_goals(candidate_h, candidate_a) != outcome_from_goals(actual_h, actual_a):
        return False
    return abs(candidate_h - actual_h) <= 1 and abs(candidate_a - actual_a) <= 1


def find_pick_column(frame: pd.DataFrame) -> str:
    for col in PICK_COLUMNS:
        if col in frame.columns:
            return col
    raise ValueError(f"Could not find pick column. Expected one of: {', '.join(PICK_COLUMNS)}")


def load_csv(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(p).fillna("")
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def ensure_match_id(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    out = frame.copy()
    if "match_id" not in out.columns:
        out["match_id"] = out.apply(lambda r: match_key(r.get("home_team"), r.get("away_team")), axis=1)
    return out


def probability_for_score(grid: pd.DataFrame, score: str) -> float | None:
    if grid.empty or "score_key" not in grid.columns:
        return None
    hits = grid[grid["score_key"].astype(str).eq(score)]
    if hits.empty:
        return None
    return to_float(hits.iloc[0].get("probability_pct"))


def expected_points_for_candidate(grid: pd.DataFrame, candidate_score: str) -> dict[str, Any]:
    cand_h, cand_a = parse_score(candidate_score)
    cand_outcome = outcome_from_goals(cand_h, cand_a)
    exact_probability_pct = probability_for_score(grid, candidate_score)
    exact_probability_pct = 0.0 if exact_probability_pct is None else exact_probability_pct
    exact_probability_points_pct = 0.0
    close_probability_pct = 0.0
    result_only_probability_pct = 0.0

    if cand_h is None or cand_a is None:
        return {
            "candidate_scoreline": candidate_score,
            "candidate_outcome": "unknown",
            "exact_probability_pct": None,
            "close_probability_pct": None,
            "result_only_probability_pct": None,
            "exact_ev_points": None,
            "close_ev_points": None,
            "result_only_ev_points": None,
            "total_expected_superbru_points": None,
        }

    for _, row in grid.iterrows():
        p_pct = to_float(row.get("probability_pct"))
        if p_pct is None:
            continue
        actual_outcome = outcome_from_row(row)
        if actual_outcome != cand_outcome:
            continue
        if is_exact_row(row):
            actual_h = to_int(row.get("home_goals"))
            actual_a = to_int(row.get("away_goals"))
            if actual_h is None or actual_a is None:
                continue
            if actual_h == cand_h and actual_a == cand_a:
                exact_probability_points_pct += p_pct
            elif is_close_score(cand_h, cand_a, actual_h, actual_a):
                close_probability_pct += p_pct
            else:
                result_only_probability_pct += p_pct
        else:
            result_only_probability_pct += p_pct

    exact_ev = 3.0 * exact_probability_points_pct / 100.0
    close_ev = 1.5 * close_probability_pct / 100.0
    result_ev = result_only_probability_pct / 100.0
    return {
        "candidate_scoreline": candidate_score,
        "candidate_outcome": cand_outcome,
        "exact_probability_pct": exact_probability_pct,
        "close_probability_pct": close_probability_pct,
        "result_only_probability_pct": result_only_probability_pct,
        "exact_ev_points": exact_ev,
        "close_ev_points": close_ev,
        "result_only_ev_points": result_ev,
        "total_expected_superbru_points": exact_ev + close_ev + result_ev,
    }


def candidate_scores(grid: pd.DataFrame) -> list[str]:
    if grid.empty or "score_key" not in grid.columns:
        return []
    exact = grid[grid.apply(is_exact_row, axis=1)].copy()
    exact["_p"] = exact["probability_pct"].map(to_float).fillna(-1)
    exact = exact.sort_values(["_p", "score_key"], ascending=[False, True])
    return [txt(v) for v in exact["score_key"].tolist() if re.fullmatch(r"\d+-\d+", txt(v))]


def market_consensus_direction(p_home: float | None, p_draw: float | None, p_away: float | None) -> str:
    vals = [(p_home, "home"), (p_draw, "draw"), (p_away, "away")]
    valid = [(v, lbl) for v, lbl in vals if v is not None]
    return max(valid, key=lambda x: x[0])[1] if valid else "unknown"


def build_outputs(args: argparse.Namespace) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    locked = ensure_match_id(load_csv(args.locked_picks_csv))
    grid = ensure_match_id(load_csv(args.oddspedia_grid_csv))
    markets = ensure_match_id(load_csv(args.markets_summary_csv))
    if locked.empty or grid.empty:
        payload = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "locked_picks_csv": args.locked_picks_csv,
            "oddspedia_grid_csv": args.oddspedia_grid_csv,
            "match_count": 0,
            "warning": "Missing or empty locked picks or Oddspedia grid CSV. Wrote empty outputs.",
        }
        return pd.DataFrame(), pd.DataFrame(), payload

    pick_col = find_pick_column(locked)
    by_score_rows: list[dict[str, Any]] = []
    rec_rows: list[dict[str, Any]] = []

    for _, pick_row in locked.iterrows():
        mid = txt(pick_row.get("match_id")) or match_key(pick_row.get("home_team"), pick_row.get("away_team"))
        home = txt(pick_row.get("home_team"))
        away = txt(pick_row.get("away_team"))
        locked_pick = txt(pick_row.get(pick_col))
        match_grid = grid[grid["match_id"].astype(str).eq(mid)].copy()
        if match_grid.empty and home and away:
            match_grid = grid[(grid.get("home_team", "").astype(str).eq(home)) & (grid.get("away_team", "").astype(str).eq(away))].copy()
        mframe = markets[markets["match_id"].astype(str).eq(mid)] if not markets.empty else pd.DataFrame()
        market_p_home = market_p_draw = market_p_away = None
        if not mframe.empty:
            mr = mframe.iloc[0]
            market_p_home = to_float(mr.get("p_home_win"))
            market_p_draw = to_float(mr.get("p_draw"))
            market_p_away = to_float(mr.get("p_away_win"))
        m_consensus = market_consensus_direction(market_p_home, market_p_draw, market_p_away)

        candidates = candidate_scores(match_grid)
        match_score_rows: list[dict[str, Any]] = []
        for cand in candidates:
            ev = expected_points_for_candidate(match_grid, cand)
            row = {
                "match_id": mid,
                "commence_time": txt(pick_row.get("commence_time")),
                "home_team": home,
                "away_team": away,
                **ev,
            }
            match_score_rows.append(row)

        if match_score_rows:
            score_df = pd.DataFrame(match_score_rows).sort_values("total_expected_superbru_points", ascending=False)
            score_df.insert(4, "ev_rank", range(1, len(score_df) + 1))
            by_score_rows.extend(score_df.to_dict(orient="records"))
            best = score_df.iloc[0].to_dict()
        else:
            best = {}

        locked_ev = expected_points_for_candidate(match_grid, locked_pick) if not match_grid.empty else {}
        current_locked_ev = locked_ev.get("total_expected_superbru_points")
        best_ev = best.get("total_expected_superbru_points")
        ev_gap = None if current_locked_ev is None or best_ev is None else float(best_ev) - float(current_locked_ev)
        locked_outcome = outcome_from_score(locked_pick)
        best_score = txt(best.get("candidate_scoreline"))
        best_outcome = outcome_from_score(best_score)
        same_outcome = bool(locked_outcome != "unknown" and best_outcome != "unknown" and locked_outcome == best_outcome)

        review_flag = False
        review_level = "none"
        review_reason = ""
        if match_grid.empty:
            review_level = "no_grid"
            review_reason = "No Oddspedia grid available for this match."
        elif not locked_pick:
            review_flag = True
            review_level = "missing_locked_pick"
            review_reason = "Locked pick is blank."
        elif ev_gap is not None and ev_gap >= args.min_ev_gap_strong:
            review_flag = True
            review_level = "strong_ev_review"
            review_reason = "Best Oddspedia EV scoreline materially exceeds the locked pick EV."
        elif ev_gap is not None and ev_gap >= args.min_ev_gap_review:
            review_flag = True
            review_level = "ev_review"
            review_reason = "Best Oddspedia EV scoreline exceeds the locked pick EV."

        rec_rows.append(
            {
                "match_id": mid,
                "commence_time": txt(pick_row.get("commence_time")),
                "home_team": home,
                "away_team": away,
                "pick_column": pick_col,
                "locked_pick": locked_pick,
                "locked_pick_outcome": locked_outcome,
                "locked_pick_exact_probability_pct": locked_ev.get("exact_probability_pct"),
                "locked_pick_close_probability_pct": locked_ev.get("close_probability_pct"),
                "locked_pick_result_only_probability_pct": locked_ev.get("result_only_probability_pct"),
                "current_locked_pick_ev": current_locked_ev,
                "best_ev_scoreline": best_score,
                "best_ev_scoreline_outcome": best_outcome,
                "best_ev_exact_probability_pct": best.get("exact_probability_pct"),
                "best_ev_expected_points": best_ev,
                "ev_gap_vs_locked": ev_gap,
                "same_outcome_flag": same_outcome,
                "market_p_home_win": market_p_home,
                "market_p_draw": market_p_draw,
                "market_p_away_win": market_p_away,
                "market_consensus_direction": m_consensus,
                "review_flag": review_flag,
                "review_level": review_level,
                "review_reason": review_reason,
            }
        )

    by_score = pd.DataFrame(by_score_rows)
    recs = pd.DataFrame(rec_rows)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "locked_picks_csv": args.locked_picks_csv,
        "oddspedia_grid_csv": args.oddspedia_grid_csv,
        "match_count": int(len(recs)),
        "score_candidate_count": int(len(by_score)),
        "review_count": int(recs["review_flag"].sum()) if not recs.empty and "review_flag" in recs.columns else 0,
        "review_level_counts": recs["review_level"].value_counts(dropna=False).to_dict() if not recs.empty else {},
        "close_score_rule": "same outcome and both team-goal estimates within one goal, excluding exact",
    }
    return by_score, recs, payload


def main() -> int:
    args = build_parser().parse_args()
    by_score, recs, payload = build_outputs(args)
    out_by_score = Path(args.out_by_score_csv)
    out_recs = Path(args.out_recommendations_csv)
    out_summary = Path(args.out_summary_json)
    out_by_score.parent.mkdir(parents=True, exist_ok=True)
    out_recs.parent.mkdir(parents=True, exist_ok=True)
    out_summary.parent.mkdir(parents=True, exist_ok=True)
    by_score.to_csv(out_by_score, index=False)
    recs.to_csv(out_recs, index=False)
    payload["out_by_score_csv"] = str(out_by_score)
    payload["out_recommendations_csv"] = str(out_recs)
    out_summary.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    if payload.get("warning"):
        print(f"WARNING: {payload['warning']}")
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
