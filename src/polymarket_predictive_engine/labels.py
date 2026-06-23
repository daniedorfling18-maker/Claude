from __future__ import annotations

from typing import Any

from .config import EngineConfig, load_config
from .utils import discover_files, find_first_column, parse_timestamp, read_csv_rows, write_csv

HORIZONS = [("1h", 1), ("6h", 6), ("24h", 24), ("3d", 72), ("7d", 168)]


def _target(row: dict[str, str], token_col: str | None) -> int | None:
    for key in ["target", "label", "won", "winner", "winning", "is_winner", "resolved_winner"]:
        if key in row:
            val = str(row.get(key, "")).lower()
            if val in {"1", "true", "yes", "won", "winner"}:
                return 1
            if val in {"0", "false", "no", "lost", "loser"}:
                return 0
    winning_token = row.get("winning_token_id") or row.get("winner_token_id") or row.get("winning_asset_id")
    if winning_token and token_col:
        return 1 if row.get(token_col, "") == winning_token else 0
    return None


def _load_resolution_index(cfg: EngineConfig) -> dict[tuple[str, str], dict[str, str]]:
    path = cfg.output_root / "polymarket_training" / "market_resolutions.csv"
    rows = read_csv_rows(path)
    index: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        token = row.get("token_id", "")
        if not token:
            continue
        for market_key in [row.get("market_slug", ""), row.get("condition_id", ""), row.get("gamma_market_id", "")]:
            if market_key:
                index[(market_key, token)] = row
    return index


def _resolution_target(resolution: dict[str, str] | None) -> int | None:
    if not resolution:
        return None
    if resolution.get("resolution_quality") != "clean_settlement":
        return None
    val = str(resolution.get("target", "")).lower()
    if val in {"1", "true", "yes"}:
        return 1
    if val in {"0", "false", "no"}:
        return 0
    winning_token = resolution.get("winning_token_id", "")
    token = resolution.get("token_id", "")
    if winning_token and token:
        return 1 if winning_token == token else 0
    return None


def build_labels(cfg: EngineConfig, allow_late_market: bool = False) -> list[dict[str, Any]]:
    files = discover_files(cfg.data_root, ["outputs/polymarket_wide/**/raw_market_snapshots.csv", "outputs/polymarket_fixed/**/raw_market_snapshots.csv"])
    resolution_index = _load_resolution_index(cfg)
    labels: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for path in files:
        rows = read_csv_rows(path)
        if not rows:
            continue
        cols = list(rows[0].keys())
        market_col = find_first_column(cols, cfg.raw.get("schema", {}).get("market_id_fields", ["market_id", "condition_id", "id", "market_slug", "slug"]))
        token_col = find_first_column(cols, cfg.raw.get("schema", {}).get("token_id_fields", ["token_id", "asset_id", "outcome_token_id"]))
        ts_col = find_first_column(cols, cfg.raw.get("schema", {}).get("timestamp_fields", ["snapshot_timestamp", "timestamp", "collected_at"]))
        close_col = find_first_column(cols, ["close_time", "end_time", "market_close_time", "closed_at", "end_date"])
        resolution_col = find_first_column(cols, ["resolution_time", "resolved_at", "settled_at"])
        if not market_col or not token_col or not ts_col:
            rejected.append({"file_path": str(path), "reason": "missing market/token/timestamp fields"})
            continue
        for row in rows:
            market_id = row.get(market_col, "")
            token_id = row.get(token_col, "")
            resolution = resolution_index.get((market_id, token_id))
            ts = parse_timestamp(row.get(ts_col))
            close = parse_timestamp(row.get(close_col or "")) or parse_timestamp((resolution or {}).get("close_time") or (resolution or {}).get("end_time"))
            res = parse_timestamp(row.get(resolution_col or "")) or parse_timestamp((resolution or {}).get("resolution_time"))
            target = _target(row, token_col)
            if target is None:
                target = _resolution_target(resolution)
            if target is None:
                rejected.append({
                    "file_path": str(path),
                    "market_id": market_id,
                    "token_id": token_id,
                    "reason": "missing resolved target",
                    "resolution_quality": (resolution or {}).get("resolution_quality", "missing_resolution_row"),
                })
                continue
            if not ts:
                rejected.append({"file_path": str(path), "market_id": market_id, "reason": "missing prediction timestamp"})
                continue
            if res and ts >= res:
                rejected.append({"file_path": str(path), "market_id": market_id, "reason": "snapshot after resolution excluded"})
                continue
            if close and ts > close and not allow_late_market:
                rejected.append({"file_path": str(path), "market_id": market_id, "reason": "snapshot after close excluded"})
                continue
            time_to_close = ((close - ts).total_seconds() / 3600) if close else None
            time_to_resolution = ((res - ts).total_seconds() / 3600) if res else None
            base = {
                "market_id": market_id,
                "market_slug": row.get("market_slug", market_id),
                "token_id": token_id,
                "outcome": row.get("outcome", (resolution or {}).get("outcome", "")),
                "prediction_timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "close_time": close.strftime("%Y-%m-%dT%H:%M:%SZ") if close else "",
                "resolution_time": res.strftime("%Y-%m-%dT%H:%M:%SZ") if res else "",
                "target": target,
                "winning_outcome": (resolution or {}).get("winning_outcome", row.get("winning_outcome", "")),
                "winning_token_id": (resolution or {}).get("winning_token_id", row.get("winning_token_id", "")),
                "resolution_quality": (resolution or {}).get("resolution_quality", "embedded_raw_label"),
                "time_to_close_hours": time_to_close if time_to_close is not None else "",
                "time_to_resolution_hours": time_to_resolution if time_to_resolution is not None else "",
            }
            labels.append({**base, "horizon": "all_valid"})
            if time_to_close is not None:
                for name, hours in HORIZONS:
                    if 0 <= time_to_close <= hours:
                        labels.append({**base, "horizon": name})
    out_root = cfg.output_root / "polymarket_training"
    write_csv(out_root / "labels.csv", labels)
    write_csv(out_root / "label_quality_report.csv", rejected)
    if not labels:
        raise RuntimeError("No resolved outcome labels found. Run collect-resolutions and ensure at least one clean closed market joins to raw snapshots.")
    return labels


def main(config_path: str) -> list[dict[str, Any]]:
    return build_labels(load_config(config_path))
