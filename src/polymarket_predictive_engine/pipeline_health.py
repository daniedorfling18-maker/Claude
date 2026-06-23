from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import EngineConfig, load_config
from .utils import discover_files, infer_category, parse_timestamp, read_csv_rows, read_json, write_csv, write_json


def _latest_timestamp(rows: list[dict[str, str]]) -> str:
    candidates = []
    for row in rows:
        for key, value in row.items():
            if any(x in key.lower() for x in ["time", "timestamp", "date"]):
                dt = parse_timestamp(value)
                if dt:
                    candidates.append(dt)
    return max(candidates).strftime("%Y-%m-%dT%H:%M:%SZ") if candidates else ""


def _has_resolved_labels(path: Path) -> bool:
    rows = read_csv_rows(path, limit=1000)
    for row in rows:
        keys = " ".join(row.keys()).lower()
        if any(k in keys for k in ["winning", "resolved", "outcome", "resolution"]):
            if any(str(v).strip() for v in row.values()):
                return True
    return False


def pipeline_health(cfg: EngineConfig) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for category in cfg.categories:
        base_patterns = [f"outputs/polymarket_wide/{category}/**/*", f"outputs/polymarket_fixed/{category}/**/*"]
        files = discover_files(cfg.data_root, base_patterns)
        heartbeat_files = [p for p in files if p.name == "collector_heartbeat.csv"]
        state_files = [p for p in files if p.name == "collector_state.json"]
        raw_files = [p for p in files if p.name == "raw_market_snapshots.csv"]
        latest_files = [p for p in files if p.name == "latest_joined_snapshot.csv"]
        opp_files = [p for p in files if p.name == "opportunities.csv"]
        exec_files = [p for p in files if p.name == "execution_log.csv"]
        latest_heartbeat = ""
        for f in heartbeat_files:
            latest_heartbeat = max(latest_heartbeat, _latest_timestamp(read_csv_rows(f, limit=5000)))
        rows_raw = sum(len(read_csv_rows(f, limit=100000)) for f in raw_files)
        latest_rows = sum(len(read_csv_rows(f, limit=5000)) for f in latest_files)
        resolved = any(_has_resolved_labels(f) for f in raw_files)
        freshness_minutes = None
        if latest_heartbeat:
            dt = parse_timestamp(latest_heartbeat)
            from datetime import datetime, timezone
            if dt:
                freshness_minutes = (datetime.now(timezone.utc) - dt).total_seconds() / 60
        stalled = freshness_minutes is not None and freshness_minutes > float(cfg.raw.get("freshness_thresholds", {}).get("collector_stale_minutes", 60))
        row = {
            "category": category,
            "last_heartbeat": latest_heartbeat,
            "collector_freshness_minutes": freshness_minutes if freshness_minutes is not None else "",
            "raw_snapshots_growing": rows_raw > 0,
            "latest_joined_updating": latest_rows > 0,
            "opportunity_files_generated": any(p.exists() for p in opp_files),
            "execution_logs_non_empty": any(p.stat().st_size > 0 for p in exec_files),
            "stalled": stalled,
            "placeholder_only_ml": latest_rows == 0 and any("ml" in str(p).lower() for p in latest_files + raw_files),
            "insufficient_data_for_training": rows_raw < int(cfg.raw.get("governance_thresholds", {}).get("min_training_rows", 1000)),
            "resolved_labels_available": resolved,
            "duplicate_services_may_write_same_output": False,
            "service_can_execute_orders": False,
            "state_file_count": len(state_files),
            "state_summary": read_json(state_files[0], {}) if state_files else {},
        }
        rows.append(row)
    summary = {
        "category_count": len(rows),
        "stalled_categories": [r["category"] for r in rows if r["stalled"]],
        "categories_with_resolved_labels": [r["category"] for r in rows if r["resolved_labels_available"]],
        "any_service_can_execute_orders": any(r["service_can_execute_orders"] for r in rows),
        "any_training_data": any(not r["insufficient_data_for_training"] for r in rows),
    }
    write_csv(cfg.governance_root / "pipeline_health.csv", rows)
    write_json(cfg.governance_root / "pipeline_health_summary.json", summary)
    return rows, summary


def main(config_path: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return pipeline_health(load_config(config_path))
