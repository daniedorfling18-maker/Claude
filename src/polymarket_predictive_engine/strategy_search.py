from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
import math
import random
import re
from typing import Any

from polymarket_common.fees import resolve_taker_fee_schedule, taker_fee_per_share

from .config import EngineConfig, load_config
from .models.calibration_v2 import joined_feature_label_rows
from .utils import append_csv_rows, now_utc, parse_timestamp, read_csv_rows, safe_float, write_csv, write_json
from .worldcup_validation import classify_market_family

# WO-171 literals. Chronological three-way split by market: the earliest half trains, the next
# quarter selects, the last quarter is read only to confirm a rule chosen elsewhere. Basis: the
# registry's H3 protocol uses a 60/40 discovery/validation split; a third untouched segment is what
# separates "selected on the data" from "tested out of sample", and 25% of the 80-market mirror is
# 20 markets, above the 4-market floor.
SPLIT_FRACTIONS = (0.5, 0.25, 0.25)
BH_Q = 0.10  # the registry's false-discovery rate
HURDLE_BASIS = "half a typical 1-2c spread on a 50c contract (1-2%); the taker fee is already netted in every ROI"
AVAILABILITY_BASIS = "venue_resolution_time_else_close_time"
FINAL_EVIDENCE_CLASS = "retrospective"
FINAL_SEGMENT_NOTE = "a final-period read is confirmation of a rule selected elsewhere; it feeds no selection and no gate"
INFERENCE_NOTE = (
    "validation statistics are recomputed on every run over a growing corpus and the family-wise "
    "correction applies per run; each run is a further look; final-period reads are listed in the "
    "ledger and their count is final_period_reads"
)
FINAL_LEDGER_FIELDS = [
    "run_utc",
    "rule_family",
    "rule_value",
    "final_rows",
    "final_markets",
    "final_roi",
    "final_roi_ci_low",
    "final_roi_ci_high",
]


PRICE_BUCKETS = (0.0, 0.02, 0.05, 0.10, 0.20, 0.40, 0.60, 0.80, 0.95, 1.0)


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    return cfg.raw.get("edge_strategy_search", {}) or {}


def _price(row: dict[str, Any]) -> float | None:
    for key in ("executable_buy_price", "executable_price", "best_ask", "implied_probability", "midpoint"):
        value = safe_float(row.get(key))
        if value is not None and 0 < value < 1:
            return value
    return None


def _bucket(value: float, buckets: tuple[float, ...]) -> str:
    for lo, hi in zip(buckets[:-1], buckets[1:]):
        if lo <= value < hi or (hi == buckets[-1] and value <= hi):
            return f"{lo:.2f}-{hi:.2f}"
    return "unknown"


def _time_bucket(row: dict[str, Any]) -> str:
    hours = safe_float(row.get("time_to_close_hours"))
    if hours is None:
        hours = safe_float(row.get("hours_to_close"))
    if hours is None:
        return "unknown"
    if hours <= 1:
        return "0-1h"
    if hours <= 6:
        return "1-6h"
    if hours <= 24:
        return "6-24h"
    if hours <= 72:
        return "1-3d"
    if hours <= 168:
        return "3-7d"
    return "7d+"


def _market_family(row: dict[str, Any]) -> str:
    return classify_market_family(row)


def _outcome(row: dict[str, Any]) -> str:
    text = str(row.get("outcome") or "").strip().lower()
    if text:
        return text.replace(" ", "_")
    slug = str(row.get("market_slug") or "").lower()
    if "updown" in slug:
        return "updown_token"
    return "unknown"


def _rule_values(row: dict[str, Any]) -> dict[str, str]:
    price = _price(row)
    family = _market_family(row)
    outcome = _outcome(row)
    price_bucket = _bucket(price, PRICE_BUCKETS) if price is not None else "unknown"
    time_bucket = _time_bucket(row)
    return {
        "family": family,
        "family_price": f"{family}|price={price_bucket}",
        "family_time": f"{family}|time={time_bucket}",
        "family_price_time": f"{family}|price={price_bucket}|time={time_bucket}",
        "family_outcome": f"{family}|outcome={outcome}",
        "family_outcome_price": f"{family}|outcome={outcome}|price={price_bucket}",
    }


