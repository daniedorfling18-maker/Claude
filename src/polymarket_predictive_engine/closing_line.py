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
from .utils import boolish, git_commit_hash, now_utc, parse_timestamp, read_csv_rows, read_json, safe_float, write_csv, write_json

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

# Cohorts whose forward edge is frozen as a diagnostic (crypto up/down; see AGENTS.md).
# The headline mean CLV mixes these in, which muddies the read on the paths that
# actually carry an edge prior (sharp anchor, dutch-arb, structural bias). The focus
# view below reports the clean mean over everything EXCEPT these diagnostic cohorts.
DEFAULT_DIAGNOSTIC_COHORT_SUBSTRINGS = ["updown", "up_down", "up-down"]


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    return cfg.raw.get("closing_line_value", {}) or {}


def _is_diagnostic_cohort(name: object, substrings: list[str]) -> bool:
    low = str(name or "").lower()
    return any(sub in low for sub in substrings)


# Final (closing-line) rows are persisted append-only so settled CLV evidence cannot
# evaporate when websocket quotes roll past the retention window. Without this, a
# position whose pre-close quotes aged out of the feature file silently loses its
# final line on the next rebuild (observed live: focus finals dropped 2 -> 0).
FINAL_HISTORY_FILENAME = "closing_line_final_history.csv"

# 2026-07-10 pipe repair: positions were being opened WITHOUT close_time for
# every non-crypto cohort (the writer only inferred crypto up/down windows),
# so position_clv_row could never emit line_kind=="closing" - the final
# history stayed empty and Gate A starved at 0 units while matches settled.
# The grader now backfills missing close times from Gamma (closedTime, else
# endDate), idempotently cached so each market is looked up at most once.
CLOSE_TIME_REPAIRS_FILENAME = "close_time_repairs.json"


def _fetch_gamma_close_time(position: dict[str, Any], *, timeout_seconds: float = 6.0) -> str | None:
    """Best-effort close-time lookup for one position; never raises."""
    import requests

    slug = str(position.get("market_slug") or "").strip()
    market_id = str(position.get("market_id") or "").strip()
    attempts: list[dict[str, str]] = []
    if slug:
        attempts.append({"slug": slug})
    if market_id.startswith("0x"):
        attempts.append({"condition_ids": market_id})
    elif market_id.isdigit():
        attempts.append({"id": market_id})
    for params in attempts:
        try:
            response = requests.get(
            "https://gamma-api.polymarket.com/markets", params=params, timeout=timeout_seconds
            )
            response.raise_for_status()
            markets = response.json()
        except Exception:
            continue
        for market in markets if isinstance(markets, list) else []:
            for key in ("closedTime", "closed_time", "endDate", "end_date_iso", "endDateIso"):
                stamp = str(market.get(key) or "").strip()
                if stamp and parse_timestamp(stamp) is not None:
                    return stamp
    return None


def _backfill_missing_close_times(
    cfg: EngineConfig, positions: list[dict[str, Any]], settings: dict[str, Any]
) -> dict[str, Any]:
    """Repair positions lacking close_time via a cached Gamma map.

    Conservative by construction: a repaired close time can only move a
    provisional line to a properly anchored closing line; failures leave the
    position exactly as it was. The cache makes reruns free and offline-safe."""
    if not boolish(settings.get("close_time_backfill_enabled", True)):
        return {"backfilled": 0, "still_missing": 0, "lookups": 0}
    repairs_path = cfg.governance_root / CLOSE_TIME_REPAIRS_FILENAME
    repairs = read_json(repairs_path, default={}) or {}
    max_lookups = int(settings.get("close_time_backfill_max_lookups", 25))
    lookups = 0
    backfilled = 0
    still_missing = 0
    dirty = False
    for position in positions:
        if parse_timestamp(position.get("close_time")) is not None:
            continue
        key = str(position.get("market_id") or position.get("market_slug") or "").strip()
        if not key:
            still_missing += 1
            continue
        stamp = repairs.get(key)
        if stamp is None and lookups < max_lookups:
            lookups += 1
            stamp = _fetch_gamma_close_time(position)
            repairs[key] = stamp or ""
            dirty = True
        if stamp and parse_timestamp(stamp) is not None:
            position["close_time"] = stamp
            backfilled += 1
        else:
            still_missing += 1
    if dirty:
        write_json(repairs_path, repairs)
    return {"backfilled": backfilled, "still_missing": still_missing, "lookups": lookups}


