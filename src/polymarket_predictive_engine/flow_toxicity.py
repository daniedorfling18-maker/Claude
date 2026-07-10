"""WO-49 flow-toxicity conditioning.

This is a measurement-only lane for maker-carry risk review:

* VPIN-lite from signed trade-print volume buckets;
* wallet-tier markout split for leaderboard top-100 wallets versus crowd.

The output is a quote-sheet conditioning artifact. It does not modify adverse
selection charges, gates, net carry, sizing, or any order path.
"""
from __future__ import annotations

import csv
import gzip
from bisect import bisect_left, bisect_right
from pathlib import Path
from statistics import mean
from typing import Any

from .config import EngineConfig, load_config
from .utils import now_utc, parse_timestamp, read_csv_rows, safe_float, write_csv, write_json

TOXICITY_FIELDS = [
    "generated_at_utc",
    "market",
    "asset_id",
    "toxicity_score",
    "vpin_raw",
    "volume_buckets",
    "trades_seen",
    "smart_fill_count",
    "crowd_fill_count",
    "smart_fill_markout",
    "crowd_fill_markout",
    "missing_price_points",
]


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = cfg.raw.get("flow_toxicity", {}) if isinstance(cfg.raw.get("flow_toxicity"), dict) else {}
    merged = {
        "enabled": True,
        "volume_bucket_usd": 500,
        "buckets": 50,
        "markout_horizon_minutes": 5,
    }
    merged.update({k: v for k, v in raw.items() if v is not None})
    return merged


def _stamp(value: Any) -> float | None:
    numeric = safe_float(value)
    if numeric is not None:
        return numeric / 1000.0 if numeric > 10_000_000_000 else numeric
    parsed = parse_timestamp(value)
    return parsed.timestamp() if parsed is not None else None


def _wallet(row: dict[str, Any]) -> str:
    for key in ("counterparty_wallet", "wallet", "proxyWallet", "proxy_wallet", "trader", "user"):
        value = str(row.get(key) or "").strip()
        if value:
            return value.lower()
    return ""