def _label_outcome_index(cfg: EngineConfig) -> dict[tuple[str, str, str], str]:
    index: dict[tuple[str, str, str], str] = {}
    for label in read_csv_rows(cfg.output_root / "polymarket_training" / "labels.csv"):
        if label.get("horizon", "all_valid") != "all_valid":
            continue
        key = (
            str(label.get("market_id") or ""),
            str(label.get("token_id") or ""),
            str(label.get("prediction_timestamp") or ""),
        )
        outcome = str(label.get("outcome") or "").strip()
        if all(key) and outcome:
            index[key] = outcome
    return index


def _fee_per_dollar(row: dict[str, Any], price: float) -> float:
    """WO-171: the canonical WO-94 taker fee at entry, per dollar staked.

    `taker_fee_per_share` is `rate x p x (1 - p)`; dividing by the entry price gives
    `rate x (1 - p)` per dollar. The schedule resolver reads the joined row's own fee
    metadata and applies its documented fallbacks, so a row with blank metadata is
    charged its category default rather than nothing."""
    if not 0 < price < 1:
        return 0.0
    return taker_fee_per_share(price=price, schedule=resolve_taker_fee_schedule(row)) / price


def _gross_profit_per_usdc(target: int, price: float) -> float:
    return (target / price) - 1.0 if target else -1.0


def _profit_at_fee_multiple(row: dict[str, Any], multiple: float = 1.0) -> float:
    """The row's return per dollar staked with the entry fee charged `multiple` times.

    The fee is paid whether the position wins or loses, so it is subtracted from both
    branches: a win at 0.5 with rate 0.05 returns 0.975, a loss -1.025."""
    return float(row["_gross_profit_per_usdc"]) - multiple * float(row["_fee_per_dollar"])


def _prepare_rows(cfg: EngineConfig) -> tuple[list[dict[str, Any]], int]:
    rows = joined_feature_label_rows(
        str(cfg.output_root / "polymarket_training" / "features_v2.csv"),
        str(cfg.output_root / "polymarket_training" / "labels.csv"),
    )
    label_outcomes = _label_outcome_index(cfg)
    prepared: list[dict[str, Any]] = []
    dropped_unparseable_timestamp = 0
    for row in rows:
        price = _price(row)
        target = safe_float(row.get("target"))
        market_id = str(row.get("market_id") or row.get("market_slug") or "")
        token_id = str(row.get("token_id") or "")
        if price is None or target is None or not market_id or not token_id:
            continue
        stamp = parse_timestamp(row.get("prediction_timestamp"))
        if stamp is None:
            # WO-171: a row with no usable clock cannot be placed in a chronological
            # segment, so it is dropped and counted rather than sorted to the epoch.
            dropped_unparseable_timestamp += 1
            continue
        key = (market_id, token_id, str(row.get("prediction_timestamp") or ""))
        if not str(row.get("outcome") or "").strip() and key in label_outcomes:
            row = {**row, "outcome": label_outcomes[key]}
        target_int = 1 if int(target) == 1 else 0
        fee = _fee_per_dollar(row, price)
        gross = _gross_profit_per_usdc(target_int, price)
        prepared.append(
            {
                **row,
                "_price": price,
                "_target": target_int,
                "_fee_per_dollar": fee,
                "_gross_profit_per_usdc": gross,
                "_profit_per_usdc": gross - fee,
                "_market_key": market_id,
                "_timestamp": stamp,
                "_family": _market_family(row),
                "_outcome": _outcome(row),
            }
        )
    return prepared, dropped_unparseable_timestamp


def _availability_index(cfg: EngineConfig) -> dict[str, datetime] | None:
    """WO-171: when each market's label became knowable, keyed exactly as the search keys markets.

    The venue's `resolution_time`, else its `close_time`. Both are earlier than the
    collector's observation time, so this purges FEWER train markets than an
    observation-time rule would; that residual is disclosed in A11. A missing
    `labels.csv` returns None, which the caller turns into `no_labels`."""
    path = cfg.output_root / "polymarket_training" / "labels.csv"
    if not Path(path).exists():
        return None
    resolutions: dict[str, datetime] = {}
    closes: dict[str, datetime] = {}
    for label in read_csv_rows(path):
        # Build-review finding: `or "all_valid"` mapped a BLANK horizon to all_valid, so a market
        # whose labels carry no horizon gained an availability time it had not earned and survived
        # the fail-closed drop. A missing key defaults; a present blank does not.
        if str(label.get("horizon", "all_valid")) != "all_valid":
            continue
        market = str(label.get("market_id") or label.get("market_slug") or "")
        if not market:
            continue
        resolved = parse_timestamp(label.get("resolution_time"))
        closed = parse_timestamp(label.get("close_time"))
        if resolved is not None:
            resolutions[market] = max(resolutions.get(market, resolved), resolved)
        if closed is not None:
            closes[market] = max(closes.get(market, closed), closed)
    # Build-review finding: the fallback is per MARKET, not per label row. Taking
    # `resolution_time or close_time` row by row let one row's close_time outvote another row's
    # resolution_time on the same market, producing an availability time the registered rule
    # never names. The maximum resolution_time wins for a market that has any.
    index = dict(closes)
    index.update(resolutions)
    return index


