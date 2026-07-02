"""Closing-line-value (CLV) evidence for shadow positions.

CLV asks the canonical prediction-market quant question: after the bot
(shadow-)entered a position, did the market line move toward the bot's entry?
Systematically beating the closing line is a well-established, settlement-
independent proxy for real forecasting edge, and it accrues much faster than
settlement evidence for slow markets.

Definitions used here (all positions are shadow BUYs):

```text
entry_price   = shadow fill price (entry ask + shadow slippage; conservative)
closing line  = last observed quote at/before the market close_time
current line  = latest observed quote (used provisionally before close)
clv           = line midpoint - entry_price          (probability points)
clv_vs_bid    = line best bid - entry_price          (executable, conservative)
```

This module is shadow-only diagnostics. It never invokes paper or live
trading, never feeds the promotion gates automatically, and fails closed:
cohorts without enough final (post-close) samples are reported as
``insufficient_clv_evidence``. Positive CLV evidence is an input for human /
governance review alongside settlement and bid/ask round-trip evidence, not a
substitute for them.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import EngineConfig, load_config
from .utils import boolish, git_commit_hash, now_utc, parse_timestamp, read_csv_rows, safe_float, write_csv, write_json

POSITION_FIELDS = [
    "shadow_position_id",
    "signal_cohort",
    "category",
    "market_id",
    "token_id",
    "market_slug",
    "question",
    "status",
    "opened_at",
    "close_time",
    "entry_price",
    "line_price",
    "line_bid",
    "line_timestamp",
    "line_kind",
    "clv",
    "clv_pct",
    "clv_vs_bid",
    "beat_close",
    "quote_count",
]

EVIDENCE_POSITIVE = "positive_clv_evidence"
EVIDENCE_NEGATIVE = "negative_clv_evidence"
EVIDENCE_INSUFFICIENT = "insufficient_clv_evidence"


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    return cfg.raw.get("closing_line_value", {}) or {}


def _quote_price(row: dict[str, Any]) -> float | None:
    """Fair line for a quote row: midpoint first, then bid/ask average, then bid."""
    mid = safe_float(row.get("midpoint"))
    if mid is not None and 0 < mid < 1:
        return mid
    bid = safe_float(row.get("best_bid"))
    ask = safe_float(row.get("best_ask"))
    if bid is not None and ask is not None and 0 < bid <= ask < 1:
        return (bid + ask) / 2.0
    if bid is not None and 0 < bid < 1:
        return bid
    return None


def _quote_time(row: dict[str, Any]) -> datetime | None:
    return parse_timestamp(row.get("source_timestamp")) or parse_timestamp(row.get("collected_at_utc"))


def build_quote_history(rows: list[dict[str, Any]]) -> dict[str, list[tuple[datetime, float, float | None]]]:
    """Index quote rows by asset/token id as sorted (time, line_price, best_bid) tuples."""
    history: dict[str, list[tuple[datetime, float, float | None]]] = defaultdict(list)
    for row in rows:
        asset_id = str(row.get("asset_id") or row.get("token_id") or "").strip()
        if not asset_id:
            continue
        when = _quote_time(row)
        price = _quote_price(row)
        if when is None or price is None:
            continue
        bid = safe_float(row.get("best_bid"))
        if bid is not None and not 0 < bid < 1:
            bid = None
        history[asset_id].append((when, price, bid))
    for asset_id in history:
        history[asset_id].sort(key=lambda item: item[0])
    return dict(history)


def position_clv_row(
    position: dict[str, Any],
    quotes: dict[str, list[tuple[datetime, float, float | None]]],
    *,
    as_of: datetime | None = None,
) -> dict[str, Any] | None:
    """Compute one position's CLV against its closing (or latest provisional) line."""
    entry_price = safe_float(position.get("entry_price"))
    if entry_price is None or not 0 < entry_price < 1:
        return None
    token_id = str(position.get("token_id") or "").strip()
    series = quotes.get(token_id, [])
    if not series:
        return None
    as_of = as_of or datetime.now(timezone.utc)
    opened_at = parse_timestamp(position.get("opened_at"))
    close_time = parse_timestamp(position.get("close_time"))

    if close_time is not None and as_of >= close_time:
        eligible = [item for item in series if item[0] <= close_time]
        line_kind = "closing" if eligible else "latest_provisional"
    else:
        eligible = []
        line_kind = "latest_provisional"
    if not eligible:
        eligible = series
    when, line_price, line_bid = eligible[-1]
    # A line observed before entry says nothing about post-entry movement.
    if opened_at is not None and when < opened_at:
        return None

    clv = line_price - entry_price
    clv_vs_bid = (line_bid - entry_price) if line_bid is not None else None
    return {
        "shadow_position_id": position.get("shadow_position_id", ""),
        "signal_cohort": position.get("signal_cohort", "") or "unknown",
        "category": position.get("category", "") or "unknown",
        "market_id": position.get("market_id", ""),
        "token_id": token_id,
        "market_slug": position.get("market_slug", ""),
        "question": position.get("question", ""),
        "status": position.get("status", ""),
        "opened_at": position.get("opened_at", ""),
        "close_time": position.get("close_time", ""),
        "entry_price": round(entry_price, 6),
        "line_price": round(line_price, 6),
        "line_bid": round(line_bid, 6) if line_bid is not None else "",
        "line_timestamp": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "line_kind": line_kind,
        "clv": round(clv, 6),
        "clv_pct": round(clv / entry_price, 6),
        "clv_vs_bid": round(clv_vs_bid, 6) if clv_vs_bid is not None else "",
        "beat_close": clv > 0,
        "quote_count": len(series),
    }


