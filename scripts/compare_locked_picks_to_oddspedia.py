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
    parser = argparse.ArgumentParser(description="Compare locked Superbru score picks against Oddspedia correct-score probability grids.")
    parser.add_argument("--locked-picks-csv", default="outputs/final_locked_picks/superbru_final_card.csv")
    parser.add_argument("--oddspedia-grid-csv", default="inputs/smartbet_grids/oddspedia_probability_grids_auto.csv")
    parser.add_argument("--oddspedia-summary-csv", default="inputs/smartbet_grids/oddspedia_probability_summary_auto.csv")
    parser.add_argument("--markets-summary-csv", default="inputs/smartbet_grids/oddspedia_markets_summary_auto.csv")
    parser.add_argument("--out-csv", default="outputs/oddspedia_pick_validation/oddspedia_pick_comparison.csv")
    parser.add_argument("--out-json", default="outputs/oddspedia_pick_validation/oddspedia_pick_comparison_summary.json")
    parser.add_argument("--min-prob-gap-review", type=float, default=2.0)
    parser.add_argument("--min-prob-gap-strong", type=float, default=4.0)
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


def outcome_from_score(score: Any) -> str:
    h, a = parse_score(score)
    if h is None or a is None:
        return "unknown"
    if h > a:
        return "home"
    if h < a:
        return "away"
    return "draw"


def probability_for_score(grid: pd.DataFrame, score: str) -> float | None:
    if grid.empty:
        return None
    hits = grid[grid["score_key"].astype(str) == score]
    if hits.empty:
        return None
    return to_float(hits.iloc[0].get("probability_pct"))


def best_exact_rows(grid: pd.DataFrame, n: int = 3) -> pd.DataFrame:
    if grid.empty:
        return grid
    exact = grid[grid.get("score_bucket", "").astype(str).eq("exact")].copy()
    if exact.empty:
        return exact
    exact["_p"] = exact["probability_pct"].map(to_float).fillna(-1)
    return exact.sort_values("_p", ascending=False).head(n)


def find_pick_column(frame: pd.DataFrame) -> str:
    for col in PICK_COLUMNS:
        if col in frame.columns:
            return col
    raise ValueError(f"Could not find pick column. Expected one of: {', '.join(PICK_COLUMNS)}")