def _split_markets_three_way(
    rows: list[dict[str, Any]],
    availability: dict[str, datetime],
) -> tuple[set[str], set[str], set[str], dict[str, Any]]:
    """WO-171: train / validation / final by market, chronologically, then purged by availability.

    The purges run AFTER the split and remove markets from their segment without
    re-splitting, so the boundaries are a property of the calendar and not of which
    rules survive. A market whose label availability is unknown is dropped from every
    segment: fail-closed, because an unknown availability time cannot be shown to
    precede the next segment's first prediction."""
    market_times: dict[str, datetime] = {}
    market_first: dict[str, datetime] = {}
    for row in rows:
        market = str(row["_market_key"])
        stamp = row["_timestamp"]
        if market not in market_times or stamp > market_times[market]:
            market_times[market] = stamp
        if market not in market_first or stamp < market_first[market]:
            market_first[market] = stamp

    purged_no_availability_time = sorted(market for market in market_times if market not in availability)
    ordered = sorted((market for market in market_times if market in availability), key=lambda m: (market_times[m], m))
    n = len(ordered)
    train_count = int(round(SPLIT_FRACTIONS[0] * n))
    validation_count = int(round(SPLIT_FRACTIONS[1] * n))
    final_count = n - train_count - validation_count
    accounting: dict[str, Any] = {
        "markets_ordered": n,
        "purged_no_availability_time": len(purged_no_availability_time),
        "split_counts": {"train": train_count, "validation": validation_count, "final": final_count},
        "purged_train_overlaps_validation": 0,
        "purged_validation_overlaps_final": 0,
        "availability_basis": AVAILABILITY_BASIS,
    }
    if min(train_count, validation_count, final_count) < 1:
        return set(), set(), set(), accounting

    train = set(ordered[:train_count])
    validation = set(ordered[train_count:train_count + validation_count])
    final = set(ordered[train_count + validation_count:])

    # The embargo is the availability time itself: a train market whose label was knowable
    # only at or after the first validation prediction would have leaked into selection.
    validation_start = min(market_first[m] for m in validation)
    kept_train = {m for m in train if availability[m] < validation_start}
    accounting["purged_train_overlaps_validation"] = len(train) - len(kept_train)
    final_start = min(market_first[m] for m in final)
    kept_validation = {m for m in validation if availability[m] < final_start}
    accounting["purged_validation_overlaps_final"] = len(validation) - len(kept_validation)
    return kept_train, kept_validation, final, accounting


def _metrics(rows: list[dict[str, Any]], stake_usdc: float) -> dict[str, Any]:
    if not rows:
        return {
            "rows": 0,
            "markets": 0,
            "profit_usdc": 0.0,
            "roi": 0.0,
            "win_rate": 0.0,
            "avg_entry_price": 0.0,
        }
    profit = sum(float(row["_profit_per_usdc"]) * stake_usdc for row in rows)
    stake = len(rows) * stake_usdc
    return {
        "rows": len(rows),
        "markets": len({str(row["_market_key"]) for row in rows}),
        "profit_usdc": profit,
        "roi": profit / stake if stake > 0 else 0.0,
        "win_rate": sum(int(row["_target"]) for row in rows) / len(rows),
        "avg_entry_price": sum(float(row["_price"]) for row in rows) / len(rows),
    }


