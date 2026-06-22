from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from superbru_score_engine.betting.polymarket import build_flat_stake_yes_trade

PRICE_COLUMNS = ["match_id", "home_team", "away_team", "scoreline", "yes_price"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Simulate a flat-stake Polymarket venue layer against the existing scoreline matrices."
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--fixtures", default="data/fixtures_real.csv")
    parser.add_argument("--odds-json", default="data/odds_snapshot_real.json")
    parser.add_argument("--predictions-csv", default="outputs/latest/predictions.csv")
    parser.add_argument("--leaderboard-csv", default="inputs/pool_leaderboard.csv")
    parser.add_argument("--chaser-profiles-csv", default="inputs/chaser_profiles.csv")
    parser.add_argument("--prices-csv", default="inputs/polymarket_flat_stake_prices.csv")
    parser.add_argument("--stake-usdc", type=float, default=10.0)
    parser.add_argument("--category", default="sports", help="Default Polymarket fee category when the prices CSV has no category column.")
    parser.add_argument("--taker-fee-rate", type=float, default=None, help="Optional explicit fee rate override, e.g. 0.03 for Sports.")
    parser.add_argument("--simulations", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=20260620)
    parser.add_argument("--include-candidates", action="store_true")
    parser.add_argument(
        "--allow-model-fair-fallback",
        action="store_true",
        help=(
            "Use the model exact-score probability as a fallback YES price when no CSV price is present. "
            "This is for plumbing tests only and should not be treated as a market edge."
        ),
    )
    parser.add_argument("--out-dir", default="outputs/polymarket_flat_stake")
    return parser


def load_mc_module():
    path = ROOT / "scripts" / "run_leaderboard_monte_carlo.py"
    if not path.exists():
        raise FileNotFoundError("scripts/run_leaderboard_monte_carlo.py not found")
    spec = importlib.util.spec_from_file_location("leaderboard_mc", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def txt(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def bool_value(value: Any, default: bool = True) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def clamp_price(value: float) -> float:
    return min(0.99, max(0.01, float(value)))


def score_exact_probability(matrix: np.ndarray, scoreline: str, mc) -> float:
    parsed = mc.parse_score(scoreline)
    if parsed is None:
        return 0.0
    home, away = parsed
    if home >= matrix.shape[0] or away >= matrix.shape[1]:
        return 0.0
    return float(matrix[home, away])


def score_wins_vector(actual_home: np.ndarray, actual_away: np.ndarray, scoreline: str, mc) -> np.ndarray:
    parsed = mc.parse_score(scoreline)
    if parsed is None:
        return np.zeros(actual_home.shape[0], dtype=bool)
    home, away = parsed
    return (actual_home == home) & (actual_away == away)


def load_prices(path: str | Path) -> pd.DataFrame:
    price_path = ROOT / Path(path)
    if not price_path.exists():
        return pd.DataFrame(columns=PRICE_COLUMNS)
    prices = pd.read_csv(price_path).fillna("")
    missing = [col for col in PRICE_COLUMNS if col not in prices.columns]
    if missing:
        raise ValueError(f"{price_path} is missing required columns: {missing}")
    return prices


def price_lookup(prices: pd.DataFrame, row: pd.Series, scoreline: str, mc) -> pd.Series | None:
    if prices.empty:
        return None
    match_id = txt(row.get("match_id"))
    home_key = mc.canonical_team_key(txt(row.get("home_team")))
    away_key = mc.canonical_team_key(txt(row.get("away_team")))
    score = txt(scoreline)

    if match_id:
        by_id = prices[(prices["match_id"].astype(str).str.strip() == match_id) & (prices["scoreline"].astype(str).str.strip() == score)]
        if not by_id.empty:
            return by_id.iloc[0]

    for _, candidate in prices.iterrows():
        if txt(candidate.get("scoreline")) != score:
            continue
        if mc.canonical_team_key(txt(candidate.get("home_team"))) == home_key and mc.canonical_team_key(txt(candidate.get("away_team"))) == away_key:
            return candidate
    return None


def candidate_scorelines(row: pd.Series, include_candidates: bool, mc) -> list[str]:
    if include_candidates:
        return mc.candidate_scores(row)
    score = txt(row.get("recommended_scoreline"))
    return [score] if mc.parse_score(score) is not None else []


def simulate(args: argparse.Namespace) -> tuple[pd.DataFrame, dict[str, Any]]:
    mc = load_mc_module()
    rng = np.random.default_rng(int(args.seed))
    predictions, _, _ = mc.load_inputs(args)
    rows = [row for _, row in predictions.iterrows() if not mc.exclude_row(row, [])]
    matrices_by_key = mc.build_score_matrices(args, predictions)
    prices = load_prices(args.prices_csv)

    output_rows: list[dict[str, Any]] = []
    missing_price_count = 0
    used_model_fallback_count = 0

    for idx, row in enumerate(rows):
        matrix = mc.find_matrix(row, matrices_by_key)
        actual_home, actual_away = mc.sample_actual_scores(matrix, int(args.simulations), rng)
        recommended = txt(row.get("recommended_scoreline"))
        for scoreline in candidate_scorelines(row, bool(args.include_candidates), mc):
            model_probability = score_exact_probability(matrix, scoreline, mc)
            found_price = price_lookup(prices, row, scoreline, mc)
            if found_price is None:
                if not args.allow_model_fair_fallback or model_probability <= 0:
                    missing_price_count += 1
                    continue
                yes_price = clamp_price(model_probability)
                price_source = "model_fair_probability_fallback"
                category = args.category
                fees_enabled = True
                fee_rate = args.taker_fee_rate
                used_model_fallback_count += 1
            else:
                yes_price = clamp_price(float(found_price.get("yes_price")))
                price_source = "prices_csv"
                category = txt(found_price.get("category")) or args.category
                fees_enabled = bool_value(found_price.get("fees_enabled"), True)
                fee_rate_text = txt(found_price.get("taker_fee_rate"))
                fee_rate = float(fee_rate_text) if fee_rate_text else args.taker_fee_rate

            trade = build_flat_stake_yes_trade(
                stake_usdc=float(args.stake_usdc),
                price=yes_price,
                category=category,
                taker_fee_rate=fee_rate,
                fees_enabled=fees_enabled,
            )
            wins = score_wins_vector(actual_home, actual_away, scoreline, mc)
            pnl = np.where(wins, trade.win_net_pnl_usdc, trade.lose_net_pnl_usdc)
            output_rows.append(
                {
                    "commence_time": txt(row.get("commence_time")),
                    "match_id": txt(row.get("match_id")),
                    "home_team": txt(row.get("home_team")),
                    "away_team": txt(row.get("away_team")),
                    "scoreline": scoreline,
                    "is_recommended_scoreline": scoreline == recommended,
                    "stake_usdc": round(trade.stake_usdc, 6),
                    "yes_price": round(trade.price, 6),
                    "price_source": price_source,
                    "shares": round(trade.shares, 6),
                    "category": category,
                    "fees_enabled": trade.fees_enabled,
                    "taker_fee_rate": round(trade.taker_fee_rate, 6),
                    "taker_fee_usdc": round(trade.taker_fee_usdc, 5),
                    "model_exact_probability": round(model_probability, 8),
                    "breakeven_probability_after_fee": round((trade.stake_usdc + trade.taker_fee_usdc) / trade.shares, 8) if trade.shares else "",
                    "expected_pnl_model_usdc": round(trade.expected_pnl(model_probability), 6),
                    "mean_pnl_mc_usdc": round(float(pnl.mean()), 6),
                    "p05_pnl_mc_usdc": round(float(np.quantile(pnl, 0.05)), 6),
                    "p50_pnl_mc_usdc": round(float(np.quantile(pnl, 0.50)), 6),
                    "p95_pnl_mc_usdc": round(float(np.quantile(pnl, 0.95)), 6),
                    "win_net_pnl_usdc": round(trade.win_net_pnl_usdc, 6),
                    "lose_net_pnl_usdc": round(trade.lose_net_pnl_usdc, 6),
                }
            )

    results = pd.DataFrame(output_rows)
    recommended_results = results[results["is_recommended_scoreline"].astype(bool)] if not results.empty else results
    summary = {
        "simulations": int(args.simulations),
        "stake_usdc": float(args.stake_usdc),
        "prices_csv": str(args.prices_csv),
        "include_candidates": bool(args.include_candidates),
        "allow_model_fair_fallback": bool(args.allow_model_fair_fallback),
        "missing_price_count": int(missing_price_count),
        "used_model_fair_fallback_count": int(used_model_fallback_count),
        "rows_simulated": int(len(results)),
        "recommended_rows_simulated": int(len(recommended_results)),
        "recommended_total_stake_usdc": round(float(recommended_results["stake_usdc"].sum()), 6) if not recommended_results.empty else 0.0,
        "recommended_expected_pnl_model_usdc": round(float(recommended_results["expected_pnl_model_usdc"].sum()), 6) if not recommended_results.empty else 0.0,
        "recommended_mean_pnl_mc_usdc": round(float(recommended_results["mean_pnl_mc_usdc"].sum()), 6) if not recommended_results.empty else 0.0,
        "note": "Model-fair fallback is only a plumbing test. Use prices_csv for a real venue simulation.",
    }
    return results, summary


def main() -> int:
    args = build_parser().parse_args()
    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    results, summary = simulate(args)
    results_path = out_dir / "polymarket_flat_stake_results.csv"
    summary_path = out_dir / "polymarket_flat_stake_summary.json"
    results.to_csv(results_path, index=False)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))
    print(f"Wrote {results_path}")
    print(f"Wrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