def _load_final_history(path: Path) -> dict[str, dict[str, Any]]:
    """Previously recorded final rows keyed by shadow_position_id, types restored.

    Each row was computed point-in-time when its quotes were still live; reusing it is
    strictly conservative (nothing is backfilled or re-estimated). CSV round-trips turn
    numbers/bools into strings, so coerce the fields the aggregates depend on —
    ``beat_close`` in particular, because the string "False" is truthy.
    """
    history: dict[str, dict[str, Any]] = {}
    for row in read_csv_rows(path):
        position_id = str(row.get("shadow_position_id") or "").strip()
        if not position_id or str(row.get("line_kind")) != "closing":
            continue
        entry_price = safe_float(row.get("entry_price"))
        line_price = safe_float(row.get("line_price"))
        clv = safe_float(row.get("clv"))
        if entry_price is None or line_price is None or clv is None:
            continue
        restored = dict(row)
        restored["entry_price"] = entry_price
        restored["line_price"] = line_price
        restored["clv"] = clv
        restored["clv_pct"] = safe_float(row.get("clv_pct")) if safe_float(row.get("clv_pct")) is not None else round(clv / entry_price, 6)
        line_bid = safe_float(row.get("line_bid"))
        restored["line_bid"] = line_bid if line_bid is not None else ""
        clv_vs_bid = safe_float(row.get("clv_vs_bid"))
        restored["clv_vs_bid"] = clv_vs_bid if clv_vs_bid is not None else ""
        restored["beat_close"] = boolish(row.get("beat_close"))
        quote_count = safe_float(row.get("quote_count"))
        restored["quote_count"] = int(quote_count) if quote_count is not None else 0
        history[position_id] = restored
    return history


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
    close_time_repair = _backfill_missing_close_times(cfg, positions, settings)
    quote_rows = read_csv_rows(features_path)
    quotes = build_quote_history(quote_rows)

    history_path = cfg.governance_root / FINAL_HISTORY_FILENAME
    final_history = _load_final_history(history_path)

    rows: list[dict[str, Any]] = []
    skipped_no_quotes = 0
    recovered_from_history = 0
    if boolish(settings.get("enabled", True)):
        for position in positions:
            row = position_clv_row(position, quotes, as_of=as_of)
            if row is None or row.get("line_kind") != "closing":
                # Quotes rolled past retention (or degraded to a post-close provisional
                # line); a final line recorded while quotes were live wins over both.
                persisted = final_history.get(str(position.get("shadow_position_id") or "").strip())
                if persisted is not None:
                    rows.append(dict(persisted))
                    recovered_from_history += 1
                    continue
            if row is None:
                skipped_no_quotes += 1
                continue
            rows.append(row)

    # Record every freshly computed final row (idempotent by position id); never drop
    # previously recorded finals, even for positions no longer in the positions file.
    for row in rows:
        if row.get("line_kind") == "closing":
            position_id = str(row.get("shadow_position_id") or "").strip()
            if position_id:
                final_history[position_id] = dict(row)
    write_csv(history_path, list(final_history.values()), fieldnames=POSITION_FIELDS)

    cohorts = _aggregate(rows, "signal_cohort", minimum_final_samples=minimum_final_samples, iterations=iterations, seed=seed)
    categories = _aggregate(rows, "category", minimum_final_samples=minimum_final_samples, iterations=iterations, seed=seed)
    final_rows = [row for row in rows if row.get("line_kind") == "closing"]
    positive_cohorts = [row["signal_cohort"] for row in cohorts if row["clv_evidence"] == EVIDENCE_POSITIVE]

    # Focus view: the clean read for the go/no-go, excluding frozen diagnostic
    # cohorts (crypto up/down) that would otherwise dilute the headline mean.
    diagnostic_substrings = [
        str(sub).lower().strip()
        for sub in (settings.get("diagnostic_cohort_substrings") or DEFAULT_DIAGNOSTIC_COHORT_SUBSTRINGS)
        if str(sub).strip()
    ]
    focus_rows = [row for row in rows if not _is_diagnostic_cohort(row.get("signal_cohort"), diagnostic_substrings)]
    focus_final = [row for row in focus_rows if row.get("line_kind") == "closing"]
    frozen_rows = [row for row in rows if _is_diagnostic_cohort(row.get("signal_cohort"), diagnostic_substrings)]
    frozen_final = [row for row in frozen_rows if row.get("line_kind") == "closing"]
    focus_positive = [
        row["signal_cohort"]
        for row in cohorts
        if row["clv_evidence"] == EVIDENCE_POSITIVE
        and not _is_diagnostic_cohort(row["signal_cohort"], diagnostic_substrings)
    ]
    focus_view = {
        "diagnostic_cohort_substrings": diagnostic_substrings,
        "focus_positions": len(focus_rows),
        "focus_final_positions": len(focus_final),
        "focus_mean_clv": round(sum(float(row["clv"]) for row in focus_rows) / len(focus_rows), 6) if focus_rows else None,
        "focus_mean_final_clv": round(sum(float(row["clv"]) for row in focus_final) / len(focus_final), 6) if focus_final else None,
        "focus_beat_close_rate": round(sum(1 for row in focus_rows if row.get("beat_close")) / len(focus_rows), 4) if focus_rows else None,
        # Finals-only beat rate: the ONLY beat statistic the pre-registered
        # $100/month verdict may use. The mixed rate above includes provisional
        # rows, which are diagnostic-only and excluded from evidence CIs.
        "focus_final_beat_close_rate": round(sum(1 for row in focus_final if row.get("beat_close")) / len(focus_final), 4) if focus_final else None,
        "focus_positive_cohorts": focus_positive,
        "focus_cohorts": sorted({row.get("signal_cohort") or "unknown" for row in focus_rows}),
        "frozen_positions": len(frozen_rows),
        "frozen_final_positions": len(frozen_final),
        "frozen_mean_final_clv": round(sum(float(row["clv"]) for row in frozen_final) / len(frozen_final), 6) if frozen_final else None,
        "frozen_cohorts": sorted({row.get("signal_cohort") or "unknown" for row in frozen_rows}),
        "note": "Focus excludes frozen diagnostic cohorts (crypto up/down); it is the clean CLV read for the "
                "sharp-anchor / structural-edge go/no-go. Frozen figures are shown for reference only.",
    }

    summary = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "git_commit": git_commit_hash(),
        "positions_seen": len(positions),
        "positions_scored": len(rows),
        "positions_skipped_no_usable_quotes": skipped_no_quotes,
        "final_line_positions": len(final_rows),
        "final_rows_recovered_from_history": recovered_from_history,
        "final_history_rows": len(final_history),
        "close_time_repair": close_time_repair,
        "final_history_path": str(history_path),
        "provisional_line_positions": len(rows) - len(final_rows),
        "minimum_final_samples": minimum_final_samples,
        "mean_clv": round(sum(float(row["clv"]) for row in rows) / len(rows), 6) if rows else None,
        "mean_final_clv": round(sum(float(row["clv"]) for row in final_rows) / len(final_rows), 6) if final_rows else None,
        "beat_close_rate": round(sum(1 for row in rows if row.get("beat_close")) / len(rows), 4) if rows else None,
        "positive_clv_cohorts": positive_cohorts,
        "focus_view": focus_view,
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
