"""
WC 2026 domain validation against completed matches with market odds.

Reads the latest pre-match snapshot per match from market_odds_history.csv,
converts the already-de-vigged fair probs to no-margin decimal odds, and
runs them through the active engine config (multiplicative / -0.08, h2h_totals).

Outputs per-match results CSV and summary JSON to:
  outputs/calibration/wc2026-domain-validation/
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from superbru_score_engine.backtest.football_data_leagues import _scoring_fields
from superbru_score_engine.backtest.runner import naive_baseline_pick
from superbru_score_engine.config import load_config
from superbru_score_engine.decision import SuperbruDecisionEngine
from superbru_score_engine.decision.superbru import score_actual_prediction
from superbru_score_engine.ingest.base import MarketOdds, MatchOdds, OutcomeOdds
from superbru_score_engine.model import OddsToScorelineModel
from superbru_score_engine.model.ratings import RatingsStore

HISTORY_CSV = REPO / "outputs" / "market_odds_history" / "market_odds_history.csv"
OUT_DIR = REPO / "outputs" / "calibration" / "wc2026-domain-validation"


def load_closing_snapshots(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    by_match: dict[tuple, list[dict]] = {}
    for row in rows:
        key = (row["home_team"], row["away_team"], row["commence_time"])
        by_match.setdefault(key, []).append(row)

    closing = []
    for match_rows in by_match.values():
        closing.append(max(match_rows, key=lambda r: r["snapshot_id"]))

    closing.sort(key=lambda r: r["commence_time"])
    return closing


def build_match_odds(row: dict, match_id: str) -> MatchOdds:
    p_home = float(row["market_p_home"])
    p_draw = float(row["market_p_draw"])
    p_away = float(row["market_p_away"])

    markets: dict = {
        "h2h": (
            MarketOdds(
                key="h2h",
                bookmaker="wc2026_fair",
                outcomes=(
                    OutcomeOdds(name=row["home_team"], price=1.0 / p_home),
                    OutcomeOdds(name="Draw", price=1.0 / p_draw),
                    OutcomeOdds(name=row["away_team"], price=1.0 / p_away),
                ),
                last_update=str(row["snapshot_id"]),
            ),
        )
    }

    if row.get("totals_available", "").strip().lower() == "true":
        p_over = float(row["market_p_over"])
        p_under = float(row["market_p_under"])
        line = float(row["total_line"])
        markets["totals"] = (
            MarketOdds(
                key="totals",
                bookmaker="wc2026_fair",
                outcomes=(
                    OutcomeOdds(name="Over", price=1.0 / p_over, point=line),
                    OutcomeOdds(name="Under", price=1.0 / p_under, point=line),
                ),
                last_update=str(row["snapshot_id"]),
            ),
        )

    return MatchOdds(
        match_id=match_id,
        commence_time=str(row["commence_time"]),
        home_team=str(row["home_team"]),
        away_team=str(row["away_team"]),
        markets=markets,
        neutral=True,
        raw=dict(row),
    )


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    config = load_config(REPO / "config.yaml")
    model = OddsToScorelineModel(config.model, RatingsStore())
    decision = SuperbruDecisionEngine(config.superbru, config.model.candidate_grid_goals)

    snapshots = load_closing_snapshots(HISTORY_CSV)

    rows = []
    for idx, row in enumerate(snapshots):
        if row.get("market_available", "").strip().lower() != "true":
            continue
        try:
            actual_home = int(float(row["home_goals"]))
            actual_away = int(float(row["away_goals"]))
        except (ValueError, TypeError, KeyError):
            continue

        match_id = f"wc2026-{idx:03d}"
        match = build_match_odds(row, match_id)
        distribution = model.build_distribution(match)
        prediction = decision.predict(distribution)
        naive_home, naive_away = naive_baseline_pick(match, distribution)

        model_pts = score_actual_prediction(
            prediction.recommended.home_goals,
            prediction.recommended.away_goals,
            actual_home,
            actual_away,
            config.superbru.ci_cutoff,
        )
        naive_pts = score_actual_prediction(
            naive_home, naive_away, actual_home, actual_away, config.superbru.ci_cutoff
        )
        scoring = _scoring_fields(distribution, actual_home, actual_away)

        rows.append({
            "match_id": match_id,
            "commence_time": match.commence_time,
            "home_team": match.home_team,
            "away_team": match.away_team,
            "snapshot_id": row["snapshot_id"],
            "actual_scoreline": f"{actual_home}-{actual_away}",
            "model_scoreline": prediction.recommended.scoreline,
            "model_points": model_pts,
            "naive_scoreline": f"{naive_home}-{naive_away}",
            "naive_points": naive_pts,
            "has_totals": "totals" in match.markets,
            "lambda_home": distribution.lambda_home,
            "lambda_away": distribution.lambda_away,
            **scoring,
        })

    if not rows:
        print("No valid market matches found with actual results.")
        return 1

    results_path = OUT_DIR / "wc2026_domain_validation_results.csv"
    with open(results_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    model_pts_arr = [float(r["model_points"]) for r in rows]
    naive_pts_arr = [float(r["naive_points"]) for r in rows]
    rps_arr = [float(r["rps_1x2"]) for r in rows]
    crps_arr = [float(r["total_goals_crps"]) for r in rows]

    n = len(rows)
    avg_model = sum(model_pts_arr) / n
    avg_naive = sum(naive_pts_arr) / n

    summary = {
        "source": "WC 2026 market_odds_history.csv (closing snapshot per match)",
        "calibration_profile": config.model.calibration_profile,
        "devig_method": config.model.devig_method,
        "dixon_coles_rho": config.model.dixon_coles_rho,
        "ci_cutoff": config.superbru.ci_cutoff,
        "matches": n,
        "h2h_totals_matches": sum(1 for r in rows if r["has_totals"]),
        "avg_model_points": avg_model,
        "avg_naive_points": avg_naive,
        "edge_vs_naive": avg_model - avg_naive,
        "exact_hits": sum(1 for r in rows if float(r["model_points"]) == 3.0),
        "close_hits": sum(1 for r in rows if float(r["model_points"]) == 1.5),
        "outcome_only_hits": sum(1 for r in rows if float(r["model_points"]) == 1.0),
        "misses": sum(1 for r in rows if float(r["model_points"]) == 0.0),
        "outcome_accuracy": sum(1 for r in rows if float(r["model_points"]) > 0.0) / n,
        "exact_rate": sum(1 for r in rows if float(r["model_points"]) == 3.0) / n,
        "avg_rps_1x2": sum(rps_arr) / n,
        "avg_total_goals_crps": sum(crps_arr) / n,
        "results_csv": str(results_path),
    }

    summary_path = OUT_DIR / "wc2026_domain_validation_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
