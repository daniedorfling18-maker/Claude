from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd


CLOB_PRICE_URL = "https://clob.polymarket.com/price"
OUTPUT_COLUMNS = [
    "intent_id",
    "generated_at_utc",
    "quote_checked_at_utc",
    "action",
    "live_decision",
    "execution_mode",
    "execution_status",
    "match_id",
    "home_team",
    "away_team",
    "commence_time",
    "selection_label",
    "polymarket_token_id",
    "model_probability",
    "bookmaker_no_vig_probability_median",
    "stored_market_implied_probability",
    "stored_limit_price",
    "live_best_bid",
    "live_best_ask",
    "live_mid_price",
    "live_spread",
    "live_buy_limit_price",
    "live_sell_limit_price",
    "max_stake_usdc",
    "target_shares",
    "live_buy_cost_usdc",
    "live_sell_proceeds_usdc",
    "live_breakeven_probability_before_fee",
    "live_model_edge_vs_buy_price",
    "live_model_edge_vs_bookmaker_median",
    "expected_pnl_model_usdc",
    "risk_flags",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Refresh Polymarket trade intents against live CLOB quotes and create a live decision report. "
            "This is a dry-run/paper execution loop and does not submit live orders."
        )
    )
    parser.add_argument("--trade-intents-csv", default="outputs/polymarket_available_markets/auto_trade_intents.csv")
    parser.add_argument("--out-csv", default="outputs/polymarket_available_markets/live_trade_decisions.csv")
    parser.add_argument("--out-json", default="outputs/polymarket_available_markets/live_trade_decisions.json")
    parser.add_argument("--execution-mode", choices=["dry_run", "paper"], default="dry_run")
    parser.add_argument("--poll-seconds", type=float, default=0.0, help="If greater than zero, run continuously at this interval.")
    parser.add_argument("--max-iterations", type=int, default=1, help="Number of loop iterations. Use with --poll-seconds.")
    parser.add_argument("--max-spread", type=float, default=0.05)
    parser.add_argument("--min-live-model-edge", type=float, default=0.01)
    parser.add_argument("--min-bookmaker-edge", type=float, default=0.01)
    parser.add_argument("--limit-price-buffer", type=float, default=0.005)
    parser.add_argument("--sleep-seconds", type=float, default=0.15)
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
        if text == "":
            return default
        return float(text)
    except Exception:
        return default


def clamp_price(value: float) -> float:
    return round(min(0.99, max(0.01, value)), 4)


def now_utc() -> str:
    return pd.Timestamp.now("UTC").isoformat()


def http_json(url: str, params: dict[str, Any], timeout: int = 15) -> Any:
    full_url = f"{url}?{urlencode(params, doseq=True)}"
    req = Request(full_url, headers={"User-Agent": "superbru-polymarket-live-decision-loop/1.0"})
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def live_price(token_id: str, side: str, sleep_seconds: float) -> float:
    """Return a Polymarket CLOB price.

    Polymarket's /price convention is side=SELL for the best ask you pay when buying YES,
    and side=BUY for the best bid you can hit when selling YES.
    """

    time.sleep(sleep_seconds)
    data = http_json(CLOB_PRICE_URL, {"token_id": token_id, "side": side})
    price = data.get("price")
    if price is None:
        return float("nan")
    return float(price)


def quote_token(token_id: str, sleep_seconds: float) -> dict[str, float]:
    best_bid = live_price(token_id, "BUY", sleep_seconds)
    best_ask = live_price(token_id, "SELL", sleep_seconds)
    mid = (best_bid + best_ask) / 2 if not math.isnan(best_bid) and not math.isnan(best_ask) else float("nan")
    spread = best_ask - best_bid if not math.isnan(best_bid) and not math.isnan(best_ask) else float("nan")
    return {
        "live_best_bid": best_bid,
        "live_best_ask": best_ask,
        "live_mid_price": mid,
        "live_spread": spread,
    }


