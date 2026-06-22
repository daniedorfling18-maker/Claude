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

PRICE_COLUMNS = ["market_type", "selection", "yes_price"]
MATCH_RESULT_SELECTIONS = ("home_win", "draw", "away_win")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Simulate flat-stake Polymarket venue layers for market types that are commonly available, "
            "starting with match-result markets derived from the existing score matrices."
        )
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--fixtures", default="data/fixtures_real.csv")
    parser.add_argument("--odds-json", default="data/odds_snapshot_real.json")
    parser.add_argument("--predictions-csv", default="outputs/latest/predictions.csv")
    parser.add_argument("--leaderboard-csv", default="inputs/pool_leaderboard.csv")
    parser.add_argument("--chaser-profiles-csv", default="inputs/chaser_profiles.csv")
    parser.add_argument("--prices-csv", default="inputs/polymarket_available_market_prices.csv")
    parser.add_argument(
        "--futures-probabilities-csv",
        default="",
        help=(
            "Optional CSV for non-fixture markets such as group_qualification or tournament_winner. "
            "Expected columns: market_type, selection, model_probability."
        ),
    )
    parser.add_argument(
        "--market-types",
        default="match_result",
        help="Comma-separated venue market types to simulate. Supported: match_result, futures.",
    )
    parser.add_argument("--stake-usdc", type=float, default=10.0)
    parser.add_argument("--category", default="sports", help="Default Polymarket fee category when the prices CSV has no category column.")
    parser.add_argument("--taker-fee-rate", type=float, default=None, help="Optional explicit fee rate override, e.g. 0.03 for Sports.")
    parser.add_argument("--simulations", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=20260620)
    parser.add_argument(
        "--allow-model-fair-fallback",
        action="store_true",
        help=(
            "Use the model probability as a fallback YES price when no CSV price is present. "
            "This is for plumbing tests only and should not be treated as a market edge."
        ),
    )
    parser.add_argument("--out-dir", default="outputs/polymarket_available_markets")
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


def requested_market_types(raw: str) -> set[str]:
    values = {item.strip().lower() for item in str(raw or "").split(",") if item.strip()}
    return values or {"match_result"}


def load_prices(path: str | Path) -> pd.DataFrame:
    price_path = ROOT / Path(path)
    if not price_path.exists():
        return pd.DataFrame(columns=PRICE_COLUMNS)
    prices = pd.read_csv(price_path).fillna("")
    missing = [col for col in PRICE_COLUMNS if col not in prices.columns]
    if missing:
        raise ValueError(f"{price_path} is missing required columns: {missing}")
    return prices


def price_lookup(
    prices: pd.DataFrame,
    *,
    market_type: str,
    selection: str,
    match_id: str = "",
    home_team: str = "",
    away_team: str = "",
    mc=None,
) -> pd.Series | None:
    if prices.empty:
        return None

    market_type = txt(market_type)
    selection = txt(selection)
    match_id = txt(match_id)

    if match_id and "match_id" in prices.columns:
        by_id = prices[
            (prices["match_id"].astype(str).str.strip() == match_id)
            & (prices["market_type"].astype(str).str.strip() == market_type)
            & (prices["selection"].astype(str).str.strip() == selection)
        ]
        if not by_id.empty:
            return by_id.iloc[0]

    if home_team and away_team and "home_team" in prices.columns and "away_team" in prices.columns and mc is not None:
        home_key = mc.canonical_team_key(home_team)
        away_key = mc.canonical_team_key(away_team)
        for _, candidate in prices.iterrows():
            if txt(candidate.get("market_type")) != market_type or txt(candidate.get("selection")) != selection:
                continue
            if (
                mc.canonical_team_key(txt(candidate.get("home_team"))) == home_key
                and mc.canonical_team_key(txt(candidate.get("away_team"))) == away_key
            ):
                return candidate

    generic = prices[
        (prices["market_type"].astype(str).str.strip() == market_type)
        & (prices["selection"].astype(str).str.strip() == selection)
    ]
    if not generic.empty:
        return generic.iloc[0]

    return None


def match_result_probability(matrix: np.ndarray, selection: str) -> float:
    if selection == "home_win":
        return float(np.tril(matrix, k=-1).sum())
    if selection == "draw":
        return float(np.trace(matrix))
    if selection == "away_win":
        return float(np.triu(matrix, k=1).sum())
    raise ValueError(f"Unsupported match result selection: {selection}")


def match_result_wins_vector(actual_home: np.ndarray, actual_away: np.ndarray, selection: str) -> np.ndarray:
    if selection == "home_win":
        return actual_home > actual_away
    if selection == "draw":
        return actual_home == actual_away
    if selection == "away_win":
        return actual_home < actual_away
    raise ValueError(f"Unsupported match result selection: {selection}")


