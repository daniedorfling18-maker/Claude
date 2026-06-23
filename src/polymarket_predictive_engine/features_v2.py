from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from .config import EngineConfig, load_config
from .utils import discover_files, find_first_column, infer_category, normalize_slug, parse_timestamp, read_csv_rows, safe_float, write_csv

FORBIDDEN_FEATURE_HINTS = [
    "target",
    "winner",
    "winning",
    "resolved",
    "settled",
    "payout",
    "final_result",
    "settlement",
]
MOMENTUM_WINDOWS_HOURS = {"5m": 5 / 60, "15m": 15 / 60, "1h": 1, "6h": 6, "24h": 24}
ROLLING_WINDOWS_HOURS = {"1h": 1, "6h": 6, "24h": 24}


def reject_leakage_columns(columns: list[str]) -> None:
    bad = [c for c in columns if any(hint in c.lower() for hint in FORBIDDEN_FEATURE_HINTS)]
    if bad:
        raise ValueError("Potential leakage columns present. Remove before feature building: " + ", ".join(bad))


def _std(values: list[float]) -> str | float:
    if len(values) < 2:
        return ""
    m = mean(values)
    return (sum((v - m) ** 2 for v in values) / (len(values) - 1)) ** 0.5


def _change_at_or_before(history: list[tuple[Any, float]], current_ts: Any, current_price: float, hours: float) -> str | float:
    candidates = [(ts, price) for ts, price in history if (current_ts - ts).total_seconds() / 3600 >= hours]
    if not candidates:
        return ""
    return current_price - candidates[-1][1]


def _rolling_values(history_with_current: list[tuple[Any, float]], current_ts: Any, hours: float) -> list[float]:
    seconds = hours * 3600
    return [price for ts, price in history_with_current if 0 <= (current_ts - ts).total_seconds() <= seconds]


def _logit(probability: float) -> float:
    p = max(1e-6, min(1 - 1e-6, probability))
    return math.log(p / (1 - p))


def _text_features(question: str, slug: str) -> dict[str, Any]:
    q = (question or "").lower()
    normalised_slug = normalize_slug(slug or question)
    return {
        "question_length": len(question or ""),
        "slug_tokens": len([x for x in normalised_slug.split("-") if x]),
        "word_will": int("will" in q),
        "word_by": int("by" in q),
        "word_before": int("before" in q),
        "word_after": int("after" in q),
        "word_or": int(" or " in q),
        "word_and": int(" and " in q),
    }


def _source_files(cfg: EngineConfig) -> list[Path]:
    files = discover_files(
        cfg.data_root,
        [
            "outputs/polymarket_wide/**/raw_market_snapshots.csv",
            "outputs/polymarket_fixed/**/raw_market_snapshots.csv",
        ],
    )
    historical = cfg.output_root / "polymarket_training" / "historical_price_snapshots.csv"
    if historical.exists():
        files.append(historical)
    return files


def _normalise_rows_from_file(cfg: EngineConfig, path: Path) -> list[dict[str, Any]]:
    rows = read_csv_rows(path)
    if not rows:
        return []
    cols = list(rows[0].keys())
    reject_leakage_columns(cols)
    is_history = path.name == "historical_price_snapshots.csv"

    market_col = find_first_column(cols, cfg.raw.get("schema", {}).get("market_id_fields", ["market_id", "condition_id", "id", "market_slug", "slug"]))
    token_col = find_first_column(cols, cfg.raw.get("schema", {}).get("token_id_fields", ["token_id", "asset_id", "outcome_token_id"]))
    ts_col = "timestamp" if is_history and "timestamp" in cols else find_first_column(cols, cfg.raw.get("schema", {}).get("timestamp_fields", ["snapshot_timestamp", "timestamp", "collected_at"]))
    slug_col = find_first_column(cols, ["market_slug", "slug"])
    question_col = find_first_column(cols, ["question", "title", "market_question"])
    bid_col = find_first_column(cols, ["best_bid", "bid"])
    ask_col = find_first_column(cols, ["best_ask", "ask"])
    bid_size_col = find_first_column(cols, ["bid_size", "best_bid_size"])
    ask_size_col = find_first_column(cols, ["ask_size", "best_ask_size"])
    price_col = find_first_column(cols, ["midpoint", "price", "last_price", "probability"])
    liq_col = find_first_column(cols, ["liquidity", "liquidity_num", "depth"])
    vol_col = find_first_column(cols, ["volume", "volume_num"])
    close_col = find_first_column(cols, ["close_time", "end_time", "market_close_time", "closed_at", "end_date"])
    tick_col = find_first_column(cols, ["tick_size", "order_price_min_tick_size"])
    category_col = find_first_column(cols, ["category"])

    if not market_col or not token_col or not ts_col:
        return []

    normalised: list[dict[str, Any]] = []
    for row in rows:
        ts = parse_timestamp(row.get(ts_col))
        if not ts:
            continue
        bid = safe_float(row.get(bid_col or ""))
        ask = safe_float(row.get(ask_col or ""))
        midpoint = safe_float(row.get(price_col or ""))
        if midpoint is None and bid is not None and ask is not None:
            midpoint = (bid + ask) / 2
        if midpoint is None:
            continue
        spread = (ask - bid) if ask is not None and bid is not None else ""
        normalised.append(
            {
                "market_id": row.get(market_col, ""),
                "market_slug": row.get(slug_col or "", "") or row.get(market_col, ""),
                "token_id": row.get(token_col, ""),
                "prediction_timestamp_dt": ts,
                "prediction_timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "category": row.get(category_col or "", "") or infer_category(path),
                "midpoint": midpoint,
                "best_bid": bid if bid is not None else "",
                "best_ask": ask if ask is not None else "",
                "spread": spread,
                "executable_buy_price": ask if ask is not None else midpoint,
                "liquidity": safe_float(row.get(liq_col or "")) or "",
                "volume": safe_float(row.get(vol_col or "")) or "",
                "bid_size": safe_float(row.get(bid_size_col or "")) or "",
                "ask_size": safe_float(row.get(ask_size_col or "")) or "",
                "tick_size": safe_float(row.get(tick_col or "")) or "",
                "close_dt": parse_timestamp(row.get(close_col or "")),
                "question": row.get(question_col or "", ""),
                "source_file": str(path),
                "source": "historical_price_snapshots" if is_history else "raw_market_snapshots",
            }
        )
    return normalised


