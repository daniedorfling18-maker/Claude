from __future__ import annotations

from datetime import timezone
from pathlib import Path
from typing import Any

from .config import EngineConfig, load_config
from .utils import discover_files, find_first_column, parse_timestamp, read_csv_rows, safe_float, write_csv

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


def build_labels(cfg: EngineConfig, allow_late_market: bool = False) -> list[dict[str, Any]]:
    files = discover_files(cfg.data_root, ["outputs/polymarket_wide/**/raw_market_snapshots.csv", "outputs/polymarket_fixed/**/raw_market_snapshots.csv"])
    labels: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for path in files:
        rows = read_csv_rows(path)
        if not rows:
            continue
        cols = list(rows[0].keys())
        market_col = find_first_column(cols, cfg.raw.get("schema", {}).get("market_id_fields", ["market_id", "condition_id", "id", "slug"]))
        token_col = find_first_column(cols, cfg.raw.get("schema", {}).get("token_id_fields", ["token_id", "asset_id", "outcome_token_id"]))
        ts_col = find_first_column(cols, cfg.raw.get("schema", {}).get("timestamp_fields", ["snapshot_timestamp", "timestamp", "collected_at"]))
        close_col = find_first_column(cols, ["close_time", "end_time", "market_close_time", "closed_at", "end_date"])
        resolution_col = find_first_column(cols, ["resolution_time", "resolved_at", "settled_at"])
        if not market_col or not token_col or not ts_col:
            rejected.append({"file_path": str(path), "reason": "missing market/token/timestamp fields"})
            continue
        for row in rows:
            ts = parse_timestamp(row.get(ts_col))
            close = parse_timestamp(row.get(close_col or ""))
            res = parse_timestamp(row.get(resolution_col or ""))
            target = _target(row, token_col)
            if target is None:
                rejected.append({"file_path": str(path), "market_id": row.get(market_col, ""), "reason": "missing resolved target"})
                continue
            if not ts:
                rejected.append({"file_path": str(path), "market_id": row.get(market_col, ""), "reason": "missing prediction timestamp"})
                continue
            if res and ts >= res:
                rejected.append({"file_path": str(path), "market_id": row.get(market_col, ""), "reason": "snapshot after resolution excluded"})
                continue
            if close and ts > close and not allow_late_market:
                rejected.append({"file_path": str(path), "market_id": row.get(market_col, ""), "reason": "snapshot after close excluded"})
                continue
            time_to_close = ((close - ts).total_seconds() / 3600) if close else None
            time_to_resolution = ((res - ts).total_seconds() / 3600) if res else None
            base = {
                "market_id": row.get(market_col, ""),
                "token_id": row.get(token_col, ""),
                "prediction_timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "close_time": close.strftime("%Y-%m-%dT%H:%M:%SZ") if close else "",
                "resolution_time": res.strftime("%Y-%m-%dT%H:%M:%SZ") if res else "",
                "target": target,
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
        raise RuntimeError("No resolved outcome labels found. Engine is not approved for supervised training yet.")
    return labels


def main(config_path: str) -> list[dict[str, Any]]:
    return build_labels(load_config(config_path))
