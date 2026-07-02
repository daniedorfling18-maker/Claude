#!/usr/bin/env python3
"""Summarise local Polymarket history into a paper-trading decision report.

Read-only. This script does not collect data, score markets, open shadow
positions, paper trade, or live trade. It only reads local CSV/JSON outputs and
writes a governance report explaining whether the current local evidence supports
paper trading.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from polymarket_predictive_engine.config import load_config  # noqa: E402
from polymarket_predictive_engine.utils import now_utc, parse_timestamp, read_json, safe_float, write_csv, write_json  # noqa: E402


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _timestamp_range(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> dict[str, str]:
    values: list[str] = []
    for row in rows:
        for key in keys:
            parsed = parse_timestamp(row.get(key))
            if parsed is not None:
                values.append(parsed.isoformat().replace("+00:00", "Z"))
                break
    if not values:
        return {"min": "", "max": ""}
    return {"min": min(values), "max": max(values)}


def _num(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    value = safe_float(row.get(key))
    return default if value is None else float(value)


def _boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _file_table(paths: dict[str, Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, path in sorted(paths.items()):
        csv_rows = _read_csv(path) if path.suffix.lower() == ".csv" else []
        rows.append(
            {
                "name": name,
                "path": str(path),
                "exists": path.exists(),
                "bytes": path.stat().st_size if path.exists() else 0,
                "rows": len(csv_rows) if path.suffix.lower() == ".csv" else "",
            }
        )
    return rows


def _labels_summary(labels: list[dict[str, Any]]) -> dict[str, Any]:
    targets = Counter(str(row.get("target") or row.get("label") or "") for row in labels)
    markets = {str(row.get("market_id") or row.get("market_slug") or "") for row in labels}
    tokens = {str(row.get("token_id") or row.get("asset_id") or "") for row in labels}
    return {
        "rows": len(labels),
        "markets": len([item for item in markets if item]),
        "tokens": len([item for item in tokens if item]),
        "target_counts": dict(sorted(targets.items())),
        "timestamp_range": _timestamp_range(labels, ("resolved_at", "timestamp", "collected_at_utc")),
    }


def _feature_summary(features: list[dict[str, Any]]) -> dict[str, Any]:
    categories = Counter(str(row.get("category") or "unknown") for row in features)
    return {
        "rows": len(features),
        "categories": dict(categories.most_common(20)),
        "timestamp_range": _timestamp_range(features, ("prediction_timestamp", "snapshot_timestamp", "collected_at_utc", "timestamp")),
    }


def _signal_summary(approved: list[dict[str, Any]], rejected: list[dict[str, Any]]) -> dict[str, Any]:
    reason_counts = Counter(str(row.get("rejection_reason") or "unknown") for row in rejected)
    category_counts = Counter(str(row.get("category") or "unknown") for row in rejected)
    return {
        "approved": len(approved),
        "rejected": len(rejected),
        "top_rejection_reasons": dict(reason_counts.most_common(15)),
        "rejected_by_category": dict(category_counts.most_common(15)),
    }


def _alpha_summary(alpha_rows: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [row for row in alpha_rows if _boolish(row.get("alpha_trade_candidate"))]
    shadow_candidates = [row for row in alpha_rows if _boolish(row.get("shadow_trade_candidate"))]
    near_miss = [row for row in alpha_rows if _boolish(row.get("near_miss_learning_candidate"))]
    sorted_rows = sorted(alpha_rows, key=lambda row: _num(row, "edge_lower_bound", -999.0), reverse=True)
    top_rows = [
        {
            "category": row.get("category", ""),
            "market_slug": row.get("market_slug", ""),
            "outcome": row.get("outcome", ""),
            "signal_cohort": row.get("signal_cohort", ""),
            "alpha_trade_candidate": row.get("alpha_trade_candidate", ""),
            "shadow_trade_candidate": row.get("shadow_trade_candidate", ""),
            "edge_lower_bound": row.get("edge_lower_bound", ""),
            "alpha_raw_edge": row.get("alpha_raw_edge", ""),
            "liquidity": row.get("liquidity", ""),
            "spread": row.get("spread", ""),
            "validation_layer_pass": row.get("validation_layer_pass", ""),
            "validation_layer_reason": row.get("validation_layer_reason", ""),
        }
        for row in sorted_rows[:10]
    ]
    return {
        "rows": len(alpha_rows),
        "alpha_trade_candidates": len(candidates),
        "shadow_trade_candidates": len(shadow_candidates),
        "near_miss_learning_candidates": len(near_miss),
        "candidate_categories": dict(Counter(str(row.get("category") or "unknown") for row in candidates).most_common()),
        "top_edge_rows": top_rows,
    }


def _shadow_summary(positions: list[dict[str, Any]], fills: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    total_cost = sum(_num(row, "cost_basis_usdc") for row in positions)
    realised = sum(_num(row, "realised_pnl_usdc") for row in positions)
    unrealised = sum(_num(row, "unrealised_pnl_usdc") for row in positions if str(row.get("status") or "").lower() == "open")
    status_counts = Counter(str(row.get("status") or "unknown") for row in positions)
    source_counts = Counter(str(row.get("shadow_source") or "unknown") for row in positions)
    cohorts: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "signal_cohort": "",
            "positions": 0,
            "open_positions": 0,
            "closed_positions": 0,
            "cost_basis_usdc": 0.0,
            "realised_pnl_usdc": 0.0,
            "unrealised_pnl_usdc": 0.0,
            "total_pnl_usdc": 0.0,
            "roi": 0.0,
        }
    )
    for row in positions:
        cohort = str(row.get("signal_cohort") or "unknown")
        item = cohorts[cohort]
        item["signal_cohort"] = cohort
        item["positions"] += 1
        if str(row.get("status") or "").lower() == "open":
            item["open_positions"] += 1
            item["unrealised_pnl_usdc"] += _num(row, "unrealised_pnl_usdc")
        elif str(row.get("status") or "").lower() == "closed":
            item["closed_positions"] += 1
        item["cost_basis_usdc"] += _num(row, "cost_basis_usdc")
        item["realised_pnl_usdc"] += _num(row, "realised_pnl_usdc")
    for item in cohorts.values():
        item["total_pnl_usdc"] = item["realised_pnl_usdc"] + item["unrealised_pnl_usdc"]
        item["roi"] = item["total_pnl_usdc"] / item["cost_basis_usdc"] if item["cost_basis_usdc"] else 0.0
    cohort_rows = sorted(cohorts.values(), key=lambda item: item["total_pnl_usdc"])
    return (
        {
            "positions": len(positions),
            "fills": len(fills),
            "status_counts": dict(status_counts.most_common()),
            "shadow_source_counts": dict(source_counts.most_common()),
            "total_cost_basis_usdc": total_cost,
            "realised_pnl_usdc": realised,
            "unrealised_pnl_usdc": unrealised,
            "total_pnl_usdc": realised + unrealised,
            "roi": (realised + unrealised) / total_cost if total_cost else 0.0,
            "worst_cohorts": cohort_rows[:10],
            "best_cohorts": list(reversed(cohort_rows[-10:])),
        },
        list(reversed(cohort_rows)),
    )


def _family_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    decisions = Counter(str(row.get("decision") or "unknown") for row in rows)
    return {
        "rows": len(rows),
        "decisions": dict(decisions.most_common()),
        "families": [
            {
                "family": row.get("family", ""),
                "decision": row.get("decision", ""),
                "settlement_roi": row.get("settlement_roi", ""),
                "liquidity_tradable_tokens": row.get("liquidity_tradable_tokens", ""),
                "live_candidates": row.get("live_candidates", ""),
                "near_approval_candidates": row.get("near_approval_candidates", ""),
                "best_edge": row.get("best_edge", ""),
            }
            for row in rows[:25]
        ],
    }


def _paper_decision(payload: dict[str, Any]) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    if payload["signals"]["approved"] <= 0:
        blockers.append("no approved trade_signals rows")
    if payload["shadow"]["total_pnl_usdc"] < 0:
        warnings.append(
            "aggregate shadow P&L is negative across all experimental cohorts; diagnostic warning, not a cross-family paper blocker"
        )
    sports = next(
        (row for row in payload["shadow_cohorts"] if row.get("signal_cohort") == "sports_other"),
        None,
    )
    if sports:
        if float(sports.get("total_pnl_usdc") or 0.0) <= 0:
            blockers.append("sports_other shadow evidence is not positive")
        if int(sports.get("closed_positions") or 0) <= 0:
            blockers.append("sports_other has no closed/settled positions yet")
    else:
        blockers.append("sports_other has no shadow evidence yet")
    if blockers:
        return {"paper_allowed": False, "reason": "; ".join(blockers), "warnings": warnings}
    return {"paper_allowed": True, "reason": "local family-specific evidence gates are satisfied", "warnings": warnings}


def _closing_line_summary(governance_root: Path) -> dict[str, Any]:
    artifact = read_json(governance_root / "closing_line_value.json", default={}) or {}
    if not isinstance(artifact, dict):
        artifact = {}
    cohorts = artifact.get("cohorts") or []
    if not isinstance(cohorts, list):
        cohorts = []
    final_cohorts = [row for row in cohorts if isinstance(row, dict) and (row.get("final_positions") or 0) > 0]
    return {
        "positions_scored": artifact.get("positions_scored", 0),
        "final_line_positions": artifact.get("final_line_positions", 0),
        "mean_final_clv": artifact.get("mean_final_clv"),
        "positive_clv_cohorts": artifact.get("positive_clv_cohorts", []),
        "cohorts": final_cohorts,
    }


def _write_markdown(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# Polymarket Local History Audit",
        "",
        f"Generated: {payload['generated_at_utc']}",
        "",
        f"## Paper decision: {'ALLOW' if payload['paper_decision']['paper_allowed'] else 'BLOCK'}",
        "",
        payload["paper_decision"]["reason"],
        "",
    ]
    warnings = payload["paper_decision"].get("warnings") or []
    if warnings:
        lines.extend(["## Warnings", ""])
        for warning in warnings:
            lines.append(f"- {warning}")
        lines.append("")
    lines.extend(
        [
            "## Core counts",
            "",
            f"- Features: {payload['features']['rows']}",
            f"- Labels: {payload['labels']['rows']}",
            f"- Predictions: {payload['alpha']['rows']}",
            f"- Approved signals: {payload['signals']['approved']}",
            f"- Rejected signals: {payload['signals']['rejected']}",
            f"- Shadow positions: {payload['shadow']['positions']}",
            f"- Shadow fills: {payload['shadow']['fills']}",
            "",
            "## Shadow P&L",
            "",
            f"- Cost basis: {payload['shadow']['total_cost_basis_usdc']:.2f}",
            f"- Realised P&L: {payload['shadow']['realised_pnl_usdc']:.2f}",
            f"- Unrealised P&L: {payload['shadow']['unrealised_pnl_usdc']:.2f}",
            f"- Total P&L: {payload['shadow']['total_pnl_usdc']:.2f}",
            f"- ROI: {payload['shadow']['roi']:.4f}",
            "",
            "## Top rejection reasons",
            "",
        ]
    )
    for reason, count in payload["signals"]["top_rejection_reasons"].items():
        lines.append(f"- {count}: {reason}")
    lines.extend(["", "## Top alpha rows", ""])
    for row in payload["alpha"]["top_edge_rows"][:10]:
        lines.append(
            "- "
            f"{row['category']} | {row['market_slug']} | {row['outcome']} | "
            f"edge_lower_bound={row['edge_lower_bound']} | candidate={row['alpha_trade_candidate']}"
        )
    lines.extend(["", "## Closing-line value (CLV)", ""])
    clv = payload.get("closing_line_value") or {}
    if not clv.get("positions_scored"):
        lines.append("No CLV evidence collected yet.")
    else:
        lines.append(
            f"- Scored positions: {clv['positions_scored']} | final lines: {clv['final_line_positions']} | "
            f"mean final CLV: {clv['mean_final_clv']}"
        )
        for row in clv.get("cohorts", []):
            lines.append(
                "- "
                f"{row.get('signal_cohort')} — n_final={row.get('final_positions')}, "
                f"mean_final_clv={row.get('mean_final_clv')}, "
                f"CI=[{row.get('final_clv_ci_low')}, {row.get('final_clv_ci_high')}], "
                f"evidence={row.get('clv_evidence')}"
            )
        positive = clv.get("positive_clv_cohorts") or []
        if positive:
            lines.append(f"- Positive CLV cohorts (diagnostic, not a promotion trigger): {', '.join(positive)}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run(config_path: str = "polymarket_predictive_config.example.yaml") -> dict[str, Any]:
    cfg = load_config(config_path)
    output_root = cfg.output_root
    governance_root = cfg.governance_root
    paths = {
        "features_v2": output_root / "polymarket_training" / "features_v2.csv",
        "features_v2_websocket": output_root / "polymarket_training" / "features_v2_websocket.csv",
        "labels": output_root / "polymarket_training" / "labels.csv",
        "predictions": output_root / "polymarket_predictions" / "predictions.csv",
        "mispricing_alpha_scores": output_root / "polymarket_predictions" / "mispricing_alpha_scores.csv",
        "trade_signals": output_root / "polymarket_predictions" / "trade_signals.csv",
        "rejected_signals": output_root / "polymarket_predictions" / "rejected_signals.csv",
        "shadow_positions": output_root / "polymarket_shadow" / "shadow_positions.csv",
        "shadow_fills": output_root / "polymarket_shadow" / "shadow_fills.csv",
        "liquidity_watchlist": output_root / "polymarket_liquidity_discovery" / "liquidity_watchlist.csv",
        "family_viability": governance_root / "family_viability_leaderboard.csv",
    }
    features = _read_csv(paths["features_v2"])
    labels = _read_csv(paths["labels"])
    approved = _read_csv(paths["trade_signals"])
    rejected = _read_csv(paths["rejected_signals"])
    alpha_rows = _read_csv(paths["mispricing_alpha_scores"])
    shadow_positions = _read_csv(paths["shadow_positions"])
    shadow_fills = _read_csv(paths["shadow_fills"])
    family_rows = _read_csv(paths["family_viability"])
    shadow_summary, shadow_cohorts = _shadow_summary(shadow_positions, shadow_fills)
    payload: dict[str, Any] = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "files": _file_table(paths),
        "features": _feature_summary(features),
        "labels": _labels_summary(labels),
        "signals": _signal_summary(approved, rejected),
        "alpha": _alpha_summary(alpha_rows),
        "shadow": shadow_summary,
        "shadow_cohorts": shadow_cohorts,
        "family_viability": _family_summary(family_rows),
    }
    payload["paper_decision"] = _paper_decision(payload)
    # CLV is report-only context added after the paper decision on purpose: it
    # must never add or remove blockers here (promotion use is governed by WP4).
    payload["closing_line_value"] = _closing_line_summary(governance_root)
    governance_root.mkdir(parents=True, exist_ok=True)
    write_json(governance_root / "local_history_audit_summary.json", payload)
    write_csv(governance_root / "local_history_shadow_cohorts.csv", shadow_cohorts)
    write_csv(governance_root / "local_history_file_inventory.csv", payload["files"])
    _write_markdown(governance_root / "local_history_audit_report.md", payload)
    print(
        json.dumps(
            {
                "status": "ok",
                "paper_decision": payload["paper_decision"],
                "features": payload["features"]["rows"],
                "labels": payload["labels"]["rows"],
                "approved_signals": payload["signals"]["approved"],
                "rejected_signals": payload["signals"]["rejected"],
                "shadow_positions": payload["shadow"]["positions"],
                "shadow_total_pnl_usdc": payload["shadow"]["total_pnl_usdc"],
                "shadow_roi": payload["shadow"]["roi"],
                "report": str(governance_root / "local_history_audit_report.md"),
                "summary": str(governance_root / "local_history_audit_summary.json"),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return payload


def main() -> int:
    config_path = sys.argv[1] if len(sys.argv) > 1 else "polymarket_predictive_config.example.yaml"
    run(config_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
