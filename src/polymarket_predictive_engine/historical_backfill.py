from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any

from .config import EngineConfig, load_config
from .resolution_collector import DEFAULT_GAMMA_BASE_URL, infer_market_resolution_rows
from .utils import write_csv, write_json

DEFAULT_PAGE_SIZE = 100


def _fetch_gamma_page(
    *,
    base_url: str,
    limit: int,
    offset: int,
    timeout_seconds: int,
    query_params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    params = dict(query_params or {})
    params.update({"closed": "true", "limit": str(limit), "offset": str(offset)})
    url = f"{base_url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": "polymarket-predictive-engine/1.0"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # nosec B310: configured public API endpoint
        payload = json.loads(response.read().decode("utf-8"))
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("markets", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [payload]
    return []


def historical_backfill(cfg: EngineConfig, historical_limit: int | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    settings = cfg.raw.get("historical_backfill", {})
    resolution_settings = cfg.raw.get("resolution", {})
    base_url = str(settings.get("gamma_base_url", resolution_settings.get("gamma_base_url", DEFAULT_GAMMA_BASE_URL)))
    max_closed = int(historical_limit or settings.get("max_closed_markets", 250))
    page_size = int(settings.get("page_size", DEFAULT_PAGE_SIZE))
    timeout = int(settings.get("request_timeout_seconds", resolution_settings.get("request_timeout_seconds", 30)))
    pause = float(settings.get("request_pause_seconds", resolution_settings.get("request_pause_seconds", 0.1)))
    win_threshold = float(resolution_settings.get("settlement_win_threshold", 0.98))
    loss_threshold = float(resolution_settings.get("settlement_loss_threshold", 0.02))
    query_params = settings.get("gamma_query_params", {}) or {}

    rows: list[dict[str, Any]] = []
    quality: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    fetched_markets = 0
    offset = 0

    while fetched_markets < max_closed:
        request_limit = min(page_size, max_closed - fetched_markets)
        try:
            markets = _fetch_gamma_page(
                base_url=base_url,
                limit=request_limit,
                offset=offset,
                timeout_seconds=timeout,
                query_params=query_params,
            )
        except Exception as exc:
            errors.append({
                "market_slug": "",
                "gamma_market_id": "",
                "resolution_quality": "gamma_fetch_error",
                "resolution_quality_reason": str(exc),
                "offset": offset,
            })
            break
        if not markets:
            break
        for market in markets:
            fetched_markets += 1
            try:
                r, q = infer_market_resolution_rows(
                    market,
                    category_hint=str(market.get("category") or ""),
                    win_threshold=win_threshold,
                    loss_threshold=loss_threshold,
                )
                rows.extend(r)
                quality.extend(q)
            except Exception as exc:
                errors.append({
                    "market_slug": str(market.get("slug", "")),
                    "gamma_market_id": str(market.get("id", "")),
                    "resolution_quality": "gamma_fetch_error",
                    "resolution_quality_reason": str(exc),
                    "offset": offset,
                })
            if fetched_markets >= max_closed:
                break
        offset += len(markets)
        if len(markets) < request_limit:
            break
        if pause:
            time.sleep(pause)

    quality.extend(errors)
    out_root = cfg.output_root / "polymarket_training"
    gov_root = cfg.governance_root
    write_csv(out_root / "historical_resolutions.csv", rows)
    write_csv(gov_root / "historical_resolution_quality_report.csv", quality)
    clean_markets = {r.get("market_slug", "") for r in rows if r.get("resolution_quality") == "clean_settlement"}
    summary = {
        "requested_closed_markets": max_closed,
        "fetched_markets": fetched_markets,
        "resolution_rows": len(rows),
        "clean_settlement_markets": len({m for m in clean_markets if m}),
        "quality_rows": len(quality),
        "error_count": len(errors),
        "output_file": str(out_root / "historical_resolutions.csv"),
        "quality_file": str(gov_root / "historical_resolution_quality_report.csv"),
    }
    write_json(gov_root / "historical_backfill_summary.json", summary)
    return rows, quality, summary


def main(config_path: str, historical_limit: int | None = None) -> dict[str, Any]:
    _, _, summary = historical_backfill(load_config(config_path), historical_limit=historical_limit)
    return summary
