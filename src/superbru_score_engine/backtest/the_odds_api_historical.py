from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd

from superbru_score_engine.config import AppConfig, env_value
from superbru_score_engine.decision import SuperbruDecisionEngine
from superbru_score_engine.decision.superbru import score_actual_prediction
from superbru_score_engine.ingest.base import MatchOdds, ProviderUnavailable
from superbru_score_engine.ingest.cache import JsonCache
from superbru_score_engine.ingest.http import get_json
from superbru_score_engine.ingest.normalise import normalise_the_odds_api_events
from superbru_score_engine.model import DistributionResult, OddsToScorelineModel
from superbru_score_engine.model.ratings import MatchResult, RatingsStore
from superbru_score_engine.model.team_names import canonical_team_key

from .runner import naive_baseline_pick, reliability_cells


def run_the_odds_api_historical_backtest(args: argparse.Namespace, config: AppConfig) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    fixtures = load_fixture_results(args.fixtures)
    if args.fetch:
        raw_snapshots = fetch_historical_snapshots(args, config, out_dir)
    elif args.snapshots_json:
        raw_snapshots = json.loads(Path(args.snapshots_json).read_text(encoding="utf-8"))
    else:
        raise ProviderUnavailable("Provide --snapshots-json, or pass --fetch with --date-grid to download historical snapshots")

    events = historical_events_from_snapshots(raw_snapshots)
    matches = normalise_the_odds_api_events(events)
    matched = match_events_to_fixtures(matches, fixtures)

    market_modes = _string_grid(args.market_mode_grid) or ["h2h", "h2h_totals"]
    compare_on_totals_subset = "h2h_totals" in market_modes
    if compare_on_totals_subset:
        matched = [item for item in matched if _has_market(item["match"], "h2h") and _has_market(item["match"], "totals")]
    else:
        matched = [item for item in matched if _has_market(item["match"], "h2h")]

    rho_grid = _float_grid(args.rho_grid) or [config.model.dixon_coles_rho]
    ci_grid = _float_grid(args.ci_grid) or [config.superbru.ci_cutoff]
    devig_methods = _string_grid(args.devig_method_grid) or [config.model.devig_method]

    calibration_rows: list[dict[str, Any]] = []
    best_by_mode: dict[str, tuple[float, str, float, float, pd.DataFrame]] = {}
    for market_mode in market_modes:
        for devig_method in devig_methods:
            for rho in rho_grid:
                for ci_cutoff in ci_grid:
                    trial_config = replace(
                        config,
                        model=replace(config.model, dixon_coles_rho=rho, devig_method=devig_method),
                        superbru=replace(config.superbru, ci_cutoff=ci_cutoff),
                    )
                    frame = evaluate_historical_matches(matched, trial_config, market_mode)
                    avg_model = float(frame["model_points"].mean()) if not frame.empty else 0.0
                    avg_naive = float(frame["naive_points"].mean()) if not frame.empty else 0.0
                    row = {
                        "market_mode": market_mode,
                        "devig_method": devig_method,
                        "dixon_coles_rho": rho,
                        "ci_cutoff": ci_cutoff,
                        "matches": int(len(frame)),
                        "avg_model_points": avg_model,
                        "avg_naive_points": avg_naive,
                        "edge_vs_naive": avg_model - avg_naive,
                        "exact_hits": int((frame["model_points"] == 3.0).sum()) if not frame.empty else 0,
                        "close_hits": int((frame["model_points"] == 1.5).sum()) if not frame.empty else 0,
                        "outcome_only_hits": int((frame["model_points"] == 1.0).sum()) if not frame.empty else 0,
                        "misses": int((frame["model_points"] == 0.0).sum()) if not frame.empty else 0,
                    }
                    calibration_rows.append(row)
                    current_best = best_by_mode.get(market_mode)
                    if current_best is None or avg_model > current_best[0]:
                        best_by_mode[market_mode] = (avg_model, devig_method, rho, ci_cutoff, frame)

    calibration = pd.DataFrame(calibration_rows).sort_values(["market_mode", "avg_model_points"], ascending=[True, False])
    calibration_path = out_dir / "the_odds_api_historical_calibration.csv"
    calibration.to_csv(calibration_path, index=False)

    result_frames: list[pd.DataFrame] = []
    mode_summaries: dict[str, dict[str, Any]] = {}
    for market_mode, (avg_model, devig_method, rho, ci_cutoff, frame) in best_by_mode.items():
        labelled = frame.copy()
        labelled.insert(0, "market_mode", market_mode)
        labelled["devig_method"] = devig_method
        labelled["dixon_coles_rho"] = rho
        labelled["ci_cutoff"] = ci_cutoff
        result_frames.append(labelled)
        mode_summaries[market_mode] = summarize_mode(market_mode, frame, devig_method, rho, ci_cutoff)

    details = pd.concat(result_frames, ignore_index=True) if result_frames else pd.DataFrame()
    details_path = out_dir / "the_odds_api_historical_backtest_results.csv"
    details.drop(columns=["distribution"], errors="ignore").to_csv(details_path, index=False)

    reliability_path = out_dir / "the_odds_api_historical_reliability_cells.csv"
    reliability_source = result_frames[-1] if result_frames else pd.DataFrame()
    reliability_cells(reliability_source).to_csv(reliability_path, index=False)

    comparison_path = out_dir / "the_odds_api_historical_mode_comparison.csv"
    pd.DataFrame(mode_summaries.values()).to_csv(comparison_path, index=False)

    summary = {
        "mode": "the_odds_api_historical_h2h_totals_backtest",
        "fixtures": str(args.fixtures),
        "snapshots_json": str(args.snapshots_json or out_dir / "the_odds_api_historical_snapshots.json"),
        "fixture_count": int(len(fixtures)),
        "historical_event_count": int(len(matches)),
        "matched_fixture_count": int(len(matched)),
        "comparison_subset": "matches with both h2h and totals" if compare_on_totals_subset else "matches with h2h",
        "market_modes": mode_summaries,
        "totals_delta_vs_h2h": _mode_delta(mode_summaries, "h2h_totals", "h2h"),
        "calibration_results": str(calibration_path),
        "mode_comparison": str(comparison_path),
        "backtest_results": str(details_path),
        "reliability_cells": str(reliability_path),
    }
    summary_path = out_dir / "the_odds_api_historical_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