def buy_decision(intent: pd.Series, quote: dict[str, float], args: argparse.Namespace) -> tuple[str, str, dict[str, float]]:
    model_probability = fnum(intent.get("model_probability"))
    bookmaker_probability = fnum(intent.get("bookmaker_no_vig_probability_median"))
    best_ask = quote["live_best_ask"]
    best_bid = quote["live_best_bid"]
    spread = quote["live_spread"]
    stake = fnum(intent.get("max_stake_usdc"), 0.0)

    risk_flags: list[str] = []
    if math.isnan(best_ask):
        risk_flags.append("missing_live_best_ask")
    if math.isnan(best_bid):
        risk_flags.append("missing_live_best_bid")
    if not math.isnan(spread) and spread > float(args.max_spread):
        risk_flags.append("spread_above_limit")

    live_buy_limit_price = clamp_price(best_ask + float(args.limit_price_buffer)) if not math.isnan(best_ask) else float("nan")
    target_shares = round(stake / live_buy_limit_price, 6) if live_buy_limit_price and not math.isnan(live_buy_limit_price) else 0.0
    live_buy_cost = round(target_shares * live_buy_limit_price, 6)
    live_edge_vs_buy = model_probability - live_buy_limit_price if not math.isnan(model_probability) and not math.isnan(live_buy_limit_price) else float("nan")
    live_edge_vs_book = model_probability - bookmaker_probability if not math.isnan(model_probability) and not math.isnan(bookmaker_probability) else float("nan")

    if math.isnan(live_edge_vs_buy) or live_edge_vs_buy < float(args.min_live_model_edge):
        risk_flags.append("insufficient_live_model_edge")
    if math.isnan(live_edge_vs_book) or live_edge_vs_book < float(args.min_bookmaker_edge):
        risk_flags.append("insufficient_bookmaker_edge")
    if stake <= 0 or target_shares <= 0:
        risk_flags.append("no_positive_size")

    decision = "PAPER_BUY" if not risk_flags and args.execution_mode == "paper" else "DRY_RUN_BUY" if not risk_flags else "SKIP"
    return decision, "|".join(risk_flags), {
        "live_buy_limit_price": live_buy_limit_price,
        "live_sell_limit_price": float("nan"),
        "target_shares": target_shares,
        "live_buy_cost_usdc": live_buy_cost,
        "live_sell_proceeds_usdc": float("nan"),
        "live_breakeven_probability_before_fee": live_buy_limit_price,
        "live_model_edge_vs_buy_price": live_edge_vs_buy,
        "live_model_edge_vs_bookmaker_median": live_edge_vs_book,
    }


def sell_decision(intent: pd.Series, quote: dict[str, float], args: argparse.Namespace) -> tuple[str, str, dict[str, float]]:
    model_probability = fnum(intent.get("model_probability"))
    bookmaker_probability = fnum(intent.get("bookmaker_no_vig_probability_median"))
    best_bid = quote["live_best_bid"]
    best_ask = quote["live_best_ask"]
    spread = quote["live_spread"]
    shares = fnum(intent.get("target_shares"), 0.0)

    risk_flags: list[str] = []
    if math.isnan(best_bid):
        risk_flags.append("missing_live_best_bid")
    if math.isnan(best_ask):
        risk_flags.append("missing_live_best_ask")
    if not math.isnan(spread) and spread > float(args.max_spread):
        risk_flags.append("spread_above_limit")

    live_sell_limit_price = clamp_price(best_bid - float(args.limit_price_buffer)) if not math.isnan(best_bid) else float("nan")
    live_sell_proceeds = round(shares * live_sell_limit_price, 6) if not math.isnan(live_sell_limit_price) else float("nan")
    live_edge_vs_buy = model_probability - best_ask if not math.isnan(model_probability) and not math.isnan(best_ask) else float("nan")
    live_edge_vs_book = model_probability - bookmaker_probability if not math.isnan(model_probability) and not math.isnan(bookmaker_probability) else float("nan")

    if shares <= 0:
        risk_flags.append("no_positive_shares")
    if not math.isnan(live_edge_vs_buy) and live_edge_vs_buy > 0:
        risk_flags.append("model_still_positive_vs_market")

    decision = "PAPER_SELL" if not risk_flags and args.execution_mode == "paper" else "DRY_RUN_SELL" if not risk_flags else "SKIP"
    return decision, "|".join(risk_flags), {
        "live_buy_limit_price": float("nan"),
        "live_sell_limit_price": live_sell_limit_price,
        "target_shares": shares,
        "live_buy_cost_usdc": float("nan"),
        "live_sell_proceeds_usdc": live_sell_proceeds,
        "live_breakeven_probability_before_fee": live_sell_limit_price,
        "live_model_edge_vs_buy_price": live_edge_vs_buy,
        "live_model_edge_vs_bookmaker_median": live_edge_vs_book,
    }


