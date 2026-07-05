#!/usr/bin/env python3
"""Run the local Polymarket paper dashboard as a live WebSocket loop.

This is intentionally local-first and Docker-free.  It keeps the WebSocket
subscription pointed at the tokens the paper system actually cares about,
normalises new messages, ingests the live top-of-book features into the paper
ledger's market_snapshots table, then writes a fresh portfolio snapshot and
regenerates dashboard_data.json.

It does not enable live trading and it does not bypass the paper broker's risk,
readiness, kill-switch, or P&L pause controls.  If new entries are paused, the
loop still keeps marking existing positions and the dashboard live.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from polymarket_predictive_engine.config import load_config  # noqa: E402
from polymarket_predictive_engine.cohort_validation import write_signal_cohort_pnl  # noqa: E402
from polymarket_predictive_engine.dashboard import render_dashboard  # noqa: E402
from polymarket_predictive_engine.execution.paper import paper_trade  # noqa: E402
from polymarket_predictive_engine.paper_cycle import run_paper_cycle  # noqa: E402
from polymarket_predictive_engine.price_action_signals import build_price_action_paper_signals  # noqa: E402
from polymarket_predictive_engine.price_action_scout import build_price_action_scout  # noqa: E402
from polymarket_predictive_engine.profit_target import write_profit_target_tracker  # noqa: E402
from polymarket_predictive_engine.refresh_governance import refresh_governance  # noqa: E402
from polymarket_predictive_engine.resolution_collector import fetch_gamma_market  # noqa: E402
from polymarket_predictive_engine.runtime_lock import runtime_lock, runtime_lock_path  # noqa: E402
from polymarket_predictive_engine.shadow_cohort import update_shadow_cohort_evidence  # noqa: E402
from polymarket_predictive_engine.storage import connect_db  # noqa: E402
from polymarket_predictive_engine.utils import now_utc, parse_timestamp, read_csv_rows, read_json, safe_float, write_csv, write_json  # noqa: E402
from polymarket_predictive_engine.websocket_collector import collect_websocket  # noqa: E402
from polymarket_predictive_engine.websocket_normaliser import normalize_websocket_file  # noqa: E402
import polymarket_mispricing_bot as scanner  # noqa: E402
import run_polymarket_live_paper_loop as discovery_loop  # noqa: E402


def force_local_safe_environment() -> None:
    """Keep this loop strictly in scan/paper mode."""
    os.environ["PM_MODE"] = "scan"
    os.environ["POLYMARKET_MODE"] = "scan"
    os.environ["POLYMARKET_EXECUTE_LIVE"] = "false"
    os.environ["POLYMARKET_LIVE_TRADING"] = "0"
    os.environ.setdefault("POLYMARKET_QUERY", "world cup")
    os.environ.setdefault("POLYMARKET_MODEL_PROBABILITIES_CSV", "inputs/polymarket/model_probabilities.csv")
    os.environ.setdefault("POLYMARKET_POSITIONS_CSV", "inputs/polymarket/positions.csv")
    os.environ.setdefault("POLYMARKET_OUTPUT_DIR", "outputs/polymarket")


def _stable_id(prefix: str, key: str) -> str:
    return prefix + "_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def _add_tokens_from_csv(path: Path, tokens: dict[str, str], source: str) -> None:
    for row in read_csv_rows(path):
        for column in ("token_id", "asset_id", "outcome_token_id"):
            token = str(row.get(column) or "").strip()
            if token:
                tokens.setdefault(token, source)


def _fast_updown_snapshot_path(cfg) -> Path:
    return cfg.output_root / "polymarket_fast_updown" / "fast_updown_market_snapshot.csv"


def _positive_fast_updown_assets(cfg) -> list[str]:
    settings = cfg.raw.get("paper_market_scan", {}) or {}
    min_score = int(settings.get("fast_updown_min_promotion_ready_score", 4))
    min_pnl = float(settings.get("fast_updown_min_pnl_usdc", 0.0))
    min_roi = float(settings.get("fast_updown_min_roi", 0.0))
    assets: set[str] = set()
    payload = read_json(cfg.governance_root / "signal_cohort_pnl.json", default={}) or {}
    cohorts = payload.get("cohorts", []) if isinstance(payload, dict) else []
    if not isinstance(cohorts, list):
        return []
    for row in cohorts:
        if not isinstance(row, dict):
            continue
        cohort = str(row.get("signal_cohort") or "")
        if "updown_5m" not in cohort:
            continue
        pnl = safe_float(row.get("total_pnl_usdc")) or 0.0
        roi = safe_float(row.get("roi")) or 0.0
        score = int(safe_float(row.get("promotion_ready_score")) or 0)
        if pnl <= min_pnl or roi <= min_roi or score < min_score:
            continue
        for asset in ("btc", "eth", "sol", "xrp"):
            if f"crypto_{asset}_updown_5m" in cohort:
                assets.add(asset)
    configured = settings.get("fast_updown_assets", []) or []
    if isinstance(configured, str):
        configured = [configured]
    for asset in configured if isinstance(configured, list) else []:
        clean = str(asset or "").strip().lower()
        if clean in {"btc", "eth", "sol", "xrp"}:
            assets.add(clean)
    return [asset for asset in ("btc", "xrp", "sol", "eth") if asset in assets]


def _floor_to_slot(dt: datetime, minutes: int) -> datetime:
    slot = max(1, int(minutes))
    discard = timedelta(
        minutes=dt.minute % slot,
        seconds=dt.second,
        microseconds=dt.microsecond,
    )
    return dt - discard


def _fast_updown_candidate_slugs(cfg, *, now_dt: datetime | None = None) -> list[str]:
    settings = cfg.raw.get("paper_market_scan", {}) or {}
    if not settings.get("fast_updown_5m_enabled", True):
        return []
    assets = _positive_fast_updown_assets(cfg)
    if not assets:
        return []
    now_dt = (now_dt or datetime.now(timezone.utc)).astimezone(timezone.utc)
    step_minutes = int(settings.get("fast_updown_5m_step_minutes", 5) or 5)
    slots_ahead = int(settings.get("fast_updown_5m_slots_ahead", 8) or 8)
    start = _floor_to_slot(now_dt, step_minutes)
    slugs: list[str] = []
    for offset in range(0, max(0, slots_ahead) + 1):
        slot = start + timedelta(minutes=step_minutes * offset)
        timestamp = int(slot.timestamp())
        for asset in assets:
            slugs.append(f"{asset}-updown-5m-{timestamp}")
    return slugs


def _event_from_gamma_market(market: dict[str, Any]) -> dict[str, Any]:
    return {
        "slug": market.get("eventSlug") or market.get("event_slug") or market.get("slug") or "",
        "title": market.get("eventTitle") or market.get("event_title") or market.get("question") or market.get("slug") or "",
        "markets": [market],
    }


def refresh_fast_updown_snapshot(cfg) -> dict[str, Any]:
    """Write a focused token snapshot for positive-evidence 5-minute Up/Down cohorts."""
    settings = cfg.raw.get("paper_market_scan", {}) or {}
    if not settings.get("fast_updown_5m_enabled", True):
        return {"status": "disabled"}
    bot_config = scanner.BotConfig.from_env()
    timeout_seconds = int(settings.get("fast_updown_gamma_timeout_seconds", 8) or 8)
    max_tokens = int(settings.get("fast_updown_5m_max_tokens", 80) or 80)
    now_dt = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    slugs = _fast_updown_candidate_slugs(cfg, now_dt=now_dt)
    markets_found = 0
    orderbook_errors = 0
    for slug in slugs:
        try:
            market = fetch_gamma_market(slug, timeout_seconds=timeout_seconds)
        except Exception:
            continue
        if not market:
            continue
        markets_found += 1
        tokens = scanner.extract_tokens([_event_from_gamma_market(market)])
        for token in tokens:
            parsed_close = parse_timestamp(token.close_time)
            if parsed_close is not None and parsed_close.astimezone(timezone.utc) <= now_dt:
                continue
            try:
                book = scanner.get_orderbook(bot_config, token)
            except Exception:
                orderbook_errors += 1
                book = None
            rows.append(
                {
                    "timestamp": now_utc(),
                    "event_slug": token.event_slug,
                    "event_title": token.event_title,
                    "market_slug": token.market_slug,
                    "condition_id": token.condition_id,
                    "close_time": token.close_time,
                    "question": token.question,
                    "outcome": token.outcome,
                    "token_id": token.token_id,
                    "gamma_price": "" if token.gamma_price is None else token.gamma_price,
                    "fair_probability": "",
                    "best_bid": "" if book is None or book.best_bid is None else book.best_bid,
                    "best_ask": "" if book is None or book.best_ask is None else book.best_ask,
                    "spread": "" if book is None or book.spread is None else book.spread,
                    "bid_size": "" if book is None else book.bid_size,
                    "ask_size": "" if book is None else book.ask_size,
                    "tick_size": token.tick_size,
                    "neg_risk": token.neg_risk,
                }
            )
            if len(rows) >= max_tokens:
                break
        if len(rows) >= max_tokens:
            break
    out = _fast_updown_snapshot_path(cfg)
    write_csv(out, rows, fieldnames=discovery_loop.SNAPSHOT_FIELDS)
    return {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "assets": _positive_fast_updown_assets(cfg),
        "candidate_slugs": len(slugs),
        "markets_found": markets_found,
        "tokens": len(rows),
        "orderbook_errors": orderbook_errors,
        "snapshot_path": str(out),
    }


def discover_websocket_asset_ids(cfg, *, include_static_config: bool = False, max_assets: int = 250) -> tuple[list[str], dict[str, str]]:
    """Return live CLOB asset ids from current paper state, not stale config.

    The checked-in config may contain old asset ids.  For live dashboard marking
    we prefer assets that have the freshest scanner metadata first, then current
    portfolio/signals, then broader prediction/model-probability context.
    """
    tokens: dict[str, str] = {}
    model_csv = ROOT / os.environ.get("POLYMARKET_MODEL_PROBABILITIES_CSV", "inputs/polymarket/model_probabilities.csv")
    candidates = [
        (cfg.output_root / "polymarket_portfolio" / "positions.csv", "open_positions"),
        (cfg.output_root / "polymarket_predictions" / "trade_signals.csv", "trade_signals"),
        (_fast_updown_snapshot_path(cfg), "fast_updown_5m"),
        (cfg.output_root / "polymarket_shadow" / "shadow_positions.csv", "shadow_positions"),
        (cfg.output_root / "polymarket" / "market_snapshot.csv", "scanner_snapshot"),
        (model_csv, "model_probabilities"),
        (cfg.output_root / "polymarket_predictions" / "predictions.csv", "predictions"),
    ]
    for path, source in candidates:
        if path.exists():
            _add_tokens_from_csv(path, tokens, source)

    if include_static_config:
        for token in cfg.raw.get("websocket_market_data", {}).get("market_ids", []) or []:
            token = str(token).strip()
            if token:
                tokens.setdefault(token, "config")

    ids = list(tokens.keys())[:max_assets]
    return ids, {token: tokens[token] for token in ids}


def configure_websocket_assets(cfg, asset_ids: list[str]) -> None:
    settings = cfg.raw.setdefault("websocket_market_data", {})
    settings["market_ids"] = asset_ids
    subscription = dict(settings.get("subscription_message") or {})
    # Polymarket CLOB market channel expects asset ids, not human market names.
    # Keep both spellings out of the way except the one used by existing config.
    subscription.pop("markets", None)
    subscription["assets_ids"] = asset_ids
    subscription["type"] = "market"
    settings["subscription_message"] = subscription


def enrich_websocket_features_with_scanner_metadata(cfg, features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach human market context to live WebSocket feature rows.

    The CLOB websocket gives the live book by asset id, but the predictive model
    also needs point-in-time market context such as question, selection, slug and
    close time.  The latest scanner snapshot supplies that context.  This keeps
    the live price source as websocket while avoiding a blind model row that only
    says "asset 123 moved".
    """
    snapshot_path = cfg.output_root / "polymarket" / "market_snapshot.csv"
    snapshot_paths = [_fast_updown_snapshot_path(cfg), snapshot_path]
    metadata_by_token: dict[str, dict[str, Any]] = {}
    for path in snapshot_paths:
        for row in read_csv_rows(path):
            token = str(row.get("token_id") or row.get("asset_id") or "").strip()
            if token:
                metadata_by_token.setdefault(token, row)

    enriched: list[dict[str, Any]] = []
    metadata_hits = 0
    for row in features:
        token = str(row.get("asset_id") or row.get("token_id") or "").strip()
        metadata = metadata_by_token.get(token, {})
        if metadata:
            metadata_hits += 1
        enriched_row = dict(row)
        for target, source in {
            "market_slug": "market_slug",
            "question": "question",
            "outcome": "outcome",
            "close_time": "close_time",
            "event_slug": "event_slug",
            "event_title": "event_title",
            "tick_size": "tick_size",
        }.items():
            value = metadata.get(source)
            if value not in {None, ""}:
                enriched_row[target] = value
        if metadata.get("condition_id") and not enriched_row.get("market"):
            enriched_row["market"] = metadata.get("condition_id")
        text = " ".join(
            str(enriched_row.get(key) or "")
            for key in ("market_slug", "question", "event_slug", "event_title")
        ).lower()
        if not enriched_row.get("category"):
            if any(term in text for term in ("bitcoin", "btc", "ethereum", "eth", "solana", "sol", "xrp", "ripple", "crypto")):
                enriched_row["category"] = "crypto"
            elif "world cup" in text or "worldcup" in text or "fifa" in text:
                enriched_row["category"] = "worldcup"
        enriched.append(enriched_row)

    settings = cfg.raw.get("websocket_market_data", {}) or {}
    rewrite_feature_file = str(
        settings.get("rewrite_enriched_feature_file_after_normalise", True)
    ).strip().lower() in {"1", "true", "yes", "y", "on"}
    if enriched and rewrite_feature_file:
        fieldnames: list[str] = []
        for row in enriched:
            for key in row.keys():
                if key not in fieldnames:
                    fieldnames.append(str(key))
        write_csv(cfg.output_root / "polymarket_training" / "websocket_market_features.csv", enriched, fieldnames=fieldnames)
    write_json(
        cfg.governance_root / "websocket_metadata_enrichment_summary.json",
        {
            "status": "ok",
            "generated_at_utc": now_utc(),
            "feature_rows": len(features),
            "metadata_hits": metadata_hits,
            "metadata_hit_rate": (metadata_hits / len(features)) if features else 0.0,
            "snapshot_path": str(snapshot_path),
            "snapshot_paths": [str(path) for path in snapshot_paths],
            "snapshot_tokens": len(metadata_by_token),
            "rewrote_feature_file": rewrite_feature_file,
        },
    )
    return enriched


