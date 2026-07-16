"""WO-96 exact prospective H3 structural/smart-flow CLV evaluator.

The evaluator is deliberately settlement-independent: it compares a public
BUY fill with the last executable bid observed at or before market close. It
does not use midpoint or resolution labels. Final rows are append-only and the
registered 60/40 analysis remains sealed until the prospective stop.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import math
from pathlib import Path
import random
from typing import Any

from polymarket_common.fees import resolve_taker_fee_schedule, taker_fee_per_share

from .config import EngineConfig, load_config
from .runtime_lock import runtime_lock
from .utils import (
    append_csv_rows,
    normalize_external_timestamp,
    now_utc,
    read_csv_rows,
    safe_float,
    write_csv,
    write_json,
)
from .worldcup_validation import classify_market_family


WORK_ORDER = "WO-96"
REGISTRATION_AT = datetime(2026, 7, 12, 13, 38, 47, tzinfo=timezone.utc)
REGISTERED_STOP_FILLS = 100
REGISTERED_STOP_DAYS = 90
REGISTERED_ENTRY_MIN = 0.05
REGISTERED_ENTRY_MAX = 0.90
REGISTERED_FINAL_QUOTE_MAX_AGE_SECONDS = 60 * 60
REGISTERED_INTELLIGENCE_MAX_AGE_SECONDS = 36 * 60 * 60
REGISTERED_MIN_VALIDATION_FILLS = 30
REGISTERED_MIN_VALIDATION_MARKETS = 10
REGISTERED_MIN_COHORT_FILLS = 20
REGISTERED_WALLET_DISCOVERY_FILLS = 5
REGISTERED_WALLET_DISCOVERY_MARKETS = 2
REGISTERED_FDR_ALPHA = 0.10
REGISTERED_MAX_FAMILY_CONCENTRATION = 0.35
REGISTERED_EXIT_COST_PER_SHARE = 0.005
REGISTERED_ADVERSE_COST_PER_SHARE = 0.005
REGISTERED_BOOTSTRAP_ITERATIONS = 1000
REGISTERED_BOOTSTRAP_SEED = 20260716

FINAL_FIELDS = [
    "observation_id",
    "trade_id",
    "wallet",
    "market",
    "token",
    "side",
    "entry_timestamp_utc",
    "entry_day_utc",
    "entry_price",
    "size",
    "title",
    "market_slug",
    "event_slug",
    "outcome",
    "outcome_index",
    "market_family",
    "price_cohort",
    "rank_cohort",
    "leaderboard_rank",
    "leaderboard_snapshot_at_utc",
    "top_holder_observed",
    "holder_snapshot_at_utc",
    "close_time_utc",
    "final_bid",
    "final_bid_timestamp_utc",
    "gross_clv_per_share",
    "entry_taker_fee_per_share",
    "exit_taker_fee_per_share",
    "fixed_exit_cost_per_share",
    "adverse_cost_per_share",
    "net_clv_per_share",
    "taker_fee_category",
    "taker_fee_rate",
    "taker_fee_source",
    "taker_fee_model_version",
    "graded_at_utc",
    "paper_trading_invoked",
    "live_trading_invoked",
]

COHORT_FIELDS = [
    "cohort",
    "cohort_type",
    "validation_fills",
    "validation_markets",
    "mean_net_clv",
    "cluster_bootstrap_ci_low_90",
    "cluster_bootstrap_ci_high_90",
    "cluster_bootstrap_p_one_sided",
    "bh_fdr_pass",
    "maximum_positive_family_share",
    "sample_floor_pass",
    "market_floor_pass",
    "cohort_floor_pass",
    "positive_mean_pass",
    "cluster_ci_pass",
    "family_concentration_pass",
    "support_gate_pass",
    "recommended_action",
]

FIXED_COHORTS = (
    ("price:longshot_0.05_0.20", "price"),
    ("price:middle_0.20_0.80", "price"),
    ("price:favorite_0.80_0.90", "price"),
    ("rank:1_10", "rank"),
    ("rank:11_50", "rank"),
    ("rank:51_100", "rank"),
    ("holder:top_n_observed", "holder"),
)

COVERAGE_KEYS = (
    "fills_seen",
    "missing_trade_id",
    "duplicate_trade_ids",
    "non_buy",
    "missing_wallet",
    "missing_token",
    "missing_market",
    "missing_or_invalid_entry_price",
    "missing_or_invalid_size",
    "outside_entry_band",
    "missing_or_invalid_entry_timestamp",
    "pre_registration",
    "future_fill",
    "after_stop_window",
    "duplicate_wallet_token_day",
    "independent_eligible_fills",
    "missing_market_token_quote_history",
    "missing_or_invalid_close_time",
    "market_not_closed",
    "no_quote_after_entry",
    "stale_or_missing_pre_close_bid",
    "graded_candidates",
    "leaderboard_snapshot_missing_or_stale",
    "holder_snapshot_missing_or_stale",
)


def _utc(value: Any) -> datetime | None:
    stamp = normalize_external_timestamp(value)
    if stamp is None:
        return None
    try:
        return datetime.fromtimestamp(stamp, timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bounded_float(raw: dict[str, Any], key: str, default: float, *, lower: bool) -> float:
    configured = safe_float(raw.get(key))
    if configured is None:
        return default
    return max(default, configured) if lower else min(default, configured)


def _bounded_int(raw: dict[str, Any], key: str, default: int, *, lower: bool) -> int:
    configured = safe_float(raw.get(key))
    if configured is None or not math.isfinite(configured):
        return default
    parsed = max(1, int(configured))
    return max(default, parsed) if lower else min(default, parsed)


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = cfg.raw.get("h3_smart_flow", {}) if isinstance(cfg.raw.get("h3_smart_flow"), dict) else {}
    return {
        "enabled": str(raw.get("enabled", True)).strip().lower() not in {"0", "false", "no", "off"},
        "entry_min": _bounded_float(raw, "entry_min", REGISTERED_ENTRY_MIN, lower=True),
        "entry_max": _bounded_float(raw, "entry_max", REGISTERED_ENTRY_MAX, lower=False),
        # Stopping boundaries are fixed, not configurable: either direction
        # would change which rows validation can see.
        "stop_fills": REGISTERED_STOP_FILLS,
        "stop_days": REGISTERED_STOP_DAYS,
        "final_quote_max_age_seconds": _bounded_int(
            raw,
            "final_quote_max_age_seconds",
            REGISTERED_FINAL_QUOTE_MAX_AGE_SECONDS,
            lower=False,
        ),
        "intelligence_max_age_seconds": _bounded_int(
            raw,
            "intelligence_max_age_seconds",
            REGISTERED_INTELLIGENCE_MAX_AGE_SECONDS,
            lower=False,
        ),
        "minimum_validation_fills": _bounded_int(
            raw,
            "minimum_validation_fills",
            REGISTERED_MIN_VALIDATION_FILLS,
            lower=True,
        ),
        "minimum_validation_markets": _bounded_int(
            raw,
            "minimum_validation_markets",
            REGISTERED_MIN_VALIDATION_MARKETS,
            lower=True,
        ),
        "minimum_cohort_fills": _bounded_int(
            raw,
            "minimum_cohort_fills",
            REGISTERED_MIN_COHORT_FILLS,
            lower=True,
        ),
        "wallet_discovery_fills": _bounded_int(
            raw,
            "wallet_discovery_fills",
            REGISTERED_WALLET_DISCOVERY_FILLS,
            lower=True,
        ),
        "wallet_discovery_markets": _bounded_int(
            raw,
            "wallet_discovery_markets",
            REGISTERED_WALLET_DISCOVERY_MARKETS,
            lower=True,
        ),
        "fdr_alpha": _bounded_float(raw, "fdr_alpha", REGISTERED_FDR_ALPHA, lower=False),
        "maximum_family_concentration": _bounded_float(
            raw,
            "maximum_family_concentration",
            REGISTERED_MAX_FAMILY_CONCENTRATION,
            lower=False,
        ),
        "fixed_exit_cost_per_share": _bounded_float(
            raw,
            "fixed_exit_cost_per_share",
            REGISTERED_EXIT_COST_PER_SHARE,
            lower=True,
        ),
        "adverse_cost_per_share": _bounded_float(
            raw,
            "adverse_cost_per_share",
            REGISTERED_ADVERSE_COST_PER_SHARE,
            lower=True,
        ),
        "bootstrap_iterations": _bounded_int(
            raw,
            "bootstrap_iterations",
            REGISTERED_BOOTSTRAP_ITERATIONS,
            lower=True,
        ),
        "bootstrap_seed": REGISTERED_BOOTSTRAP_SEED,
    }


def _price_cohort(price: float) -> str:
    if price < 0.20:
        return "price:longshot_0.05_0.20"
    if price <= 0.80:
        return "price:middle_0.20_0.80"
    return "price:favorite_0.80_0.90"


def _rank_cohort(rank: float | None) -> str:
    if rank is None or rank < 1 or rank > 100:
        return ""
    if rank <= 10:
        return "rank:1_10"
    if rank <= 50:
        return "rank:11_50"
    return "rank:51_100"


def _snapshot_index(
    rows: list[dict[str, Any]],
    key_fn: Any,
) -> dict[Any, tuple[list[float], list[dict[str, Any]]]]:
    grouped: dict[Any, list[tuple[float, dict[str, Any]]]] = defaultdict(list)
    for row in rows:
        stamp = normalize_external_timestamp(row.get("snapshot_at_utc"))
        key = key_fn(row)
        if stamp is not None and key is not None:
            grouped[key].append((stamp, row))
    out: dict[Any, tuple[list[float], list[dict[str, Any]]]] = {}
    for key, values in grouped.items():
        values.sort(key=lambda item: item[0])
        out[key] = ([item[0] for item in values], [item[1] for item in values])
    return out


def _latest_snapshot(
    index: dict[Any, tuple[list[float], list[dict[str, Any]]]],
    key: Any,
    at: datetime,
    max_age_seconds: int,
) -> tuple[dict[str, Any] | None, datetime | None]:
    values = index.get(key)
    if values is None:
        return None, None
    times, rows = values
    target = at.timestamp()
    position = -1
    low, high = 0, len(times)
    while low < high:
        middle = (low + high) // 2
        if times[middle] <= target:
            low = middle + 1
        else:
            high = middle
    position = low - 1
    if position < 0 or target - times[position] > max_age_seconds:
        return None, None
    stamp = datetime.fromtimestamp(times[position], timezone.utc)
    return rows[position], stamp


def _eligible_fills(
    rows: list[dict[str, Any]],
    *,
    as_of: datetime,
    settings: dict[str, Any],
    coverage: dict[str, int],
) -> list[dict[str, Any]]:
    seen_trade_ids: set[str] = set()
    parsed: list[dict[str, Any]] = []
    deadline = REGISTRATION_AT + timedelta(days=int(settings["stop_days"]))
    coverage["fills_seen"] = len(rows)
    for raw in rows:
        trade_id = str(raw.get("trade_id") or "").strip()
        if not trade_id:
            coverage["missing_trade_id"] += 1
            continue
        if trade_id and trade_id in seen_trade_ids:
            coverage["duplicate_trade_ids"] += 1
            continue
        if trade_id:
            seen_trade_ids.add(trade_id)
        if str(raw.get("side") or "").strip().upper() != "BUY":
            coverage["non_buy"] += 1
            continue
        wallet = str(raw.get("wallet") or "").strip().lower()
        token = str(raw.get("asset_id") or raw.get("token") or "").strip()
        market = str(raw.get("market") or raw.get("condition_id") or "").strip()
        if not wallet:
            coverage["missing_wallet"] += 1
            continue
        if not token:
            coverage["missing_token"] += 1
            continue
        if not market:
            coverage["missing_market"] += 1
            continue
        price = safe_float(raw.get("price") or raw.get("entry_price"))
        if price is None or not 0 < price < 1:
            coverage["missing_or_invalid_entry_price"] += 1
            continue
        size = safe_float(raw.get("size") or raw.get("quantity"))
        if size is None or size <= 0:
            coverage["missing_or_invalid_size"] += 1
            continue
        if not float(settings["entry_min"]) <= price <= float(settings["entry_max"]):
            coverage["outside_entry_band"] += 1
            continue
        entered_at = _utc(raw.get("timestamp") or raw.get("opened_at"))
        if entered_at is None:
            coverage["missing_or_invalid_entry_timestamp"] += 1
            continue
        if entered_at <= REGISTRATION_AT:
            coverage["pre_registration"] += 1
            continue
        if entered_at > as_of:
            coverage["future_fill"] += 1
            continue
        if entered_at > deadline:
            coverage["after_stop_window"] += 1
            continue
        parsed.append(
            {
                **raw,
                "trade_id": trade_id,
                "wallet": wallet,
                "token": token,
                "market": market,
                "entry_price": price,
                "entry_size": size,
                "entry_dt": entered_at,
            }
        )
    parsed.sort(key=lambda row: (row["entry_dt"], row["trade_id"]))
    independent: list[dict[str, Any]] = []
    seen_units: set[tuple[str, str, str]] = set()
    for row in parsed:
        key = (row["wallet"], row["token"], row["entry_dt"].date().isoformat())
        if key in seen_units:
            coverage["duplicate_wallet_token_day"] += 1
            continue
        seen_units.add(key)
        independent.append(row)
    coverage["independent_eligible_fills"] = len(independent)
    return independent


def _quote_index(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    index: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        market = str(row.get("market") or row.get("market_id") or "").strip()
        token = str(row.get("asset_id") or row.get("token_id") or "").strip()
        quote_at = _utc(row.get("source_timestamp")) or _utc(row.get("collected_at_utc"))
        close_at = _utc(row.get("close_time"))
        bid = safe_float(row.get("best_bid"))
        if market and token:
            index[(market, token)].append(
                {**row, "quote_dt": quote_at, "close_dt": close_at, "bid": bid}
            )
    for values in index.values():
        values.sort(key=lambda row: row.get("quote_dt") or datetime.min.replace(tzinfo=timezone.utc))
    return index


def _final_quote(
    index: dict[tuple[str, str], list[dict[str, Any]]],
    fill: dict[str, Any],
    *,
    as_of: datetime,
    max_age_seconds: int,
) -> tuple[dict[str, Any] | None, str]:
    rows = index.get((fill["market"], fill["token"]), [])
    if not rows:
        return None, "missing_market_token_quote_history"
    with_close = [row for row in rows if row.get("close_dt") is not None]
    if not with_close:
        return None, "missing_or_invalid_close_time"
    closed = [row for row in with_close if row["close_dt"] <= as_of]
    if not closed:
        return None, "market_not_closed"
    after_entry = [
        row
        for row in closed
        if row.get("quote_dt") is not None
        and row["quote_dt"] > fill["entry_dt"]
        and row["quote_dt"] <= row["close_dt"]
    ]
    if not after_entry:
        return None, "no_quote_after_entry"
    final = [
        row
        for row in after_entry
        if row.get("bid") is not None
        and 0 < float(row["bid"]) < 1
        and 0 <= (row["close_dt"] - row["quote_dt"]).total_seconds() <= max_age_seconds
    ]
    if not final:
        return None, "stale_or_missing_pre_close_bid"
    return max(final, key=lambda row: row["quote_dt"]), ""


def _observation_id(fill: dict[str, Any]) -> str:
    material = "|".join(
        (
            fill["trade_id"],
            fill["wallet"],
            fill["token"],
            fill["entry_dt"].date().isoformat(),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _grade_rows(
    fills: list[dict[str, Any]],
    quote_rows: list[dict[str, Any]],
    leaderboard_rows: list[dict[str, Any]],
    holder_rows: list[dict[str, Any]],
    *,
    as_of: datetime,
    generated_at: str,
    settings: dict[str, Any],
    coverage: dict[str, int],
) -> list[dict[str, Any]]:
    quotes = _quote_index(quote_rows)
    leaderboard = _snapshot_index(
        leaderboard_rows,
        lambda row: str(row.get("wallet") or "").strip().lower() or None,
    )
    holders = _snapshot_index(
        holder_rows,
        lambda row: (
            str(row.get("wallet") or "").strip().lower(),
            str(row.get("token") or "").strip(),
            str(row.get("market") or "").strip(),
        )
        if row.get("wallet") and row.get("token") and row.get("market")
        else None,
    )
    graded: list[dict[str, Any]] = []
    for fill in fills:
        quote, reason = _final_quote(
            quotes,
            fill,
            as_of=as_of,
            max_age_seconds=int(settings["final_quote_max_age_seconds"]),
        )
        if quote is None:
            coverage[reason] += 1
            continue
        rank_row, rank_at = _latest_snapshot(
            leaderboard,
            fill["wallet"],
            fill["entry_dt"],
            int(settings["intelligence_max_age_seconds"]),
        )
        rank = safe_float(rank_row.get("rank")) if rank_row else None
        rank_bucket = _rank_cohort(rank)
        if rank_row is None:
            coverage["leaderboard_snapshot_missing_or_stale"] += 1
        holder_row, holder_at = _latest_snapshot(
            holders,
            (fill["wallet"], fill["token"], fill["market"]),
            fill["entry_dt"],
            int(settings["intelligence_max_age_seconds"]),
        )
        holder_observed = bool(holder_row and (safe_float(holder_row.get("amount") or holder_row.get("size")) or 0) > 0)
        if holder_row is None:
            coverage["holder_snapshot_missing_or_stale"] += 1

        market_meta = {
            **quote,
            "question": fill.get("title") or quote.get("question"),
            "market_slug": fill.get("market_slug") or quote.get("market_slug"),
            "event_slug": fill.get("event_slug") or quote.get("event_slug"),
            "outcome": fill.get("outcome") or quote.get("selection"),
        }
        fee_schedule = resolve_taker_fee_schedule(market_meta)
        entry_price = float(fill["entry_price"])
        final_bid = float(quote["bid"])
        entry_fee = taker_fee_per_share(price=entry_price, schedule=fee_schedule)
        exit_fee = taker_fee_per_share(price=final_bid, schedule=fee_schedule)
        gross_clv = final_bid - entry_price
        net_clv = (
            gross_clv
            - entry_fee
            - exit_fee
            - float(settings["fixed_exit_cost_per_share"])
            - float(settings["adverse_cost_per_share"])
        )
        family_row = {
            **market_meta,
            "market_id": fill["market"],
            "category": quote.get("category"),
        }
        graded.append(
            {
                "observation_id": _observation_id(fill),
                "trade_id": fill["trade_id"],
                "wallet": fill["wallet"],
                "market": fill["market"],
                "token": fill["token"],
                "side": "BUY",
                "entry_timestamp_utc": _iso(fill["entry_dt"]),
                "entry_day_utc": fill["entry_dt"].date().isoformat(),
                "entry_price": round(entry_price, 8),
                "size": fill["entry_size"],
                "title": str(fill.get("title") or quote.get("question") or ""),
                "market_slug": str(fill.get("market_slug") or quote.get("market_slug") or ""),
                "event_slug": str(fill.get("event_slug") or quote.get("event_slug") or ""),
                "outcome": str(fill.get("outcome") or quote.get("selection") or ""),
                "outcome_index": str(fill.get("outcome_index") or ""),
                "market_family": classify_market_family(family_row),
                "price_cohort": _price_cohort(entry_price),
                "rank_cohort": rank_bucket,
                "leaderboard_rank": rank if rank is not None else "",
                "leaderboard_snapshot_at_utc": _iso(rank_at) if rank_at else "",
                "top_holder_observed": holder_observed,
                "holder_snapshot_at_utc": _iso(holder_at) if holder_at else "",
                "close_time_utc": _iso(quote["close_dt"]),
                "final_bid": round(final_bid, 8),
                "final_bid_timestamp_utc": _iso(quote["quote_dt"]),
                "gross_clv_per_share": round(gross_clv, 8),
                "entry_taker_fee_per_share": round(entry_fee, 8),
                "exit_taker_fee_per_share": round(exit_fee, 8),
                "fixed_exit_cost_per_share": float(settings["fixed_exit_cost_per_share"]),
                "adverse_cost_per_share": float(settings["adverse_cost_per_share"]),
                "net_clv_per_share": round(net_clv, 8),
                "taker_fee_category": fee_schedule.category,
                "taker_fee_rate": fee_schedule.rate,
                "taker_fee_source": fee_schedule.source,
                "taker_fee_model_version": fee_schedule.model_version,
                "graded_at_utc": generated_at,
                "paper_trading_invoked": False,
                "live_trading_invoked": False,
            }
        )
    coverage["graded_candidates"] = len(graded)
    return graded


def _append_final_rows(
    cfg: EngineConfig,
    path: Path,
    candidates: list[dict[str, Any]],
    *,
    stop_fills: int,
) -> tuple[list[dict[str, str]], int, bool]:
    with runtime_lock(cfg, "h3_final_fills_append", stale_after_seconds=300) as lock:
        if not lock.acquired:
            return read_csv_rows(path), 0, True
        existing = read_csv_rows(path)
        seen = {str(row.get("observation_id") or "") for row in existing}
        available = max(0, stop_fills - len(existing))
        new_rows = [
            row
            for row in sorted(candidates, key=lambda item: (item["entry_timestamp_utc"], item["trade_id"]))
            if row["observation_id"] not in seen
        ][:available]
        append_csv_rows(path, new_rows, fieldnames=FINAL_FIELDS)
        return [*existing, *new_rows], len(new_rows), False


def _cluster_bootstrap(
    rows: list[dict[str, Any]],
    *,
    iterations: int,
    seed: int,
) -> tuple[float | None, float | None, float]:
    by_market: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        value = safe_float(row.get("net_clv_per_share"))
        market = str(row.get("market") or "").strip()
        if value is not None and market:
            by_market[market].append(value)
    market_means = [sum(values) / len(values) for _, values in sorted(by_market.items())]
    if len(market_means) < 2:
        return None, None, 1.0
    rng = random.Random(seed)
    means: list[float] = []
    n = len(market_means)
    for _ in range(iterations):
        sample = [market_means[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    low = means[max(0, math.ceil(0.05 * len(means)) - 1)]
    high = means[min(len(means) - 1, math.ceil(0.95 * len(means)) - 1)]
    p_value = (1 + sum(1 for value in means if value <= 0)) / (len(means) + 1)
    return round(low, 8), round(high, 8), round(p_value, 8)


def _positive_family_share(rows: list[dict[str, Any]]) -> float:
    positive: dict[str, float] = defaultdict(float)
    for row in rows:
        value = safe_float(row.get("net_clv_per_share")) or 0.0
        if value > 0:
            positive[str(row.get("market_family") or "unknown")] += value
    total = sum(positive.values())
    if total <= 0:
        return 1.0
    return max(positive.values()) / total


def _memberships(row: dict[str, Any], selected_wallets: set[str]) -> set[str]:
    memberships = {str(row.get("price_cohort") or "")}
    if row.get("rank_cohort"):
        memberships.add(str(row["rank_cohort"]))
    if str(row.get("top_holder_observed") or "").strip().lower() in {"true", "1", "yes"}:
        memberships.add("holder:top_n_observed")
    wallet = str(row.get("wallet") or "").strip().lower()
    if wallet in selected_wallets:
        memberships.add(f"wallet:{wallet}")
    memberships.discard("")
    return memberships


def _bh_apply(rows: list[dict[str, Any]], alpha: float) -> None:
    ranked = sorted(
        enumerate(rows),
        key=lambda item: float(item[1].get("cluster_bootstrap_p_one_sided") or 1.0),
    )
    cutoff = -1
    total = len(ranked)
    for position, (_, row) in enumerate(ranked, start=1):
        if float(row.get("cluster_bootstrap_p_one_sided") or 1.0) <= alpha * position / max(1, total):
            cutoff = position
    passing = {index for position, (index, _) in enumerate(ranked, start=1) if position <= cutoff}
    for index, row in enumerate(rows):
        row["bh_fdr_pass"] = index in passing


def _formal_evaluation(
    rows: list[dict[str, Any]], settings: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    split_at = math.floor(0.60 * len(rows))
    discovery = rows[:split_at]
    validation = rows[split_at:]
    wallet_discovery: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in discovery:
        wallet_discovery[str(row.get("wallet") or "").strip().lower()].append(row)
    selected_wallets = {
        wallet
        for wallet, wallet_rows in wallet_discovery.items()
        if wallet
        and len(wallet_rows) >= int(settings["wallet_discovery_fills"])
        and len({str(row.get("market") or "") for row in wallet_rows})
        >= int(settings["wallet_discovery_markets"])
    }
    definitions = [*FIXED_COHORTS, *((f"wallet:{wallet}", "wallet") for wallet in sorted(selected_wallets))]
    overall_floor = len(validation) >= int(settings["minimum_validation_fills"])
    overall_markets = len({str(row.get("market") or "") for row in validation if row.get("market")})
    overall_market_floor = overall_markets >= int(settings["minimum_validation_markets"])
    cohort_rows: list[dict[str, Any]] = []
    for index, (cohort, cohort_type) in enumerate(definitions):
        members = [row for row in validation if cohort in _memberships(row, selected_wallets)]
        values = [float(row["net_clv_per_share"]) for row in members]
        mean = sum(values) / len(values) if values else None
        low, high, p_value = _cluster_bootstrap(
            members,
            iterations=int(settings["bootstrap_iterations"]),
            seed=int(settings["bootstrap_seed"]) + index,
        )
        concentration = _positive_family_share(members)
        cohort_rows.append(
            {
                "cohort": cohort,
                "cohort_type": cohort_type,
                "validation_fills": len(members),
                "validation_markets": len({str(row.get("market") or "") for row in members}),
                "mean_net_clv": round(mean, 8) if mean is not None else None,
                "cluster_bootstrap_ci_low_90": low,
                "cluster_bootstrap_ci_high_90": high,
                "cluster_bootstrap_p_one_sided": p_value,
                "bh_fdr_pass": False,
                "maximum_positive_family_share": round(concentration, 6),
                "sample_floor_pass": overall_floor,
                "market_floor_pass": overall_market_floor,
                "cohort_floor_pass": len(members) >= int(settings["minimum_cohort_fills"]),
                "positive_mean_pass": mean is not None and mean > 0,
                "cluster_ci_pass": low is not None and low > 0,
                "family_concentration_pass": concentration <= float(settings["maximum_family_concentration"]),
                "support_gate_pass": False,
                "recommended_action": "suppress",
            }
        )
    _bh_apply(cohort_rows, float(settings["fdr_alpha"]))
    for row in cohort_rows:
        row["support_gate_pass"] = all(
            bool(row[key])
            for key in (
                "sample_floor_pass",
                "market_floor_pass",
                "cohort_floor_pass",
                "positive_mean_pass",
                "cluster_ci_pass",
                "bh_fdr_pass",
                "family_concentration_pass",
            )
        )
        row["recommended_action"] = "shadow_research_candidate" if row["support_gate_pass"] else "suppress"
    passing = [str(row["cohort"]) for row in cohort_rows if row["support_gate_pass"]]
    split = {
        "discovery_fills": len(discovery),
        "validation_fills": len(validation),
        "validation_markets": overall_markets,
        "selected_wallets": sorted(selected_wallets),
    }
    return cohort_rows, passing, split


def evaluate_h3_smart_flow(
    cfg: EngineConfig,
    *,
    as_of: datetime | None = None,
    fills_input: str | Path | None = None,
    features_input: str | Path | None = None,
    leaderboard_input: str | Path | None = None,
    holders_input: str | Path | None = None,
) -> dict[str, Any]:
    settings = _settings(cfg)
    as_of = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    generated_at = _iso(as_of)
    out_root = cfg.output_root / "h3_smart_flow"
    summary_path = out_root / "h3_evaluation.json"
    final_path = out_root / "h3_final_fills.csv"
    cohort_path = out_root / "h3_cohorts.csv"
    paths = {
        "fills": Path(fills_input or cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv"),
        "features": Path(
            features_input or cfg.output_root / "polymarket_training" / "websocket_market_features.csv"
        ),
        "leaderboard": Path(
            leaderboard_input or cfg.output_root / "wallet_intelligence" / "leaderboard_history.csv"
        ),
        "holders": Path(holders_input or cfg.output_root / "wallet_intelligence" / "holders_history.csv"),
    }
    coverage = {key: 0 for key in COVERAGE_KEYS}
    base = {
        "work_order": WORK_ORDER,
        "generated_at_utc": generated_at,
        "registration_at_utc": _iso(REGISTRATION_AT),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
        "critical_settings": settings,
        "inputs": {key: str(path) for key, path in paths.items()},
        "final_ledger_path": str(final_path),
        "cohort_path": str(cohort_path),
    }
    if not settings["enabled"]:
        summary = {
            **base,
            "status": "disabled",
            "formal_evaluation_started": False,
            "coverage": coverage,
            "passing_cohorts": [],
        }
        write_csv(cohort_path, [], fieldnames=COHORT_FIELDS)
        write_json(summary_path, summary)
        return summary

    fills = read_csv_rows(paths["fills"])
    features = read_csv_rows(paths["features"])
    leaderboard = read_csv_rows(paths["leaderboard"])
    holders = read_csv_rows(paths["holders"])
    independent = _eligible_fills(fills, as_of=as_of, settings=settings, coverage=coverage)
    graded = _grade_rows(
        independent,
        features,
        leaderboard,
        holders,
        as_of=as_of,
        generated_at=generated_at,
        settings=settings,
        coverage=coverage,
    )
    all_final, appended, lock_contended = _append_final_rows(
        cfg,
        final_path,
        graded,
        stop_fills=int(settings["stop_fills"]),
    )
    all_final.sort(key=lambda row: (str(row.get("entry_timestamp_utc") or ""), str(row.get("trade_id") or "")))
    deadline = REGISTRATION_AT + timedelta(days=int(settings["stop_days"]))
    stop_reached = len(all_final) >= int(settings["stop_fills"]) or as_of >= deadline
    stop_reason = (
        "independent_final_fill_cap"
        if len(all_final) >= int(settings["stop_fills"])
        else "calendar_deadline"
        if as_of >= deadline
        else "not_reached"
    )
    if lock_contended:
        cohort_rows: list[dict[str, Any]] = []
        passing: list[str] = []
        split: dict[str, Any] = {}
        status = "error"
        formal_started = False
    elif stop_reached:
        cohort_rows, passing, split = _formal_evaluation(all_final, settings)
        status = "evaluated" if passing else "suppressed"
        formal_started = True
    else:
        cohort_rows = []
        passing = []
        split = {}
        status = "collecting"
        formal_started = False
    write_csv(cohort_path, cohort_rows, fieldnames=COHORT_FIELDS)
    summary = {
        **base,
        "status": status,
        "formal_evaluation_started": formal_started,
        "stop_reached": stop_reached and not lock_contended,
        "stop_reason": stop_reason,
        "stop_deadline_utc": _iso(deadline),
        "final_fills": len(all_final),
        "final_fills_remaining": max(0, int(settings["stop_fills"]) - len(all_final)),
        "new_final_fills_appended": appended,
        "append_lock_contended": lock_contended,
        "coverage": coverage,
        "split": split,
        "cohorts_tested": len(cohort_rows),
        "passing_cohorts": passing,
        "decision": "shadow_research_candidate" if passing else "suppress" if formal_started else "collect_more",
        "evidence_semantics": (
            "final executable bid minus observed public BUY, net canonical entry/exit taker fees and "
            "registered fixed costs; no midpoint or resolution label"
        ),
        "governance_note": (
            "H3 can create a shadow research candidate only. It never authorises paper/live risk or orders."
        ),
    }
    write_json(summary_path, summary)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return evaluate_h3_smart_flow(load_config(config_path))