def _market_clustered_roi_ci(
    rows: list[dict[str, Any]], *, n_boot: int = 2000, seed: int = 20260625
) -> tuple[float, float, float]:
    """Bootstrap a 95% CI for a rule's ROI, resampling whole *markets* with replacement.

    Snapshots within one market are autocorrelated, so the independent unit is the market,
    not the row. A rule whose ROI is carried by one lucky longshot market has a CI lower
    bound far below zero - exactly the overfit signal the promotion gate must catch. Returns
    ``(point_roi, ci_low, ci_high)``; the CI is NaN when there are fewer than two markets.
    """
    if not rows:
        return 0.0, float("nan"), float("nan")
    point = sum(float(row["_profit_per_usdc"]) for row in rows) / len(rows)
    by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_market[str(row["_market_key"])].append(row)
    keys = list(by_market)
    if len(keys) < 2:
        return point, float("nan"), float("nan")
    rng = random.Random(seed)
    rois: list[float] = []
    for _ in range(max(1, n_boot)):
        profit = 0.0
        count = 0
        for _ in range(len(keys)):
            for row in by_market[keys[rng.randrange(len(keys))]]:
                profit += float(row["_profit_per_usdc"])
                count += 1
        rois.append(profit / count if count else 0.0)
    rois.sort()
    lo = rois[int(0.025 * (len(rois) - 1))]
    hi = rois[int(0.975 * (len(rois) - 1))]
    return point, lo, hi


def _bootstrap_rois(rows: list[dict[str, Any]], *, n_boot: int, seed: int) -> list[float]:
    """The market-cluster resampled ROIs, sorted. Shared by the interval and the p-value so
    both read the same draws."""
    by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_market[str(row["_market_key"])].append(row)
    keys = list(by_market)
    if len(keys) < 2:
        return []
    rng = random.Random(seed)
    rois: list[float] = []
    for _ in range(max(1, n_boot)):
        profit = 0.0
        count = 0
        for _ in range(len(keys)):
            for row in by_market[keys[rng.randrange(len(keys))]]:
                profit += float(row["_profit_per_usdc"])
                count += 1
        rois.append(profit / count if count else 0.0)
    rois.sort()
    return rois


def _bootstrap_p_value(rois: list[float], hurdle: float) -> float:
    """WO-171: one-sided against the hurdle, floored at one draw.

    The share of resamples at or below the hurdle. The floor is `1 / n_boot`: a
    bootstrap of n draws cannot resolve a tail finer than one draw, and reporting 0
    would claim certainty the resampling does not have. Consequence, stated: with
    n = 2,000 the floor 0.0005 exceeds `q / m` once `m > 200`, so a lone rule at the
    floor cannot be rejected in a family that large — conservative by construction."""
    if not rois:
        return 1.0
    at_or_below = sum(1 for roi in rois if roi <= hurdle)
    return max(at_or_below / len(rois), 1.0 / len(rois))


def _bh_significant(p_values: list[float], *, q: float = BH_Q) -> list[bool]:
    """Benjamini-Hochberg over the whole tested family.

    Sorted ascending, the largest `k` with `p_(k) <= k*q/m` rejects that rule and every
    smaller p-value. A non-finite p-value is 1.0 and still counts in `m`: a rule whose
    inference failed makes the correction stricter, never laxer."""
    cleaned = [value if math.isfinite(value) else 1.0 for value in p_values]
    m = len(cleaned)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: cleaned[i])
    cutoff = -1
    for rank, index in enumerate(order, start=1):
        if cleaned[index] <= rank * q / m:
            cutoff = rank
    significant = [False] * m
    for rank, index in enumerate(order, start=1):
        if rank <= cutoff:
            significant[index] = True
    return significant


def _turnover_rows_per_market_day(rows: list[dict[str, Any]]) -> float:
    """Rows per distinct (market, UTC date). The clock is `prediction_timestamp`."""
    if not rows:
        return 0.0
    market_days = {(str(row["_market_key"]), row["_timestamp"].date()) for row in rows}
    return len(rows) / len(market_days) if market_days else 0.0


def _max_drawdown_net_per_stake(rows: list[dict[str, Any]]) -> float:
    """Peak-to-trough of the cumulative fee-net profit per 1 USDC staked, in stake units.

    Rows are ordered by the clock, then by market and token so the order is total and
    reproducible. Returned as a non-positive number; 0.0 when nothing ever draws down."""
    if not rows:
        return 0.0
    ordered = sorted(rows, key=lambda row: (row["_timestamp"], str(row["_market_key"]), str(row.get("token_id") or "")))
    cumulative = 0.0
    peak = 0.0
    worst = 0.0
    for row in ordered:
        cumulative += float(row["_profit_per_usdc"])
        peak = max(peak, cumulative)
        worst = min(worst, cumulative - peak)
    return worst


def _roi_at_fee_multiple(rows: list[dict[str, Any]], multiple: float) -> float:
    if not rows:
        return 0.0
    return sum(_profit_at_fee_multiple(row, multiple) for row in rows) / len(rows)


