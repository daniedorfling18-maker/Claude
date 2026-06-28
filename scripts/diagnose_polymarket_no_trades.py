#!/usr/bin/env python3
"""Diagnose why the local Polymarket paper bot has not produced trades.

This is read-only and paper-safe. It inspects runtime CSV/JSON artifacts and
explains whether the bottleneck is scanning, prediction generation, signal gates,
cohort promotion, liquidity, or broker execution.
"""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
GOV = ROOT / "outputs" / "polymarket_model_governance"
PRED = ROOT / "outputs" / "polymarket_predictions"
PORT = ROOT / "outputs" / "polymarket_portfolio"


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"status": "read_error", "path": str(path), "error": f"{type(exc).__name__}: {exc}"}


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    except Exception:
        return []


def num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "approved", "promoted"}


def query_key(value: str) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


def cohort_query_keys(cohort: str) -> set[str]:
    text = str(cohort or "").lower()
    keys: set[str] = set()
    if "btc" in text or "bitcoin" in text:
        keys.update({"btc updown", "bitcoin", "btc"})
    if "xrp" in text or "ripple" in text:
        keys.update({"xrp updown", "xrp", "ripple"})
    if "sol" in text or "solana" in text:
        keys.update({"solana updown", "solana", "sol"})
    if "eth" in text or "ethereum" in text:
        keys.update({"eth updown", "ethereum", "eth"})
    if "tennis" in text:
        keys.add("tennis")
    if "worldcup" in text or "world_cup" in text or "world cup" in text:
        keys.add("world cup")
    if text == "unknown" or text.startswith("near_miss_learning|unknown"):
        keys.add("unknown")
    return {query_key(key) for key in keys if key}


def top_counter(counter: Counter[str], limit: int = 12) -> list[dict[str, Any]]:
    return [{"count": count, "value": key} for key, count in counter.most_common(limit)]


