from __future__ import annotations

from typing import Any

from .config import EngineConfig, load_config
from .data_quality import data_quality
from .pipeline_health import pipeline_health
from .utils import csv_columns, discover_files, find_first_column, read_csv_rows, write_json

DECISIONS = {
    "APPROVED_FOR_TRAINING",
    "APPROVED_FOR_BACKTEST_ONLY",
    "APPROVED_FOR_PAPER_TRADING_ONLY",
    "NOT_APPROVED_INSUFFICIENT_LABELS",
    "NOT_APPROVED_DATA_QUALITY_BLOCKERS",
    "NOT_APPROVED_PIPELINE_STALE",
    "NOT_APPROVED_SCHEMA_UNKNOWN",
}


def readiness_decision(cfg: EngineConfig) -> dict[str, Any]:
    _, dq_summary = data_quality(cfg, allow_warnings=True)
    health_rows, health_summary = pipeline_health(cfg)
    raw_files = discover_files(cfg.data_root, ["outputs/polymarket_wide/**/raw_market_snapshots.csv", "outputs/polymarket_fixed/**/raw_market_snapshots.csv"])
    unique_markets: set[str] = set()
    resolved_markets: set[str] = set()
    snapshot_counts: dict[str, int] = {}
    schema_unknown = False
    for path in raw_files:
        cols = csv_columns(path)
        market_col = find_first_column(cols, cfg.raw.get("schema", {}).get("market_id_fields", ["market_id", "condition_id", "id", "slug"]))
        if not market_col:
            schema_unknown = True
            continue
        rows = read_csv_rows(path, limit=200000)
        for row in rows:
            m = row.get(market_col, "")
            if m:
                unique_markets.add(m)
                snapshot_counts[m] = snapshot_counts.get(m, 0) + 1
                keys = " ".join(row.keys()).lower()
                if any(k in keys for k in ["winning", "resolved", "resolution"]) and any(str(v).lower() in {"1", "true", "yes", "won", "winner"} for v in row.values()):
                    resolved_markets.add(m)
    if dq_summary.get("blocker_count", 0):
        decision = "NOT_APPROVED_DATA_QUALITY_BLOCKERS"
    elif schema_unknown:
        decision = "NOT_APPROVED_SCHEMA_UNKNOWN"
    elif health_summary.get("stalled_categories"):
        decision = "NOT_APPROVED_PIPELINE_STALE"
    elif len(resolved_markets) < cfg.min_resolved_markets:
        decision = "NOT_APPROVED_INSUFFICIENT_LABELS"
    elif len(unique_markets) >= cfg.min_resolved_markets and min(snapshot_counts.values() or [0]) >= cfg.min_snapshots_per_market:
        decision = "APPROVED_FOR_TRAINING"
    else:
        decision = "APPROVED_FOR_BACKTEST_ONLY"
    payload = {
        "decision": decision,
        "data_quality_blockers": dq_summary.get("blocker_count", 0),
        "unique_markets": len(unique_markets),
        "unique_resolved_markets": len(resolved_markets),
        "minimum_snapshots_per_market_observed": min(snapshot_counts.values() or [0]),
        "category_coverage": [r["category"] for r in health_rows if r.get("raw_snapshots_growing")],
        "stale_collectors": health_summary.get("stalled_categories", []),
        "duplicate_writer_risks": [],
        "unsuitable_latest_only_data": any(r.get("latest_joined_updating") and not r.get("raw_snapshots_growing") for r in health_rows),
        "missing_resolution_outcomes": len(resolved_markets) == 0,
    }
    write_json(cfg.governance_root / "data_readiness_decision.json", payload)
    return payload


def main(config_path: str) -> dict[str, Any]:
    return readiness_decision(load_config(config_path))
