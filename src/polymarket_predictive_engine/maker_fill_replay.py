"""WO-40 maker fill realism replay.

The maker-carry study charges adverse selection from bar moves and trade-print
markouts. This replay asks a narrower execution question: given the recorded
top-of-book/depth archive, would a last-in-queue maker quote actually have
filled when public prints crossed the quote level?

Measurement only. The replay reports a realism ratio next to the study charge
but never modifies the study, gates, quote sheet, or any order path.
"""
from __future__ import annotations

import csv
import gzip
from bisect import bisect_left, bisect_right
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import EngineConfig, load_config
from .utils import now_utc, parse_timestamp, read_csv_rows, read_json, safe_float, write_json

HORIZONS_MINUTES = (5, 15, 60)


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = cfg.raw.get("maker_fill_replay", {}) if isinstance(cfg.raw.get("maker_fill_replay"), dict) else {}
    merged = {
        "enabled": True,
        "max_markets": 10,
        "replay_days": 7,
    }
    merged.update({k: v for k, v in raw.items() if v is not None})
    return merged


def _stamp(value: Any) -> float | None:
    numeric = safe_float(value)
    if numeric is not None:
        return numeric / 1000.0 if numeric > 10_000_000_000 else numeric
    parsed = parse_timestamp(value)
    return parsed.timestamp() if parsed is not None else None


