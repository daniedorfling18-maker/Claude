"""
build_oddspedia_model_independence.py

Assesses how much the Oddspedia correct-score grid diverges from the
underlying bookmaker market on 1X2, OU2.5, and BTTS — the three
independent signal dimensions available from the scraped data.

Outputs per-match independence class, signal consistency, and whether
the locked pick follows the grid or the market when they disagree.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Score Oddspedia grid independence from bookmaker market signals."
    )
    parser.add_argument(
        "--shape-features-csv",
        default="inputs/smartbet_grids/oddspedia_score_shape_features.csv",
    )
    parser.add_argument(
        "--ev-recommendations-csv",
        default="outputs/oddspedia_pick_validation/oddspedia_ev_recommendations.csv",
    )
    parser.add_argument(
        "--out-csv",
        default="outputs/oddspedia_pick_validation/oddspedia_model_independence.csv",
    )
    parser.add_argument(
        "--out-summary-json",
        default="outputs/oddspedia_pick_validation/oddspedia_model_independence_summary.json",
    )
    parser.add_argument("--mild-threshold", type=float, default=2.0,
                        help="Absolute diff (pp) where grid starts to diverge from market")
    parser.add_argument("--strong-threshold", type=float, default=5.0,
                        help="Absolute diff (pp) flagged as strongly independent")
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
        return float(s) if s else None
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


def classify_independence(abs_diffs: list[float], mild: float, strong: float) -> str:
    if any(d >= strong for d in abs_diffs):
        return "strongly_independent"
    if any(d >= mild for d in abs_diffs):
        return "mildly_independent"
    return "market_aligned"


def signal_consistency(ou_diff: float | None, btts_diff: float | None) -> str:
    """
    market_vs_grid_ou25_diff_pct = market_p_over_2_5 - grid_over_2_5
    Positive: market expects more goals than grid.
    Negative: grid expects more goals than market.
    Same logic for BTTS.
    """
    if ou_diff is None or btts_diff is None:
        return "unknown"
    threshold = 1.0
    if abs(ou_diff) < threshold and abs(btts_diff) < threshold:
        return "both_neutral"
    ou_dir = "more" if ou_diff > 0 else "less"
    btts_dir = "more" if btts_diff > 0 else "less"
    if ou_dir == btts_dir:
        label = "grid_expects_more_goals" if ou_diff < 0 else "grid_expects_fewer_goals"
        return f"consistent_{label}"
    return "inconsistent_ou_vs_btts"


def consensus(home: float | None, draw: float | None, away: float | None) -> str:
    vals = {
        "home": home if home is not None else 0.0,
        "draw": draw if draw is not None else 0.0,
        "away": away if away is not None else 0.0,
    }
    return max(vals, key=vals.get)


def pick_alignment(pick_outcome: str, grid_dir: str, market_dir: str) -> str:
    if not pick_outcome or pick_outcome == "unknown":
        return "unknown"
    if grid_dir == market_dir:
        return "n/a_grid_market_agree"
    if pick_outcome == grid_dir:
        return "grid"
    if pick_outcome == market_dir:
        return "market"
    return "neither"


def max_1x2_diff(home_d: float | None, draw_d: float | None, away_d: float | None) -> float | None:
    vals = [abs(v) for v in [home_d, draw_d, away_d] if v is not None]
    return max(vals) if vals else None


def build_independence(
    features: pd.DataFrame,
    recs: pd.DataFrame,
    mild: float,
    strong: float,
) -> pd.DataFrame:
    if features.empty:
        return pd.DataFrame()

    ev_lookup: dict[str, dict[str, Any]] = {}
    if not recs.empty and "match_id" in recs.columns:
        for _, r in recs.iterrows():
            mid = txt(r.get("match_id"))
            if mid:
                ev_lookup[mid] = r.to_dict()

    rows: list[dict[str, Any]] = []
    for _, row in features.iterrows():
        mid = txt(row.get("match_id"))
        ou_diff = to_float(row.get("market_vs_grid_ou25_diff_pct"))
        btts_diff = to_float(row.get("market_vs_grid_btts_diff_pct"))
        home_diff = to_float(row.get("market_vs_grid_home_diff_pct"))
        draw_diff = to_float(row.get("market_vs_grid_draw_diff_pct"))
        away_diff = to_float(row.get("market_vs_grid_away_diff_pct"))

        mx1x2 = max_1x2_diff(home_diff, draw_diff, away_diff)
        abs_ou = abs(ou_diff) if ou_diff is not None else None
        abs_btts = abs(btts_diff) if btts_diff is not None else None

        all_abs = [v for v in [mx1x2, abs_ou, abs_btts] if v is not None]
        ind_class = classify_independence(all_abs, mild, strong) if all_abs else "unknown"

        sig_con = signal_consistency(ou_diff, btts_diff)

        grid_dir = consensus(
            to_float(row.get("home_win_probability_from_grid_pct")),
            to_float(row.get("draw_probability_from_grid_pct")),
            to_float(row.get("away_win_probability_from_grid_pct")),
        )
        market_dir = consensus(
            to_float(row.get("market_p_home_win")),
            to_float(row.get("market_p_draw")),
            to_float(row.get("market_p_away_win")),
        )
        grid_market_agree = grid_dir == market_dir

        ev = ev_lookup.get(mid, {})
        locked_pick_outcome = txt(ev.get("locked_pick_outcome"))
        locked_pick = txt(ev.get("locked_pick"))
        pick_align = pick_alignment(locked_pick_outcome, grid_dir, market_dir)

        # biggest diverging dimension
        dim_scores = {
            "1x2": mx1x2 or 0.0,
            "ou25": abs_ou or 0.0,
            "btts": abs_btts or 0.0,
        }
        biggest_dim = max(dim_scores, key=dim_scores.get)

        rows.append({
            "match_id": mid,
            "commence_time": txt(row.get("commence_time")),
            "home_team": txt(row.get("home_team")),
            "away_team": txt(row.get("away_team")),
            # Grid vs market diffs
            "market_vs_grid_home_diff_pct": home_diff,
            "market_vs_grid_draw_diff_pct": draw_diff,
            "market_vs_grid_away_diff_pct": away_diff,
            "market_vs_grid_ou25_diff_pct": ou_diff,
            "market_vs_grid_btts_diff_pct": btts_diff,
            "max_1x2_abs_diff_pct": mx1x2,
            "abs_ou25_diff_pct": abs_ou,
            "abs_btts_diff_pct": abs_btts,
            # Classifications
            "independence_class": ind_class,
            "biggest_diverging_dimension": biggest_dim,
            "signal_consistency": sig_con,
            "grid_consensus_direction": grid_dir,
            "market_consensus_direction": market_dir,
            "grid_market_direction_agree": grid_market_agree,
            # Pick alignment
            "locked_pick": locked_pick,
            "locked_pick_outcome": locked_pick_outcome,
            "pick_follows": pick_align,
            # Grid and market raw probabilities for reference
            "grid_home_win_pct": to_float(row.get("home_win_probability_from_grid_pct")),
            "grid_draw_pct": to_float(row.get("draw_probability_from_grid_pct")),
            "grid_away_win_pct": to_float(row.get("away_win_probability_from_grid_pct")),
            "market_home_win_pct": to_float(row.get("market_p_home_win")),
            "market_draw_pct": to_float(row.get("market_p_draw")),
            "market_away_win_pct": to_float(row.get("market_p_away_win")),
            "grid_over_2_5_pct": to_float(row.get("over_2_5_probability_pct")),
            "market_over_2_5_pct": to_float(row.get("market_p_over_2_5")),
            "grid_btts_pct": to_float(row.get("btts_probability_pct")),
            "market_btts_pct": to_float(row.get("market_p_btts_yes")),
        })

    return pd.DataFrame(rows)


def main() -> int:
    args = build_parser().parse_args()
    features = load_csv(args.shape_features_csv)
    recs = load_csv(args.ev_recommendations_csv)

    out_df = build_independence(features, recs, args.mild_threshold, args.strong_threshold)

    out_csv = Path(args.out_csv)
    out_json = Path(args.out_summary_json)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out_csv, index=False)

    summary: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "shape_features_csv": args.shape_features_csv,
        "ev_recommendations_csv": args.ev_recommendations_csv,
        "mild_threshold_pct": args.mild_threshold,
        "strong_threshold_pct": args.strong_threshold,
        "match_count": int(len(out_df)),
        "out_csv": str(out_csv),
    }
    if not out_df.empty and "independence_class" in out_df.columns:
        counts = out_df["independence_class"].value_counts(dropna=False).to_dict()
        summary["independence_class_counts"] = counts
        summary["market_aligned_count"] = int(counts.get("market_aligned", 0))
        summary["mildly_independent_count"] = int(counts.get("mildly_independent", 0))
        summary["strongly_independent_count"] = int(counts.get("strongly_independent", 0))
        summary["grid_market_direction_disagree_count"] = int(
            (~out_df["grid_market_direction_agree"]).sum()
        ) if "grid_market_direction_agree" in out_df.columns else 0
        summary["signal_consistency_counts"] = (
            out_df["signal_consistency"].value_counts(dropna=False).to_dict()
        ) if "signal_consistency" in out_df.columns else {}
        summary["pick_follows_counts"] = (
            out_df["pick_follows"].value_counts(dropna=False).to_dict()
        ) if "pick_follows" in out_df.columns else {}
        if "max_1x2_abs_diff_pct" in out_df.columns:
            summary["mean_max_1x2_abs_diff_pct"] = round(
                float(out_df["max_1x2_abs_diff_pct"].dropna().mean()), 2
            )
        if "abs_ou25_diff_pct" in out_df.columns:
            summary["mean_abs_ou25_diff_pct"] = round(
                float(out_df["abs_ou25_diff_pct"].dropna().mean()), 2
            )
        if "abs_btts_diff_pct" in out_df.columns:
            summary["mean_abs_btts_diff_pct"] = round(
                float(out_df["abs_btts_diff_pct"].dropna().mean()), 2
            )
    else:
        summary["warning"] = "No features available. Wrote empty output."

    if summary.get("warning"):
        print(f"WARNING: {summary['warning']}")
    out_json.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