def _evaluate_rule(
    *,
    rule_family: str,
    rule_value: str,
    rule_rows: list[dict[str, Any]],
    train_markets: set[str],
    validation_markets: set[str],
    final_markets: set[str],
    stake_usdc: float,
    roi_bootstrap_samples: int = 2000,
    hurdle: float = 0.02,
) -> dict[str, Any]:
    """WO-171: train and validation are measured for selection; the final segment is NOT read here.

    Build-review finding: evaluating the final segment for every variant published its ROI and
    clustered interval for every rule in both CSVs, which hands a downstream reader exactly the
    selection channel this work order exists to close, and it made the read ledger count only the
    promotable rules while every variant had in fact been read. `attach_final_segment` reads it,
    and only for a rule that is already promotable."""
    train_rows = [row for row in rule_rows if str(row["_market_key"]) in train_markets]
    validation_rows = [row for row in rule_rows if str(row["_market_key"]) in validation_markets]
    train = _metrics(train_rows, stake_usdc)
    validation = _metrics(validation_rows, stake_usdc)
    all_metrics = _metrics(rule_rows, stake_usdc)

    validation_rois = _bootstrap_rois(validation_rows, n_boot=roi_bootstrap_samples, seed=20260625)
    if validation_rois:
        low = validation_rois[int(0.025 * (len(validation_rois) - 1))]
        high = validation_rois[int(0.975 * (len(validation_rois) - 1))]
    else:
        low = high = float("nan")
    return {
        # Selection quantities.
        "holdout_roi_ci_low": low,
        "holdout_roi_ci_high": high,
        "validation_roi_ci_low": low,
        "validation_roi_ci_high": high,
        "validation_p_value": _bootstrap_p_value(validation_rois, hurdle),
        "rule_family": rule_family,
        "rule_value": rule_value,
        "family": rule_rows[0].get("_family", "") if rule_rows else "",
        "outcome": rule_rows[0].get("_outcome", "") if rule_rows else "",
        "rows": all_metrics["rows"],
        "markets": all_metrics["markets"],
        "roi": all_metrics["roi"],
        "profit_usdc_per_1_stake": all_metrics["profit_usdc"] / stake_usdc if stake_usdc > 0 else 0.0,
        "win_rate": all_metrics["win_rate"],
        # Display only: the entry-price floor is applied to the VALIDATION mean, so the final
        # segment never enters family membership.
        "avg_entry_price": all_metrics["avg_entry_price"],
        "validation_avg_entry_price": validation["avg_entry_price"],
        # One-release aliases: every consumer reads what it reads today.
        "dev_rows": train["rows"],
        "dev_markets": train["markets"],
        "dev_roi": train["roi"],
        "dev_profit_usdc_per_1_stake": train["profit_usdc"] / stake_usdc if stake_usdc > 0 else 0.0,
        "dev_win_rate": train["win_rate"],
        "holdout_rows": validation["rows"],
        "holdout_markets": validation["markets"],
        "holdout_roi": validation["roi"],
        "holdout_profit_usdc_per_1_stake": validation["profit_usdc"] / stake_usdc if stake_usdc > 0 else 0.0,
        "holdout_win_rate": validation["win_rate"],
        "train_rows": train["rows"],
        "train_markets": train["markets"],
        "train_roi": train["roi"],
        "train_profit_usdc_per_1_stake": train["profit_usdc"] / stake_usdc if stake_usdc > 0 else 0.0,
        "train_win_rate": train["win_rate"],
        "validation_rows": validation["rows"],
        "validation_markets": validation["markets"],
        "validation_roi": validation["roi"],
        "validation_profit_usdc_per_1_stake": validation["profit_usdc"] / stake_usdc if stake_usdc > 0 else 0.0,
        "validation_win_rate": validation["win_rate"],
        "turnover_rows_per_market_day": _turnover_rows_per_market_day(validation_rows),
        "max_drawdown_net_per_stake": _max_drawdown_net_per_stake(validation_rows),
        "cost_sensitivity_2x_validation_roi": _roi_at_fee_multiple(validation_rows, 2.0),
        # Confirmation only, and empty unless `attach_final_segment` fills them.
        "final_rows": "",
        "final_markets": "",
        "final_roi": "",
        "final_roi_ci_low": "",
        "final_roi_ci_high": "",
        "final_evidence_class": "",
        "final_segment_note": FINAL_SEGMENT_NOTE,
    }


