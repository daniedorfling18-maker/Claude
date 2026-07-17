from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .config import EngineConfig, load_config
from .utils import now_utc, read_csv_rows, read_json, safe_float, write_csv, write_json, write_text_atomic

OUTPUT_DIRNAME = "polymarket_strategy_v2"
REPORT_JSON = "opportunity_audit_report.json"
REPORT_MD = "opportunity_audit_report.md"
COHORTS_CSV = "opportunity_audit_cohorts.csv"
ALPHA_CSV = "opportunity_audit_alpha_rows.csv"


def _num(value: Any, default: float = 0.0) -> float:
    parsed = safe_float(value)
    return default if parsed is None else float(parsed)


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _read_csv(path: Path) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in read_csv_rows(path)]
    except Exception:
        return []


def _path_inventory(paths: dict[str, Path]) -> list[dict[str, Any]]:
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


def _missing_gates(row: dict[str, Any]) -> list[str]:
    gates = row.get("missing_full_gates")
    if isinstance(gates, list):
        return [str(gate) for gate in gates]
    text = str(gates or "").strip()
    if not text:
        return []
    return [part.strip() for part in text.replace("{", "").replace("}", "").split(",") if part.strip()]


def _cohort_action(row: dict[str, Any]) -> tuple[str, str, float]:
    cohort = str(row.get("cohort") or row.get("signal_cohort") or "unknown")
    metadata_blocker = str(row.get("metadata_blocker") or "").strip()
    promoted = bool(row.get("promoted") is True or str(row.get("promoted")).lower() == "true")
    probationary = bool(row.get("probationary") is True or str(row.get("probationary")).lower() == "true")
    fills = _num(row.get("buy_fills"))
    settled = _num(row.get("settled_fills"))
    pnl = _num(row.get("total_pnl_usdc"))
    roi = _num(row.get("roi"))
    monthly = _num(row.get("monthly_run_rate_usdc"))
    missing = _missing_gates(row)

    score = 0.0
    score += max(-3.0, min(3.0, pnl / 10.0))
    score += max(-2.0, min(2.0, roi * 2.0))
    score += max(-2.0, min(2.0, monthly / 100.0))
    score += min(1.0, settled / 10.0)
    score -= 0.35 * len(missing)

    if metadata_blocker:
        return "blocked_metadata", "Classify/resolve metadata first; do not promote or probe.", score - 10.0
    if promoted or probationary:
        return "already_promoted_or_probationary", "Review risk caps before any paper expansion.", score + 5.0
    if settled >= 3 and pnl < 0:
        return "kill_or_quarantine", "Enough settled evidence exists and P&L is negative; stop spending research slots here.", score - 5.0
    if fills >= 5 and settled >= 3 and pnl > 0 and roi >= 0.03 and monthly >= 20:
        return "promotion_review_candidate", "Meets mechanical evidence shape; review family/anchor quality before paper.", score + 5.0
    if fills >= 5 and settled >= 3 and pnl > 0 and "roi" in missing:
        return "near_but_roi_short", "Keep collecting evidence, but do not loosen ROI gate.", score + 1.0
    if pnl > 0 and (fills < 5 or settled < 3):
        return "thin_positive", "Potential signal but too thin; collect more only if family has a clean anchor path.", score + 0.5
    if fills >= 5 and settled >= 3 and pnl == 0:
        return "stagnant", "Evidence count exists but no P&L; deprioritise unless a new anchor explains edge.", score - 1.0
    if pnl < 0:
        return "negative_thin_or_open", "Do not promote; collect only if this family is strategically important.", score - 2.0
    return "collect_more_evidence", "Insufficient evidence; keep shadow-only and require an independent anchor.", score


