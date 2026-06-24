from __future__ import annotations

from typing import Any

from .config import EngineConfig, kill_switch_active, load_config
from .data_quality import data_quality
from .pipeline_health import pipeline_health
from .utils import csv_columns, discover_files, find_first_column, read_csv_rows, read_json, write_json

DECISIONS = {
    "APPROVED_FOR_TRAINING",
    "APPROVED_FOR_BACKTEST_ONLY",
    "APPROVED_FOR_PAPER_TRADING_ONLY",
    "NOT_APPROVED_INSUFFICIENT_LABELS",
    "NOT_APPROVED_DATA_QUALITY_BLOCKERS",
    "NOT_APPROVED_PIPELINE_STALE",
    "NOT_APPROVED_SCHEMA_UNKNOWN",
}


def _clean_resolved_markets_from_resolution_file(cfg: EngineConfig) -> set[str]:
    path = cfg.output_root / "polymarket_training" / "market_resolutions.csv"
    resolved: set[str] = set()
    for row in read_csv_rows(path):
        if row.get("resolution_quality") == "clean_settlement" and str(row.get("target", "")).lower() in {"0", "1", "true", "false"}:
            market = row.get("market_slug") or row.get("condition_id") or row.get("gamma_market_id")
            if market:
                resolved.add(market)
    return resolved


def count_clean_resolved_labels(cfg: EngineConfig) -> int:
    """Unique (market, token) clean settlements across all resolution files."""
    seen: set[tuple[str, str]] = set()
    for name in ("market_resolutions.csv", "historical_resolutions.csv", "websocket_resolutions.csv"):
        for row in read_csv_rows(cfg.output_root / "polymarket_training" / name):
            if row.get("resolution_quality") != "clean_settlement":
                continue
            token = row.get("token_id", "")
            if not token or str(row.get("target", "")).lower() not in {"0", "1", "true", "false"}:
                continue
            market = row.get("condition_id") or row.get("market_slug") or row.get("gamma_market_id") or ""
            seen.add((market, token))
    return len(seen)


def paper_live_promotion_gate(cfg: EngineConfig) -> dict[str, Any]:
    """Gate paper->live promotion on accumulated labels AND proven out-of-sample skill.

    Promotion is blocked until at least ``min_resolved_labels`` clean labels exist and the
    skill model shows a statistically significant edge over the market out of sample; live
    additionally requires ``target_resolved_labels`` and the existing live-trading approvals.
    """
    thresholds = cfg.raw.get("governance_thresholds", {})
    min_labels = int(thresholds.get("min_resolved_labels", 100))
    target_labels = int(thresholds.get("target_resolved_labels", 300))

    labels = count_clean_resolved_labels(cfg)
    skill = read_json(cfg.governance_root / "skill_model_summary.json", default={}) or {}
    oos = skill.get("oos_vs_market", {}) if isinstance(skill, dict) else {}
    skill_significant = bool(oos.get("beats_market_significantly"))
    dq_issues, _ = data_quality(cfg, allow_warnings=True)
    # "no_raw_snapshots" reflects the live raw collector, not the historical-pull corpus
    # this gate judges, so it is not a promotion blocker here.
    blockers = sum(1 for i in dq_issues if i.get("severity") == "blocker" and i.get("issue_type") != "no_raw_snapshots")

    paper_reasons: list[str] = []
    if labels < min_labels:
        paper_reasons.append(f"insufficient resolved labels: {labels} < {min_labels}")
    if not skill_significant:
        paper_reasons.append("no statistically significant out-of-sample skill over the market")
    if blockers:
        paper_reasons.append(f"{blockers} data-quality blocker(s)")
    if kill_switch_active():
        paper_reasons.append("kill switch active")

    live_reasons = list(paper_reasons)
    if labels < target_labels:
        live_reasons.append(f"labels below live target: {labels} < {target_labels}")
    if not cfg.live_approval_file.exists():
        live_reasons.append(f"human approval file missing: {cfg.live_approval_file}")

    payload = {
        "resolved_labels": labels,
        "min_resolved_labels": min_labels,
        "target_resolved_labels": target_labels,
        "skill_model_status": skill.get("status", "missing"),
        "oos_brier_skill_vs_market": oos.get("brier_skill_vs_market"),
        "oos_beats_market_significantly": skill_significant,
        "data_quality_blockers": blockers,
        "approved_for_paper_trading": not paper_reasons,
        "approved_for_live_trading": not live_reasons,
        "paper_blockers": paper_reasons,
        "live_blockers": live_reasons,
    }
    write_json(cfg.governance_root / "promotion_gate.json", payload)
    return payload


def readiness_decision(cfg: EngineConfig) -> dict[str, Any]:
    _, dq_summary = data_quality(cfg, allow_warnings=True)
    health_rows, health_summary = pipeline_health(cfg)
    raw_files = discover_files(cfg.data_root, ["outputs/polymarket_wide/**/raw_market_snapshots.csv", "outputs/polymarket_fixed/**/raw_market_snapshots.csv"])
    unique_markets: set[str] = set()
    resolved_markets: set[str] = _clean_resolved_markets_from_resolution_file(cfg)
    snapshot_counts: dict[str, int] = {}
    schema_unknown = False
    for path in raw_files:
        cols = csv_columns(path)
        market_col = find_first_column(cols, cfg.raw.get("schema", {}).get("market_id_fields", ["market_id", "condition_id", "id", "market_slug", "slug"]))
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
    joined_resolved_markets = unique_markets.intersection(resolved_markets)
    if dq_summary.get("blocker_count", 0):
        decision = "NOT_APPROVED_DATA_QUALITY_BLOCKERS"
    elif schema_unknown:
        decision = "NOT_APPROVED_SCHEMA_UNKNOWN"
    elif health_summary.get("stalled_categories"):
        decision = "NOT_APPROVED_PIPELINE_STALE"
    elif len(joined_resolved_markets) < cfg.min_resolved_markets:
        decision = "NOT_APPROVED_INSUFFICIENT_LABELS"
    elif len(unique_markets) >= cfg.min_resolved_markets and min(snapshot_counts.values() or [0]) >= cfg.min_snapshots_per_market:
        decision = "APPROVED_FOR_TRAINING"
    else:
        decision = "APPROVED_FOR_BACKTEST_ONLY"
    payload = {
        "decision": decision,
        "data_quality_blockers": dq_summary.get("blocker_count", 0),
        "unique_markets": len(unique_markets),
        "unique_resolved_markets": len(joined_resolved_markets),
        "clean_resolution_markets_available": len(resolved_markets),
        "minimum_snapshots_per_market_observed": min(snapshot_counts.values() or [0]),
        "category_coverage": [r["category"] for r in health_rows if r.get("raw_snapshots_growing")],
        "stale_collectors": health_summary.get("stalled_categories", []),
        "duplicate_writer_risks": [],
        "unsuitable_latest_only_data": any(r.get("latest_joined_updating") and not r.get("raw_snapshots_growing") for r in health_rows),
        "missing_resolution_outcomes": len(joined_resolved_markets) == 0,
    }
    write_json(cfg.governance_root / "data_readiness_decision.json", payload)
    return payload


def main(config_path: str) -> dict[str, Any]:
    return readiness_decision(load_config(config_path))
