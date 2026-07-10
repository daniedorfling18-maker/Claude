"""Martingale drift scan for harvested Polymarket price histories.

Under market efficiency, prices should be martingales: conditional expected
future price change is zero.  This study estimates ``E[dP]`` by price bin,
time-to-close bin, category, and horizon using market-clustered resampling.
It is study-only: it creates no lane, order, gate, or sizing change.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import math
import random
from pathlib import Path
from typing import Any

from .config import EngineConfig, load_config
from .utils import now_utc, parse_timestamp, read_csv_rows, safe_float, write_csv, write_json

DEFAULT_PRICE_BINS = [round(i / 100, 2) for i in range(5, 100, 5)]

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "price_history_paths": ["outputs/polymarket_training/historical_price_snapshots.csv"],
    "horizons_hours": [24, 48],
    "price_bins": DEFAULT_PRICE_BINS,
    "time_to_close_bins_hours": [24, 72, 168],
    "min_markets_per_bin": 150,
    "fdr_alpha": 0.10,
    "bootstrap_iterations": 1000,
    "bootstrap_seed": 20260710,
    "max_forward_lag_minutes": 180,
    # Taker cost stack: fee + 0.5c exit + 0.5c adverse. Registered study
    # threshold; it can only make flags harder, never easier.
    "taker_fee_rate": 0.05,
    "exit_cost_per_dollar": 0.005,
    "adverse_cost_per_dollar": 0.005,
}

OUTPUT_FIELDS = [
    "category",
    "horizon_hours",
    "time_to_close_bin",
    "price_bin",
    "markets",
    "samples",
    "mean_start_price",
    "mean_drift",
    "cluster_bootstrap_ci_low",
    "cluster_bootstrap_ci_high",
    "cluster_bootstrap_p",
    "bh_fdr_pass",
    "cost_stack_per_dollar",
    "abs_drift_exceeds_cost",
    "research_flag",
    "paper_trading_invoked",
    "live_trading_invoked",
]


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = cfg.raw.get("drift_scan", {}) if isinstance(cfg.raw.get("drift_scan"), dict) else {}
    merged = dict(DEFAULT_SETTINGS)
    merged.update({key: value for key, value in raw.items() if value is not None})
    return merged


def _repo_root(cfg: EngineConfig) -> Path:
    data_root = cfg.data_root
    if not data_root.is_absolute():
        data_root = cfg.path.parent / data_root
    return data_root


def _expand_paths(cfg: EngineConfig, configured: Any) -> list[Path]:
    if isinstance(configured, (str, Path)):
        items = [configured]
    elif isinstance(configured, (list, tuple, set)):
        items = list(configured)
    else:
        items = []
    root = _repo_root(cfg)
    paths: list[Path] = []
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        raw = Path(text)
        if raw.is_absolute():
            matches = sorted(raw.parent.glob(raw.name)) if any(ch in raw.name for ch in "*?[") else [raw]
        else:
            matches = sorted(root.glob(text)) if any(ch in text for ch in "*?[") else [root / raw]
        for match in matches:
            if match not in paths:
                paths.append(match)
    return paths


def _price(row: dict[str, Any]) -> float | None:
    value = safe_float(row.get("price") or row.get("midpoint") or row.get("market_midpoint"))
    return value if value is not None and 0 < value < 1 else None


def _history_series(cfg: EngineConfig, settings: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    series: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in _expand_paths(cfg, settings.get("price_history_paths")):
        for row in read_csv_rows(path):
            token = str(row.get("token_id") or row.get("asset_id") or row.get("clob_token_id") or "").strip()
            timestamp = parse_timestamp(row.get("timestamp") or row.get("source_timestamp") or row.get("collected_at_utc"))
            close_time = parse_timestamp(row.get("close_time") or row.get("closedTime") or row.get("endDate"))
            price = _price(row)
            if not token or timestamp is None or close_time is None or price is None:
                continue
            market = str(row.get("market_id") or row.get("condition_id") or row.get("market_slug") or token).strip()
            category = str(row.get("category") or row.get("sport") or "unknown").strip().lower() or "unknown"
            series[token].append(
                {
                    "token_id": token,
                    "market_id": market,
                    "market_slug": row.get("market_slug", ""),
                    "category": category,
                    "timestamp": timestamp.astimezone(timezone.utc),
                    "close_time": close_time.astimezone(timezone.utc),
                    "price": price,
                }
            )
    for token in series:
        series[token].sort(key=lambda item: item["timestamp"])
    return dict(series)


def _price_bin(price: float, edges: list[float]) -> str | None:
    ordered = sorted(float(edge) for edge in edges)
    for low, high in zip(ordered, ordered[1:]):
        if low <= price < high or (high == ordered[-1] and low <= price <= high):
            return f"{low:.2f}-{high:.2f}"
    return None


def _time_to_close_bin(hours: float, edges: list[float]) -> str | None:
    low = 0.0
    for high in sorted(float(edge) for edge in edges):
        if low < hours <= high:
            return f"{low:.0f}-{high:.0f}h"
        low = high
    return None


def _first_at_or_after(
    series: list[dict[str, Any]],
    target: datetime,
    *,
    max_lag: timedelta,
) -> dict[str, Any] | None:
    for row in series:
        delta = row["timestamp"] - target
        if delta >= timedelta(0):
            return row if delta <= max_lag else None
    return None


def _samples(settings: dict[str, Any], series_by_token: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    horizons = [float(value) for value in settings.get("horizons_hours", [24, 48])]
    price_bins = [float(value) for value in settings.get("price_bins", DEFAULT_PRICE_BINS)]
    ttc_bins = [float(value) for value in settings.get("time_to_close_bins_hours", [24, 72, 168])]
    max_lag = timedelta(minutes=float(settings.get("max_forward_lag_minutes", 180)))
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, float, str, str]] = set()
    for token, series in series_by_token.items():
        if len(series) < 2:
            continue
        for idx, start in enumerate(series[:-1]):
            hours_to_close = (start["close_time"] - start["timestamp"]).total_seconds() / 3600.0
            time_bin = _time_to_close_bin(hours_to_close, ttc_bins)
            price_bin = _price_bin(float(start["price"]), price_bins)
            if time_bin is None or price_bin is None:
                continue
            for horizon in horizons:
                target = start["timestamp"] + timedelta(hours=horizon)
                if target > start["close_time"]:
                    continue
                future = _first_at_or_after(series[idx + 1 :], target, max_lag=max_lag)
                if future is None:
                    continue
                key = (token, start["timestamp"].isoformat(), horizon, time_bin, price_bin)
                if key in seen:
                    continue
                seen.add(key)
                drift = float(future["price"]) - float(start["price"])
                out.append(
                    {
                        "token_id": token,
                        "market_id": start["market_id"],
                        "category": start["category"],
                        "horizon_hours": horizon,
                        "time_to_close_bin": time_bin,
                        "price_bin": price_bin,
                        "start_price": float(start["price"]),
                        "future_price": float(future["price"]),
                        "drift": drift,
                    }
                )
    return out


def _cluster_means(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_market[str(row.get("market_id") or row.get("token_id") or "unknown")].append(row)
    out: list[dict[str, Any]] = []
    for market, market_rows in by_market.items():
        out.append(
            {
                "market_id": market,
                "drift": sum(float(row["drift"]) for row in market_rows) / len(market_rows),
                "start_price": sum(float(row["start_price"]) for row in market_rows) / len(market_rows),
                "samples": len(market_rows),
            }
        )
    return out


def _bootstrap_cluster_mean(
    values: list[float],
    *,
    iterations: int,
    seed: int,
) -> tuple[float | None, float | None, float | None]:
    if len(values) < 2:
        return None, None, None
    rng = random.Random(seed)
    n = len(values)
    means: list[float] = []
    for _ in range(max(1, iterations)):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    low = means[max(0, int(0.025 * len(means)) - 1) if int(0.025 * len(means)) > 0 else 0]
    high = means[min(len(means) - 1, int(math.ceil(0.975 * len(means))) - 1)]
    non_positive = sum(1 for value in means if value <= 0) / len(means)
    non_negative = sum(1 for value in means if value >= 0) / len(means)
    p_value = min(1.0, 2.0 * min(non_positive, non_negative))
    return round(low, 6), round(high, 6), round(p_value, 6)


def _cost_stack(mean_price: float, settings: dict[str, Any]) -> float:
    fee_rate = float(settings.get("taker_fee_rate", 0.05))
    exit_cost = float(settings.get("exit_cost_per_dollar", 0.005))
    adverse = float(settings.get("adverse_cost_per_dollar", 0.005))
    return fee_rate * max(0.0, 1.0 - mean_price) + exit_cost + adverse


def _bh_apply(rows: list[dict[str, Any]], alpha: float) -> None:
    eligible = [
        row
        for row in rows
        if safe_float(row.get("cluster_bootstrap_p")) is not None
        and (
            (safe_float(row.get("cluster_bootstrap_ci_low")) or 0) > 0
            or (safe_float(row.get("cluster_bootstrap_ci_high")) or 0) < 0
        )
    ]
    ranked = sorted(eligible, key=lambda row: float(row["cluster_bootstrap_p"]))
    m = len(ranked)
    cutoff_index = -1
    for idx, row in enumerate(ranked, start=1):
        if float(row["cluster_bootstrap_p"]) <= alpha * idx / max(m, 1):
            cutoff_index = idx - 1
    passed = {id(row) for row in ranked[: cutoff_index + 1]} if cutoff_index >= 0 else set()
    for row in rows:
        row["bh_fdr_pass"] = id(row) in passed
        row["abs_drift_exceeds_cost"] = abs(float(row.get("mean_drift") or 0.0)) > float(row.get("cost_stack_per_dollar") or 0.0)
        row["research_flag"] = bool(row["bh_fdr_pass"] and row["abs_drift_exceeds_cost"])


def run_drift_scan(cfg: EngineConfig) -> dict[str, Any]:
    settings = _settings(cfg)
    out_path = cfg.governance_root / "drift_scan.csv"
    summary_path = cfg.governance_root / "drift_scan.json"
    if str(settings.get("enabled", True)).strip().lower() in {"0", "false", "no", "off"}:
        write_csv(out_path, [], fieldnames=OUTPUT_FIELDS)
        summary = {
            "status": "disabled",
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
            "generated_at_utc": now_utc(),
        }
        write_json(summary_path, summary)
        return summary

    series = _history_series(cfg, settings)
    samples = _samples(settings, series)
    groups: dict[tuple[str, float, str, str], list[dict[str, Any]]] = defaultdict(list)
    for sample in samples:
        groups[
            (
                str(sample["category"]),
                float(sample["horizon_hours"]),
                str(sample["time_to_close_bin"]),
                str(sample["price_bin"]),
            )
        ].append(sample)

    min_markets = int(settings.get("min_markets_per_bin", 150))
    iterations = int(settings.get("bootstrap_iterations", 1000))
    base_seed = int(settings.get("bootstrap_seed", 20260710))
    rows: list[dict[str, Any]] = []
    thin_bins = 0
    for idx, (key, group_rows) in enumerate(sorted(groups.items(), key=lambda item: item[0])):
        category, horizon, time_bin, price_bin = key
        clusters = _cluster_means(group_rows)
        if len(clusters) < min_markets:
            thin_bins += 1
            continue
        drifts = [float(row["drift"]) for row in clusters]
        start_prices = [float(row["start_price"]) for row in clusters]
        mean_start_price = sum(start_prices) / len(start_prices)
        mean_drift = sum(drifts) / len(drifts)
        ci_low, ci_high, p_value = _bootstrap_cluster_mean(drifts, iterations=iterations, seed=base_seed + idx)
        cost = _cost_stack(mean_start_price, settings)
        rows.append(
            {
                "category": category,
                "horizon_hours": int(horizon) if float(horizon).is_integer() else horizon,
                "time_to_close_bin": time_bin,
                "price_bin": price_bin,
                "markets": len(clusters),
                "samples": sum(int(row["samples"]) for row in clusters),
                "mean_start_price": round(mean_start_price, 6),
                "mean_drift": round(mean_drift, 6),
                "cluster_bootstrap_ci_low": ci_low,
                "cluster_bootstrap_ci_high": ci_high,
                "cluster_bootstrap_p": p_value,
                "bh_fdr_pass": False,
                "cost_stack_per_dollar": round(cost, 6),
                "abs_drift_exceeds_cost": False,
                "research_flag": False,
                "paper_trading_invoked": False,
                "live_trading_invoked": False,
            }
        )

    _bh_apply(rows, float(settings.get("fdr_alpha", 0.10)))
    write_csv(out_path, rows, fieldnames=OUTPUT_FIELDS)
    flagged = [row for row in rows if row.get("research_flag")]
    summary = {
        "status": "ok" if rows else "insufficient_data",
        "generated_at_utc": now_utc(),
        "series_tokens": len(series),
        "samples": len(samples),
        "tested_bins": len(rows),
        "thin_bins_skipped": thin_bins,
        "flagged_bins": len(flagged),
        "flagged": flagged[:25],
        "settings": {
            "horizons_hours": settings.get("horizons_hours", [24, 48]),
            "price_bins": settings.get("price_bins", DEFAULT_PRICE_BINS),
            "time_to_close_bins_hours": settings.get("time_to_close_bins_hours", [24, 72, 168]),
            "min_markets_per_bin": min_markets,
            "fdr_alpha": float(settings.get("fdr_alpha", 0.10)),
            "taker_fee_rate": float(settings.get("taker_fee_rate", 0.05)),
            "exit_cost_per_dollar": float(settings.get("exit_cost_per_dollar", 0.005)),
            "adverse_cost_per_dollar": float(settings.get("adverse_cost_per_dollar", 0.005)),
        },
        "governance_note": (
            "Martingale drift scan is study-only. Flags identify bins for future "
            "pre-registration; they do not authorise paper/live trading or alter gates."
        ),
        "outputs": {"drift_scan_csv": str(out_path), "summary_json": str(summary_path)},
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    write_json(summary_path, summary)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return run_drift_scan(load_config(config_path))
