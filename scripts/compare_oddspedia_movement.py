from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare current Oddspedia outputs against the previous archived snapshot.")
    parser.add_argument("--history-dir", default="outputs/oddspedia_probability_extract/history")
    parser.add_argument("--summary-csv", default="inputs/smartbet_grids/oddspedia_probability_summary_auto.csv")
    parser.add_argument("--shape-csv", default="inputs/smartbet_grids/oddspedia_score_shape_features.csv")
    parser.add_argument("--comparison-csv", default="outputs/oddspedia_pick_validation/oddspedia_pick_comparison.csv")
    parser.add_argument("--ev-recommendations-csv", default="outputs/oddspedia_pick_validation/oddspedia_ev_recommendations.csv")
    parser.add_argument("--out-csv", default="outputs/oddspedia_pick_validation/oddspedia_movement_vs_previous.csv")
    parser.add_argument("--out-summary-json", default="outputs/oddspedia_pick_validation/oddspedia_movement_summary.json")
    parser.add_argument("--movement-threshold-pct", type=float, default=2.0)
    parser.add_argument("--ev-movement-threshold", type=float, default=0.10)
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


def load_csv(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(p).fillna("")
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def latest_snapshot_dir(history_dir: str | Path) -> Path | None:
    root = Path(history_dir)
    if not root.exists():
        return None
    dirs = [p for p in root.iterdir() if p.is_dir()]
    if not dirs:
        return None
    return sorted(dirs, key=lambda p: p.name)[-1]


def by_mid(frame: pd.DataFrame) -> dict[str, pd.Series]:
    if frame.empty or "match_id" not in frame.columns:
        return {}
    return {txt(r.get("match_id")): r for _, r in frame.iterrows() if txt(r.get("match_id"))}


def delta(new: Any, old: Any) -> float | None:
    nf = to_float(new)
    of = to_float(old)
    if nf is None or of is None:
        return None
    return nf - of


def changed_text(new: Any, old: Any) -> bool:
    return txt(new) != txt(old)


def movement_row(mid: str, current: dict[str, dict[str, pd.Series]], previous: dict[str, dict[str, pd.Series]], args: argparse.Namespace) -> dict[str, Any]:
    cs = current["summary"].get(mid)
    ps = previous["summary"].get(mid)
    cc = current["comparison"].get(mid)
    pc = previous["comparison"].get(mid)
    cshape = current["shape"].get(mid)
    pshape = previous["shape"].get(mid)
    cev = current["ev"].get(mid)
    pev = previous["ev"].get(mid)

    def get(row: pd.Series | None, col: str) -> Any:
        return row.get(col) if row is not None else ""

    winner_home_delta = delta(get(cs, "winner_home_pct"), get(ps, "winner_home_pct"))
    winner_draw_delta = delta(get(cs, "winner_draw_pct"), get(ps, "winner_draw_pct"))
    winner_away_delta = delta(get(cs, "winner_away_pct"), get(ps, "winner_away_pct"))
    locked_prob_delta = delta(get(cc, "locked_pick_probability_pct"), get(pc, "locked_pick_probability_pct"))
    btts_delta = delta(get(cshape, "btts_probability_pct"), get(pshape, "btts_probability_pct"))
    over_2_5_delta = delta(get(cshape, "over_2_5_probability_pct"), get(pshape, "over_2_5_probability_pct"))
    under_2_5_delta = delta(get(cshape, "under_2_5_probability_pct"), get(pshape, "under_2_5_probability_pct"))
    ev_gap_delta = delta(get(cev, "ev_gap_vs_locked"), get(pev, "ev_gap_vs_locked"))

    current_review = txt(get(cev, "review_flag")).lower() in {"true", "1", "yes"} or txt(get(cc, "action")) in {
        "same_outcome_review",
        "strong_same_outcome_review",
        "market_outcome_conflict_review",
        "review_missing_locked_score",
    }
    previous_review = txt(get(pev, "review_flag")).lower() in {"true", "1", "yes"} or txt(get(pc, "action")) in {
        "same_outcome_review",
        "strong_same_outcome_review",
        "market_outcome_conflict_review",
        "review_missing_locked_score",
    }

    movement_flags = []
    for name, value in [
        ("winner_home", winner_home_delta),
        ("winner_draw", winner_draw_delta),
        ("winner_away", winner_away_delta),
        ("locked_probability", locked_prob_delta),
        ("btts", btts_delta),
        ("over_2_5", over_2_5_delta),
        ("under_2_5", under_2_5_delta),
    ]:
        if value is not None and abs(value) >= args.movement_threshold_pct:
            movement_flags.append(name)
    if ev_gap_delta is not None and abs(ev_gap_delta) >= args.ev_movement_threshold:
        movement_flags.append("ev_gap")
    if changed_text(get(cs, "modal_correct_score"), get(ps, "modal_correct_score")):
        movement_flags.append("modal_score")
    if changed_text(get(cev, "best_ev_scoreline"), get(pev, "best_ev_scoreline")):
        movement_flags.append("ev_recommendation")
    if current_review and not previous_review:
        movement_flags.append("new_conflict_flag")

    return {
        "match_id": mid,
        "commence_time": txt(get(cs, "commence_time")) or txt(get(cc, "commence_time")) or txt(get(cev, "commence_time")),
        "home_team": txt(get(cs, "home_team")) or txt(get(cc, "home_team")) or txt(get(cev, "home_team")),
        "away_team": txt(get(cs, "away_team")) or txt(get(cc, "away_team")) or txt(get(cev, "away_team")),
        "previous_modal_score": txt(get(ps, "modal_correct_score")),
        "current_modal_score": txt(get(cs, "modal_correct_score")),
        "modal_score_changed": changed_text(get(cs, "modal_correct_score"), get(ps, "modal_correct_score")),
        "previous_locked_pick_probability_pct": get(pc, "locked_pick_probability_pct"),
        "current_locked_pick_probability_pct": get(cc, "locked_pick_probability_pct"),
        "locked_pick_probability_delta_pct": locked_prob_delta,
        "winner_home_delta_pct": winner_home_delta,
        "winner_draw_delta_pct": winner_draw_delta,
        "winner_away_delta_pct": winner_away_delta,
        "btts_delta_pct": btts_delta,
        "over_2_5_delta_pct": over_2_5_delta,
        "under_2_5_delta_pct": under_2_5_delta,
        "previous_best_ev_scoreline": txt(get(pev, "best_ev_scoreline")),
        "current_best_ev_scoreline": txt(get(cev, "best_ev_scoreline")),
        "ev_recommendation_changed": changed_text(get(cev, "best_ev_scoreline"), get(pev, "best_ev_scoreline")),
        "previous_ev_gap_vs_locked": get(pev, "ev_gap_vs_locked"),
        "current_ev_gap_vs_locked": get(cev, "ev_gap_vs_locked"),
        "ev_gap_delta": ev_gap_delta,
        "previous_review_flag": previous_review,
        "current_review_flag": current_review,
        "new_conflict_flag": current_review and not previous_review,
        "movement_flag": bool(movement_flags),
        "movement_reasons": ";".join(movement_flags),
    }


def main() -> int:
    args = build_parser().parse_args()
    previous_dir = latest_snapshot_dir(args.history_dir)
    out_csv = Path(args.out_csv)
    out_json = Path(args.out_summary_json)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)

    if previous_dir is None:
        out = pd.DataFrame()
        payload = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "warning": "No archived Oddspedia snapshot available for movement comparison.",
            "movement_count": 0,
            "out_csv": str(out_csv),
        }
        out.to_csv(out_csv, index=False)
        out_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(f"WARNING: {payload['warning']}")
        print(json.dumps(payload, indent=2, default=str))
        return 0

    current = {
        "summary": by_mid(load_csv(args.summary_csv)),
        "shape": by_mid(load_csv(args.shape_csv)),
        "comparison": by_mid(load_csv(args.comparison_csv)),
        "ev": by_mid(load_csv(args.ev_recommendations_csv)),
    }
    previous = {
        "summary": by_mid(load_csv(previous_dir / Path(args.summary_csv).name)),
        "shape": by_mid(load_csv(previous_dir / Path(args.shape_csv).name)),
        "comparison": by_mid(load_csv(previous_dir / Path(args.comparison_csv).name)),
        "ev": by_mid(load_csv(previous_dir / Path(args.ev_recommendations_csv).name)),
    }
    mids = sorted(set().union(*[set(v.keys()) for v in current.values()], *[set(v.keys()) for v in previous.values()]))
    rows = [movement_row(mid, current, previous, args) for mid in mids]
    out = pd.DataFrame(rows)
    out.to_csv(out_csv, index=False)
    movement_count = int(out["movement_flag"].sum()) if not out.empty else 0
    new_conflict_count = int(out["new_conflict_flag"].sum()) if not out.empty else 0
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "previous_snapshot_dir": str(previous_dir),
        "match_count": int(len(out)),
        "movement_count": movement_count,
        "new_conflict_count": new_conflict_count,
        "movement_reason_counts": out["movement_reasons"].value_counts(dropna=False).to_dict() if not out.empty else {},
        "out_csv": str(out_csv),
    }
    out_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    if movement_count:
        print(f"WARNING: Oddspedia movement found {movement_count} match(es) with meaningful changes.")
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
