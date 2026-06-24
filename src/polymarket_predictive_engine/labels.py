from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import EngineConfig, load_config
from .utils import discover_files, find_first_column, parse_timestamp, read_csv_rows, write_csv

HORIZONS = [("1h", 1), ("6h", 6), ("24h", 24), ("3d", 72), ("7d", 168)]


def _load_resolution_index(cfg: EngineConfig) -> dict[tuple[str, str], dict[str, str]]:
    rows: list[dict[str, str]] = []
    for name in ("market_resolutions.csv", "historical_resolutions.csv", "websocket_resolutions.csv"):
        rows.extend(read_csv_rows(cfg.output_root / "polymarket_training" / name))

    index: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        if row.get("resolution_quality") != "clean_settlement":
            continue
        token = row.get("token_id", "")
        if not token:
            continue
        keys = [
            row.get("market_slug", ""),
            row.get("condition_id", ""),
            row.get("gamma_market_id", ""),
            row.get("question_id", ""),
        ]
        for market_key in keys:
            if market_key:
                index[(market_key, token)] = row
        # CLOB token ids are globally unique. This fallback lets WebSocket feature rows
        # join once a websocket_resolutions.csv row exists, even if the WebSocket
        # event did not carry a Gamma slug or condition id.
        index.setdefault(("", token), row)
    return index


def _resolution_target(resolution: dict[str, str] | None) -> int | None:
    if not resolution or resolution.get("resolution_quality") != "clean_settlement":
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


def _candidate_files(cfg: EngineConfig) -> list[Path]:
    files = discover_files(
        cfg.data_root,
        [
            "outputs/polymarket_wide/**/raw_market_snapshots.csv",
            "outputs/polymarket_fixed/**/raw_market_snapshots.csv",
        ],
    )
    historical = cfg.output_root / "polymarket_training" / "historical_price_snapshots.csv"
    if historical.exists():
        files.append(historical)

    websocket = cfg.output_root / "polymarket_training" / "websocket_market_features.csv"
    if websocket.exists():
        files.append(websocket)

    return files


def _columns_for_file(cfg: EngineConfig, path: Path, cols: list[str]) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    is_history = path.name == "historical_price_snapshots.csv"
    is_websocket = path.name == "websocket_market_features.csv"

    market_candidates = ["market"] + cfg.raw.get("schema", {}).get("market_id_fields", ["market_id", "condition_id", "id", "market_slug", "slug"])
    token_candidates = ["asset_id"] + cfg.raw.get("schema", {}).get("token_id_fields", ["token_id", "asset_id", "outcome_token_id"])
    timestamp_candidates = ["collected_at_utc", "source_timestamp"] + cfg.raw.get("schema", {}).get("timestamp_fields", ["snapshot_timestamp", "timestamp", "collected_at"])

    market_col = find_first_column(cols, market_candidates)
    token_col = find_first_column(cols, token_candidates)
    ts_col = "timestamp" if is_history and "timestamp" in cols else find_first_column(cols, timestamp_candidates)
    close_col = find_first_column(cols, ["close_time", "end_time", "market_close_time", "closed_at", "end_date"])
    slug_col = find_first_column(cols, ["market_slug", "slug"])

    # WebSocket files may carry only an asset/token id. They are still valid
    # label candidates after token-only resolution fallback is available.
    if is_websocket and not market_col:
        market_col = token_col

    return market_col, token_col, ts_col, close_col, slug_col

def build_labels(cfg: EngineConfig, allow_late_market: bool = False) -> list[dict[str, Any]]:
    resolution_index = _load_resolution_index(cfg)
    labels: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for path in _candidate_files(cfg):
        rows = read_csv_rows(path)
        if not rows:
            continue
        cols = list(rows[0].keys())
        market_col, token_col, ts_col, close_col, slug_col = _columns_for_file(cfg, path, cols)
        if not market_col or not token_col or not ts_col:
            rejected.append({"file_path": str(path), "reason": "missing market/token/timestamp fields"})
            continue

        for row in rows:
            token_id = row.get(token_col, "")
            market_id = row.get(market_col, "") or (token_id if path.name == "websocket_market_features.csv" else "")
            market_slug = row.get(slug_col or "", "") or market_id
            resolution = (
                resolution_index.get((market_id, token_id))
                or resolution_index.get((market_slug, token_id))
                or resolution_index.get(("", token_id))
            )
            target = _resolution_target(resolution)
            if target is None:
                rejected.append(
                    {
                        "file_path": str(path),
                        "market_id": market_id,
                        "token_id": token_id,
                        "reason": "missing clean resolution target",
                        "resolution_quality": (resolution or {}).get("resolution_quality", "missing_resolution_row"),
                    }
                )
                continue

            ts = parse_timestamp(row.get(ts_col))
            close = parse_timestamp(row.get(close_col or "")) or parse_timestamp((resolution or {}).get("close_time") or (resolution or {}).get("end_time"))
            res = parse_timestamp((resolution or {}).get("resolution_time"))
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
                "market_slug": market_slug,
                "token_id": token_id,
                "outcome": row.get("outcome", (resolution or {}).get("outcome", "")),
                "prediction_timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "close_time": close.strftime("%Y-%m-%dT%H:%M:%SZ") if close else "",
                "resolution_time": res.strftime("%Y-%m-%dT%H:%M:%SZ") if res else "",
                "target": target,
                "resolution_quality": "clean_settlement",
                "label_source": "resolution_join",
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
        raise RuntimeError("No resolved outcome labels found. Run collect-resolutions or backfill-resolved-markets and ensure clean closed markets join to snapshots.")
    return labels


def main(config_path: str) -> list[dict[str, Any]]:
    return build_labels(load_config(config_path))