def _read_csv_any(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8-sig", errors="replace", newline="") as handle:
            return [{str(k): "" if v is None else str(v) for k, v in row.items()} for row in csv.DictReader(handle)]
    return read_csv_rows(path)


def _top_wallets(cfg: EngineConfig, limit: int = 100) -> tuple[set[str], bool]:
    path = cfg.output_root / "wallet_intelligence" / "leaderboard_history.csv"
    rows = read_csv_rows(path)
    if not rows:
        return set(), True
    latest = max(str(row.get("snapshot_date") or row.get("snapshot_at_utc") or "") for row in rows)
    latest_rows = [row for row in rows if str(row.get("snapshot_date") or row.get("snapshot_at_utc") or "") == latest]
    latest_rows.sort(key=lambda row: safe_float(row.get("rank")) if safe_float(row.get("rank")) is not None else 1e9)
    return {str(row.get("wallet") or "").lower() for row in latest_rows[:limit] if row.get("wallet")}, False


def _feature_rows(cfg: EngineConfig) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    archive = cfg.output_root / "polymarket_training_archive"
    if archive.exists():
        for path in sorted(archive.glob("*.csv.gz")):
            rows.extend(_read_csv_any(path))
    live = cfg.output_root / "polymarket_training" / "websocket_market_features.csv"
    rows.extend(_read_csv_any(live))
    return rows


def _price_series(cfg: EngineConfig) -> dict[str, list[tuple[float, float]]]:
    series: dict[str, list[tuple[float, float]]] = {}
    for row in _feature_rows(cfg):
        token = str(row.get("asset_id") or row.get("token_id") or "").strip()
        stamp = _stamp(row.get("source_timestamp") or row.get("collected_at_utc"))
        midpoint = safe_float(row.get("midpoint"))
        if not token or stamp is None or midpoint is None:
            continue
        series.setdefault(token, []).append((stamp, midpoint))
    for values in series.values():
        values.sort(key=lambda item: item[0])
    return series


def _mid_at_or_after(series: list[tuple[float, float]], stamp: float) -> float | None:
    index = bisect_left(series, (stamp, -1.0))
    return series[index][1] if index < len(series) else None


def _mid_at_or_before(series: list[tuple[float, float]], stamp: float) -> float | None:
    index = bisect_right(series, (stamp, float("inf"))) - 1
    return series[index][1] if index >= 0 else None


def _vpin_raw(trades: list[dict[str, Any]], bucket_usd: float, bucket_count: int) -> tuple[float, int]:
    buckets: list[tuple[float, float]] = []
    signed = 0.0
    volume = 0.0
    for trade in sorted(trades, key=lambda row: row["stamp"]):
        remaining = trade["usd_volume"]
        sign = 1.0 if trade["side"] == "BUY" else -1.0
        while remaining > 0:
            take = min(remaining, bucket_usd - volume)
            signed += sign * take
            volume += take
            remaining -= take
            if volume >= bucket_usd - 1e-9:
                buckets.append((signed, volume))
                signed = 0.0
                volume = 0.0
    if volume > 0:
        buckets.append((signed, volume))
    recent = buckets[-bucket_count:] if bucket_count > 0 else buckets
    if not recent:
        return 0.0, 0
    return round(mean(abs(signed_volume) / max(total_volume, 1e-9) for signed_volume, total_volume in recent), 6), len(recent)


def _percentiles(raw_by_market: dict[str, float]) -> dict[str, float]:
    if not raw_by_market:
        return {}
    ordered = sorted(raw_by_market.items(), key=lambda item: item[1])
    if len(ordered) == 1:
        return {ordered[0][0]: round(ordered[0][1], 6)}
    return {market: round(index / (len(ordered) - 1), 6) for index, (market, _) in enumerate(ordered)}


def _trade_rows(cfg: EngineConfig) -> list[dict[str, Any]]:
    rows = read_csv_rows(cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv")
    parsed: list[dict[str, Any]] = []
    for row in rows:
        market = str(row.get("market") or "").strip()
        token = str(row.get("asset_id") or row.get("token_id") or "").strip()
        price = safe_float(row.get("price"))
        size = safe_float(row.get("size"))
        stamp = _stamp(row.get("timestamp") or row.get("collected_at_utc"))
        side = str(row.get("side") or "").upper()
        if not market or not token or price is None or size is None or stamp is None or side not in {"BUY", "SELL"}:
            continue
        parsed.append(
            {
                "market": market,
                "asset_id": token,
                "price": price,
                "size": size,
                "usd_volume": price * size,
                "stamp": stamp,
                "side": side,
                "wallet": _wallet(row),
            }
        )
    return parsed


def build_flow_toxicity(cfg: EngineConfig) -> dict[str, Any]:
    settings = _settings(cfg)
    out_root = cfg.output_root / "maker_carry"
    path = out_root / "flow_toxicity.csv"
    summary_path = out_root / "flow_toxicity_summary.json"
    generated_at = now_utc()
    summary: dict[str, Any] = {
        "status": "disabled",
        "generated_at_utc": generated_at,
        "work_order": "WO-49",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    if str(settings.get("enabled", True)).strip().lower() in {"0", "false", "no"}:
        write_json(summary_path, summary)
        return summary
    trades = _trade_rows(cfg)
    price_by_token = _price_series(cfg)
    top_wallets, missing_wallet_data = _top_wallets(cfg)
    by_market: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        by_market.setdefault(trade["market"], []).append(trade)
    raw_vpin = {
        market: _vpin_raw(rows, float(settings["volume_bucket_usd"]), int(settings["buckets"]))[0]
        for market, rows in by_market.items()
    }
    toxicity = _percentiles(raw_vpin)
    horizon = float(settings["markout_horizon_minutes"]) * 60.0
    rows_out: list[dict[str, Any]] = []
    for market, rows in sorted(by_market.items()):
        smart_markouts: list[float] = []
        crowd_markouts: list[float] = []
        missing_prices = 0
        asset_id = rows[0]["asset_id"] if rows else ""
        for trade in rows:
            series = price_by_token.get(trade["asset_id"]) or []
            later = _mid_at_or_after(series, trade["stamp"] + horizon)
            if later is None:
                missing_prices += 1
                continue
            if trade["side"] == "BUY":
                markout = later - trade["price"]
            else:
                markout = trade["price"] - later
            if trade["wallet"] and trade["wallet"] in top_wallets:
                smart_markouts.append(markout)
            else:
                crowd_markouts.append(markout)
        _, bucket_n = _vpin_raw(rows, float(settings["volume_bucket_usd"]), int(settings["buckets"]))
        rows_out.append(
            {
                "generated_at_utc": generated_at,
                "market": market,
                "asset_id": asset_id,
                "toxicity_score": toxicity.get(market, 0.0),
                "vpin_raw": raw_vpin.get(market, 0.0),
                "volume_buckets": bucket_n,
                "trades_seen": len(rows),
                "smart_fill_count": len(smart_markouts),
                "crowd_fill_count": len(crowd_markouts),
                "smart_fill_markout": round(mean(smart_markouts), 6) if smart_markouts else None,
                "crowd_fill_markout": round(mean(crowd_markouts), 6) if crowd_markouts else None,
                "missing_price_points": missing_prices,
            }
        )
    write_csv(path, rows_out, fieldnames=TOXICITY_FIELDS)
    summary.update(
        {
            "status": "ok" if rows_out or not trades else "no_trades",
            "markets_scored": len(rows_out),
            "trades_seen": len(trades),
            "missing_wallet_data": missing_wallet_data,
            "max_toxicity_score": max([safe_float(row.get("toxicity_score")) or 0.0 for row in rows_out], default=0.0),
            "output_path": str(path),
            "note": (
                "Flow-toxicity is quote-sheet conditioning only. It does not modify maker adverse charges, "
                "net carry, gates, sizing, or any order path."
            ),
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        }
    )
    write_json(summary_path, summary)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return build_flow_toxicity(load_config(config_path))