def attach_final_segment(
    result: dict[str, Any],
    rule_rows: list[dict[str, Any]],
    final_markets: set[str],
    *,
    stake_usdc: float,
    roi_bootstrap_samples: int = 2000,
) -> None:
    """Read the untouched final segment for ONE already-selected rule, in place.

    Every call is a read of the final period, so every call is a row in the ledger. That is why
    this is separate from `_evaluate_rule`: the count in the artifact must be the number of reads
    that actually happened."""
    final_rows = [row for row in rule_rows if str(row["_market_key"]) in final_markets]
    final = _metrics(final_rows, stake_usdc)
    _, ci_low, ci_high = _market_clustered_roi_ci(final_rows, n_boot=roi_bootstrap_samples)
    result.update(
        {
            "final_rows": final["rows"],
            "final_markets": final["markets"],
            "final_roi": final["roi"],
            "final_roi_ci_low": ci_low,
            "final_roi_ci_high": ci_high,
            "final_evidence_class": FINAL_EVIDENCE_CLASS,
            "final_segment_note": FINAL_SEGMENT_NOTE,
        }
    )


def _setting(settings: dict[str, Any], new_key: str, old_key: str, default: Any) -> Any:
    """WO-171: the new key wins when both exist; the VPS runtime config's old keys keep working."""
    if new_key in settings:
        return settings[new_key]
    if old_key in settings:
        return settings[old_key]
    return default


def _empty_payload(cfg: EngineConfig, status: str, **extra: Any) -> dict[str, Any]:
    """Every fail branch writes both CSVs empty, so no stale file outlives a failed run."""
    payload = {"status": status, "generated_at_utc": now_utc(), **extra}
    write_json(cfg.governance_root / "edge_strategy_search_summary.json", payload)
    write_csv(cfg.governance_root / "edge_strategy_search.csv", [])
    write_csv(cfg.governance_root / "edge_strategy_search_family.csv", [])
    return payload


