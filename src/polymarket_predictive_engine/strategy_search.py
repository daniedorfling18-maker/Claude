from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import math
import random
import re
from typing import Any

from .config import EngineConfig, load_config
from .models.calibration_v2 import joined_feature_label_rows
from .utils import now_utc, parse_timestamp, read_csv_rows, safe_float, write_csv, write_json
from .worldcup_validation import classify_market_family


PRICE_BUCKETS = (0.0, 0.02, 0.05, 0.10, 0.20, 0.40, 0.60, 0.80, 0.95, 1.0)


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    return cfg.raw.get("edge_strategy_search", {}) or {}


def _price(row: dict[str, Any]) -> float | None:
    for key in ("executable_buy_price", "executable_price", "best_ask", "implied_probability", "midpoint"):
        value = safe_float(row.get(key))
        if value is not None and 0 < value < 1:
            return value
    return None


def _timestamp(row: dict[str, Any]) -> datetime:
    parsed = parse_timestamp(row.get("prediction_timestamp"))
    return parsed or datetime.min.replace(tzinfo=timezone.utc)


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


def _prepare_rows(cfg: EngineConfig) -> list[dict[str, Any]]:
    rows = joined_feature_label_rows(
        str(cfg.output_root / "polymarket_training" / "features_v2.csv"),
        str(cfg.output_root / "polymarket_training" / "labels.csv"),
    )
    label_outcomes = _label_outcome_index(cfg)
    prepared: list[dict[str, Any]] = []
    for row in rows:
        price = _price(row)
        target = safe_float(row.get("target"))
        market_id = str(row.get("market_id") or row.get("market_slug") or "")
        token_id = str(row.get("token_id") or "")
        if price is None or target is None or not market_id or not token_id:
            continue
        key = (market_id, token_id, str(row.get("prediction_timestamp") or ""))
        if not str(row.get("outcome") or "").strip() and key in label_outcomes:
            row = {**row, "outcome": label_outcomes[key]}
        target_int = 1 if int(target) == 1 else 0
        profit_per_usdc = (target_int / price) - 1.0 if target_int else -1.0
        prepared.append(
            {
                **row,
                "_price": price,
                "_target": target_int,
                "_profit_per_usdc": profit_per_usdc,
                "_market_key": market_id,
                "_timestamp": _timestamp(row),
                "_family": _market_family(row),
                "_outcome": _outcome(row),
            }
        )
    return prepared


def _split_markets(rows: list[dict[str, Any]], holdout_fraction: float) -> tuple[set[str], set[str]]:
    market_times: dict[str, datetime] = {}
    for row in rows:
        market = str(row["_market_key"])
        market_times[market] = max(market_times.get(market, datetime.min.replace(tzinfo=timezone.utc)), row["_timestamp"])
    markets = sorted(market_times, key=lambda market: market_times[market])
    if not markets:
        return set(), set()
    holdout_count = max(1, int(round(len(markets) * holdout_fraction)))
    holdout = set(markets[-holdout_count:])
    development = set(markets[:-holdout_count])
    return development, holdout


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


def _evaluate_rule(
    *,
    rule_family: str,
    rule_value: str,
    rule_rows: list[dict[str, Any]],
    development_markets: set[str],
    holdout_markets: set[str],
    stake_usdc: float,
    roi_bootstrap_samples: int = 2000,
) -> dict[str, Any]:
    dev_rows = [row for row in rule_rows if str(row["_market_key"]) in development_markets]
    holdout_rows = [row for row in rule_rows if str(row["_market_key"]) in holdout_markets]
    dev = _metrics(dev_rows, stake_usdc)
    holdout = _metrics(holdout_rows, stake_usdc)
    all_metrics = _metrics(rule_rows, stake_usdc)
    _, holdout_roi_ci_low, holdout_roi_ci_high = _market_clustered_roi_ci(
        holdout_rows, n_boot=roi_bootstrap_samples
    )
    return {
        "holdout_roi_ci_low": holdout_roi_ci_low,
        "holdout_roi_ci_high": holdout_roi_ci_high,
        "rule_family": rule_family,
        "rule_value": rule_value,
        "family": rule_rows[0].get("_family", "") if rule_rows else "",
        "outcome": rule_rows[0].get("_outcome", "") if rule_rows else "",
        "rows": all_metrics["rows"],
        "markets": all_metrics["markets"],
        "roi": all_metrics["roi"],
        "profit_usdc_per_1_stake": all_metrics["profit_usdc"] / stake_usdc if stake_usdc > 0 else 0.0,
        "win_rate": all_metrics["win_rate"],
        "avg_entry_price": all_metrics["avg_entry_price"],
        "dev_rows": dev["rows"],
        "dev_markets": dev["markets"],
        "dev_roi": dev["roi"],
        "dev_profit_usdc_per_1_stake": dev["profit_usdc"] / stake_usdc if stake_usdc > 0 else 0.0,
        "dev_win_rate": dev["win_rate"],
        "holdout_rows": holdout["rows"],
        "holdout_markets": holdout["markets"],
        "holdout_roi": holdout["roi"],
        "holdout_profit_usdc_per_1_stake": holdout["profit_usdc"] / stake_usdc if stake_usdc > 0 else 0.0,
        "holdout_win_rate": holdout["win_rate"],
    }


