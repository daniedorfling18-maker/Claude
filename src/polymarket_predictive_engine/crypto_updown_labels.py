from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .config import EngineConfig
from .utils import now_utc, read_csv_rows, write_csv, write_json

RESOLUTION_FIELDS = [
    "resolution_time",
    "resolution_quality",
    "target",
    "token_id",
    "market_slug",
    "condition_id",
    "gamma_market_id",
    "question_id",
    "outcome",
    "close_time",
    "resolution_source",
    "resolution_reason",
]

LABEL_SOURCE_FIELDS = [
    "snapshot_timestamp",
    "market_id",
    "market_slug",
    "token_id",
    "outcome",
    "target",
    "resolution_quality",
    "close_time",
    "resolution_time",
    "resolution_source",
    "resolution_reason",
]

SettlementFn = Callable[[dict[str, Any]], tuple[float | None, str]]


def _default_settlement_fn(timeout: int) -> SettlementFn:
    # Import lazily so importing the CLI remains lightweight and so tests that
    # only inspect COMMANDS do not import the whole shadow-settlement stack.
    from .shadow_cohort import _crypto_updown_proxy_settlement_price

    def settle(row: dict[str, Any]) -> tuple[float | None, str]:
        return _crypto_updown_proxy_settlement_price(row, timeout_seconds=timeout)

    return settle


