#!/usr/bin/env python3
"""Read-only Polymarket family viability audit.

Ranks market families by final-settlement evidence, current candidate quality,
liquidity, and quarantine state. It does not open positions, generate orders,
or loosen paper-trading gates.
"""
from __future__ import annotations

import csv
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from polymarket_predictive_engine.config import load_config  # noqa: E402
from polymarket_predictive_engine.shadow_cohort import _crypto_updown_proxy_settlement_price  # noqa: E402
from polymarket_predictive_engine.strategy_search import _market_family  # noqa: E402
from polymarket_predictive_engine.utils import now_utc, read_csv_rows, read_json, safe_float, write_csv, write_json  # noqa: E402

_HEX_SLUG_RE = re.compile(r"^0x[0-9a-fA-F]{24,}$")


def _family(row: dict[str, Any]) -> str:
    return _market_family(
        {
            "market_slug": row.get("market_slug", ""),
            "question": row.get("question", ""),
            "category": row.get("category", ""),
            "outcome": row.get("outcome", ""),
        }
    )


def _is_unresolved_unknown_row(row: dict[str, Any]) -> bool:
    family = _family(row)
    if family != "unknown":
        return False
    question = str(row.get("question") or "").strip()
    slug = str(row.get("market_slug") or "").strip()
    cohort = str(row.get("signal_cohort") or "").strip().lower()
    return not question or bool(_HEX_SLUG_RE.match(slug)) or cohort.startswith("near_miss_learning|unknown")


def _stats() -> dict[str, Any]:
    return {
        "settled": 0,
        "wins": 0,
        "stake": 0.0,
        "settlement_pnl": 0.0,
        "early_pnl": 0.0,
        "live_candidates": 0,
        "scored_candidates": 0,
        "near_approval_candidates": 0,
        "best_edge": -999.0,
        "max_live_liquidity": 0.0,
        "quarantined_cohorts": 0,
        "liquidity_tradable_tokens": 0,
        "liquidity_tokens": 0,
        "liquidity_max": 0.0,
        "liquidity_min_spread": "",
        "unresolved_unknown_rows": 0,
    }


def _add_settlement(stats: dict[str, Any], *, pnl: float, cost: float, early_pnl: float) -> None:
    if cost <= 0:
        return
    stats["settled"] += 1
    stats["wins"] += 1 if pnl > 0 else 0
    stats["stake"] += cost
    stats["settlement_pnl"] += pnl
    stats["early_pnl"] += early_pnl