def selection_label(row: pd.Series, selection: str) -> str:
    if selection == "home_win":
        return f"{txt(row.get('home_team'))} win"
    if selection == "draw":
        return "Draw"
    if selection == "away_win":
        return f"{txt(row.get('away_team'))} win"
    return selection


def build_trade_from_price(args: argparse.Namespace, found_price: pd.Series | None, model_probability: float) -> tuple[Any, str, str, bool, float | None, int, bool]:
    used_fallback = False
    missing = False

    if found_price is None:
        if not args.allow_model_fair_fallback or model_probability <= 0:
            missing = True
            return None, "", "", True, None, 0, missing
        yes_price = clamp_price(model_probability)
        price_source = "model_fair_probability_fallback"
        category = args.category
        fees_enabled = True
        fee_rate = args.taker_fee_rate
        used_fallback = True
    else:
        raw_yes_price = txt(found_price.get("yes_price"))
        if raw_yes_price == "":
            if not args.allow_model_fair_fallback or model_probability <= 0:
                missing = True
                return None, "", "", True, None, 0, missing
            yes_price = clamp_price(model_probability)
            price_source = "model_fair_probability_fallback"
            category = txt(found_price.get("category")) or args.category
            fees_enabled = bool_value(found_price.get("fees_enabled"), True)
            fee_rate_text = txt(found_price.get("taker_fee_rate"))
            fee_rate = float(fee_rate_text) if fee_rate_text else args.taker_fee_rate
            used_fallback = True
        else:
            yes_price = clamp_price(float(raw_yes_price))
            price_source = txt(found_price.get("price_source")) or "prices_csv"
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
    return trade, price_source, category, fees_enabled, fee_rate, int(used_fallback), missing