def evaluate_intents(intents: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    checked_at = now_utc()

    for _, intent in intents.iterrows():
        token_id = txt(intent.get("polymarket_token_id"))
        if not token_id:
            quote = {"live_best_bid": float("nan"), "live_best_ask": float("nan"), "live_mid_price": float("nan"), "live_spread": float("nan")}
        else:
            try:
                quote = quote_token(token_id, float(args.sleep_seconds))
            except Exception:
                quote = {"live_best_bid": float("nan"), "live_best_ask": float("nan"), "live_mid_price": float("nan"), "live_spread": float("nan")}

        action = txt(intent.get("action"))
        if action.startswith("BUY"):
            decision, risk_flags, live_metrics = buy_decision(intent, quote, args)
        elif action.startswith("SELL"):
            decision, risk_flags, live_metrics = sell_decision(intent, quote, args)
        else:
            decision, risk_flags, live_metrics = "SKIP", "unsupported_action", {}

        rows.append(
            {
                "intent_id": txt(intent.get("intent_id")),
                "generated_at_utc": txt(intent.get("generated_at_utc")),
                "quote_checked_at_utc": checked_at,
                "action": action,
                "live_decision": decision,
                "execution_mode": args.execution_mode,
                "execution_status": "PAPER_FILLED" if decision.startswith("PAPER") else "DRY_RUN_NOT_SUBMITTED" if decision.startswith("DRY_RUN") else "SKIPPED",
                "match_id": txt(intent.get("match_id")),
                "home_team": txt(intent.get("home_team")),
                "away_team": txt(intent.get("away_team")),
                "commence_time": txt(intent.get("commence_time")),
                "selection_label": txt(intent.get("selection_label")),
                "polymarket_token_id": token_id,
                "model_probability": fnum(intent.get("model_probability")),
                "bookmaker_no_vig_probability_median": fnum(intent.get("bookmaker_no_vig_probability_median")),
                "stored_market_implied_probability": fnum(intent.get("market_implied_probability")),
                "stored_limit_price": fnum(intent.get("limit_price")),
                **quote,
                **live_metrics,
                "max_stake_usdc": fnum(intent.get("max_stake_usdc"), 0.0),
                "expected_pnl_model_usdc": fnum(intent.get("expected_pnl_model_usdc")),
                "risk_flags": risk_flags,
            }
        )

    frame = pd.DataFrame(rows)
    return frame[[col for col in OUTPUT_COLUMNS if col in frame.columns]]


def run_once(args: argparse.Namespace) -> pd.DataFrame:
    intents_path = Path(args.trade_intents_csv)
    if not intents_path.exists():
        raise FileNotFoundError(f"Trade intents CSV not found: {intents_path}")
    intents = pd.read_csv(intents_path).fillna("")
    decisions = evaluate_intents(intents, args)

    out_csv = Path(args.out_csv)
    out_json = Path(args.out_json)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)

    decisions.to_csv(out_csv, index=False)
    out_json.write_text(json.dumps(decisions.to_dict("records"), indent=2), encoding="utf-8")

    print(f"Wrote {out_csv}")
    print(f"Wrote {out_json}")
    print(f"Decisions: {len(decisions)}")
    if not decisions.empty:
        preview = ["intent_id", "action", "home_team", "away_team", "selection_label", "live_best_bid", "live_best_ask", "live_buy_limit_price", "max_stake_usdc", "target_shares", "live_decision", "risk_flags"]
        print(decisions[[col for col in preview if col in decisions.columns]].to_string(index=False))
    print("No live orders were submitted.")
    return decisions


def main() -> int:
    args = build_parser().parse_args()
    iterations = max(1, int(args.max_iterations))
    for idx in range(iterations):
        run_once(args)
        if idx < iterations - 1 and float(args.poll_seconds) > 0:
            time.sleep(float(args.poll_seconds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