def _minute(stamp: float) -> float:
    return float(int(stamp // 60) * 60)


def _read_csv_any(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8-sig", errors="replace", newline="") as handle:
            return [{str(k): "" if v is None else str(v) for k, v in row.items()} for row in csv.DictReader(handle)]
    return read_csv_rows(path)


def _feature_files(cfg: EngineConfig) -> list[Path]:
    archive_root = cfg.output_root / "polymarket_training_archive"
    files = sorted(archive_root.glob("*.csv.gz")) if archive_root.exists() else []
    live = cfg.output_root / "polymarket_training" / "websocket_market_features.csv"
    if live.exists():
        files.append(live)
    return files


def _book_states(cfg: EngineConfig, token_ids: set[str], replay_days: float) -> dict[str, list[dict[str, float]]]:
    rows: list[dict[str, Any]] = []
    for path in _feature_files(cfg):
        rows.extend(_read_csv_any(path))
    parsed: list[dict[str, float]] = []
    max_stamp = 0.0
    for row in rows:
        token_id = str(row.get("asset_id") or row.get("token_id") or "").strip()
        if token_id not in token_ids:
            continue
        stamp = _stamp(row.get("source_timestamp") or row.get("collected_at_utc"))
        bid = safe_float(row.get("best_bid"))
        ask = safe_float(row.get("best_ask"))
        midpoint = safe_float(row.get("midpoint"))
        if stamp is None or bid is None or ask is None or ask <= bid:
            continue
        midpoint = midpoint if midpoint is not None else (bid + ask) / 2.0
        max_stamp = max(max_stamp, stamp)
        parsed.append(
            {
                "stamp": stamp,
                "minute": _minute(stamp),
                "token_id": token_id,
                "best_bid": bid,
                "best_ask": ask,
                "midpoint": midpoint,
                "bid_depth": safe_float(row.get("resting_bid_depth_at_quote"))
                or safe_float(row.get("top_bid_size"))
                or safe_float(row.get("bid_depth_1pct"))
                or 0.0,
                "ask_depth": safe_float(row.get("resting_ask_depth_at_quote"))
                or safe_float(row.get("top_ask_size"))
                or safe_float(row.get("ask_depth_1pct"))
                or 0.0,
            }
        )
    cutoff = max_stamp - replay_days * 86400.0 if max_stamp and replay_days > 0 else float("-inf")
    latest_by_minute: dict[tuple[str, float], dict[str, float]] = {}
    for row in parsed:
        if row["stamp"] >= cutoff:
            latest_by_minute[(str(row["token_id"]), row["minute"])] = row
    by_token: dict[str, list[dict[str, float]]] = {}
    for row in latest_by_minute.values():
        by_token.setdefault(str(row["token_id"]), []).append(row)
    for token_rows in by_token.values():
        token_rows.sort(key=lambda item: item["stamp"])
    return by_token


def _trades(cfg: EngineConfig, markets: set[str], token_ids: set[str]) -> list[dict[str, Any]]:
    rows = read_csv_rows(cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv")
    trades: list[dict[str, Any]] = []
    for row in rows:
        market = str(row.get("market") or "").strip()
        token_id = str(row.get("asset_id") or row.get("token_id") or "").strip()
        if market not in markets and token_id not in token_ids:
            continue
        stamp = _stamp(row.get("timestamp") or row.get("collected_at_utc"))
        price = safe_float(row.get("price"))
        size = safe_float(row.get("size"))
        side = str(row.get("side") or "").upper()
        if stamp is None or price is None or size is None or side not in {"BUY", "SELL"}:
            continue
        trades.append({"stamp": stamp, "market": market, "token_id": token_id, "side": side, "price": price, "size": size})
    trades.sort(key=lambda item: item["stamp"])
    return trades


def _state_at_or_before(states: list[dict[str, float]], stamp: float) -> dict[str, float] | None:
    stamps = [row["stamp"] for row in states]
    index = bisect_right(stamps, stamp) - 1
    return states[index] if index >= 0 else None


def _state_at_or_after(states: list[dict[str, float]], stamp: float) -> dict[str, float] | None:
    stamps = [row["stamp"] for row in states]
    index = bisect_left(stamps, stamp)
    return states[index] if index < len(states) else None


def _candidate_map(cfg: EngineConfig) -> dict[str, dict[str, str]]:
    rows = read_csv_rows(cfg.output_root / "maker_carry" / "maker_carry_candidates.csv")
    return {str(row.get("condition_id") or ""): row for row in rows if row.get("condition_id")}


def _portfolio(summary: dict[str, Any], candidates: dict[str, dict[str, str]], max_markets: int) -> list[dict[str, Any]]:
    portfolio: list[dict[str, Any]] = []
    for entry in (summary.get("portfolio") or [])[:max_markets]:
        if not isinstance(entry, dict):
            continue
        condition_id = str(entry.get("condition_id") or "").strip()
        candidate = candidates.get(condition_id, {})
        token_id = str(entry.get("token_id") or candidate.get("token_id") or "").strip()
        quote_size = safe_float(entry.get("quote_size_shares"))
        quote_distance = safe_float(entry.get("quote_distance"))
        if condition_id and token_id and quote_size is not None and quote_distance is not None:
            portfolio.append({**entry, "condition_id": condition_id, "token_id": token_id, "quote_size_shares": quote_size, "quote_distance": quote_distance})
    return portfolio


def _mean(values: Iterable[float]) -> float | None:
    vals = list(values)
    return round(sum(vals) / len(vals), 6) if vals else None


def run_maker_fill_replay(cfg: EngineConfig) -> dict[str, Any]:
    settings = _settings(cfg)
    out_root = cfg.output_root / "maker_carry"
    summary_path = out_root / "maker_fill_replay.json"
    generated_at = now_utc()
    payload: dict[str, Any] = {
        "status": "disabled",
        "generated_at_utc": generated_at,
        "work_order": "WO-40",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    if str(settings.get("enabled", True)).strip().lower() in {"0", "false", "no"}:
        write_json(summary_path, payload)
        return payload

    maker_summary = read_json(out_root / "maker_carry_study.json", default={}) or {}
    if not isinstance(maker_summary, dict):
        maker_summary = {}
    portfolio = _portfolio(maker_summary, _candidate_map(cfg), int(settings["max_markets"]))
    if not portfolio:
        payload.update(
            {
                "status": "no_portfolio",
                "portfolio_markets": 0,
                "simulated_fills": 0,
                "simulated_fills_per_day": 0.0,
                "implied_adverse_usd_per_day": 0.0,
                "realism_ratio": None,
                "note": "No current maker-carry quote-sheet portfolio with token IDs was available to replay.",
            }
        )
        write_json(summary_path, payload)
        return payload

    token_ids = {row["token_id"] for row in portfolio}
    markets = {row["condition_id"] for row in portfolio}
    states_by_token = _book_states(cfg, token_ids, float(settings["replay_days"]))
    trades = _trades(cfg, markets, token_ids)
    if not states_by_token or not trades:
        payload.update(
            {
                "status": "no_replay_data",
                "portfolio_markets": len(portfolio),
                "simulated_fills": 0,
                "simulated_fills_per_day": 0.0,
                "implied_adverse_usd_per_day": 0.0,
                "realism_ratio": None,
                "note": "Recorded book archive or trade prints were absent for the quote-sheet portfolio.",
            }
        )
        write_json(summary_path, payload)
        return payload

    portfolio_by_token = {row["token_id"]: row for row in portfolio}
    fills: list[dict[str, Any]] = []
    for trade in trades:
        entry = portfolio_by_token.get(trade["token_id"])
        if entry is None:
            continue
        states = states_by_token.get(trade["token_id"]) or []
        state = _state_at_or_before(states, trade["stamp"])
        if state is None:
            continue
        bid_quote = state["midpoint"] - float(entry["quote_distance"])
        ask_quote = state["midpoint"] + float(entry["quote_distance"])
        if trade["side"] == "SELL" and trade["price"] <= bid_quote:
            depth_ahead = state["bid_depth"]
            fill_price = bid_quote
            direction = "bid_fill"
        elif trade["side"] == "BUY" and trade["price"] >= ask_quote:
            depth_ahead = state["ask_depth"]
            fill_price = ask_quote
            direction = "ask_fill"
        else:
            continue
        fillable = trade["size"] - depth_ahead
        if fillable <= 0:
            continue
        fill_size = min(float(entry["quote_size_shares"]), fillable)
        if fill_size <= 0:
            continue
        markouts: dict[str, float] = {}
        adverse_usd: dict[str, float] = {}
        for horizon in HORIZONS_MINUTES:
            later = _state_at_or_after(states, trade["stamp"] + horizon * 60.0)
            if later is None:
                continue
            if direction == "bid_fill":
                per_share = fill_price - later["midpoint"]
            else:
                per_share = later["midpoint"] - fill_price
            markouts[f"{horizon}m"] = round(per_share, 6)
            adverse_usd[f"{horizon}m"] = round(per_share * fill_size, 6)
        fills.append(
            {
                "condition_id": entry["condition_id"],
                "token_id": entry["token_id"],
                "direction": direction,
                "stamp": trade["stamp"],
                "fill_price": round(fill_price, 6),
                "fill_size": round(fill_size, 6),
                "depth_ahead": round(depth_ahead, 6),
                "trade_size": trade["size"],
                "markout_per_share": markouts,
                "adverse_usd": adverse_usd,
            }
        )

    all_stamps = [row["stamp"] for rows in states_by_token.values() for row in rows]
    span_days = max((max(all_stamps) - min(all_stamps)) / 86400.0, 1.0 / 1440.0) if all_stamps else 1.0
    adverse_5m = sum((fill.get("adverse_usd") or {}).get("5m", 0.0) for fill in fills)
    implied_adverse = round(adverse_5m / span_days, 6)
    candidates = _candidate_map(cfg)
    study_charge = 0.0
    for entry in portfolio:
        candidate = candidates.get(entry["condition_id"], {})
        charge = safe_float(candidate.get("adverse_selection_usd_per_day")) or 0.0
        size_multiple = safe_float(entry.get("size_multiple")) or 1.0
        study_charge += charge * size_multiple
    realism_ratio = round(implied_adverse / study_charge, 6) if study_charge > 0 else None
    payload.update(
        {
            "status": "ok",
            "portfolio_markets": len(portfolio),
            "replay_days": float(settings["replay_days"]),
            "book_states": sum(len(rows) for rows in states_by_token.values()),
            "trade_prints_seen": len(trades),
            "simulated_fills": len(fills),
            "simulated_fills_per_day": round(len(fills) / span_days, 6),
            "markout_per_fill": {
                f"{horizon}m": _mean(
                    (fill.get("markout_per_share") or {}).get(f"{horizon}m")
                    for fill in fills
                    if (fill.get("markout_per_share") or {}).get(f"{horizon}m") is not None
                )
                for horizon in HORIZONS_MINUTES
            },
            "implied_adverse_usd_per_day": implied_adverse,
            "study_adverse_usd_per_day": round(study_charge, 6),
            "realism_ratio": realism_ratio,
            "fills_preview": fills[:20],
            "note": (
                "Last-in-queue replay against recorded book states. Ratio > 1 means the maker-carry "
                "study may undercharge adverse selection; this report does not alter the study automatically."
            ),
        }
    )
    write_json(summary_path, payload)
    return payload


def main(config_path: str) -> dict[str, Any]:
    return run_maker_fill_replay(load_config(config_path))
