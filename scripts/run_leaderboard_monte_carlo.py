from __future__ import annotations

import argparse
import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from superbru_score_engine.config import load_config
from superbru_score_engine.ingest.offline import OfflineJsonProvider
from superbru_score_engine.model import OddsToScorelineModel
from superbru_score_engine.model.ratings import RatingsStore
from superbru_score_engine.model.team_names import canonical_team_key


DEFAULT_LEADERBOARD = [
    {"player": "Danie", "current_points": 29.0},
    {"player": "Dheben", "current_points": 28.0},
    {"player": "Bijal", "current_points": 28.0},
    {"player": "Peter", "current_points": 27.5},
    {"player": "rob87", "current_points": 27.0},
    {"player": "Leonard", "current_points": 26.5},
    {"player": "Kea", "current_points": 26.0},
]

DEFAULT_PROFILES = [
    {"player": "Dheben", "raw_weight": 0.35, "top1_weight": 0.25, "top2_weight": 0.15, "top3_weight": 0.08, "modal_weight": 0.10, "chase_weight": 0.07, "chalk_weight": 0.10},
    {"player": "Bijal", "raw_weight": 0.30, "top1_weight": 0.25, "top2_weight": 0.12, "top3_weight": 0.06, "modal_weight": 0.12, "chase_weight": 0.05, "chalk_weight": 0.18},
    {"player": "Peter", "raw_weight": 0.25, "top1_weight": 0.22, "top2_weight": 0.15, "top3_weight": 0.12, "modal_weight": 0.08, "chase_weight": 0.18, "chalk_weight": 0.08},
    {"player": "rob87", "raw_weight": 0.24, "top1_weight": 0.20, "top2_weight": 0.16, "top3_weight": 0.14, "modal_weight": 0.08, "chase_weight": 0.18, "chalk_weight": 0.08},
    {"player": "Leonard", "raw_weight": 0.32, "top1_weight": 0.24, "top2_weight": 0.14, "top3_weight": 0.08, "modal_weight": 0.10, "chase_weight": 0.08, "chalk_weight": 0.12},
    {"player": "Kea", "raw_weight": 0.26, "top1_weight": 0.20, "top2_weight": 0.16, "top3_weight": 0.14, "modal_weight": 0.08, "chase_weight": 0.16, "chalk_weight": 0.10},
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run full-round leaderboard Monte Carlo for Superbru pool defence")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--fixtures", default="data/fixtures_real.csv")
    parser.add_argument("--odds-json", default="data/odds_snapshot_real.json")
    parser.add_argument("--predictions-csv", default="outputs/latest/predictions.csv")
    parser.add_argument("--leaderboard-csv", default="inputs/pool_leaderboard.csv")
    parser.add_argument("--chaser-profiles-csv", default="inputs/chaser_profiles.csv")
    parser.add_argument("--leader-player", default="Danie")
    parser.add_argument("--chasers", default="Dheben,Bijal,Peter,rob87,Leonard,Kea")
    parser.add_argument("--simulations", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=20260620)
    parser.add_argument("--max-ev-loss-per-switch", type=float, default=0.025)
    parser.add_argument("--max-total-ev-loss", type=float, default=0.10)
    parser.add_argument("--min-lead-prob-gain", type=float, default=0.005)
    parser.add_argument("--high-risk-min-lead-prob-gain", type=float, default=0.015)
    parser.add_argument("--different-outcome-min-gain", type=float, default=0.020)
    parser.add_argument("--min-z", type=float, default=2.0)
    parser.add_argument("--exclude-match", action="append", default=[])
    parser.add_argument("--out-dir", default="outputs/leaderboard_mc")
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    value = str(value).strip()
    return "" if value.lower() == "nan" else value


def fnum(value: Any, default: float = 0.0) -> float:
    try:
        text = txt(value)
        if not text:
            return default
        return float(text)
    except Exception:
        return default


def parse_score(scoreline: Any) -> tuple[int, int] | None:
    text = txt(scoreline)
    if "-" not in text:
        return None
    left, right = text.split("-", 1)
    if not left.strip().isdigit() or not right.strip().isdigit():
        return None
    return int(left.strip()), int(right.strip())


def scoreline(home: int, away: int) -> str:
    return f"{home}-{away}"