def simulate_match_result(args: argparse.Namespace, prices: pd.DataFrame) -> tuple[list[dict[str, Any]], dict[str, int]]:
    mc = load_mc_module()
    rng = np.random.default_rng(int(args.seed))
    predictions, _, _ = mc.load_inputs(args)
    rows = [row for _, row in predictions.iterrows() if not mc.exclude_row(row, [])]
    matrices_by_key = mc.build_score_matrices(args, predictions)

    output_rows: list[dict[str, Any]] = []
    missing_price_count = 0
    used_model_fallback_count = 0

    for _, row in enumerate(rows):
        matrix = mc.find_matrix(row, matrices_by_key)
        actual_home, actual_away = mc.sample_actual_scores(matrix, int(args.simulations), rng)

        for selection in MATCH_RESULT_SELECTIONS:
            model_probability = match_result_probability(matrix, selection)
            found_price = price_lookup(
                prices,
                market_type="match_result",
                selection=selection,
                match_id=txt(row.get("match_id")),
                home_team=txt(row.get("home_team")),
                away_team=txt(row.get("away_team")),
                mc=mc,
            )
            trade, price_source, category, fees_enabled, fee_rate, used_fallback, missing = build_trade_from_price(
                args,
                found_price,
                model_probability,
            )
            used_model_fallback_count += used_fallback
            if missing or trade is None:
                missing_price_count += 1
                continue

            wins = match_result_wins_vector(actual_home, actual_away, selection)
            pnl = np.where(wins, trade.win_net_pnl_usdc, trade.lose_net_pnl_usdc)

            output_rows.append(
                {
                    "commence_time": txt(row.get("commence_time")),
                    "match_id": txt(row.get("match_id")),
                    "home_team": txt(row.get("home_team")),
                    "away_team": txt(row.get("away_team")),
                    "market_type": "match_result",
                    "selection": selection,
                    "selection_label": selection_label(row, selection),
                    "stake_usdc": round(trade.stake_usdc, 6),
                    "yes_price": round(trade.price, 6),
                    "price_source": price_source,
                    "shares": round(trade.shares, 6),
                    "category": category,
                    "fees_enabled": trade.fees_enabled,
                    "taker_fee_rate": round(trade.taker_fee_rate, 6),
                    "taker_fee_usdc": round(trade.taker_fee_usdc, 5),
                    "model_probability": round(model_probability, 8),
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

    return output_rows, {
        "match_result_missing_price_count": missing_price_count,
        "match_result_used_model_fair_fallback_count": used_model_fallback_count,
    }


def simulate_futures(args: argparse.Namespace, prices: pd.DataFrame) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if not args.futures_probabilities_csv:
        return [], {"futures_missing_price_count": 0, "futures_used_model_fair_fallback_count": 0}

    path = ROOT / args.futures_probabilities_csv
    if not path.exists():
        raise FileNotFoundError(f"Futures probabilities CSV not found: {path}")

    futures = pd.read_csv(path).fillna("")
    required = ["market_type", "selection", "model_probability"]
    missing_cols = [col for col in required if col not in futures.columns]
    if missing_cols:
        raise ValueError(f"{path} is missing required columns: {missing_cols}")

    output_rows: list[dict[str, Any]] = []
    missing_price_count = 0
    used_model_fallback_count = 0

    for _, row in futures.iterrows():
        market_type = txt(row.get("market_type"))
        selection = txt(row.get("selection"))
        model_probability = float(row.get("model_probability"))
        found_price = price_lookup(
            prices,
            market_type=market_type,
            selection=selection,
        )
        trade, price_source, category, fees_enabled, fee_rate, used_fallback, missing = build_trade_from_price(
            args,
            found_price,
            model_probability,
        )
        used_model_fallback_count += used_fallback
        if missing or trade is None:
            missing_price_count += 1
            continue

        output_rows.append(
            {
                "commence_time": "",
                "match_id": "",
                "home_team": "",
                "away_team": "",
                "market_type": market_type,
                "selection": selection,
                "selection_label": txt(row.get("selection_label")) or selection,
                "stake_usdc": round(trade.stake_usdc, 6),
                "yes_price": round(trade.price, 6),
                "price_source": price_source,
                "shares": round(trade.shares, 6),
                "category": category,
                "fees_enabled": trade.fees_enabled,
                "taker_fee_rate": round(trade.taker_fee_rate, 6),
                "taker_fee_usdc": round(trade.taker_fee_usdc, 5),
                "model_probability": round(model_probability, 8),
                "breakeven_probability_after_fee": round((trade.stake_usdc + trade.taker_fee_usdc) / trade.shares, 8) if trade.shares else "",
                "expected_pnl_model_usdc": round(trade.expected_pnl(model_probability), 6),
                "mean_pnl_mc_usdc": "",
                "p05_pnl_mc_usdc": "",
                "p50_pnl_mc_usdc": "",
                "p95_pnl_mc_usdc": "",
                "win_net_pnl_usdc": round(trade.win_net_pnl_usdc, 6),
                "lose_net_pnl_usdc": round(trade.lose_net_pnl_usdc, 6),
            }
        )

    return output_rows, {
        "futures_missing_price_count": missing_price_count,
        "futures_used_model_fair_fallback_count": used_model_fallback_count,
    }


def simulate(args: argparse.Namespace) -> tuple[pd.DataFrame, dict[str, Any]]:
    prices = load_prices(args.prices_csv)
    market_types = requested_market_types(args.market_types)

    output_rows: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

    if "match_result" in market_types:
        rows, match_counts = simulate_match_result(args, prices)
        output_rows.extend(rows)
        counts.update(match_counts)

    futures_requested = "futures" in market_types or "group_qualification" in market_types or "tournament_winner" in market_types
    if futures_requested:
        rows, futures_counts = simulate_futures(args, prices)
        output_rows.extend(rows)
        counts.update(futures_counts)

    results = pd.DataFrame(output_rows)
    by_market_type = {}
    if not results.empty:
        for market_type, frame in results.groupby("market_type"):
            by_market_type[market_type] = {
                "rows_simulated": int(len(frame)),
                "total_stake_usdc": round(float(frame["stake_usdc"].sum()), 6),
                "expected_pnl_model_usdc": round(float(frame["expected_pnl_model_usdc"].sum()), 6),
            }

    summary = {
        "simulations": int(args.simulations),
        "stake_usdc": float(args.stake_usdc),
        "prices_csv": str(args.prices_csv),
        "market_types": sorted(market_types),
        "allow_model_fair_fallback": bool(args.allow_model_fair_fallback),
        **counts,
        "rows_simulated": int(len(results)),
        "total_stake_usdc": round(float(results["stake_usdc"].sum()), 6) if not results.empty else 0.0,
        "expected_pnl_model_usdc": round(float(results["expected_pnl_model_usdc"].sum()), 6) if not results.empty else 0.0,
        "by_market_type": by_market_type,
        "note": (
            "Match-result markets use fixture score matrices. Futures markets require a probabilities CSV. "
            "Model-fair fallback is only a plumbing test."
        ),
    }
    return results, summary


def main() -> int:
    args = build_parser().parse_args()
    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    results, summary = simulate(args)
    results_path = out_dir / "polymarket_available_market_results.csv"
    summary_path = out_dir / "polymarket_available_market_summary.json"
    results.to_csv(results_path, index=False)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))
    print(f"Wrote {results_path}")
    print(f"Wrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