def split_reasons(rows: list[dict[str, str]]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for row in rows:
        reason = str(row.get("rejection_reason") or "").strip()
        if not reason:
            counter["<blank>"] += 1
            continue
        counter[reason] += 1
    return counter


def field_reason_counts(rows: list[dict[str, str]]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for row in rows:
        reason = str(row.get("rejection_reason") or "").strip()
        for part in [item.strip() for item in reason.split(";") if item.strip()]:
            counter[part] += 1
    return counter


def compact_signal(row: dict[str, str]) -> dict[str, Any]:
    return {
        "market": row.get("market_slug") or row.get("question") or row.get("market_id"),
        "outcome": row.get("outcome"),
        "cohort": row.get("signal_cohort"),
        "cohort_status": row.get("cohort_promotion_status"),
        "cohort_source": row.get("cohort_evidence_source_cohort"),
        "edge": num(row.get("edge")),
        "edge_lower_bound": num(row.get("edge_lower_bound")),
        "liquidity": num(row.get("liquidity")),
        "spread": num(row.get("spread")),
        "alpha_trade_candidate": row.get("alpha_trade_candidate"),
        "shadow_trade_candidate": row.get("shadow_trade_candidate"),
        "microstructure_filter_pass": row.get("microstructure_filter_pass"),
        "bookmaker_cross_check_pass": row.get("bookmaker_cross_check_pass"),
        "validation_layer_pass": row.get("validation_layer_pass"),
        "crypto_model_status": row.get("crypto_model_status"),
        "reason": row.get("rejection_reason"),
    }


def family_liquidity(liquidity: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = liquidity.get("family_summary", []) if isinstance(liquidity, dict) else []
    out: dict[str, dict[str, Any]] = {}
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                out[str(row.get("family") or "unknown")] = row
    return out


def main() -> int:
    agent = read_json(GOV / "agent_status.json", default={}) or {}
    heartbeat = read_json(GOV / "live_paper_loop_heartbeat.json", default={}) or {}
    local_heartbeat = read_json(GOV / "local_live_loop_heartbeat.json", default={}) or {}
    liquidity = read_json(GOV / "liquidity_discovery_summary.json", default={}) or {}
    cohort_payload = read_json(GOV / "signal_cohort_pnl.json", default={}) or {}
    goal = read_json(GOV / "paper_profit_goal_plan.json", default={}) or {}
    broker_summary = read_json(PORT / "paper_trading_summary.json", default={}) or {}

    predictions = read_csv(PRED / "predictions.csv")
    approved = read_csv(PRED / "trade_signals.csv")
    rejected = read_csv(PRED / "rejected_signals.csv")
    near_miss = read_csv(PRED / "near_miss_learning_candidates.csv")
    alpha_scores = read_csv(PRED / "mispricing_alpha_scores.csv")

    scan_plan = (((heartbeat or {}).get("scan") or {}).get("scan_plan") or {}) if isinstance(heartbeat, dict) else {}
    research_focus = (agent.get("research_focus") or {}) if isinstance(agent, dict) else {}
    focus_queries = [str(item) for item in research_focus.get("collection_queries", [])] if isinstance(research_focus, dict) else []
    selected_queries = [str(item) for item in scan_plan.get("selected_queries", [])] if isinstance(scan_plan, dict) else []
    ordered_queries = [str(item) for item in scan_plan.get("ordered_queries", [])] if isinstance(scan_plan, dict) else []

    cohorts = cohort_payload.get("cohorts", []) if isinstance(cohort_payload, dict) else []
    if not isinstance(cohorts, list):
        cohorts = []
    probationary = [row for row in cohorts if isinstance(row, dict) and truthy(row.get("probationary"))]
    promoted = [row for row in cohorts if isinstance(row, dict) and truthy(row.get("promoted"))]

    prediction_cohorts = Counter(str(row.get("signal_cohort") or "") for row in predictions)
    rejected_cohorts = Counter(str(row.get("signal_cohort") or "") for row in rejected)
    probationary_match_rows: list[dict[str, Any]] = []
    probationary_without_matches: list[dict[str, Any]] = []
    for row in probationary:
        cohort = str(row.get("signal_cohort") or row.get("cohort") or "")
        base = cohort.split("|", 1)[1] if cohort.startswith("near_miss_learning|") and "|" in cohort else cohort
        direct = prediction_cohorts.get(cohort, 0)
        base_count = prediction_cohorts.get(base, 0)
        if direct or base_count:
            probationary_match_rows.append({"cohort": cohort, "direct_prediction_rows": direct, "base_prediction_rows": base_count})
        else:
            probationary_without_matches.append({"cohort": cohort, "base_cohort": base, "reason": "no current predictions use this cohort/base cohort"})

    approved_by_status = Counter(str(row.get("cohort_promotion_status") or "") for row in approved)
    rejected_by_status = Counter(str(row.get("cohort_promotion_status") or "") for row in rejected)
    rejected_probationary = [row for row in rejected if str(row.get("cohort_promotion_status") or "").lower() == "probationary"]

    families = family_liquidity(liquidity if isinstance(liquidity, dict) else {})
    scan_alignment = {
        "focus_queries": focus_queries,
        "selected_queries": selected_queries,
        "ordered_queries_first_10": ordered_queries[:10],
        "selected_matches_focus": bool({query_key(q) for q in focus_queries} & {query_key(q) for q in selected_queries}),
        "note": "If this is false for many cycles, scan selection is not following research focus.",
    }

    top_rejected = sorted(
        rejected,
        key=lambda row: (
            num(row.get("edge")),
            num(row.get("edge_lower_bound")),
            num(row.get("liquidity")),
        ),
        reverse=True,
    )[:12]

    root_causes: list[str] = []
    if not approved and rejected:
        root_causes.append("No paper trades because trade_signals.csv has 0 approved rows; the broker never receives a buy signal.")
    if probationary_without_matches:
        root_causes.append("At least one probationary cohort has no matching current predictions, so it cannot create a paper trade.")
    if rejected and any("liquidity" in str(row.get("rejection_reason") or "") for row in rejected):
        root_causes.append("Current candidates are repeatedly failing liquidity/spread gates.")
    if rejected and any("alpha lower-bound edge" in str(row.get("rejection_reason") or "") for row in rejected):
        root_causes.append("Current candidates are repeatedly failing lower-bound edge / alpha candidate gates.")
    if rejected and any("cohort_quarantined" in str(row.get("rejection_reason") or "") for row in rejected):
        root_causes.append("Some candidates are still in quarantined cohorts from negative forward evidence.")
    if focus_queries and selected_queries and not scan_alignment["selected_matches_focus"]:
        root_causes.append("The live scan selected query does not match the research-focus collection query in this heartbeat.")
    if not root_causes:
        root_causes.append("No single blocker detected; inspect top rejected rows and broker state.")

    report = {
        "status": "ok",
        "headline": root_causes[0],
        "root_causes": root_causes,
        "runtime": {
            "local_loop_status": local_heartbeat.get("status") if isinstance(local_heartbeat, dict) else "unknown",
            "live_paper_status": heartbeat.get("status") if isinstance(heartbeat, dict) else "unknown",
            "agent_status": agent.get("status") if isinstance(agent, dict) else "unknown",
            "research_focus_status": research_focus.get("status") if isinstance(research_focus, dict) else "unknown",
        },
        "scan_alignment": scan_alignment,
        "row_counts": {
            "predictions": len(predictions),
            "alpha_scores": len(alpha_scores),
            "approved_trade_signals": len(approved),
            "rejected_trade_signals": len(rejected),
            "near_miss_candidates": len(near_miss),
        },
        "broker": {
            "status": broker_summary.get("status") if isinstance(broker_summary, dict) else "unknown",
            "cash": broker_summary.get("cash") if isinstance(broker_summary, dict) else "",
            "equity": broker_summary.get("equity") if isinstance(broker_summary, dict) else "",
            "orders_filled": broker_summary.get("orders_filled") if isinstance(broker_summary, dict) else "",
            "signals_processed": broker_summary.get("signals_processed") if isinstance(broker_summary, dict) else "",
            "entry_pause_reason": broker_summary.get("entry_pause_reason") if isinstance(broker_summary, dict) else "",
        },
        "cohorts": {
            "promoted": [row.get("signal_cohort") for row in promoted[:10]],
            "probationary": [row.get("signal_cohort") for row in probationary[:10]],
            "probationary_matching_current_predictions": probationary_match_rows,
            "probationary_without_current_predictions": probationary_without_matches,
        },
        "signal_status_counts": {
            "approved_by_cohort_status": dict(approved_by_status),
            "rejected_by_cohort_status": dict(rejected_by_status),
        },
        "rejection_reason_counts": top_counter(split_reasons(rejected)),
        "rejection_gate_counts": top_counter(field_reason_counts(rejected), limit=20),
        "top_rejected_candidates": [compact_signal(row) for row in top_rejected],
        "rejected_probationary_candidates": [compact_signal(row) for row in rejected_probationary[:10]],
        "liquidity_family_summary": {
            key: {
                "tradable_tokens": value.get("tradable_tokens"),
                "tokens": value.get("tokens"),
                "max_liquidity": value.get("max_liquidity"),
                "min_spread": value.get("min_spread"),
            }
            for key, value in families.items()
        },
        "goal_plan": {
            "status": goal.get("status") if isinstance(goal, dict) else "unknown",
            "main_gap": goal.get("main_gap") if isinstance(goal, dict) else "",
            "recommended_action": goal.get("recommended_action") if isinstance(goal, dict) else "",
            "required_daily_from_here_usdc": goal.get("required_daily_from_here_usdc") if isinstance(goal, dict) else "",
        },
        "safe_next_steps": [
            "Do not lower live/paper gates. First confirm whether any probationary candidates are being generated and why they are rejected.",
            "If scan_alignment.selected_matches_focus is false across cycles, force POLYMARKET_QUERIES to the focus queries and rerun discovery.",
            "If probationary_without_current_predictions is non-empty, collect more shadow evidence and resolve the unknown near-miss family before expecting paper fills.",
            "If rejected_probationary_candidates is non-empty, inspect their rejection reasons; that is the closest path to a small capped paper probe.",
        ],
    }
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
