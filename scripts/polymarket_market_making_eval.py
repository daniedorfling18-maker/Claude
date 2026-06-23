#!/usr/bin/env python3
"""Evaluate Polymarket market-making quote mechanics.

This is a conservative public-data proxy evaluator. It does not claim real fills.
It compares the previous run's MARKET_MAKE quotes against the current order-book
snapshot and records whether quotes were touched/crossed and whether subsequent
markout was favourable or adverse.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path

STATE_FIELDS = ["stored_at_utc", "intent_id", "token_id", "question", "outcome", "order_price", "order_size", "stake_usdc", "model_prob", "bid", "ask"]
EVAL_FIELDS = [
    "evaluated_at_utc", "intent_id", "token_id", "question", "outcome",
    "order_price", "order_size", "stake_usdc", "quote_model_prob", "quote_bid", "quote_ask",
    "current_fair", "current_bid", "current_ask", "current_mid",
    "touch_fill_proxy", "bid_crossed_proxy", "markout_to_mid", "markout_to_fair",
    "markout_mid_usdc", "markout_fair_usdc", "mechanical_status",
]
SUMMARY_FIELDS = [
    "evaluated_at_utc", "quotes_evaluated", "touch_fill_proxy_count", "bid_crossed_proxy_count",
    "favourable_mid_count", "adverse_mid_count", "avg_markout_mid_usdc", "avg_markout_fair_usdc",
    "total_markout_mid_usdc", "total_markout_fair_usdc",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def f(value, default=None):
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
    return {row.get("token_id", ""): row for row in read_csv(snapshot_path) if row.get("token_id")}


def evaluate_previous(state_path: Path, snapshot_path: Path) -> tuple[list[dict[str, object]], dict[str, object] | None]:
    previous = read_csv(state_path)
    books = current_books(snapshot_path)
    ts = utc_now()
    rows: list[dict[str, object]] = []

    for q in previous:
        token_id = q.get("token_id", "")
        b = books.get(token_id)
        if not b:
            continue
        price = f(q.get("order_price"))
        size = f(q.get("order_size"))
        stake = f(q.get("stake_usdc"), 0.0)
        bid = f(b.get("best_bid"))
        ask = f(b.get("best_ask"))
        fair = f(b.get("fair_probability"))
        if price is None or size is None or bid is None or ask is None:
            continue

        mid = (bid + ask) / 2.0
        touch = ask <= price
        crossed = bid >= price
        mid_markout = mid - price
        fair_markout = None if fair is None else fair - price
        if touch or crossed:
            status = "proxy_fill_favourable_mid" if mid_markout > 0 else "proxy_fill_adverse_mid" if mid_markout < 0 else "proxy_fill_flat_mid"
        else:
            status = "not_touched"

        rows.append({
            "evaluated_at_utc": ts,
            "intent_id": q.get("intent_id", ""),
            "token_id": token_id,
            "question": q.get("question", ""),
            "outcome": q.get("outcome", ""),
            "order_price": f"{price:.6f}",
            "order_size": f"{size:.6f}",
            "stake_usdc": f"{stake:.2f}",
            "quote_model_prob": q.get("model_prob", ""),
            "quote_bid": q.get("bid", ""),
            "quote_ask": q.get("ask", ""),
            "current_fair": "" if fair is None else f"{fair:.6f}",
            "current_bid": f"{bid:.6f}",
            "current_ask": f"{ask:.6f}",
            "current_mid": f"{mid:.6f}",
            "touch_fill_proxy": touch,
            "bid_crossed_proxy": crossed,
            "markout_to_mid": f"{mid_markout:.6f}",
            "markout_to_fair": "" if fair_markout is None else f"{fair_markout:.6f}",
            "markout_mid_usdc": f"{mid_markout * size:.6f}",
            "markout_fair_usdc": "" if fair_markout is None else f"{fair_markout * size:.6f}",
            "mechanical_status": status,
        })

    if not rows:
        return rows, None

    mid_pnl = [float(r["markout_mid_usdc"]) for r in rows]
    fair_pnl = [float(r["markout_fair_usdc"]) for r in rows if r["markout_fair_usdc"] != ""]
    summary = {
        "evaluated_at_utc": ts,
        "quotes_evaluated": len(rows),
        "touch_fill_proxy_count": sum(str(r["touch_fill_proxy"]).lower() == "true" for r in rows),
        "bid_crossed_proxy_count": sum(str(r["bid_crossed_proxy"]).lower() == "true" for r in rows),
        "favourable_mid_count": sum(float(r["markout_to_mid"]) > 0 for r in rows),
        "adverse_mid_count": sum(float(r["markout_to_mid"]) < 0 for r in rows),
        "avg_markout_mid_usdc": f"{sum(mid_pnl) / len(mid_pnl):.6f}",
        "avg_markout_fair_usdc": "" if not fair_pnl else f"{sum(fair_pnl) / len(fair_pnl):.6f}",
        "total_markout_mid_usdc": f"{sum(mid_pnl):.6f}",
        "total_markout_fair_usdc": "" if not fair_pnl else f"{sum(fair_pnl):.6f}",
    }
    return rows, summary


def store_current(intents_path: Path, state_path: Path) -> int:
    ts = utc_now()
    rows = []
    for i in read_csv(intents_path):
        if i.get("mode") != "MARKET_MAKE" or not i.get("token_id") or not i.get("order_price"):
            continue
        rows.append({
            "stored_at_utc": ts,
            "intent_id": i.get("intent_id", ""),
            "token_id": i.get("token_id", ""),
            "question": i.get("match", ""),
            "outcome": i.get("outcome", ""),
            "order_price": i.get("order_price", ""),
            "order_size": i.get("order_size", ""),
            "stake_usdc": i.get("stake_usdc", ""),
            "model_prob": i.get("model_prob", ""),
            "bid": i.get("bid", ""),
            "ask": i.get("ask", ""),
        })
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
