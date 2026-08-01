"""Mispricing alpha: training and scoring for model-vs-market edge.

Leakage invariant: ``_enrich_with_latest_websocket_quotes`` merges the *latest*
observed quotes into rows and is a scoring-time convenience only. It must be
reachable solely from ``apply_mispricing_alpha`` (live decision scoring).
Training (``train_mispricing_alpha_model``), label building, and historical
backtests must consume stored point-in-time rows only — enriching them with
later quotes would be lookahead. A regression test pins this invariant.

Volatility copy-through only: a missing, blank, or malformed
`rolling_volatility_6h`/`rolling_volatility_24h` yields `volatility_penalty =
0.0` and `volatility_source = "missing"`, which is unchanged from prior
behaviour and never raises; nothing here marks a market measured, changes any
M-A/M-B/M-C or `maker_min_*` threshold, changes `_measurement_eligible`, moves
any gate, sizing rule, filter, or eligibility surface, and `edge_lower_bound`
may only shrink or stay equal as a result of this change, never grow.
"""

from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from polymarket_common.fees import resolve_taker_fee_schedule, taker_fee_per_share

from .config import EngineConfig, load_config
from .crypto_updown_model import is_crypto_updown_contract, score_crypto_updown_prediction
from .execution_costs import estimate_execution_cost
from .models.calibration_v2 import joined_feature_label_rows
from .utils import (
    boolish,
    git_commit_hash,
    now_utc,
    parse_timestamp,
    read_csv_rows,
    read_json,
    safe_float,
    write_csv,
    write_json,
)
from .worldcup_validation import classify_market_family, is_worldcup_winner_market, signal_cohort

MODEL_VERSION = "pm-mispricing-alpha-v1"
ARTIFACT_NAME = "mispricing_alpha_v1.json"
QUOTE_ENRICHMENT_FIELDS = (
    "best_bid",
    "best_ask",
    "spread",
    "liquidity",
    "top_bid_size",
    "top_ask_size",
    "bid_depth_1pct",
    "ask_depth_1pct",
    "bid_depth_5pct",
    "ask_depth_5pct",
    "book_imbalance",
)


def _setting_bool(settings: dict[str, Any], key: str, default: bool = False) -> bool:
    value = settings.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return boolish(value)


def _setting_strings(
    settings: dict[str, Any],
    key: str,
    default: tuple[str, ...] = (),
) -> tuple[str, ...]:
    value = settings.get(key)
    if value is None:
        value = default
    if isinstance(value, (str, Path)):
        values = [value]
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        values = []
    return tuple(
        str(item).strip().lower().replace("\\", "/")
        for item in values
        if str(item).strip()
    )


def _source_matches_any_fragment(source: str, fragments: tuple[str, ...]) -> bool:
    if not source or not fragments:
        return False
    normalised = source.strip().lower().replace("\\", "/")
    return any(fragment in normalised for fragment in fragments)


def _clamp_probability(value: float) -> float:
    return max(1e-6, min(1 - 1e-6, float(value)))


def _market_probability(row: dict[str, Any]) -> float | None:
    for key in ("market_midpoint", "market_probability", "midpoint", "implied_probability", "raw_probability"):
        value = safe_float(row.get(key))
        if value is not None and 0 <= value <= 1:
            return _clamp_probability(value)
    return None


def _model_probability(row: dict[str, Any]) -> float | None:
    for key in ("calibrated_probability", "model_probability", "raw_probability"):
        value = safe_float(row.get(key))
        if value is not None and 0 <= value <= 1:
            return _clamp_probability(value)
    return None


def _executable_price(row: dict[str, Any]) -> float | None:
    for key in ("executable_price", "executable_buy_price", "best_ask", "market_price"):
        value = safe_float(row.get(key))
        if value is not None and 0 < value < 1:
            return value
    return None


def _time_bucket(hours: float | None) -> str:
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


def _probability_bucket(probability: float | None, bucket_count: int) -> str:
    if probability is None:
        return "unknown"
    p = _clamp_probability(probability)
    bucket = min(bucket_count - 1, max(0, int(p * bucket_count)))
    return f"{bucket / bucket_count:.2f}-{(bucket + 1) / bucket_count:.2f}"


def _category(row: dict[str, Any]) -> str:
    return str(row.get("category") or "unknown").lower()


def _cohort_evidence_blocks(cfg: EngineConfig) -> dict[str, dict[str, Any]]:
    """Return exact cohorts that should be suppressed by measured negative evidence.

    This is deliberately an exact-cohort guard. It does not generalise a losing
    BTC-up 5m live-model cohort onto other assets, intervals, or rule families;
    it only stops the scorer from repeatedly surfacing a cohort that the shadow
    ledger/governance layer has already measured as bad.
    """
    settings = cfg.raw.get("shadow_cohort_validation", {}) or {}
    min_closed = int(settings.get("quarantine_min_closed_positions", 3))
    max_roi = float(settings.get("quarantine_max_closed_roi", -0.05))
    max_pnl = float(settings.get("quarantine_max_closed_pnl_usdc", -1.0))
    blocks: dict[str, dict[str, Any]] = {}

    shadow_summary = read_json(cfg.governance_root / "shadow_signal_cohort_pnl.json", default={}) or {}
    quarantined = shadow_summary.get("quarantined_cohorts", []) if isinstance(shadow_summary, dict) else []
    if isinstance(quarantined, list):
        for row in quarantined:
            if not isinstance(row, dict):
                continue
            cohort = str(row.get("signal_cohort") or row.get("cohort") or "").strip()
            if not cohort:
                continue
            blocks[cohort] = {
                "status": "cohort_quarantined",
                "reason": row.get("quarantine_reason") or "cohort is quarantined by closed shadow evidence",
                "closed_positions": row.get("closed_positions", ""),
                "pnl_usdc": row.get("closed_realised_pnl_usdc", ""),
                "roi": row.get("closed_roi", ""),
            }

    signal_summary = read_json(cfg.governance_root / "signal_cohort_pnl.json", default={}) or {}
    cohorts = signal_summary.get("cohorts", []) if isinstance(signal_summary, dict) else []
    if isinstance(cohorts, list):
        for row in cohorts:
            if not isinstance(row, dict):
                continue
            cohort = str(row.get("signal_cohort") or row.get("cohort") or "").strip()
            if not cohort or cohort in blocks:
                continue
            settled = int(safe_float(row.get("settled_fills") or row.get("shadow_sell_fills") or 0) or 0)
            pnl = safe_float(row.get("total_pnl_usdc") or row.get("shadow_total_pnl_usdc"))
            roi = safe_float(row.get("roi") or row.get("shadow_roi"))
            if settled >= min_closed and pnl is not None and roi is not None and (roi <= max_roi or pnl <= max_pnl):
                blocks[cohort] = {
                    "status": "cohort_negative_direct_evidence",
                    "reason": f"cohort has negative closed evidence: roi={roi:.4f}, pnl={pnl:.2f}, settled={settled}",
                    "closed_positions": settled,
                    "pnl_usdc": pnl,
                    "roi": roi,
                }
    return blocks


