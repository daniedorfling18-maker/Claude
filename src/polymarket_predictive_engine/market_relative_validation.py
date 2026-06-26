from __future__ import annotations

import math
import random
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from .config import EngineConfig, load_config
from .utils import now_utc, parse_timestamp, read_csv_rows, safe_float, write_csv, write_json

EPS = 1e-12
LEAKAGE_FIELD_HINTS = (
    "target",
    "label",
    "outcome",
    "resolution",
    "resolved",
    "winner",
    "winning",
    "settled",
    "settlement",
    "payout",
)
MODEL_PROBABILITY_FIELDS = (
    "calibrated_probability",
    "model_probability",
    "raw_probability",
    "predicted_probability",
)
MARKET_MIDPOINT_FIELDS = (
    "market_midpoint",
    "midpoint",
    "implied_probability",
    "market_probability",
    "market_price",
)
YES_EXECUTABLE_PRICE_FIELDS = (
    "executable_price",
    "best_ask",
    "ask",
    "ask_price",
)
NO_EXECUTABLE_PRICE_FIELDS = (
    "executable_no_price",
    "no_executable_price",
    "no_best_ask",
)
DATE_FIELDS = (
    "fixture_date",
    "event_start_time",
    "market_date",
    "close_time",
    "resolution_time",
    "prediction_timestamp",
)
UNIT_FIELDS = (
    "fixture_id",
    "event_id",
    "market_id",
    "condition_id",
    "market_slug",
)


def _clip_probability(value: float) -> float:
    if not math.isfinite(value):
        return 0.5
    return max(EPS, min(1 - EPS, value))


def _is_probability(value: float | None) -> bool:
    return value is not None and math.isfinite(value) and 0.0 <= value <= 1.0


def leakage_columns(columns: Iterable[str]) -> list[str]:
    """Columns that must never be used as model features.

    These names either carry the realised result directly or are close enough to
    target/outcome/resolution state that an actuarial validation pass should fail
    closed rather than risk label leakage.
    """
    bad: list[str] = []
    for column in columns:
        lower = str(column).strip().lower()
        if any(hint in lower for hint in LEAKAGE_FIELD_HINTS):
            bad.append(str(column))
    return bad


def assert_no_leakage_columns(columns: Iterable[str]) -> None:
    bad = leakage_columns(columns)
    if bad:
        raise ValueError("Leakage columns are not permitted as model inputs: " + ", ".join(sorted(set(bad))))


def _first_probability(row: Mapping[str, Any], fields: Sequence[str]) -> tuple[float | None, str]:
    lower_to_key = {str(k).lower(): str(k) for k in row.keys()}
    for field in fields:
        key = lower_to_key.get(field.lower())
        if key is None:
            continue
        value = safe_float(row.get(key))
        if _is_probability(value):
            return _clip_probability(float(value)), key
    return None, ""


def _first_float(row: Mapping[str, Any], fields: Sequence[str]) -> tuple[float | None, str]:
    lower_to_key = {str(k).lower(): str(k) for k in row.keys()}
    for field in fields:
        key = lower_to_key.get(field.lower())
        if key is None:
            continue
        value = safe_float(row.get(key))
        if value is not None and math.isfinite(value):
            return float(value), key
    return None, ""


def _label_target(row: Mapping[str, Any]) -> int | None:
    if row.get("resolution_quality") != "clean_settlement":
        return None
    raw = str(row.get("target", "")).strip().lower()
    if raw in {"1", "true", "yes"}:
        return 1
    if raw in {"0", "false", "no"}:
        return 0
    return None


def _label_index(label_rows: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str, str], Mapping[str, Any]]:
    index: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for row in label_rows:
        if row.get("horizon", "all_valid") not in {"", "all_valid"}:
            continue
        target = _label_target(row)
        if target is None:
            continue
        key = (str(row.get("market_id", "")), str(row.get("token_id", "")), str(row.get("prediction_timestamp", "")))
        if all(key):
            index[key] = row
    return index


def _unit_key(row: Mapping[str, Any]) -> str:
    for field in UNIT_FIELDS:
        value = str(row.get(field, "")).strip()
        if value:
            return value
    return f"{row.get('market_id', '')}:{row.get('token_id', '')}"


def _unit_date(row: Mapping[str, Any]) -> datetime:
    for field in DATE_FIELDS:
        parsed = parse_timestamp(row.get(field))
        if parsed is not None:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return datetime.max.replace(tzinfo=timezone.utc)


