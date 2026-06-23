#!/usr/bin/env python3
"""Evaluate Polymarket passive maker quote mechanics.

This evaluator is intentionally conservative. It does NOT claim real fills.

For passive BUY maker quotes, a public-data fill proxy is only counted when the
next observed ask moves down to or through our stored bid price:

    current_ask <= our_order_price

The current bid being at or above our order price is not a fill proxy for our
passive bid. That situation usually just means our quote is behind, at, or inside
the bid side of the book.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path


STATE_FIELDS = [
    "stored_at_utc",
    "intent_id",
    "token_id",
    "question",
    "outcome",
    "order_price",
    "order_size",
    "stake_usdc",
    "model_prob",
    "bid",
    "ask",
]

EVAL_FIELDS = [
    "evaluated_at_utc",
    "intent_id",
    "token_id",
    "question",
    "outcome",
    "order_price",
    "order_size",
    "stake_usdc",
    "quote_model_prob",
    "quote_bid",
    "quote_ask",
    "current_fair",
    "current_bid",
    "current_ask",
    "current_mid",
    "touch_fill_proxy",
    "bid_crossed_proxy",
    "markout_to_mid",
    "markout_to_fair",
    "markout_mid_usdc",
    "markout_fair_usdc",
    "mechanical_status",
]

SUMMARY_FIELDS = [
    "evaluated_at_utc",
    "quotes_evaluated",
    "touch_fill_proxy_count",
    "bid_crossed_proxy_count",
    "favourable_mid_count",
    "adverse_mid_count",
    "avg_markout_mid_usdc",
    "avg_markout_fair_usdc",
    "total_markout_mid_usdc",
    "total_markout_fair_usdc",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def as_float(value, default=None):
    try:
        if value in {None, ""}:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str], append: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a" if append else "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        if not append or not exists:
            writer.writeheader()
        writer.writerows(rows)


def current_books(snapshot_path: Path) -> dict[str, dict[str, str]]:
    return {
        row.get("token_id", ""): row
        for row in read_csv(snapshot_path)
        if row.get("token_id")
    }


def evaluate_previous(state_path: Path, snapshot_path: Path) -> tuple[list[dict[str, object]], dict[str, object] | None]:
    previous_quotes = read_csv(state_path)
    books = current_books(snapshot_path)
    ts = utc_now()
    rows: list[dict[str, object]] = []

    for quote in previous_quotes:
        token_id = quote.get("token_id", "")
        book = books.get(token_id)
        if not book:
            continue

        order_price = as_float(quote.get("order_price"))
        order_size = as_float(quote.get("order_size"))
        stake = as_float(quote.get("stake_usdc"), 0.0)
        current_bid = as_float(book.get("best_bid"))
        current_ask = as_float(book.get("best_ask"))
        current_fair = as_float(book.get("fair_probability"))

        if order_price is None or order_size is None or current_bid is None or current_ask is None:
            continue

        current_mid = (current_bid + current_ask) / 2.0

        # Conservative passive BUY quote fill proxy:
        # only count a possible fill when the ask moves down to / through our bid.
        touch_fill_proxy = current_ask <= order_price

        # Kept for backward-compatible CSV schema, but not used as a passive-bid fill proxy.
        bid_crossed_proxy = False

        if touch_fill_proxy:
            markout_to_mid = current_mid - order_price
            markout_to_fair = None if current_fair is None else current_fair - order_price
            if markout_to_mid > 0:
                status = "proxy_fill_favourable_mid"
            elif markout_to_mid < 0:
                status = "proxy_fill_adverse_mid"
            else:
                status = "proxy_fill_flat_mid"
        else:
            markout_to_mid = 0.0
            markout_to_fair = None if current_fair is None else 0.0
            status = "not_touched"

        rows.append(
            {
                "evaluated_at_utc": ts,
                "intent_id": quote.get("intent_id", ""),
                "token_id": token_id,
                "question": quote.get("question", ""),
                "outcome": quote.get("outcome", ""),
                "order_price": f"{order_price:.6f}",
                "order_size": f"{order_size:.6f}",
                "stake_usdc": f"{stake:.2f}",
                "quote_model_prob": quote.get("model_prob", ""),
                "quote_bid": quote.get("bid", ""),
                "quote_ask": quote.get("ask", ""),
                "current_fair": "" if current_fair is None else f"{current_fair:.6f}",
                "current_bid": f"{current_bid:.6f}",
                "current_ask": f"{current_ask:.6f}",
                "current_mid": f"{current_mid:.6f}",
                "touch_fill_proxy": touch_fill_proxy,
                "bid_crossed_proxy": bid_crossed_proxy,
                "markout_to_mid": f"{markout_to_mid:.6f}",
                "markout_to_fair": "" if markout_to_fair is None else f"{markout_to_fair:.6f}",
                "markout_mid_usdc": f"{markout_to_mid * order_size:.6f}",
                "markout_fair_usdc": "" if markout_to_fair is None else f"{markout_to_fair * order_size:.6f}",
                "mechanical_status": status,
            }
        )

    if not rows:
        return rows, None

    mid_pnl = [float(row["markout_mid_usdc"]) for row in rows]
    fair_pnl = [float(row["markout_fair_usdc"]) for row in rows if row["markout_fair_usdc"] != ""]
    touched_rows = [row for row in rows if str(row["touch_fill_proxy"]).lower() == "true"]

    summary = {
        "evaluated_at_utc": ts,
        "quotes_evaluated": len(rows),
        "touch_fill_proxy_count": len(touched_rows),
        "bid_crossed_proxy_count": 0,
        "favourable_mid_count": sum(float(row["markout_to_mid"]) > 0 for row in touched_rows),
        "adverse_mid_count": sum(float(row["markout_to_mid"]) < 0 for row in touched_rows),
        "avg_markout_mid_usdc": f"{sum(mid_pnl) / len(mid_pnl):.6f}",
        "avg_markout_fair_usdc": "" if not fair_pnl else f"{sum(fair_pnl) / len(fair_pnl):.6f}",
        "total_markout_mid_usdc": f"{sum(mid_pnl):.6f}",
        "total_markout_fair_usdc": "" if not fair_pnl else f"{sum(fair_pnl):.6f}",
    }
    return rows, summary


def store_current(intents_path: Path, state_path: Path) -> int:
    ts = utc_now()
    rows: list[dict[str, object]] = []

    for intent in read_csv(intents_path):
        if intent.get("mode") != "MARKET_MAKE" or not intent.get("token_id") or not intent.get("order_price"):
            continue

        rows.append(
            {
                "stored_at_utc": ts,
                "intent_id": intent.get("intent_id", ""),
                "token_id": intent.get("token_id", ""),
                "question": intent.get("match", ""),
                "outcome": intent.get("outcome", ""),
                "order_price": intent.get("order_price", ""),
                "order_size": intent.get("order_size", ""),
                "stake_usdc": intent.get("stake_usdc", ""),
                "model_prob": intent.get("model_prob", ""),
                "bid": intent.get("bid", ""),
                "ask": intent.get("ask", ""),
            }
        )

    write_csv(state_path, rows, STATE_FIELDS, append=False)
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", default="outputs/polymarket/market_snapshot.csv")
    parser.add_argument("--intents", default="outputs/polymarket/long_short_intents.csv")
    parser.add_argument("--state", default="outputs/polymarket/mm_quote_state.csv")
    parser.add_argument("--eval-csv", default="outputs/polymarket/mm_quote_evaluations.csv")
    parser.add_argument("--summary-csv", default="outputs/polymarket/mm_quote_summary.csv")
    args = parser.parse_args()

    eval_rows, summary = evaluate_previous(Path(args.state), Path(args.snapshot))

    if eval_rows:
        write_csv(Path(args.eval_csv), eval_rows, EVAL_FIELDS, append=True)
    if summary:
        write_csv(Path(args.summary_csv), [summary], SUMMARY_FIELDS, append=True)

    stored = store_current(Path(args.intents), Path(args.state))

    print(
        f"market-making eval: evaluated={len(eval_rows)} stored_current_quotes={stored} "
        f"touch_fill_proxy={0 if summary is None else summary['touch_fill_proxy_count']} "
        f"bid_crossed_proxy={0 if summary is None else summary['bid_crossed_proxy_count']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())