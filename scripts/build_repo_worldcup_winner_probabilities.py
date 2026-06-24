#!/usr/bin/env python3
"""Build Polymarket model probabilities from repository match-probability data.

This is the mechanical bridge for the Polymarket mispricing bot:

repo match probabilities -> relative team strengths -> outright winner probabilities
-> Polymarket YES token probabilities.

It intentionally writes the simple bot input format:

    inputs/polymarket/model_probabilities.csv
    token_id,probability

The current converter is a first-pass strength-normalisation model, not a full
bracket Monte Carlo. Its purpose is to make the bot use repo-derived fairs
instead of manual/dummy probabilities. Upgrade path: replace the probability
conversion step with a bracket-aware tournament simulator while preserving the
same output contract.
"""
from __future__ import annotations

import csv
import math
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
SNAPSHOT = ROOT / "outputs/polymarket/market_snapshot.csv"
OUT = ROOT / "inputs/polymarket/model_probabilities.csv"
DETAIL = ROOT / "outputs/polymarket/repo_worldcup_winner_probabilities.csv"

SOURCE_FILES = [
    ROOT / "outputs/market_odds_history/market_odds_history.csv",
    ROOT / "outputs/oddspedia_probability_extract/oddspedia_grid_quality.csv",
    ROOT / "inputs/smartbet_grids/oddspedia_probability_grids_auto.csv",
    ROOT / "inputs/smartbet_grids/converted_old_oddspedia_score_grids.csv",
]

ALIASES = {
    "usa": "unitedstates",
    "us": "unitedstates",
    "unitedstatesofamerica": "unitedstates",
    "czech": "czechia",
    "czechrepublic": "czechia",
    "bosnia": "bosniaandherzegovina",
    "bosniaherzegovina": "bosniaandherzegovina",
    "cotedivoire": "ivorycoast",
    "congodr": "drcongo",
    "drc": "drcongo",
    "democraticrepublicofcongo": "drcongo",
    "newzealand": "newzealand",
    "saudiarabia": "saudiarabia",
    "southafrica": "southafrica",
    "southkorea": "southkorea",
    "capeverde": "capeverde",
    "curaao": "curacao",
}


def norm(value: object) -> str:
    key = re.sub(r"[^a-z0-9]+", "", str(value or "").lower())
    return ALIASES.get(key, key)


def parse_float(value: object) -> float | None:
    try:
        x = float(str(value).strip().replace("%", ""))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    return x


def parse_probability(value: object) -> float | None:
    x = parse_float(value)
    if x is None:
        return None
    if 0 <= x <= 1:
        return x
    if 1 < x <= 100:
        return x / 100.0
    return None


def logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    except Exception as exc:  # noqa: BLE001 - converter should not kill the pipeline on one stale file
        print(f"converter warning: could not read {path}: {exc}", flush=True)
        return []


def winner_tokens_from_snapshot() -> list[dict[str, object]]:
    if not SNAPSHOT.exists():
        raise SystemExit(f"No market snapshot found at {SNAPSHOT}; run the Polymarket scanner first.")

    tokens: list[dict[str, object]] = []
    with SNAPSHOT.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            question = row.get("question", "")
            match = re.match(r"Will (.*?) win the 2026 FIFA World Cup\?", question)
            if row.get("outcome") != "Yes" or not match or not row.get("token_id"):
                continue
            team = match.group(1).strip()
            tokens.append(
                {
                    "team": team,
                    "key": norm(team),
                    "token_id": row["token_id"],
                    "market_bid": parse_probability(row.get("best_bid")),
                    "market_ask": parse_probability(row.get("best_ask")),
                }
            )

    if not tokens:
        raise SystemExit("No World Cup Winner YES tokens found in market_snapshot.csv.")
    return tokens