def load_locked(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing locked picks CSV: {path}")
    frame = pd.read_csv(path).fillna("")
    if "match_id" not in frame.columns:
        frame["match_id"] = frame.apply(lambda r: match_key(r.get("home_team"), r.get("away_team")), axis=1)
    return frame


def market_consensus_direction(p_home: float | None, p_draw: float | None, p_away: float | None) -> str:
    vals = [(p_home, "home"), (p_draw, "draw"), (p_away, "away")]
    valid = [(v, lbl) for v, lbl in vals if v is not None]
    if not valid:
        return "unknown"
    return max(valid, key=lambda x: x[0])[1]


def compare(args: argparse.Namespace) -> tuple[pd.DataFrame, dict[str, Any]]:
    locked = load_locked(Path(args.locked_picks_csv))
    grid = pd.read_csv(args.oddspedia_grid_csv).fillna("")
    summary = pd.read_csv(args.oddspedia_summary_csv).fillna("") if Path(args.oddspedia_summary_csv).exists() else pd.DataFrame()
    markets = pd.read_csv(args.markets_summary_csv).fillna("") if Path(args.markets_summary_csv).exists() else pd.DataFrame()
    if "match_id" not in grid.columns:
        grid["match_id"] = grid.apply(lambda r: match_key(r.get("home_team"), r.get("away_team")), axis=1)
    if not summary.empty and "match_id" not in summary.columns:
        summary["match_id"] = summary.apply(lambda r: match_key(r.get("home_team"), r.get("away_team")), axis=1)
    if not markets.empty and "match_id" not in markets.columns:
        markets["match_id"] = markets.apply(lambda r: match_key(r.get("home_team"), r.get("away_team")), axis=1)

    pick_col = find_pick_column(locked)
    rows: list[dict[str, Any]] = []

    for _, pick_row in locked.iterrows():
        mid = txt(pick_row.get("match_id")) or match_key(pick_row.get("home_team"), pick_row.get("away_team"))
        home = txt(pick_row.get("home_team"))
        away = txt(pick_row.get("away_team"))
        locked_pick = txt(pick_row.get(pick_col))
        g = grid[grid["match_id"].astype(str).eq(mid)].copy()
        if g.empty and home and away:
            g = grid[(grid.get("home_team", "").astype(str).eq(home)) & (grid.get("away_team", "").astype(str).eq(away))].copy()
        srow = summary[summary["match_id"].astype(str).eq(mid)].head(1) if not summary.empty else pd.DataFrame()
        mrow = markets[markets["match_id"].astype(str).eq(mid)].head(1) if not markets.empty else pd.DataFrame()

        top = best_exact_rows(g, 3)
        p_locked = probability_for_score(g, locked_pick)
        top_scores = []
        top_probs = []
        for _, tr in top.iterrows():
            top_scores.append(txt(tr.get("score_key")))
            top_probs.append(to_float(tr.get("probability_pct")))
        best_score = top_scores[0] if top_scores else ""
        best_prob = top_probs[0] if top_probs else None
        prob_gap = None if p_locked is None or best_prob is None else best_prob - p_locked

        locked_outcome = outcome_from_score(locked_pick)
        best_outcome = outcome_from_score(best_score)
        same_outcome = locked_outcome == best_outcome if locked_outcome != "unknown" and best_outcome != "unknown" else False

        winner_home = winner_draw = winner_away = None
        modal_score = modal_prob = ""
        if not srow.empty:
            sr = srow.iloc[0]
            winner_home = to_float(sr.get("winner_home_pct"))
            winner_draw = to_float(sr.get("winner_draw_pct"))
            winner_away = to_float(sr.get("winner_away_pct"))
            modal_score = txt(sr.get("modal_correct_score"))
            modal_prob = txt(sr.get("modal_correct_score_pct"))

        # Market 100 (1X2) direct probabilities from markets summary
        market_home = market_draw = market_away = None
        market_consensus = "unknown"
        if not mrow.empty:
            mr = mrow.iloc[0]
            market_home = to_float(mr.get("p_home_win"))
            market_draw = to_float(mr.get("p_draw"))
            market_away = to_float(mr.get("p_away_win"))
            market_consensus = market_consensus_direction(market_home, market_draw, market_away)

        action = "keep"
        reason = "locked pick available in grid"
        if g.empty:
            action = "no_grid"
            reason = "no Oddspedia grid for match"
        elif p_locked is None:
            action = "review_missing_locked_score"
            reason = "locked score not present in exact 0-3 grid"
        elif (
            market_consensus != "unknown"
            and locked_outcome != "unknown"
            and locked_outcome != market_consensus
        ):
            action = "market_1x2_conflict"
            reason = f"locked pick outcome ({locked_outcome}) conflicts with 1X2 market consensus ({market_consensus})"
        elif prob_gap is not None and prob_gap >= args.min_prob_gap_strong and same_outcome:
            action = "strong_same_outcome_review"
            reason = "higher-probability same-outcome scoreline exists"
        elif prob_gap is not None and prob_gap >= args.min_prob_gap_review and same_outcome:
            action = "same_outcome_review"
            reason = "moderately higher-probability same-outcome scoreline exists"
        elif prob_gap is not None and prob_gap >= args.min_prob_gap_strong and not same_outcome:
            action = "market_outcome_conflict_review"
            reason = "Oddspedia modal implies different result outcome"

        rows.append(
            {
                "match_id": mid,
                "commence_time": txt(pick_row.get("commence_time")),
                "home_team": home,
                "away_team": away,
                "pick_column": pick_col,
                "locked_pick": locked_pick,
                "locked_pick_probability_pct": p_locked,
                "locked_pick_outcome": locked_outcome,
                "oddspedia_best_score": best_score,
                "oddspedia_best_probability_pct": best_prob,
                "oddspedia_best_outcome": best_outcome,
                "probability_gap_vs_locked_pct": prob_gap,
                "same_outcome_as_locked": same_outcome,
                "oddspedia_top1_score": top_scores[0] if len(top_scores) > 0 else "",
                "oddspedia_top1_pct": top_probs[0] if len(top_probs) > 0 else None,
                "oddspedia_top2_score": top_scores[1] if len(top_scores) > 1 else "",
                "oddspedia_top2_pct": top_probs[1] if len(top_probs) > 1 else None,
                "oddspedia_top3_score": top_scores[2] if len(top_scores) > 2 else "",
                "oddspedia_top3_pct": top_probs[2] if len(top_probs) > 2 else None,
                "winner_home_pct": winner_home,
                "winner_draw_pct": winner_draw,
                "winner_away_pct": winner_away,
                "modal_correct_score": modal_score,
                "modal_correct_score_pct": modal_prob,
                "market_p_home_win": market_home,
                "market_p_draw": market_draw,
                "market_p_away_win": market_away,
                "market_consensus_direction": market_consensus,
                "action": action,
                "reason": reason,
            }
        )

    out = pd.DataFrame(rows)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "locked_picks_csv": args.locked_picks_csv,
        "pick_column": pick_col,
        "oddspedia_grid_csv": args.oddspedia_grid_csv,
        "match_count": len(out),
        "matches_with_grid": int((out["action"] != "no_grid").sum()) if not out.empty else 0,
        "action_counts": out["action"].value_counts(dropna=False).to_dict() if not out.empty else {},
    }
    return out, payload


def main() -> int:
    args = build_parser().parse_args()
    out, payload = compare(args)
    out_csv = Path(args.out_csv)
    out_json = Path(args.out_json)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_csv, index=False)
    payload["out_csv"] = str(out_csv)
    out_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(json.dumps(payload, indent=2, default=str))
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