def _row_hours_to_close(row: dict[str, Any]) -> float | None:
    for key in ("time_to_close_hours", "hours_to_close"):
        value = safe_float(row.get(key))
        if value is not None:
            return value
    return None


def _cell_keys(row: dict[str, Any], bucket_count: int) -> list[str]:
    probability = _market_probability(row)
    probability_bucket = _probability_bucket(probability, bucket_count)
    time_bucket = _time_bucket(_row_hours_to_close(row))
    category = _category(row)
    return [
        f"category_time_probability|{category}|{time_bucket}|{probability_bucket}",
        f"category_probability|{category}|{probability_bucket}",
        f"time_probability|all|{time_bucket}|{probability_bucket}",
        f"probability|all|{probability_bucket}",
        "global|all",
    ]


def _cell_summary(rows: list[dict[str, Any]], prior_strength: float) -> dict[str, Any]:
    targets = [int(row["_target"]) for row in rows]
    markets = [float(row["_market_probability"]) for row in rows]
    actual = sum(targets) / len(targets)
    market = sum(markets) / len(markets)
    raw_bias = actual - market
    shrinkage = len(rows) / (len(rows) + prior_strength)
    return {
        "rows": len(rows),
        "actual_win_rate": actual,
        "mean_market_probability": market,
        "raw_bias": raw_bias,
        "shrunk_bias": raw_bias * shrinkage,
        "market_brier": sum((p - y) ** 2 for p, y in zip(markets, targets)) / len(targets),
    }


def train_mispricing_alpha_model(cfg: EngineConfig) -> dict[str, Any]:
    settings = cfg.raw.get("mispricing_alpha", {})
    train_root = cfg.output_root / "polymarket_training"
    rows = joined_feature_label_rows(str(train_root / "features_v2.csv"), str(train_root / "labels.csv"))
    bucket_count = int(settings.get("probability_buckets", 10))
    prior_strength = float(settings.get("bias_prior_strength", 25.0))
    minimum_rows = int(settings.get("minimum_rows", cfg.raw.get("governance_thresholds", {}).get("min_training_rows", 100)))

    prepared: list[dict[str, Any]] = []
    for row in rows:
        market_probability = _market_probability(row)
        target = safe_float(row.get("target"))
        if market_probability is None or target is None:
            continue
        prepared.append(
            {
                **row,
                "_market_probability": market_probability,
                "_target": int(target),
            }
        )

    output_dir = cfg.output_root / "polymarket_models"
    output_dir.mkdir(parents=True, exist_ok=True)
    if len(prepared) < minimum_rows:
        payload = {
            "status": "insufficient_data",
            "deployment_mode": "shadow",
            "reason": f"insufficient joined rows: {len(prepared)} < {minimum_rows}",
            "model_version": MODEL_VERSION,
            "training_rows": len(prepared),
            "probability_buckets": bucket_count,
            "generated_at_utc": now_utc(),
            "git_commit_hash": git_commit_hash(),
        }
        write_json(output_dir / ARTIFACT_NAME, payload)
        write_json(cfg.governance_root / "mispricing_alpha_summary.json", payload)
        write_csv(cfg.governance_root / "mispricing_alpha_bias_cells.csv", [])
        return payload

    by_cell: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in prepared:
        for key in _cell_keys(row, bucket_count):
            by_cell[key].append(row)

    min_cell_rows = int(settings.get("minimum_cell_rows", 8))
    cells = {
        key: _cell_summary(cell_rows, prior_strength)
        for key, cell_rows in sorted(by_cell.items())
        if len(cell_rows) >= min_cell_rows or key == "global|all"
    }
    cell_rows_out = [
        {"cell_key": key, **value}
        for key, value in cells.items()
        if key != "global|all"
    ]

    markets = {
        str(row.get("market_id") or row.get("market_slug") or "")
        for row in prepared
        if str(row.get("market_id") or row.get("market_slug") or "")
    }
    payload = {
        "status": "trained",
        "deployment_mode": "alpha_overlay",
        "model_version": MODEL_VERSION,
        "trained_at": now_utc(),
        "git_commit_hash": git_commit_hash(),
        "training_rows": len(prepared),
        "training_markets": len(markets),
        "probability_buckets": bucket_count,
        "minimum_cell_rows": min_cell_rows,
        "bias_prior_strength": prior_strength,
        "global_bias": cells.get("global|all", {}).get("shrunk_bias", 0.0),
        "cells": cells,
        "feature_policy": "Learns only ex-ante market calibration residuals: target - market_probability by category/time/probability bucket.",
    }
    write_json(output_dir / ARTIFACT_NAME, payload)
    write_json(cfg.governance_root / "mispricing_alpha_summary.json", payload)
    write_csv(cfg.governance_root / "mispricing_alpha_bias_cells.csv", cell_rows_out)
    return payload


def _load_alpha_model(cfg: EngineConfig) -> dict[str, Any]:
    model = read_json(cfg.output_root / "polymarket_models" / ARTIFACT_NAME, default={}) or {}
    return model if isinstance(model, dict) else {}


def _row_time(row: dict[str, Any]):
    return (
        row.get("prediction_timestamp")
        or row.get("collected_at_utc")
        or row.get("source_timestamp")
        or row.get("timestamp")
        or ""
    )


