from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from .config import EngineConfig, load_config
from .utils import now_utc, parse_timestamp, read_csv_rows, read_json, safe_float, write_csv, write_json

HISTORY_FIELDS = [
    "anchor_generated_at_utc",
    "recorded_at_utc",
    "sport",
    "market_key",
    "rows_fetched",
    "rows_mapped",
    "join_rate",
    "direct_token_joins",
    "token_map_joins",
    "h2h_public_search_token_joins",
    "worldcup_winner_token_joins",
    "advance_composite_token_joins",
    "skipped_no_token",
    "zero_join_streak",
    "classification",
    "recommendation",
]


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    return cfg.raw.get("sharp_anchor_coverage", {}) or {}


def _split_markets(value: Any, default: Any = "h2h") -> list[str]:
    raw = value if value not in (None, "") else default
    if isinstance(raw, (list, tuple, set)):
        parts = raw
    else:
        parts = str(raw).split(",")
    out: list[str] = []
    for part in parts:
        text = str(part or "").strip().lower()
        if text and text not in out:
            out.append(text)
    return out or ["h2h"]


def _configured_sport_markets(cfg: EngineConfig, fetch_summary: Mapping[str, Any]) -> list[tuple[str, str]]:
    fetch_settings = cfg.raw.get("sharp_odds_fetch", {}) or {}
    default_markets = fetch_settings.get("markets", "h2h")
    raw_configs = fetch_summary.get("configured_sports") if isinstance(fetch_summary, Mapping) else None
    if not isinstance(raw_configs, list) or not raw_configs:
        raw_configs = fetch_settings.get("sports", []) or []
    out: list[tuple[str, str]] = []
    for item in raw_configs:
        if isinstance(item, Mapping):
            sport = str(item.get("sport") or item.get("key") or item.get("sport_key") or "").strip()
            markets = _split_markets(item.get("markets", item.get("market", default_markets)), default_markets)
        else:
            sport = str(item or "").strip()
            markets = _split_markets(default_markets, "h2h")
        if not sport:
            continue
        for market in markets:
            key = (sport, market)
            if key not in out:
                out.append(key)
    return out


def _coverage_by_sport_market(anchor_summary: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    rows = anchor_summary.get("coverage_by_sport_market") if isinstance(anchor_summary, Mapping) else []
    rows = rows if isinstance(rows, list) else []
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        sport = str(row.get("sport") or "unknown").strip() or "unknown"
        market_key = str(row.get("market_key") or "unknown").strip() or "unknown"
        key = (sport, market_key)
        bucket = buckets.setdefault(
            key,
            {
                "sport": sport,
                "market_key": market_key,
                "rows_in": 0,
                "priced_rows": 0,
                "fundamental_rows": 0,
                "skipped_no_token": 0,
                "direct_token_joins": 0,
                "token_map_joins": 0,
                "h2h_public_search_token_joins": 0,
                "worldcup_winner_token_joins": 0,
                "advance_composite_token_joins": 0,
            },
        )
        for field in (
            "rows_in",
            "priced_rows",
            "fundamental_rows",
            "skipped_no_token",
            "direct_token_joins",
            "token_map_joins",
            "h2h_public_search_token_joins",
            "worldcup_winner_token_joins",
            "advance_composite_token_joins",
        ):
            bucket[field] += int(safe_float(row.get(field)) or 0)
    return buckets


def _history_path(cfg: EngineConfig) -> Path:
    return cfg.governance_root / "sharp_anchor_coverage_history.csv"


def _summary_path(cfg: EngineConfig) -> Path:
    return cfg.governance_root / "sharp_anchor_coverage.json"


def _row_key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("anchor_generated_at_utc") or ""),
        str(row.get("sport") or ""),
        str(row.get("market_key") or ""),
    )


def _sort_key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    parsed = parse_timestamp(row.get("anchor_generated_at_utc"))
    sortable_time = parsed.isoformat() if parsed is not None else str(row.get("anchor_generated_at_utc") or "")
    return sortable_time, str(row.get("sport") or ""), str(row.get("market_key") or "")


