from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd


TRADE_COLUMNS = [
    "intent_id",
    "generated_at_utc",
    "action",
    "execution_status",
    "match_id",
    "home_team",
    "away_team",
    "commence_time",
    "selection",
    "selection_label",
    "polymarket_event",
    "polymarket_question",
    "polymarket_outcome",
    "polymarket_token_id",
    "limit_price",
    "max_stake_usdc",
    "target_shares",
    "model_probability",
    "market_implied_probability",
    "breakeven_probability_after_fee",
    "bookmaker_no_vig_probability_median",
    "model_edge_vs_polymarket_breakeven",
    "model_edge_vs_bookmaker_median",
    "expected_pnl_model_usdc",
    "paper_stake_units",
    "superbru_alignment",
    "bookmaker_cross_check_class",
    "decision_status",
    "decision_reason",
    "risk_flags",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate automatic Polymarket trade intents from the manual decision board. "
            "This script does not submit orders, sign transactions, or manage account funding."
        )
    )
    parser.add_argument("--decision-board-csv", default="outputs/polymarket_available_markets/polymarket_superbru_decision_board.csv")
    parser.add_argument("--prices-csv", default="inputs/polymarket_available_market_prices.csv")
    parser.add_argument("--positions-csv", default="", help="Optional current-position CSV for SELL intent generation.")
    parser.add_argument("--out-csv", default="outputs/polymarket_available_markets/auto_trade_intents.csv")
    parser.add_argument("--out-json", default="outputs/polymarket_available_markets/auto_trade_intents.json")
    parser.add_argument("--allow-review-only", action="store_true", help="Allow REVIEW_ONLY rows to generate intents if all numeric gates pass.")
    parser.add_argument("--min-polymarket-edge", type=float, default=0.0)
    parser.add_argument("--min-bookmaker-edge", type=float, default=0.01)
    parser.add_argument("--max-model-to-market-ratio", type=float, default=1.5)
    parser.add_argument("--max-total-stake-usdc", type=float, default=50.0)
    parser.add_argument("--max-stake-per-trade-usdc", type=float, default=10.0)
    parser.add_argument("--min-stake-usdc", type=float, default=1.0)
    parser.add_argument("--limit-price-buffer", type=float, default=0.005)
    parser.add_argument("--sell-edge-threshold", type=float, default=-0.005)
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def fnum(value: Any, default: float = float("nan")) -> float:
    try:
        text = txt(value)
        if not text:
            return default
        return float(text)
    except Exception:
        return default