def _row_time(row: dict[str, Any]) -> str:
    for key in ("timestamp", "collected_at_utc", "snapshot_timestamp", "collected_at", "source_timestamp"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return now_utc()


def _market_id(row: dict[str, Any]) -> str:
    for key in ("condition_id", "market_id", "market", "gamma_market_id", "market_slug"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _source_paths(cfg: EngineConfig) -> list[Path]:
    paths: list[Path] = [
        cfg.output_root / "polymarket" / "market_snapshot.csv",
        cfg.output_root / "polymarket_fast_updown" / "fast_updown_market_snapshot.csv",
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        cfg.output_root / "polymarket_predictions" / "predictions.csv",
        cfg.output_root / "polymarket_predictions" / "mispricing_alpha_scores.csv",
    ]
    query_scan_root = cfg.output_root / "polymarket" / "query_scans"
    if query_scan_root.exists():
        paths.extend(sorted(query_scan_root.glob("*/market_snapshot.csv")))
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def _candidate_rows(cfg: EngineConfig) -> list[tuple[Path, dict[str, Any]]]:
    candidates: list[tuple[Path, dict[str, Any]]] = []
    for path in _source_paths(cfg):
        for row in read_csv_rows(path):
            slug = str(row.get("market_slug") or row.get("slug") or "").strip().lower()
            if "-updown-5m-" not in slug and "-updown-15m-" not in slug:
                continue
            if not str(row.get("token_id") or row.get("asset_id") or row.get("outcome_token_id") or "").strip():
                continue
            if not str(row.get("outcome") or "").strip():
                continue
            candidates.append((path, row))
    return candidates


def _resolution_row(row: dict[str, Any], target: float, reason: str) -> dict[str, Any]:
    token_id = str(row.get("token_id") or row.get("asset_id") or row.get("outcome_token_id") or "")
    market_slug = str(row.get("market_slug") or row.get("slug") or "")
    close_time = str(row.get("close_time") or row.get("end_time") or row.get("endDate") or row.get("endDateIso") or "")
    return {
        "resolution_time": now_utc(),
        "resolution_quality": "clean_settlement",
        "target": int(target),
        "token_id": token_id,
        "market_slug": market_slug,
        "condition_id": str(row.get("condition_id") or row.get("market_id") or row.get("market") or ""),
        "gamma_market_id": str(row.get("gamma_market_id") or ""),
        "question_id": str(row.get("question_id") or ""),
        "outcome": str(row.get("outcome") or ""),
        "close_time": close_time,
        "resolution_source": "crypto_updown_proxy",
        "resolution_reason": reason,
    }


def _label_source_row(row: dict[str, Any], target: float, reason: str) -> dict[str, Any]:
    return {
        "snapshot_timestamp": _row_time(row),
        "market_id": _market_id(row),
        "market_slug": str(row.get("market_slug") or row.get("slug") or ""),
        "token_id": str(row.get("token_id") or row.get("asset_id") or row.get("outcome_token_id") or ""),
        "outcome": str(row.get("outcome") or ""),
        "target": int(target),
        "resolution_quality": "clean_settlement",
        "close_time": str(row.get("close_time") or row.get("end_time") or row.get("endDate") or row.get("endDateIso") or ""),
        "resolution_time": now_utc(),
        "resolution_source": "crypto_updown_proxy",
        "resolution_reason": reason,
    }


def _merge_resolution_rows(existing: list[dict[str, str]], new_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for row in existing:
        token = str(row.get("token_id") or "")
        market = str(row.get("market_slug") or row.get("condition_id") or row.get("gamma_market_id") or "")
        if token:
            merged[(market, token)] = dict(row)
    for row in new_rows:
        token = str(row.get("token_id") or "")
        market = str(row.get("market_slug") or row.get("condition_id") or row.get("gamma_market_id") or "")
        if token:
            merged[(market, token)] = row
    return list(merged.values())


def build_crypto_updown_proxy_labels(
    cfg: EngineConfig,
    *,
    max_rows: int = 250,
    timeout_seconds: int | None = None,
    settlement_fn: SettlementFn | None = None,
) -> dict[str, Any]:
    settings = cfg.raw.get("shadow_cohort_validation", {}) or {}
    timeout = int(timeout_seconds or settings.get("settlement_request_timeout_seconds", 20) or 20)
    settle = settlement_fn or _default_settlement_fn(timeout)
    checked = 0
    resolved = 0
    skipped = 0
    reasons: dict[str, int] = {}
    resolution_rows: list[dict[str, Any]] = []
    label_source_rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for path, source_row in _candidate_rows(cfg):
        if checked >= max_rows:
            break
        row = dict(source_row)
        row["token_id"] = str(row.get("token_id") or row.get("asset_id") or row.get("outcome_token_id") or "")
        row.setdefault("market_slug", row.get("slug", ""))
        key = (str(row.get("market_slug") or ""), str(row.get("token_id") or ""))
        if key in seen:
            continue
        seen.add(key)
        checked += 1
        try:
            target, reason = settle(row)
        except Exception as exc:  # noqa: BLE001 - one failed proxy lookup must not fail the whole builder
            target, reason = None, f"proxy_label_error:{type(exc).__name__}"
        reasons[reason] = reasons.get(reason, 0) + 1
        if target not in {0.0, 1.0}:
            skipped += 1
            continue
        resolution_rows.append(_resolution_row(row, float(target), reason))
        label_source = _label_source_row(row, float(target), reason)
        label_source["source_path"] = str(path)
        label_source_rows.append(label_source)
        resolved += 1

    training = cfg.output_root / "polymarket_training"
    existing_resolutions = read_csv_rows(training / "websocket_resolutions.csv")
    merged = _merge_resolution_rows(existing_resolutions, resolution_rows)
    if resolution_rows:
        write_csv(training / "websocket_resolutions.csv", merged, fieldnames=RESOLUTION_FIELDS)
    write_csv(training / "crypto_updown_proxy_labels.csv", label_source_rows, fieldnames=LABEL_SOURCE_FIELDS + ["source_path"])

    payload = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "checked_rows": checked,
        "resolved_rows": resolved,
        "skipped_rows": skipped,
        "resolution_reasons": reasons,
        "output_resolution_file": str(training / "websocket_resolutions.csv"),
        "output_label_file": str(training / "crypto_updown_proxy_labels.csv"),
        "note": "Resolved rows are merged into websocket_resolutions.csv so build-labels can join WebSocket/snapshot features by token.",
    }
    write_json(cfg.governance_root / "crypto_updown_proxy_label_summary.json", payload)
    return payload
