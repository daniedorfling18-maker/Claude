from __future__ import annotations

import json
from typing import Any

import requests

from .config import EngineConfig, load_config
from .resolution_collector import DEFAULT_GAMMA_BASE_URL, infer_market_resolution_rows
from .resolution_corpus import append_resolution_observations
from .utils import now_utc, read_csv_rows, write_csv, write_json


def _parse_jsonish_list(value: Any) -> list[Any]:
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


def _token_ids_from_websocket_features(cfg: EngineConfig) -> list[str]:
    paths = [
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        cfg.output_root / "polymarket_training" / "features_v2.csv",
    ]
    tokens: list[str] = []
    seen: set[str] = set()
    for path in paths:
        for row in read_csv_rows(path):
            if path.name == "features_v2.csv" and row.get("feature_source") not in {"", "websocket"}:
                continue
            token = str(row.get("asset_id") or row.get("token_id") or "").strip()
            if token and token not in seen:
                seen.add(token)
                tokens.append(token)
    return tokens


def _as_market_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("data", "markets", "results"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
        return [payload]
    return []


def _fetch_page(base_url: str, params: dict[str, Any], timeout: int) -> list[dict[str, Any]]:
    response = requests.get(base_url, params=params, timeout=timeout)
    if response.status_code == 422:
        return []
    response.raise_for_status()
    return _as_market_list(response.json())


def _market_tokens(market: dict[str, Any]) -> list[str]:
    return [str(token) for token in _parse_jsonish_list(market.get("clobTokenIds")) if str(token).strip()]


def _scan_gamma_for_tokens(
    *,
    token_ids: set[str],
    base_url: str,
    page_size: int,
    max_pages: int,
    timeout: int,
    base_params: dict[str, Any],
    label: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    matched: dict[str, dict[str, Any]] = {}
    rows_seen = 0
    pages_scanned = 0
    stop_reason = "max_pages"

    for page in range(max_pages):
        params = dict(base_params)
        params["limit"] = page_size
        params["offset"] = page * page_size
        rows = _fetch_page(base_url, params, timeout)
        pages_scanned += 1

        if not rows:
            stop_reason = "empty_or_pagination_limit"
            break

        rows_seen += len(rows)

        for market in rows:
            market_tokens = set(_market_tokens(market))
            intersection = token_ids & market_tokens
            for token in intersection:
                matched.setdefault(token, market)

        if token_ids and token_ids.issubset(set(matched.keys())):
            stop_reason = "all_tokens_matched"
            break

    return matched, {
        "label": label,
        "pages_scanned": pages_scanned,
        "rows_seen": rows_seen,
        "matched_tokens": len(matched),
        "stop_reason": stop_reason,
    }


def _dedupe_resolution_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for row in rows:
        key = (
            str(row.get("market_slug") or row.get("condition_id") or row.get("gamma_market_id") or ""),
            str(row.get("token_id") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def collect_websocket_resolutions(cfg: EngineConfig) -> dict[str, Any]:
    settings = cfg.raw.get("websocket_resolution", {})
    base_url = str(settings.get("gamma_base_url", cfg.raw.get("historical_backfill", {}).get("gamma_base_url", DEFAULT_GAMMA_BASE_URL)))
    page_size = int(settings.get("page_size", 100))
    max_pages = int(settings.get("max_pages", 50))
    timeout = int(settings.get("request_timeout_seconds", 30))

    token_list = _token_ids_from_websocket_features(cfg)
    token_ids = set(token_list)
    out_root = cfg.output_root / "polymarket_training"
    gov_root = cfg.governance_root

    if not token_ids:
        run_at = now_utc()
        summary = {
            "status": "skipped",
            "reason": "no websocket token ids found",
            "websocket_tokens": 0,
            "resolution_rows": 0,
            "clean_settlement_markets": 0,
            "collected_at_utc": run_at,
            "work_order": "WO-101",
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        }
        write_csv(out_root / "websocket_resolutions.csv", [])
        write_csv(gov_root / "websocket_resolution_quality_report.csv", [])
        write_json(gov_root / "websocket_resolution_summary.json", summary)
        return summary

    scan_specs = [
        ("default_recent", {}),
        ("closed_true", {"closed": "true"}),
        ("active_true", {"active": "true"}),
    ]

    matched: dict[str, dict[str, Any]] = {}
    scans: list[dict[str, Any]] = []

    for label, params in scan_specs:
        remaining = token_ids - set(matched.keys())
        if not remaining:
            break

        found, meta = _scan_gamma_for_tokens(
            token_ids=remaining,
            base_url=base_url,
            page_size=page_size,
            max_pages=max_pages,
            timeout=timeout,
            base_params=params,
            label=label,
        )
        matched.update(found)
        scans.append(meta)

    # The availability clock must follow the final paginated Gamma response;
    # scan-start time would make later labels appear known too early.
    run_at = now_utc()
    resolution_rows: list[dict[str, Any]] = []
    quality_rows: list[dict[str, Any]] = []

    for token in sorted(token_ids):
        market = matched.get(token)
        if not market:
            quality_rows.append(
                {
                    "token_id": token,
                    "resolution_quality": "gamma_market_not_found_for_websocket_token",
                    "resolution_quality_reason": "token id was not found in scanned Gamma market pages",
                }
            )
            continue

        rows, quality = infer_market_resolution_rows(
            market,
            observed_at_utc=run_at,
        )
        resolution_rows.extend(rows)

        for row in quality:
            quality_rows.append({**row, "matched_token_id": token})

    resolution_rows = _dedupe_resolution_rows(resolution_rows)

    write_csv(out_root / "websocket_resolutions.csv", resolution_rows)
    write_csv(gov_root / "websocket_resolution_quality_report.csv", quality_rows)
    corpus = append_resolution_observations(
        cfg,
        resolution_rows,
        producer="websocket_resolution_collector",
        observed_at_utc=run_at,
    )

    clean_markets = len(
        {
            row.get("market_slug") or row.get("condition_id") or row.get("gamma_market_id")
            for row in resolution_rows
            if row.get("resolution_quality") == "clean_settlement"
        }
    )

    unresolved_markets = len(
        {
            row.get("market_slug") or row.get("condition_id") or row.get("gamma_market_id")
            for row in resolution_rows
            if row.get("resolution_quality") == "unresolved_active"
        }
    )

    summary = {
        "work_order": "WO-101",
        "status": "ok",
        "websocket_tokens": len(token_ids),
        "matched_tokens": len(matched),
        "unmatched_tokens": len(token_ids) - len(matched),
        "resolution_rows": len(resolution_rows),
        "clean_settlement_markets": clean_markets,
        "unresolved_markets": unresolved_markets,
        "scans": scans,
        "collected_at_utc": run_at,
        "output_file": str(out_root / "websocket_resolutions.csv"),
        "quality_file": str(gov_root / "websocket_resolution_quality_report.csv"),
        "append_only_resolution_corpus": corpus,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }

    write_json(gov_root / "websocket_resolution_summary.json", summary)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return collect_websocket_resolutions(load_config(config_path))