def _zero_join_streak(history: Iterable[Mapping[str, Any]], sport: str, market_key: str) -> int:
    rows = [
        row
        for row in history
        if str(row.get("sport") or "") == sport and str(row.get("market_key") or "") == market_key
    ]
    rows.sort(key=_sort_key, reverse=True)
    streak = 0
    for row in rows:
        fetched = int(safe_float(row.get("rows_fetched")) or 0)
        mapped = int(safe_float(row.get("rows_mapped")) or 0)
        if fetched <= 0:
            continue
        if mapped > 0:
            break
        streak += 1
    return streak


def _classify(rows_fetched: int, rows_mapped: int, zero_join_streak: int, threshold: int) -> tuple[str, str]:
    if rows_mapped > 0:
        return "mappable", "Keep fetching; this sport/market currently maps into Polymarket tokens."
    if rows_fetched <= 0:
        return "collecting_coverage_evidence", "No fetched rows this run; keep collecting before trimming."
    if zero_join_streak >= threshold:
        return (
            "no_mappable_market",
            "Recommendation only: consider trimming this sport/market from sharp_odds_fetch unless a new Polymarket market appears.",
        )
    return (
        "collecting_coverage_evidence",
        f"Fetched rows but mapped zero tokens; need {max(threshold - zero_join_streak, 0)} more zero-join fetched cycle(s) before flagging.",
    )


