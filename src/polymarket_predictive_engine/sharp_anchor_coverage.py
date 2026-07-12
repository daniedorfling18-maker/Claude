from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .config import EngineConfig, load_config
from .utils import now_utc, parse_timestamp, read_csv_rows, read_json, safe_float, write_csv, write_json
from .worldcup_validation import classify_market_family

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

FUNNEL_HISTORY_FIELDS = [
    "anchor_generated_at_utc",
    "recorded_at_utc",
    "source",
    "sport",
    "market_key",
    "source_rows_fetched",
    "source_rows_normalized",
    "mapping_audit_rows",
    "polymarket_rows_eligible",
    "rows_mapped",
    "rows_joined",
    "unique_markets_mapped",
    "mapping_rate",
    "join_rate",
    "ambiguous_rows",
    "stale_rows",
    "missing_anchor_timestamp_rows",
    "price_observed_rows",
    "mean_executable_buy_divergence",
    "mean_midpoint_divergence",
    "zero_mapping",
    "zero_current_join",
    "actionable_rows",
    "accounting_status",
    "denominator_conservation_ok",
]

MAPPING_AUDIT_FINAL_STATUSES = {
    "joined",
    "unmapped",
    "ambiguous",
    "rejected_unpriced",
    "rejected_incomplete_market",
}

_SPORT_FAMILY_PREFIXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("soccer_fifa_world_cup_winner", ("worldcup_2026_winner",)),
    ("soccer_fifa_world_cup", ("soccer_match", "worldcup")),
    ("basketball_nba", ("basketball_nba", "basketball")),
    ("baseball_mlb", ("baseball_mlb", "baseball")),
    ("mma_", ("mma",)),
    ("tennis_", ("tennis",)),
)


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


def _funnel_history_path(cfg: EngineConfig) -> Path:
    return cfg.governance_root / "sharp_anchor_funnel_history.csv"


def _positive_setting(settings: Mapping[str, Any], key: str, default: float) -> float:
    value = safe_float(settings.get(key))
    return float(value) if value is not None and value >= 0 else float(default)


def _row_source(row: Mapping[str, Any]) -> str:
    return (
        str(row.get("source") or row.get("anchor_source") or row.get("bookmaker") or "unknown")
        .strip()
        .lower()
        or "unknown"
    )


def _dimension_key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        _row_source(row),
        str(row.get("sport") or "unknown").strip().lower() or "unknown",
        str(row.get("market_key") or "unknown").strip().lower() or "unknown",
    )


def _prediction_matches_sport_market(row: Mapping[str, Any], sport: str, market_key: str) -> bool:
    family = classify_market_family(dict(row)).lower()
    sport_lower = sport.lower()
    prefixes: tuple[str, ...] = ()
    for marker, candidates in _SPORT_FAMILY_PREFIXES:
        if sport_lower.startswith(marker):
            prefixes = candidates
            break
    if not prefixes:
        return False
    if not any(family.startswith(prefix) or prefix in family for prefix in prefixes):
        return False
    text = " ".join(
        str(row.get(key) or "").lower()
        for key in ("market_slug", "question", "category", "event_slug", "event_title")
    )
    outright = market_key.lower() in {"outright", "outrights", "futures"} or "winner" in sport_lower
    winner_like = any(term in text for term in ("winner", "champion", "to win", "win the"))
    return winner_like if outright else not winner_like


def _latest_predictions(cfg: EngineConfig) -> tuple[list[dict[str, str]], dict[str, dict[str, str]]]:
    rows = read_csv_rows(cfg.output_root / "polymarket_predictions" / "predictions.csv")
    by_token: dict[str, dict[str, str]] = {}
    for row in rows:
        token = str(row.get("token_id") or row.get("asset_id") or "").strip()
        if not token:
            continue
        current = by_token.get(token)
        candidate_time = parse_timestamp(row.get("prediction_timestamp") or row.get("generated_at_utc"))
        current_time = parse_timestamp(
            current.get("prediction_timestamp") or current.get("generated_at_utc")
        ) if current else None
        if current is None or (candidate_time is not None and (current_time is None or candidate_time > current_time)):
            by_token[token] = row
    return rows, by_token