def fetch_historical_snapshots(args: argparse.Namespace, config: AppConfig, out_dir: Path) -> Any:
    api_key = env_value(config.providers.the_odds_api)
    if not api_key:
        raise ProviderUnavailable("The Odds API key is not configured")
    dates = _string_grid(args.date_grid)
    if not dates:
        raise ProviderUnavailable("--fetch requires --date-grid with one or more ISO8601 timestamps")

    provider_config = config.providers.the_odds_api
    base_url = provider_config.get("base_url", "https://api.the-odds-api.com")
    sport_key = args.sport_key or provider_config.get("sport_key", "soccer_fifa_world_cup")
    endpoint = f"{base_url.rstrip('/')}/v4/historical/sports/{sport_key}/odds"
    cache = JsonCache(config.paths.cache_dir)
    snapshots: list[dict[str, Any]] = []
    for snapshot_date in dates:
        snapshots.append(
            get_json(
                endpoint,
                params={
                    "apiKey": api_key,
                    "regions": args.regions or provider_config.get("regions", "eu"),
                    "markets": args.markets or provider_config.get("markets", "h2h,totals"),
                    "oddsFormat": provider_config.get("odds_format", "decimal"),
                    "dateFormat": "iso",
                    "bookmakers": args.bookmakers or provider_config.get("bookmakers"),
                    "date": snapshot_date,
                },
                cache=cache,
                namespace="the_odds_api_historical",
                rate_limit_seconds=float(provider_config.get("rate_limit_seconds", 0.0)),
            )
        )
    payload = {"snapshots": snapshots}
    (out_dir / "the_odds_api_historical_snapshots.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def historical_events_from_snapshots(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict) and "snapshots" in raw:
        snapshots = raw["snapshots"]
    elif isinstance(raw, dict) and "responses" in raw:
        snapshots = raw["responses"]
    else:
        snapshots = raw if isinstance(raw, list) else [raw]

    if _looks_like_event_list(snapshots):
        snapshots = [{"timestamp": "", "data": snapshots}]

    events: list[dict[str, Any]] = []
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        timestamp = str(snapshot.get("timestamp") or snapshot.get("date") or "")
        data = snapshot.get("data", snapshot)
        if isinstance(data, dict) and "data" in data:
            timestamp = str(data.get("timestamp") or timestamp)
            data = data["data"]
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            continue
        for event in data:
            if not isinstance(event, dict) or "bookmakers" not in event:
                continue
            copied = dict(event)
            copied["_snapshot_timestamp"] = timestamp
            events.append(copied)
    return events


def load_fixture_results(path: str | Path) -> pd.DataFrame:
    fixtures = pd.read_csv(path).fillna("")
    required = {"home_team", "away_team", "home_goals", "away_goals"}
    missing = required - set(fixtures.columns)
    if missing:
        raise ValueError(f"Fixture results file is missing required columns: {sorted(missing)}")
    if "match_id" not in fixtures.columns:
        fixtures["match_id"] = [
            f"fixture-{idx}-{canonical_team_key(row.home_team)}-{canonical_team_key(row.away_team)}"
            for idx, row in fixtures.iterrows()
        ]
    return fixtures


def match_events_to_fixtures(matches: list[MatchOdds], fixtures: pd.DataFrame) -> list[dict[str, Any]]:
    events_by_pair: dict[tuple[str, str], list[MatchOdds]] = {}
    for match in matches:
        key = (canonical_team_key(match.home_team), canonical_team_key(match.away_team))
        events_by_pair.setdefault(key, []).append(match)

    matched: list[dict[str, Any]] = []
    used_event_ids: set[str] = set()
    for _, fixture in fixtures.iterrows():
        key = (canonical_team_key(str(fixture["home_team"])), canonical_team_key(str(fixture["away_team"])))
        candidates = [candidate for candidate in events_by_pair.get(key, []) if candidate.match_id not in used_event_ids]
        if not candidates:
            continue
        fixture_date = _date_only(fixture.get("commence_time") or fixture.get("date"))
        if fixture_date:
            same_date = [candidate for candidate in candidates if _date_only(candidate.commence_time) == fixture_date]
            if same_date:
                candidates = same_date
        selected = max(candidates, key=_snapshot_sort_key)
        used_event_ids.add(selected.match_id)
        matched.append({"fixture": fixture.to_dict(), "match": selected})
    return matched


def evaluate_historical_matches(matched: list[dict[str, Any]], config: AppConfig, market_mode: str) -> pd.DataFrame:
    ratings = RatingsStore()
    model = OddsToScorelineModel(config.model, ratings)
    decision = SuperbruDecisionEngine(config.superbru, config.model.candidate_grid_goals)
    rows: list[dict[str, Any]] = []
    distributions: dict[str, DistributionResult] = {}

    for item in sorted(matched, key=lambda row: str(row["match"].commence_time)):
        raw_fixture = item["fixture"]
        match = filter_market_mode(item["match"], market_mode)
        if not _has_market(match, "h2h"):
            continue
        distribution = model.build_distribution(match)
        prediction = decision.predict(distribution)
        actual_home = int(float(raw_fixture["home_goals"]))
        actual_away = int(float(raw_fixture["away_goals"]))
        naive_home, naive_away = naive_baseline_pick(match, distribution)
        row = {
            "match_id": str(raw_fixture["match_id"]),
            "event_id": match.match_id,
            "commence_time": match.commence_time,
            "home_team": match.home_team,
            "away_team": match.away_team,
            "actual_scoreline": f"{actual_home}-{actual_away}",
            "model_scoreline": prediction.recommended.scoreline,
            "model_expected_points": prediction.recommended.expected_points,
            "model_points": score_actual_prediction(
                prediction.recommended.home_goals,
                prediction.recommended.away_goals,
                actual_home,
                actual_away,
                config.superbru.ci_cutoff,
            ),
            "naive_scoreline": f"{naive_home}-{naive_away}",
            "naive_points": score_actual_prediction(naive_home, naive_away, actual_home, actual_away, config.superbru.ci_cutoff),
            "p_exact": prediction.recommended.p_exact,
            "p_close": prediction.recommended.p_close,
            "p_outcome": prediction.recommended.p_outcome,
            "lambda_home": distribution.lambda_home,
            "lambda_away": distribution.lambda_away,
            "has_totals": distribution.fair_total is not None,
            "fair_total_line": distribution.diagnostics.get("fair_total_line"),
            "fair_over": distribution.diagnostics.get("fair_over"),
            "model_over": distribution.diagnostics.get("model_over"),
            "home_goals": actual_home,
            "away_goals": actual_away,
            "distribution": str(raw_fixture["match_id"]),
        }
        rows.append(row)
        distributions[str(raw_fixture["match_id"])] = distribution
        ratings.update_results(
            [
                MatchResult(
                    match_id=str(raw_fixture["match_id"]),
                    commence_time=match.commence_time,
                    home_team=match.home_team,
                    away_team=match.away_team,
                    home_goals=actual_home,
                    away_goals=actual_away,
                    venue_country=match.venue_country,
                )
            ]
        )

    frame = pd.DataFrame(rows)
    frame.attrs["distributions"] = distributions
    return frame


def filter_market_mode(match: MatchOdds, market_mode: str) -> MatchOdds:
    market_mode = market_mode.strip().lower()
    allowed = {"h2h", "totals"} if market_mode in {"h2h_totals", "h2h+totals", "h2h,totals"} else {"h2h"}
    return replace(match, markets={key: value for key, value in match.markets.items() if key in allowed})


def summarize_mode(market_mode: str, frame: pd.DataFrame, devig_method: str, rho: float, ci_cutoff: float) -> dict[str, Any]:
    if frame.empty:
        return {
            "market_mode": market_mode,
            "devig_method": devig_method,
            "dixon_coles_rho": rho,
            "ci_cutoff": ci_cutoff,
            "matches": 0,
            "avg_model_points": 0.0,
            "avg_naive_points": 0.0,
            "edge_vs_naive": 0.0,
        }
    return {
        "market_mode": market_mode,
        "devig_method": devig_method,
        "dixon_coles_rho": rho,
        "ci_cutoff": ci_cutoff,
        "matches": int(len(frame)),
        "avg_model_points": float(frame["model_points"].mean()),
        "avg_naive_points": float(frame["naive_points"].mean()),
        "edge_vs_naive": float(frame["model_points"].mean() - frame["naive_points"].mean()),
        "exact_hits": int((frame["model_points"] == 3.0).sum()),
        "close_hits": int((frame["model_points"] == 1.5).sum()),
        "outcome_only_hits": int((frame["model_points"] == 1.0).sum()),
        "misses": int((frame["model_points"] == 0.0).sum()),
        "outcome_accuracy": float((frame["model_points"] > 0.0).mean()),
        "exact_rate": float((frame["model_points"] == 3.0).mean()),
    }


def _mode_delta(summaries: dict[str, dict[str, Any]], test_mode: str, baseline_mode: str) -> float | None:
    if test_mode not in summaries or baseline_mode not in summaries:
        return None
    return float(summaries[test_mode]["avg_model_points"] - summaries[baseline_mode]["avg_model_points"])


def _has_market(match: MatchOdds, key: str) -> bool:
    return bool(match.markets.get(key))


def _looks_like_event_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, dict) and "bookmakers" in item for item in value)


def _snapshot_sort_key(match: MatchOdds) -> tuple[str, str]:
    return (str(match.raw.get("_snapshot_timestamp") or ""), str(match.commence_time or ""))


def _date_only(value: Any) -> str:
    if value in ("", None):
        return ""
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(parsed):
        parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return ""
    return parsed.date().isoformat()


def _float_grid(raw: str) -> list[float]:
    if not raw:
        return []
    return [float(item.strip()) for item in raw.split(",") if item.strip()]


def _string_grid(raw: str) -> list[str]:
    if not raw:
        return []
    return [item.strip().lower() for item in raw.split(",") if item.strip()]