def run_edge_strategy_search(cfg: EngineConfig) -> dict[str, Any]:
    """WO-171: a data-relative replay of recorded rows, clocked by `prediction_timestamp`.

    Nothing here reads a wall clock for ordering: every segment boundary, purge, turnover figure
    and drawdown ordering comes from the rows' own recorded timestamps, so the same corpus gives
    the same split on any day it is run (S1)."""
    settings = _settings(cfg)
    if not settings.get("enabled", True):
        return _empty_payload(cfg, "disabled")

    rows, dropped_unparseable_timestamp = _prepare_rows(cfg)
    availability = _availability_index(cfg)
    if availability is None:
        return _empty_payload(cfg, "no_labels", dropped_unparseable_timestamp=dropped_unparseable_timestamp)

    train_markets, validation_markets, final_markets, split_accounting = _split_markets_three_way(rows, availability)
    split_accounting["dropped_unparseable_timestamp"] = dropped_unparseable_timestamp
    if not train_markets or not validation_markets or not final_markets:
        return _empty_payload(cfg, "insufficient_markets", split=split_accounting)

    allowed_rule_families = set(settings.get("allowed_rule_families") or [])
    if not allowed_rule_families:
        allowed_rule_families = {
            "family",
            "family_price",
            "family_time",
            "family_price_time",
            "family_outcome",
            "family_outcome_price",
        }
    by_rule: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        for rule_family, rule_value in _rule_values(row).items():
            if rule_family in allowed_rule_families:
                by_rule[(rule_family, rule_value)].append(row)

    min_rows = int(_setting(settings, "min_train_rows", "min_rows", 20))
    min_markets = int(_setting(settings, "min_train_markets", "min_markets", 5))
    min_train_roi = float(_setting(settings, "min_train_roi", "min_dev_roi", 0.02))
    min_validation_rows = int(_setting(settings, "min_validation_rows", "min_holdout_rows", 6))
    min_validation_markets = int(_setting(settings, "min_validation_markets", "min_holdout_markets", 4))
    min_validation_roi = float(_setting(settings, "min_validation_roi", "min_holdout_roi", 0.02))
    min_validation_roi_ci_lower_bound = float(
        _setting(settings, "min_validation_roi_ci_lower_bound", "min_holdout_roi_ci_lower_bound", 0.0)
    )
    min_avg_entry_price = float(settings.get("min_avg_entry_price", 0.05))
    roi_bootstrap_samples = int(settings.get("roi_bootstrap_samples", 2000))
    stake_usdc = float(settings.get("stake_usdc", 1.0))
    ignored_keys = [key for key in ("holdout_fraction",) if key in settings]

    evaluated: list[dict[str, Any]] = []
    rows_by_rule: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for (rule_family, rule_value), rule_rows in by_rule.items():
        rows_by_rule[(rule_family, rule_value)] = rule_rows
        result = _evaluate_rule(
            rule_family=rule_family,
            rule_value=rule_value,
            rule_rows=rule_rows,
            train_markets=train_markets,
            validation_markets=validation_markets,
            final_markets=final_markets,
            stake_usdc=stake_usdc,
            roi_bootstrap_samples=roi_bootstrap_samples,
            hurdle=min_validation_roi,
        )
        # Family membership is a SAMPLE condition only: never a ROI or an interval, so
        # membership cannot shrink on outcomes and `m` cannot be gamed by the results.
        result["in_tested_family"] = bool(
            int(result["validation_rows"]) >= min_validation_rows
            and int(result["validation_markets"]) >= min_validation_markets
            and float(result["validation_avg_entry_price"]) >= min_avg_entry_price
        )
        evaluated.append(result)

    tested = [result for result in evaluated if result["in_tested_family"]]
    flags = _bh_significant([float(result["validation_p_value"]) for result in tested], q=BH_Q)
    for result, significant in zip(tested, flags):
        result["bh_significant"] = bool(significant)
    for result in evaluated:
        if not result["in_tested_family"]:
            result["bh_significant"] = False
            result["family_status"] = "untested_insufficient_sample"
        else:
            result["family_status"] = "tested"

    benchmark_rows = [row for row in rows if str(row["_market_key"]) in validation_markets]
    benchmark = _metrics(benchmark_rows, stake_usdc)
    _, benchmark_ci_low, benchmark_ci_high = _market_clustered_roi_ci(benchmark_rows, n_boot=roi_bootstrap_samples)

    for result in evaluated:
        ci_low = float(result["validation_roi_ci_low"])
        result["benchmark_buy_all_roi"] = benchmark["roi"]
        result["excess_over_buy_all"] = float(result["validation_roi"]) - float(benchmark["roi"])
        result["promotable"] = bool(
            int(result["train_rows"]) >= min_rows
            and int(result["train_markets"]) >= min_markets
            and int(result["validation_rows"]) >= min_validation_rows
            and int(result["validation_markets"]) >= min_validation_markets
            and float(result["validation_avg_entry_price"]) >= min_avg_entry_price
            and float(result["train_roi"]) >= min_train_roi
            and float(result["validation_roi"]) >= min_validation_roi
            and math.isfinite(ci_low)
            and ci_low >= min_validation_roi_ci_lower_bound
            and bool(result["bh_significant"])
        )
        result["promotion_reason"] = (
            "clears the train floors, the validation gates (ROI, clustered interval lower bound, "
            "entry-price floor, independent markets) and the Benjamini-Hochberg correction over the "
            "whole tested family"
            if result["promotable"]
            else (
                f"needs train_rows>={min_rows}, train_markets>={min_markets}, "
                f"validation_rows>={min_validation_rows}, validation_markets>={min_validation_markets}, "
                f"validation_avg_entry_price>={min_avg_entry_price:.2f}, train_roi>={min_train_roi:.2%}, "
                f"validation_roi>={min_validation_roi:.2%}, "
                f"validation_roi_ci_low>={min_validation_roi_ci_lower_bound:.2%}, "
                f"and BH significance at q={BH_Q:.2f} over the tested family"
            )
        )

    def _ci_low_key(row: dict[str, Any]) -> float:
        value = float(row.get("validation_roi_ci_low", float("nan")))
        return value if math.isfinite(value) else -1e9

    # WO-171: ranking reads validation only. No final-segment key appears in this key.
    ranked = sorted(
        evaluated,
        key=lambda row: (
            bool(row.get("promotable")),
            _ci_low_key(row),
            float(row.get("validation_roi") or 0.0),
            int(row.get("validation_markets") or 0),
        ),
        reverse=True,
    )
    family = list(ranked)
    family_size = len(family)
    family_tested_size = len(tested)

    max_ranked = int(settings.get("max_ranked_rules", 100))
    ranked = ranked[:max_ranked]
    promoted = [row for row in ranked if row.get("promotable")]

    # The final period is read here and nowhere else: once per promotable rule, after selection is
    # complete. Every read is a ledger row, so `final_period_reads` is the number that happened.
    for result in promoted:
        attach_final_segment(
            result,
            rows_by_rule[(result["rule_family"], result["rule_value"])],
            final_markets,
            stake_usdc=stake_usdc,
            roi_bootstrap_samples=roi_bootstrap_samples,
        )

    # The complete tested-variant registry is written BEFORE any truncation, so
    # `max_ranked_rules` can never hide a variant that was evaluated. It is written after the
    # final reads so a promotable row carries them; a non-promotable row's final columns are "".
    write_csv(cfg.governance_root / "edge_strategy_search_family.csv", family)

    generated_at = now_utc()
    ledger_path = cfg.governance_root / "edge_strategy_search_final_period_ledger.csv"
    ledger_status = "ok"
    final_period_reads: int | None = None
    ledger_rows = [
        {
            "run_utc": generated_at,
            "rule_family": row["rule_family"],
            "rule_value": row["rule_value"],
            "final_rows": row["final_rows"],
            "final_markets": row["final_markets"],
            "final_roi": row["final_roi"],
            "final_roi_ci_low": row["final_roi_ci_low"],
            "final_roi_ci_high": row["final_roi_ci_high"],
        }
        for row in promoted
    ]
    try:
        append_csv_rows(ledger_path, ledger_rows, fieldnames=FINAL_LEDGER_FIELDS)
        final_period_reads = max(len(read_csv_rows(ledger_path)), 0)
    except ValueError:
        ledger_status = "schema_mismatch"
        final_period_reads = None

    payload = {
        "status": "computed",
        "generated_at_utc": generated_at,
        "joined_rows": len(rows),
        "markets": len({str(row["_market_key"]) for row in rows}),
        "split": split_accounting,
        "train_markets": len(train_markets),
        "validation_markets": len(validation_markets),
        "final_markets": len(final_markets),
        # One-release aliases for the two-way vocabulary.
        "development_markets": len(train_markets),
        "holdout_markets": len(validation_markets),
        "ranked_rules": len(ranked),
        "promotable_rules": len(promoted),
        "family_size": family_size,
        "family_tested_size": family_tested_size,
        "bh_rejections": sum(1 for row in tested if row.get("bh_significant")),
        "benchmark_buy_all": {
            "rows": benchmark["rows"],
            "markets": benchmark["markets"],
            "validation_roi": benchmark["roi"],
            "validation_roi_ci_low": benchmark_ci_low,
            "validation_roi_ci_high": benchmark_ci_high,
        },
        "hurdle": min_validation_roi,
        "hurdle_basis": HURDLE_BASIS,
        "availability_basis": AVAILABILITY_BASIS,
        "final_evidence_class": FINAL_EVIDENCE_CLASS,
        "final_segment_note": FINAL_SEGMENT_NOTE,
        "inference_note": INFERENCE_NOTE,
        "ledger_status": ledger_status,
        "final_period_reads": final_period_reads,
        "top_rules": ranked[:10],
        "promoted_rules": promoted[:10],
        "settings": {
            "split_fractions": list(SPLIT_FRACTIONS),
            "bh_q": BH_Q,
            "min_train_rows": min_rows,
            "min_train_markets": min_markets,
            "min_validation_rows": min_validation_rows,
            "min_validation_markets": min_validation_markets,
            "min_avg_entry_price": min_avg_entry_price,
            "min_train_roi": min_train_roi,
            "min_validation_roi": min_validation_roi,
            "min_validation_roi_ci_lower_bound": min_validation_roi_ci_lower_bound,
            "roi_bootstrap_samples": roi_bootstrap_samples,
            "ignored_keys": ignored_keys,
            # One-release aliases so a reader pinned to the old names still resolves them.
            "min_rows": min_rows,
            "min_markets": min_markets,
            "min_holdout_rows": min_validation_rows,
            "min_holdout_markets": min_validation_markets,
            "min_dev_roi": min_train_roi,
            "min_holdout_roi": min_validation_roi,
            "min_holdout_roi_ci_lower_bound": min_validation_roi_ci_lower_bound,
        },
    }
    write_json(cfg.governance_root / "edge_strategy_search_summary.json", payload)
    write_csv(cfg.governance_root / "edge_strategy_search.csv", ranked)
    return payload


def main(config_path: str) -> dict[str, Any]:
    return run_edge_strategy_search(load_config(config_path))