def _price_fields(row: Mapping[str, Any]) -> tuple[float | None, float | None, float | None, float | None, float | None]:
    bid = safe_float(row.get("best_bid"))
    ask = safe_float(row.get("best_ask") or row.get("executable_price"))
    midpoint = safe_float(row.get("market_midpoint"))
    if midpoint is None and bid is not None and ask is not None:
        midpoint = (bid + ask) / 2.0
    spread = safe_float(row.get("spread"))
    if spread is None and bid is not None and ask is not None:
        spread = ask - bid
    liquidity = safe_float(row.get("liquidity"))
    return bid, ask, midpoint, spread, liquidity


def _source_funnel_rows(
    cfg: EngineConfig,
    *,
    settings: Mapping[str, Any],
    fetch_summary: Mapping[str, Any],
    anchor_summary: Mapping[str, Any],
    as_of: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    audit_path = Path(
        str(anchor_summary.get("mapping_audit_path") or cfg.governance_root / "sharp_anchor_mapping_audit.csv")
    )
    audit_rows = read_csv_rows(audit_path)
    fetch_rows_raw = fetch_summary.get("per_source_sport_market")
    fetch_rows = [dict(row) for row in fetch_rows_raw if isinstance(row, Mapping)] if isinstance(fetch_rows_raw, list) else []
    predictions, predictions_by_token = _latest_predictions(cfg)
    max_anchor_age = _positive_setting(settings, "max_anchor_age_seconds", 6 * 3600)
    max_price_age = _positive_setting(settings, "max_price_age_seconds", 300)
    min_divergence = _positive_setting(settings, "minimum_executable_divergence", 0.03)
    max_spread = _positive_setting(settings, "max_actionable_spread", 0.04)
    min_liquidity = _positive_setting(settings, "min_actionable_liquidity", 100)

    fetch_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in fetch_rows:
        key = _dimension_key(row)
        bucket = fetch_by_key.setdefault(
            key,
            {
                "source_rows_fetched": 0,
                "source_rows_normalized": 0,
                "fetch_records": 0,
            },
        )
        fetched = safe_float(row.get("source_rows_fetched"))
        normalized = safe_float(row.get("source_rows_normalized"))
        if fetched is not None:
            bucket["source_rows_fetched"] += int(fetched)
        else:
            bucket["missing_fetched_count"] = True
        if normalized is not None:
            bucket["source_rows_normalized"] += int(normalized)
        else:
            bucket["missing_normalized_count"] = True
        bucket["fetch_records"] += 1
    audit_by_key: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in audit_rows:
        audit_by_key[_dimension_key(row)].append(row)

    keys = sorted(set(fetch_by_key) | set(audit_by_key))
    if not keys:
        coverage = _coverage_by_sport_market(anchor_summary)
        keys = [("unknown", sport, market) for sport, market in coverage]

    output: list[dict[str, Any]] = []
    all_eligible_tokens: set[str] = set()
    all_mapped_tokens: set[str] = set()
    all_joined_tokens: set[str] = set()
    all_mapped_markets: set[tuple[str, str, str]] = set()
    for source, sport, market_key in keys:
        key = (source, sport, market_key)
        fetch = fetch_by_key.get(key, {})
        audited = audit_by_key.get(key, [])
        legacy = _coverage_by_sport_market(anchor_summary).get((sport, market_key), {})
        has_fetch_denominator = bool(fetch) and not fetch.get("missing_fetched_count")
        has_fetch_normalized = bool(fetch) and not fetch.get("missing_normalized_count")
        fetched_raw = safe_float(fetch.get("source_rows_fetched")) if has_fetch_denominator else None
        normalized_raw = safe_float(fetch.get("source_rows_normalized")) if has_fetch_normalized else None
        audit_count = len(audited)
        normalized = (
            int(normalized_raw)
            if normalized_raw is not None
            else audit_count
            if audited
            else int(safe_float(legacy.get("rows_in")) or 0)
        )
        fetched = int(fetched_raw) if fetched_raw is not None else normalized
        fetched_source = "provider_raw_outcomes" if fetched_raw is not None else "normalized_lower_bound"
        mapped_rows = [row for row in audited if str(row.get("mapping_status") or "") == "joined"]
        mapped = len(mapped_rows) if audited else int(safe_float(legacy.get("fundamental_rows")) or 0)
        unmapped_or_rejected = sum(
            1 for row in audited if str(row.get("mapping_status") or "") != "joined"
        )
        classified_mapping_rows = sum(
            1
            for row in audited
            if str(row.get("mapping_status") or "") in MAPPING_AUDIT_FINAL_STATUSES
        )
        unclassified_mapping_rows = audit_count - classified_mapping_rows
        ambiguous = sum(1 for row in audited if str(row.get("mapping_status") or "") == "ambiguous")
        unique_mapped = len({str(row.get("market_slug") or "") for row in mapped_rows if row.get("market_slug")})
        if not audited and mapped:
            unique_mapped = None

        eligible_predictions = [
            row for row in predictions if _prediction_matches_sport_market(row, sport, market_key)
        ]
        eligible_tokens = {
            str(row.get("token_id") or row.get("asset_id") or "").strip()
            for row in eligible_predictions
            if str(row.get("token_id") or row.get("asset_id") or "").strip()
        }
        all_eligible_tokens.update(eligible_tokens)
        joined_rows: list[tuple[dict[str, str], dict[str, str]]] = []
        for audit in mapped_rows:
            token = str(audit.get("token_id") or "").strip()
            if token:
                all_mapped_tokens.add(token)
            market_slug = str(audit.get("market_slug") or "").strip()
            if market_slug:
                all_mapped_markets.add((sport, market_key, market_slug))
            prediction = predictions_by_token.get(token)
            if prediction is not None:
                joined_rows.append((audit, prediction))
                if token:
                    all_joined_tokens.add(token)

        stale_anchor_rows = 0
        missing_anchor_timestamps = 0
        anchor_ages: list[float] = []
        for audit in audited:
            timestamp = parse_timestamp(audit.get("anchor_timestamp_utc"))
            if timestamp is None:
                missing_anchor_timestamps += 1
                continue
            age = max(0.0, (as_of - timestamp).total_seconds())
            anchor_ages.append(age)
            if age > max_anchor_age:
                stale_anchor_rows += 1

        midpoint_divergences: list[float] = []
        executable_divergences: list[float] = []
        stale_price_rows = 0
        actionable_rows = 0
        for audit, prediction in joined_rows:
            anchor_probability = safe_float(audit.get("anchor_probability"))
            bid, ask, midpoint, spread, liquidity = _price_fields(prediction)
            price_timestamp = parse_timestamp(prediction.get("prediction_timestamp") or prediction.get("generated_at_utc"))
            anchor_timestamp = parse_timestamp(audit.get("anchor_timestamp_utc"))
            price_fresh = price_timestamp is not None and max(0.0, (as_of - price_timestamp).total_seconds()) <= max_price_age
            anchor_fresh = anchor_timestamp is not None and max(0.0, (as_of - anchor_timestamp).total_seconds()) <= max_anchor_age
            if not price_fresh:
                stale_price_rows += 1
            if anchor_probability is not None and midpoint is not None:
                midpoint_divergences.append(anchor_probability - midpoint)
            if anchor_probability is not None and ask is not None:
                executable_divergence = anchor_probability - ask
                executable_divergences.append(executable_divergence)
                if (
                    bid is not None
                    and ask >= bid
                    and anchor_fresh
                    and price_fresh
                    and executable_divergence >= min_divergence
                    and spread is not None
                    and spread <= max_spread
                    and liquidity is not None
                    and liquidity >= min_liquidity
                ):
                    actionable_rows += 1

        raw_to_normalized_ok = (
            fetched_raw is not None
            and normalized_raw is not None
            and int(fetched_raw) >= int(normalized_raw)
        )
        mapping_audit_matches_normalized = normalized_raw is not None and int(normalized_raw) == audit_count
        mapping_classification_conservation_ok = (
            audit_count == mapped + unmapped_or_rejected
            and classified_mapping_rows == audit_count
        )
        conservation_ok = bool(
            raw_to_normalized_ok
            and mapping_audit_matches_normalized
            and mapping_classification_conservation_ok
        )
        accounting_status = "complete"
        if not audit_path.exists():
            accounting_status = "incomplete_missing_mapping_audit"
        elif fetched_raw is None or normalized_raw is None:
            accounting_status = "incomplete_missing_raw_fetch_denominator"
        elif int(fetched_raw) < int(normalized_raw):
            accounting_status = "inconsistent_raw_normalized_counts"
        elif int(normalized_raw) != audit_count:
            accounting_status = "inconsistent_fetch_anchor_artifacts"
        elif unclassified_mapping_rows:
            accounting_status = "incomplete_unclassified_mapping_audit"
        elif missing_anchor_timestamps:
            accounting_status = "incomplete_missing_anchor_timestamps"
        row = {
            "source": source,
            "sport": sport,
            "market_key": market_key,
            "source_rows_fetched": fetched,
            "source_rows_normalized": normalized,
            "normalization_rejections": (
                fetched - normalized
                if fetched_raw is not None and normalized_raw is not None and fetched >= normalized
                else None
            ),
            "fetched_denominator_source": fetched_source,
            "mapping_audit_rows": audit_count,
            "classified_mapping_rows": classified_mapping_rows,
            "unclassified_mapping_rows": unclassified_mapping_rows,
            "polymarket_rows_eligible": len(eligible_tokens),
            "rows_mapped": mapped,
            "unmapped_or_rejected_rows": unmapped_or_rejected,
            "rows_joined": len(joined_rows),
            "unique_markets_mapped": unique_mapped,
            "mapping_rate": round(mapped / normalized, 6) if normalized else None,
            "join_rate": round(len(joined_rows) / mapped, 6) if mapped else None,
            "ambiguous_rows": ambiguous,
            "stale_rows": stale_anchor_rows,
            "missing_anchor_timestamp_rows": missing_anchor_timestamps,
            "max_anchor_age_seconds": round(max(anchor_ages), 3) if anchor_ages else None,
            "stale_price_rows": stale_price_rows,
            "price_observed_rows": len(executable_divergences),
            "mean_executable_buy_divergence": (
                round(sum(executable_divergences) / len(executable_divergences), 6)
                if executable_divergences
                else None
            ),
            "mean_midpoint_divergence": (
                round(sum(midpoint_divergences) / len(midpoint_divergences), 6)
                if midpoint_divergences
                else None
            ),
            "zero_mapping": fetched > 0 and mapped == 0,
            "zero_current_join": mapped > 0 and not joined_rows,
            "actionable_rows": actionable_rows,
            "accounting_status": accounting_status,
            "raw_to_normalized_conservation_ok": raw_to_normalized_ok,
            "mapping_audit_matches_normalized": mapping_audit_matches_normalized,
            "mapping_classification_conservation_ok": mapping_classification_conservation_ok,
            "denominator_conservation_ok": conservation_ok,
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        }
        output.append(row)

    totals = {
        "source_rows_fetched": sum(int(row["source_rows_fetched"]) for row in output),
        "source_rows_normalized": sum(int(row["source_rows_normalized"]) for row in output),
        "mapping_audit_rows": sum(int(row["mapping_audit_rows"]) for row in output),
        "polymarket_rows_eligible": len(all_eligible_tokens),
        "polymarket_rows_eligible_dimension_sum": sum(
            int(row["polymarket_rows_eligible"]) for row in output
        ),
        "rows_mapped": sum(int(row["rows_mapped"]) for row in output),
        "rows_joined": sum(int(row["rows_joined"]) for row in output),
        "unique_tokens_mapped": len(all_mapped_tokens),
        "unique_tokens_joined": len(all_joined_tokens),
        "unique_markets_mapped": len(all_mapped_markets),
        "ambiguous_rows": sum(int(row["ambiguous_rows"]) for row in output),
        "stale_rows": sum(int(row["stale_rows"]) for row in output),
        "zero_mapping_count": sum(bool(row["zero_mapping"]) for row in output),
        "zero_current_join_count": sum(bool(row["zero_current_join"]) for row in output),
        "actionable_rows": sum(int(row["actionable_rows"]) for row in output),
        "accounting_mismatch_count": sum(
            row["accounting_status"] != "complete" for row in output
        ),
    }
    complete = (
        all(
            row["accounting_status"] == "complete" and row["denominator_conservation_ok"]
            for row in output
        )
        and bool(output)
    )
    return output, {
        "status": "complete" if complete else "incomplete",
        "definitions": {
            "mapping_rate": "rows_mapped / source_rows_normalized",
            "join_rate": "rows with a current Polymarket prediction / rows_mapped",
            "denominator_conservation": (
                "raw fetched >= normalized; normalized == mapping-audit rows; every audit row "
                "classified exactly once as joined, unmapped, ambiguous, or rejected"
            ),
            "price_divergence": "sharp fair probability minus executable Polymarket ask (buy side)",
            "actionable": "fresh anchor and price, real bid/ask, divergence/spread/liquidity reporting thresholds met",
        },
        "thresholds": {
            "max_anchor_age_seconds": max_anchor_age,
            "max_price_age_seconds": max_price_age,
            "minimum_executable_divergence": min_divergence,
            "max_actionable_spread": max_spread,
            "min_actionable_liquidity": min_liquidity,
        },
        "totals": totals,
        "mapping_audit_path": str(audit_path),
        "history_path": str(_funnel_history_path(cfg)),
        "reporting_only": True,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }


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
    as_of = parse_timestamp(now) or datetime.now(timezone.utc)
    source_sport_markets, anchor_funnel = _source_funnel_rows(
        cfg,
        settings=settings,
        fetch_summary=fetch_summary,
        anchor_summary=anchor_summary,
        as_of=as_of,
    )
    funnel_history = read_csv_rows(_funnel_history_path(cfg))
    funnel_keys = {
        (
            str(row.get("anchor_generated_at_utc") or ""),
            str(row.get("source") or ""),
            str(row.get("sport") or ""),
            str(row.get("market_key") or ""),
        )
        for row in funnel_history
    }
    funnel_appended = 0
    if anchor_generated_at:
        for row in source_sport_markets:
            key = (anchor_generated_at, row["source"], row["sport"], row["market_key"])
            if key in funnel_keys:
                continue
            funnel_history.append(
                {
                    "anchor_generated_at_utc": anchor_generated_at,
                    "recorded_at_utc": now,
                    **{field: row.get(field) for field in FUNNEL_HISTORY_FIELDS if field not in {"anchor_generated_at_utc", "recorded_at_utc"}},
                }
            )
            funnel_keys.add(key)
            funnel_appended += 1
        write_csv(_funnel_history_path(cfg), funnel_history, fieldnames=FUNNEL_HISTORY_FIELDS)
    anchor_funnel["history_rows"] = len(funnel_history)
    anchor_funnel["history_rows_appended"] = funnel_appended
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
        "source_sport_markets": source_sport_markets,
        "anchor_funnel": anchor_funnel,
        "flagged_no_mappable_market": flagged,
        "flagged_no_mappable_market_count": len(flagged),
        "mappable_count": sum(1 for row in per_market if row["classification"] == "mappable"),
        "collecting_count": sum(1 for row in per_market if row["classification"] == "collecting_coverage_evidence"),
        "total_rows_fetched": sum(int(row["rows_fetched"]) for row in per_market),
        "total_rows_mapped": sum(int(row["rows_mapped"]) for row in per_market),
        "total_rows_joined": anchor_funnel["totals"]["rows_joined"],
        "total_polymarket_rows_eligible": anchor_funnel["totals"]["polymarket_rows_eligible"],
        "total_actionable_rows": anchor_funnel["totals"]["actionable_rows"],
        "total_ambiguous_rows": anchor_funnel["totals"]["ambiguous_rows"],
        "total_stale_rows": anchor_funnel["totals"]["stale_rows"],
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
