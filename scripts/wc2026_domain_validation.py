"""
WC 2026 domain validation against completed matches with market odds.

This script intentionally does NOT read home_goals/away_goals from
outputs/market_odds_history/market_odds_history.csv as actual results. That file
contains prediction-card score fields and can leak the pick back into the score.
Actuals must come from a trusted completed-results source, by default the
Superbru results scrape:

  outputs/superbru_pool/superbru_match_results_auto.csv

The validation joins trusted completed results to the latest available pre-match
market snapshot, then scores the active model and a naive low-score favourite
baseline.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run leakage-safe WC 2026 validation against trusted completed results.")
    p.add_argument("--history-csv", default="outputs/market_odds_history/market_odds_history.csv")
    p.add_argument("--results-csv", default="outputs/superbru_pool/superbru_match_results_auto.csv")
    p.add_argument("--out-dir", default="outputs/calibration/wc2026-domain-validation")
    return p


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def team_key(value: Any) -> str:
    text = txt(value).lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "", text)


def match_key(home: Any, away: Any) -> tuple[str, str]:
    return team_key(home), team_key(away)


def parse_dt(value: Any) -> datetime | None:
    text = txt(value)
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def truthy(value: Any) -> bool:
    return txt(value).lower() in {"1", "true", "yes", "y", "completed"}


def load_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_pre_match_snapshots(path: Path) -> list[dict[str, str]]:
    rows = load_rows(path)
    by_match: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in rows:
        if not truthy(row.get("market_available")):
            continue
        created = parse_dt(row.get("snapshot_created_at_utc"))
        kickoff = parse_dt(row.get("commence_time"))
        if created is not None and kickoff is not None and created > kickoff:
            continue
        key = match_key(row.get("home_team"), row.get("away_team"))
        by_match.setdefault(key, []).append(row)

    closing = []
    for match_rows in by_match.values():
        closing.append(max(match_rows, key=lambda r: txt(r.get("snapshot_created_at_utc")) or txt(r.get("snapshot_id"))))
    closing.sort(key=lambda r: txt(r.get("commence_time")))
    return closing


def load_trusted_results(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    results: dict[tuple[str, str], dict[str, Any]] = {}
    for row in load_rows(path):
        if "is_completed" in row and not truthy(row.get("is_completed")):
            continue
        try:
            home_goals = int(float(row["home_goals"]))
            away_goals = int(float(row["away_goals"]))
        except (KeyError, TypeError, ValueError):
            continue
        key = match_key(row.get("home_team"), row.get("away_team"))
        payload = dict(row)
        payload["actual_home_goals"] = home_goals
        payload["actual_away_goals"] = away_goals
        payload["actual_scoreline"] = f"{home_goals}-{away_goals}"
        results[key] = payload
    return results


def build_match_odds(row: dict[str, str], match_id: str) -> MatchOdds:
    p_home = float(row["market_p_home"])
    p_draw = float(row["market_p_draw"])
    p_away = float(row["market_p_away"])

    markets: dict = {
        "h2h": (
            MarketOdds(
                key="h2h",
                bookmaker="wc2026_fair_pre_match",
                outcomes=(
                    OutcomeOdds(name=row["home_team"], price=1.0 / p_home),
                    OutcomeOdds(name="Draw", price=1.0 / p_draw),
                    OutcomeOdds(name=row["away_team"], price=1.0 / p_away),
                ),
                last_update=txt(row.get("snapshot_id")),
            ),
        )
    }

    if truthy(row.get("totals_available")) and txt(row.get("market_p_over")) and txt(row.get("market_p_under")):
        p_over = float(row["market_p_over"])
        p_under = float(row["market_p_under"])
        line = float(row["total_line"])
        markets["totals"] = (
            MarketOdds(
                key="totals",
                bookmaker="wc2026_fair_pre_match",
                outcomes=(
                    OutcomeOdds(name="Over", price=1.0 / p_over, point=line),
                    OutcomeOdds(name="Under", price=1.0 / p_under, point=line),
                ),
                last_update=txt(row.get("snapshot_id")),
            ),
        )

    return MatchOdds(
        match_id=match_id,
        commence_time=txt(row.get("commence_time")),
        home_team=txt(row.get("home_team")),
        away_team=txt(row.get("away_team")),
        markets=markets,
        neutral=True,
        raw=dict(row),
    )


def invalidated_summary(out_dir: Path, reason: str, history_csv: Path, results_csv: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "validation_status": "invalidated",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "contamination_guard": "Actual results must come from trusted completed-results CSV, never from market_odds_history home_goals/away_goals.",
        "history_csv": str(history_csv),
        "trusted_results_csv": str(results_csv),
        "matches": 0,
    }
    (out_dir / "wc2026_domain_validation_summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (out_dir / "wc2026_domain_validation_results.csv").write_text(
        "validation_status,reason\ninvalidated," + reason.replace(",", ";") + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = build_parser().parse_args()
    history_csv = REPO / args.history_csv
    results_csv = REPO / args.results_csv
    out_dir = REPO / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    results_by_match = load_trusted_results(results_csv)
    if not results_by_match:
        invalidated_summary(out_dir, "No trusted completed results found; validation not run.", history_csv, results_csv)
        print("No trusted completed results found. Wrote invalidated validation summary.")
        return 1

    config = load_config(REPO / "config.yaml")
    model = OddsToScorelineModel(config.model, RatingsStore())
    decision = SuperbruDecisionEngine(config.superbru, config.model.candidate_grid_goals)

    snapshots = load_pre_match_snapshots(history_csv)
    rows = []
    for idx, row in enumerate(snapshots):
        result = results_by_match.get(match_key(row.get("home_team"), row.get("away_team")))
        if not result:
            continue
        actual_home = int(result["actual_home_goals"])
        actual_away = int(result["actual_away_goals"])

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
        naive_pts = score_actual_prediction(naive_home, naive_away, actual_home, actual_away, config.superbru.ci_cutoff)
        scoring = _scoring_fields(distribution, actual_home, actual_away)

        rows.append(
            {
                "match_id": match_id,
                "commence_time": match.commence_time,
                "home_team": match.home_team,
                "away_team": match.away_team,
                "snapshot_id": txt(row.get("snapshot_id")),
                "snapshot_created_at_utc": txt(row.get("snapshot_created_at_utc")),
                "trusted_score_source": txt(result.get("score_source")),
                "actual_scoreline": f"{actual_home}-{actual_away}",
                "model_scoreline": prediction.recommended.scoreline,
                "model_points": model_pts,
                "naive_scoreline": f"{naive_home}-{naive_away}",
                "naive_points": naive_pts,
                "has_totals": "totals" in match.markets,
                "lambda_home": distribution.lambda_home,
                "lambda_away": distribution.lambda_away,
                **scoring,
            }
        )

    if not rows:
        invalidated_summary(out_dir, "No trusted completed results joined to pre-match market snapshots.", history_csv, results_csv)
        print("No trusted completed results joined to pre-match market snapshots. Wrote invalidated summary.")
        return 1

    results_path = out_dir / "wc2026_domain_validation_results.csv"
    with results_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    model_pts_arr = [float(r["model_points"]) for r in rows]
    naive_pts_arr = [float(r["naive_points"]) for r in rows]
    rps_arr = [float(r["rps_1x2"]) for r in rows]
    crps_arr = [float(r["total_goals_crps"]) for r in rows]
    n = len(rows)

    summary = {
        "validation_status": "valid",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "WC 2026 pre-match market_odds_history joined to trusted completed results CSV",
        "trusted_results_csv": str(results_csv),
        "history_csv": str(history_csv),
        "contamination_guard": "Actual goals were read only from trusted completed results, not from market_odds_history.",
        "calibration_profile": config.model.calibration_profile,
        "devig_method": config.model.devig_method,
        "dixon_coles_rho": config.model.dixon_coles_rho,
        "ci_cutoff": config.superbru.ci_cutoff,
        "matches": n,
        "h2h_totals_matches": sum(1 for r in rows if r["has_totals"]),
        "avg_model_points": sum(model_pts_arr) / n,
        "avg_naive_points": sum(naive_pts_arr) / n,
        "edge_vs_naive": (sum(model_pts_arr) - sum(naive_pts_arr)) / n,
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

    summary_path = out_dir / "wc2026_domain_validation_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
