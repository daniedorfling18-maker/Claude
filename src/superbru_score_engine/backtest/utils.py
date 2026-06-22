from __future__ import annotations

from typing import Any


def parse_float_grid(raw: str) -> list[float]:
    if not raw:
        return []
    return [float(item.strip()) for item in raw.split(",") if item.strip()]


def parse_int_grid(raw: str) -> list[int]:
    if not raw:
        return []
    return [int(item.strip()) for item in raw.split(",") if item.strip()]


def parse_str_grid(raw: str, *, lowercase: bool = False) -> list[str]:
    if not raw:
        return []
    items = [item.strip() for item in raw.split(",") if item.strip()]
    return [item.lower() for item in items] if lowercase else items


def mode_delta(summaries: dict[str, Any], test_mode: str, baseline_mode: str) -> float | None:
    if test_mode not in summaries or baseline_mode not in summaries:
        return None
    return float(summaries[test_mode]["avg_model_points"] - summaries[baseline_mode]["avg_model_points"])