def outcome_from_scoreline(value: Any) -> str:
    parsed = parse_score(value)
    if parsed is None:
        return ""
    home, away = parsed
    if home > away:
        return "home"
    if home < away:
        return "away"
    return "draw"


def chalk_scores(raw_scoreline: str) -> list[str]:
    outcome = outcome_from_scoreline(raw_scoreline)
    if outcome == "home":
        return ["1-0", "2-0", "2-1", "3-0", "3-1"]
    if outcome == "away":
        return ["0-1", "0-2", "1-2", "0-3", "1-3"]
    return ["0-0", "1-1", "2-2"]


def ensure_default_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def load_inputs(args: argparse.Namespace) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    leaderboard_path = Path(args.leaderboard_csv)
    profiles_path = Path(args.chaser_profiles_csv)
    ensure_default_csv(leaderboard_path, DEFAULT_LEADERBOARD)
    ensure_default_csv(profiles_path, DEFAULT_PROFILES)

    predictions = pd.read_csv(args.predictions_csv).fillna("")
    leaderboard = pd.read_csv(leaderboard_path).fillna("")
    profiles = pd.read_csv(profiles_path).fillna("")
    return predictions, leaderboard, profiles


def exclude_row(row: pd.Series, exclusions: list[str]) -> bool:
    home = txt(row.get("home_team")).lower()
    away = txt(row.get("away_team")).lower()
    pair_pipe = f"{home}|{away}"
    pair_vs = f"{home} vs {away}"
    for item in exclusions:
        item = item.strip().lower()
        if item and (item in pair_pipe or item in pair_vs):
            return True
    return False


def merge_fixture_metadata(matches: list[Any], fixtures_path: str | Path) -> list[Any]:
    fixtures_path = Path(fixtures_path)
    if not fixtures_path.exists():
        return matches

    fixtures = pd.read_csv(fixtures_path).fillna("")
    by_id = {str(row.get("match_id", "")): row for _, row in fixtures.iterrows() if str(row.get("match_id", ""))}
    by_teams = {
        (canonical_team_key(str(row.get("home_team", ""))), canonical_team_key(str(row.get("away_team", "")))): row
        for _, row in fixtures.iterrows()
    }

    merged = []
    for match in matches:
        row = by_id.get(match.match_id)
        if row is None:
            row = by_teams.get((canonical_team_key(match.home_team), canonical_team_key(match.away_team)))
        if row is None:
            merged.append(match)
            continue

        raw = dict(match.raw)
        for column in row.index:
            raw[column] = row[column]

        merged.append(
            replace(
                match,
                commence_time=str(row.get("commence_time") or match.commence_time),
                neutral=bool_value(row.get("neutral"), match.neutral),
                venue_country=str(row.get("venue_country") or match.venue_country or ""),
                stage=str(row.get("stage") or match.stage or ""),
                raw=raw,
            )
        )
    return merged


def bool_value(value: Any, default: bool) -> bool:
    if value in ("", None):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def row_key(row: pd.Series | dict[str, Any]) -> tuple[str, str, str]:
    return (
        txt(row.get("match_id")),
        canonical_team_key(txt(row.get("home_team"))),
        canonical_team_key(txt(row.get("away_team"))),
    )


def match_key(match: Any) -> tuple[str, str, str]:
    return (
        txt(match.match_id),
        canonical_team_key(txt(match.home_team)),
        canonical_team_key(txt(match.away_team)),
    )


def build_score_matrices(args: argparse.Namespace, predictions: pd.DataFrame) -> dict[tuple[str, str, str], np.ndarray]:
    config = load_config(args.config)
    ratings = RatingsStore(config.paths.ratings_store, config.ratings)
    matches = OfflineJsonProvider(args.odds_json).fetch_odds()
    matches = merge_fixture_metadata(matches, args.fixtures)

    wanted = {row_key(row) for _, row in predictions.iterrows()}
    by_pair = {(key[1], key[2]) for key in wanted}

    model = OddsToScorelineModel(config.model, ratings)
    matrices: dict[tuple[str, str, str], np.ndarray] = {}

    for match in matches:
        key = match_key(match)
        pair = (key[1], key[2])
        if key not in wanted and pair not in by_pair:
            continue
        distribution = model.build_distribution(match)
        matrices[key] = np.asarray(distribution.matrix, dtype=float)

    return matrices


