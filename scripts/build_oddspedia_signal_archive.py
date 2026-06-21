"""
build_oddspedia_signal_archive.py

Snapshots the current Oddspedia signals and locked picks into a rolling
archive CSV so that once match results arrive, backtesting can compare
the pre-match signals against actual outcomes.

Appends a dated row per match to outputs/backtesting/signal_archive_rolling.csv.
Also writes a point-in-time snapshot to outputs/backtesting/signal_archive_YYYYMMDD.csv.
Deduplicates on (archive_date, match_id) so re-running on the same day
is safe (updates existing rows for that date rather than duplicating them).
"""
from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Archive today's Oddspedia signals + locked picks for future backtesting."
    )
    parser.add_argument(
        "--shape-features-csv",
        default="inputs/smartbet_grids/oddspedia_score_shape_features.csv",
    )
    parser.add_argument(
        "--comparison-csv",
        default="outputs/oddspedia_pick_validation/oddspedia_pick_comparison.csv",
    )
    parser.add_argument(
        "--ev-recommendations-csv",
        default="outputs/oddspedia_pick_validation/oddspedia_ev_recommendations.csv",
    )
    parser.add_argument(
        "--independence-csv",
        default="outputs/oddspedia_pick_validation/oddspedia_model_independence.csv",
    )
    parser.add_argument(
        "--rolling-archive-csv",
        default="outputs/backtesting/signal_archive_rolling.csv",
    )
    parser.add_argument(
        "--snapshot-dir",
        default="outputs/backtesting/snapshots",
    )
    parser.add_argument(
        "--out-summary-json",
        default="outputs/backtesting/signal_archive_summary.json",
    )
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


def build_snapshot(
    features: pd.DataFrame,
    comparison: pd.DataFrame,
    recs: pd.DataFrame,
    independence: pd.DataFrame,
    archive_date: str,
    archive_ts: str,
) -> pd.DataFrame:
    if recs.empty:
        return pd.DataFrame()

    feat_idx: dict[str, dict] = {}
    if not features.empty and "match_id" in features.columns:
        for _, r in features.iterrows():
            feat_idx[txt(r.get("match_id"))] = r.to_dict()

    comp_idx: dict[str, dict] = {}
    if not comparison.empty and "match_id" in comparison.columns:
        for _, r in comparison.iterrows():
            comp_idx[txt(r.get("match_id"))] = r.to_dict()

    ind_idx: dict[str, dict] = {}
    if not independence.empty and "match_id" in independence.columns:
        for _, r in independence.iterrows():
            ind_idx[txt(r.get("match_id"))] = r.to_dict()

    rows: list[dict[str, Any]] = []
    for _, rec in recs.iterrows():
        mid = txt(rec.get("match_id"))
        feat = feat_idx.get(mid, {})
        comp = comp_idx.get(mid, {})
        ind = ind_idx.get(mid, {})

        rows.append({
            "archive_date": archive_date,
            "archive_timestamp_utc": archive_ts,
            "match_id": mid,
            "commence_time": txt(rec.get("commence_time")),
            "home_team": txt(rec.get("home_team")),
            "away_team": txt(rec.get("away_team")),
            # Locked pick
            "locked_pick": txt(rec.get("locked_pick")),
            "locked_pick_outcome": txt(rec.get("locked_pick_outcome")),
            "locked_pick_ev": to_float(rec.get("current_locked_pick_ev")),
            # Best EV score from Oddspedia
            "best_ev_scoreline": txt(rec.get("best_ev_scoreline")),
            "best_ev_expected_points": to_float(rec.get("best_ev_expected_points")),
            "ev_gap_vs_locked": to_float(rec.get("ev_gap_vs_locked")),
            "review_level": txt(rec.get("review_level")),
            # Oddspedia top picks
            "oddspedia_top1_score": txt(comp.get("oddspedia_top1_score")),
            "oddspedia_top1_pct": to_float(comp.get("oddspedia_top1_pct")),
            "oddspedia_top2_score": txt(comp.get("oddspedia_top2_score")),
            "oddspedia_top2_pct": to_float(comp.get("oddspedia_top2_pct")),
            "oddspedia_top3_score": txt(comp.get("oddspedia_top3_score")),
            "oddspedia_top3_pct": to_float(comp.get("oddspedia_top3_pct")),
            # Market direction
            "market_consensus_direction": txt(rec.get("market_consensus_direction")),
            "market_p_home_win": to_float(rec.get("market_p_home_win")),
            "market_p_draw": to_float(rec.get("market_p_draw")),
            "market_p_away_win": to_float(rec.get("market_p_away_win")),
            # Goal signals from grid
            "grid_over_2_5_pct": to_float(feat.get("over_2_5_probability_pct")),
            "grid_btts_pct": to_float(feat.get("btts_probability_pct")),
            "market_p_over_2_5": to_float(feat.get("market_p_over_2_5")),
            "market_p_btts_yes": to_float(feat.get("market_p_btts_yes")),
            "ou25_diff_pct": to_float(feat.get("market_vs_grid_ou25_diff_pct")),
            "btts_diff_pct": to_float(feat.get("market_vs_grid_btts_diff_pct")),
            # Independence classification
            "independence_class": txt(ind.get("independence_class")),
            "signal_consistency": txt(ind.get("signal_consistency")),
            "pick_follows": txt(ind.get("pick_follows")),
            # Placeholder for results (filled in at backtest time)
            "actual_score": "",
            "actual_outcome": "",
            "backtest_points": "",
        })

    return pd.DataFrame(rows)


