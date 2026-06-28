from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .config import EngineConfig, load_config
from .goal_planner import build_goal_plan
from .promotion_review import build_promotion_review
from .utils import boolish, now_utc, read_csv_rows, read_json, safe_float, write_csv, write_json


DECISION_PAPER_TRADE_READY = "PAPER_TRADE_READY"
DECISION_PROBATIONARY_PROBE_READY = "PROBATIONARY_PROBE_READY"
DECISION_WAIT_ACTIVE_WINDOW = "WAIT_ACTIVE_WINDOW"
DECISION_COLLECT_MORE_EVIDENCE = "COLLECT_MORE_EVIDENCE"
DECISION_FIX_INPUTS = "FIX_INPUTS"
DECISION_DO_NOT_TRADE_NEGATIVE_EVIDENCE = "DO_NOT_TRADE_NEGATIVE_EVIDENCE"

TARGET_WAIT_ACTIVE_WINDOW = "WAIT_ACTIVE_WINDOW"
TARGET_SHADOW_LABEL_GATE = "SHADOW_LABEL_GATE"
TARGET_SHADOW_EDGE_WATCH = "SHADOW_EDGE_WATCH"
TARGET_IGNORE_LOW_EDGE = "IGNORE_LOW_EDGE"
TARGET_IGNORE_LIQUIDITY = "IGNORE_LIQUIDITY"
TARGET_IGNORE_BOOKMAKER = "IGNORE_BOOKMAKER"
TARGET_IGNORE_NEGATIVE_EDGE = "IGNORE_NEGATIVE_EDGE"


_ACTION_PRIORITY = {
    DECISION_PAPER_TRADE_READY: 100,
    DECISION_PROBATIONARY_PROBE_READY: 90,
    DECISION_WAIT_ACTIVE_WINDOW: 70,
    DECISION_COLLECT_MORE_EVIDENCE: 60,
    DECISION_FIX_INPUTS: 50,
    DECISION_DO_NOT_TRADE_NEGATIVE_EVIDENCE: 10,
}

_TARGET_PRIORITY = {
    TARGET_WAIT_ACTIVE_WINDOW: 95,
    TARGET_SHADOW_LABEL_GATE: 85,
    TARGET_SHADOW_EDGE_WATCH: 65,
    TARGET_IGNORE_LIQUIDITY: 35,
    TARGET_IGNORE_BOOKMAKER: 30,
    TARGET_IGNORE_LOW_EDGE: 20,
    TARGET_IGNORE_NEGATIVE_EDGE: 5,
}


def _num(value: Any, default: float = 0.0) -> float:
    parsed = safe_float(value)
    return default if parsed is None else float(parsed)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return boolish(value)


def _repo_root(cfg: EngineConfig) -> Path:
    root = cfg.path.parent
    return root if str(root) not in {"", "."} else Path(".")


def _status_file_candidates(cfg: EngineConfig) -> list[Path]:
    rel = Path("work") / "shadow_research_cycle_latest_status.json"
    candidates = [
        _repo_root(cfg) / rel,
        Path.cwd() / rel,
        cfg.output_root.parent / rel,
    ]
    out: list[Path] = []
    for path in candidates:
        if path not in out:
            out.append(path)
    return out


def _read_json_loose(path: Path) -> Any:
    """Read a small JSON file that may have been written by PowerShell.

    The repo's normal ``read_json`` uses UTF-8. Some local PowerShell scripts can
    write status files with UTF-16/Windows encodings, so the sprint report should
    not silently lose the status snapshot just because of encoding.
    """
    if not path.exists() or path.stat().st_size == 0:
        return None
    for encoding in ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "cp1252"):
        try:
            with path.open("r", encoding=encoding) as handle:
                return json.load(handle)
        except Exception:
            continue
    return None


def _read_shadow_status(cfg: EngineConfig) -> dict[str, Any]:
    for path in _status_file_candidates(cfg):
        payload = _read_json_loose(path)
        if isinstance(payload, dict):
            return {**payload, "status_file": str(path)}
    return {"status": "missing", "status_file_candidates": [str(path) for path in _status_file_candidates(cfg)]}


