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
    parser = argparse.ArgumentParser(description="Build score-shape features from Oddspedia correct-score grids.")
    parser.add_argument("--oddspedia-grid-csv", default="inputs/smartbet_grids/oddspedia_probability_grids_auto.csv")
    parser.add_argument("--markets-summary-csv", default="inputs/smartbet_grids/oddspedia_markets_summary_auto.csv")
    parser.add_argument("--out-csv", default="inputs/smartbet_grids/oddspedia_score_shape_features.csv")
    parser.add_argument("--out-summary-json", default="outputs/oddspedia_probability_extract/oddspedia_score_shape_features_summary.json")
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def to_float(value: Any) -> float:
    try:
        s = txt(value).replace("%", "")
        return float(s) if s else 0.0
    except Exception:
        return 0.0


def to_int(value: Any) -> int | None:
    s = txt(value)
    return int(s) if re.fullmatch(r"\d+", s) else None


def load_grid(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(p).fillna("")
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def outcome(row: pd.Series) -> str:
    key = txt(row.get("score_key"))
    bucket = txt(row.get("score_bucket"))
    if key == "Other_1" or bucket == "other_home_win":
        return "home"
    if key == "Other_X" or bucket == "other_draw":
        return "draw"
    if key == "Other_2" or bucket == "other_away_win":
        return "away"
    h = to_int(row.get("home_goals"))
    a = to_int(row.get("away_goals"))
    if h is None or a is None:
        return "unknown"
    if h > a:
        return "home"
    if h < a:
        return "away"
    return "draw"


def is_exact(row: pd.Series) -> bool:
    return txt(row.get("score_bucket")) == "exact" or bool(re.fullmatch(r"\d+-\d+", txt(row.get("score_key"))))


def pct_sum(frame: pd.DataFrame) -> float:
    if frame.empty:
        return 0.0
    return float(frame["probability_pct"].map(to_float).sum()) if "probability_pct" in frame.columns else 0.0


def build_features(grid: pd.DataFrame) -> pd.DataFrame:
    if grid.empty:
        return pd.DataFrame()
    if "match_id" not in grid.columns:
        grid["match_id"] = grid.apply(lambda r: f"{txt(r.get('home_team')).lower()}-{txt(r.get('away_team')).lower()}", axis=1)
    rows: list[dict[str, Any]] = []
    for mid, g in grid.groupby("match_id", dropna=False):
        g = g.copy().fillna("")
        exact = g[g.apply(is_exact, axis=1)].copy()
        exact["_hg"] = exact["home_goals"].map(to_int)
        exact["_ag"] = exact["away_goals"].map(to_int)
        exact["_total"] = exact["_hg"].fillna(0) + exact["_ag"].fillna(0)
        exact["_margin"] = (exact["_hg"].fillna(0) - exact["_ag"].fillna(0)).abs()
        exact["_outcome"] = exact.apply(outcome, axis=1)
        g["_outcome"] = g.apply(outcome, axis=1)

        other_1 = pct_sum(g[g["score_key"].astype(str).eq("Other_1")])
        other_x = pct_sum(g[g["score_key"].astype(str).eq("Other_X")])
        other_2 = pct_sum(g[g["score_key"].astype(str).eq("Other_2")])
        other_total = other_1 + other_x + other_2

        rows.append(
            {
                "match_id": txt(mid),
                "commence_time": txt(g.iloc[0].get("commence_time")) if not g.empty else "",
                "home_team": txt(g.iloc[0].get("home_team")) if not g.empty else "",
                "away_team": txt(g.iloc[0].get("away_team")) if not g.empty else "",
                "btts_probability_pct": pct_sum(exact[(exact["_hg"] > 0) & (exact["_ag"] > 0)]),
                "home_clean_sheet_probability_pct": pct_sum(exact[exact["_ag"] == 0]),
                "away_clean_sheet_probability_pct": pct_sum(exact[exact["_hg"] == 0]),
                "over_1_5_probability_pct": pct_sum(exact[exact["_total"] > 1.5]),
                "under_1_5_probability_pct": pct_sum(exact[exact["_total"] < 1.5]),
                "over_2_5_probability_pct": pct_sum(exact[exact["_total"] > 2.5]),
                "under_2_5_probability_pct": pct_sum(exact[exact["_total"] < 2.5]),
                "over_3_5_probability_pct": pct_sum(exact[exact["_total"] > 3.5]),
                "under_3_5_probability_pct": pct_sum(exact[exact["_total"] < 3.5]),
                "draw_probability_from_grid_pct": pct_sum(g[g["_outcome"] == "draw"]),
                "home_win_probability_from_grid_pct": pct_sum(g[g["_outcome"] == "home"]),
                "away_win_probability_from_grid_pct": pct_sum(g[g["_outcome"] == "away"]),
                "one_goal_margin_probability_pct": pct_sum(exact[exact["_margin"] == 1]),
                "two_goal_margin_probability_pct": pct_sum(exact[exact["_margin"] == 2]),
                "big_win_or_other_bucket_probability_pct": pct_sum(exact[exact["_margin"] >= 3]) + other_total,
                "other_1_risk_pct": other_1,
                "other_x_risk_pct": other_x,
                "other_2_risk_pct": other_2,
                "other_bucket_total_probability_pct": other_total,
                "known_exact_probability_pct": pct_sum(exact),
                "grid_probability_total_pct": pct_sum(g),
            }
        )
    return pd.DataFrame(rows)


def main() -> int:
    args = build_parser().parse_args()
    grid = load_grid(args.oddspedia_grid_csv)
    features = build_features(grid)

    # Join market 100 (1X2) direct probabilities and compare against grid-derived direction
    markets_path = Path(args.markets_summary_csv)
    if not features.empty and markets_path.exists():
        try:
            mkts = pd.read_csv(markets_path).fillna("")
            if "match_id" not in mkts.columns:
                mkts["match_id"] = mkts.apply(
                    lambda r: f"{txt(r.get('home_team')).lower()}-{txt(r.get('away_team')).lower()}", axis=1
                )
            keep = ["match_id", "p_home_win", "p_draw", "p_away_win"]
            mkts = mkts[[c for c in keep if c in mkts.columns]]
            mkts = mkts.rename(columns={"p_home_win": "market_p_home_win", "p_draw": "market_p_draw", "p_away_win": "market_p_away_win"})
            features = features.merge(mkts, on="match_id", how="left")
            for col in ("market_p_home_win", "market_p_draw", "market_p_away_win"):
                features[col] = pd.to_numeric(features[col], errors="coerce")
            features["market_vs_grid_home_diff_pct"] = features["market_p_home_win"] - features["home_win_probability_from_grid_pct"]
            features["market_vs_grid_draw_diff_pct"] = features["market_p_draw"] - features["draw_probability_from_grid_pct"]
            features["market_vs_grid_away_diff_pct"] = features["market_p_away_win"] - features["away_win_probability_from_grid_pct"]
        except Exception:
            pass

    out_csv = Path(args.out_csv)
    out_json = Path(args.out_summary_json)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    features.to_csv(out_csv, index=False)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "oddspedia_grid_csv": args.oddspedia_grid_csv,
        "markets_summary_csv": args.markets_summary_csv,
        "match_count": int(len(features)),
        "out_csv": str(out_csv),
        "warning": "Missing or empty grid CSV. Wrote empty output." if grid.empty else "",
    }
    out_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    if payload["warning"]:
        print(f"WARNING: {payload['warning']}")
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
