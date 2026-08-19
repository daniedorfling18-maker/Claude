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
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from statistics import mean
from tempfile import TemporaryDirectory
from typing import Any

from .config import EngineConfig, load_config
from .utils import normalize_external_timestamp, now_utc, read_csv_rows, safe_float, write_csv, write_json

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
    "raw_imbalance_block",
    "percentile_block",
    "markout_coverage_ratio",
    "toxic_blocked",
    "toxicity_block_reasons",
]

# Wallet-axis markouts.
#
# The market-axis table above answers "is this market's flow toxic". It cannot
# answer "which wallets actually predict", because it discards the wallet the
# moment it classifies a fill as smart or crowd. That classification is itself
# the constraint: _top_wallets resolves "smart" to the LATEST snapshot of
# leaderboard_history.csv capped at 100 wallets, and the mirror holds 200 rows
# across 2 snapshots naming the same 100 wallets. So of 475 markets scored from
# 200,000 fills, only 16 ever produce a smart-fill markout - not because prices
# are missing (176 markets have coverage) but because a fill can only be smart
# if it belongs to one of a hundred wallets on a public PnL leaderboard.
#
# A PnL/volume ranking is not a measure of prediction. This table publishes the
# empirical alternative the data already supports: forward markout per wallet,
# split into an earlier ranking window and a later evaluation window so a wallet
# can be ranked on one and judged on the other. Diagnostic only - no gate,
# sizing or order surface reads it.
WALLET_MARKOUT_FIELDS = [
    "generated_at_utc",
    "wallet",
    "on_current_leaderboard",
    "fills_total",
    "markout_mean_total",
    "fills_ranking_window",
    "markout_mean_ranking_window",
    "fills_evaluation_window",
    "markout_mean_evaluation_window",
    "fills_split_spanning",
    "fills_stale_price_excluded",
    "markets_touched",
    # AGENTS.md artifact-level provenance invariant: every NEW artifact states
    # both flags itself; the summary's copy does not satisfy it.
    "paper_trading_invoked",
    "live_trading_invoked",
]

# WO-102 (2026-07-17): the historical toxicity_score is a UNIVERSE-RELATIVE
# percentile (index / (n-1)). A genuinely one-sided market can silently fall
# BELOW the standing-rule-8 percentile threshold simply because more calm
# markets were measured alongside it that day -- de-vetoing a toxic market
# with no change in its own flow. This adds an ABSOLUTE, universe-independent
# raw-imbalance floor so the veto cannot drift. The composite screen is
# strictly TIGHTEN-ONLY: a market is blocked if the percentile rule OR the
# absolute floor fires. It never clears a market the old rule blocked.
REGISTERED_RAW_IMBALANCE_FLOOR = 0.90
REGISTERED_PERCENTILE_BLOCK = 0.90


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
    return normalize_external_timestamp(value)


def _wallet(row: dict[str, Any]) -> str:
    for key in ("counterparty_wallet", "wallet", "proxyWallet", "proxy_wallet", "trader", "user"):
        value = str(row.get(key) or "").strip()
        if value:
            return value.lower()
    return ""


