"""
build_oddspedia_synthetic_pool_crowding.py

Estimates pool pick crowding risk without live pool pick data.

Method:
  - Assumes pool players gravitate toward the Oddspedia top-probability
    scorelines (the "obvious" picks visible to anyone checking odds).
  - Estimates probability that the pool clusters on a given scoreline
    based on: its rank by grid probability, its outcome alignment with
    the market favourite, and whether it is the 1-0 / 1-1 / 2-0 type
    that casual players default to.
  - Compares against our locked pick to flag crowding risk or
    differentiation opportunity.

Outputs per-match crowding risk rows and a summary JSON.
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


TYPICAL_POOL_SIZE = 32
# Fraction of pool estimated on top-1, top-2, top-3 scorelines
TOP_PICK_FRACTIONS = [0.32, 0.18, 0.12]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Estimate synthetic pool crowding from Oddspedia grid signals."
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
        "--leaderboard-csv",
        default="inputs/pool_leaderboard.csv",
    )
    parser.add_argument(
        "--round-summary-csv",
        default="inputs/round_summary_behaviour.csv",
    )
    parser.add_argument(
        "--out-csv",
        default="outputs/superbru_pool/superbru_synthetic_crowding.csv",
    )
    parser.add_argument(
        "--out-summary-json",
        default="outputs/superbru_pool/superbru_synthetic_crowding_summary.json",
    )
    parser.add_argument("--pool-size", type=int, default=TYPICAL_POOL_SIZE)
    parser.add_argument("--high-crowding-threshold", type=float, default=25.0,
                        help="Estimated % of pool on our pick that flags crowding risk")
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


def is_popular_scoreline(score: str) -> bool:
    """Heuristic: low-scoring draws and single-goal home wins are picked by many casual players."""
    return score in {"1-1", "1-0", "2-0", "0-0", "2-1"}


def estimate_pool_pct_on_score(
    score: str,
    rank: int,
    grid_pct: float | None,
    market_outcome: str,
    score_outcome: str,
) -> float:
    """
    Estimate what fraction of the pool picks this scoreline (0-100).
    Heuristic model:
      - Base from rank-based fraction (top-1=32%, top-2=18%, top-3=12%)
      - Boost if it is a "popular" clean scoreline
      - Small boost if it aligns with market favourite
    """
    if rank <= 0:
        return 0.0
    base_fracs = TOP_PICK_FRACTIONS
    if rank <= len(base_fracs):
        base = base_fracs[rank - 1] * 100.0
    else:
        base = max(0.0, 100.0 * (0.08 * (0.7 ** (rank - len(base_fracs)))))

    if is_popular_scoreline(score):
        base *= 1.25
    if market_outcome and score_outcome and score_outcome == market_outcome:
        base *= 1.10

    return round(min(base, 95.0), 1)


def scoreline_outcome(score: str) -> str:
    m = re.search(r"(\d+)\s*[-:]\s*(\d+)", score)
    if not m:
        return "unknown"
    h, a = int(m.group(1)), int(m.group(2))
    if h > a:
        return "home"
    if h < a:
        return "away"
    return "draw"


def round_behaviour_summary(behaviour: pd.DataFrame) -> dict[str, Any]:
    if behaviour.empty:
        return {}
    num = pd.to_numeric(behaviour.get("exact_count", pd.Series(dtype=float)), errors="coerce")
    tot = pd.to_numeric(behaviour.get("total_matches", pd.Series(dtype=float)), errors="coerce")
    exact_rate = float((num / tot.replace(0, float("nan"))).mean()) if len(num) else None
    return {
        "pool_mean_exact_rate": round(exact_rate, 3) if exact_rate else None,
        "rounds_observed": int(behaviour["round"].nunique()) if "round" in behaviour.columns else 0,
        "players_observed": int(behaviour["player"].nunique()) if "player" in behaviour.columns else 0,
    }


def build_crowding(
    comparison: pd.DataFrame,
    recs: pd.DataFrame,
    pool_size: int,
    high_threshold: float,
) -> pd.DataFrame:
    if comparison.empty:
        return pd.DataFrame()

    ev_lookup: dict[str, dict] = {}
    if not recs.empty and "match_id" in recs.columns:
        for _, r in recs.iterrows():
            mid = txt(r.get("match_id"))
            if mid:
                ev_lookup[mid] = r.to_dict()

    rows: list[dict[str, Any]] = []
    for _, row in comparison.iterrows():
        mid = txt(row.get("match_id"))
        market_dir = txt(row.get("market_consensus_direction"))
        ev = ev_lookup.get(mid, {})
        locked_pick = txt(ev.get("locked_pick")) or txt(row.get("locked_pick"))
        locked_outcome = scoreline_outcome(locked_pick) if locked_pick else "unknown"

        # Collect ranked scorelines from comparison CSV
        candidates: list[tuple[str, float | None]] = []
        for i in range(1, 6):
            score_col = f"oddspedia_top{i}_score"
            pct_col = f"oddspedia_top{i}_pct"
            s = txt(row.get(score_col))
            if s:
                candidates.append((s, to_float(row.get(pct_col))))

        # Estimate synthetic pool distribution
        locked_rank: int | None = None
        locked_est_pct: float = 0.0
        modal_score = txt(row.get("modal_correct_score"))
        modal_est_pct: float = 0.0
        score_estimates: list[dict[str, Any]] = []

        for rank, (s, g_pct) in enumerate(candidates, start=1):
            s_outcome = scoreline_outcome(s)
            est_pct = estimate_pool_pct_on_score(s, rank, g_pct, market_dir, s_outcome)
            score_estimates.append({
                "rank": rank,
                "scoreline": s,
                "grid_probability_pct": g_pct,
                "est_pool_pct": est_pct,
            })
            if s == locked_pick:
                locked_rank = rank
                locked_est_pct = est_pct
            if s == modal_score:
                modal_est_pct = est_pct

        top_est = score_estimates[0] if score_estimates else {}
        modal_est_pct = modal_est_pct or top_est.get("est_pool_pct", 0.0)

        # Crowding risk: are we picking the obvious crowded score?
        crowding_risk = bool(locked_est_pct >= high_threshold)
        # Differentiation: we pick something most players won't
        differentiation_flag = bool(locked_est_pct < 10.0 and locked_pick)
        # Contrarian premium: if we're right but most aren't, we gain big positions
        contrarian_premium = bool(
            differentiation_flag
            and locked_outcome in {"home", "away", "draw"}
            and locked_rank is not None
            and locked_rank >= 3
        )

        rows.append({
            "match_id": mid,
            "commence_time": txt(row.get("commence_time")),
            "home_team": txt(row.get("home_team")),
            "away_team": txt(row.get("away_team")),
            "locked_pick": locked_pick,
            "locked_pick_outcome": locked_outcome,
            "locked_pick_rank_in_grid": locked_rank,
            "est_pool_pct_on_locked_pick": locked_est_pct,
            "est_pool_players_on_locked_pick": round(locked_est_pct / 100.0 * pool_size, 1),
            "modal_score_estimate": modal_score or top_est.get("scoreline", ""),
            "est_pool_pct_on_modal": modal_est_pct,
            "top1_score": candidates[0][0] if candidates else "",
            "top1_grid_pct": candidates[0][1] if candidates else None,
            "top1_est_pool_pct": score_estimates[0]["est_pool_pct"] if score_estimates else None,
            "crowding_risk_flag": crowding_risk,
            "differentiation_flag": differentiation_flag,
            "contrarian_premium_flag": contrarian_premium,
            "crowding_signal": (
                "high_crowding" if crowding_risk else
                "moderate" if locked_est_pct >= 10.0 else
                "contrarian"
            ),
        })

    return pd.DataFrame(rows)


def main() -> int:
    args = build_parser().parse_args()
    comparison = load_csv(args.comparison_csv)
    recs = load_csv(args.ev_recommendations_csv)
    leaderboard = load_csv(args.leaderboard_csv)
    behaviour = load_csv(args.round_summary_csv)

    pool_size = args.pool_size
    if not leaderboard.empty and "player" in leaderboard.columns:
        pool_size = max(pool_size, int(len(leaderboard)))

    behaviour_stats = round_behaviour_summary(behaviour)
    crowding = build_crowding(comparison, recs, pool_size, args.high_crowding_threshold)

    out_csv = Path(args.out_csv)
    out_json = Path(args.out_summary_json)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    crowding.to_csv(out_csv, index=False)

    summary: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "pool_size_used": pool_size,
        "high_crowding_threshold_pct": args.high_crowding_threshold,
        "match_count": int(len(crowding)),
        "behaviour_stats": behaviour_stats,
        "out_csv": str(out_csv),
    }
    if not crowding.empty:
        summary["crowding_risk_count"] = int(crowding["crowding_risk_flag"].sum())
        summary["differentiation_count"] = int(crowding["differentiation_flag"].sum())
        summary["contrarian_premium_count"] = int(crowding["contrarian_premium_flag"].sum())
        summary["signal_counts"] = crowding["crowding_signal"].value_counts(dropna=False).to_dict()
        summary["mean_est_pool_pct_on_locked"] = round(
            float(crowding["est_pool_pct_on_locked_pick"].mean()), 1
        )
    else:
        summary["warning"] = "No comparison data available. Wrote empty output."

    if summary.get("warning"):
        print(f"WARNING: {summary['warning']}")
    out_json.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