def merge_into_rolling(rolling_path: Path, new_rows: pd.DataFrame, archive_date: str) -> pd.DataFrame:
    if rolling_path.exists():
        try:
            existing = pd.read_csv(rolling_path).fillna("")
        except pd.errors.EmptyDataError:
            existing = pd.DataFrame()
    else:
        existing = pd.DataFrame()

    if existing.empty:
        return new_rows

    # Drop rows from today (idempotent re-run)
    if "archive_date" in existing.columns:
        existing = existing[existing["archive_date"].astype(str) != archive_date].copy()

    return pd.concat([existing, new_rows], ignore_index=True, sort=False).fillna("")


def main() -> int:
    args = build_parser().parse_args()
    now = datetime.now(timezone.utc)
    archive_date = now.strftime("%Y-%m-%d")
    archive_ts = now.isoformat()

    features = load_csv(args.shape_features_csv)
    comparison = load_csv(args.comparison_csv)
    recs = load_csv(args.ev_recommendations_csv)
    independence = load_csv(args.independence_csv)

    snapshot = build_snapshot(features, comparison, recs, independence, archive_date, archive_ts)

    rolling_path = Path(args.rolling_archive_csv)
    rolling_path.parent.mkdir(parents=True, exist_ok=True)

    snapshot_dir = Path(args.snapshot_dir)
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    snapshot_path = snapshot_dir / f"signal_archive_{archive_date}.csv"
    snapshot.to_csv(snapshot_path, index=False)

    rolling = merge_into_rolling(rolling_path, snapshot, archive_date)
    rolling.to_csv(rolling_path, index=False)

    summary: dict[str, Any] = {
        "generated_at_utc": archive_ts,
        "archive_date": archive_date,
        "snapshot_rows_written": int(len(snapshot)),
        "rolling_archive_total_rows": int(len(rolling)),
        "rolling_archive_dates": sorted(
            rolling["archive_date"].unique().tolist()
        ) if not rolling.empty and "archive_date" in rolling.columns else [],
        "snapshot_csv": str(snapshot_path),
        "rolling_csv": str(rolling_path),
    }
    if snapshot.empty:
        summary["warning"] = "No EV recommendations available. Archive snapshot is empty."

    out_json = Path(args.out_summary_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    if summary.get("warning"):
        print(f"WARNING: {summary['warning']}")
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