def _iter_csv_any(path: Path) -> Iterator[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8-sig", errors="replace", newline="") as handle:
            for row in csv.DictReader(handle):
                yield {str(k): "" if v is None else str(v) for k, v in row.items()}
        return
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        for row in csv.DictReader(handle):
            yield {str(k): "" if v is None else str(v) for k, v in row.items()}


def _top_wallets(cfg: EngineConfig, limit: int = 100) -> tuple[set[str], bool]:
    path = cfg.output_root / "wallet_intelligence" / "leaderboard_history.csv"
    rows = read_csv_rows(path)
    if not rows:
        return set(), True
    latest = max(str(row.get("snapshot_date") or row.get("snapshot_at_utc") or "") for row in rows)
    latest_rows = [row for row in rows if str(row.get("snapshot_date") or row.get("snapshot_at_utc") or "") == latest]
    latest_rows.sort(key=lambda row: safe_float(row.get("rank")) if safe_float(row.get("rank")) is not None else 1e9)
    return {str(row.get("wallet") or "").lower() for row in latest_rows[:limit] if row.get("wallet")}, False


def _feature_paths(cfg: EngineConfig) -> Iterator[Path]:
    archive = cfg.output_root / "polymarket_training_archive"
    if archive.exists():
        for path in sorted(archive.glob("*.csv.gz")):
            yield path
    live = cfg.output_root / "polymarket_training" / "websocket_market_features.csv"
    if live.exists():
        yield live


def _price_target_bounds(
    trades: list[dict[str, Any]],
    horizon_seconds: float,
) -> dict[str, tuple[float, float]]:
    bounds: dict[str, tuple[float, float]] = {}
    for trade in trades:
        token = str(trade["asset_id"])
        target = float(trade["stamp"]) + horizon_seconds
        current = bounds.get(token)
        if current is None:
            bounds[token] = (target, target)
        else:
            bounds[token] = (min(current[0], target), max(current[1], target))
    return bounds


def _build_price_index(
    cfg: EngineConfig,
    connection: sqlite3.Connection,
    target_bounds: dict[str, tuple[float, float]],
) -> tuple[int, int]:
    """Stream feature corpora into a bounded, disk-backed lookup index.

    Only points inside a token's required markout interval are retained. One
    earliest tail point is also kept so the final trade target can still be
    marked when its next observation falls after the interval. This preserves
    the original first-midpoint-at-or-after lookup without holding every
    decompressed archive row in RAM.
    """

    connection.executescript(
        """
        PRAGMA journal_mode = OFF;
        PRAGMA synchronous = OFF;
        PRAGMA temp_store = FILE;
        PRAGMA cache_size = -32768;
        PRAGMA mmap_size = 0;
        CREATE TABLE feature_prices (
            asset_id TEXT NOT NULL,
            stamp REAL NOT NULL,
            midpoint REAL NOT NULL,
            source_order INTEGER NOT NULL
        );
        """
    )
    batch: list[tuple[str, float, float, int]] = []
    tail_candidates: dict[str, tuple[float, float, int]] = {}
    scanned_rows = 0
    indexed_rows = 0

    def flush() -> None:
        nonlocal indexed_rows
        if not batch:
            return
        connection.executemany(
            "INSERT INTO feature_prices(asset_id, stamp, midpoint, source_order) VALUES (?, ?, ?, ?)",
            batch,
        )
        indexed_rows += len(batch)
        batch.clear()

    for path in _feature_paths(cfg):
        for row in _iter_csv_any(path):
            scanned_rows += 1
            token = str(row.get("asset_id") or row.get("token_id") or "").strip()
            bounds = target_bounds.get(token)
            if bounds is None:
                continue
            stamp = _stamp(row.get("source_timestamp") or row.get("collected_at_utc"))
            midpoint = safe_float(row.get("midpoint"))
            if stamp is None or midpoint is None or stamp < bounds[0]:
                continue
            source_order = scanned_rows
            if stamp <= bounds[1]:
                batch.append((token, stamp, midpoint, source_order))
                if len(batch) >= 10_000:
                    flush()
                continue
            candidate = (stamp, midpoint, source_order)
            current = tail_candidates.get(token)
            if current is None or candidate < current:
                tail_candidates[token] = candidate

    for token, (stamp, midpoint, source_order) in tail_candidates.items():
        batch.append((token, stamp, midpoint, source_order))
    flush()
    connection.commit()
    connection.execute(
        "CREATE INDEX feature_prices_token_stamp_idx "
        "ON feature_prices(asset_id, stamp, midpoint, source_order)"
    )
    connection.commit()
    return scanned_rows, indexed_rows


def _markout_stats(
    connection: sqlite3.Connection,
    trades: list[dict[str, Any]],
    top_wallets: set[str],
    horizon_seconds: float,
) -> dict[str, dict[str, float | int]]:
    trades_by_token: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        trades_by_token.setdefault(str(trade["asset_id"]), []).append(trade)

    stats: dict[str, dict[str, float | int]] = {}
    wallet_stats: dict[str, dict[str, Any]] = {}
    # Median fill time splits the sample chronologically, and the split is by
    # WHOLE MARKET, not by fill: a wallet trading one market on both sides of
    # the median would let market-specific effects observed during ranking
    # reappear in the evaluation window (Codex P1 on #451; AGENTS.md requires
    # validation chronological and out-of-sample BY MARKET). A market whose
    # fills span the split belongs to NEITHER window - excluded fail-closed and
    # disclosed per wallet as fills_split_spanning, never silently scored.
    stamps = sorted(float(trade["stamp"]) for trade in trades)
    split_stamp = stamps[len(stamps) // 2] if stamps else 0.0
    market_bounds: dict[str, tuple[float, float]] = {}
    for trade in trades:
        market_key = str(trade["market"])
        stamp = float(trade["stamp"])
        low, high = market_bounds.get(market_key, (stamp, stamp))
        market_bounds[market_key] = (min(low, stamp), max(high, stamp))
    market_window: dict[str, str] = {}
    for market_key, (low, high) in market_bounds.items():
        if high < split_stamp:
            market_window[market_key] = "ranking"
        elif low >= split_stamp:
            market_window[market_key] = "evaluation"
        else:
            market_window[market_key] = "spanning"
    # A markout meant to measure the configured horizon, read from a price more
    # than one horizon late, measures a different horizon (Codex P1 on #451:
    # without a ceiling, the first observation hours later still scored). The
    # wallet axis accepts a price only inside [target, target + horizon] and
    # otherwise counts the fill as stale-excluded. The market-axis smart/crowd
    # columns keep their long-standing WO-49 lookup unchanged - tightening a
    # registered artifact is its own change, not a rider on this one.
    staleness_tolerance = horizon_seconds
    for token, token_trades in trades_by_token.items():
        feature_rows = iter(
            connection.execute(
                "SELECT stamp, midpoint FROM feature_prices "
                "WHERE asset_id = ? ORDER BY stamp, midpoint, source_order",
                (token,),
            )
        )
        current_feature = next(feature_rows, None)
        for trade in sorted(token_trades, key=lambda row: float(row["stamp"])):
            target = float(trade["stamp"]) + horizon_seconds
            while current_feature is not None and float(current_feature[0]) < target:
                current_feature = next(feature_rows, None)
            market = str(trade["market"])
            market_stats = stats.setdefault(
                market,
                {
                    "smart_count": 0,
                    "smart_sum": 0.0,
                    "crowd_count": 0,
                    "crowd_sum": 0.0,
                    "missing_prices": 0,
                },
            )
            if current_feature is None:
                market_stats["missing_prices"] += 1
                continue
            later = float(current_feature[1])
            markout = later - float(trade["price"])
            if trade["side"] == "SELL":
                markout = -markout
            tier = "smart" if trade["wallet"] and trade["wallet"] in top_wallets else "crowd"
            market_stats[f"{tier}_count"] += 1
            market_stats[f"{tier}_sum"] += markout
            wallet = str(trade["wallet"] or "").strip().lower()
            if wallet:
                entry = wallet_stats.setdefault(
                    wallet,
                    {
                        "fills_total": 0,
                        "markout_total": 0.0,
                        "fills_ranking": 0,
                        "markout_ranking": 0.0,
                        "fills_evaluation": 0,
                        "markout_evaluation": 0.0,
                        "fills_split_spanning": 0,
                        "fills_stale_price_excluded": 0,
                        "markets": set(),
                    },
                )
                entry["markets"].add(market)
                if float(current_feature[0]) - target > staleness_tolerance:
                    entry["fills_stale_price_excluded"] += 1
                    continue
                entry["fills_total"] += 1
                entry["markout_total"] += markout
                window = market_window.get(market, "spanning")
                if window == "spanning":
                    entry["fills_split_spanning"] += 1
                else:
                    entry[f"fills_{window}"] += 1
                    entry[f"markout_{window}"] += markout
    return stats, wallet_stats


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
    parsed: list[dict[str, Any]] = []
    path = cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv"
    for row in _iter_csv_any(path):
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
    markout_by_market: dict[str, dict[str, float | int]] = {}
    markout_by_wallet: dict[str, dict[str, Any]] = {}
    feature_rows_scanned = 0
    feature_rows_indexed = 0
    price_index_disk_bytes = 0
    if trades:
        with TemporaryDirectory(prefix="polymarket-flow-toxicity-") as temp_dir:
            database_path = Path(temp_dir) / "price_index.sqlite3"
            connection = sqlite3.connect(database_path)
            try:
                feature_rows_scanned, feature_rows_indexed = _build_price_index(
                    cfg,
                    connection,
                    _price_target_bounds(trades, horizon),
                )
                markout_by_market, markout_by_wallet = _markout_stats(connection, trades, top_wallets, horizon)
                price_index_disk_bytes = database_path.stat().st_size
            finally:
                connection.close()
    rows_out: list[dict[str, Any]] = []
    for market, rows in sorted(by_market.items()):
        markouts = markout_by_market.get(market, {})
        smart_count = int(markouts.get("smart_count", 0))
        smart_sum = float(markouts.get("smart_sum", 0.0))
        crowd_count = int(markouts.get("crowd_count", 0))
        crowd_sum = float(markouts.get("crowd_sum", 0.0))
        missing_prices = int(markouts.get("missing_prices", len(rows)))
        asset_id = rows[0]["asset_id"] if rows else ""
        _, bucket_n = _vpin_raw(rows, float(settings["volume_bucket_usd"]), int(settings["buckets"]))
        market_vpin = raw_vpin.get(market, 0.0)
        market_percentile = toxicity.get(market, 0.0)
        measured_markouts = smart_count + crowd_count
        coverage_ratio = round(measured_markouts / len(rows), 6) if rows else 0.0
        raw_block = market_vpin >= REGISTERED_RAW_IMBALANCE_FLOOR
        pct_block = market_percentile > REGISTERED_PERCENTILE_BLOCK
        reasons = []
        if raw_block:
            reasons.append(f"raw_imbalance>={REGISTERED_RAW_IMBALANCE_FLOOR:g}")
        if pct_block:
            reasons.append(f"percentile>{REGISTERED_PERCENTILE_BLOCK:g}")
        rows_out.append(
            {
                "generated_at_utc": generated_at,
                "market": market,
                "asset_id": asset_id,
                "toxicity_score": market_percentile,
                "vpin_raw": market_vpin,
                "volume_buckets": bucket_n,
                "trades_seen": len(rows),
                "smart_fill_count": smart_count,
                "crowd_fill_count": crowd_count,
                "smart_fill_markout": round(smart_sum / smart_count, 6) if smart_count else None,
                "crowd_fill_markout": round(crowd_sum / crowd_count, 6) if crowd_count else None,
                "missing_price_points": missing_prices,
                "raw_imbalance_block": raw_block,
                "percentile_block": pct_block,
                "markout_coverage_ratio": coverage_ratio,
                "toxic_blocked": raw_block or pct_block,
                "toxicity_block_reasons": ";".join(reasons),
            }
        )
    write_csv(path, rows_out, fieldnames=TOXICITY_FIELDS)

    def _mean(total: float, count: int) -> float | None:
        return round(total / count, 6) if count else None

    wallet_rows = [
        {
            "generated_at_utc": generated_at,
            "wallet": wallet,
            "on_current_leaderboard": wallet in top_wallets,
            "fills_total": entry["fills_total"],
            "markout_mean_total": _mean(entry["markout_total"], entry["fills_total"]),
            "fills_ranking_window": entry["fills_ranking"],
            "markout_mean_ranking_window": _mean(entry["markout_ranking"], entry["fills_ranking"]),
            "fills_evaluation_window": entry["fills_evaluation"],
            "markout_mean_evaluation_window": _mean(entry["markout_evaluation"], entry["fills_evaluation"]),
            "fills_split_spanning": entry["fills_split_spanning"],
            "fills_stale_price_excluded": entry["fills_stale_price_excluded"],
            "markets_touched": len(entry["markets"]),
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        }
        for wallet, entry in sorted(markout_by_wallet.items(), key=lambda item: -item[1]["fills_total"])
    ]
    wallet_path = cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv"
    write_csv(wallet_path, wallet_rows, fieldnames=WALLET_MARKOUT_FIELDS)
    summary.update(
        {
            "status": "ok" if rows_out or not trades else "no_trades",
            "markets_scored": len(rows_out),
            "wallets_scored": len(wallet_rows),
            # Published so the sample is visible before any ranking is trusted:
            # a wallet ranked on the earlier window must be judged on the later
            # one, and a wallet present in only one window cannot be judged at all.
            "wallets_in_both_windows": sum(
                1 for row in wallet_rows if row["fills_ranking_window"] and row["fills_evaluation_window"]
            ),
            "wallet_output_path": str(wallet_path),
            "trades_seen": len(trades),
            "missing_wallet_data": missing_wallet_data,
            "price_index_strategy": "disk_backed_streaming_sqlite",
            "feature_rows_scanned": feature_rows_scanned,
            "feature_rows_indexed": feature_rows_indexed,
            "price_index_disk_bytes": price_index_disk_bytes,
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