def _enrich_with_latest_websocket_quotes(
    cfg: EngineConfig,
    predictions: list[dict[str, Any]],
    settings: dict[str, Any],
) -> list[dict[str, Any]]:
    if not _setting_bool(settings, "enrich_with_latest_websocket_quotes", True):
        return predictions
    path = cfg.output_root / "polymarket_training" / "websocket_market_features.csv"
    if not path.exists():
        return predictions
    latest: dict[str, dict[str, Any]] = {}
    latest_time: dict[str, Any] = {}
    for row in read_csv_rows(path):
        token = str(row.get("token_id") or row.get("asset_id") or "").strip()
        if not token:
            continue
        parsed = parse_timestamp(_row_time(row))
        if parsed is None:
            continue
        if token not in latest_time or parsed >= latest_time[token]:
            latest[token] = row
            latest_time[token] = parsed
    if not latest:
        return predictions

    max_age_seconds = safe_float(settings.get("max_websocket_quote_enrichment_age_seconds"))
    if max_age_seconds is None:
        max_age_seconds = 3600.0
    enriched: list[dict[str, Any]] = []
    for prediction in predictions:
        token = str(prediction.get("token_id") or prediction.get("asset_id") or "").strip()
        quote = latest.get(token)
        if not quote:
            enriched.append(prediction)
            continue
        pred_time = parse_timestamp(_row_time(prediction))
        quote_time = latest_time.get(token)
        age_seconds = abs((quote_time - pred_time).total_seconds()) if pred_time is not None and quote_time is not None else None
        if age_seconds is not None and age_seconds > max_age_seconds:
            enriched.append(
                {
                    **prediction,
                    "websocket_quote_enrichment_status": "stale_quote_outside_age_window",
                    "websocket_quote_age_seconds": round(age_seconds, 3),
                }
            )
            continue
        merged = dict(prediction)
        for field in QUOTE_ENRICHMENT_FIELDS:
            value = quote.get(field)
            if value not in {None, ""}:
                merged[field] = value
        best_ask = safe_float(quote.get("best_ask"))
        if best_ask is not None and 0 < best_ask < 1:
            merged["executable_price"] = best_ask
        midpoint = safe_float(quote.get("midpoint"))
        if midpoint is not None and 0 <= midpoint <= 1:
            merged["market_midpoint"] = midpoint
        merged["websocket_quote_enrichment_status"] = "enriched"
        merged["websocket_quote_timestamp"] = "" if quote_time is None else quote_time.isoformat().replace("+00:00", "Z")
        merged["websocket_quote_age_seconds"] = "" if age_seconds is None else round(age_seconds, 3)
        enriched.append(merged)
    return enriched


def _resolve_repo_relative_path(cfg: EngineConfig, path: str | Path) -> Path:
    resolved = Path(path)
    if resolved.is_absolute():
        return resolved
    data_root = cfg.data_root
    if not data_root.is_absolute():
        data_root = cfg.path.parent / data_root
    return data_root / resolved


def _fundamental_probability_paths(cfg: EngineConfig, settings: dict[str, Any]) -> list[Path]:
    configured = settings.get("fundamental_probability_paths")
    if configured is None:
        configured = [
            cfg.output_root / "polymarket" / "repo_worldcup_winner_probabilities.csv",
            Path("inputs/polymarket/model_probabilities.csv"),
        ]
    if isinstance(configured, (str, Path)):
        configured = [configured]
    paths: list[Path] = []
    for raw_path in configured:
        if raw_path in {None, ""}:
            continue
        paths.append(_resolve_repo_relative_path(cfg, Path(str(raw_path))))
    return paths


