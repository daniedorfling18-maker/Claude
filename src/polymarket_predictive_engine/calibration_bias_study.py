"""WO-42 calibration-curve harvesting for favourite-longshot bias.

Study only: joins clean resolved outcomes to point-in-time CLOB prices before
close, estimates realised-vs-price calibration by category/horizon/bin, applies
clustered bootstrap CIs by market, then BH-FDR across scanned bins.
"""
from __future__ import annotations

import random
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any

from .config import EngineConfig, load_config
from .utils import now_utc, parse_timestamp, read_csv_rows, safe_float, write_csv, write_json

CURVE_FIELDS = [
    "category",
    "horizon_hours",
    "price_bin",
    "market_count",
    "avg_price",
    "realised_frequency",
    "isotonic_frequency",
    "deviation",
    "ci_low",
    "ci_high",
    "p_value",
    "bh_fdr_pass",
    "cost_stack",
    "flagged_edge",
]


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = cfg.raw.get("calibration_bias", {}) if isinstance(cfg.raw.get("calibration_bias"), dict) else {}
    merged = {
        "enabled": True,
        "price_horizons_hours": [24, 72],
        "min_markets_per_bin": 200,
        "price_bins": [round(i / 100, 2) for i in range(5, 100, 5)],
        "fdr_alpha": 0.10,
        "bootstrap_iterations": 300,
        "bootstrap_seed": 4242,
        "taker_fee_rate": 0.05,
        "exit_cost": 0.005,
        "adverse_cost": 0.005,
    }
    merged.update({k: v for k, v in raw.items() if v is not None})
    return merged


def _dt(value: Any) -> datetime | None:
    parsed = parse_timestamp(value)
    if parsed is not None:
        return parsed.astimezone(timezone.utc)
    return None


def _resolutions(cfg: EngineConfig) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in read_csv_rows(cfg.output_root / "polymarket_training" / "market_resolutions.csv"):
        target = safe_float(row.get("target"))
        token = str(row.get("token_id") or "").strip()
        close_time = _dt(row.get("close_time") or row.get("resolution_time"))
        quality = str(row.get("resolution_quality") or "clean_settlement")
        if token and target in {0.0, 1.0} and close_time is not None and quality == "clean_settlement":
            rows.append(
                {
                    "market": str(row.get("condition_id") or row.get("market_slug") or token),
                    "token_id": token,
                    "category": str(row.get("category") or "unknown"),
                    "target": float(target),
                    "close_time": close_time,
                }
            )
    return rows


def _price_rows(cfg: EngineConfig) -> dict[str, list[tuple[datetime, float]]]:
    paths = [
        cfg.output_root / "polymarket_training" / "historical_price_snapshots.csv",
        cfg.output_root / "polymarket_training" / "price_history.csv",
    ]
    by_token: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    for path in paths:
        for row in read_csv_rows(path):
            token = str(row.get("token_id") or row.get("asset_id") or "").strip()
            stamp = _dt(row.get("timestamp") or row.get("source_timestamp"))
            price = safe_float(row.get("midpoint") or row.get("price"))
            if token and stamp is not None and price is not None:
                by_token[token].append((stamp, price))
    for values in by_token.values():
        values.sort(key=lambda item: item[0])
    return by_token


def _price_at_or_before(series: list[tuple[datetime, float]], stamp: datetime) -> float | None:
    index = bisect_right(series, (stamp, float("inf"))) - 1
    return series[index][1] if index >= 0 else None


def _bin_label(price: float, bins: list[float]) -> str | None:
    ordered = sorted(float(x) for x in bins)
    if not ordered or price < ordered[0] or price > ordered[-1]:
        return None
    step = min((b - a for a, b in zip(ordered, ordered[1:]) if b > a), default=0.05)
    low = max(edge for edge in ordered if edge <= price)
    high = min(low + step, 1.0)
    return f"{low:.2f}-{high:.2f}"


def _cost_stack(avg_price: float, settings: dict[str, Any]) -> float:
    fee = float(settings["taker_fee_rate"]) * avg_price * (1.0 - avg_price)
    return fee + float(settings["exit_cost"]) + float(settings["adverse_cost"])


def _bootstrap_deviation(samples: list[dict[str, Any]], iterations: int, seed: int) -> tuple[float, float, float]:
    by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sample in samples:
        by_market[str(sample["market"])].append(sample)
    markets = list(by_market)
    observed = mean(row["target"] for row in samples) - mean(row["price"] for row in samples)
    if not markets:
        return observed, observed, 1.0
    rng = random.Random(seed)
    values: list[float] = []
    for _ in range(iterations):
        drawn: list[dict[str, Any]] = []
        for _ in markets:
            drawn.extend(by_market[rng.choice(markets)])
        values.append(mean(row["target"] for row in drawn) - mean(row["price"] for row in drawn))
    values.sort()
    low = values[int(0.025 * (len(values) - 1))]
    high = values[int(0.975 * (len(values) - 1))]
    below = sum(1 for value in values if value <= 0)
    above = sum(1 for value in values if value >= 0)
    p_value = min(1.0, 2.0 * (min(below, above) + 1) / (len(values) + 1))
    return low, high, p_value


def _bh_pass(p_values: list[float], alpha: float) -> list[bool]:
    indexed = sorted(enumerate(p_values), key=lambda item: item[1])
    cutoff = -1
    m = len(indexed)
    for rank, (index, p_value) in enumerate(indexed, start=1):
        if p_value <= (rank / m) * alpha:
            cutoff = rank
    passed = [False] * len(p_values)
    if cutoff > 0:
        for _, (index, _) in zip(range(cutoff), indexed):
            passed[index] = True
    return passed