def run_edge_strategy_search(cfg: EngineConfig) -> dict[str, Any]:
    settings = _settings(cfg)
    if not settings.get("enabled", True):
        payload = {"status": "disabled", "generated_at_utc": now_utc()}
        write_json(cfg.governance_root / "edge_strategy_search_summary.json", payload)
        write_csv(cfg.governance_root / "edge_strategy_search.csv", [])
        return payload

    rows = _prepare_rows(cfg)
    holdout_fraction = float(settings.get("holdout_fraction", 0.25))
    development_markets, holdout_markets = _split_markets(rows, holdout_fraction)
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

    min_rows = int(settings.get("min_rows", 20))
    min_markets = int(settings.get("min_markets", 5))
    min_holdout_rows = int(settings.get("min_holdout_rows", 6))
    min_dev_roi = float(settings.get("min_dev_roi", 0.02))
    min_holdout_roi = float(settings.get("min_holdout_roi", 0.02))
    # Hardened gates: a rule must clear an entry-price floor (no untradeable sub-cent longshots),
    # have enough independent holdout markets, and show a holdout ROI whose market-clustered
    # bootstrap lower bound stays above the bar - so a single lucky tail hit cannot promote.
    min_holdout_markets = int(settings.get("min_holdout_markets", 4))
    min_avg_entry_price = float(settings.get("min_avg_entry_price", 0.05))
    min_holdout_roi_ci_lower_bound = float(settings.get("min_holdout_roi_ci_lower_bound", 0.0))
    roi_bootstrap_samples = int(settings.get("roi_bootstrap_samples", 2000))
    stake_usdc = float(settings.get("stake_usdc", 1.0))
    ranked: list[dict[str, Any]] = []
    for (rule_family, rule_value), rule_rows in by_rule.items():
        result = _evaluate_rule(
            rule_family=rule_family,
            rule_value=rule_value,
            rule_rows=rule_rows,
            development_markets=development_markets,
            holdout_markets=holdout_markets,
            stake_usdc=stake_usdc,
            roi_bootstrap_samples=roi_bootstrap_samples,
        )
        ci_low = float(result["holdout_roi_ci_low"])
        result["promotable"] = bool(
            int(result["dev_rows"]) >= min_rows
            and int(result["dev_markets"]) >= min_markets
            and int(result["holdout_rows"]) >= min_holdout_rows
            and int(result["holdout_markets"]) >= min_holdout_markets
            and float(result["avg_entry_price"]) >= min_avg_entry_price
            and float(result["dev_roi"]) >= min_dev_roi
            and float(result["holdout_roi"]) >= min_holdout_roi
            and math.isfinite(ci_low)
            and ci_low >= min_holdout_roi_ci_lower_bound
        )
        result["promotion_reason"] = (
            "clears significance (holdout ROI CI lower bound), entry-price floor, "
            "and independent-market gates"
            if result["promotable"]
            else (
                f"needs dev_rows>={min_rows}, dev_markets>={min_markets}, "
                f"holdout_rows>={min_holdout_rows}, holdout_markets>={min_holdout_markets}, "
                f"avg_entry_price>={min_avg_entry_price:.2f}, dev_roi>={min_dev_roi:.2%}, "
                f"holdout_roi>={min_holdout_roi:.2%}, "
                f"holdout_roi_ci_low>={min_holdout_roi_ci_lower_bound:.2%}"
            )
        )
        ranked.append(result)

    def _ci_low_key(row: dict[str, Any]) -> float:
        value = float(row.get("holdout_roi_ci_low", float("nan")))
        return value if math.isfinite(value) else -1e9

    ranked.sort(
        key=lambda row: (
            bool(row.get("promotable")),
            _ci_low_key(row),          # rank by the significance-adjusted ROI, not the point estimate
            float(row.get("holdout_roi") or 0.0),
            int(row.get("holdout_markets") or 0),
        ),
        reverse=True,
    )
    max_ranked = int(settings.get("max_ranked_rules", 100))
    ranked = ranked[:max_ranked]
    promoted = [row for row in ranked if row.get("promotable")]
    payload = {
        "status": "computed",
        "generated_at_utc": now_utc(),
        "joined_rows": len(rows),
        "markets": len({str(row["_market_key"]) for row in rows}),
        "development_markets": len(development_markets),
        "holdout_markets": len(holdout_markets),
        "ranked_rules": len(ranked),
        "promotable_rules": len(promoted),
        "top_rules": ranked[:10],
        "promoted_rules": promoted[:10],
        "settings": {
            "holdout_fraction": holdout_fraction,
            "min_rows": min_rows,
            "min_markets": min_markets,
            "min_holdout_rows": min_holdout_rows,
            "min_holdout_markets": min_holdout_markets,
            "min_avg_entry_price": min_avg_entry_price,
            "min_dev_roi": min_dev_roi,
            "min_holdout_roi": min_holdout_roi,
            "min_holdout_roi_ci_lower_bound": min_holdout_roi_ci_lower_bound,
            "roi_bootstrap_samples": roi_bootstrap_samples,
        },
    }
    write_json(cfg.governance_root / "edge_strategy_search_summary.json", payload)
    write_csv(cfg.governance_root / "edge_strategy_search.csv", ranked)
    return payload


def main(config_path: str) -> dict[str, Any]:
    return run_edge_strategy_search(load_config(config_path))