def _load_fundamental_probabilities(cfg: EngineConfig, settings: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not settings.get("use_fundamental_probabilities", True):
        return {}

    loaded: dict[str, dict[str, Any]] = {}
    for path in _fundamental_probability_paths(cfg, settings):
        for row in read_csv_rows(path):
            token_id = str(row.get("token_id") or row.get("asset_id") or "").strip()
            probability = safe_float(
                row.get("probability")
                or row.get("fundamental_probability")
                or row.get("model_probability")
                or row.get("fair_probability")
            )
            if not token_id or probability is None or not 0 <= probability <= 1:
                continue
            loaded.setdefault(
                token_id,
                {
                    "probability": _clamp_probability(probability),
                    "source": str(path),
                },
            )
    return loaded


def _lookup_bias(row: dict[str, Any], model: dict[str, Any], settings: dict[str, Any]) -> tuple[float, str, int]:
    if model.get("status") != "trained":
        return 0.0, "missing_or_untrained", 0
    bucket_count = int(model.get("probability_buckets") or settings.get("probability_buckets", 10))
    minimum_cell_rows = int(settings.get("minimum_live_cell_rows", model.get("minimum_cell_rows", 8)))
    cells = model.get("cells", {}) or {}
    for key in _cell_keys(row, bucket_count):
        cell = cells.get(key, {})
        if key == "global|all" or int(cell.get("rows", 0)) >= minimum_cell_rows:
            return float(cell.get("shrunk_bias", 0.0)), key, int(cell.get("rows", 0))
    return float(model.get("global_bias", 0.0)), "global|all", int((cells.get("global|all", {}) or {}).get("rows", 0))


def _confidence(row: dict[str, Any], probability: float) -> float:
    value = safe_float(row.get("model_confidence"))
    if value is None:
        value = safe_float(row.get("confidence"))
    if value is None:
        value = 2.0 * abs(probability - 0.5)
    return max(0.0, min(1.0, value))


def _microstructure_penalty(
    row: dict[str, Any],
    settings: dict[str, Any],
    *,
    flat_slippage: float = 0.0,
) -> tuple[float, dict[str, Any]]:
    spread = safe_float(row.get("spread"))
    liquidity = safe_float(row.get("liquidity"))
    ask_size = safe_float(row.get("ask_size") or row.get("top_ask_size"))
    bid_size = safe_float(row.get("bid_size") or row.get("top_bid_size"))
    volatility = safe_float(row.get("rolling_volatility_6h"))
    volatility_source = "rolling_volatility_6h" if volatility is not None else ""
    if volatility is None:
        volatility = safe_float(row.get("rolling_volatility_24h"))
        volatility_source = "rolling_volatility_24h" if volatility is not None else "missing"

    spread_penalty = max(0.0, spread or 0.0) * float(settings.get("spread_penalty_weight", 0.50))
    reference_liquidity = float(settings.get("reference_liquidity", 1000.0))
    liquidity_penalty = 0.0
    if liquidity is not None and reference_liquidity > 0:
        liquidity_penalty = max(0.0, 1.0 - min(1.0, liquidity / reference_liquidity)) * float(
            settings.get("low_liquidity_penalty", 0.02)
        )
    elif liquidity is None:
        liquidity_penalty = float(settings.get("missing_liquidity_penalty", 0.01))

    depth_penalty = 0.0
    if ask_size is not None and bid_size is not None and ask_size + bid_size > 0:
        buy_pressure = bid_size / (ask_size + bid_size)
        depth_penalty = max(0.0, buy_pressure - 0.5) * float(settings.get("depth_imbalance_penalty_weight", 0.02))

    execution_estimate = estimate_execution_cost(
        row,
        stake_usdc=float(settings.get("execution_cost_probe_stake_usdc", 1.0) or 1.0),
        flat_slippage=flat_slippage,
    )
    execution_slippage = safe_float(execution_estimate.get("expected_slippage")) or 0.0
    execution_cost_penalty = execution_slippage * float(settings.get("execution_cost_penalty_weight", 1.0))

    volatility_penalty = max(0.0, volatility or 0.0) * float(settings.get("volatility_penalty_weight", 0.15))
    total = spread_penalty + liquidity_penalty + depth_penalty + volatility_penalty + execution_cost_penalty
    return total, {
        "spread_penalty": spread_penalty,
        "liquidity_penalty": liquidity_penalty,
        "depth_penalty": depth_penalty,
        "volatility_penalty": volatility_penalty,
        "volatility_source": volatility_source,
        "execution_cost_penalty": execution_cost_penalty,
        "execution_cost_status": execution_estimate.get("status", ""),
        "execution_expected_slippage": execution_estimate.get("expected_slippage", ""),
        "execution_depth_stake_cap_usdc": execution_estimate.get("max_stake_at_acceptable_impact_usdc", ""),
    }


def _group_key(row: dict[str, Any]) -> str:
    return str(row.get("market_id") or row.get("condition_id") or row.get("market_slug") or "")


def apply_mispricing_alpha(
    cfg: EngineConfig,
    predictions: list[dict[str, Any]] | None = None,
    *,
    output_path: str | None = None,
) -> list[dict[str, Any]]:
    settings = cfg.raw.get("mispricing_alpha", {})
    if predictions is None:
        predictions = read_csv_rows(cfg.output_root / "polymarket_predictions" / "predictions.csv")
    if not settings.get("enabled", True):
        return predictions
    predictions = _enrich_with_latest_websocket_quotes(cfg, predictions, settings)

    model = _load_alpha_model(cfg)
    alpha_shrinkage = float(settings.get("bias_alpha_shrinkage", 0.50))
    model_residual_shrinkage = float(settings.get("model_residual_shrinkage", 0.25))
    max_model_residual_adjustment = float(settings.get("max_model_residual_adjustment", 0.04))
    max_deviation_from_market = float(settings.get("max_alpha_probability_deviation_from_market", 0.25))
    uncertainty_penalty_weight = float(settings.get("uncertainty_penalty_weight", 0.03))
    market_overround_weight = float(settings.get("market_overround_penalty_weight", 0.35))
    model_overround_weight = float(settings.get("model_overround_penalty_weight", 0.20))
    fundamental_residual_shrinkage = float(settings.get("fundamental_residual_shrinkage", 0.35))
    fundamental_probability_haircut = max(0.0, min(1.0, float(settings.get("fundamental_probability_haircut", 0.5))))
    require_fundamental_cross_check = _setting_bool(settings, "require_fundamental_cross_check_for_trading", True)
    minimum_fundamental_edge_after_haircut = float(
        settings.get(
            "minimum_fundamental_edge_after_haircut",
            cfg.raw.get("risk", {}).get("minimum_edge", 0.03),
        )
    )
    max_alpha_spread = safe_float(
        settings.get("max_spread_for_alpha_trade", cfg.raw.get("risk", {}).get("maximum_spread"))
    )
    max_alpha_relative_spread = safe_float(settings.get("max_relative_spread_for_alpha_trade"))
    min_alpha_liquidity = safe_float(
        settings.get("min_liquidity_for_alpha_trade", cfg.raw.get("risk", {}).get("minimum_liquidity"))
    )
    min_alpha_price = safe_float(settings.get("min_price_for_alpha_trade"))
    trade_edge_threshold = float(cfg.raw.get("risk", {}).get("minimum_edge", 0.03))
    near_miss_settings = settings.get("near_miss_learning", {}) or {}
    near_miss_enabled = _setting_bool(near_miss_settings, "enabled", False)
    near_miss_min_raw_edge = float(near_miss_settings.get("min_raw_edge", 0.05))
    near_miss_min_edge_lower_bound = float(near_miss_settings.get("min_edge_lower_bound", 0.0))
    near_miss_max_edge_lower_bound = float(
        near_miss_settings.get("max_edge_lower_bound", trade_edge_threshold)
    )
    near_miss_min_liquidity = safe_float(
        near_miss_settings.get("min_liquidity", min_alpha_liquidity)
    )
    near_miss_max_spread = safe_float(
        near_miss_settings.get("max_spread", max_alpha_spread)
    )
    near_miss_max_relative_spread = safe_float(
        near_miss_settings.get("max_relative_spread", max_alpha_relative_spread)
    )
    near_miss_max_candidates = max(0, int(near_miss_settings.get("max_candidates", 25) or 25))
    shadow_settings = cfg.raw.get("shadow_cohort_validation", {}) or {}
    shadow_enabled = _setting_bool(shadow_settings, "enabled", False)
    shadow_min_edge_lower_bound = float(shadow_settings.get("minimum_edge_lower_bound", -0.01))
    shadow_min_fundamental_edge = float(shadow_settings.get("minimum_fundamental_edge_after_haircut", 0.008))
    shadow_min_price = safe_float(shadow_settings.get("minimum_price"))
    shadow_max_spread = safe_float(shadow_settings.get("maximum_spread", max_alpha_spread))
    shadow_max_relative_spread = safe_float(shadow_settings.get("maximum_relative_spread", max_alpha_relative_spread))
    shadow_min_liquidity = safe_float(shadow_settings.get("minimum_liquidity", min_alpha_liquidity))
    shadow_worldcup_only = _setting_bool(shadow_settings, "worldcup_winner_only", True)
    shadow_non_worldcup_source_fragments = _setting_strings(
        shadow_settings,
        "allowed_non_worldcup_fundamental_source_fragments",
        ("sharp_fundamental_probabilities.csv",),
    )
    max_fundamental_adjustment = float(settings.get("max_fundamental_adjustment", 0.12))
    fundamental_probabilities = _load_fundamental_probabilities(cfg, settings)
    crypto_settings = cfg.raw.get("crypto_updown_live_model", {}) or {}
    crypto_probability_blend = float(crypto_settings.get("probability_blend_weight", 0.65))
    crypto_max_adjustment = float(crypto_settings.get("max_probability_adjustment", 0.25))
    crypto_max_rows = int(crypto_settings.get("max_scored_rows_per_cycle", 24))
    crypto_min_ttc = safe_float(crypto_settings.get("minimum_time_to_close_hours", 0.0))
    crypto_max_ttc = safe_float(crypto_settings.get("maximum_time_to_close_hours", 36.0))
    require_scored_crypto_model_for_trading = _setting_bool(
        crypto_settings,
        "require_scored_model_for_trading",
        True,
    )
    crypto_rows_attempted = 0
    crypto_shadow_enabled = _setting_bool(crypto_settings, "allow_shadow_candidates", True)
    crypto_min_shadow_edge_after_cost = float(crypto_settings.get("minimum_shadow_edge_after_cost", 0.015))
    cohort_evidence_blocks = _cohort_evidence_blocks(cfg)

    enriched: list[dict[str, Any]] = []
    for row in predictions:
        row = dict(row)
        classified_family = classify_market_family(row)
        if str(row.get("category") or "").strip().lower() in {"", "unknown", "uncategorised", "uncategorized"}:
            row["category"] = classified_family
        market_probability = _market_probability(row)
        model_probability = _model_probability(row)
        price = _executable_price(row)
        if market_probability is None or model_probability is None or price is None:
            enriched.append({**row, "alpha_status": "skipped_missing_probability_or_price"})
            continue
        bias, cell_key, cell_rows = _lookup_bias(row, model, settings)
        bias_adjustment = bias * alpha_shrinkage
        raw_residual_adjustment = model_residual_shrinkage * (model_probability - market_probability)
        residual_adjustment = max(
            -max_model_residual_adjustment,
            min(max_model_residual_adjustment, raw_residual_adjustment),
        )
        token_id = str(row.get("token_id") or row.get("asset_id") or "").strip()
        fundamental = fundamental_probabilities.get(token_id)
        fundamental_probability = safe_float(fundamental.get("probability")) if fundamental else None
        fundamental_source = str(fundamental.get("source")) if fundamental else ""
        crypto_model = {"crypto_model_status": "not_crypto_updown"}
        crypto_updown_contract = is_crypto_updown_contract(row)
        if crypto_updown_contract:
            time_to_close = safe_float(row.get("time_to_close_hours") or row.get("hours_to_close"))
            if crypto_min_ttc is not None and time_to_close is not None and time_to_close < crypto_min_ttc:
                crypto_model = {"crypto_model_status": "outside_time_to_close_window"}
            elif crypto_max_ttc is not None and time_to_close is not None and time_to_close > crypto_max_ttc:
                crypto_model = {"crypto_model_status": "outside_time_to_close_window"}
            elif crypto_rows_attempted >= crypto_max_rows:
                crypto_model = {"crypto_model_status": "row_budget_exhausted"}
            else:
                crypto_rows_attempted += 1
                crypto_model = score_crypto_updown_prediction(row, crypto_settings)
        crypto_model_probability = safe_float(crypto_model.get("crypto_model_probability"))
        worldcup_winner = is_worldcup_winner_market(row)
        haircut_fundamental_probability = None
        fundamental_edge_after_haircut = None
        bookmaker_required = bool(require_fundamental_cross_check and worldcup_winner)
        bookmaker_cross_check_pass = not bookmaker_required
        fundamental_adjustment = 0.0
        if fundamental_probability is not None:
            haircut_fundamental_probability = _clamp_probability(
                market_probability + fundamental_probability_haircut * (fundamental_probability - market_probability)
            )
            fundamental_edge_after_haircut = haircut_fundamental_probability - price
            bookmaker_cross_check_pass = (
                not bookmaker_required
                or fundamental_edge_after_haircut >= minimum_fundamental_edge_after_haircut
            )
            fundamental_adjustment = max(
                -max_fundamental_adjustment,
                min(
                    max_fundamental_adjustment,
                    fundamental_residual_shrinkage * (fundamental_probability - market_probability),
                ),
            )
        crypto_model_adjustment = 0.0
        if crypto_model_probability is not None:
            crypto_model_adjustment = max(
                -crypto_max_adjustment,
                min(
                    crypto_max_adjustment,
                    crypto_probability_blend * (crypto_model_probability - market_probability),
                ),
            )
        unconstrained_alpha = (
            market_probability
            + bias_adjustment
            + residual_adjustment
            + fundamental_adjustment
            + crypto_model_adjustment
        )
        alpha_probability = _clamp_probability(
            max(
                market_probability - max_deviation_from_market,
                min(market_probability + max_deviation_from_market, unconstrained_alpha),
            )
        )
        confidence = _confidence(row, alpha_probability)
        uncertainty_penalty = (1.0 - confidence) * uncertainty_penalty_weight
        micro_penalty, micro_parts = _microstructure_penalty(
            row,
            settings,
            flat_slippage=float(cfg.raw.get("costs", {}).get("slippage", 0.0) or 0.0),
        )
        spread = safe_float(row.get("spread"))
        liquidity = safe_float(row.get("liquidity"))
        relative_spread = (spread / price) if spread is not None and price > 0 else None
        # Microstructure filters only block when book data is present and breaches a limit.
        # Absent spread/liquidity (a prediction row carrying no book) is unknown, not a breach,
        # so it passes this research-layer gate; live execution still applies its own
        # liquidity/geoblock guards before any order is placed.
        price_filter_pass = min_alpha_price is None or price >= min_alpha_price
        spread_filter_pass = max_alpha_spread is None or spread is None or spread <= max_alpha_spread
        relative_spread_filter_pass = (
            max_alpha_relative_spread is None
            or relative_spread is None
            or relative_spread <= max_alpha_relative_spread
        )
        liquidity_filter_pass = (
            min_alpha_liquidity is None or liquidity is None or liquidity >= min_alpha_liquidity
        )
        microstructure_filter_pass = bool(
            price_filter_pass and spread_filter_pass and relative_spread_filter_pass and liquidity_filter_pass
        )
        validation_reasons = []
        if not bookmaker_cross_check_pass:
            validation_reasons.append("bookmaker_fundamental_cross_check_failed")
        crypto_model_gate_pass = bool(
            not (crypto_updown_contract and require_scored_crypto_model_for_trading)
            or str(crypto_model.get("crypto_model_status") or "") == "scored"
        )
        if not crypto_model_gate_pass:
            validation_reasons.append(
                "crypto_updown_model_not_scored_for_trading:"
                + str(crypto_model.get("crypto_model_status") or "missing")
            )
        if not price_filter_pass:
            validation_reasons.append("price_below_alpha_trade_limit")
        if not spread_filter_pass:
            validation_reasons.append("spread_above_alpha_trade_limit")
        current_signal_cohort = signal_cohort(
            {**row, **crypto_model, "fundamental_probability": fundamental_probability or ""}
        )
        cohort_evidence_block = cohort_evidence_blocks.get(current_signal_cohort)
        cohort_evidence_filter_pass = cohort_evidence_block is None
        if not relative_spread_filter_pass:
            validation_reasons.append("relative_spread_above_alpha_trade_limit")
        if not liquidity_filter_pass:
            validation_reasons.append("liquidity_below_alpha_trade_limit")
        if not cohort_evidence_filter_pass:
            validation_reasons.append(str(cohort_evidence_block.get("status") or "cohort_negative_evidence"))
        enriched.append(
            {
                **row,
                "_alpha_probability_numeric": alpha_probability,
                "_alpha_market_probability_numeric": market_probability,
                "alpha_status": "scored",
                "alpha_model_version": model.get("model_version", "missing"),
                "alpha_bias_cell": cell_key,
                "alpha_bias_cell_rows": cell_rows,
                "alpha_bias_adjustment": bias_adjustment,
                "alpha_model_residual_raw_adjustment": raw_residual_adjustment,
                "alpha_model_residual_adjustment": residual_adjustment,
                "fundamental_probability": "" if fundamental_probability is None else fundamental_probability,
                "haircut_fundamental_probability": ""
                if haircut_fundamental_probability is None
                else haircut_fundamental_probability,
                "fundamental_edge_after_haircut": ""
                if fundamental_edge_after_haircut is None
                else fundamental_edge_after_haircut,
                "fundamental_probability_haircut": fundamental_probability_haircut,
                "bookmaker_cross_check_required": bookmaker_required,
                "bookmaker_cross_check_pass": bookmaker_cross_check_pass,
                "fundamental_adjustment": fundamental_adjustment,
                "fundamental_source": fundamental_source,
                "crypto_model_adjustment": crypto_model_adjustment,
                **crypto_model,
                "crypto_model_gate_pass": crypto_model_gate_pass,
                "worldcup_winner_validation_market": worldcup_winner,
                "signal_cohort": current_signal_cohort,
                "cohort_evidence_filter_pass": cohort_evidence_filter_pass,
                "cohort_evidence_status": "" if cohort_evidence_block is None else cohort_evidence_block.get("status", ""),
                "cohort_evidence_reason": "" if cohort_evidence_block is None else cohort_evidence_block.get("reason", ""),
                "relative_spread": "" if relative_spread is None else relative_spread,
                "price_filter_pass": price_filter_pass,
                "spread_filter_pass": spread_filter_pass,
                "relative_spread_filter_pass": relative_spread_filter_pass,
                "liquidity_filter_pass": liquidity_filter_pass,
                "microstructure_filter_pass": microstructure_filter_pass,
                "validation_layer_reason": "; ".join(validation_reasons),
                "alpha_unconstrained_probability": unconstrained_alpha,
                "alpha_probability": alpha_probability,
                "alpha_uncertainty_penalty": uncertainty_penalty,
                "alpha_microstructure_penalty": micro_penalty,
                **micro_parts,
            }
        )

    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in enriched:
        key = _group_key(row)
        if key:
            by_group[key].append(row)

    final_rows: list[dict[str, Any]] = []
    for row in enriched:
        price = _executable_price(row)
        alpha_probability = safe_float(row.get("_alpha_probability_numeric"))
        if price is None or alpha_probability is None:
            final_rows.append({key: value for key, value in row.items() if not key.startswith("_")})
            continue
        group = by_group.get(_group_key(row), [])
        valid_group = [
            item
            for item in group
            if safe_float(item.get("_alpha_probability_numeric")) is not None
            and safe_float(item.get("_alpha_market_probability_numeric")) is not None
        ]
        market_sum = sum(float(item["_alpha_market_probability_numeric"]) for item in valid_group) if len(valid_group) >= 2 else ""
        model_sum = sum(float(item["_alpha_probability_numeric"]) for item in valid_group) if len(valid_group) >= 2 else ""
        market_overround = max(0.0, float(market_sum) - 1.0) if market_sum != "" else 0.0
        model_overround = max(0.0, float(model_sum) - 1.0) if model_sum != "" else 0.0
        complete_set_discount = max(0.0, 1.0 - float(market_sum)) if market_sum != "" else 0.0
        cross_penalty = market_overround * market_overround_weight + model_overround * model_overround_weight
        raw_alpha_edge = alpha_probability - price
        total_penalty = (
            float(row.get("alpha_uncertainty_penalty", 0.0))
            + float(row.get("alpha_microstructure_penalty", 0.0))
            + cross_penalty
        )
        fee_schedule = resolve_taker_fee_schedule(row)
        fee_per_share = taker_fee_per_share(price=price, schedule=fee_schedule)
        edge_before_taker_fee = raw_alpha_edge - total_penalty
        edge_lower_bound = edge_before_taker_fee - fee_per_share
        alpha_score = edge_lower_bound / max(0.01, math.sqrt(max(price * (1 - price), 1e-6)))
        validation_layer_pass = bool(
            row.get("bookmaker_cross_check_pass")
            and row.get("microstructure_filter_pass")
            and row.get("cohort_evidence_filter_pass", True)
            and row.get("crypto_model_gate_pass", True)
        )
        fundamental_edge_for_shadow_gross = safe_float(row.get("fundamental_edge_after_haircut"))
        fundamental_edge_for_shadow = (
            None if fundamental_edge_for_shadow_gross is None else fundamental_edge_for_shadow_gross - fee_per_share
        )
        crypto_edge_for_shadow = safe_float(row.get("crypto_model_edge_after_cost"))
        crypto_shadow_mode = bool(
            crypto_shadow_enabled
            and str(row.get("crypto_model_status") or "") == "scored"
        )
        crypto_fast_shadow_mode = bool(
            crypto_shadow_mode
            and str(row.get("crypto_model_contract_kind") or "").strip().lower() == "fast"
        )
        spread_for_shadow = safe_float(row.get("spread"))
        liquidity_for_shadow = safe_float(row.get("liquidity"))
        relative_spread_for_shadow = (spread_for_shadow / price) if spread_for_shadow is not None and price > 0 else None
        worldcup_shadow_market = bool(row.get("worldcup_winner_validation_market"))
        non_worldcup_shadow_source_allowed = (
            not worldcup_shadow_market
            and _source_matches_any_fragment(
                str(row.get("fundamental_source") or ""),
                shadow_non_worldcup_source_fragments,
            )
        )
        shadow_effective_max_spread = shadow_max_spread
        shadow_effective_max_relative_spread = shadow_max_relative_spread
        shadow_effective_min_liquidity = shadow_min_liquidity
        if crypto_fast_shadow_mode:
            shadow_effective_max_spread = safe_float(
                shadow_settings.get("fast_maximum_spread", shadow_effective_max_spread)
            )
            shadow_effective_max_relative_spread = safe_float(
                shadow_settings.get("fast_maximum_relative_spread", shadow_effective_max_relative_spread)
            )
            shadow_effective_min_liquidity = safe_float(
                shadow_settings.get("fast_minimum_liquidity", shadow_effective_min_liquidity)
            )
        shadow_reasons: list[str] = []
        if crypto_shadow_mode:
            if crypto_edge_for_shadow is None or crypto_edge_for_shadow < crypto_min_shadow_edge_after_cost:
                shadow_reasons.append("crypto_model_edge_below_shadow_minimum")
        else:
            if shadow_worldcup_only and not worldcup_shadow_market:
                shadow_reasons.append("not_worldcup_winner_market")
            elif not worldcup_shadow_market and not non_worldcup_shadow_source_allowed:
                shadow_reasons.append("non_worldcup_fundamental_source_not_allowed_for_shadow")
            if fundamental_edge_for_shadow is None or fundamental_edge_for_shadow < shadow_min_fundamental_edge:
                shadow_reasons.append("fundamental_edge_below_shadow_minimum")
        if not boolish(row.get("cohort_evidence_filter_pass", True)):
            shadow_reasons.append(str(row.get("cohort_evidence_status") or "cohort_evidence_blocked"))
        if shadow_min_price is not None and price < shadow_min_price:
            shadow_reasons.append("price_below_shadow_minimum")
        if not crypto_shadow_mode and edge_lower_bound < shadow_min_edge_lower_bound:
            shadow_reasons.append("edge_lower_bound_below_shadow_minimum")
        if shadow_effective_max_spread is not None and (spread_for_shadow is None or spread_for_shadow > shadow_effective_max_spread):
            shadow_reasons.append("spread_above_shadow_limit")
        if shadow_effective_max_relative_spread is not None and (
            relative_spread_for_shadow is None or relative_spread_for_shadow > shadow_effective_max_relative_spread
        ):
            shadow_reasons.append("relative_spread_above_shadow_limit")
        if shadow_effective_min_liquidity is not None and (
            liquidity_for_shadow is None or liquidity_for_shadow < shadow_effective_min_liquidity
        ):
            shadow_reasons.append("liquidity_below_shadow_limit")
        shadow_trade_candidate = bool(shadow_enabled and not shadow_reasons)
        shadow_priority_score = (
            (fundamental_edge_for_shadow or 0.0)
            + (crypto_edge_for_shadow or 0.0)
            + max(0.0, edge_lower_bound)
            - max(0.0, spread_for_shadow or 0.0)
        )
        alpha_trade_candidate = bool(edge_lower_bound >= trade_edge_threshold and validation_layer_pass)
        near_miss_reasons: list[str] = []
        if not near_miss_enabled:
            near_miss_reasons.append("near_miss_learning_disabled")
        if alpha_trade_candidate:
            near_miss_reasons.append("already_trade_candidate")
        if not validation_layer_pass:
            near_miss_reasons.append("validation_layer_failed")
        if raw_alpha_edge < near_miss_min_raw_edge:
            near_miss_reasons.append("raw_edge_below_near_miss_minimum")
        if edge_lower_bound < near_miss_min_edge_lower_bound:
            near_miss_reasons.append("edge_lower_bound_below_near_miss_band")
        if edge_lower_bound >= near_miss_max_edge_lower_bound:
            near_miss_reasons.append("edge_lower_bound_above_near_miss_band")
        if near_miss_min_liquidity is not None and (
            liquidity_for_shadow is None or liquidity_for_shadow < near_miss_min_liquidity
        ):
            near_miss_reasons.append("liquidity_below_near_miss_minimum")
        if near_miss_max_spread is not None and (
            spread_for_shadow is None or spread_for_shadow > near_miss_max_spread
        ):
            near_miss_reasons.append("spread_above_near_miss_limit")
        if near_miss_max_relative_spread is not None and (
            relative_spread_for_shadow is None or relative_spread_for_shadow > near_miss_max_relative_spread
        ):
            near_miss_reasons.append("relative_spread_above_near_miss_limit")
        near_miss_learning_candidate = bool(not near_miss_reasons)
        near_miss_priority_score = (
            raw_alpha_edge
            + max(0.0, edge_lower_bound)
            - max(0.0, spread_for_shadow or 0.0)
            + min(1.0, max(0.0, (liquidity_for_shadow or 0.0) / max(1.0, near_miss_min_liquidity or 1.0))) * 0.01
        )
        clean = {key: value for key, value in row.items() if not key.startswith("_")}
        clean.update(
            {
                "alpha_raw_edge": raw_alpha_edge,
                "cross_market_group_size": len(valid_group),
                "cross_market_probability_sum": market_sum,
                "cross_model_probability_sum": model_sum,
                "cross_market_overround": market_overround,
                "cross_model_overround": model_overround,
                "complete_set_discount": complete_set_discount,
                "alpha_cross_market_penalty": cross_penalty,
                "alpha_total_penalty": total_penalty,
                "edge_lower_bound_before_taker_fee": edge_before_taker_fee,
                "taker_fee_in_edge": True,
                **fee_schedule.audit_fields(price=price),
                "edge_lower_bound": edge_lower_bound,
                "alpha_score": alpha_score,
                "fundamental_edge_after_haircut_after_taker_fee": ""
                if fundamental_edge_for_shadow is None
                else fundamental_edge_for_shadow,
                "validation_layer_pass": validation_layer_pass,
                "alpha_trade_candidate": alpha_trade_candidate,
                "near_miss_learning_candidate": near_miss_learning_candidate,
                "near_miss_learning_reason": "near_miss_eligible"
                if near_miss_learning_candidate
                else "; ".join(near_miss_reasons),
                "near_miss_priority_score": near_miss_priority_score,
                "non_worldcup_shadow_fundamental_source_allowed": non_worldcup_shadow_source_allowed,
                "shadow_trade_candidate": shadow_trade_candidate,
                "shadow_candidate_reason": "shadow_eligible" if shadow_trade_candidate else "; ".join(shadow_reasons),
                "shadow_priority_score": shadow_priority_score,
            }
        )
        final_rows.append(clean)

    target_monthly_profit = float(settings.get("target_monthly_profit_usdc", 100.0))
    candidate_rows = [row for row in final_rows if str(row.get("alpha_trade_candidate")).lower() == "true"]
    near_miss_rows = sorted(
        [row for row in final_rows if str(row.get("near_miss_learning_candidate")).lower() == "true"],
        key=lambda row: safe_float(row.get("near_miss_priority_score")) or 0.0,
        reverse=True,
    )
    if near_miss_max_candidates:
        near_miss_rows = near_miss_rows[:near_miss_max_candidates]
    unrisked_lower_bound_ev_per_100 = 0.0
    for row in candidate_rows:
        price = _executable_price(row)
        edge_lower_bound = safe_float(row.get("edge_lower_bound"))
        if price is not None and edge_lower_bound is not None and edge_lower_bound > 0:
            capped_price = max(price, float(settings.get("target_metric_min_price", 0.05)))
            unrisked_lower_bound_ev_per_100 += 100.0 * edge_lower_bound / capped_price
    summary = {
        "status": "scored",
        "generated_at_utc": now_utc(),
        "predictions": len(final_rows),
        "scored": sum(1 for row in final_rows if row.get("alpha_status") == "scored"),
        "trade_candidates": len(candidate_rows),
        "near_miss_learning_candidates": len(near_miss_rows),
        "shadow_trade_candidates": sum(
            1 for row in final_rows if str(row.get("shadow_trade_candidate")).lower() == "true"
        ),
        "target_monthly_profit_usdc": target_monthly_profit,
        "unrisked_lower_bound_ev_per_100usdc_if_all_candidates": unrisked_lower_bound_ev_per_100,
        "risk_aware_target_note": "Final target pace is computed after strategy/risk approval in forward_paper_cycle.json.",
        "alpha_model_status": model.get("status", "missing"),
        "alpha_model_version": model.get("model_version", "missing"),
        "fundamental_probabilities_loaded": len(fundamental_probabilities),
        "fundamental_probability_hits": sum(1 for row in final_rows if row.get("fundamental_probability") not in {"", None}),
        "crypto_model_enabled": bool(crypto_settings.get("enabled", True)),
        "crypto_model_scored": sum(1 for row in final_rows if row.get("crypto_model_status") == "scored"),
        "crypto_model_status_counts": dict(
            sorted(
                {
                    str(row.get("crypto_model_status") or "missing"): sum(
                        1
                        for item in final_rows
                        if str(item.get("crypto_model_status") or "missing")
                        == str(row.get("crypto_model_status") or "missing")
                    )
                    for row in final_rows
                }.items()
            )
        ),
        "bookmaker_cross_check_failures": sum(
            1 for row in final_rows if str(row.get("bookmaker_cross_check_pass")).lower() == "false"
        ),
        "microstructure_filter_failures": sum(
            1 for row in final_rows if str(row.get("microstructure_filter_pass")).lower() == "false"
        ),
        "rows_with_rolling_volatility": sum(
            1
            for row in final_rows
            if row.get("volatility_source") in {"rolling_volatility_6h", "rolling_volatility_24h"}
        ),
        "rows_missing_rolling_volatility": sum(
            1 for row in final_rows if row.get("volatility_source") == "missing"
        ),
        "volatility_penalty_sum": sum(safe_float(row.get("volatility_penalty")) or 0.0 for row in final_rows),
        "fundamental_probability_sources": sorted(
            {
                str(row.get("fundamental_source"))
                for row in final_rows
                if str(row.get("fundamental_source") or "")
            }
        ),
        "edge_field_for_trading": settings.get("edge_field_for_trading", "edge_lower_bound"),
    }
    write_json(cfg.governance_root / "mispricing_alpha_live_summary.json", summary)
    write_csv(cfg.output_root / "polymarket_predictions" / "mispricing_alpha_scores.csv", final_rows)
    write_csv(cfg.output_root / "polymarket_predictions" / "near_miss_learning_candidates.csv", near_miss_rows)
    if output_path:
        write_csv(output_path, final_rows)
    return final_rows


def main(config_path: str) -> dict[str, Any]:
    return train_mispricing_alpha_model(load_config(config_path))