def _stable_seed(value: tuple[Any, ...]) -> int:
    return sum((idx + 1) * ord(char) for idx, char in enumerate("|".join(str(part) for part in value)))


def _pava(points: list[tuple[float, float, int]]) -> list[float]:
    blocks: list[dict[str, float]] = []
    for _, y, weight in points:
        blocks.append({"sum": y * weight, "weight": float(weight), "count": 1})
        while len(blocks) >= 2:
            left = blocks[-2]["sum"] / blocks[-2]["weight"]
            right = blocks[-1]["sum"] / blocks[-1]["weight"]
            if left <= right:
                break
            merged = {
                "sum": blocks[-2]["sum"] + blocks[-1]["sum"],
                "weight": blocks[-2]["weight"] + blocks[-1]["weight"],
                "count": blocks[-2]["count"] + blocks[-1]["count"],
            }
            blocks = [*blocks[:-2], merged]
    fitted: list[float] = []
    for block in blocks:
        fitted.extend([block["sum"] / block["weight"]] * int(block["count"]))
    return fitted


def build_calibration_bias_study(cfg: EngineConfig) -> dict[str, Any]:
    settings = _settings(cfg)
    out_root = cfg.output_root / "calibration_bias"
    curve_path = out_root / "calibration_curve.csv"
    summary_path = out_root / "calibration_bias_study.json"
    generated_at = now_utc()
    summary: dict[str, Any] = {
        "status": "disabled",
        "generated_at_utc": generated_at,
        "work_order": "WO-42",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    if str(settings.get("enabled", True)).strip().lower() in {"0", "false", "no"}:
        write_json(summary_path, summary)
        return summary
    resolutions = _resolutions(cfg)
    prices = _price_rows(cfg)
    samples_by_group: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for resolution in resolutions:
        series = prices.get(resolution["token_id"]) or []
        if not series:
            continue
        for horizon in [int(x) for x in settings["price_horizons_hours"]]:
            stamp = resolution["close_time"] - timedelta(hours=horizon)
            price = _price_at_or_before(series, stamp)
            if price is None:
                continue
            label = _bin_label(price, [float(x) for x in settings["price_bins"]])
            if label is None:
                continue
            sample = {**resolution, "price": price, "horizon": horizon}
            samples_by_group[(resolution["category"], horizon, label)].append(sample)
    rows: list[dict[str, Any]] = []
    for (category, horizon, label), samples in sorted(samples_by_group.items()):
        if len({row["market"] for row in samples}) < int(settings["min_markets_per_bin"]):
            continue
        avg_price = mean(row["price"] for row in samples)
        realised = mean(row["target"] for row in samples)
        deviation = realised - avg_price
        ci_low, ci_high, p_value = _bootstrap_deviation(
            samples,
            int(settings["bootstrap_iterations"]),
            int(settings["bootstrap_seed"]) + _stable_seed((category, horizon, label)) % 10_000,
        )
        rows.append(
            {
                "category": category,
                "horizon_hours": horizon,
                "price_bin": label,
                "market_count": len({row["market"] for row in samples}),
                "avg_price": round(avg_price, 6),
                "realised_frequency": round(realised, 6),
                "isotonic_frequency": None,
                "deviation": round(deviation, 6),
                "ci_low": round(ci_low, 6),
                "ci_high": round(ci_high, 6),
                "p_value": round(p_value, 6),
                "bh_fdr_pass": False,
                "cost_stack": round(_cost_stack(avg_price, settings), 6),
                "flagged_edge": False,
            }
        )
    for key in {(row["category"], row["horizon_hours"]) for row in rows}:
        group_indices = [idx for idx, row in enumerate(rows) if (row["category"], row["horizon_hours"]) == key]
        group_points = [
            (float(rows[idx]["avg_price"]), float(rows[idx]["realised_frequency"]), int(rows[idx]["market_count"]))
            for idx in group_indices
        ]
        for idx, fitted in zip(group_indices, _pava(group_points)):
            rows[idx]["isotonic_frequency"] = round(fitted, 6)
    passes = _bh_pass([float(row["p_value"]) for row in rows], float(settings["fdr_alpha"])) if rows else []
    for row, passed in zip(rows, passes):
        row["bh_fdr_pass"] = passed
        excludes_zero = float(row["ci_low"]) > 0 or float(row["ci_high"]) < 0
        clears_cost = abs(float(row["deviation"])) > float(row["cost_stack"])
        row["flagged_edge"] = bool(passed and excludes_zero and clears_cost)
    write_csv(curve_path, rows, fieldnames=CURVE_FIELDS)
    flagged = [row for row in rows if row["flagged_edge"]]
    summary.update(
        {
            "status": "ok" if rows else "insufficient_data",
            "resolved_markets": len({row["market"] for row in resolutions}),
            "curve_rows": len(rows),
            "flagged_bins": len(flagged),
            "fdr_alpha": float(settings["fdr_alpha"]),
            "min_markets_per_bin": int(settings["min_markets_per_bin"]),
            "curve_path": str(curve_path),
            "flagged_preview": flagged[:20],
            "note": "Study-only favourite/longshot calibration scan. Flags are candidates for future pre-registration, not trades.",
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        }
    )
    write_json(summary_path, summary)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return build_calibration_bias_study(load_config(config_path))
