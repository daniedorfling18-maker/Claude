from __future__ import annotations

import math
from typing import Iterable, Mapping, Any


def clamp(p: float) -> float:
    return max(1e-6, min(1 - 1e-6, p))


def midpoint_baseline(row: Mapping[str, Any]) -> float:
    return clamp(float(row.get("midpoint") or row.get("market_midpoint") or 0.5))


def best_ask_implied_baseline(row: Mapping[str, Any]) -> float:
    return clamp(float(row.get("best_ask") or row.get("executable_buy_price") or row.get("midpoint") or 0.5))


def last_price_baseline(row: Mapping[str, Any]) -> float:
    return clamp(float(row.get("last_price") or row.get("midpoint") or 0.5))


def category_average_baseline(rows: Iterable[Mapping[str, Any]], category: str) -> float:
    vals = [float(r.get("target")) for r in rows if r.get("category") == category and str(r.get("target", "")).strip() != ""]
    return clamp(sum(vals) / len(vals)) if vals else 0.5


def no_trade_baseline(_: Mapping[str, Any] | None = None) -> float:
    return 0.0


def brier_score(y_true: list[int], y_prob: list[float]) -> float:
    if not y_true:
        return float("nan")
    return sum((clamp(p) - y) ** 2 for y, p in zip(y_true, y_prob)) / len(y_true)


def log_loss(y_true: list[int], y_prob: list[float]) -> float:
    if not y_true:
        return float("nan")
    return -sum(y * math.log(clamp(p)) + (1 - y) * math.log(1 - clamp(p)) for y, p in zip(y_true, y_prob)) / len(y_true)