def ingest_websocket_features_into_ledger(cfg, features: list[dict[str, Any]]) -> dict[str, Any]:
    """Make WebSocket top-of-book rows visible to the paper portfolio marker.

    paper_broker.portfolio_state reads the market_snapshots SQLite table.  The
    normal WebSocket normaliser only writes CSV features, so without this ingest
    the dashboard can collect WebSocket messages but still mark positions using
    stale scanner quotes.  This bridge is the key local-live update.
    """
    inserted = 0
    skipped = 0
    con = connect_db(cfg.database_path)
    try:
        with con:
            for row in features:
                market_id = str(row.get("market") or row.get("market_id") or "").strip()
                token_id = str(row.get("asset_id") or row.get("token_id") or "").strip()
                collected_at = str(row.get("collected_at_utc") or now_utc())
                if not market_id or not token_id:
                    skipped += 1
                    continue
                bid = safe_float(row.get("best_bid"))
                ask = safe_float(row.get("best_ask"))
                midpoint = safe_float(row.get("midpoint"))
                last_trade = safe_float(row.get("last_trade_price"))
                if midpoint is None and bid is not None and ask is not None:
                    midpoint = (bid + ask) / 2.0
                if midpoint is None:
                    midpoint = last_trade
                if bid is None and midpoint is None and ask is None:
                    skipped += 1
                    continue
                spread = safe_float(row.get("spread"))
                if spread is None and bid is not None and ask is not None:
                    spread = max(0.0, ask - bid)
                liquidity = (safe_float(row.get("top_bid_size")) or 0.0) + (safe_float(row.get("top_ask_size")) or 0.0)
                key = "|".join(["websocket", collected_at, market_id, token_id, str(row.get("event_type", ""))])
                cur = con.execute(
                    """
                    INSERT OR IGNORE INTO market_snapshots(
                        snapshot_id, idempotency_key, collected_at, market_id, token_id,
                        best_bid, best_ask, midpoint, spread, liquidity, raw_payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _stable_id("snapshot", key),
                        key,
                        collected_at,
                        market_id,
                        token_id,
                        bid,
                        ask,
                        midpoint,
                        spread,
                        liquidity,
                        json.dumps(row, sort_keys=True),
                    ),
                )
                inserted += int(cur.rowcount or 0)
    finally:
        con.close()
    return {"status": "ok", "inserted_market_snapshots": inserted, "skipped_rows": skipped}


def _latest_websocket_feature_index(features: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in features:
        token = str(row.get("asset_id") or row.get("token_id") or row.get("outcome_token_id") or "").strip()
        if not token:
            continue
        current = index.get(token)
        if current is None or str(row.get("collected_at_utc") or "") >= str(current.get("collected_at_utc") or ""):
            index[token] = row
    return index


def _merge_websocket_marks_into_predictions(
    predictions: list[dict[str, Any]],
    features: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    feature_by_token = _latest_websocket_feature_index(features)
    if not feature_by_token:
        return predictions
    merged: list[dict[str, Any]] = []
    for row in predictions:
        token = str(row.get("token_id") or row.get("asset_id") or row.get("outcome_token_id") or "").strip()
        feature = feature_by_token.get(token)
        if not feature:
            merged.append(row)
            continue
        enriched = dict(row)
        bid = safe_float(feature.get("best_bid"))
        ask = safe_float(feature.get("best_ask"))
        midpoint = safe_float(feature.get("midpoint"))
        spread = safe_float(feature.get("spread"))
        top_bid_size = safe_float(feature.get("top_bid_size")) or 0.0
        top_ask_size = safe_float(feature.get("top_ask_size")) or 0.0
        if spread is None and bid is not None and ask is not None:
            spread = max(0.0, ask - bid)
        if midpoint is None and bid is not None and ask is not None:
            midpoint = (bid + ask) / 2.0
        if bid is not None:
            enriched["best_bid"] = bid
            enriched["bid"] = bid
            enriched["executable_sell_price"] = bid
        if ask is not None:
            enriched["best_ask"] = ask
            enriched["executable_price"] = ask
        if midpoint is not None:
            enriched["market_midpoint"] = midpoint
            enriched["midpoint"] = midpoint
        if spread is not None:
            enriched["spread"] = spread
        liquidity = top_bid_size + top_ask_size
        if liquidity > 0:
            enriched["liquidity"] = liquidity
        enriched["websocket_mark_timestamp"] = feature.get("collected_at_utc", "")
        merged.append(enriched)
    return merged


def _lightweight_shadow_maintenance(cfg, features: list[dict[str, Any]]) -> dict[str, Any]:
    predictions_root = cfg.output_root / "polymarket_predictions"
    rows = read_csv_rows(predictions_root / "mispricing_alpha_scores.csv")
    if not rows:
        rows = read_csv_rows(predictions_root / "predictions.csv")
    near_miss = read_csv_rows(predictions_root / "near_miss_learning_candidates.csv")
    seen: set[tuple[str, str]] = {
        (str(row.get("market_id") or ""), str(row.get("token_id") or ""))
        for row in rows
    }
    for row in near_miss:
        key = (str(row.get("market_id") or ""), str(row.get("token_id") or ""))
        if key not in seen:
            rows.append(row)
            seen.add(key)
    marked_rows = _merge_websocket_marks_into_predictions(rows, features)
    shadow = update_shadow_cohort_evidence(cfg, marked_rows)
    cohort: dict[str, Any] = {}
    try:
        con = connect_db(cfg.database_path)
        try:
            cohort = write_signal_cohort_pnl(con, cfg)
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001 - keep live dashboard alive if cohort aggregation fails
        cohort = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    return {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "prediction_rows": len(rows),
        "websocket_feature_rows": len(features),
        "shadow": {
            "status": shadow.get("status"),
            "open_positions": shadow.get("open_positions"),
            "near_miss_candidates_seen": shadow.get("near_miss_candidates_seen"),
            "near_miss_open_positions": shadow.get("near_miss_open_positions"),
            "near_miss_opened_this_cycle": shadow.get("near_miss_opened_this_cycle"),
        },
        "cohort_pnl": {
            "status": cohort.get("status"),
            "cohorts": len(cohort.get("cohorts", [])) if isinstance(cohort.get("cohorts"), list) else 0,
        },
    }


def mark_portfolio_and_render_dashboard(
    cfg,
    *,
    source: str,
    loop_summary: dict[str, Any],
    features: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    shadow_maintenance = _lightweight_shadow_maintenance(cfg, features or [])
    broker = paper_trade(cfg)
    actual_target = write_profit_target_tracker(cfg, broker if isinstance(broker, dict) else {})
    previous = read_json(cfg.governance_root / "forward_paper_cycle.json", default={}) or {}
    forward = dict(previous) if isinstance(previous, dict) else {}
    for stale_prediction_key in ("blockers", "runtime_lock"):
        forward.pop(stale_prediction_key, None)
    forward.update(
        {
            "status": "ran",
            "source": source,
            "live_trading": False,
            "generated_at_utc": now_utc(),
            "broker": broker,
            "actual_profit_target": actual_target,
            "local_live_loop": loop_summary,
            "shadow_maintenance": shadow_maintenance,
        }
    )
    write_json(cfg.governance_root / "forward_paper_cycle.json", forward)
    forward["dashboard"] = render_dashboard(cfg, forward)
    write_json(cfg.governance_root / "forward_paper_cycle.json", forward)
    return forward


def _clear_orphaned_same_process_prediction_lock(cfg, prediction_future: Future[dict[str, Any]] | None) -> dict[str, Any]:
    if prediction_future is not None and not prediction_future.done():
        return {"status": "active_prediction_future"}
    path = runtime_lock_path(cfg, "prediction_cycle")
    payload = read_json(path, default={}) or {}
    if not path.exists() or not isinstance(payload, dict):
        return {"status": "not_locked"}
    if str(payload.get("name") or "") != "prediction_cycle":
        return {"status": "foreign_lock_name", "lock_name": payload.get("name", "")}
    try:
        lock_pid = int(payload.get("pid"))
    except (TypeError, ValueError):
        return {"status": "foreign_or_malformed_lock", "pid": payload.get("pid", "")}
    if lock_pid != os.getpid():
        return {"status": "owned_by_other_process", "pid": lock_pid}
    try:
        path.unlink()
    except FileNotFoundError:
        return {"status": "already_cleared"}
    return {
        "status": "cleared_same_process_orphan",
        "pid": lock_pid,
        "acquired_at_utc": payload.get("acquired_at_utc", ""),
    }


def _initial_discovery_due_timestamp(discovery_cycle_seconds: float, *, now: float | None = None) -> float:
    """Schedule the first discovery refresh immediately after the first live tick.

    WebSocket ticks are the hot path; discovery is the slower market-universe
    refresh path.  The first discovery pass after a local restart should still
    be a real scanner pass, not accidentally inherit a high WebSocket iteration
    number and fall into the scanner's settlement-only maintenance schedule.
    """
    if discovery_cycle_seconds <= 0:
        return float("inf")
    return time.time() if now is None else now


def _run_discovery_iteration(
    *,
    config_path: Path,
    optimize_model: bool,
    discovery_iteration: int,
    paper_source: str,
) -> tuple[int, dict[str, Any]]:
    """Run one scanner discovery pass using its own sequence counter."""
    next_iteration = max(0, int(discovery_iteration)) + 1
    scanner_iteration = next_iteration * 2
    discovery_loop.force_paper_environment()
    discovery_loop.ensure_scanner_runtime_files()
    cfg = load_config(config_path)

    guard = discovery_loop._resource_guard(cfg)
    write_json(cfg.governance_root / "runtime_resource_guard.json", {**guard, "generated_at_utc": now_utc()})
    if guard.get("skip_cycle"):
        summary = {
            "status": "skipped_resource_guard",
            "generated_at_utc": now_utc(),
            "live_data": False,
            "live_trading": False,
            "resource_guard": guard,
            "message": "Background discovery skipped to protect local machine resources.",
        }
    else:
        schedule = cfg.raw.get("runtime_schedule", {}) or {}
        heavy_due = any(
            discovery_loop._scheduled(schedule, key, scanner_iteration, default=12)
            for key in (
                "optimize_model_every_iterations",
                "sharp_odds_fetch_every_iterations",
                "sharp_anchor_build_every_iterations",
                "crypto_fundamental_every_iterations",
                "mispricing_alpha_every_iterations",
                "edge_strategy_search_every_iterations",
                "promoted_rule_shadow_every_iterations",
                "liquidity_discovery_every_iterations",
            )
        )
        if optimize_model and heavy_due:
            summary = discovery_loop.run_iteration(
                config_path=config_path,
                optimize_model=optimize_model,
                iteration=scanner_iteration,
                paper_source=paper_source,
            )
        else:
            liquidity_discovery = (
                discovery_loop.run_liquidity_discovery(cfg, mode="targeted-evidence")
                if bool(cfg.raw.get("liquidity_discovery", {}).get("enabled", True))
                else {"status": "disabled"}
            )
            scan = discovery_loop.scan_once(cfg, scan_sequence=next_iteration)
            fast_updown = refresh_fast_updown_snapshot(cfg)
            ingest = discovery_loop.ingest_scanner_snapshot(cfg, snapshot_path=scan["snapshot_path"])
            settlement = discovery_loop._run_settlement_only_cycle(cfg)
            summary = {
                "status": "ran",
                "mode": "background_lightweight_discovery",
                "generated_at_utc": now_utc(),
                "live_data": True,
                "live_trading": False,
                "resource_guard": guard,
                "scan": scan,
                "fast_updown": fast_updown,
                "liquidity_discovery": liquidity_discovery,
                "ingest": ingest,
                "settlement": settlement,
                "optimization": {"status": "skipped_until_scheduled_heavy_cycle"},
            }
            write_json(cfg.governance_root / "live_paper_loop_heartbeat.json", summary)
    if isinstance(summary, dict):
        summary["discovery_iteration"] = next_iteration
        summary["scanner_iteration"] = scanner_iteration
    return next_iteration, summary


def _write_discovery_heartbeat(
    cfg,
    *,
    status: str,
    live_iteration: int,
    discovery_iteration: int,
    summary: dict[str, Any] | None = None,
    error: str = "",
    started_at_utc: str = "",
) -> None:
    summary = summary or {}
    payload: dict[str, Any] = {
        "status": status,
        "generated_at_utc": now_utc(),
        "live_iteration": live_iteration,
        "discovery_iteration": discovery_iteration,
        "scan": summary.get("scan", {}) if isinstance(summary, dict) else {},
        "fast_updown": summary.get("fast_updown", {}) if isinstance(summary, dict) else {},
    }
    if started_at_utc:
        payload["started_at_utc"] = started_at_utc
    if error:
        payload["error"] = error
    write_json(cfg.governance_root / "local_live_loop_discovery_heartbeat.json", payload)


def _run_live_paper_bridge_cycle(*, config_path: Path, paper_source: str) -> dict[str, Any]:
    """Fast live paper bridge: current price-action signals -> broker -> proof tracker.

    The full canonical paper cycle rebuilds features, predictions, alpha scores,
    longshot scans, cohort P&L, signals, broker, and dashboard. That is useful
    as a heavier background refresh, but it can take minutes on the VPS. The
    hot live lane already collects websocket bid/ask data every tick; this
    bridge converts the current governed price-action evidence into broker
    action without changing thresholds or enabling live trading.
    """
    cfg = load_config(config_path)
    report: dict[str, Any] = {
        "status": "ran",
        "mode": "live_price_action_paper_bridge",
        "source": paper_source,
        "generated_at_utc": now_utc(),
        "live_trading": False,
        "paper_trading_invoked": True,
        "live_trading_invoked": False,
    }
    lock_settings = cfg.raw.get("runtime_resource_guard", {}) or {}
    lock_stale_seconds = float(lock_settings.get("prediction_cycle_lock_stale_seconds", 1800) or 1800)
    with runtime_lock(cfg, "prediction_cycle", stale_after_seconds=lock_stale_seconds) as lock:
        if not lock.acquired:
            report.update(
                {
                    "status": "skipped_existing_prediction_cycle",
                    "blockers": ["prediction_cycle_lock_already_held"],
                    "runtime_lock": lock.as_dict(),
                    "paper_trading_invoked": False,
                }
            )
            write_json(cfg.governance_root / "live_paper_bridge_cycle.json", report)
            return report
        price_action = build_price_action_paper_signals(cfg)
        broker = paper_trade(cfg)
        actual_profit_target = write_profit_target_tracker(cfg, broker if isinstance(broker, dict) else {})
        report.update(
            {
                "price_action_paper_signals": price_action,
                "signals_approved": int(price_action.get("signals", 0) or 0) if isinstance(price_action, dict) else 0,
                "signals_rejected": int(price_action.get("rejections", 0) or 0) if isinstance(price_action, dict) else 0,
                "price_action_decision": price_action.get("decision") if isinstance(price_action, dict) else "",
                "paper_confirmation_candidates": price_action.get("paper_confirmation_candidates")
                if isinstance(price_action, dict)
                else None,
                "broker": broker,
                "actual_profit_target": actual_profit_target,
                "runtime_lock": lock.as_dict(),
            }
        )
        write_json(cfg.governance_root / "live_paper_bridge_cycle.json", report)
        return report


def _run_prediction_cycle(
    *,
    config_path: Path,
    paper_source: str,
    prediction_mode: str = "paper-bridge",
) -> dict[str, Any]:
    if prediction_mode == "full":
        cfg = load_config(config_path)
        return run_paper_cycle(cfg, source=paper_source)
    return _run_live_paper_bridge_cycle(config_path=config_path, paper_source=paper_source)


def _run_governance_refresh(*, config_path: Path) -> dict[str, Any]:
    """Refresh quant learning/governance artefacts without placing orders.

    The live WebSocket loop owns dashboard rendering so the dashboard only has
    one writer. This refresh updates price-action model evidence, feedback,
    price-action paper-signal evidence, promotion review, cohort P&L, and the
    profit-goal plan from the current paper/shadow evidence.
    """
    cfg = load_config(config_path)
    return refresh_governance(cfg, refresh_dashboard=False)


def _write_governance_refresh_heartbeat(
    cfg,
    *,
    status: str,
    live_iteration: int,
    summary: dict[str, Any] | None = None,
    error: str = "",
    started_at_utc: str = "",
) -> None:
    summary = summary or {}
    payload: dict[str, Any] = {
        "status": status,
        "generated_at_utc": now_utc(),
        "live_iteration": live_iteration,
        "summary": summary,
    }
    if started_at_utc:
        payload["started_at_utc"] = started_at_utc
    if error:
        payload["error"] = error
    write_json(cfg.governance_root / "governance_refresh_heartbeat.json", payload)


def _run_degraded_prediction_cycle(*, config_path: Path, paper_source: str, guard: dict[str, Any]) -> dict[str, Any]:
    """Run a bounded websocket-only paper cycle while the full loop is degraded.

    The source is still the canonical paper cycle, but the current local loop
    feeds it websocket features capped by the degraded asset budget.  This keeps
    scoring/paper-signal generation alive without enabling the heavier scanner,
    optimisation, or discovery lanes.
    """
    cfg = load_config(config_path)
    result = run_paper_cycle(cfg, source=paper_source)
    if isinstance(result, dict):
        result = dict(result)
        result["mode"] = "degraded_websocket_prediction_refresh"
        result["resource_guard"] = guard
        result["message"] = "Ran bounded websocket paper cycle while heavy prediction/discovery remained guarded off."
        write_json(cfg.governance_root / "forward_paper_cycle.json", result)
        return result
    return {
        "status": "ran",
        "mode": "degraded_websocket_prediction_refresh",
        "source": paper_source,
        "resource_guard": guard,
    }


def _summarise_prediction_cycle(result: dict[str, Any], *, paper_source: str, started_at_utc: str = "") -> dict[str, Any]:
    broker = result.get("broker", {}) if isinstance(result, dict) else {}
    summary = {
        "status": result.get("status", "ran") if isinstance(result, dict) else "ran",
        "mode": result.get("mode", "") if isinstance(result, dict) else "",
        "source": paper_source,
        "generated_at_utc": result.get("generated_at_utc", "") if isinstance(result, dict) else "",
        "started_at_utc": started_at_utc,
        "features": result.get("features") if isinstance(result, dict) else None,
        "predictions": result.get("predictions") if isinstance(result, dict) else None,
        "signals_approved": result.get("signals_approved") if isinstance(result, dict) else None,
        "signals_rejected": result.get("signals_rejected") if isinstance(result, dict) else None,
        "equity": broker.get("equity") if isinstance(broker, dict) else None,
    }
    return {key: value for key, value in summary.items() if value not in {None, ""}}


def _running_prediction_summary(*, paper_source: str, started_at_utc: str) -> dict[str, Any]:
    return {
        "status": "running",
        "source": paper_source,
        "started_at_utc": started_at_utc,
    }


def _truthy_setting(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _local_live_resource_guard(cfg) -> dict[str, Any]:
    guard = discovery_loop._resource_guard(cfg)
    settings = cfg.raw.get("runtime_resource_guard", {}) or {}
    degraded_cap = int(settings.get("degraded_max_websocket_assets", 80) or 80)
    guard["degrade_live_loop"] = bool(guard.get("skip_cycle"))
    guard["degraded_max_websocket_assets"] = max(1, degraded_cap)
    guard["skip_prediction_cycle"] = bool(
        guard.get("skip_cycle")
        and _truthy_setting(settings.get("skip_prediction_on_high_memory"), default=True)
    )
    guard["skip_discovery_cycle"] = bool(
        guard.get("skip_cycle")
        and _truthy_setting(settings.get("skip_discovery_on_high_memory"), default=True)
    )
    return guard


def _effective_max_assets(requested_max_assets: int, guard: dict[str, Any]) -> int:
    requested = max(1, int(requested_max_assets or 1))
    if not guard.get("degrade_live_loop"):
        return requested
    degraded = max(1, int(guard.get("degraded_max_websocket_assets") or requested))
    return min(requested, degraded)


def _resource_skipped_prediction_summary(*, paper_source: str, guard: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "skipped_resource_guard",
        "source": paper_source,
        "generated_at_utc": now_utc(),
        "resource_guard": guard,
        "message": "Prediction cycle skipped to protect local machine resources; websocket marking remains live.",
    }


def _degraded_prediction_refresh_enabled(cfg, guard: dict[str, Any]) -> bool:
    settings = cfg.raw.get("runtime_resource_guard", {}) or {}
    return bool(
        guard.get("skip_prediction_cycle")
        and _truthy_setting(settings.get("allow_degraded_websocket_prediction_refresh"), default=True)
    )


def _degraded_prediction_cycle_seconds(cfg, fallback_seconds: float) -> float:
    settings = cfg.raw.get("runtime_resource_guard", {}) or {}
    configured = safe_float(settings.get("degraded_prediction_cycle_seconds"))
    if configured is None:
        configured = max(60.0, float(fallback_seconds or 0.0))
    return max(15.0, float(configured))


def _heavy_future_running(*futures: Future[Any] | None) -> bool:
    return any(future is not None and not future.done() for future in futures)


def _future_running_seconds(
    future: Future[Any] | None,
    *,
    started_ts: float,
    now_ts: float,
) -> float | None:
    if future is None or future.done() or started_ts <= 0:
        return None
    return max(0.0, float(now_ts) - float(started_ts))


def _future_exceeded_runtime(
    future: Future[Any] | None,
    *,
    started_ts: float,
    max_runtime_seconds: float,
    now_ts: float,
) -> bool:
    running_seconds = _future_running_seconds(
        future,
        started_ts=started_ts,
        now_ts=now_ts,
    )
    if running_seconds is None:
        return False
    if max_runtime_seconds <= 0:
        return False
    return running_seconds >= max_runtime_seconds


def _stale_background_summary(
    *,
    job_name: str,
    started_at_utc: str,
    running_seconds: float | None,
    max_runtime_seconds: float,
) -> dict[str, Any]:
    return {
        "status": "stale_timeout_abandoned",
        "job": job_name,
        "started_at_utc": started_at_utc,
        "generated_at_utc": now_utc(),
        "running_seconds": round(float(running_seconds or 0.0), 3),
        "max_runtime_seconds": round(float(max_runtime_seconds), 3),
        "message": (
            f"{job_name} exceeded the local live-loop supervisor timeout. The loop will exit "
            "fail-closed so the process supervisor can restart it and clear any stuck worker."
        ),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }


def _exit_for_stale_background_job() -> None:
    """Hard-exit so Docker/Task Scheduler restarts the loop and kills stuck workers.

    Python cannot safely terminate an already-running thread in a
    ThreadPoolExecutor.  A soft "abandon" leaves the thread alive and can leak
    CPU/memory.  The local/VPS runner is supervised, so the fail-closed action is
    to publish the stale heartbeat and exit the process.
    """
    os._exit(75)


def _background_job_blocks_governance(
    future: Future[Any] | None,
    *,
    started_ts: float,
    max_block_seconds: float,
    now_ts: float,
) -> bool:
    """Return whether a running background job may still delay governance.

    Prediction/discovery work can be useful, but it is not allowed to starve
    model/governance refresh indefinitely. Once a background lane has run
    longer than the configured block window, governance is allowed to start on
    its own executor while websocket marking continues.
    """
    running_seconds = _future_running_seconds(
        future,
        started_ts=started_ts,
        now_ts=now_ts,
    )
    if running_seconds is None:
        return False
    if max_block_seconds <= 0:
        return False
    return running_seconds < max_block_seconds


def _prediction_blocks_governance(
    prediction_future: Future[Any] | None,
    *,
    prediction_started_ts: float,
    max_block_seconds: float,
    now_ts: float,
) -> bool:
    return _background_job_blocks_governance(
        prediction_future,
        started_ts=prediction_started_ts,
        max_block_seconds=max_block_seconds,
        now_ts=now_ts,
    )


def _background_jobs_block_discovery(
    *,
    prediction_future: Future[Any] | None,
    prediction_started_ts: float,
    prediction_block_seconds: float,
    governance_future: Future[Any] | None,
    governance_started_ts: float,
    governance_block_seconds: float,
    now_ts: float,
) -> bool:
    """Return whether fresh background work should briefly delay discovery.

    Discovery keeps the websocket market universe fresh.  A just-started
    prediction/governance job may delay it to avoid needless contention, but a
    long-running job must not starve discovery forever.  Use the same bounded
    block semantics as governance scheduling.
    """
    return _background_job_blocks_governance(
        prediction_future,
        started_ts=prediction_started_ts,
        max_block_seconds=prediction_block_seconds,
        now_ts=now_ts,
    ) or _background_job_blocks_governance(
        governance_future,
        started_ts=governance_started_ts,
        max_block_seconds=governance_block_seconds,
        now_ts=now_ts,
    )


def _background_jobs_block_prediction(
    *,
    prediction_mode: str = "full",
    discovery_future: Future[Any] | None,
    discovery_started_ts: float,
    discovery_block_seconds: float,
    governance_future: Future[Any] | None,
    governance_started_ts: float,
    governance_block_seconds: float,
    now_ts: float,
) -> bool:
    """Return whether background jobs may still delay fresh predictions.

    A slow discovery pass should not starve the live paper prediction lane.  It
    can delay prediction briefly to avoid contention, but after the configured
    block window the system must keep re-scoring the current websocket universe
    rather than waiting forever for broader discovery to finish.

    The fast paper bridge is deliberately lightweight: it converts already
    collected websocket bid/ask evidence into governed paper signals and broker
    refreshes.  Letting discovery/governance starve that hot bridge recreates
    stale dashboard/broker behaviour, so only the full canonical prediction
    path waits behind other heavy background lanes.
    """
    if prediction_mode == "paper-bridge":
        return False
    return _background_job_blocks_governance(
        discovery_future,
        started_ts=discovery_started_ts,
        max_block_seconds=discovery_block_seconds,
        now_ts=now_ts,
    ) or _background_job_blocks_governance(
        governance_future,
        started_ts=governance_started_ts,
        max_block_seconds=governance_block_seconds,
        now_ts=now_ts,
    )


def _governance_due_blocks_prediction(*, prediction_mode: str, governance_due_now: bool) -> bool:
    """Return whether due governance should get priority over prediction.

    Full canonical prediction is heavy and can wait one tick for a due
    governance refresh.  The live paper bridge is the hot trade/no-trade lane
    and runs on its own executor, so a due governance refresh should not stop it
    from checking for fresh executable paper candidates.
    """
    return bool(governance_due_now and prediction_mode != "paper-bridge")


def _governance_refresh_due(
    *,
    now_ts: float,
    cycle_seconds: float,
    next_refresh: float,
    governance_future: Future[Any] | None,
) -> bool:
    return cycle_seconds > 0 and now_ts >= next_refresh and governance_future is None


def _first_discovery_should_precede_governance(
    *,
    discovery_iteration: int,
    last_discovery_summary: dict[str, Any],
) -> bool:
    """Let the first market-universe refresh run before immediate governance.

    Governance is important, but after a container restart the bot needs at
    least one fresh discovery pass so websocket targets and paper-candidate
    evidence are not built from stale pre-deploy rows.
    """
    status = str(last_discovery_summary.get("status") or "").strip().lower()
    return discovery_iteration <= 0 and status in {"", "not_run_yet", "skipped"}


def _degraded_discovery_refresh(cfg, guard: dict[str, Any], *, reason: str) -> dict[str, Any]:
    """Run the cheapest edge-relevant discovery lane while heavy discovery is guarded off.

    Full scanner discovery can be memory/CPU heavy on a local laptop.  The fast
    crypto Up/Down lane is deliberately bounded by slots/tokens and is the lane
    with current positive cohort evidence, so degraded mode should keep it fresh
    instead of letting the only promoted short-horizon opportunity source go
    stale.
    """
    settings = cfg.raw.get("runtime_resource_guard", {}) or {}
    if not _truthy_setting(settings.get("allow_degraded_fast_updown_refresh"), default=True):
        return {
            "status": "skipped_resource_guard",
            "mode": "degraded_discovery_disabled",
            "generated_at_utc": now_utc(),
            "live_data": False,
            "live_trading": False,
            "resource_guard": guard,
            "message": "Background discovery skipped to protect local machine resources.",
            "reason": reason,
        }
    try:
        fast_updown = refresh_fast_updown_snapshot(cfg)
        tokens = int(safe_float(fast_updown.get("tokens")) or 0) if isinstance(fast_updown, dict) else 0
        return {
            "status": "ran",
            "mode": "degraded_fast_updown_refresh",
            "generated_at_utc": now_utc(),
            "live_data": tokens > 0,
            "live_trading": False,
            "resource_guard": guard,
            "reason": reason,
            "scan": {
                "status": "skipped_resource_guard",
                "message": "Full scanner discovery skipped; refreshed bounded fast Up/Down lane only.",
            },
            "fast_updown": fast_updown,
            "optimization": {"status": "skipped_resource_guard"},
        }
    except Exception as exc:  # noqa: BLE001 - degraded refresh should never stop the live tick
        return {
            "status": "error",
            "mode": "degraded_fast_updown_refresh",
            "generated_at_utc": now_utc(),
            "live_data": False,
            "live_trading": False,
            "resource_guard": guard,
            "reason": reason,
            "error": f"{type(exc).__name__}: {exc}",
            "message": "Fast Up/Down degraded refresh failed; websocket marking remains live.",
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_polymarket_local_live_loop.py")
    parser.add_argument("--config", default="polymarket_predictive_config.example.yaml")
    parser.add_argument("--websocket-seconds", type=int, default=5, help="WebSocket collection window per live tick.")
    parser.add_argument("--sleep-seconds", type=float, default=0.0, help="Optional sleep after each tick.")
    parser.add_argument("--iterations", type=int, default=0, help="0 means run forever.")
    parser.add_argument("--max-assets", type=int, default=250)
    parser.add_argument("--include-config-websocket-assets", action="store_true", help="Also subscribe to static config assets after dynamic assets.")
    parser.add_argument(
        "--prediction-cycle-seconds",
        type=float,
        default=15.0,
        help="How often to run the full paper-cycle prediction path; websocket marking still updates every tick.",
    )
    parser.add_argument(
        "--discovery-cycle-seconds",
        type=float,
        default=300.0,
        help="Periodically refresh scanner discovery so websocket assets follow changing markets.",
    )
    parser.add_argument(
        "--governance-refresh-seconds",
        type=float,
        default=120.0,
        help="How often to refresh quant governance, price-action model evidence, feedback, and paper-signal learning artefacts.",
    )
    parser.add_argument(
        "--prediction-governance-block-seconds",
        type=float,
        default=180.0,
        help="Maximum time a running prediction job may delay governance refresh; 0 means prediction never blocks governance.",
    )
    parser.add_argument(
        "--discovery-governance-block-seconds",
        type=float,
        default=180.0,
        help="Maximum time a running discovery job may delay governance refresh; 0 means discovery never blocks governance.",
    )
    parser.add_argument(
        "--discovery-max-runtime-seconds",
        type=float,
        default=float(os.environ.get("POLYMARKET_DISCOVERY_MAX_RUNTIME_SECONDS", "900") or 900),
        help="Fail-closed abandon a background discovery pass after this many seconds; 0 disables.",
    )
    parser.add_argument(
        "--prediction-max-runtime-seconds",
        type=float,
        default=float(os.environ.get("POLYMARKET_PREDICTION_MAX_RUNTIME_SECONDS", "600") or 600),
        help="Fail-closed abandon a background prediction pass after this many seconds; 0 disables.",
    )
    parser.add_argument(
        "--governance-max-runtime-seconds",
        type=float,
        default=float(os.environ.get("POLYMARKET_GOVERNANCE_MAX_RUNTIME_SECONDS", "600") or 600),
        help="Fail-closed abandon a background governance refresh after this many seconds; 0 disables.",
    )
    parser.add_argument("--paper-source", choices=["raw_snapshot", "websocket"], default="raw_snapshot")
    parser.add_argument(
        "--prediction-mode",
        choices=["paper-bridge", "full"],
        default="paper-bridge",
        help="paper-bridge keeps the hot live lane fast; full runs the canonical feature/prediction cycle.",
    )
    parser.add_argument("--optimize-model", action="store_true", help="Allow the slower discovery lane to run scheduled model optimisation.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    force_local_safe_environment()
    cfg = load_config(ROOT / args.config)
    cfg.output_root.mkdir(parents=True, exist_ok=True)
    cfg.governance_root.mkdir(parents=True, exist_ok=True)

    config_path = ROOT / args.config
    next_prediction_cycle = time.time() + args.prediction_cycle_seconds if args.prediction_cycle_seconds > 0 else float("inf")
    next_degraded_prediction_cycle = time.time()
    next_discovery_cycle = _initial_discovery_due_timestamp(args.discovery_cycle_seconds)
    next_governance_refresh = time.time() if args.governance_refresh_seconds > 0 else float("inf")
    discovery_iteration = 0
    last_discovery_summary: dict[str, Any] = {"status": "not_run_yet"}
    discovery_future: Future[tuple[int, dict[str, Any]]] | None = None
    discovery_running_iteration = 0
    discovery_started_at_utc = ""
    discovery_started_ts = 0.0
    prediction_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="polymarket-prediction")
    prediction_future: Future[dict[str, Any]] | None = None
    prediction_started_at_utc = ""
    prediction_started_ts = 0.0
    last_prediction_summary: dict[str, Any] = {"status": "not_started", "source": args.paper_source}
    governance_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="polymarket-governance")
    governance_future: Future[dict[str, Any]] | None = None
    governance_started_at_utc = ""
    governance_started_ts = 0.0
    last_governance_summary: dict[str, Any] = {"status": "not_started"}
    iteration = 0
    failures = 0
    discovery_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="polymarket-discovery")
    print("local-live-loop: started; press Ctrl+C to stop", flush=True)
    try:
        while args.iterations <= 0 or iteration < args.iterations:
            iteration += 1
            started = time.time()
            try:
                discovery_summary: dict[str, Any] = {"status": "skipped"}
                now_for_timeout = time.time()
                if _future_exceeded_runtime(
                    discovery_future,
                    started_ts=discovery_started_ts,
                    max_runtime_seconds=args.discovery_max_runtime_seconds,
                    now_ts=now_for_timeout,
                ):
                    running_seconds = _future_running_seconds(
                        discovery_future,
                        started_ts=discovery_started_ts,
                        now_ts=now_for_timeout,
                    )
                    last_discovery_summary = _stale_background_summary(
                        job_name="discovery",
                        started_at_utc=discovery_started_at_utc,
                        running_seconds=running_seconds,
                        max_runtime_seconds=args.discovery_max_runtime_seconds,
                    )
                    _write_discovery_heartbeat(
                        cfg,
                        status="stale_timeout_abandoned",
                        live_iteration=iteration,
                        discovery_iteration=discovery_running_iteration,
                        summary=last_discovery_summary,
                        error="discovery_max_runtime_exceeded",
                        started_at_utc=discovery_started_at_utc,
                    )
                    _exit_for_stale_background_job()
                if _future_exceeded_runtime(
                    prediction_future,
                    started_ts=prediction_started_ts,
                    max_runtime_seconds=args.prediction_max_runtime_seconds,
                    now_ts=now_for_timeout,
                ):
                    running_seconds = _future_running_seconds(
                        prediction_future,
                        started_ts=prediction_started_ts,
                        now_ts=now_for_timeout,
                    )
                    last_prediction_summary = _stale_background_summary(
                        job_name="prediction",
                        started_at_utc=prediction_started_at_utc,
                        running_seconds=running_seconds,
                        max_runtime_seconds=args.prediction_max_runtime_seconds,
                    )
                    last_prediction_summary["source"] = args.paper_source
                    write_json(
                        cfg.governance_root / "local_live_loop_heartbeat.json",
                        {
                            **last_prediction_summary,
                            "iteration": iteration,
                            "heavy_background_job": "prediction",
                            "error": "prediction_max_runtime_exceeded",
                        },
                    )
                    _exit_for_stale_background_job()
                if _future_exceeded_runtime(
                    governance_future,
                    started_ts=governance_started_ts,
                    max_runtime_seconds=args.governance_max_runtime_seconds,
                    now_ts=now_for_timeout,
                ):
                    running_seconds = _future_running_seconds(
                        governance_future,
                        started_ts=governance_started_ts,
                        now_ts=now_for_timeout,
                    )
                    last_governance_summary = _stale_background_summary(
                        job_name="governance",
                        started_at_utc=governance_started_at_utc,
                        running_seconds=running_seconds,
                        max_runtime_seconds=args.governance_max_runtime_seconds,
                    )
                    _write_governance_refresh_heartbeat(
                        cfg,
                        status="stale_timeout_abandoned",
                        live_iteration=iteration,
                        summary=last_governance_summary,
                        error="governance_max_runtime_exceeded",
                        started_at_utc=governance_started_at_utc,
                    )
                    _exit_for_stale_background_job()
                if discovery_future is not None and discovery_future.done():
                    try:
                        discovery_iteration, last_discovery_summary = discovery_future.result()
                        _write_discovery_heartbeat(
                            cfg,
                            status=str(last_discovery_summary.get("status") or "unknown"),
                            live_iteration=iteration,
                            discovery_iteration=discovery_iteration,
                            summary=last_discovery_summary,
                            started_at_utc=discovery_started_at_utc,
                        )
                    except Exception as exc:  # noqa: BLE001 - discovery must not stop live websocket ticks
                        failures += 1
                        last_discovery_summary = {
                            "status": "error",
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                        _write_discovery_heartbeat(
                            cfg,
                            status="error",
                            live_iteration=iteration,
                            discovery_iteration=discovery_running_iteration,
                            summary=last_discovery_summary,
                            error=str(last_discovery_summary["error"]),
                            started_at_utc=discovery_started_at_utc,
                        )
                    finally:
                        discovery_future = None
                        discovery_running_iteration = 0
                        discovery_started_at_utc = ""
                        discovery_started_ts = 0.0
                        next_discovery_cycle = time.time() + args.discovery_cycle_seconds
                if prediction_future is not None and prediction_future.done():
                    try:
                        last_prediction_summary = _summarise_prediction_cycle(
                            prediction_future.result(),
                            paper_source=args.paper_source,
                            started_at_utc=prediction_started_at_utc,
                        )
                    except Exception as exc:  # noqa: BLE001 - prediction must not stop live websocket ticks
                        failures += 1
                        last_prediction_summary = {
                            "status": "error",
                            "source": args.paper_source,
                            "started_at_utc": prediction_started_at_utc,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    finally:
                        prediction_future = None
                        prediction_started_at_utc = ""
                        prediction_started_ts = 0.0
                        if last_prediction_summary.get("mode") == "degraded_websocket_prediction_refresh":
                            next_degraded_prediction_cycle = time.time() + _degraded_prediction_cycle_seconds(
                                cfg,
                                args.prediction_cycle_seconds,
                            )
                        next_prediction_cycle = (
                            time.time() + args.prediction_cycle_seconds
                            if args.prediction_cycle_seconds > 0
                            else float("inf")
                        )
                if governance_future is not None and governance_future.done():
                    try:
                        last_governance_summary = governance_future.result()
                        _write_governance_refresh_heartbeat(
                            cfg,
                            status=str(last_governance_summary.get("status") or "unknown"),
                            live_iteration=iteration,
                            summary=last_governance_summary,
                            started_at_utc=governance_started_at_utc,
                        )
                    except Exception as exc:  # noqa: BLE001 - governance refresh must not stop live websocket ticks
                        failures += 1
                        last_governance_summary = {
                            "status": "error",
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                        _write_governance_refresh_heartbeat(
                            cfg,
                            status="error",
                            live_iteration=iteration,
                            summary=last_governance_summary,
                            error=str(last_governance_summary["error"]),
                            started_at_utc=governance_started_at_utc,
                        )
                    finally:
                        governance_future = None
                        governance_started_at_utc = ""
                        governance_started_ts = 0.0
                        next_governance_refresh = (
                            time.time() + args.governance_refresh_seconds
                            if args.governance_refresh_seconds > 0
                            else float("inf")
                        )

                prediction_lock_maintenance = _clear_orphaned_same_process_prediction_lock(cfg, prediction_future)
                resource_guard = _local_live_resource_guard(cfg)
                write_json(
                    cfg.governance_root / "runtime_resource_guard.json",
                    {**resource_guard, "generated_at_utc": now_utc()},
                )
                effective_max_assets = _effective_max_assets(args.max_assets, resource_guard)

                asset_ids, token_sources = discover_websocket_asset_ids(
                    cfg,
                    include_static_config=args.include_config_websocket_assets,
                    max_assets=effective_max_assets,
                )
                if not asset_ids and args.discovery_cycle_seconds > 0:
                    if resource_guard.get("skip_discovery_cycle"):
                        discovery_summary = _degraded_discovery_refresh(
                            cfg,
                            resource_guard,
                            reason="no_websocket_assets",
                        )
                        last_discovery_summary = discovery_summary
                        next_discovery_cycle = time.time() + args.discovery_cycle_seconds
                    else:
                        discovery_iteration, discovery_summary = _run_discovery_iteration(
                            config_path=config_path,
                            optimize_model=args.optimize_model,
                            discovery_iteration=discovery_iteration,
                            paper_source="raw_snapshot",
                        )
                        last_discovery_summary = discovery_summary
                        next_discovery_cycle = time.time() + args.discovery_cycle_seconds
                    asset_ids, token_sources = discover_websocket_asset_ids(
                        cfg,
                        include_static_config=args.include_config_websocket_assets,
                        max_assets=effective_max_assets,
                    )
                if not asset_ids:
                    raise RuntimeError("No websocket asset ids found from positions, signals, predictions, scanner snapshot, or model probabilities")
                configure_websocket_assets(cfg, asset_ids)
                websocket = collect_websocket(cfg, websocket_seconds=max(1, args.websocket_seconds))
                features, quality, websocket_features = normalize_websocket_file(cfg)
                features = enrich_websocket_features_with_scanner_metadata(cfg, features)
                ingest = ingest_websocket_features_into_ledger(cfg, features)
                try:
                    price_action_scout = build_price_action_scout(cfg)
                except Exception as exc:  # noqa: BLE001 - scout marking must not stop live websocket ticks
                    failures += 1
                    price_action_scout = {
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                        "paper_trading_invoked": False,
                        "live_trading_invoked": False,
                    }

                full_cycle = dict(last_prediction_summary)
                now_ts = time.time()
                prediction_running_seconds = _future_running_seconds(
                    prediction_future,
                    started_ts=prediction_started_ts,
                    now_ts=now_ts,
                )
                prediction_blocks_governance = _prediction_blocks_governance(
                    prediction_future,
                    prediction_started_ts=prediction_started_ts,
                    max_block_seconds=args.prediction_governance_block_seconds,
                    now_ts=now_ts,
                )
                discovery_running_seconds = _future_running_seconds(
                    discovery_future,
                    started_ts=discovery_started_ts,
                    now_ts=now_ts,
                )
                governance_running_seconds = _future_running_seconds(
                    governance_future,
                    started_ts=governance_started_ts,
                    now_ts=now_ts,
                )
                discovery_blocks_governance = _background_job_blocks_governance(
                    discovery_future,
                    started_ts=discovery_started_ts,
                    max_block_seconds=args.discovery_governance_block_seconds,
                    now_ts=now_ts,
                )
                governance_due_now = _governance_refresh_due(
                    now_ts=now_ts,
                    cycle_seconds=args.governance_refresh_seconds,
                    next_refresh=next_governance_refresh,
                    governance_future=governance_future,
                )
                background_jobs_block_prediction = _background_jobs_block_prediction(
                    prediction_mode=args.prediction_mode,
                    discovery_future=discovery_future,
                    discovery_started_ts=discovery_started_ts,
                    discovery_block_seconds=args.discovery_governance_block_seconds,
                    governance_future=governance_future,
                    governance_started_ts=governance_started_ts,
                    governance_block_seconds=args.prediction_governance_block_seconds,
                    now_ts=now_ts,
                )
                governance_due_blocks_prediction = _governance_due_blocks_prediction(
                    prediction_mode=args.prediction_mode,
                    governance_due_now=governance_due_now,
                )
                if prediction_future is not None and not prediction_future.done():
                    full_cycle = _running_prediction_summary(
                        paper_source=args.paper_source,
                        started_at_utc=prediction_started_at_utc,
                    )
                elif (
                    args.prediction_cycle_seconds > 0
                    and now_ts >= next_prediction_cycle
                    and not governance_due_blocks_prediction
                    and not background_jobs_block_prediction
                ):
                    if resource_guard.get("skip_prediction_cycle"):
                        degraded_prediction_enabled = _degraded_prediction_refresh_enabled(cfg, resource_guard)
                        if degraded_prediction_enabled and time.time() >= next_degraded_prediction_cycle:
                            prediction_started_at_utc = now_utc()
                            prediction_started_ts = time.time()
                            prediction_future = prediction_executor.submit(
                                _run_degraded_prediction_cycle,
                                config_path=config_path,
                                paper_source=args.paper_source,
                                guard=resource_guard,
                            )
                            next_prediction_cycle = float("inf")
                            next_degraded_prediction_cycle = float("inf")
                            full_cycle = _running_prediction_summary(
                                paper_source=args.paper_source,
                                started_at_utc=prediction_started_at_utc,
                            )
                            full_cycle["mode"] = "degraded_websocket_prediction_refresh"
                        else:
                            if degraded_prediction_enabled:
                                full_cycle = dict(last_prediction_summary)
                                full_cycle.update(
                                    {
                                        "status": "waiting_degraded_prediction_cooldown",
                                        "source": args.paper_source,
                                        "resource_guard": resource_guard,
                                        "next_degraded_due_in_seconds": None
                                        if next_degraded_prediction_cycle == float("inf")
                                        else max(0.0, round(next_degraded_prediction_cycle - time.time(), 3)),
                                        "message": "Heavy prediction skipped; bounded websocket scoring will run when the degraded cooldown expires.",
                                    }
                                )
                            else:
                                last_prediction_summary = _resource_skipped_prediction_summary(
                                    paper_source=args.paper_source,
                                    guard=resource_guard,
                                )
                                full_cycle = dict(last_prediction_summary)
                            next_prediction_cycle = time.time() + args.prediction_cycle_seconds
                    else:
                        prediction_started_at_utc = now_utc()
                        prediction_started_ts = time.time()
                        prediction_future = prediction_executor.submit(
                            _run_prediction_cycle,
                            config_path=config_path,
                            paper_source=args.paper_source,
                            prediction_mode=args.prediction_mode,
                        )
                        next_prediction_cycle = float("inf")
                        full_cycle = _running_prediction_summary(
                            paper_source=args.paper_source,
                            started_at_utc=prediction_started_at_utc,
                        )
                prediction_running_seconds = _future_running_seconds(
                    prediction_future,
                    started_ts=prediction_started_ts,
                    now_ts=time.time(),
                )
                prediction_blocks_governance = _prediction_blocks_governance(
                    prediction_future,
                    prediction_started_ts=prediction_started_ts,
                    max_block_seconds=args.prediction_governance_block_seconds,
                    now_ts=time.time(),
                )
                discovery_running_seconds = _future_running_seconds(
                    discovery_future,
                    started_ts=discovery_started_ts,
                    now_ts=time.time(),
                )
                discovery_blocks_governance = _background_job_blocks_governance(
                    discovery_future,
                    started_ts=discovery_started_ts,
                    max_block_seconds=args.discovery_governance_block_seconds,
                    now_ts=time.time(),
                )
                background_jobs_block_prediction = _background_jobs_block_prediction(
                    prediction_mode=args.prediction_mode,
                    discovery_future=discovery_future,
                    discovery_started_ts=discovery_started_ts,
                    discovery_block_seconds=args.discovery_governance_block_seconds,
                    governance_future=governance_future,
                    governance_started_ts=governance_started_ts,
                    governance_block_seconds=args.prediction_governance_block_seconds,
                    now_ts=time.time(),
                )
                next_discovery_in_seconds = None
                if next_discovery_cycle != float("inf"):
                    next_discovery_in_seconds = max(0.0, round(next_discovery_cycle - time.time(), 3))
                next_degraded_prediction_in_seconds = None
                if next_degraded_prediction_cycle != float("inf"):
                    next_degraded_prediction_in_seconds = max(0.0, round(next_degraded_prediction_cycle - time.time(), 3))
                discovery_is_running = discovery_future is not None and not discovery_future.done()
                governance_is_running = governance_future is not None and not governance_future.done()
                next_governance_in_seconds = None
                if next_governance_refresh != float("inf"):
                    next_governance_in_seconds = max(0.0, round(next_governance_refresh - time.time(), 3))

                loop_summary = {
                    "status": "ok",
                    "iteration": iteration,
                    "generated_at_utc": now_utc(),
                    "heavy_background_job": (
                        "discovery"
                        if discovery_is_running
                        else "governance"
                        if governance_is_running
                        else "prediction"
                        if prediction_future is not None and not prediction_future.done()
                        else ""
                    ),
                    "asset_count": len(asset_ids),
                    "requested_max_assets": args.max_assets,
                    "effective_max_assets": effective_max_assets,
                    "websocket_seconds": args.websocket_seconds,
                    "prediction_cycle_seconds": args.prediction_cycle_seconds,
                    "prediction_mode": args.prediction_mode,
                    "prediction_governance_block_seconds": args.prediction_governance_block_seconds,
                    "prediction_max_runtime_seconds": args.prediction_max_runtime_seconds,
                    "prediction_running_seconds": prediction_running_seconds,
                    "prediction_blocks_governance": prediction_blocks_governance,
                    "governance_due_blocks_prediction": governance_due_blocks_prediction,
                    "prediction_lock_maintenance": prediction_lock_maintenance,
                    "prediction_no_longer_blocks_governance": bool(
                        prediction_future is not None
                        and not prediction_future.done()
                        and not prediction_blocks_governance
                    ),
                    "discovery_governance_block_seconds": args.discovery_governance_block_seconds,
                    "discovery_max_runtime_seconds": args.discovery_max_runtime_seconds,
                    "discovery_running_seconds": discovery_running_seconds,
                    "discovery_blocks_governance": discovery_blocks_governance,
                    "background_jobs_block_prediction": background_jobs_block_prediction,
                    "discovery_no_longer_blocks_prediction": bool(
                        discovery_future is not None
                        and not discovery_future.done()
                        and not background_jobs_block_prediction
                    ),
                    "discovery_no_longer_blocks_governance": bool(
                        discovery_future is not None
                        and not discovery_future.done()
                        and not discovery_blocks_governance
                    ),
                    "governance_running_seconds": governance_running_seconds,
                    "governance_max_runtime_seconds": args.governance_max_runtime_seconds,
                    "degraded_prediction_cycle_seconds": _degraded_prediction_cycle_seconds(cfg, args.prediction_cycle_seconds),
                    "degraded_prediction_next_due_in_seconds": next_degraded_prediction_in_seconds,
                    "asset_sources": {source: list(token_sources.values()).count(source) for source in sorted(set(token_sources.values()))},
                    "websocket": websocket,
                    "websocket_features": websocket_features,
                    "websocket_quality_rows": len(quality),
                    "discovery": {
                        "status": "running"
                        if discovery_is_running
                        else discovery_summary.get("status")
                        if isinstance(discovery_summary, dict)
                        else "unknown",
                        "scan": discovery_summary.get("scan", {}) if isinstance(discovery_summary, dict) else {},
                        "last_status": last_discovery_summary.get("status")
                        if isinstance(last_discovery_summary, dict)
                        else "unknown",
                        "last_scan": last_discovery_summary.get("scan", {})
                        if isinstance(last_discovery_summary, dict)
                        else {},
                        "last_fast_updown": last_discovery_summary.get("fast_updown", {})
                        if isinstance(last_discovery_summary, dict)
                        else {},
                        "discovery_iteration": discovery_iteration,
                        "running_iteration": discovery_running_iteration if discovery_is_running else 0,
                        "started_at_utc": discovery_started_at_utc if discovery_is_running else "",
                        "cycle_seconds": args.discovery_cycle_seconds,
                        "next_due_in_seconds": next_discovery_in_seconds,
                    },
                    "governance_refresh": {
                        "status": "running"
                        if governance_is_running
                        else last_governance_summary.get("status")
                        if isinstance(last_governance_summary, dict)
                        else "unknown",
                        "started_at_utc": governance_started_at_utc if governance_is_running else "",
                        "cycle_seconds": args.governance_refresh_seconds,
                        "next_due_in_seconds": next_governance_in_seconds,
                        "last_summary": last_governance_summary,
                    },
                    "resource_guard": resource_guard,
                    "ingest": ingest,
                    "price_action_scout": {
                        "status": price_action_scout.get("status") if isinstance(price_action_scout, dict) else "unknown",
                        "decision": price_action_scout.get("decision") if isinstance(price_action_scout, dict) else "",
                        "current_positive_analogue_targets": price_action_scout.get("current_positive_analogue_targets")
                        if isinstance(price_action_scout, dict)
                        else "",
                        "open_trades": price_action_scout.get("open_trades") if isinstance(price_action_scout, dict) else "",
                        "closed_trades": price_action_scout.get("closed_trades") if isinstance(price_action_scout, dict) else "",
                    },
                    "full_prediction_cycle": {
                        "status": full_cycle.get("status") if isinstance(full_cycle, dict) else "unknown",
                        "source": args.paper_source,
                        **(
                            {key: value for key, value in full_cycle.items() if key != "source"}
                            if isinstance(full_cycle, dict)
                            else {}
                        ),
                    },
                    "elapsed_seconds": round(time.time() - started, 3),
                }
                write_json(cfg.governance_root / "local_live_loop_heartbeat.json", loop_summary)
                forward = mark_portfolio_and_render_dashboard(
                    cfg,
                    source="websocket_live",
                    loop_summary=loop_summary,
                    features=features,
                )
                broker = forward.get("broker", {}) if isinstance(forward, dict) else {}
                print(
                    "local-live-loop "
                    f"iteration={iteration} messages={websocket.get('new_messages', 0)} "
                    f"latest_features={websocket_features.get('returned_feature_rows', websocket_features.get('new_feature_rows', 0))} "
                    f"retained_features={websocket_features.get('feature_rows', 0)} "
                    f"snapshots_inserted={ingest.get('inserted_market_snapshots', 0)} "
                    f"equity={broker.get('equity', 'n/a')} dashboard=updated",
                    flush=True,
                )
                governance_due_now = _governance_refresh_due(
                    now_ts=time.time(),
                    cycle_seconds=args.governance_refresh_seconds,
                    next_refresh=next_governance_refresh,
                    governance_future=governance_future,
                )
                prediction_blocks_governance = _prediction_blocks_governance(
                    prediction_future,
                    prediction_started_ts=prediction_started_ts,
                    max_block_seconds=args.prediction_governance_block_seconds,
                    now_ts=time.time(),
                )
                discovery_blocks_governance = _background_job_blocks_governance(
                    discovery_future,
                    started_ts=discovery_started_ts,
                    max_block_seconds=args.discovery_governance_block_seconds,
                    now_ts=time.time(),
                )
                background_jobs_block_discovery = _background_jobs_block_discovery(
                    prediction_future=prediction_future,
                    prediction_started_ts=prediction_started_ts,
                    prediction_block_seconds=args.prediction_governance_block_seconds,
                    governance_future=governance_future,
                    governance_started_ts=governance_started_ts,
                    governance_block_seconds=args.discovery_governance_block_seconds,
                    now_ts=time.time(),
                )
                first_discovery_precedes_governance = _first_discovery_should_precede_governance(
                    discovery_iteration=discovery_iteration,
                    last_discovery_summary=last_discovery_summary,
                )
                discovery_started_this_loop = False
                if (
                    time.time() >= next_discovery_cycle
                    and discovery_future is None
                    and (not governance_due_now or first_discovery_precedes_governance)
                    and not background_jobs_block_discovery
                ):
                    discovery_running_iteration = discovery_iteration + 1
                    discovery_started_at_utc = now_utc()
                    if resource_guard.get("skip_discovery_cycle"):
                        last_discovery_summary = _degraded_discovery_refresh(
                            cfg,
                            resource_guard,
                            reason="scheduled_discovery_due",
                        )
                        discovery_summary = dict(last_discovery_summary)
                        _write_discovery_heartbeat(
                            cfg,
                            status=str(last_discovery_summary.get("status") or "unknown"),
                            live_iteration=iteration,
                            discovery_iteration=discovery_running_iteration,
                            summary=last_discovery_summary,
                            started_at_utc=discovery_started_at_utc,
                        )
                        discovery_running_iteration = 0
                        discovery_started_at_utc = ""
                        next_discovery_cycle = time.time() + args.discovery_cycle_seconds
                    else:
                        discovery_started_ts = time.time()
                        discovery_future = discovery_executor.submit(
                            _run_discovery_iteration,
                            config_path=config_path,
                            optimize_model=args.optimize_model,
                            discovery_iteration=discovery_iteration,
                            paper_source="raw_snapshot",
                        )
                        next_discovery_cycle = float("inf")
                        _write_discovery_heartbeat(
                            cfg,
                            status="running",
                            live_iteration=iteration,
                            discovery_iteration=discovery_running_iteration,
                            summary={},
                            started_at_utc=discovery_started_at_utc,
                        )
                    discovery_started_this_loop = True
                if (
                    time.time() >= next_governance_refresh
                    and governance_future is None
                    and not discovery_started_this_loop
                    and not discovery_blocks_governance
                    and not prediction_blocks_governance
                ):
                    if resource_guard.get("skip_cycle"):
                        last_governance_summary = {
                            "status": "skipped_resource_guard",
                            "generated_at_utc": now_utc(),
                            "message": "Quant governance refresh skipped by resource guard; live WebSocket marking continues.",
                            "resource_guard": resource_guard,
                        }
                        _write_governance_refresh_heartbeat(
                            cfg,
                            status="skipped_resource_guard",
                            live_iteration=iteration,
                            summary=last_governance_summary,
                        )
                        next_governance_refresh = time.time() + args.governance_refresh_seconds
                    else:
                        governance_started_at_utc = now_utc()
                        governance_started_ts = time.time()
                        governance_future = governance_executor.submit(
                            _run_governance_refresh,
                            config_path=config_path,
                        )
                        next_governance_refresh = float("inf")
                        _write_governance_refresh_heartbeat(
                            cfg,
                            status="running",
                            live_iteration=iteration,
                            summary={},
                            started_at_utc=governance_started_at_utc,
                        )
            except Exception as exc:  # noqa: BLE001 - keep loop alive while surfacing the failure
                failures += 1
                traceback.print_exc()
                write_json(
                    cfg.governance_root / "local_live_loop_heartbeat.json",
                    {
                        "status": "error",
                        "iteration": iteration,
                        "generated_at_utc": now_utc(),
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
                try:
                    render_dashboard(cfg)
                except Exception:
                    pass
            if args.sleep_seconds > 0:
                time.sleep(args.sleep_seconds)
    except KeyboardInterrupt:
        print("local-live-loop: stopped by user", flush=True)
    finally:
        discovery_executor.shutdown(wait=False, cancel_futures=True)
        prediction_executor.shutdown(wait=False, cancel_futures=True)
        governance_executor.shutdown(wait=False, cancel_futures=True)
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
