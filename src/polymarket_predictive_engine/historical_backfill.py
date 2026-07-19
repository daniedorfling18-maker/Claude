from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from .config import EngineConfig, load_config
from .resolution_collector import infer_market_resolution_rows
from .resolution_corpus import append_resolution_observations, canonical_utc
from .utils import normalize_external_timestamp, write_csv, write_json

DEFAULT_GAMMA_BASE_URL = "https://gamma-api.polymarket.com/markets"


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _market_close_dt(market: dict[str, Any]) -> datetime:
    for key in ("closedTime", "closed_time", "endDate", "endDateIso", "updatedAt", "createdAt"):
        seconds = normalize_external_timestamp(market.get(key))
        if seconds is not None:
            return datetime.fromtimestamp(seconds, timezone.utc)
    return datetime(1970, 1, 1, tzinfo=timezone.utc)


def _is_closed_candidate(market: dict[str, Any]) -> bool:
    return _truthy(market.get("closed"))


def _as_market_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("data", "markets", "results"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    return []


def _fetch_page(base_url: str, params: dict[str, Any], timeout: int) -> tuple[list[dict[str, Any]], str]:
    try:
        response = requests.get(base_url, params=params, timeout=timeout)
        if response.status_code == 422:
            return [], "pagination_limit"
        response.raise_for_status()
        return _as_market_list(response.json()), "ok"
    except requests.HTTPError as exc:
        if getattr(exc.response, "status_code", None) == 422:
            return [], "pagination_limit"
        raise


def _append_unique(candidates: list[dict[str, Any]], seen: set[str], market: dict[str, Any]) -> None:
    market_id = str(market.get("id") or market.get("conditionId") or market.get("slug") or "")
    if not market_id or market_id in seen:
        return
    seen.add(market_id)
    if _is_closed_candidate(market):
        candidates.append(market)


def _scan_feed(
    *,
    base_url: str,
    timeout: int,
    page_size: int,
    max_pages: int,
    base_params: dict[str, Any],
    requested_closed_markets: int,
    label: str,
    progress_every_pages: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    pages_scanned = 0
    rows_seen = 0
    stop_reason = "max_pages"

    for page in range(max_pages):
        params = dict(base_params)
        params["limit"] = page_size
        params["offset"] = page * page_size
        rows, status = _fetch_page(base_url, params, timeout)
        pages_scanned += 1

        if status == "pagination_limit":
            stop_reason = "pagination_limit"
            break
        if not rows:
            stop_reason = "empty_page"
            break

        rows_seen += len(rows)
        for market in rows:
            _append_unique(candidates, seen, market)

        if progress_every_pages and (pages_scanned % progress_every_pages == 0):
            newest = _market_close_dt(sorted(candidates, key=_market_close_dt, reverse=True)[0]).isoformat() if candidates else ""
            print(
                f"backfill {label} progress: pages={pages_scanned}; rows_seen={rows_seen}; closed_candidates={len(candidates)}; newest_candidate={newest}",
                flush=True,
            )

        # Closed=true can return very old markets first, so do not stop too early
        # unless a useful number of candidates has already been collected.
        if len(candidates) >= max(requested_closed_markets * 4, requested_closed_markets + 100):
            stop_reason = "candidate_buffer_reached"
            break

    candidates.sort(key=_market_close_dt, reverse=True)
    return candidates[:requested_closed_markets], {
        "label": label,
        "pages_scanned": pages_scanned,
        "rows_seen": rows_seen,
        "closed_candidates": len(candidates),
        "stop_reason": stop_reason,
    }


def _candidate_markets(
    *,
    base_url: str,
    page_size: int,
    max_pages: int,
    timeout: int,
    gamma_query_params: dict[str, Any],
    requested_closed_markets: int,
    progress_every_pages: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    all_candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    scans: list[dict[str, Any]] = []

    # 1) Explicit closed pagination. Gamma's closed=true feed starts with ancient
    # rows, so we page forward until either we collect a large buffer or hit the
    # API pagination cap, then sort locally by close time.
    closed_params = dict(gamma_query_params or {})
    closed_params["closed"] = "true"
    closed_params.setdefault("order", "closedTime")
    closed_params.setdefault("ascending", "false")
    closed_candidates, closed_meta = _scan_feed(
        base_url=base_url,
        timeout=timeout,
        page_size=page_size,
        max_pages=max_pages,
        base_params=closed_params,
        requested_closed_markets=requested_closed_markets,
        label="closed_true",
        progress_every_pages=progress_every_pages,
    )
    scans.append(closed_meta)
    for market in closed_candidates:
        _append_unique(all_candidates, seen, market)

    # 2) Default recent feed as a supplement. This often contains active markets,
    # but keeping it as a supplement is useful when Gamma returns recently closed
    # markets in the default feed.
    recent_params = dict(gamma_query_params or {})
    recent_params.pop("closed", None)
    recent_candidates, recent_meta = _scan_feed(
        base_url=base_url,
        timeout=timeout,
        page_size=page_size,
        max_pages=max_pages,
        base_params=recent_params,
        requested_closed_markets=requested_closed_markets,
        label="default_recent",
        progress_every_pages=progress_every_pages,
    )
    scans.append(recent_meta)
    for market in recent_candidates:
        _append_unique(all_candidates, seen, market)

    all_candidates.sort(key=_market_close_dt, reverse=True)
    return all_candidates[:requested_closed_markets], {
        "strategy": "closed_true_paginated_plus_default_recent_supplement",
        "scans": scans,
        "merged_closed_candidates": len(all_candidates),
    }


def historical_backfill(
    cfg: EngineConfig,
    historical_limit: int | None = None,
    *,
    allow_old_history: bool = False,
    as_of: datetime | str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    # A production observation timestamp must not predate the HTTP response
    # whose terminal state it records.  Explicit ``as_of`` is retained only as
    # a deterministic replay/test clock; the CLI never supplies it.
    replay_run_at = canonical_utc(as_of) if as_of is not None else None
    settings = cfg.raw.get("historical_backfill", {})
    base_url = str(settings.get("gamma_base_url", DEFAULT_GAMMA_BASE_URL))
    requested = int(historical_limit or settings.get("max_closed_markets", 250))
    page_size = int(settings.get("page_size", 100))
    max_pages = int(settings.get("max_pages", 25))
    timeout = int(settings.get("request_timeout_seconds", 30))
    progress_every_pages = int(settings.get("progress_every_pages", 5))
    gamma_query_params = dict(settings.get("gamma_query_params", {}) or {})
    max_age_days = int(settings.get("max_age_days", 365))

    candidates, fetch_meta = _candidate_markets(
        base_url=base_url,
        page_size=page_size,
        max_pages=max_pages,
        timeout=timeout,
        gamma_query_params=gamma_query_params,
        requested_closed_markets=requested,
        progress_every_pages=progress_every_pages,
    )
    run_at = replay_run_at or canonical_utc(None)
    run_clock = datetime.fromisoformat(run_at.replace("Z", "+00:00"))
    cutoff = run_clock - timedelta(days=max_age_days)
    old_candidates = [market for market in candidates if _market_close_dt(market) < cutoff]
    if old_candidates and not allow_old_history:
        candidates = [market for market in candidates if _market_close_dt(market) >= cutoff]

    resolution_rows: list[dict[str, Any]] = []
    quality_rows: list[dict[str, Any]] = []
    errors = 0

    for market in candidates:
        try:
            rows, quality = infer_market_resolution_rows(
                market,
                observed_at_utc=run_at,
            )
            resolution_rows.extend(rows)
            quality_rows.extend(quality)
        except Exception as exc:
            errors += 1
            quality_rows.append(
                {
                    "gamma_market_id": market.get("id", ""),
                    "market_slug": market.get("slug", ""),
                    "condition_id": market.get("conditionId", ""),
                    "resolution_quality": "backfill_error",
                    "reason": str(exc),
                }
            )

    out_root = cfg.output_root / "polymarket_training"
    gov_root = cfg.governance_root
    write_csv(out_root / "historical_resolutions.csv", resolution_rows)
    write_csv(gov_root / "historical_resolution_quality_report.csv", quality_rows)
    corpus = append_resolution_observations(
        cfg,
        resolution_rows,
        producer="historical_backfill",
        observed_at_utc=run_at,
    )

    clean_markets = len(
        {
            row.get("market_slug") or row.get("condition_id") or row.get("gamma_market_id")
            for row in resolution_rows
            if row.get("resolution_quality") == "clean_settlement"
        }
    )
    close_dates = [_market_close_dt(market).isoformat() for market in candidates[:10]]

    summary = {
        "work_order": "WO-101",
        "requested_closed_markets": requested,
        "fetched_markets": len(candidates),
        "resolution_rows": len(resolution_rows),
        "quality_rows": len(quality_rows),
        "clean_settlement_markets": clean_markets,
        "error_count": errors,
        "fetch_strategy": fetch_meta,
        "historical_cutoff_utc": cutoff.isoformat(),
        "old_candidates_excluded": 0 if allow_old_history else len(old_candidates),
        "old_history_explicitly_allowed": allow_old_history,
        "newest_candidate_close_times": close_dates,
        "collected_at_utc": run_at,
        "output_file": str(out_root / "historical_resolutions.csv"),
        "quality_file": str(gov_root / "historical_resolution_quality_report.csv"),
        "append_only_resolution_corpus": corpus,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    write_json(gov_root / "historical_resolution_summary.json", summary)
    return resolution_rows, quality_rows, summary


def main(
    config_path: str,
    historical_limit: int | None = None,
    *,
    allow_old_history: bool = False,
) -> dict[str, Any]:
    _, _, summary = historical_backfill(
        load_config(config_path),
        historical_limit=historical_limit,
        allow_old_history=allow_old_history,
    )
    return summary