def _group_counts(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    for row in rows:
        key = str(row.get(field) or "").strip() or "missing"
        counts[key] += 1
    return [
        {field: key, "count": count}
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _cohort_sets(review: dict[str, Any]) -> tuple[set[str], set[str], list[dict[str, Any]]]:
    rows = [row for row in review.get("review", []) if isinstance(row, dict)] if isinstance(review, dict) else []
    promoted = {
        str(row.get("cohort") or "")
        for row in rows
        if row.get("promoted") is True or str(row.get("status") or "").lower() == "promoted"
    }
    probationary = {
        str(row.get("cohort") or "")
        for row in rows
        if row.get("probationary") is True or str(row.get("status") or "").lower() == "probationary"
    }
    return promoted, probationary, rows


def _candidate_cohort_keys(row: dict[str, Any], near_miss_prefix: str) -> set[str]:
    cohort = str(row.get("signal_cohort") or "").strip()
    keys = {cohort} if cohort else set()
    if _truthy(row.get("near_miss_learning_candidate")) and cohort:
        keys.add(f"{near_miss_prefix}|{cohort}")
    return keys


def _is_current_candidate(row: dict[str, Any]) -> bool:
    if _truthy(row.get("alpha_trade_candidate")):
        return True
    if _truthy(row.get("shadow_trade_candidate")):
        return True
    if _truthy(row.get("near_miss_learning_candidate")):
        return True
    edge_lower_bound = safe_float(row.get("edge_lower_bound"))
    return bool(edge_lower_bound is not None and edge_lower_bound > 0 and _truthy(row.get("validation_layer_pass")))


def _current_candidate_rows(
    alpha_scores: list[dict[str, Any]],
    *,
    promoted: set[str],
    probationary: set[str],
    near_miss_prefix: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    promoted_rows: list[dict[str, Any]] = []
    probationary_rows: list[dict[str, Any]] = []
    for row in alpha_scores:
        if not _is_current_candidate(row):
            continue
        keys = _candidate_cohort_keys(row, near_miss_prefix)
        payload = {
            "market_slug": row.get("market_slug", ""),
            "question": row.get("question", ""),
            "outcome": row.get("outcome", ""),
            "category": row.get("category", ""),
            "signal_cohort": row.get("signal_cohort", ""),
            "matched_cohort": "",
            "alpha_raw_edge": row.get("alpha_raw_edge", ""),
            "edge_lower_bound": row.get("edge_lower_bound", ""),
            "alpha_score": row.get("alpha_score", ""),
            "validation_layer_pass": row.get("validation_layer_pass", ""),
            "validation_layer_reason": row.get("validation_layer_reason", ""),
            "shadow_trade_candidate": row.get("shadow_trade_candidate", ""),
            "shadow_candidate_reason": row.get("shadow_candidate_reason", ""),
            "near_miss_learning_candidate": row.get("near_miss_learning_candidate", ""),
            "near_miss_learning_reason": row.get("near_miss_learning_reason", ""),
        }
        matched_promoted = sorted(keys & promoted)
        matched_probationary = sorted(keys & probationary)
        if matched_promoted:
            promoted_rows.append({**payload, "matched_cohort": matched_promoted[0]})
        if matched_probationary:
            probationary_rows.append({**payload, "matched_cohort": matched_probationary[0]})
    return promoted_rows, probationary_rows


def _input_warnings(cfg: EngineConfig) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    sharp_fetch = read_json(cfg.governance_root / "sharp_odds_fetch_summary.json", default={}) or {}
    sharp_anchor = read_json(cfg.governance_root / "sharp_anchor_summary.json", default={}) or {}
    crypto_summary = read_json(cfg.governance_root / "crypto_fundamental_summary.json", default={}) or {}

    if isinstance(sharp_fetch, dict) and str(sharp_fetch.get("status") or "") in {"missing_api_key", "error"}:
        warnings.append({"source": "sharp_odds_fetch", "status": sharp_fetch.get("status"), "reason": sharp_fetch.get("note") or sharp_fetch.get("error") or "sharp odds fetch is not usable"})
    if isinstance(sharp_anchor, dict) and int(_num(sharp_anchor.get("fundamental_rows"), 0.0)) == 0:
        warnings.append({"source": "sharp_anchor", "status": sharp_anchor.get("status", "empty"), "reason": "no sharp fundamental rows are available"})
    if isinstance(crypto_summary, dict) and str(crypto_summary.get("status") or "") in {"no_targets", "no_input"}:
        warnings.append({"source": "crypto_fundamental", "status": crypto_summary.get("status"), "reason": "no crypto fundamental targets are configured"})
    return warnings


def _target_thresholds(cfg: EngineConfig) -> dict[str, float]:
    alpha = cfg.raw.get("mispricing_alpha", {}) or {}
    near = alpha.get("near_miss_learning", {}) or {}
    return {
        "min_liquidity": float(alpha.get("min_liquidity_for_alpha_trade", near.get("min_liquidity", 100.0)) or 100.0),
        "max_spread": float(alpha.get("max_spread_for_alpha_trade", near.get("max_spread", 0.04)) or 0.04),
        "edge_watch_floor": float(near.get("min_edge_lower_bound", 0.0) or 0.0),
    }


def _recommended_query(row: dict[str, Any]) -> str:
    text = " ".join(str(row.get(key) or "") for key in ("market_slug", "question", "category", "signal_cohort")).lower()
    if "xrp" in text:
        return "xrp updown" if "updown" in text else "xrp"
    if "solana" in text or "sol_" in text or "crypto_sol" in text:
        return "solana updown"
    if "btc" in text or "bitcoin" in text:
        return "btc updown" if "updown" in text else "bitcoin"
    if "worldcup" in text or "world cup" in text:
        return "world cup"
    if "sports_other" in text or "round-of-16" in text:
        return "sports"
    if "tennis" in text:
        return "tennis"
    return "top_active"


def _target_next_command(action: str) -> str:
    if action == TARGET_WAIT_ACTIVE_WINDOW:
        return "polymarket-engine paper-cycle --config polymarket_predictive_config.example.yaml --paper-source websocket"
    if action == TARGET_SHADOW_LABEL_GATE:
        return "polymarket-engine collect-snapshot-labels --config polymarket_predictive_config.example.yaml"
    if action == TARGET_IGNORE_BOOKMAKER:
        return "polymarket-engine refresh-sharp-anchor --config polymarket_predictive_config.example.yaml"
    return "polymarket-engine profit-sprint --config polymarket_predictive_config.example.yaml"


def _classify_rejected_row(cfg: EngineConfig, row: dict[str, Any]) -> dict[str, Any]:
    thresholds = _target_thresholds(cfg)
    reason = str(row.get("rejection_reason") or "")
    reason_l = reason.lower()
    edge = safe_float(row.get("edge_lower_bound"))
    if edge is None:
        edge = safe_float(row.get("edge"))
    liquidity = _num(row.get("liquidity"), 0.0)
    spread = safe_float(row.get("spread"))
    spread_value = spread if spread is not None else 999.0
    positive_edge = edge is not None and edge > thresholds["edge_watch_floor"]
    liquid = liquidity >= thresholds["min_liquidity"]
    tight = spread_value <= thresholds["max_spread"]

    if "before_model_window" in reason_l and positive_edge and liquid and tight:
        action = TARGET_WAIT_ACTIVE_WINDOW
        target_reason = "Positive edge, liquid/tight book, but fast crypto model says this is before the valid trading window."
    elif "same-category validation gate failed" in reason_l and positive_edge and liquid and tight:
        action = TARGET_SHADOW_LABEL_GATE
        target_reason = "Positive edge but category label gate is not satisfied; collect shadow/label evidence only."
    elif "bookmaker_fundamental_cross_check_failed" in reason_l:
        action = TARGET_IGNORE_BOOKMAKER
        target_reason = "Bookmaker/fundamental cross-check failed; do not trade until independent anchor agrees."
    elif "liquidity_below" in reason_l or "spread_above" in reason_l or not liquid or not tight:
        action = TARGET_IGNORE_LIQUIDITY
        target_reason = "Liquidity/spread is not good enough for the current edge; wait for a better book."
    elif edge is None or edge <= 0:
        action = TARGET_IGNORE_NEGATIVE_EDGE
        target_reason = "No positive lower-bound edge after costs and haircuts."
    elif positive_edge:
        action = TARGET_SHADOW_EDGE_WATCH
        target_reason = "Positive lower-bound edge exists but not enough to pass paper-entry gates; keep in watch/shadow mode."
    else:
        action = TARGET_IGNORE_LOW_EDGE
        target_reason = "Lower-bound edge is below the configured trading threshold."

    # A mild score keeps the queue stable inside each target class without making
    # any row tradeable. The action class remains the real decision.
    score = (
        _TARGET_PRIORITY[action]
        + max(0.0, edge or 0.0) * 100.0
        + min(liquidity, 5000.0) / 1000.0
        - max(0.0, spread_value) * 10.0
    )
    return {
        "target_action": action,
        "priority": _TARGET_PRIORITY[action],
        "target_score": round(score, 6),
        "market_slug": row.get("market_slug", ""),
        "question": row.get("question", ""),
        "outcome": row.get("outcome", ""),
        "category": row.get("category", ""),
        "signal_cohort": row.get("signal_cohort", ""),
        "edge_lower_bound": "" if edge is None else edge,
        "liquidity": liquidity,
        "spread": "" if spread is None else spread,
        "rejection_reason": reason,
        "recommended_query": _recommended_query(row),
        "reason": target_reason,
        "next_command": _target_next_command(action),
    }


def _build_target_queue(cfg: EngineConfig, rejected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [_classify_rejected_row(cfg, row) for row in rejected]
    rows.sort(
        key=lambda row: (
            _num(row.get("priority")),
            _num(row.get("target_score")),
            str(row.get("market_slug") or ""),
            str(row.get("outcome") or ""),
        ),
        reverse=True,
    )
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return rows


def _top_rejection_reason(rejection_counts: list[dict[str, Any]]) -> str:
    if not rejection_counts:
        return ""
    return str(rejection_counts[0].get("rejection_reason") or "")


def _choose_decision(
    *,
    approved: list[dict[str, Any]],
    promoted_current: list[dict[str, Any]],
    probationary_current: list[dict[str, Any]],
    target_rows: list[dict[str, Any]],
    top_rejection: str,
    promotion_rows: list[dict[str, Any]],
    shadow_status: dict[str, Any],
    input_warnings: list[dict[str, Any]],
) -> tuple[str, str]:
    if approved:
        return DECISION_PAPER_TRADE_READY, "Approved paper trade signals exist; broker should process them under existing risk controls."
    if promoted_current:
        return DECISION_PAPER_TRADE_READY, "A current candidate matches a promoted cohort; keep sizing/risk gates intact."
    if probationary_current:
        return DECISION_PROBATIONARY_PROBE_READY, "A current candidate matches a probationary cohort; keep the probationary stake cap small."
    if any(row.get("target_action") == TARGET_WAIT_ACTIVE_WINDOW for row in target_rows):
        return DECISION_WAIT_ACTIVE_WINDOW, "Best current targets are liquid positive-edge crypto Up/Down rows that must be rescored inside the model window."
    if "before_model_window" in top_rejection:
        return DECISION_WAIT_ACTIVE_WINDOW, "Current crypto Up/Down rows are liquid but outside the live model window."
    actionable = [row for row in promotion_rows if str(row.get("status") or "") in {"probationary", "eligible_for_probationary_review", "near_full_promotion", "near_probationary", "collecting_evidence"}]
    if actionable:
        return DECISION_COLLECT_MORE_EVIDENCE, "Positive or near-positive evidence exists, but no current candidate passes paper-entry gates."
    if input_warnings:
        return DECISION_FIX_INPUTS, "No actionable cohort is available and at least one optional external anchor input is empty or unavailable."
    shadow_roi = _num(shadow_status.get("shadow_roi"))
    shadow_pnl = _num(shadow_status.get("shadow_total_pnl_usdc"))
    if shadow_roi < 0 or shadow_pnl < 0:
        return DECISION_DO_NOT_TRADE_NEGATIVE_EVIDENCE, "Aggregate shadow evidence is negative and no current promoted/probationary candidate is available."
    return DECISION_COLLECT_MORE_EVIDENCE, "No paper-ready trade exists; continue collecting forward evidence."


def _action_rows(
    decision: str,
    decision_reason: str,
    *,
    approved: list[dict[str, Any]],
    promoted_current: list[dict[str, Any]],
    probationary_current: list[dict[str, Any]],
    promotion_rows: list[dict[str, Any]],
    target_rows: list[dict[str, Any]],
    rejection_counts: list[dict[str, Any]],
    input_warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        {
            "rank": 1,
            "action": decision,
            "priority": _ACTION_PRIORITY.get(decision, 0),
            "cohort": "",
            "market_slug": "",
            "reason": decision_reason,
            "next_command": "polymarket-engine paper-trade --config polymarket_predictive_config.example.yaml" if decision == DECISION_PAPER_TRADE_READY else "polymarket-engine promotion-review --config polymarket_predictive_config.example.yaml",
        }
    ]
    rank = 2
    for source, action, cohort_rows in (
        ("approved", DECISION_PAPER_TRADE_READY, approved[:5]),
        ("promoted_current", DECISION_PAPER_TRADE_READY, promoted_current[:5]),
        ("probationary_current", DECISION_PROBATIONARY_PROBE_READY, probationary_current[:5]),
    ):
        for row in cohort_rows:
            rows.append(
                {
                    "rank": rank,
                    "action": action,
                    "priority": _ACTION_PRIORITY.get(action, 0),
                    "source": source,
                    "cohort": row.get("matched_cohort") or row.get("signal_cohort", ""),
                    "market_slug": row.get("market_slug", ""),
                    "reason": row.get("validation_layer_reason", "") or row.get("shadow_candidate_reason", "") or "current candidate",
                    "next_command": "polymarket-engine paper-cycle --config polymarket_predictive_config.example.yaml --paper-source websocket",
                }
            )
            rank += 1
    for row in target_rows[:8]:
        rows.append(
            {
                "rank": rank,
                "action": row.get("target_action", ""),
                "priority": row.get("priority", 0),
                "source": "profit_sprint_targets",
                "cohort": row.get("signal_cohort", ""),
                "market_slug": row.get("market_slug", ""),
                "reason": row.get("reason", ""),
                "next_command": row.get("next_command", ""),
            }
        )
        rank += 1
    for row in promotion_rows[:8]:
        rows.append(
            {
                "rank": rank,
                "action": DECISION_COLLECT_MORE_EVIDENCE,
                "priority": _ACTION_PRIORITY[DECISION_COLLECT_MORE_EVIDENCE],
                "source": "promotion_review",
                "cohort": row.get("cohort", ""),
                "market_slug": "",
                "reason": row.get("next_action", ""),
                "next_command": "polymarket-engine promotion-review --config polymarket_predictive_config.example.yaml",
            }
        )
        rank += 1
    for row in rejection_counts[:5]:
        rows.append(
            {
                "rank": rank,
                "action": "DIAGNOSE_REJECTION_BUCKET",
                "priority": 40,
                "source": "rejected_signals",
                "cohort": "",
                "market_slug": "",
                "reason": f"{row.get('count', 0)} rejected: {row.get('rejection_reason', '')}",
                "next_command": "Import-Csv .\\outputs\\polymarket_predictions\\rejected_signals.csv | Group-Object rejection_reason",
            }
        )
        rank += 1
    for warning in input_warnings[:5]:
        rows.append(
            {
                "rank": rank,
                "action": DECISION_FIX_INPUTS,
                "priority": _ACTION_PRIORITY[DECISION_FIX_INPUTS],
                "source": warning.get("source", ""),
                "cohort": "",
                "market_slug": "",
                "reason": warning.get("reason", ""),
                "next_command": "polymarket-engine refresh-sharp-anchor --config polymarket_predictive_config.example.yaml",
            }
        )
        rank += 1
    return rows


def build_profit_sprint(cfg: EngineConfig) -> dict[str, Any]:
    """Build the $100/month paper-profit sprint report.

    This is deliberately read/report only. It does not create signals, paper-fill
    orders, change thresholds, invoke live trading, or relax promotion gates.
    """
    goal = build_goal_plan(cfg)
    promotion = build_promotion_review(cfg)
    approved = read_csv_rows(cfg.output_root / "polymarket_predictions" / "trade_signals.csv")
    rejected = read_csv_rows(cfg.output_root / "polymarket_predictions" / "rejected_signals.csv")
    alpha_scores = read_csv_rows(cfg.output_root / "polymarket_predictions" / "mispricing_alpha_scores.csv")
    shadow_status = _read_shadow_status(cfg)
    tracker = read_json(cfg.governance_root / "paper_profit_target_tracker.json", default={}) or {}
    input_warnings = _input_warnings(cfg)

    promoted, probationary, promotion_rows = _cohort_sets(promotion)
    near_miss_prefix = str(cfg.raw.get("cohort_promotion", {}).get("near_miss_cohort_prefix", "near_miss_learning"))
    promoted_current, probationary_current = _current_candidate_rows(
        alpha_scores,
        promoted=promoted,
        probationary=probationary,
        near_miss_prefix=near_miss_prefix,
    )
    target_rows = _build_target_queue(cfg, rejected)
    rejection_counts = _group_counts(rejected, "rejection_reason")
    target_counts = _group_counts(target_rows, "target_action")
    top_rejection = _top_rejection_reason(rejection_counts)
    decision, decision_reason = _choose_decision(
        approved=approved,
        promoted_current=promoted_current,
        probationary_current=probationary_current,
        target_rows=target_rows,
        top_rejection=top_rejection,
        promotion_rows=promotion_rows,
        shadow_status=shadow_status,
        input_warnings=input_warnings,
    )
    actions = _action_rows(
        decision,
        decision_reason,
        approved=approved,
        promoted_current=promoted_current,
        probationary_current=probationary_current,
        promotion_rows=promotion_rows,
        target_rows=target_rows,
        rejection_counts=rejection_counts,
        input_warnings=input_warnings,
    )

    out_dir = cfg.governance_root
    action_path = cfg.output_root / "polymarket_predictions" / "profit_sprint_actions.csv"
    target_path = out_dir / "profit_sprint_targets.csv"
    write_csv(action_path, actions)
    write_csv(target_path, target_rows)

    payload = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "live_trading_invoked": False,
        "paper_trading_invoked": False,
        "decision": decision,
        "decision_reason": decision_reason,
        "target_monthly_profit_usdc": goal.get("target_monthly_profit_usdc"),
        "target_daily_profit_usdc": goal.get("target_daily_profit_usdc"),
        "actual_pnl_since_baseline_usdc": goal.get("actual_pnl_since_baseline_usdc"),
        "monthly_run_rate_usdc": tracker.get("monthly_run_rate_usdc") if isinstance(tracker, dict) else None,
        "required_daily_from_here_usdc": goal.get("required_daily_from_here_usdc"),
        "approved_signals": len(approved),
        "rejected_signals": len(rejected),
        "shadow_status": {
            "status": shadow_status.get("status"),
            "status_file": shadow_status.get("status_file", ""),
            "paper_allowed": shadow_status.get("paper_allowed"),
            "paper_reason": shadow_status.get("paper_reason", ""),
            "shadow_total_pnl_usdc": shadow_status.get("shadow_total_pnl_usdc"),
            "shadow_roi": shadow_status.get("shadow_roi"),
            "liquidity_tradable_tokens": shadow_status.get("liquidity_tradable_tokens"),
            "liquidity_fast_feedback_tradable_tokens": shadow_status.get("liquidity_fast_feedback_tradable_tokens"),
            "status_file_candidates": shadow_status.get("status_file_candidates", []),
        },
        "promoted_cohorts": sorted(promoted),
        "probationary_cohorts": sorted(probationary),
        "current_promoted_candidates": promoted_current[:10],
        "current_probationary_candidates": probationary_current[:10],
        "top_rejection_reasons": rejection_counts[:10],
        "target_summary": target_counts,
        "top_targets": target_rows[:10],
        "top_actionable": promotion.get("top_actionable", [])[:10] if isinstance(promotion, dict) else [],
        "input_warnings": input_warnings,
        "actions_file": str(action_path),
        "targets_file": str(target_path),
        "plan_file": str(out_dir / "profit_sprint_plan.json"),
        "notes": [
            "Read/report only: no orders, no live trading, no threshold changes.",
            "Use PAPER_TRADE_READY or PROBATIONARY_PROBE_READY only as a cue to run the existing paper broker path under current risk gates.",
            "WAIT_ACTIVE_WINDOW targets are not trades; they are queued for rescoring when the model window opens.",
        ],
    }
    write_json(out_dir / "profit_sprint_plan.json", payload)
    return payload


def main(config_path: str) -> dict[str, Any]:
    return build_profit_sprint(load_config(config_path))