def extract_match_probability_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []

    for source in SOURCE_FILES:
        for row in read_csv(source):
            home = row.get("home_team") or row.get("home_name") or row.get("home")
            away = row.get("away_team") or row.get("away_name") or row.get("away")
            if not home or not away:
                continue

            p_home = p_draw = p_away = None
            if row.get("market_p_home") not in {None, ""}:
                p_home = parse_probability(row.get("market_p_home"))
                p_draw = parse_probability(row.get("market_p_draw"))
                p_away = parse_probability(row.get("market_p_away"))
            elif row.get("market_p_home_win") not in {None, ""}:
                p_home = parse_probability(row.get("market_p_home_win"))
                p_draw = parse_probability(row.get("market_p_draw"))
                p_away = parse_probability(row.get("market_p_away_win"))
            elif row.get("winner_home_pct") not in {None, ""}:
                p_home = parse_probability(row.get("winner_home_pct"))
                p_draw = parse_probability(row.get("winner_draw_pct"))
                p_away = parse_probability(row.get("winner_away_pct"))

            if p_home is None or p_draw is None or p_away is None:
                continue

            total = p_home + p_draw + p_away
            if total <= 0:
                continue

            rows.append(
                {
                    "source": str(source.relative_to(ROOT)),
                    "home": str(home).strip(),
                    "away": str(away).strip(),
                    "home_key": norm(home),
                    "away_key": norm(away),
                    "p_home": p_home / total,
                    "p_draw": p_draw / total,
                    "p_away": p_away / total,
                }
            )

    # Many score-grid files repeat one match once per scoreline. Deduplicate the
    # 1X2 observation, not the whole CSV row.
    seen: set[tuple[object, ...]] = set()
    unique: list[dict[str, object]] = []
    for row in rows:
        key = (
            row["source"],
            row["home_key"],
            row["away_key"],
            round(float(row["p_home"]), 5),
            round(float(row["p_draw"]), 5),
            round(float(row["p_away"]), 5),
        )
        if key in seen or row["home_key"] == row["away_key"]:
            continue
        seen.add(key)
        unique.append(row)

    return unique


def fit_strength_ratings(match_rows: list[dict[str, object]], token_keys: list[str]) -> dict[str, float]:
    teams = set(token_keys)
    for row in match_rows:
        teams.add(str(row["home_key"]))
        teams.add(str(row["away_key"]))

    rating = {team: 0.0 for team in teams if team}
    observations: list[tuple[str, str, float]] = []

    for row in match_rows:
        home = str(row["home_key"])
        away = str(row["away_key"])
        expected_score = float(row["p_home"]) + 0.5 * float(row["p_draw"])
        observations.append((home, away, logit(expected_score)))

    if not observations:
        raise SystemExit("No match probability observations found for converter.")

    learning_rate = 0.015
    ridge = 0.0005
    for _ in range(5000):
        for home, away, target in observations:
            pred = rating[home] - rating[away]
            err = pred - target
            rating[home] -= learning_rate * (err + ridge * rating[home])
            rating[away] += learning_rate * (err - ridge * rating[away])

        mean_rating = sum(rating.values()) / len(rating)
        for team in rating:
            rating[team] -= mean_rating

    return rating


def completed_result_bonus() -> defaultdict[str, float]:
    """Small conservative form/survival bonus from completed repo results."""
    bonus: defaultdict[str, float] = defaultdict(float)
    for row in read_csv(ROOT / "outputs/superbru_pool/superbru_match_results_auto.csv"):
        if str(row.get("is_completed", "")).strip().lower() not in {"true", "1", "yes"}:
            continue
        home = norm(row.get("home_team"))
        away = norm(row.get("away_team"))
        home_goals = parse_float(row.get("home_goals"))
        away_goals = parse_float(row.get("away_goals"))
        if not home or not away or home_goals is None or away_goals is None:
            continue
        if home_goals > away_goals:
            bonus[home] += 0.08
            bonus[away] -= 0.03
        elif away_goals > home_goals:
            bonus[away] += 0.08
            bonus[home] -= 0.03
        else:
            bonus[home] += 0.02
            bonus[away] += 0.02
    return bonus


def _row(token, probability, score):
    ask = token["market_ask"]
    return {
        "team": token["team"],
        "token_id": token["token_id"],
        "probability": probability,
        "repo_strength_score": score,
        "market_bid": token["market_bid"],
        "market_ask": ask,
        "edge_vs_ask": "" if ask is None else probability - float(ask),
    }