def join_clean_settled_predictions(
    prediction_rows: Iterable[Mapping[str, Any]],
    label_rows: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Join prediction rows to clean settled labels without using label columns as features."""
    labels = _label_index(label_rows)
    joined: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for pred in prediction_rows:
        key = (str(pred.get("market_id", "")), str(pred.get("token_id", "")), str(pred.get("prediction_timestamp", "")))
        label = labels.get(key)
        if not label:
            rejected.append({"market_id": key[0], "token_id": key[1], "prediction_timestamp": key[2], "reason": "no clean settled label"})
            continue

        model_p, model_source = _first_probability(pred, MODEL_PROBABILITY_FIELDS)
        market_p, market_source = _first_probability(pred, MARKET_MIDPOINT_FIELDS)
        if model_p is None or market_p is None:
            rejected.append({"market_id": key[0], "token_id": key[1], "prediction_timestamp": key[2], "reason": "missing model or market probability"})
            continue
        if model_source == market_source:
            rejected.append({"market_id": key[0], "token_id": key[1], "prediction_timestamp": key[2], "reason": "model probability source is the same as market midpoint source"})
            continue

        target = _label_target(label)
        if target is None:
            rejected.append({"market_id": key[0], "token_id": key[1], "prediction_timestamp": key[2], "reason": "label is not a clean binary settlement"})
            continue

        spread, spread_source = _first_float(pred, ("spread", "bid_ask_spread", "market_spread"))
        best_bid, _ = _first_float(pred, ("best_bid", "bid", "bid_price"))
        best_ask, _ = _first_float(pred, YES_EXECUTABLE_PRICE_FIELDS)
        if spread is None and best_bid is not None and best_ask is not None:
            spread = max(0.0, best_ask - best_bid)
            spread_source = "best_ask_minus_best_bid"
        if spread is None:
            spread = 0.0
            spread_source = "missing_default_0"

        yes_price, yes_source = _first_probability(pred, YES_EXECUTABLE_PRICE_FIELDS)
        if yes_price is None:
            yes_price = _clip_probability(market_p + max(0.0, spread) / 2)
            yes_source = "midpoint_plus_half_spread"

        no_price, no_source = _first_probability(pred, NO_EXECUTABLE_PRICE_FIELDS)
        if no_price is None:
            if best_bid is not None and _is_probability(best_bid):
                no_price = _clip_probability(1.0 - best_bid)
                no_source = "one_minus_best_bid"
            else:
                no_price = _clip_probability((1.0 - market_p) + max(0.0, spread) / 2)
                no_source = "one_minus_midpoint_plus_half_spread"

        liquidity, liquidity_source = _first_float(pred, ("liquidity", "book_liquidity", "market_liquidity", "volume"))
        if liquidity is None or liquidity < 0:
            liquidity = 0.0
            liquidity_source = "missing_default_0"

        merged = {
            **{str(k): v for k, v in pred.items()},
            "target": target,
            "resolution_quality": "clean_settlement",
            "model_probability": model_p,
            "market_probability": market_p,
            "model_probability_source": model_source,
            "market_probability_source": market_source,
            "yes_executable_price": yes_price,
            "yes_executable_price_source": yes_source,
            "no_executable_price": no_price,
            "no_executable_price_source": no_source,
            "spread": max(0.0, float(spread)),
            "spread_source": spread_source,
            "liquidity": float(liquidity),
            "liquidity_source": liquidity_source,
            "validation_unit_id": _unit_key({**pred, **label}),
            "validation_unit_date": _unit_date({**pred, **label}).isoformat(),
        }
        joined.append(merged)

    return joined, rejected


def brier_score(rows: Sequence[Mapping[str, Any]], probability_field: str) -> float:
    if not rows:
        return float("nan")
    return sum((_clip_probability(float(r[probability_field])) - int(r["target"])) ** 2 for r in rows) / len(rows)


def log_loss(rows: Sequence[Mapping[str, Any]], probability_field: str) -> float:
    if not rows:
        return float("nan")
    total = 0.0
    for row in rows:
        p = _clip_probability(float(row[probability_field]))
        y = int(row["target"])
        total += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    return total / len(rows)


def calibration_by_decile(rows: Sequence[Mapping[str, Any]], probability_field: str, forecaster: str, bucket_count: int = 10) -> list[dict[str, Any]]:
    buckets: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        p = _clip_probability(float(row[probability_field]))
        bucket = min(bucket_count - 1, max(0, int(p * bucket_count)))
        buckets[bucket].append(row)

    out: list[dict[str, Any]] = []
    for bucket in range(bucket_count):
        vals = buckets.get(bucket, [])
        lo = bucket / bucket_count
        hi = (bucket + 1) / bucket_count
        if vals:
            mean_pred = sum(float(v[probability_field]) for v in vals) / len(vals)
            realized = sum(int(v["target"]) for v in vals) / len(vals)
            abs_error: float | str = abs(mean_pred - realized)
        else:
            mean_pred = ""
            realized = ""
            abs_error = ""
        out.append({
            "forecaster": forecaster,
            "decile": bucket + 1,
            "probability_min": lo,
            "probability_max": hi,
            "count": len(vals),
            "mean_probability": mean_pred,
            "realized_rate": realized,
            "absolute_calibration_error": abs_error,
        })
    return out


def expected_calibration_error(decile_rows: Sequence[Mapping[str, Any]]) -> float:
    total = sum(int(r.get("count", 0)) for r in decile_rows)
    if total <= 0:
        return float("nan")
    ece = 0.0
    for row in decile_rows:
        count = int(row.get("count", 0))
        err = row.get("absolute_calibration_error", "")
        if count and err != "":
            ece += (count / total) * float(err)
    return ece


def brier_decomposition(rows: Sequence[Mapping[str, Any]], probability_field: str, forecaster: str, bucket_count: int = 10) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not rows:
        return {"forecaster": forecaster, "brier_score": float("nan"), "uncertainty": float("nan"), "resolution": float("nan"), "reliability": float("nan")}, []
    base_rate = sum(int(r["target"]) for r in rows) / len(rows)
    deciles = calibration_by_decile(rows, probability_field, forecaster, bucket_count=bucket_count)
    reliability = 0.0
    resolution = 0.0
    detail_rows: list[dict[str, Any]] = []
    for row in deciles:
        count = int(row["count"])
        if not count:
            continue
        weight = count / len(rows)
        mean_pred = float(row["mean_probability"])
        realized = float(row["realized_rate"])
        rel_component = weight * (mean_pred - realized) ** 2
        res_component = weight * (realized - base_rate) ** 2
        reliability += rel_component
        resolution += res_component
        detail_rows.append({**row, "base_rate": base_rate, "reliability_component": rel_component, "resolution_component": res_component})
    uncertainty = base_rate * (1 - base_rate)
    return {
        "forecaster": forecaster,
        "brier_score": brier_score(rows, probability_field),
        "base_rate": base_rate,
        "uncertainty": uncertainty,
        "resolution": resolution,
        "reliability": reliability,
        "decomposed_brier_score": uncertainty - resolution + reliability,
    }, detail_rows


def walk_forward_market_splits(
    rows: Sequence[Mapping[str, Any]],
    *,
    min_train_markets: int = 5,
    n_splits: int = 5,
) -> list[dict[str, Any]]:
    unit_dates: dict[str, datetime] = {}
    for row in rows:
        unit = str(row.get("validation_unit_id") or _unit_key(row))
        dt = _unit_date(row)
        if unit not in unit_dates or dt < unit_dates[unit]:
            unit_dates[unit] = dt
    ordered_units = [unit for unit, _ in sorted(unit_dates.items(), key=lambda kv: (kv[1], kv[0]))]
    if len(ordered_units) <= min_train_markets:
        return []
    remaining = len(ordered_units) - min_train_markets
    window = max(1, math.ceil(remaining / max(1, n_splits)))
    splits: list[dict[str, Any]] = []
    fold = 1
    for start in range(min_train_markets, len(ordered_units), window):
        train_units = set(ordered_units[:start])
        test_units = set(ordered_units[start:start + window])
        if not test_units:
            continue
        splits.append({"fold": fold, "train_units": train_units, "test_units": test_units})
        fold += 1
    return splits


def _bootstrap_brier_gain_ci(rows: Sequence[Mapping[str, Any]], n_boot: int = 1000, seed: int = 20260624) -> tuple[float, float, float]:
    if not rows:
        return float("nan"), float("nan"), float("nan")
    by_unit: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_unit[str(row.get("validation_unit_id") or _unit_key(row))].append(row)
    units = list(by_unit.keys())
    gains = [
        (float(r["market_probability"]) - int(r["target"])) ** 2 - (float(r["model_probability"]) - int(r["target"])) ** 2
        for r in rows
    ]
    point = sum(gains) / len(gains)
    if len(units) < 2:
        return point, float("nan"), float("nan")
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(max(1, n_boot)):
        total = 0.0
        count = 0
        for _ in range(len(units)):
            unit_rows = by_unit[units[rng.randrange(len(units))]]
            for row in unit_rows:
                total += (float(row["market_probability"]) - int(row["target"])) ** 2 - (float(row["model_probability"]) - int(row["target"])) ** 2
                count += 1
        samples.append(total / count if count else 0.0)
    samples.sort()
    lo = samples[int(0.025 * (len(samples) - 1))]
    hi = samples[int(0.975 * (len(samples) - 1))]
    return point, lo, hi


def _metric_summary(rows: Sequence[Mapping[str, Any]], bucket_count: int = 10) -> dict[str, Any]:
    model_deciles = calibration_by_decile(rows, "model_probability", "model", bucket_count=bucket_count)
    market_deciles = calibration_by_decile(rows, "market_probability", "market", bucket_count=bucket_count)
    model_brier = brier_score(rows, "model_probability")
    market_brier = brier_score(rows, "market_probability")
    model_log_loss = log_loss(rows, "model_probability")
    market_log_loss = log_loss(rows, "market_probability")
    return {
        "sample_size": len(rows),
        "markets": len({str(r.get("validation_unit_id") or _unit_key(r)) for r in rows}),
        "model_brier_score": model_brier,
        "market_brier_score": market_brier,
        "brier_improvement_vs_market": market_brier - model_brier,
        "brier_skill_vs_market": 1 - model_brier / market_brier if market_brier and math.isfinite(market_brier) else float("nan"),
        "model_log_loss": model_log_loss,
        "market_log_loss": market_log_loss,
        "log_loss_improvement_vs_market": market_log_loss - model_log_loss,
        "log_loss_skill_vs_market": 1 - model_log_loss / market_log_loss if market_log_loss and math.isfinite(market_log_loss) else float("nan"),
        "model_calibration_error": expected_calibration_error(model_deciles),
        "market_calibration_error": expected_calibration_error(market_deciles),
        "calibration_error_improvement_vs_market": expected_calibration_error(market_deciles) - expected_calibration_error(model_deciles),
    }


def simulate_roi(
    rows: Sequence[Mapping[str, Any]],
    *,
    bankroll: float,
    minimum_edge: float,
    slippage: float,
    transaction_cost: float,
    liquidity_cap_fraction: float,
    maximum_single_market_exposure: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    trades: list[dict[str, Any]] = []
    for row in rows:
        model_p = _clip_probability(float(row["model_probability"]))
        target = int(row["target"])
        spread = max(0.0, float(row.get("spread") or 0.0))
        liquidity = max(0.0, float(row.get("liquidity") or 0.0))
        yes_price = _clip_probability(float(row.get("yes_executable_price") or row["market_probability"]))
        no_price = _clip_probability(float(row.get("no_executable_price") or (1 - float(row["market_probability"]))))
        yes_net_price = _clip_probability(yes_price + slippage + transaction_cost)
        no_net_price = _clip_probability(no_price + slippage + transaction_cost)
        yes_edge = model_p - yes_net_price
        no_edge = (1.0 - model_p) - no_net_price
        side = ""
        price = 0.0
        payoff = 0.0
        edge = 0.0
        if yes_edge >= minimum_edge and yes_edge >= no_edge:
            side, price, payoff, edge = "BUY_YES", yes_net_price, float(target), yes_edge
        elif no_edge >= minimum_edge:
            side, price, payoff, edge = "BUY_NO", no_net_price, float(1 - target), no_edge
        else:
            continue
        max_exposure_amount = max(0.0, bankroll * maximum_single_market_exposure)
        liquidity_cap = max(0.0, liquidity * liquidity_cap_fraction)
        spend = min(max_exposure_amount, liquidity_cap if liquidity_cap > 0 else max_exposure_amount)
        if spend <= 0 or price <= 0:
            continue
        quantity = spend / price
        pnl = quantity * (payoff - price)
        trades.append({
            "market_id": row.get("market_id", ""),
            "token_id": row.get("token_id", ""),
            "prediction_timestamp": row.get("prediction_timestamp", ""),
            "side": side,
            "model_probability": model_p,
            "market_probability": row.get("market_probability", ""),
            "raw_spread": spread,
            "slippage_assumption": slippage,
            "transaction_cost_assumption": transaction_cost,
            "net_executable_price": price,
            "edge_after_costs": edge,
            "liquidity": liquidity,
            "liquidity_cap_fraction": liquidity_cap_fraction,
            "max_exposure_amount": max_exposure_amount,
            "simulated_spend": spend,
            "simulated_quantity": quantity,
            "target": target,
            "pnl": pnl,
        })
    staked = sum(float(t["simulated_spend"]) for t in trades)
    pnl = sum(float(t["pnl"]) for t in trades)
    summary = {
        "n_trades": len(trades),
        "staked": staked,
        "pnl": pnl,
        "roi": pnl / staked if staked > 0 else None,
        "bankroll": bankroll,
        "minimum_edge": minimum_edge,
        "slippage": slippage,
        "transaction_cost": transaction_cost,
        "liquidity_cap_fraction": liquidity_cap_fraction,
        "maximum_single_market_exposure": maximum_single_market_exposure,
    }
    return summary, trades


def validate_market_relative_model(cfg: EngineConfig) -> dict[str, Any]:
    thresholds = cfg.raw.get("governance_thresholds", {})
    settings = cfg.raw.get("market_relative_validation", {})
    bucket_count = int(settings.get("bucket_count", cfg.raw.get("calibration", {}).get("buckets", 10)))
    min_rows = int(settings.get("min_rows", thresholds.get("min_validation_rows", 100)))
    min_markets = int(settings.get("min_markets", thresholds.get("min_validation_markets", max(20, min_rows // 5))))
    min_train_markets = int(settings.get("min_train_markets", min(5, max(1, min_markets // 2))))
    n_splits = int(settings.get("walk_forward_splits", 5))
    min_ci_gain = float(settings.get("min_brier_gain_ci_low", thresholds.get("min_brier_gain_ci_low", 0.0)))
    copy_tolerance = float(settings.get("copy_tolerance", 1e-9))
    n_boot = int(settings.get("bootstrap_iterations", 1000))

    model_feature_columns = settings.get("model_feature_columns", [])
    if model_feature_columns:
        assert_no_leakage_columns(model_feature_columns)

    out = cfg.output_root / "polymarket_model_validation"
    prediction_rows = read_csv_rows(out.parent / "polymarket_predictions" / "predictions.csv")
    label_rows = read_csv_rows(out.parent / "polymarket_training" / "labels.csv")
    joined, rejected = join_clean_settled_predictions(prediction_rows, label_rows)
    splits = walk_forward_market_splits(joined, min_train_markets=min_train_markets, n_splits=n_splits)

    split_rows: list[dict[str, Any]] = []
    oos_rows: list[dict[str, Any]] = []
    for split in splits:
        train_units = set(split["train_units"])
        test_units = set(split["test_units"])
        if train_units.intersection(test_units):
            raise AssertionError("walk-forward split leakage: train/test units overlap")
        train = [r for r in joined if str(r.get("validation_unit_id")) in train_units]
        test = [r for r in joined if str(r.get("validation_unit_id")) in test_units]
        metrics = _metric_summary(test, bucket_count=bucket_count) if test else {}
        split_rows.append({
            "fold": split["fold"],
            "split_method": "walk_forward_by_market_or_fixture_date",
            "train_markets": len(train_units),
            "test_markets": len(test_units),
            "train_rows": len(train),
            "test_rows": len(test),
            **metrics,
        })
        oos_rows.extend(test)

    if not oos_rows and joined:
        units = sorted({str(r.get("validation_unit_id")) for r in joined})
        if len(units) >= 2:
            latest = {units[-1]}
            oos_rows = [r for r in joined if str(r.get("validation_unit_id")) in latest]

    summary = _metric_summary(oos_rows, bucket_count=bucket_count) if oos_rows else {
        "sample_size": 0,
        "markets": 0,
        "model_brier_score": None,
        "market_brier_score": None,
        "brier_improvement_vs_market": None,
        "brier_skill_vs_market": None,
        "model_log_loss": None,
        "market_log_loss": None,
        "log_loss_improvement_vs_market": None,
        "log_loss_skill_vs_market": None,
        "model_calibration_error": None,
        "market_calibration_error": None,
        "calibration_error_improvement_vs_market": None,
    }

    point_gain, ci_low, ci_high = _bootstrap_brier_gain_ci(oos_rows, n_boot=n_boot)
    model_deciles = calibration_by_decile(oos_rows, "model_probability", "model", bucket_count=bucket_count) if oos_rows else []
    market_deciles = calibration_by_decile(oos_rows, "market_probability", "market", bucket_count=bucket_count) if oos_rows else []
    model_decomp, model_decomp_rows = brier_decomposition(oos_rows, "model_probability", "model", bucket_count=bucket_count)
    market_decomp, market_decomp_rows = brier_decomposition(oos_rows, "market_probability", "market", bucket_count=bucket_count)

    costs = cfg.raw.get("costs", {})
    risk = cfg.raw.get("risk", {})
    roi_summary, roi_trades = simulate_roi(
        oos_rows,
        bankroll=float(risk.get("bankroll", 1000)),
        minimum_edge=float(settings.get("roi_minimum_edge", risk.get("minimum_edge", 0.03))),
        slippage=float(settings.get("slippage", costs.get("slippage", risk.get("maximum_slippage", 0.01)))),
        transaction_cost=float(settings.get("transaction_cost", costs.get("transaction_cost", 0.0))),
        liquidity_cap_fraction=float(settings.get("liquidity_cap_fraction", risk.get("liquidity_cap_fraction", 0.05))),
        maximum_single_market_exposure=float(settings.get("maximum_single_market_exposure", risk.get("maximum_single_market_exposure", 0.02))),
    )

    model_copies_market = bool(oos_rows) and all(abs(float(r["model_probability"]) - float(r["market_probability"])) <= copy_tolerance for r in oos_rows)
    blockers: list[str] = []
    if len(oos_rows) < min_rows:
        blockers.append(f"insufficient validation rows: {len(oos_rows)} < {min_rows}")
    if summary.get("markets", 0) < min_markets:
        blockers.append(f"insufficient validation markets: {summary.get('markets', 0)} < {min_markets}")
    if model_copies_market:
        blockers.append("model probabilities merely copy the market midpoint")
    if not (summary.get("brier_improvement_vs_market") is not None and float(summary["brier_improvement_vs_market"]) > 0):
        blockers.append("model does not improve Brier score versus market out of sample")
    if not (math.isfinite(ci_low) and ci_low > min_ci_gain):
        blockers.append("Brier gain versus market is not statistically credible")

    approved = not blockers
    payload = {
        "status": "approved" if approved else "blocked",
        "model_version": "pm-market-relative-validation-v1",
        "generated_at_utc": now_utc(),
        "split_method": "walk_forward_by_market_or_fixture_date",
        "clean_settled_joined_rows": len(joined),
        "rejected_join_rows": len(rejected),
        "oos": summary,
        "brier_gain_vs_market": point_gain,
        "brier_gain_vs_market_ci95": [ci_low, ci_high],
        "beats_market_oos": bool(summary.get("brier_improvement_vs_market") is not None and float(summary["brier_improvement_vs_market"]) > 0),
        "statistically_credible_market_relative_skill": bool(math.isfinite(ci_low) and ci_low > min_ci_gain),
        "model_copies_market_midpoint": model_copies_market,
        "approved_for_paper_trading": approved,
        "approved_for_live_trading": False,
        "blockers": blockers,
        "thresholds": {
            "min_rows": min_rows,
            "min_markets": min_markets,
            "min_train_markets": min_train_markets,
            "min_brier_gain_ci_low": min_ci_gain,
            "copy_tolerance": copy_tolerance,
        },
        "brier_decomposition": {"model": model_decomp, "market": market_decomp},
        "roi_simulation": roi_summary,
    }

    write_json(out / "market_relative_validation_summary.json", payload)
    write_json(cfg.governance_root / "market_relative_validation_summary.json", payload)
    write_csv(out / "market_relative_holdout_rows.csv", oos_rows)
    write_csv(out / "rejected_market_relative_rows.csv", rejected)
    write_csv(out / "walk_forward_results.csv", split_rows)
    write_csv(out / "calibration_by_decile.csv", [*model_deciles, *market_deciles])
    write_csv(out / "brier_decomposition.csv", [*model_decomp_rows, *market_decomp_rows])
    write_csv(out / "roi_simulation_trades.csv", roi_trades)
    write_csv(out / "baseline_comparison.csv", [
        {"forecaster": "model", "brier_score": summary.get("model_brier_score"), "log_loss": summary.get("model_log_loss"), "calibration_error": summary.get("model_calibration_error")},
        {"forecaster": "market_midpoint", "brier_score": summary.get("market_brier_score"), "log_loss": summary.get("market_log_loss"), "calibration_error": summary.get("market_calibration_error")},
    ])
    return payload


def main(config_path: str) -> dict[str, Any]:
    return validate_market_relative_model(load_config(config_path))