def _bootstrap_mean_ci(values: list[float], *, iterations: int, seed: int) -> tuple[float | None, float | None]:
    if len(values) < 2:
        return None, None
    rng = random.Random(seed)
    means = []
    n = len(values)
    for _ in range(max(1, iterations)):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    low = means[max(0, int(0.025 * len(means)) - 1) if int(0.025 * len(means)) > 0 else 0]
    high = means[min(len(means) - 1, int(math.ceil(0.975 * len(means))) - 1)]
    return round(low, 6), round(high, 6)


def _aggregate(rows: list[dict[str, Any]], key_field: str, *, minimum_final_samples: int, iterations: int, seed: int) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(key_field) or "unknown")].append(row)
    out: list[dict[str, Any]] = []
    for name in sorted(groups):
        group = groups[name]
        final = [row for row in group if row.get("line_kind") == "closing"]
        clvs = [float(row["clv"]) for row in group]
        final_clvs = [float(row["clv"]) for row in final]
        ci_low, ci_high = _bootstrap_mean_ci(final_clvs, iterations=iterations, seed=seed)
        if len(final) >= minimum_final_samples and ci_low is not None and ci_low > 0:
            evidence = EVIDENCE_POSITIVE
        elif len(final) >= minimum_final_samples and ci_high is not None and ci_high < 0:
            evidence = EVIDENCE_NEGATIVE
        else:
            evidence = EVIDENCE_INSUFFICIENT
        out.append(
            {
                key_field: name,
                "positions": len(group),
                "final_positions": len(final),
                "provisional_positions": len(group) - len(final),
                "mean_clv": round(sum(clvs) / len(clvs), 6) if clvs else None,
                "mean_final_clv": round(sum(final_clvs) / len(final_clvs), 6) if final_clvs else None,
                "beat_close_rate": round(sum(1 for row in group if row.get("beat_close")) / len(group), 4) if group else None,
                "final_beat_close_rate": round(sum(1 for row in final if row.get("beat_close")) / len(final), 4) if final else None,
                "final_clv_ci_low": ci_low,
                "final_clv_ci_high": ci_high,
                "final_samples_needed": max(0, minimum_final_samples - len(final)),
                "clv_evidence": evidence,
            }
        )
    return out


def build_closing_line_value(
    cfg: EngineConfig,
    *,
    features_input: str | Path | None = None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Compute CLV for every shadow position and write governance artifacts."""
    settings = _settings(cfg)
    minimum_final_samples = int(settings.get("minimum_final_samples", 12))
    iterations = int(settings.get("bootstrap_iterations", 1000))
    seed = int(settings.get("bootstrap_seed", 20260702))

    positions_path = cfg.output_root / "polymarket_shadow" / "shadow_positions.csv"
    features_path = Path(features_input) if features_input else cfg.output_root / "polymarket_training" / "websocket_market_features.csv"
    positions = read_csv_rows(positions_path)
    quote_rows = read_csv_rows(features_path)
    quotes = build_quote_history(quote_rows)

    rows: list[dict[str, Any]] = []
    skipped_no_quotes = 0
    if boolish(settings.get("enabled", True)):
        for position in positions:
            row = position_clv_row(position, quotes, as_of=as_of)
            if row is None:
                skipped_no_quotes += 1
                continue
            rows.append(row)

    cohorts = _aggregate(rows, "signal_cohort", minimum_final_samples=minimum_final_samples, iterations=iterations, seed=seed)
    categories = _aggregate(rows, "category", minimum_final_samples=minimum_final_samples, iterations=iterations, seed=seed)
    final_rows = [row for row in rows if row.get("line_kind") == "closing"]
    positive_cohorts = [row["signal_cohort"] for row in cohorts if row["clv_evidence"] == EVIDENCE_POSITIVE]

    summary = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "git_commit": git_commit_hash(),
        "positions_seen": len(positions),
        "positions_scored": len(rows),
        "positions_skipped_no_usable_quotes": skipped_no_quotes,
        "final_line_positions": len(final_rows),
        "provisional_line_positions": len(rows) - len(final_rows),
        "minimum_final_samples": minimum_final_samples,
        "mean_clv": round(sum(float(row["clv"]) for row in rows) / len(rows), 6) if rows else None,
        "mean_final_clv": round(sum(float(row["clv"]) for row in final_rows) / len(final_rows), 6) if final_rows else None,
        "beat_close_rate": round(sum(1 for row in rows if row.get("beat_close")) / len(rows), 4) if rows else None,
        "positive_clv_cohorts": positive_cohorts,
        "cohorts": cohorts,
        "categories": categories,
        "evidence_semantics": {
            "clv": "line midpoint minus shadow entry fill (entry ask + slippage); positive means the market moved toward the bot after entry",
            "closing": "last quote at/before market close_time; the settlement-independent forward-evidence standard",
            "latest_provisional": "market not yet closed or no pre-close quote; diagnostic only, excluded from evidence CIs",
        },
        "governance_note": "CLV is diagnostic forward evidence for promotion review; it does not authorise paper or live trading by itself.",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    write_csv(cfg.governance_root / "closing_line_value_positions.csv", rows, fieldnames=POSITION_FIELDS)
    write_json(cfg.governance_root / "closing_line_value.json", summary)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return build_closing_line_value(load_config(config_path))
