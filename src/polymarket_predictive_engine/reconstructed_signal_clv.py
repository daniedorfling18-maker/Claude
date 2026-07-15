"""Reconstructed sharp-anchor CLV study.

WO-55 is deliberately *research only*: it reconstructs historical sharp-anchor
signals that would have met the frozen 2026-07-10 entry rules, prices them from
venue price history, and reports CLV as judgement input.  The output is never
Gate A evidence, never touches ``profit_verdict.py``, and never invokes any
paper/live trading path.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from math import isfinite
from pathlib import Path
import time
from typing import Any

import requests

from .config import EngineConfig, load_config
from .price_history_collector import normalize_price_history_payload
from .profit_verdict import _fixture_tags, _sign_test_p
from .sharp_anchor import devig
from .utils import (
    find_first_column,
    normalize_external_timestamp,
    now_utc,
    parse_timestamp,
    read_csv_rows,
    safe_float,
    write_csv,
    write_json,
)

EVIDENCE_CLASS = "reconstructed_research"

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": True,
    # The snapshot file is usually rewritten in-place; operators may add archive
    # globs here without changing the estimator.
    "snapshot_paths": [
        "inputs/polymarket/sharp_odds.csv",
        "outputs/polymarket_training/sharp_fundamental_probabilities.csv",
    ],
    "token_map_paths": ["outputs/polymarket_training/sharp_fundamental_probabilities.csv"],
    "price_history_paths": ["outputs/polymarket_training/historical_price_snapshots.csv"],
    "clob_base_url": "https://clob.polymarket.com",
    "gamma_base_url": "https://gamma-api.polymarket.com/markets",
    "fidelity_seconds": 300,
    "lookback_days": 45,
    "lookahead_days": 2,
    "request_timeout_seconds": 20,
    "request_pause_seconds": 0.1,
    "max_price_history_requests": 50,
    "max_gamma_requests": 25,
    "max_reconstructed_entries": 1000,
    "entry_max_lag_minutes": 180,
    "devig_method": "power",
    # Frozen mirror of the live sharp-anchor entry semantics as registered on
    # 2026-07-10. These may only be tightened in future dated amendments.
    "fundamental_probability_haircut": 0.50,
    "minimum_fundamental_edge_after_haircut": 0.03,
    "minimum_entry_price": 0.05,
    "maximum_entry_price": 0.90,
    "minimum_units_per_cohort": 8,
    "fdr_alpha": 0.10,
}

SNAPSHOT_FIELDNAMES = [
    "reconstructed_signal_id",
    "evidence_class",
    "signal_timestamp_utc",
    "market_id",
    "market_slug",
    "question",
    "token_id",
    "outcome",
    "category",
    "source_path",
    "anchor_probability",
    "entry_price",
    "haircut_anchor_probability",
    "fundamental_edge_after_haircut",
    "entry_price_timestamp_utc",
    "close_time",
    "closing_price",
    "closing_price_timestamp_utc",
    "clv",
    "beat_close",
    "cluster_unit",
    "cohort",
]


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = cfg.raw.get("reconstructed_clv", {}) if isinstance(cfg.raw.get("reconstructed_clv"), dict) else {}
    merged = dict(DEFAULT_SETTINGS)
    merged.update({key: value for key, value in raw.items() if value is not None})
    # If the operator did not duplicate the frozen thresholds into this block,
    # keep the mirror exact by reading the current registered base config.
    alpha = cfg.raw.get("mispricing_alpha", {}) if isinstance(cfg.raw.get("mispricing_alpha"), dict) else {}
    risk = cfg.raw.get("risk", {}) if isinstance(cfg.raw.get("risk"), dict) else {}
    merged.setdefault("fundamental_probability_haircut", alpha.get("fundamental_probability_haircut", 0.50))
    merged.setdefault(
        "minimum_fundamental_edge_after_haircut",
        alpha.get("minimum_fundamental_edge_after_haircut", 0.03),
    )
    merged.setdefault("minimum_entry_price", risk.get("minimum_entry_price", 0.05))
    merged.setdefault("maximum_entry_price", risk.get("maximum_entry_price", 0.90))
    return merged


def _repo_root(cfg: EngineConfig) -> Path:
    data_root = cfg.data_root
    if not data_root.is_absolute():
        data_root = cfg.path.parent / data_root
    return data_root


def _expand_paths(cfg: EngineConfig, configured: Any) -> list[Path]:
    if isinstance(configured, (str, Path)):
        items = [configured]
    elif isinstance(configured, (list, tuple, set)):
        items = list(configured)
    else:
        items = []
    root = _repo_root(cfg)
    paths: list[Path] = []
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        raw = Path(text)
        if raw.is_absolute():
            matches = sorted(raw.parent.glob(raw.name)) if any(ch in raw.name for ch in "*?[") else [raw]
        else:
            matches = sorted(root.glob(text)) if any(ch in text for ch in "*?[") else [root / raw]
        for match in matches:
            if match not in paths:
                paths.append(match)
    return paths


def _row_time(row: dict[str, Any]) -> datetime | None:
    for key in (
        "anchor_timestamp_utc",
        "signal_timestamp_utc",
        "snapshot_timestamp",
        "timestamp",
        "collected_at_utc",
        "generated_at_utc",
    ):
        parsed = parse_timestamp(row.get(key))
        if parsed is not None:
            return parsed.astimezone(timezone.utc)
    return None


def _row_close_time(row: dict[str, Any]) -> datetime | None:
    for key in ("close_time", "closedTime", "closed_time", "endDate", "end_date_iso", "endDateIso"):
        seconds = normalize_external_timestamp(row.get(key))
        if seconds is not None:
            return datetime.fromtimestamp(seconds, timezone.utc)
    return None


def _normalised_key(*parts: Any) -> str:
    return "::".join(str(part or "").strip().lower() for part in parts)


def _token_map(cfg: EngineConfig, settings: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for path in _expand_paths(cfg, settings.get("token_map_paths")):
        rows = read_csv_rows(path)
        if not rows:
            continue
        cols = list(rows[0].keys())
        market_col = find_first_column(cols, ["market_slug", "market", "event_slug", "group"])
        outcome_col = find_first_column(cols, ["outcome", "selection", "team", "runner"])
        token_col = find_first_column(cols, ["token_id", "asset_id", "clob_token_id"])
        if not market_col or not outcome_col or not token_col:
            continue
        for row in rows:
            token = str(row.get(token_col) or "").strip()
            if token:
                mapping.setdefault(_normalised_key(row.get(market_col), row.get(outcome_col)), token)
    return mapping


def _probability_from_row(row: dict[str, Any]) -> float | None:
    for key in ("probability", "fundamental_probability", "fair_probability", "anchor_probability"):
        value = safe_float(row.get(key))
        if value is not None and 0 < value < 1:
            return value
    implied = safe_float(row.get("implied_probability") or row.get("raw_implied_probability"))
    if implied is not None and 0 < implied < 1:
        return implied
    return None


def _raw_snapshot_candidates(cfg: EngineConfig, settings: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    token_lookup = _token_map(cfg, settings)
    candidates: list[dict[str, Any]] = []
    stats = {
        "snapshot_rows_seen": 0,
        "snapshot_rows_without_timestamp": 0,
        "snapshot_rows_without_probability": 0,
        "snapshot_rows_without_token": 0,
    }

    for path in _expand_paths(cfg, settings.get("snapshot_paths")):
        rows = read_csv_rows(path)
        if not rows:
            continue
        cols = list(rows[0].keys())
        market_col = find_first_column(cols, ["market_slug", "market", "event_slug", "group"])
        outcome_col = find_first_column(cols, ["outcome", "selection", "team", "runner"])
        token_col = find_first_column(cols, ["token_id", "asset_id", "clob_token_id"])
        odds_col = find_first_column(cols, ["decimal_odds", "odds", "price_decimal", "decimal"])
        grouped_for_devig: dict[tuple[str, str], list[int]] = defaultdict(list)

        path_candidates: list[dict[str, Any]] = []
        for row in rows:
            stats["snapshot_rows_seen"] += 1
            signal_time = _row_time(row)
            if signal_time is None:
                stats["snapshot_rows_without_timestamp"] += 1
                continue
            market_slug = str(row.get(market_col) or row.get("market_id") or "").strip() if market_col else str(row.get("market_id") or "").strip()
            outcome = str(row.get(outcome_col) or "").strip() if outcome_col else ""
            token = str(row.get(token_col) or "").strip() if token_col else ""
            if not token and market_slug and outcome:
                token = token_lookup.get(_normalised_key(market_slug, outcome), "")
            if not token:
                stats["snapshot_rows_without_token"] += 1
                continue
            probability = _probability_from_row(row)
            candidate = {
                **row,
                "_source_path": str(path),
                "_signal_time": signal_time,
                "_market_slug": market_slug,
                "_outcome": outcome,
                "_token_id": token,
                "_probability": probability,
            }
            path_candidates.append(candidate)
            if probability is None and odds_col:
                grouped_for_devig[(signal_time.isoformat(), market_slug)].append(len(path_candidates) - 1)

        for indexes in grouped_for_devig.values():
            raw_probs: list[float] = []
            valid_indexes: list[int] = []
            for idx in indexes:
                decimal = safe_float(path_candidates[idx].get(odds_col or ""))
                if decimal is None or decimal <= 1:
                    continue
                raw_probs.append(1.0 / decimal)
                valid_indexes.append(idx)
            if len(raw_probs) >= 2:
                fair = devig(raw_probs, str(settings.get("devig_method", "power")))
                for idx, probability in zip(valid_indexes, fair):
                    path_candidates[idx]["_probability"] = probability

        for candidate in path_candidates:
            probability = safe_float(candidate.get("_probability"))
            if probability is None or not 0 < probability < 1:
                stats["snapshot_rows_without_probability"] += 1
                continue
            candidates.append(candidate)
    return candidates, stats


def _load_local_price_history(cfg: EngineConfig, settings: dict[str, Any]) -> dict[str, list[tuple[datetime, float, str]]]:
    series: dict[str, list[tuple[datetime, float, str]]] = defaultdict(list)
    for path in _expand_paths(cfg, settings.get("price_history_paths")):
        for row in read_csv_rows(path):
            token = str(row.get("token_id") or row.get("asset_id") or row.get("clob_token_id") or "").strip()
            when = parse_timestamp(row.get("timestamp") or row.get("source_timestamp") or row.get("collected_at_utc"))
            price = safe_float(row.get("price") or row.get("midpoint") or row.get("market_midpoint"))
            if not token or when is None or price is None or not 0 < price < 1:
                continue
            series[token].append((when.astimezone(timezone.utc), price, "local_official_snapshot"))
    for token in series:
        series[token].sort(key=lambda item: item[0])
    return dict(series)


def _official_price_history(
    token_id: str,
    signal_time: datetime,
    close_time: datetime,
    settings: dict[str, Any],
) -> list[tuple[datetime, float, str]]:
    base = str(settings.get("clob_base_url") or DEFAULT_SETTINGS["clob_base_url"]).rstrip("/")
    lookback_days = int(settings.get("lookback_days", 45))
    lookahead_days = int(settings.get("lookahead_days", 2))
    start = min(signal_time, close_time) - timedelta(days=max(1, lookback_days))
    end = close_time + timedelta(days=max(1, lookahead_days))
    response = requests.get(
        f"{base}/prices-history",
        params={
            "market": token_id,
            "startTs": int(start.timestamp()),
            "endTs": int(end.timestamp()),
            "fidelity": int(settings.get("fidelity_seconds", 300)),
        },
        timeout=int(settings.get("request_timeout_seconds", 20)),
    )
    response.raise_for_status()
    out: list[tuple[datetime, float, str]] = []
    for point in normalize_price_history_payload(response.json()):
        when = parse_timestamp(point.get("timestamp"))
        price = safe_float(point.get("price"))
        if when is not None and price is not None and 0 < price < 1:
            out.append((when.astimezone(timezone.utc), price, "clob_prices_history"))
    return sorted(out, key=lambda item: item[0])


def _fetch_gamma_close_time(row: dict[str, Any], settings: dict[str, Any]) -> datetime | None:
    base = str(settings.get("gamma_base_url") or DEFAULT_SETTINGS["gamma_base_url"])
    market_slug = str(row.get("_market_slug") or row.get("market_slug") or "").strip()
    market_id = str(row.get("market_id") or row.get("condition_id") or "").strip()
    attempts: list[dict[str, str]] = []
    if market_slug:
        attempts.append({"slug": market_slug})
    if market_id.startswith("0x"):
        attempts.append({"condition_ids": market_id})
    elif market_id.isdigit():
        attempts.append({"id": market_id})
    for params in attempts:
        try:
            response = requests.get(base, params=params, timeout=int(settings.get("request_timeout_seconds", 20)))
            response.raise_for_status()
            payload = response.json()
        except Exception:
            continue
        for market in payload if isinstance(payload, list) else []:
            if not isinstance(market, dict):
                continue
            parsed = _row_close_time(market)
            if parsed is not None:
                return parsed
    return None


def _last_at_or_before(
    series: list[tuple[datetime, float, str]],
    when: datetime,
    *,
    min_when: datetime | None = None,
) -> tuple[datetime, float, str] | None:
    best: tuple[datetime, float, str] | None = None
    for item in series:
        if min_when is not None and item[0] < min_when:
            continue
        if item[0] <= when:
            best = item
        elif item[0] > when:
            break
    return best


def _cluster_units(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    parent: dict[str, str] = {}

    def find(node: str) -> str:
        while parent.setdefault(node, node) != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: str, right: str) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    row_nodes: list[str] = []
    for idx, row in enumerate(rows):
        market_key = str(row.get("market_id") or row.get("market_slug") or row.get("token_id") or idx)
        node = f"market:{market_key}"
        row_nodes.append(node)
        for tag in _fixture_tags(row):
            union(node, f"fixture:{tag}")

    clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node, row in zip(row_nodes, rows):
        clusters[find(node)].append(row)
    return dict(clusters)


def _bh_flags(cohorts: list[dict[str, Any]], alpha: float) -> None:
    eligible = [
        row
        for row in cohorts
        if safe_float(row.get("sign_test_p")) is not None
        and int(safe_float(row.get("independent_units")) or 0) >= int(safe_float(row.get("minimum_units_per_cohort")) or 0)
    ]
    ranked = sorted(eligible, key=lambda row: float(row["sign_test_p"]))
    m = len(ranked)
    cutoff_index = -1
    for idx, row in enumerate(ranked, start=1):
        if float(row["sign_test_p"]) <= alpha * idx / max(m, 1):
            cutoff_index = idx - 1
    passed = {id(row) for row in ranked[: cutoff_index + 1]} if cutoff_index >= 0 else set()
    for row in cohorts:
        row["bh_fdr_pass"] = id(row) in passed
        row["research_flag"] = bool(
            row["bh_fdr_pass"]
            and safe_float(row.get("unit_mean_clv")) is not None
            and float(row["unit_mean_clv"]) > 0
        )


def run_reconstructed_clv_study(cfg: EngineConfig) -> dict[str, Any]:
    settings = _settings(cfg)
    out_dir = cfg.governance_root
    rows_path = out_dir / "reconstructed_signal_clv_positions.csv"
    summary_path = out_dir / "reconstructed_signal_clv.json"

    if str(settings.get("enabled", True)).strip().lower() in {"0", "false", "no", "off"}:
        write_csv(rows_path, [], fieldnames=SNAPSHOT_FIELDNAMES)
        summary = {
            "status": "disabled",
            "evidence_class": EVIDENCE_CLASS,
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
            "generated_at_utc": now_utc(),
        }
        write_json(summary_path, summary)
        return summary

    candidates, candidate_stats = _raw_snapshot_candidates(cfg, settings)
    local_history = _load_local_price_history(cfg, settings)
    max_entries = int(settings.get("max_reconstructed_entries", 1000))
    max_history_requests = int(settings.get("max_price_history_requests", 50))
    max_gamma_requests = int(settings.get("max_gamma_requests", 25))
    pause = float(settings.get("request_pause_seconds", 0.1))
    haircut = max(0.0, min(1.0, float(settings.get("fundamental_probability_haircut", 0.5))))
    min_edge = float(settings.get("minimum_fundamental_edge_after_haircut", 0.03))
    min_price = float(settings.get("minimum_entry_price", 0.05))
    max_price = float(settings.get("maximum_entry_price", 0.90))
    max_lag = timedelta(minutes=float(settings.get("entry_max_lag_minutes", 180)))

    reconstructed: list[dict[str, Any]] = []
    skipped: defaultdict[str, int] = defaultdict(int)
    history_cache: dict[str, list[tuple[datetime, float, str]]] = {}
    gamma_cache: dict[str, datetime | None] = {}
    official_requests = 0
    gamma_requests = 0

    # Dedup identical archived snapshots before price-history work.
    seen_signals: set[tuple[str, str, str]] = set()
    for candidate in sorted(candidates, key=lambda row: (row["_signal_time"], row["_token_id"], row["_market_slug"])):
        if len(reconstructed) >= max_entries:
            skipped["max_entries_reached"] += 1
            continue
        token = str(candidate.get("_token_id") or "").strip()
        signal_time = candidate["_signal_time"]
        close_time = _row_close_time(candidate)
        if close_time is None:
            cache_key = str(candidate.get("market_id") or candidate.get("_market_slug") or token)
            if cache_key not in gamma_cache and gamma_requests < max_gamma_requests:
                gamma_requests += 1
                gamma_cache[cache_key] = _fetch_gamma_close_time(candidate, settings)
                if pause:
                    time.sleep(pause)
            close_time = gamma_cache.get(cache_key)
        if close_time is None:
            skipped["missing_close_time"] += 1
            continue
        if signal_time >= close_time:
            skipped["signal_after_close"] += 1
            continue
        dedup_key = (token, signal_time.isoformat(), str(candidate.get("_market_slug") or ""))
        if dedup_key in seen_signals:
            skipped["duplicate_signal"] += 1
            continue
        seen_signals.add(dedup_key)

        series = local_history.get(token, [])
        if not series:
            if token not in history_cache and official_requests < max_history_requests:
                try:
                    history_cache[token] = _official_price_history(token, signal_time, close_time, settings)
                    official_requests += 1
                except Exception:
                    history_cache[token] = []
                if pause:
                    time.sleep(pause)
            series = history_cache.get(token, [])
        if not series:
            skipped["missing_price_history"] += 1
            continue
        entry = _last_at_or_before(series, signal_time)
        if entry is None:
            skipped["missing_entry_price"] += 1
            continue
        entry_time, entry_price, _entry_source = entry
        if signal_time - entry_time > max_lag:
            skipped["stale_entry_price"] += 1
            continue
        if not min_price <= entry_price <= max_price:
            skipped["entry_price_outside_band"] += 1
            continue
        closing = _last_at_or_before(series, close_time, min_when=signal_time)
        if closing is None:
            skipped["missing_closing_price"] += 1
            continue
        close_price_time, close_price, _close_source = closing
        probability = float(candidate["_probability"])
        haircut_probability = entry_price + haircut * (probability - entry_price)
        edge = haircut_probability - entry_price
        if edge < min_edge:
            skipped["edge_below_frozen_threshold"] += 1
            continue
        if not all(isfinite(value) for value in (probability, entry_price, haircut_probability, edge, close_price)):
            skipped["nonfinite_value"] += 1
            continue
        clv = close_price - entry_price
        market_id = str(candidate.get("market_id") or candidate.get("condition_id") or candidate.get("_market_slug") or token)
        row = {
            "reconstructed_signal_id": f"recon-{len(reconstructed) + 1:06d}",
            "evidence_class": EVIDENCE_CLASS,
            "signal_timestamp_utc": signal_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "market_id": market_id,
            "market_slug": candidate.get("_market_slug", ""),
            "question": candidate.get("question", ""),
            "token_id": token,
            "outcome": candidate.get("_outcome", ""),
            "category": candidate.get("category") or candidate.get("sport") or candidate.get("sport_title") or "unknown",
            "source_path": candidate.get("_source_path", ""),
            "anchor_probability": round(probability, 6),
            "entry_price": round(entry_price, 6),
            "haircut_anchor_probability": round(haircut_probability, 6),
            "fundamental_edge_after_haircut": round(edge, 6),
            "entry_price_timestamp_utc": entry_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "close_time": close_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "closing_price": round(close_price, 6),
            "closing_price_timestamp_utc": close_price_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "clv": round(clv, 6),
            "beat_close": clv > 0,
            "cluster_unit": "",
            "cohort": "reconstructed_sharp_anchor|" + str(candidate.get("sport") or candidate.get("category") or "unknown").lower(),
        }
        reconstructed.append(row)

    clusters = _cluster_units(reconstructed)
    for unit, members in clusters.items():
        for row in members:
            row["cluster_unit"] = unit

    rows_by_cohort: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for unit, members in clusters.items():
        # Equal-weight by independent unit: average the correlated rows inside
        # the market/fixture cluster, then aggregate those unit means.
        primary = dict(members[0])
        primary["_unit_clv"] = sum(float(row["clv"]) for row in members) / len(members)
        primary["_unit_beat"] = primary["_unit_clv"] > 0
        primary["_unit_size"] = len(members)
        rows_by_cohort[str(primary.get("cohort") or "unknown")].append(primary)

    min_units = int(settings.get("minimum_units_per_cohort", 8))
    cohort_summaries: list[dict[str, Any]] = []
    for cohort, units in sorted(rows_by_cohort.items()):
        clvs = [float(row["_unit_clv"]) for row in units]
        successes = sum(1 for row in units if row["_unit_beat"])
        sign_p = _sign_test_p(successes, len(units)) if units else None
        cohort_summaries.append(
            {
                "cohort": cohort,
                "evidence_class": EVIDENCE_CLASS,
                "independent_units": len(units),
                "reconstructed_rows": sum(int(row["_unit_size"]) for row in units),
                "minimum_units_per_cohort": min_units,
                "unit_mean_clv": round(sum(clvs) / len(clvs), 6) if clvs else None,
                "unit_beat_close_rate": round(successes / len(units), 4) if units else None,
                "sign_test_p": round(sign_p, 6) if sign_p is not None else None,
                "bh_fdr_pass": False,
                "research_flag": False,
            }
        )
    _bh_flags(cohort_summaries, float(settings.get("fdr_alpha", 0.10)))

    unit_clvs = [
        sum(float(row["clv"]) for row in members) / len(members)
        for members in clusters.values()
    ]
    unit_successes = sum(1 for value in unit_clvs if value > 0)
    unit_sign_p = _sign_test_p(unit_successes, len(unit_clvs)) if unit_clvs else None
    write_csv(rows_path, reconstructed, fieldnames=SNAPSHOT_FIELDNAMES)
    summary = {
        "status": "ok" if reconstructed else "no_reconstructed_entries",
        "evidence_class": EVIDENCE_CLASS,
        "non_verdict_warning": (
            "Reconstructed entries are retrospective research datapoints only. "
            "They are not Gate A evidence and must not be consumed by profit_verdict.py."
        ),
        "generated_at_utc": now_utc(),
        "candidate_stats": candidate_stats,
        "reconstructed_entries": len(reconstructed),
        "independent_units": len(unit_clvs),
        "unit_mean_clv": round(sum(unit_clvs) / len(unit_clvs), 6) if unit_clvs else None,
        "unit_beat_close_rate": round(unit_successes / len(unit_clvs), 4) if unit_clvs else None,
        "unit_sign_test_p": round(unit_sign_p, 6) if unit_sign_p is not None else None,
        "cohorts": cohort_summaries,
        "research_flagged_cohorts": [row["cohort"] for row in cohort_summaries if row.get("research_flag")],
        "skipped": dict(sorted(skipped.items())),
        "official_price_history_requests": official_requests,
        "gamma_close_time_requests": gamma_requests,
        "thresholds_frozen_as_of_utc": "2026-07-10T00:00:00Z",
        "thresholds": {
            "fundamental_probability_haircut": haircut,
            "minimum_fundamental_edge_after_haircut": min_edge,
            "minimum_entry_price": min_price,
            "maximum_entry_price": max_price,
            "entry_max_lag_minutes": float(settings.get("entry_max_lag_minutes", 180)),
            "fdr_alpha": float(settings.get("fdr_alpha", 0.10)),
            "minimum_units_per_cohort": min_units,
        },
        "outputs": {"positions_csv": str(rows_path), "summary_json": str(summary_path)},
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    write_json(summary_path, summary)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return run_reconstructed_clv_study(load_config(config_path))
