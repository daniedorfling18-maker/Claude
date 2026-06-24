from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import EngineConfig, load_config
from .utils import csv_columns, discover_files, file_summary, find_first_column, infer_category, read_csv_rows, write_csv, write_json

TRAINING_CANDIDATES = {"raw_market_snapshots.csv"}
DIAGNOSTICS = {"latest_joined_snapshot.csv", "market_snapshot.csv"}
AUDIT_OUTPUTS = {"opportunities.csv", "execution_log.csv"}
RELIABILITY = {"collector_state.json", "collector_heartbeat.csv"}


def classify_file(path: Path) -> tuple[str, str, str]:
    name = path.name
    if name in TRAINING_CANDIDATES:
        return "raw_point_in_time_training_candidate", "true", "raw_market_snapshots.csv is the candidate point-in-time historical base"
    if name in DIAGNOSTICS:
        return "current_state_diagnostic_snapshot", "diagnostics_only", "current or latest snapshot, not a safe training history"
    if name in AUDIT_OUTPUTS:
        return "trading_decision_audit_output", "governance_only", "decision or execution audit output, not predictive training data"
    if name in RELIABILITY:
        return "pipeline_reliability_file", "governance_only", "collector state or heartbeat"
    return "unknown", "diagnostics_only", "unrecognized file type"


def inventory(cfg: EngineConfig) -> list[dict[str, Any]]:
    patterns = cfg.raw.get("file_discovery", {}).get("patterns", ["outputs/polymarket_wide/**/*", "outputs/polymarket_fixed/**/*"])
    files = discover_files(cfg.data_root, patterns)
    rows: list[dict[str, Any]] = []
    for path in files:
        rel = path.relative_to(cfg.data_root) if path.is_relative_to(cfg.data_root) else path
        category = infer_category(path)
        file_type, suitability, reason = classify_file(path)
        columns = csv_columns(path) if path.suffix.lower() == ".csv" else []
        sample = read_csv_rows(path, limit=5000) if path.suffix.lower() == ".csv" else []
        null_counts = {c: sum(1 for r in sample if r.get(c, "") == "") for c in columns}
        duplicate_count = len(sample) - len({tuple(sorted(r.items())) for r in sample}) if sample else 0
        ts_cols = [c for c in columns if "time" in c.lower() or "date" in c.lower() or "timestamp" in c.lower()]
        timestamps: list[str] = []
        for c in ts_cols:
            timestamps.extend([r.get(c, "") for r in sample if r.get(c, "")])
        row = {
            **file_summary(path),
            "file_path": str(rel),
            "category": category,
            "file_type": file_type,
            "row_count": len(sample),
            "column_list": ";".join(columns),
            "null_counts": null_counts,
            "duplicate_row_count": duplicate_count,
            "timestamp_columns": ";".join(ts_cols),
            "minimum_timestamp": min(timestamps) if timestamps else "",
            "maximum_timestamp": max(timestamps) if timestamps else "",
            "market_identifier_columns": find_first_column(columns, ["market_id", "condition_id", "id", "market_slug", "slug"]) or "",
            "token_identifier_columns": find_first_column(columns, ["token_id", "asset_id", "outcome_token_id"]) or "",
            "price_columns": ";".join([c for c in columns if any(x in c.lower() for x in ["price", "bid", "ask", "midpoint", "probability"])]),
            "liquidity_columns": ";".join([c for c in columns if "liquidity" in c.lower() or "depth" in c.lower()]),
            "volume_columns": ";".join([c for c in columns if "volume" in c.lower()]),
            "suitable_for_training": suitability == "true",
            "suitable_for_diagnostics_only": suitability == "diagnostics_only",
            "suitable_for_governance_only": suitability == "governance_only",
            "classification_reason": reason,
        }
        rows.append(row)
    out = cfg.governance_root
    write_csv(out / "data_inventory.csv", rows)
    write_json(out / "data_inventory.json", rows)
    return rows


def main(config_path: str) -> list[dict[str, Any]]:
    return inventory(load_config(config_path))