def build_sharp_anchor_coverage(cfg: EngineConfig) -> dict[str, Any]:
    """Reconcile sharp-odds fetch coverage with actual Polymarket token joins.

    This is a reporting-only guardrail. It never edits config and never authorises paper/live
    trading. A ``no_mappable_market`` classification is only a recommendation string for a human
    or coding agent to trim wasteful fetches deliberately.
    """

    settings = _settings(cfg)
    threshold = max(1, int(safe_float(settings.get("zero_join_cycles_before_flag")) or 5))
    anchor_summary = read_json(cfg.governance_root / "sharp_anchor_summary.json", default={}) or {}
    fetch_summary = read_json(cfg.governance_root / "sharp_odds_fetch_summary.json", default={}) or {}
    if not isinstance(anchor_summary, dict):
        anchor_summary = {}
    if not isinstance(fetch_summary, dict):
        fetch_summary = {}

    anchor_generated_at = str(anchor_summary.get("generated_at_utc") or "")
    coverage = _coverage_by_sport_market(anchor_summary)
    configured = _configured_sport_markets(cfg, fetch_summary)
    keys = list(configured)
    for key in coverage:
        if key not in keys:
            keys.append(key)

    now = now_utc()
    existing_history = read_csv_rows(_history_path(cfg))
    existing_keys = {_row_key(row) for row in existing_history}
    current_history_rows: list[dict[str, Any]] = []
    per_market: list[dict[str, Any]] = []

    if anchor_generated_at and keys:
        for sport, market_key in keys:
            bucket = coverage.get((sport, market_key), {})
            rows_fetched = int(safe_float(bucket.get("rows_in")) or 0)
            rows_mapped = int(safe_float(bucket.get("fundamental_rows")) or 0)
            join_rate = round(rows_mapped / rows_fetched, 6) if rows_fetched > 0 else None
            current_history_rows.append(
                {
                    "anchor_generated_at_utc": anchor_generated_at,
                    "recorded_at_utc": now,
                    "sport": sport,
                    "market_key": market_key,
                    "rows_fetched": rows_fetched,
                    "rows_mapped": rows_mapped,
                    "join_rate": "" if join_rate is None else join_rate,
                    "direct_token_joins": int(safe_float(bucket.get("direct_token_joins")) or 0),
                    "token_map_joins": int(safe_float(bucket.get("token_map_joins")) or 0),
                    "h2h_public_search_token_joins": int(safe_float(bucket.get("h2h_public_search_token_joins")) or 0),
                    "worldcup_winner_token_joins": int(safe_float(bucket.get("worldcup_winner_token_joins")) or 0),
                    "advance_composite_token_joins": int(safe_float(bucket.get("advance_composite_token_joins")) or 0),
                    "skipped_no_token": int(safe_float(bucket.get("skipped_no_token")) or 0),
                    "zero_join_streak": 0,
                    "classification": "pending",
                    "recommendation": "pending",
                }
            )

    merged_history = list(existing_history)
    appended_rows = 0
    for row in current_history_rows:
        if _row_key(row) in existing_keys:
            continue
        merged_history.append(row)
        appended_rows += 1

    for sport, market_key in keys:
        bucket = coverage.get((sport, market_key), {})
        rows_fetched = int(safe_float(bucket.get("rows_in")) or 0)
        rows_mapped = int(safe_float(bucket.get("fundamental_rows")) or 0)
        join_rate = round(rows_mapped / rows_fetched, 6) if rows_fetched > 0 else None
        streak = _zero_join_streak(merged_history, sport, market_key)
        classification, recommendation = _classify(rows_fetched, rows_mapped, streak, threshold)
        for row in merged_history:
            if _row_key(row) == (anchor_generated_at, sport, market_key):
                row["zero_join_streak"] = streak
                row["classification"] = classification
                row["recommendation"] = recommendation
        per_market.append(
            {
                "sport": sport,
                "market_key": market_key,
                "rows_fetched": rows_fetched,
                "rows_mapped": rows_mapped,
                "join_rate": join_rate,
                "zero_join_streak": streak,
                "classification": classification,
                "recommendation": recommendation,
                "direct_token_joins": int(safe_float(bucket.get("direct_token_joins")) or 0),
                "token_map_joins": int(safe_float(bucket.get("token_map_joins")) or 0),
                "h2h_public_search_token_joins": int(safe_float(bucket.get("h2h_public_search_token_joins")) or 0),
                "worldcup_winner_token_joins": int(safe_float(bucket.get("worldcup_winner_token_joins")) or 0),
                "advance_composite_token_joins": int(safe_float(bucket.get("advance_composite_token_joins")) or 0),
                "skipped_no_token": int(safe_float(bucket.get("skipped_no_token")) or 0),
            }
        )

    per_market.sort(key=lambda row: (row["classification"] != "no_mappable_market", row["sport"], row["market_key"]))
    merged_history.sort(key=_sort_key)
    if anchor_generated_at and keys:
        write_csv(_history_path(cfg), merged_history, fieldnames=HISTORY_FIELDS)

    flagged = [row for row in per_market if row["classification"] == "no_mappable_market"]
    status = "missing_anchor_summary" if not anchor_summary else "ok"
    payload = {
        "status": status,
        "generated_at_utc": now,
        "anchor_generated_at_utc": anchor_generated_at,
        "history_path": str(_history_path(cfg)),
        "history_rows": len(merged_history),
        "history_rows_appended": appended_rows,
        "zero_join_cycles_before_flag": threshold,
        "configured_sport_markets": [
            {"sport": sport, "market_key": market_key} for sport, market_key in configured
        ],
        "sport_markets": per_market,
        "flagged_no_mappable_market": flagged,
        "flagged_no_mappable_market_count": len(flagged),
        "mappable_count": sum(1 for row in per_market if row["classification"] == "mappable"),
        "collecting_count": sum(1 for row in per_market if row["classification"] == "collecting_coverage_evidence"),
        "total_rows_fetched": sum(int(row["rows_fetched"]) for row in per_market),
        "total_rows_mapped": sum(int(row["rows_mapped"]) for row in per_market),
        "decision_use": "coverage_reconciliation_only_not_config_edit_or_trade_authorisation",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    write_json(_summary_path(cfg), payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build sharp-anchor per-sport coverage reconciliation.")
    parser.add_argument("--config", default="polymarket_predictive_config.example.yaml")
    args = parser.parse_args(argv)
    print(json.dumps(build_sharp_anchor_coverage(load_config(args.config)), indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