def _cohort_rows(promotion_review: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _list(promotion_review.get("review")):
        if not isinstance(row, dict):
            continue
        action_status, action, opportunity_score = _cohort_action(row)
        rows.append(
            {
                "cohort": row.get("cohort") or row.get("signal_cohort") or "unknown",
                "status": row.get("status", ""),
                "action_status": action_status,
                "recommended_action": action,
                "opportunity_score": opportunity_score,
                "promoted": row.get("promoted", False),
                "probationary": row.get("probationary", False),
                "metadata_blocker": row.get("metadata_blocker", ""),
                "buy_fills": _num(row.get("buy_fills")),
                "settled_fills": _num(row.get("settled_fills")),
                "total_pnl_usdc": _num(row.get("total_pnl_usdc")),
                "roi": _num(row.get("roi")),
                "monthly_run_rate_usdc": _num(row.get("monthly_run_rate_usdc")),
                "missing_full_gates": ",".join(_missing_gates(row)),
                "next_action": row.get("next_action", ""),
            }
        )
    rows.sort(key=lambda item: _num(item.get("opportunity_score")), reverse=True)
    return rows


def _rejection_summary(rejected_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter(str(row.get("rejection_reason") or "unknown") for row in rejected_rows)
    return [{"reason": reason, "count": count} for reason, count in counts.most_common(20)]


def _alpha_rows(alpha_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in alpha_rows:
        edge = safe_float(row.get("edge_lower_bound"))
        raw_edge = safe_float(row.get("alpha_raw_edge"))
        liquidity = safe_float(row.get("liquidity"))
        spread = safe_float(row.get("spread"))
        category = str(row.get("category") or "unknown")
        signal_cohort = str(row.get("signal_cohort") or "")
        validation_reason = str(row.get("validation_layer_reason") or "")
        blockers: list[str] = []
        if category == "unknown" or signal_cohort.startswith("near_miss_learning|unknown"):
            blockers.append("needs_classification")
        if edge is None or edge < 0.03:
            blockers.append("edge_below_trade_threshold")
        if liquidity is None or liquidity < 250:
            blockers.append("liquidity_thin_for_strategy_v2")
        if spread is None or spread > 0.02:
            blockers.append("spread_wide_for_strategy_v2")
        if validation_reason:
            blockers.append(validation_reason)
        rows.append(
            {
                "category": category,
                "signal_cohort": signal_cohort,
                "market_slug": row.get("market_slug", ""),
                "outcome": row.get("outcome", ""),
                "edge_lower_bound": "" if edge is None else edge,
                "alpha_raw_edge": "" if raw_edge is None else raw_edge,
                "liquidity": "" if liquidity is None else liquidity,
                "spread": "" if spread is None else spread,
                "alpha_trade_candidate": row.get("alpha_trade_candidate", ""),
                "shadow_trade_candidate": row.get("shadow_trade_candidate", ""),
                "validation_layer_pass": row.get("validation_layer_pass", ""),
                "blockers": "; ".join(blockers) if blockers else "needs_external_anchor_for_v2",
            }
        )
    rows.sort(key=lambda item: safe_float(item.get("edge_lower_bound")) or -999.0, reverse=True)
    return rows[:50]


def _liquidity_rows(liquidity_summary: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = []
    for key in ("model_target_queue", "family_summary", "families", "family_rows"):
        value = liquidity_summary.get(key)
        if isinstance(value, list):
            candidates = value
            break
    rows: list[dict[str, Any]] = []
    for row in candidates:
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                "family": row.get("family", row.get("name", "unknown")),
                "status": row.get("status", row.get("decision", "")),
                "tradable_tokens": row.get("tradable_tokens", row.get("tradable", row.get("tokens", ""))),
                "max_liquidity": row.get("max_liquidity", ""),
                "min_spread": row.get("min_spread", ""),
                "recommendation": row.get("recommendation", ""),
            }
        )
    return rows


def _build_decision(validation_report: dict[str, Any], cohort_rows: list[dict[str, Any]]) -> tuple[str, str]:
    promotion_gate = validation_report.get("promotion_gate", {}) if isinstance(validation_report, dict) else {}
    approved_for_paper = bool(validation_report.get("approved_for_paper_trading"))
    oos_skill = bool(promotion_gate.get("oos_beats_market_significantly"))
    promotable = [row for row in cohort_rows if row.get("action_status") == "promotion_review_candidate"]
    thin_positive = [row for row in cohort_rows if row.get("action_status") == "thin_positive"]
    near_roi = [row for row in cohort_rows if row.get("action_status") == "near_but_roi_short"]
    if approved_for_paper and promotable:
        return "promotion_review_required", "A cohort may be mechanically promotable, but human review is still required before paper."
    if promotable:
        return "cohort_shape_found_but_global_gate_blocks", "Mechanical cohort evidence exists, but global validation still blocks paper; investigate anchor quality."
    if near_roi:
        return "near_roi_but_not_goal_path", "One cohort is close on counts but fails ROI; do not wait indefinitely or loosen gates."
    if thin_positive:
        return "thin_positive_hypotheses", "Some high-ROI thin positives exist; only continue them if Strategy V2 can attach independent anchors."
    if not oos_skill:
        return "pivot_to_anchored_edge", "Collected data does not show model skill over the market; focus on independent-anchor opportunities."
    return "collect_more_evidence", "Continue shadow-only collection and rerun the opportunity audit daily."


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Polymarket Collected-Data Opportunity Audit",
        "",
        f"Generated: {report['generated_at_utc']}",
        "",
        f"Decision: **{report['decision']}**",
        "",
        report["recommended_action"],
        "",
        "## Global validation",
        "",
        f"- Approved for paper: {report['global_validation'].get('approved_for_paper_trading')}",
        f"- Approved for live: {report['global_validation'].get('approved_for_live_trading')}",
        f"- OOS beats market: {report['global_validation'].get('oos_beats_market_significantly')}",
        f"- OOS Brier skill vs market: {report['global_validation'].get('oos_brier_skill_vs_market')}",
        f"- Resolved labels: {report['global_validation'].get('resolved_labels')} / {report['global_validation'].get('target_resolved_labels')}",
        "",
        "## Top cohort opportunities",
        "",
        "| Cohort | Action status | P&L | ROI | Fills | Settled | Missing gates | Recommended action |",
        "|---|---|---:|---:|---:|---:|---|---|",
    ]
    for row in report.get("top_cohorts", [])[:15]:
        lines.append(
            "| {cohort} | {status} | {pnl:.2f} | {roi:.4f} | {fills:.0f} | {settled:.0f} | {missing} | {action} |".format(
                cohort=str(row.get("cohort", ""))[:80],
                status=row.get("action_status", ""),
                pnl=_num(row.get("total_pnl_usdc")),
                roi=_num(row.get("roi")),
                fills=_num(row.get("buy_fills")),
                settled=_num(row.get("settled_fills")),
                missing=row.get("missing_full_gates", ""),
                action=str(row.get("recommended_action", ""))[:120],
            )
        )
    lines.extend([
        "",
        "## Kill / deprioritise list",
        "",
        "| Cohort | P&L | ROI | Settled | Reason |",
        "|---|---:|---:|---:|---|",
    ])
    for row in report.get("kill_or_deprioritise", [])[:15]:
        lines.append(
            "| {cohort} | {pnl:.2f} | {roi:.4f} | {settled:.0f} | {action} |".format(
                cohort=str(row.get("cohort", ""))[:80],
                pnl=_num(row.get("total_pnl_usdc")),
                roi=_num(row.get("roi")),
                settled=_num(row.get("settled_fills")),
                action=str(row.get("recommended_action", ""))[:120],
            )
        )
    lines.extend([
        "",
        "## Top alpha rows needing Strategy V2 anchors",
        "",
        "| Category | Market | Outcome | Edge LB | Liquidity | Spread | Blockers |",
        "|---|---|---|---:|---:|---:|---|",
    ])
    for row in report.get("top_alpha_rows", [])[:15]:
        lines.append(
            "| {category} | {market} | {outcome} | {edge} | {liq} | {spread} | {blockers} |".format(
                category=row.get("category", ""),
                market=str(row.get("market_slug", ""))[:70],
                outcome=row.get("outcome", ""),
                edge=row.get("edge_lower_bound", ""),
                liq=row.get("liquidity", ""),
                spread=row.get("spread", ""),
                blockers=str(row.get("blockers", ""))[:120],
            )
        )
    lines.extend([
        "",
        "## Rejection summary",
        "",
        "| Reason | Count |",
        "|---|---:|",
    ])
    for row in report.get("rejection_summary", [])[:12]:
        lines.append(f"| {row['reason']} | {row['count']} |")
    lines.extend([
        "",
        "## Output files",
        "",
        f"- Cohort CSV: `{report['outputs']['cohorts_csv']}`",
        f"- Alpha rows CSV: `{report['outputs']['alpha_csv']}`",
        "",
        "This audit is read-only research evidence. It is not permission to paper trade or live trade.",
    ])
    write_text_atomic(path, "\n".join(lines))


def build_opportunity_audit(cfg: EngineConfig) -> dict[str, Any]:
    output_dir = cfg.output_root / OUTPUT_DIRNAME
    governance_root = cfg.governance_root
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "validation_report": governance_root / "validation_report.json",
        "promotion_review": governance_root / "promotion_review.json",
        "signal_cohort_pnl": governance_root / "signal_cohort_pnl.json",
        "local_history_audit_summary": governance_root / "local_history_audit_summary.json",
        "liquidity_discovery_summary": governance_root / "liquidity_discovery_summary.json",
        "mispricing_alpha_scores": cfg.output_root / "polymarket_predictions" / "mispricing_alpha_scores.csv",
        "rejected_signals": cfg.output_root / "polymarket_predictions" / "rejected_signals.csv",
        "trade_signals": cfg.output_root / "polymarket_predictions" / "trade_signals.csv",
        "shadow_positions": cfg.output_root / "polymarket_shadow" / "shadow_positions.csv",
        "shadow_fills": cfg.output_root / "polymarket_shadow" / "shadow_fills.csv",
    }
    validation_report = read_json(paths["validation_report"], default={}) or {}
    promotion_review = read_json(paths["promotion_review"], default={}) or {}
    liquidity_summary = read_json(paths["liquidity_discovery_summary"], default={}) or {}
    alpha = _alpha_rows(_read_csv(paths["mispricing_alpha_scores"]))
    rejected = _read_csv(paths["rejected_signals"])
    cohorts = _cohort_rows(promotion_review)
    decision, recommended_action = _build_decision(validation_report, cohorts)
    promotion_gate = validation_report.get("promotion_gate", {}) if isinstance(validation_report, dict) else {}
    report = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "decision": decision,
        "recommended_action": recommended_action,
        "global_validation": {
            "approved_for_paper_trading": validation_report.get("approved_for_paper_trading"),
            "approved_for_live_trading": validation_report.get("approved_for_live_trading"),
            "model_admissibility": validation_report.get("model_admissibility"),
            "oos_beats_market_significantly": promotion_gate.get("oos_beats_market_significantly"),
            "oos_brier_skill_vs_market": promotion_gate.get("oos_brier_skill_vs_market"),
            "resolved_labels": promotion_gate.get("resolved_labels"),
            "target_resolved_labels": promotion_gate.get("target_resolved_labels"),
            "paper_blockers": promotion_gate.get("paper_blockers", []),
            "live_blockers": promotion_gate.get("live_blockers", []),
        },
        "cohort_action_counts": dict(Counter(str(row.get("action_status")) for row in cohorts).most_common()),
        "top_cohorts": cohorts[:25],
        "thin_positive": [row for row in cohorts if row.get("action_status") == "thin_positive"][:25],
        "kill_or_deprioritise": [
            row
            for row in cohorts
            if row.get("action_status") in {"kill_or_quarantine", "stagnant", "negative_thin_or_open", "blocked_metadata"}
        ][:25],
        "top_alpha_rows": alpha[:25],
        "rejection_summary": _rejection_summary(rejected),
        "liquidity_families": _liquidity_rows(liquidity_summary),
        "data_inventory": _path_inventory(paths),
        "outputs": {
            "report_json": str(output_dir / REPORT_JSON),
            "report_md": str(output_dir / REPORT_MD),
            "cohorts_csv": str(output_dir / COHORTS_CSV),
            "alpha_csv": str(output_dir / ALPHA_CSV),
        },
    }
    write_csv(output_dir / COHORTS_CSV, cohorts)
    write_csv(output_dir / ALPHA_CSV, alpha)
    write_json(output_dir / REPORT_JSON, report)
    _write_markdown(output_dir / REPORT_MD, report)
    return report


def run(config_path: str = "polymarket_predictive_config.example.yaml") -> dict[str, Any]:
    cfg = load_config(config_path)
    report = build_opportunity_audit(cfg)
    return {
        "status": "ok",
        "generated_at_utc": report["generated_at_utc"],
        "decision": report["decision"],
        "recommended_action": report["recommended_action"],
        "cohort_action_counts": report["cohort_action_counts"],
        "top_cohort": report["top_cohorts"][0] if report["top_cohorts"] else None,
        "report_json": report["outputs"]["report_json"],
        "report_md": report["outputs"]["report_md"],
        "cohorts_csv": report["outputs"]["cohorts_csv"],
        "alpha_csv": report["outputs"]["alpha_csv"],
    }