def bool_value(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def clamp_price(value: float) -> float:
    return round(min(0.99, max(0.01, value)), 4)


def current_utc_iso() -> str:
    return pd.Timestamp.now("UTC").isoformat()


def enrich_with_market_details(board: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    if prices.empty:
        board["polymarket_event"] = ""
        board["polymarket_question"] = ""
        board["polymarket_outcome"] = ""
        board["polymarket_token_id"] = ""
        return board

    price_cols = [
        col
        for col in [
            "match_id",
            "home_team",
            "away_team",
            "selection",
            "polymarket_event",
            "polymarket_question",
            "polymarket_outcome",
            "polymarket_token_id",
        ]
        if col in prices.columns
    ]
    market_details = prices[price_cols].fillna("").copy()
    market_details = market_details.drop_duplicates(subset=["match_id", "home_team", "away_team", "selection"])
    return board.merge(market_details, on=["match_id", "home_team", "away_team", "selection"], how="left").fillna("")


def row_passes_buy_gates(row: pd.Series, args: argparse.Namespace) -> tuple[bool, list[str]]:
    flags: list[str] = []

    decision_status = txt(row.get("decision_status"))
    if decision_status != "MANUAL_TICKET_CANDIDATE" and not args.allow_review_only:
        flags.append("not_manual_ticket_candidate")

    if txt(row.get("price_source")) != "clob_best_ask":
        flags.append("not_clob_best_ask")
    if txt(row.get("polymarket_token_id")) == "":
        flags.append("missing_polymarket_token_id")
    if bool_value(row.get("manual_review_required"), False):
        flags.append("manual_review_required")
    if "model_probability_gt_2x_market_implied" in txt(row.get("sanity_flags")):
        flags.append("extreme_market_mismatch")

    if fnum(row.get("model_edge_vs_polymarket_breakeven"), -999.0) <= float(args.min_polymarket_edge):
        flags.append("insufficient_polymarket_edge")
    if fnum(row.get("model_edge_vs_bookmaker_median"), -999.0) <= float(args.min_bookmaker_edge):
        flags.append("insufficient_bookmaker_edge")
    if fnum(row.get("model_to_market_ratio"), 999.0) > float(args.max_model_to_market_ratio):
        flags.append("model_to_market_ratio_too_high")

    return not flags, flags


def stake_for_row(row: pd.Series, args: argparse.Namespace) -> float:
    paper_stake_units = fnum(row.get("paper_stake_units"), 0.0)
    paper_usdc = fnum(row.get("paper_stake_usdc_at_10_usdc_unit"), float("nan"))
    if not math.isnan(paper_usdc) and paper_usdc > 0:
        stake = paper_usdc
    else:
        stake = paper_stake_units * 10.0
    stake = min(float(args.max_stake_per_trade_usdc), max(0.0, stake))
    if stake < float(args.min_stake_usdc):
        return 0.0
    return round(stake, 2)


def build_buy_intent(row: pd.Series, intent_number: int, generated_at: str, args: argparse.Namespace) -> dict[str, Any]:
    yes_price = fnum(row.get("yes_price"), fnum(row.get("market_implied_probability")))
    limit_price = clamp_price(yes_price + float(args.limit_price_buffer))
    stake = stake_for_row(row, args)
    target_shares = round(stake / limit_price, 6) if limit_price > 0 else 0.0

    return {
        "intent_id": f"PM-BUY-{intent_number:04d}",
        "generated_at_utc": generated_at,
        "action": "BUY_YES_LIMIT",
        "execution_status": "GENERATED_NOT_SUBMITTED",
        "match_id": txt(row.get("match_id")),
        "home_team": txt(row.get("home_team")),
        "away_team": txt(row.get("away_team")),
        "commence_time": txt(row.get("commence_time")),
        "selection": txt(row.get("selection")),
        "selection_label": txt(row.get("selection_label")),
        "polymarket_event": txt(row.get("polymarket_event")),
        "polymarket_question": txt(row.get("polymarket_question")),
        "polymarket_outcome": txt(row.get("polymarket_outcome")),
        "polymarket_token_id": txt(row.get("polymarket_token_id")),
        "limit_price": limit_price,
        "max_stake_usdc": stake,
        "target_shares": target_shares,
        "model_probability": fnum(row.get("model_probability")),
        "market_implied_probability": fnum(row.get("market_implied_probability"), yes_price),
        "breakeven_probability_after_fee": fnum(row.get("breakeven_probability_after_fee")),
        "bookmaker_no_vig_probability_median": fnum(row.get("bookmaker_no_vig_probability_median")),
        "model_edge_vs_polymarket_breakeven": fnum(row.get("model_edge_vs_polymarket_breakeven")),
        "model_edge_vs_bookmaker_median": fnum(row.get("model_edge_vs_bookmaker_median")),
        "expected_pnl_model_usdc": fnum(row.get("expected_pnl_model_usdc")),
        "paper_stake_units": fnum(row.get("paper_stake_units"), 0.0),
        "superbru_alignment": txt(row.get("superbru_alignment")),
        "bookmaker_cross_check_class": txt(row.get("bookmaker_cross_check_class")),
        "decision_status": txt(row.get("decision_status")),
        "decision_reason": txt(row.get("decision_reason")),
        "risk_flags": "",
    }


def build_buy_intents(board: pd.DataFrame, args: argparse.Namespace, generated_at: str) -> list[dict[str, Any]]:
    intents: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for _, row in board.iterrows():
        passes, flags = row_passes_buy_gates(row, args)
        if not passes:
            continue
        stake = stake_for_row(row, args)
        if stake <= 0:
            continue
        intent = build_buy_intent(row, len(intents) + 1, generated_at, args)
        if intent["max_stake_usdc"] > 0 and intent["target_shares"] > 0:
            intents.append(intent)

    intents = sorted(intents, key=lambda item: float(item.get("expected_pnl_model_usdc") or 0.0), reverse=True)

    capped: list[dict[str, Any]] = []
    total = 0.0
    for idx, intent in enumerate(intents, start=1):
        stake = float(intent["max_stake_usdc"])
        remaining = float(args.max_total_stake_usdc) - total
        if remaining < float(args.min_stake_usdc):
            break
        if stake > remaining:
            stake = round(remaining, 2)
            intent["max_stake_usdc"] = stake
            intent["target_shares"] = round(stake / float(intent["limit_price"]), 6)
            intent["risk_flags"] = "stake_capped_by_total_limit"
        intent["intent_id"] = f"PM-BUY-{idx:04d}"
        capped.append(intent)
        total += stake

    return capped


def load_positions(path: str) -> pd.DataFrame:
    if not path:
        return pd.DataFrame()
    positions_path = Path(path)
    if not positions_path.exists():
        return pd.DataFrame()
    return pd.read_csv(positions_path).fillna("")


def build_sell_intents(board: pd.DataFrame, positions: pd.DataFrame, generated_at: str, args: argparse.Namespace) -> list[dict[str, Any]]:
    if positions.empty or "polymarket_token_id" not in positions.columns:
        return []

    by_token = {txt(row.get("polymarket_token_id")): row for _, row in board.iterrows() if txt(row.get("polymarket_token_id"))}
    intents: list[dict[str, Any]] = []

    for _, position in positions.iterrows():
        token_id = txt(position.get("polymarket_token_id"))
        if not token_id:
            continue
        shares = fnum(position.get("shares"), 0.0)
        if shares <= 0:
            continue
        row = by_token.get(token_id)
        if row is None:
            continue

        edge = fnum(row.get("model_edge_vs_polymarket_breakeven"), 0.0)
        if edge > float(args.sell_edge_threshold):
            continue

        yes_price = fnum(row.get("yes_price"), fnum(row.get("market_implied_probability")))
        limit_price = clamp_price(yes_price - float(args.limit_price_buffer))
        intents.append(
            {
                "intent_id": f"PM-SELL-{len(intents) + 1:04d}",
                "generated_at_utc": generated_at,
                "action": "SELL_YES_LIMIT",
                "execution_status": "GENERATED_NOT_SUBMITTED",
                "match_id": txt(row.get("match_id")),
                "home_team": txt(row.get("home_team")),
                "away_team": txt(row.get("away_team")),
                "commence_time": txt(row.get("commence_time")),
                "selection": txt(row.get("selection")),
                "selection_label": txt(row.get("selection_label")),
                "polymarket_event": txt(row.get("polymarket_event")),
                "polymarket_question": txt(row.get("polymarket_question")),
                "polymarket_outcome": txt(row.get("polymarket_outcome")),
                "polymarket_token_id": token_id,
                "limit_price": limit_price,
                "max_stake_usdc": round(float(shares) * limit_price, 2),
                "target_shares": round(float(shares), 6),
                "model_probability": fnum(row.get("model_probability")),
                "market_implied_probability": fnum(row.get("market_implied_probability"), yes_price),
                "breakeven_probability_after_fee": fnum(row.get("breakeven_probability_after_fee")),
                "bookmaker_no_vig_probability_median": fnum(row.get("bookmaker_no_vig_probability_median")),
                "model_edge_vs_polymarket_breakeven": edge,
                "model_edge_vs_bookmaker_median": fnum(row.get("model_edge_vs_bookmaker_median")),
                "expected_pnl_model_usdc": fnum(row.get("expected_pnl_model_usdc")),
                "paper_stake_units": fnum(row.get("paper_stake_units"), 0.0),
                "superbru_alignment": txt(row.get("superbru_alignment")),
                "bookmaker_cross_check_class": txt(row.get("bookmaker_cross_check_class")),
                "decision_status": txt(row.get("decision_status")),
                "decision_reason": txt(row.get("decision_reason")),
                "risk_flags": "sell_edge_threshold_triggered",
            }
        )

    return intents


def write_outputs(intents: list[dict[str, Any]], args: argparse.Namespace) -> None:
    out_csv = Path(args.out_csv)
    out_json = Path(args.out_json)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)

    frame = pd.DataFrame(intents, columns=TRADE_COLUMNS)
    frame.to_csv(out_csv, index=False)
    out_json.write_text(json.dumps(intents, indent=2), encoding="utf-8")

    print(f"Wrote {out_csv}")
    print(f"Wrote {out_json}")
    print(f"Generated intents: {len(frame)}")
    if not frame.empty:
        print(frame[["intent_id", "action", "home_team", "away_team", "selection_label", "limit_price", "max_stake_usdc", "target_shares", "execution_status"]].to_string(index=False))
    print("Execution status is GENERATED_NOT_SUBMITTED. This script does not place live orders.")


def main() -> int:
    args = build_parser().parse_args()
    decision_board_path = Path(args.decision_board_csv)
    prices_path = Path(args.prices_csv)
    if not decision_board_path.exists():
        raise FileNotFoundError(f"Decision board not found: {decision_board_path}")
    if not prices_path.exists():
        raise FileNotFoundError(f"Prices CSV not found: {prices_path}")

    board = pd.read_csv(decision_board_path).fillna("")
    prices = pd.read_csv(prices_path).fillna("")
    board = enrich_with_market_details(board, prices)

    generated_at = current_utc_iso()
    buy_intents = build_buy_intents(board, args, generated_at)
    positions = load_positions(args.positions_csv)
    sell_intents = build_sell_intents(board, positions, generated_at, args)
    write_outputs([*buy_intents, *sell_intents], args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
