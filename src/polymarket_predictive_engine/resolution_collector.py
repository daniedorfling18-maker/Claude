from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .config import EngineConfig, load_config
from .utils import boolish, discover_files, infer_category, now_utc, read_csv_rows, safe_float, write_csv, write_json

DEFAULT_GAMMA_BASE_URL = "https://gamma-api.polymarket.com/markets"


def _parse_jsonish(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return boolish(value)


def _as_text(value: Any) -> str:
    return "" if value is None else str(value)


def fetch_gamma_market(slug: str, base_url: str = DEFAULT_GAMMA_BASE_URL, timeout_seconds: int = 20) -> dict[str, Any] | None:
    query = urllib.parse.urlencode({"slug": slug})
    url = f"{base_url}?{query}"
    request = urllib.request.Request(url, headers={"User-Agent": "polymarket-predictive-engine/1.0"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # nosec B310: configured public API endpoint
        payload = json.loads(response.read().decode("utf-8"))
    if isinstance(payload, list) and payload:
        first = payload[0]
        return first if isinstance(first, dict) else None
    if isinstance(payload, dict):
        return payload
    return None


def infer_market_resolution_rows(
    market: dict[str, Any],
    *,
    category_hint: str = "",
    win_threshold: float = 0.98,
    loss_threshold: float = 0.02,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Convert one Gamma market payload into token-level resolution rows.

    This function is deliberately conservative. It only assigns targets where the market is closed and the
    settlement vector has exactly one near-one outcome and all remaining outcomes are near-zero. Active or
    ambiguous markets are retained as metadata but remain unlabelled.
    """
    slug = _as_text(market.get("slug"))
    outcomes = [_as_text(x) for x in _parse_jsonish(market.get("outcomes"))]
    token_ids = [_as_text(x) for x in _parse_jsonish(market.get("clobTokenIds"))]
    prices_raw = _parse_jsonish(market.get("outcomePrices"))
    prices = [safe_float(x) for x in prices_raw]
    closed = _as_bool(market.get("closed"))
    active = _as_bool(market.get("active"))
    category = _as_text(market.get("category")) or category_hint
    quality = "unresolved_active" if not closed else "closed_unclassified"
    target_by_idx: dict[int, int | str] = {i: "" for i in range(max(len(outcomes), len(token_ids), len(prices), 1))}
    winning_idx: int | None = None
    winning_outcome = ""
    winning_token_id = ""
    reasons: list[str] = []

    if not outcomes:
        reasons.append("missing outcomes")
        quality = "missing_outcomes"
    if not token_ids:
        reasons.append("missing clobTokenIds")
        quality = "missing_token_ids" if closed else quality
    if not prices:
        reasons.append("missing outcomePrices")
        quality = "missing_outcome_prices" if closed else quality
    if outcomes and token_ids and len(outcomes) != len(token_ids):
        reasons.append("outcomes/token id length mismatch")
        quality = "token_mapping_mismatch"
    if prices and outcomes and len(prices) != len(outcomes):
        reasons.append("outcomes/prices length mismatch")
        quality = "price_mapping_mismatch"

    if closed and outcomes and token_ids and prices and len(outcomes) == len(token_ids) == len(prices):
        if len(outcomes) != 2:
            quality = "unsupported_non_binary"
            reasons.append("v1 supports binary markets only")
        elif all(p is not None and p == 0 for p in prices):
            quality = "missing_settlement_vector"
            reasons.append("closed market has all-zero outcomePrices")
        elif any(p is None for p in prices):
            quality = "invalid_outcome_prices"
            reasons.append("one or more outcome prices are not numeric")
        else:
            winners = [i for i, p in enumerate(prices) if p is not None and p >= win_threshold]
            losers_ok = all((p is not None and (p <= loss_threshold or i in winners)) for i, p in enumerate(prices))
            if len(winners) == 1 and losers_ok:
                winning_idx = winners[0]
                winning_outcome = outcomes[winning_idx]
                winning_token_id = token_ids[winning_idx]
                target_by_idx = {i: 1 if i == winning_idx else 0 for i in range(len(outcomes))}
                quality = "clean_settlement"
            else:
                quality = "ambiguous_settlement_vector"
                reasons.append(f"outcomePrices={prices}")

    base = {
        "market_slug": slug,
        "gamma_market_id": _as_text(market.get("id")),
        "condition_id": _as_text(market.get("conditionId")),
        "question_id": _as_text(market.get("questionID")),
        "question": _as_text(market.get("question")),
        "category": category,
        "closed": closed,
        "active": active,
        "archived": _as_bool(market.get("archived")),
        "end_time": _as_text(market.get("endDate")),
        "close_time": _as_text(market.get("closedTime")) or _as_text(market.get("endDate")),
        "resolution_time": _as_text(market.get("closedTime")),
        "resolution_source": _as_text(market.get("resolutionSource")),
        "resolved_by": _as_text(market.get("resolvedBy")),
        "neg_risk": _as_bool(market.get("negRisk")),
        "accepting_orders": _as_bool(market.get("acceptingOrders")),
        "order_min_size": _as_text(market.get("orderMinSize")),
        "order_price_min_tick_size": _as_text(market.get("orderPriceMinTickSize")),
        "winning_outcome": winning_outcome,
        "winning_token_id": winning_token_id,
        "resolution_quality": quality,
        "resolution_quality_reason": "; ".join(reasons),
        "resolution_collected_at_utc": now_utc(),
    }
    row_count = max(len(outcomes), len(token_ids), len(prices), 1)
    rows: list[dict[str, Any]] = []
    for i in range(row_count):
        rows.append({
            **base,
            "outcome_index": i,
            "outcome": outcomes[i] if i < len(outcomes) else "",
            "token_id": token_ids[i] if i < len(token_ids) else "",
            "outcome_price": prices[i] if i < len(prices) and prices[i] is not None else "",
            "target": target_by_idx.get(i, ""),
        })
    quality_rows = [{
        "market_slug": slug,
        "closed": closed,
        "active": active,
        "resolution_quality": quality,
        "resolution_quality_reason": "; ".join(reasons),
        "row_count": len(rows),
    }]
    return rows, quality_rows


def _unique_raw_slugs(cfg: EngineConfig) -> dict[str, str]:
    files = discover_files(cfg.data_root, [
        "outputs/polymarket_wide/**/raw_market_snapshots.csv",
        "outputs/polymarket_fixed/**/raw_market_snapshots.csv",
    ])
    slugs: dict[str, str] = {}
    for path in files:
        category = infer_category(path)
        for row in read_csv_rows(path, limit=None):
            slug = row.get("market_slug") or row.get("slug") or row.get("market_id") or ""
            if slug:
                slugs.setdefault(slug, category)
    return slugs


def collect_resolutions(cfg: EngineConfig, limit: int | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    settings = cfg.raw.get("resolution", {})
    base_url = str(settings.get("gamma_base_url", DEFAULT_GAMMA_BASE_URL))
    timeout = int(settings.get("request_timeout_seconds", 20))
    pause = float(settings.get("request_pause_seconds", 0.05))
    win_threshold = float(settings.get("settlement_win_threshold", 0.98))
    loss_threshold = float(settings.get("settlement_loss_threshold", 0.02))
    slugs = _unique_raw_slugs(cfg)
    if limit is None:
        configured_limit = int(settings.get("max_markets", 0) or 0)
        limit = configured_limit if configured_limit > 0 else None
    selected = list(slugs.items())[:limit]

    rows: list[dict[str, Any]] = []
    quality: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for slug, category in selected:
        try:
            market = fetch_gamma_market(slug, base_url=base_url, timeout_seconds=timeout)
            if not market:
                errors.append({"market_slug": slug, "resolution_quality": "gamma_not_found", "resolution_quality_reason": "empty response"})
                continue
            r, q = infer_market_resolution_rows(market, category_hint=category, win_threshold=win_threshold, loss_threshold=loss_threshold)
            rows.extend(r)
            quality.extend(q)
            if pause:
                time.sleep(pause)
        except Exception as exc:
            errors.append({"market_slug": slug, "resolution_quality": "gamma_fetch_error", "resolution_quality_reason": str(exc)})
    quality.extend(errors)

    out_root = cfg.output_root / "polymarket_training"
    gov_root = cfg.governance_root
    write_csv(out_root / "market_resolutions.csv", rows)
    write_csv(gov_root / "resolution_quality_report.csv", quality)
    summary = {
        "requested_markets": len(selected),
        "resolution_rows": len(rows),
        "clean_settlement_markets": len({r["market_slug"] for r in rows if r.get("resolution_quality") == "clean_settlement"}),
        "unresolved_markets": len({r["market_slug"] for r in rows if r.get("resolution_quality") == "unresolved_active"}),
        "error_count": len(errors),
        "output_file": str(out_root / "market_resolutions.csv"),
        "quality_file": str(gov_root / "resolution_quality_report.csv"),
    }
    write_json(gov_root / "gamma_resolution_summary.json", summary)
    return rows, quality, summary


def main(config_path: str) -> dict[str, Any]:
    _, _, summary = collect_resolutions(load_config(config_path))
    return summary