def build_features_v2(cfg: EngineConfig) -> list[dict[str, Any]]:
    source_rows: list[dict[str, Any]] = []
    for path in _source_files(cfg):
        source_rows.extend(_normalise_rows_from_file(cfg, path))

    source_rows.sort(key=lambda row: (row["market_id"], row["token_id"], row["prediction_timestamp"]))
    history: dict[tuple[str, str], list[tuple[Any, float]]] = defaultdict(list)
    features: list[dict[str, Any]] = []
    lineage: list[dict[str, Any]] = []

    for row in source_rows:
        key = (row["market_id"], row["token_id"])
        ts = row["prediction_timestamp_dt"]
        midpoint = float(row["midpoint"])
        past = list(history[key])
        history_with_current = past + [(ts, midpoint)]

        spread = row.get("spread", "")
        spread_pct = float(spread) / midpoint if spread != "" and midpoint else ""
        bid_size = safe_float(row.get("bid_size"))
        ask_size = safe_float(row.get("ask_size"))
        top_total = ""
        top_imbalance = ""
        if bid_size is not None and ask_size is not None:
            top_total = bid_size + ask_size
            top_imbalance = ((bid_size - ask_size) / top_total) if top_total else ""

        close = row.get("close_dt")
        hours_to_close = ((close - ts).total_seconds() / 3600) if close else ""

        feature = {
            "market_id": row["market_id"],
            "market_slug": row["market_slug"],
            "token_id": row["token_id"],
            "prediction_timestamp": row["prediction_timestamp"],
            "category": row["category"],
            "midpoint": midpoint,
            "implied_probability": midpoint,
            "best_bid": row["best_bid"],
            "best_ask": row["best_ask"],
            "spread": spread,
            "spread_pct_midpoint": spread_pct,
            "executable_buy_price": row["executable_buy_price"],
            "liquidity": row["liquidity"],
            "volume": row["volume"],
            "bid_size": row["bid_size"],
            "ask_size": row["ask_size"],
            "top_of_book_size_total": top_total,
            "top_of_book_size_imbalance": top_imbalance,
            "tick_size": row["tick_size"],
            "hours_to_close": hours_to_close,
            "days_to_close": (hours_to_close / 24) if hours_to_close != "" else "",
            "is_last_24h": int(hours_to_close != "" and 0 <= hours_to_close <= 24),
            "is_last_6h": int(hours_to_close != "" and 0 <= hours_to_close <= 6),
            "is_last_1h": int(hours_to_close != "" and 0 <= hours_to_close <= 1),
            "hour_of_day": ts.hour,
            "day_of_week": ts.weekday(),
            "logit_midpoint": _logit(midpoint),
            "distance_to_half": abs(midpoint - 0.5),
            "snapshots_so_far": len(past),
            **_text_features(str(row.get("question", "")), str(row.get("market_slug", ""))),
        }

        for name, hours in MOMENTUM_WINDOWS_HOURS.items():
            feature[f"price_change_{name}"] = _change_at_or_before(past, ts, midpoint, hours)
        for name, hours in ROLLING_WINDOWS_HOURS.items():
            values = _rolling_values(history_with_current, ts, hours)
            feature[f"rolling_mean_{name}"] = mean(values) if values else ""
            feature[f"rolling_volatility_{name}"] = _std(values)

        features.append(feature)
        lineage.append(
            {
                "source_file": row["source_file"],
                "source": row["source"],
                "market_id": row["market_id"],
                "token_id": row["token_id"],
                "prediction_timestamp": row["prediction_timestamp"],
                "point_in_time_rule": "only rows at or before prediction_timestamp are used",
            }
        )
        history[key].append((ts, midpoint))

    out_root = cfg.output_root / "polymarket_training"
    write_csv(out_root / "features_v2.csv", features)
    write_csv(
        out_root / "feature_dictionary_v2.csv",
        [{"feature": key, "description": "point-in-time v2 engineered feature"} for key in sorted({key for row in features for key in row.keys()})],
    )
    write_csv(cfg.governance_root / "feature_lineage_v2.csv", lineage)
    return features


def main(config_path: str) -> list[dict[str, Any]]:
    return build_features_v2(load_config(config_path))
