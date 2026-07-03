"""Websocket collection coverage diagnostics.

CLV and attribution only become decision-useful when we have quotes near the
times that matter. This module reports two things:

* family-level quote coverage from ``websocket_market_features.csv``;
* shadow positions that do not have a quote in the configured window before
  ``close_time`` and would therefore remain provisional for CLV.

It is a collection scheduler input only. It never trades and never relaxes
promotion gates.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from .config import EngineConfig, load_config
from .utils import git_commit_hash, now_utc, parse_timestamp, read_csv_rows, write_csv, write_json
from .worldcup_validation import classify_market_family

FAMILY_FIELDS = [
    "family",
    "quote_rows",
    "distinct_assets",
    "assets_with_gap_samples",
    "first_quote_timestamp",
    "last_quote_timestamp",
    "median_gap_seconds",
]

MISSING_FIELDS = [
    "shadow_position_id",
    "family",
    "signal_cohort",
    "market_slug",
    "token_id",
    "close_time",
    "reason",
]


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    return cfg.raw.get("collection_coverage", {}) or {}


def _quote_time(row: dict[str, Any]) -> datetime | None:
    return (
        parse_timestamp(row.get("source_timestamp"))
        or parse_timestamp(row.get("collected_at_utc"))
        or parse_timestamp(row.get("prediction_timestamp"))
    )


def _asset_id(row: dict[str, Any]) -> str:
    return str(row.get("asset_id") or row.get("token_id") or "").strip()


def _family(row: dict[str, Any]) -> str:
    return classify_market_family(row) or "unknown"


def _iso(dt: datetime | None) -> str:
    if dt is None:
        return ""
    dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _median(values: list[float]) -> float | None:
    return round(float(median(values)), 6) if values else None


def _family_coverage_rows(quote_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, list[datetime]]]:
    family_assets: dict[str, dict[str, list[datetime]]] = defaultdict(lambda: defaultdict(list))
    quote_times_by_asset: dict[str, list[datetime]] = defaultdict(list)
    for row in quote_rows:
        asset = _asset_id(row)
        when = _quote_time(row)
        if not asset or when is None:
            continue
        family = _family(row)
        family_assets[family][asset].append(when)
        quote_times_by_asset[asset].append(when)

    out: list[dict[str, Any]] = []
    for family, assets in sorted(family_assets.items()):
        all_times: list[datetime] = []
        asset_median_gaps: list[float] = []
        for times in assets.values():
            times.sort()
            all_times.extend(times)
            gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:]) if (b - a).total_seconds() >= 0]
            if gaps:
                asset_median_gaps.append(float(median(gaps)))
        out.append(
            {
                "family": family,
                "quote_rows": sum(len(times) for times in assets.values()),
                "distinct_assets": len(assets),
                "assets_with_gap_samples": len(asset_median_gaps),
                "first_quote_timestamp": _iso(min(all_times) if all_times else None),
                "last_quote_timestamp": _iso(max(all_times) if all_times else None),
                "median_gap_seconds": _median(asset_median_gaps),
            }
        )
    for times in quote_times_by_asset.values():
        times.sort()
    return out, dict(quote_times_by_asset)


def _has_pre_close_quote(
    token_id: str,
    close_time: datetime,
    quote_times_by_asset: dict[str, list[datetime]],
    *,
    pre_close_window: timedelta,
) -> bool:
    window_start = close_time - pre_close_window
    return any(window_start <= when <= close_time for when in quote_times_by_asset.get(token_id, []))


def _missing_position_row(position: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "shadow_position_id": position.get("shadow_position_id", ""),
        "family": _family(position),
        "signal_cohort": position.get("signal_cohort", "") or "unknown",
        "market_slug": position.get("market_slug", ""),
        "token_id": position.get("token_id", ""),
        "close_time": position.get("close_time", ""),
        "reason": reason,
    }


def _position_coverage(
    positions: list[dict[str, Any]],
    quote_times_by_asset: dict[str, list[datetime]],
    *,
    pre_close_window: timedelta,
) -> tuple[int, list[dict[str, Any]]]:
    covered = 0
    missing: list[dict[str, Any]] = []
    for position in positions:
        token_id = str(position.get("token_id") or "").strip()
        close_time = parse_timestamp(position.get("close_time"))
        if not token_id:
            missing.append(_missing_position_row(position, "missing_token_id"))
            continue
        if close_time is None:
            missing.append(_missing_position_row(position, "missing_close_time"))
            continue
        if _has_pre_close_quote(token_id, close_time, quote_times_by_asset, pre_close_window=pre_close_window):
            covered += 1
        else:
            missing.append(_missing_position_row(position, "missing_pre_close_quote"))
    missing.sort(
        key=lambda row: (
            str(row.get("close_time") or ""),
            str(row.get("family") or ""),
            str(row.get("shadow_position_id") or ""),
        )
    )
    return covered, missing


def build_collection_coverage(cfg: EngineConfig) -> dict[str, Any]:
    settings = _settings(cfg)
    pre_close_window_minutes = int(settings.get("pre_close_window_minutes", 30))
    pre_close_window = timedelta(minutes=pre_close_window_minutes)

    quote_rows = read_csv_rows(cfg.output_root / "polymarket_training" / "websocket_market_features.csv")
    positions = read_csv_rows(cfg.output_root / "polymarket_shadow" / "shadow_positions.csv")
    family_rows, quote_times_by_asset = _family_coverage_rows(quote_rows)
    covered_count, missing_positions = _position_coverage(
        positions,
        quote_times_by_asset,
        pre_close_window=pre_close_window,
    )
    missing_reason_counts = Counter(str(row.get("reason") or "unknown") for row in missing_positions)
    missing_family_counts = Counter(str(row.get("family") or "unknown") for row in missing_positions)

    payload = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "git_commit": git_commit_hash(),
        "pre_close_window_minutes": pre_close_window_minutes,
        "quote_rows_seen": len(quote_rows),
        "quote_assets_seen": len(quote_times_by_asset),
        "families": family_rows,
        "shadow_positions_seen": len(positions),
        "positions_with_pre_close_quote": covered_count,
        "positions_missing_pre_close_quote": len(missing_positions),
        "missing_pre_close_positions": missing_positions,
        "missing_by_reason": dict(missing_reason_counts),
        "missing_by_family": dict(missing_family_counts),
        "governance_note": "Collection coverage is a scheduler diagnostic; it does not authorise paper or live trading.",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    write_json(cfg.governance_root / "collection_coverage.json", payload)
    write_csv(cfg.governance_root / "collection_coverage_families.csv", family_rows, fieldnames=FAMILY_FIELDS)
    write_csv(cfg.governance_root / "collection_coverage_missing_positions.csv", missing_positions, fieldnames=MISSING_FIELDS)
    return payload


def main(config_path: str) -> dict[str, Any]:
    return build_collection_coverage(load_config(config_path))