def _winner_probabilities_via_bracket(ratings: dict[str, float]) -> dict[str, float] | None:
    """Bracket Monte Carlo -> realistic outright-winner probabilities. None if the
    tournament structure cannot be derived (caller falls back to the softmax)."""
    try:
        from polymarket_predictive_engine.tournament import derive_group_stage, simulate_winner_probabilities
    except Exception as exc:  # noqa: BLE001
        print(f"converter: bracket simulator unavailable ({exc}); softmax fallback", flush=True)
        return None

    fixtures = [
        (r.get("home_team"), r.get("away_team"))
        for r in read_csv(ROOT / "inputs/oddspedia_match_urls.csv")
        if r.get("home_team") and r.get("away_team")
    ]
    results = [
        (r["home_team"], r["away_team"], r["home_goals"], r["away_goals"])
        for r in read_csv(ROOT / "outputs/superbru_pool/superbru_match_results_auto.csv")
        if str(r.get("is_completed", "")).lower() == "true" and r.get("home_goals") and r.get("away_goals")
    ]
    if len(fixtures) < 60 or not results:
        print("converter: insufficient fixtures/results for bracket MC; softmax fallback", flush=True)
        return None

    stage = derive_group_stage(fixtures, results, normalize=norm)
    if len(stage.groups) != 12 or stage.warnings:
        print(f"converter: group derivation off ({len(stage.groups)} groups {stage.warnings}); softmax fallback", flush=True)
        return None

    n_sims = int(os.getenv("POLYMARKET_WINNER_SIMS", "20000"))
    probs = simulate_winner_probabilities(stage, ratings, n_sims=n_sims)
    print(f"converter: bracket Monte Carlo over {n_sims} sims "
          f"({len(stage.remaining)} remaining group matches)", flush=True)
    return probs


def build_winner_probabilities(tokens: list[dict[str, object]], ratings: dict[str, float]) -> list[dict[str, object]]:
    bonus = completed_result_bonus()
    winner_probs = _winner_probabilities_via_bracket(ratings)
    if winner_probs is None:
        return _softmax_winner_probabilities(tokens, ratings, bonus)
    output = [
        _row(token, float(winner_probs.get(str(token["key"]), 0.0)),
             ratings.get(str(token["key"]), 0.0) + bonus.get(str(token["key"]), 0.0))
        for token in tokens
    ]
    output.sort(key=lambda item: float(item["probability"]), reverse=True)
    return output


def _softmax_winner_probabilities(tokens, ratings, bonus) -> list[dict[str, object]]:
    """First-pass strength softmax. Kept as a fallback when the bracket cannot be derived."""
    temperature = float(os.getenv("POLYMARKET_WINNER_TEMPERATURE", "1.35"))
    scored = []
    for token in tokens:
        key = str(token["key"])
        score = ratings.get(key, 0.0) + bonus.get(key, 0.0)
        scored.append((token, score, math.exp(score / temperature)))
    total_weight = sum(weight for _token, _score, weight in scored)
    if total_weight <= 0:
        raise SystemExit("Could not compute winner probability weights.")
    output = [_row(token, weight / total_weight, score) for token, score, weight in scored]
    output.sort(key=lambda item: float(item["probability"]), reverse=True)
    return output


def write_outputs(rows: list[dict[str, object]], observation_count: int) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    DETAIL.parent.mkdir(parents=True, exist_ok=True)

    with OUT.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["token_id", "probability"])
        writer.writeheader()
        for row in rows:
            writer.writerow({"token_id": row["token_id"], "probability": f"{float(row['probability']):.8f}"})

    with DETAIL.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["team", "token_id", "probability", "repo_strength_score", "market_bid", "market_ask", "edge_vs_ask"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "team": row["team"],
                    "token_id": row["token_id"],
                    "probability": f"{float(row['probability']):.8f}",
                    "repo_strength_score": f"{float(row['repo_strength_score']):.6f}",
                    "market_bid": "" if row["market_bid"] is None else f"{float(row['market_bid']):.6f}",
                    "market_ask": "" if row["market_ask"] is None else f"{float(row['market_ask']):.6f}",
                    "edge_vs_ask": "" if row["edge_vs_ask"] == "" else f"{float(row['edge_vs_ask']):.6f}",
                }
            )

    print(
        f"converter: wrote {OUT.relative_to(ROOT)} from {observation_count} repo match-probability "
        f"observations and {len(rows)} Polymarket YES tokens",
        flush=True,
    )
    print("converter: top 10 mapped winner probabilities:", flush=True)
    for row in rows[:10]:
        ask = "" if row["market_ask"] is None else f" ask={float(row['market_ask']):.2%}"
        edge = "" if row["edge_vs_ask"] == "" else f" edge_vs_ask={float(row['edge_vs_ask']):.2%}"
        print(f"  {str(row['team']):<22} {float(row['probability']):.2%}{ask}{edge}", flush=True)


def main() -> int:
    tokens = winner_tokens_from_snapshot()
    observations = extract_match_probability_rows()
    ratings = fit_strength_ratings(observations, [str(token["key"]) for token in tokens])
    rows = build_winner_probabilities(tokens, ratings)
    write_outputs(rows, len(observations))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