def find_matrix(row: pd.Series, matrices: dict[tuple[str, str, str], np.ndarray]) -> np.ndarray:
    key = row_key(row)
    if key in matrices:
        return matrices[key]
    pair = (key[1], key[2])
    for stored_key, matrix in matrices.items():
        if (stored_key[1], stored_key[2]) == pair:
            return matrix
    raise KeyError(f"No score matrix found for {txt(row.get('home_team'))} vs {txt(row.get('away_team'))}")


def superbru_points_vector(pred_home: int, pred_away: int, actual_home: np.ndarray, actual_away: np.ndarray, ci_cutoff: float) -> np.ndarray:
    exact = (actual_home == pred_home) & (actual_away == pred_away)

    pred_outcome = 0
    if pred_home > pred_away:
        pred_outcome = 1
    elif pred_home < pred_away:
        pred_outcome = -1

    actual_outcome = np.zeros_like(actual_home, dtype=np.int8)
    actual_outcome[actual_home > actual_away] = 1
    actual_outcome[actual_home < actual_away] = -1
    same_outcome = actual_outcome == pred_outcome

    pred_gd = pred_home - pred_away
    actual_gd = actual_home - actual_away
    pred_total = pred_home + pred_away
    actual_total = actual_home + actual_away
    close = same_outcome & ((np.abs(pred_gd - actual_gd) + np.abs(pred_total - actual_total) / 2.0) <= ci_cutoff)

    points = np.zeros(actual_home.shape[0], dtype=float)
    points[same_outcome] = 1.0
    points[close] = 1.5
    points[exact] = 3.0
    return points


def expected_points_from_matrix(matrix: np.ndarray, candidate: str, ci_cutoff: float) -> float:
    parsed = parse_score(candidate)
    if parsed is None:
        return 0.0
    pred_home, pred_away = parsed
    expected = 0.0
    for actual_home in range(matrix.shape[0]):
        for actual_away in range(matrix.shape[1]):
            prob = float(matrix[actual_home, actual_away])
            if prob <= 0:
                continue
            points = superbru_points_vector(
                pred_home,
                pred_away,
                np.array([actual_home], dtype=int),
                np.array([actual_away], dtype=int),
                ci_cutoff,
            )[0]
            expected += prob * float(points)
    return float(expected)