def _settlement_audit(cfg, positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for position in positions:
        settlement_price, reason = _crypto_updown_proxy_settlement_price(position, timeout_seconds=20)
        entry = safe_float(position.get("entry_price")) or 0.0
        cost = safe_float(position.get("cost_basis_usdc")) or 0.0
        early = safe_float(position.get("realised_pnl_usdc")) or safe_float(position.get("unrealised_pnl_usdc")) or 0.0
        final_pnl: float | None = None
        if settlement_price in {0.0, 1.0} and entry > 0 and cost > 0:
            final_pnl = (cost / entry) * float(settlement_price) - cost
        elif str(position.get("status") or "").lower() == "closed" and cost > 0:
            final_pnl = safe_float(position.get("realised_pnl_usdc")) or 0.0
            if reason == "not_crypto_updown_slug":
                reason = "non_crypto_closed_realised_pnl"
        if final_pnl is None:
            continue
        rows.append(
            {
                "family": _family(position),
                "unresolved_unknown": _is_unresolved_unknown_row(position),
                "signal_cohort": position.get("signal_cohort", ""),
                "shadow_source": position.get("shadow_source", ""),
                "market_slug": position.get("market_slug", ""),
                "question": position.get("question", ""),
                "outcome": position.get("outcome", ""),
                "status": position.get("status", ""),
                "entry_price": entry,
                "cost_basis_usdc": cost,
                "early_close_reason": position.get("close_reason", ""),
                "early_pnl_usdc": early,
                "settlement_price": "" if settlement_price is None else settlement_price,
                "settlement_reason": reason,
                "settlement_pnl_if_held_usdc": final_pnl,
                "opened_at": position.get("opened_at", ""),
                "close_time": position.get("close_time", ""),
                "closed_at": position.get("closed_at", ""),
            }
        )
    return rows


def _decision_for_family(family: str, stats: dict[str, Any], roi: float) -> tuple[str, float]:
    score = max(-50.0, min(50.0, roi * 100.0))
    score += min(10.0, float(stats["liquidity_tradable_tokens"]))
    score += min(10.0, float(stats["scored_candidates"]))
    score += min(5.0, max(0.0, float(stats["best_edge"])) * 50.0)
    score -= min(20.0, float(stats["quarantined_cohorts"]) * 5.0)
    if stats["settled"] < 3:
        score -= 10.0

    if family == "unknown" or stats.get("unresolved_unknown_rows", 0) > 0:
        return "research_only_resolve_family", min(score, -15.0)
    if stats["settled"] >= 3 and roi > 0.03 and stats["liquidity_tradable_tokens"] > 0:
        return "candidate_for_focus", score
    if stats["liquidity_tradable_tokens"] > 0 and stats["settled"] < 3:
        return "collect_settlement_evidence", score
    if stats["settled"] >= 3 and roi <= 0:
        return "avoid_negative_settlement_evidence", score
    return "avoid", min(score, -10.0)


def build_audit(config_path: str = "polymarket_predictive_config.example.yaml") -> dict[str, Any]:
    cfg = load_config(config_path)
    gov = cfg.governance_root
    predictions_root = cfg.output_root / "polymarket_predictions"
    shadow_root = cfg.output_root / "polymarket_shadow"
    training_root = cfg.output_root / "polymarket_training"

    positions = read_csv_rows(shadow_root / "shadow_positions.csv")
    rejected = read_csv_rows(predictions_root / "rejected_signals.csv")
    predictions = read_csv_rows(predictions_root / "predictions.csv")
    settlement_rows = _settlement_audit(cfg, positions)
    liquidity = read_json(gov / "liquidity_discovery_summary.json", default={}) or {}
    shadow_summary = read_json(gov / "shadow_signal_cohort_pnl.json", default={}) or {}

    families: dict[str, dict[str, Any]] = defaultdict(_stats)
    for row in settlement_rows:
        family = str(row.get("family") or "unknown")
        stats = families[family]
        _add_settlement(
            stats,
            pnl=float(row.get("settlement_pnl_if_held_usdc") or 0.0),
            cost=float(row.get("cost_basis_usdc") or 0.0),
            early_pnl=float(row.get("early_pnl_usdc") or 0.0),
        )
        if str(row.get("unresolved_unknown") or "").lower() == "true":
            stats["unresolved_unknown_rows"] += 1

    gate_counts: Counter[str] = Counter()
    near_approval: list[dict[str, Any]] = []
    for row in rejected:
        family = _family(row)
        stats = families[family]
        edge = safe_float(row.get("edge_lower_bound") or row.get("edge"))
        liquidity_value = safe_float(row.get("liquidity")) or 0.0
        reason = str(row.get("rejection_reason") or "")
        model_status = str(row.get("crypto_model_status") or "")
        stats["live_candidates"] += 1
        stats["scored_candidates"] += 1 if model_status == "scored" else 0
        if edge is not None:
            stats["best_edge"] = max(float(stats["best_edge"]), edge)
        stats["max_live_liquidity"] = max(float(stats["max_live_liquidity"]), liquidity_value)
        failed = []
        if edge is None or edge < 0.03:
            failed.append("edge_lt_0_03")
        if liquidity_value < 100:
            failed.append("liquidity_lt_100")
        if "cohort_quarantined" in reason:
            failed.append("cohort_quarantined")
        if "before_model_window" in reason:
            failed.append("before_model_window")
        if "too_close_to_resolution" in reason:
            failed.append("too_close_to_resolution")
        for item in failed:
            gate_counts[item] += 1
        if len(failed) <= 1:
            stats["near_approval_candidates"] += 1
            near_approval.append(
                {
                    "family": family,
                    "market_slug": row.get("market_slug", ""),
                    "question": row.get("question", ""),
                    "outcome": row.get("outcome", ""),
                    "signal_cohort": row.get("signal_cohort", ""),
                    "edge_lower_bound": edge,
                    "liquidity": liquidity_value,
                    "spread": row.get("spread", ""),
                    "crypto_model_status": model_status,
                    "failed_gates": "; ".join(failed),
                    "rejection_reason": reason,
                }
            )

    if isinstance(liquidity, dict):
        for row in liquidity.get("family_summary", []) or []:
            if not isinstance(row, dict):
                continue
            family = str(row.get("family") or "unknown")
            stats = families[family]
            stats["liquidity_tradable_tokens"] = int(safe_float(row.get("tradable_tokens")) or 0)
            stats["liquidity_tokens"] = int(safe_float(row.get("tokens")) or 0)
            stats["liquidity_max"] = safe_float(row.get("max_liquidity")) or 0.0
            stats["liquidity_min_spread"] = row.get("min_spread", "")

    quarantined = shadow_summary.get("quarantined_cohorts", []) if isinstance(shadow_summary, dict) else []
    if isinstance(quarantined, list):
        for row in quarantined:
            cohort = str((row or {}).get("signal_cohort") or "")
            for family in list(families.keys()):
                if family and family in cohort:
                    families[family]["quarantined_cohorts"] += 1

    leaderboard = []
    for family, stats in families.items():
        stake = float(stats["stake"])
        roi = float(stats["settlement_pnl"]) / stake if stake > 0 else 0.0
        win_rate = float(stats["wins"]) / float(stats["settled"]) if stats["settled"] else 0.0
        decision, score = _decision_for_family(family, stats, roi)
        leaderboard.append(
            {
                "family": family,
                "decision": decision,
                "score": round(score, 4),
                "settled": int(stats["settled"]),
                "wins": int(stats["wins"]),
                "win_rate": round(win_rate, 4),
                "stake_usdc": round(stake, 4),
                "settlement_pnl_usdc": round(float(stats["settlement_pnl"]), 4),
                "settlement_roi": round(roi, 4),
                "early_pnl_usdc": round(float(stats["early_pnl"]), 4),
                "early_minus_settlement_pnl": round(float(stats["early_pnl"]) - float(stats["settlement_pnl"]), 4),
                "live_candidates": int(stats["live_candidates"]),
                "scored_candidates": int(stats["scored_candidates"]),
                "near_approval_candidates": int(stats["near_approval_candidates"]),
                "best_edge": "" if stats["best_edge"] == -999.0 else round(float(stats["best_edge"]), 6),
                "max_live_liquidity": round(float(stats["max_live_liquidity"]), 4),
                "liquidity_tradable_tokens": int(stats["liquidity_tradable_tokens"]),
                "liquidity_tokens": int(stats["liquidity_tokens"]),
                "liquidity_max": round(float(stats["liquidity_max"]), 4),
                "liquidity_min_spread": stats["liquidity_min_spread"],
                "quarantined_cohorts": int(stats["quarantined_cohorts"]),
                "unresolved_unknown_rows": int(stats.get("unresolved_unknown_rows", 0)),
            }
        )
    leaderboard.sort(key=lambda row: float(row.get("score") or 0.0), reverse=True)
    near_approval.sort(key=lambda row: float(row.get("edge_lower_bound") or -999.0), reverse=True)

    features = read_csv_rows(training_root / "features_v2.csv")
    labels = read_csv_rows(training_root / "labels.csv")
    training_payload = {
        "status": "ok",
        "features_v2_rows": len(features),
        "labels_rows": len(labels),
        "labels_by_category_or_family": dict(Counter(str(row.get("category") or row.get("family") or "unknown") for row in labels).most_common(50)),
        "features_by_category_or_family": dict(Counter(str(row.get("category") or row.get("family") or "unknown") for row in features).most_common(50)),
    }

    db_summary: dict[str, Any] = {"database_path": str(cfg.database_path), "exists": Path(cfg.database_path).exists(), "tables": {}}
    if Path(cfg.database_path).exists():
        con = sqlite3.connect(cfg.database_path)
        try:
            for table, in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
                try:
                    db_summary["tables"][table] = {"rows": con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]}
                except Exception as exc:  # noqa: BLE001
                    db_summary["tables"][table] = {"error": f"{type(exc).__name__}: {exc}"}
        finally:
            con.close()

    payload = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "row_counts": {
            "predictions": len(predictions),
            "rejected_signals": len(rejected),
            "shadow_positions": len(positions),
            "settlement_audit_rows": len(settlement_rows),
            "features_v2": len(features),
            "labels": len(labels),
        },
        "top_10": leaderboard[:10],
        "candidate_for_focus": [row for row in leaderboard if row["decision"] == "candidate_for_focus"],
        "collect_settlement_evidence": [row for row in leaderboard if row["decision"] == "collect_settlement_evidence"][:10],
        "research_only_resolve_family": [row for row in leaderboard if row["decision"] == "research_only_resolve_family"],
        "avoid": [row for row in leaderboard if row["decision"].startswith("avoid")][:10],
        "failed_gate_counts": dict(gate_counts.most_common()),
    }
    write_csv(gov / "family_viability_leaderboard.csv", leaderboard)
    write_json(gov / "family_viability_leaderboard.json", payload)
    write_csv(gov / "settlement_hold_to_expiry_audit.csv", settlement_rows)
    write_csv(gov / "near_approval_candidates.csv", near_approval)
    write_json(gov / "family_viability_training_coverage.json", training_payload)
    write_json(gov / "sqlite_ledger_summary.json", db_summary)
    return payload


def main() -> int:
    payload = build_audit(sys.argv[1] if len(sys.argv) > 1 else "polymarket_predictive_config.example.yaml")
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