def sample_actual_scores(matrix: np.ndarray, simulations: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    probs = matrix.astype(float).reshape(-1)
    total = probs.sum()
    if total <= 0:
        raise ValueError("Score matrix has no probability mass")
    probs = probs / total
    idx = rng.choice(len(probs), size=simulations, replace=True, p=probs)
    actual_home = idx // matrix.shape[1]
    actual_away = idx % matrix.shape[1]
    return actual_home.astype(int), actual_away.astype(int)


def add_weight(weights: dict[str, float], score: Any, weight: float) -> None:
    score = txt(score)
    if parse_score(score) is None:
        return
    weights[score] = weights.get(score, 0.0) + max(0.0, float(weight))


def normalise(weights: dict[str, float]) -> dict[str, float]:
    total = sum(max(0.0, value) for value in weights.values())
    if total <= 0:
        return {}
    return {key: max(0.0, value) / total for key, value in weights.items()}


def candidate_scores(row: pd.Series) -> list[str]:
    candidates: list[str] = []
    for field in [
        "recommended_scoreline",
        "raw_ev_scoreline",
        "top1_scoreline",
        "top2_scoreline",
        "top3_scoreline",
        "modal_scoreline",
        "private_chase_scoreline",
        "conservative_scoreline",
        "exact_chase_scoreline",
    ]:
        score = txt(row.get(field))
        if parse_score(score) is not None and score not in candidates:
            candidates.append(score)

    for score in chalk_scores(txt(row.get("recommended_scoreline"))):
        if score not in candidates:
            candidates.append(score)

    return candidates


def chaser_distribution(row: pd.Series, profile: pd.Series) -> dict[str, float]:
    weights: dict[str, float] = {}
    add_weight(weights, row.get("recommended_scoreline"), fnum(profile.get("raw_weight"), 0.30))
    add_weight(weights, row.get("top1_scoreline"), fnum(profile.get("top1_weight"), 0.25))
    add_weight(weights, row.get("top2_scoreline"), fnum(profile.get("top2_weight"), 0.15))
    add_weight(weights, row.get("top3_scoreline"), fnum(profile.get("top3_weight"), 0.10))
    add_weight(weights, row.get("modal_scoreline"), fnum(profile.get("modal_weight"), 0.10))
    add_weight(weights, row.get("private_chase_scoreline"), fnum(profile.get("chase_weight"), 0.10))

    chalk = chalk_scores(txt(row.get("recommended_scoreline")))
    chalk_weight = fnum(profile.get("chalk_weight"), 0.10)
    if chalk:
        for score in chalk:
            add_weight(weights, score, chalk_weight / len(chalk))

    return normalise(weights)


def sample_chaser_picks(row: pd.Series, profile: pd.Series, simulations: int, rng: np.random.Generator) -> np.ndarray:
    dist = chaser_distribution(row, profile)
    if not dist:
        return np.repeat(txt(row.get("recommended_scoreline")), simulations)
    labels = np.array(list(dist.keys()), dtype=object)
    probs = np.array([dist[label] for label in labels], dtype=float)
    probs = probs / probs.sum()
    return rng.choice(labels, size=simulations, replace=True, p=probs)


def points_for_pick_array(picks: np.ndarray, actual_home: np.ndarray, actual_away: np.ndarray, ci_cutoff: float) -> np.ndarray:
    result = np.zeros(actual_home.shape[0], dtype=float)
    for pick in np.unique(picks):
        parsed = parse_score(pick)
        if parsed is None:
            continue
        mask = picks == pick
        result[mask] = superbru_points_vector(parsed[0], parsed[1], actual_home[mask], actual_away[mask], ci_cutoff)
    return result


def points_for_fixed_pick(pick: str, actual_home: np.ndarray, actual_away: np.ndarray, ci_cutoff: float) -> np.ndarray:
    parsed = parse_score(pick)
    if parsed is None:
        return np.zeros(actual_home.shape[0], dtype=float)
    return superbru_points_vector(parsed[0], parsed[1], actual_home, actual_away, ci_cutoff)


def baseline_card(rows: list[pd.Series]) -> dict[int, str]:
    return {idx: txt(row.get("recommended_scoreline")) for idx, row in enumerate(rows)}


def evaluate_card(
    card: dict[int, str],
    actuals: dict[int, tuple[np.ndarray, np.ndarray]],
    chaser_totals: dict[str, np.ndarray],
    leader_start: float,
    ci_cutoff: float,
) -> tuple[float, float, np.ndarray]:
    simulations = next(iter(chaser_totals.values())).shape[0] if chaser_totals else next(iter(actuals.values()))[0].shape[0]
    leader_points = np.full(simulations, leader_start, dtype=float)

    for idx, pick in card.items():
        actual_home, actual_away = actuals[idx]
        leader_points += points_for_fixed_pick(pick, actual_home, actual_away, ci_cutoff)

    if chaser_totals:
        max_chaser = np.maximum.reduce(list(chaser_totals.values()))
        first = leader_points > max_chaser
        tied_first = leader_points >= max_chaser
    else:
        first = np.ones(simulations, dtype=bool)
        tied_first = np.ones(simulations, dtype=bool)

    return float(first.mean()), float(tied_first.mean()), first


def build_chaser_totals(
    rows: list[pd.Series],
    actuals: dict[int, tuple[np.ndarray, np.ndarray]],
    profiles: pd.DataFrame,
    leaderboard: pd.DataFrame,
    chaser_names: list[str],
    simulations: int,
    ci_cutoff: float,
    rng: np.random.Generator,
) -> dict[str, np.ndarray]:
    points_lookup = {txt(row.get("player")): fnum(row.get("current_points")) for _, row in leaderboard.iterrows()}
    profile_lookup = {txt(row.get("player")): row for _, row in profiles.iterrows()}
    default_profile = pd.Series(DEFAULT_PROFILES[0])

    totals: dict[str, np.ndarray] = {}
    for player in chaser_names:
        profile = profile_lookup.get(player, default_profile)
        total = np.full(simulations, points_lookup.get(player, 0.0), dtype=float)
        for idx, row in enumerate(rows):
            actual_home, actual_away = actuals[idx]
            picks = sample_chaser_picks(row, profile, simulations, rng)
            total += points_for_pick_array(picks, actual_home, actual_away, ci_cutoff)
        totals[player] = total
    return totals


def paired_delta_stats(base_win: np.ndarray, candidate_win: np.ndarray) -> tuple[float, float, float]:
    diff = candidate_win.astype(float) - base_win.astype(float)
    delta = float(diff.mean())
    if diff.shape[0] <= 1:
        return delta, 0.0, 0.0
    se = float(diff.std(ddof=1) / math.sqrt(diff.shape[0]))
    z = delta / se if se > 0 else (float("inf") if delta > 0 else 0.0)
    return delta, se, z


def optimise(
    rows: list[pd.Series],
    matrices: dict[int, np.ndarray],
    actuals: dict[int, tuple[np.ndarray, np.ndarray]],
    chaser_totals: dict[str, np.ndarray],
    leader_start: float,
    ci_cutoff: float,
    args: argparse.Namespace,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    card = baseline_card(rows)
    baseline_first, baseline_tied, baseline_win = evaluate_card(card, actuals, chaser_totals, leader_start, ci_cutoff)

    candidate_rows: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    total_ev_loss = 0.0

    for idx, row in enumerate(rows):
        raw_pick = txt(row.get("recommended_scoreline"))
        raw_ev = expected_points_from_matrix(matrices[idx], raw_pick, ci_cutoff)
        base_card_for_match = dict(card)
        base_first, _, base_win = evaluate_card(base_card_for_match, actuals, chaser_totals, leader_start, ci_cutoff)

        best_pass: dict[str, Any] | None = None
        for candidate in candidate_scores(row):
            candidate_ev = expected_points_from_matrix(matrices[idx], candidate, ci_cutoff)
            ev_loss = max(0.0, raw_ev - candidate_ev)

            test_card = dict(card)
            test_card[idx] = candidate
            p_first, p_tied, candidate_win = evaluate_card(test_card, actuals, chaser_totals, leader_start, ci_cutoff)
            delta, se, z = paired_delta_stats(base_win, candidate_win)

            min_gain = float(args.min_lead_prob_gain)
            if txt(row.get("risk_tier")).lower() == "high" or txt(row.get("confidence_tier")).lower() == "fragile":
                min_gain = max(min_gain, float(args.high_risk_min_lead_prob_gain))
            if outcome_from_scoreline(candidate) != outcome_from_scoreline(raw_pick):
                min_gain = max(min_gain, float(args.different_outcome_min_gain))

            passes = True
            reasons: list[str] = []
            if candidate == raw_pick:
                passes = False
                reasons.append("raw_pick")
            if delta < min_gain:
                passes = False
                reasons.append("insufficient_p_first_gain")
            if se > 0 and z < float(args.min_z):
                passes = False
                reasons.append("not_statistically_meaningful")
            if ev_loss > float(args.max_ev_loss_per_switch):
                passes = False
                reasons.append("ev_loss_too_high")
            if total_ev_loss + ev_loss > float(args.max_total_ev_loss):
                passes = False
                reasons.append("round_ev_loss_cap")

            record = {
                "commence_time": txt(row.get("commence_time")),
                "home_team": txt(row.get("home_team")),
                "away_team": txt(row.get("away_team")),
                "raw_pick": raw_pick,
                "candidate_pick": candidate,
                "raw_ev_full_matrix": round(raw_ev, 6),
                "candidate_ev_full_matrix": round(candidate_ev, 6),
                "ev_loss": round(ev_loss, 6),
                "baseline_p_finish_first": round(base_first, 6),
                "candidate_p_finish_first": round(p_first, 6),
                "candidate_p_finish_first_or_tied": round(p_tied, 6),
                "delta_p_finish_first": round(delta, 6),
                "paired_se": round(se, 6),
                "z_score": round(z, 3) if math.isfinite(z) else "inf",
                "risk_tier": txt(row.get("risk_tier")),
                "confidence_tier": txt(row.get("confidence_tier")),
                "passes_hard_rule": passes,
                "reject_reasons": ";".join(reasons),
            }
            candidate_rows.append(record)

            if passes and (best_pass is None or delta > float(best_pass["delta_p_finish_first"])):
                best_pass = record

        if best_pass is not None:
            card[idx] = str(best_pass["candidate_pick"])
            total_ev_loss += float(best_pass["ev_loss"])
            accepted.append(best_pass)

    final_first, final_tied, _ = evaluate_card(card, actuals, chaser_totals, leader_start, ci_cutoff)

    final_rows: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        raw_pick = txt(row.get("recommended_scoreline"))
        final_pick = card[idx]
        raw_ev = expected_points_from_matrix(matrices[idx], raw_pick, ci_cutoff)
        final_ev = expected_points_from_matrix(matrices[idx], final_pick, ci_cutoff)
        final_rows.append(
            {
                "commence_time": txt(row.get("commence_time")),
                "home_team": txt(row.get("home_team")),
                "away_team": txt(row.get("away_team")),
                "raw_pick": raw_pick,
                "leader_mc_pick": final_pick,
                "switched": final_pick != raw_pick,
                "raw_ev_full_matrix": round(raw_ev, 6),
                "leader_mc_ev_full_matrix": round(final_ev, 6),
                "ev_loss": round(max(0.0, raw_ev - final_ev), 6),
                "risk_tier": txt(row.get("risk_tier")),
                "confidence_tier": txt(row.get("confidence_tier")),
            }
        )

    summary = {
        "simulations": int(args.simulations),
        "leader_player": args.leader_player,
        "leader_start_points": leader_start,
        "chasers": list(chaser_totals.keys()),
        "baseline_p_finish_first": round(baseline_first, 6),
        "baseline_p_finish_first_or_tied": round(baseline_tied, 6),
        "final_p_finish_first": round(final_first, 6),
        "final_p_finish_first_or_tied": round(final_tied, 6),
        "delta_p_finish_first": round(final_first - baseline_first, 6),
        "accepted_switches": accepted,
        "total_ev_loss": round(float(sum(row["ev_loss"] for row in final_rows)), 6),
    }

    return pd.DataFrame(final_rows), pd.DataFrame(candidate_rows), summary


def main() -> int:
    args = build_parser().parse_args()
    rng = np.random.default_rng(args.seed)

    predictions, leaderboard, profiles = load_inputs(args)
    rows = [row for _, row in predictions.iterrows() if not exclude_row(row, args.exclude_match)]
    if not rows:
        raise ValueError("No prediction rows remain after exclusions")

    config = load_config(args.config)
    ci_cutoff = float(config.superbru.ci_cutoff)

    matrices_by_key = build_score_matrices(args, predictions)
    matrices: dict[int, np.ndarray] = {}
    actuals: dict[int, tuple[np.ndarray, np.ndarray]] = {}

    for idx, row in enumerate(rows):
        matrix = find_matrix(row, matrices_by_key)
        matrices[idx] = matrix
        actuals[idx] = sample_actual_scores(matrix, int(args.simulations), rng)

    points_lookup = {txt(row.get("player")): fnum(row.get("current_points")) for _, row in leaderboard.iterrows()}
    if args.leader_player not in points_lookup:
        raise ValueError(f"Leader player {args.leader_player!r} not found in {args.leaderboard_csv}")
    leader_start = points_lookup[args.leader_player]

    chaser_names = [name.strip() for name in args.chasers.split(",") if name.strip()]
    chaser_totals = build_chaser_totals(rows, actuals, profiles, leaderboard, chaser_names, int(args.simulations), ci_cutoff, rng)

    picks, candidates, summary = optimise(rows, matrices, actuals, chaser_totals, leader_start, ci_cutoff, args)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    picks_path = out_dir / "leader_mc_picks.csv"
    candidates_path = out_dir / "leader_mc_candidate_tests.csv"
    summary_path = out_dir / "leader_mc_summary.json"

    picks.to_csv(picks_path, index=False)
    candidates.to_csv(candidates_path, index=False)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    display_cols = [
        "commence_time",
        "home_team",
        "away_team",
        "raw_pick",
        "leader_mc_pick",
        "switched",
        "ev_loss",
        "risk_tier",
        "confidence_tier",
    ]
    print()
    print(picks[display_cols].to_string(index=False))
    print()
    print(json.dumps(summary, indent=2))
    print()
    print(f"Wrote {picks_path}")
    print(f"Wrote {candidates_path}")
    print(f"Wrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
