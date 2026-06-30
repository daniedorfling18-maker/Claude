from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

from .config import EngineConfig, load_config
from .utils import now_utc, parse_timestamp, read_csv_rows, safe_float, write_csv, write_json

OUTPUT_DIRNAME = "polymarket_strategy_v2"
PERSISTENCE_FILE = "anchored_edge_persistence_log.csv"
FORWARD_EVIDENCE_FILE = "strategy_v2_forward_evidence.csv"
COHORT_EVIDENCE_FILE = "strategy_v2_cohort_forward_evidence.csv"
SUMMARY_JSON = "strategy_v2_forward_evidence.json"

CANDIDATE_FIELDS = [
    "signal_cohort",
    "family",
    "market_slug",
    "outcome",
    "token_id",
    "first_seen_at_utc",
    "last_seen_at_utc",
    "observations",
    "shadow_observations",
    "latest_status",
    "latest_blockers",
    "entry_price",
    "latest_price",
    "stake_usdc",
    "quantity",
    "mark_value_usdc",
    "mark_pnl_usdc",
    "mark_roi",
    "monthly_run_rate_usdc",
    "entry_risk_adjusted_anchor_edge",
    "latest_risk_adjusted_anchor_edge",
    "latest_anchor_fair_probability",
    "latest_liquidity",
    "latest_spread",
    "terminal_mark_proxy",
    "resolved_evidence",
    "settlement_status",
]

COHORT_FIELDS = [
    "signal_cohort",
    "family",
    "candidates",
    "observations",
    "current_shadow_candidates",
    "terminal_mark_proxy_candidates",
    "resolved_candidates",
    "stake_usdc",
    "total_mark_pnl_usdc",
    "mark_roi",
    "monthly_run_rate_usdc",
    "median_risk_adjusted_anchor_edge",
    "top_positive_market_pnl_share",
    "promotion_ready_score",
    "promotion_ready_checks",
    "paper_review_candidate",
    "status",
    "reason",
    "first_seen_at_utc",
    "last_seen_at_utc",
]


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    return cfg.raw.get("strategy_v2", {}) if isinstance(cfg.raw.get("strategy_v2"), dict) else {}


def _cohort(family: str) -> str:
    cleaned = str(family or "unknown").strip().lower() or "unknown"
    return f"strategy_v2|{cleaned}"


def _key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("family") or "unknown").strip().lower() or "unknown",
        str(row.get("market_slug") or "").strip(),
        str(row.get("outcome") or "").strip(),
    )


def _row_time(row: dict[str, Any]) -> datetime:
    parsed = parse_timestamp(row.get("logged_at_utc"))
    if parsed is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fmt_time(value: datetime | None) -> str:
    if value is None or value == datetime.min.replace(tzinfo=timezone.utc):
        return ""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _terminal_price_proxy(price: float | None, settings: dict[str, Any]) -> bool:
    if price is None:
        return False
    win = safe_float(settings.get("terminal_win_price")) or 0.99
    loss = safe_float(settings.get("terminal_loss_price")) or 0.01
    return price >= win or price <= loss


def _candidate_from_timeline(
    family: str,
    market_slug: str,
    outcome: str,
    timeline: list[dict[str, Any]],
    *,
    stake_usdc: float,
    settings: dict[str, Any],
) -> dict[str, Any] | None:
    timeline = sorted(timeline, key=_row_time)
    entry = next(
        (
            row
            for row in timeline
            if str(row.get("status") or "") == "shadow_candidate"
            and safe_float(row.get("executable_price")) is not None
        ),
        None,
    )
    if entry is None:
        return None

    entry_time = _row_time(entry)
    after_entry = [row for row in timeline if _row_time(row) >= entry_time and safe_float(row.get("executable_price")) is not None]
    latest = after_entry[-1] if after_entry else entry
    latest_time = _row_time(latest)
    entry_price = safe_float(entry.get("executable_price"))
    latest_price = safe_float(latest.get("executable_price"))
    if entry_price is None or latest_price is None or entry_price <= 0:
        return None

    quantity = stake_usdc / entry_price
    mark_value = quantity * latest_price
    pnl = mark_value - stake_usdc
    roi = pnl / stake_usdc if stake_usdc > 0 else 0.0
    elapsed_hours = max((latest_time - entry_time).total_seconds() / 3600.0, 0.0)
    monthly_run_rate = (pnl / elapsed_hours * 24.0 * 30.0) if elapsed_hours > 0 else None
    terminal = _terminal_price_proxy(latest_price, settings)

    return {
        "signal_cohort": _cohort(family),
        "family": family,
        "market_slug": market_slug,
        "outcome": outcome,
        "token_id": str(entry.get("token_id") or latest.get("token_id") or "").strip(),
        "first_seen_at_utc": _fmt_time(entry_time),
        "last_seen_at_utc": _fmt_time(latest_time),
        "observations": len(after_entry),
        "shadow_observations": sum(1 for row in after_entry if str(row.get("status") or "") == "shadow_candidate"),
        "latest_status": latest.get("status", ""),
        "latest_blockers": latest.get("blockers", ""),
        "entry_price": entry_price,
        "latest_price": latest_price,
        "stake_usdc": stake_usdc,
        "quantity": quantity,
        "mark_value_usdc": mark_value,
        "mark_pnl_usdc": pnl,
        "mark_roi": roi,
        "monthly_run_rate_usdc": "" if monthly_run_rate is None else monthly_run_rate,
        "entry_risk_adjusted_anchor_edge": entry.get("risk_adjusted_anchor_edge", ""),
        "latest_risk_adjusted_anchor_edge": latest.get("risk_adjusted_anchor_edge", ""),
        "latest_anchor_fair_probability": latest.get("anchor_fair_probability", ""),
        "latest_liquidity": latest.get("liquidity", ""),
        "latest_spread": latest.get("spread", ""),
        "terminal_mark_proxy": terminal,
        "resolved_evidence": False,
        "settlement_status": "terminal_price_proxy_unresolved" if terminal else "open_unresolved",
    }


def _cohort_rows(candidates: list[dict[str, Any]], settings: dict[str, Any]) -> list[dict[str, Any]]:
    by_cohort: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        by_cohort[str(row.get("signal_cohort") or _cohort(str(row.get("family") or "unknown")))].append(row)

    min_candidates = int(safe_float(settings.get("promotion_min_candidates")) or 20)
    min_resolved = int(safe_float(settings.get("promotion_min_settled")) or 10)
    min_roi = float(safe_float(settings.get("promotion_min_roi")) or 0.03)
    min_run_rate = float(safe_float(settings.get("promotion_min_monthly_run_rate_usdc")) or 20.0)
    max_positive_share = float(safe_float(settings.get("promotion_max_single_market_positive_pnl_share")) or 0.35)

    rows: list[dict[str, Any]] = []
    for cohort, cohort_candidates in by_cohort.items():
        family = str(cohort_candidates[0].get("family") or "unknown")
        total_stake = sum(float(safe_float(row.get("stake_usdc")) or 0.0) for row in cohort_candidates)
        total_pnl = sum(float(safe_float(row.get("mark_pnl_usdc")) or 0.0) for row in cohort_candidates)
        mark_roi = total_pnl / total_stake if total_stake > 0 else 0.0
        first_seen = min((parse_timestamp(row.get("first_seen_at_utc")) for row in cohort_candidates), default=None)
        last_seen = max((parse_timestamp(row.get("last_seen_at_utc")) for row in cohort_candidates), default=None)
        elapsed_hours = (
            max((last_seen - first_seen).total_seconds() / 3600.0, 0.0)
            if first_seen is not None and last_seen is not None
            else 0.0
        )
        monthly_run_rate = (total_pnl / elapsed_hours * 24.0 * 30.0) if elapsed_hours > 0 else None
        edge_values = [
            float(edge)
            for edge in (safe_float(row.get("entry_risk_adjusted_anchor_edge")) for row in cohort_candidates)
            if edge is not None
        ]
        positive_pnls = [max(0.0, float(safe_float(row.get("mark_pnl_usdc")) or 0.0)) for row in cohort_candidates]
        positive_total = sum(positive_pnls)
        top_positive_share = (max(positive_pnls) / positive_total) if positive_total > 0 else 0.0
        terminal = sum(1 for row in cohort_candidates if str(row.get("terminal_mark_proxy")).lower() == "true")
        resolved = sum(1 for row in cohort_candidates if str(row.get("resolved_evidence")).lower() == "true")
        current_shadow = sum(1 for row in cohort_candidates if str(row.get("latest_status") or "") == "shadow_candidate")
        observations = sum(int(safe_float(row.get("observations")) or 0) for row in cohort_candidates)
        median_edge = median(edge_values) if edge_values else 0.0

        checks = [
            len(cohort_candidates) >= min_candidates,
            resolved >= min_resolved,
            total_pnl > 0,
            mark_roi >= min_roi,
            (monthly_run_rate is not None and monthly_run_rate >= min_run_rate),
            median_edge > 0,
            top_positive_share <= max_positive_share if positive_total > 0 else False,
        ]
        paper_review_candidate = all(checks)
        if paper_review_candidate:
            status = "ready_for_human_paper_review"
            reason = "All Strategy V2 forward-evidence review gates passed; human review still required."
        elif resolved < min_resolved:
            status = "collect_resolved_forward_evidence"
            reason = f"Needs >= {min_resolved} resolved candidates; current resolved evidence is {resolved}."
        elif len(cohort_candidates) < min_candidates:
            status = "collect_more_unique_candidates"
            reason = f"Needs >= {min_candidates} unique candidates; current count is {len(cohort_candidates)}."
        elif total_pnl <= 0 or mark_roi < min_roi:
            status = "negative_or_insufficient_forward_pnl"
            reason = "Forward mark-to-market evidence does not clear the P&L/ROI gate."
        elif monthly_run_rate is None or monthly_run_rate < min_run_rate:
            status = "run_rate_below_review_threshold"
            reason = f"Monthly run-rate is below the ${min_run_rate:.2f} review threshold."
        elif top_positive_share > max_positive_share:
            status = "concentration_risk"
            reason = "Positive P&L is too concentrated in one market."
        else:
            status = "collect_more_evidence"
            reason = "Forward evidence remains incomplete."

        rows.append(
            {
                "signal_cohort": cohort,
                "family": family,
                "candidates": len(cohort_candidates),
                "observations": observations,
                "current_shadow_candidates": current_shadow,
                "terminal_mark_proxy_candidates": terminal,
                "resolved_candidates": resolved,
                "stake_usdc": total_stake,
                "total_mark_pnl_usdc": total_pnl,
                "mark_roi": mark_roi,
                "monthly_run_rate_usdc": "" if monthly_run_rate is None else monthly_run_rate,
                "median_risk_adjusted_anchor_edge": median_edge,
                "top_positive_market_pnl_share": top_positive_share,
                "promotion_ready_score": sum(1 for item in checks if item),
                "promotion_ready_checks": len(checks),
                "paper_review_candidate": paper_review_candidate,
                "status": status,
                "reason": reason,
                "first_seen_at_utc": _fmt_time(first_seen),
                "last_seen_at_utc": _fmt_time(last_seen),
            }
        )
    rows.sort(
        key=lambda row: (
            bool(row.get("paper_review_candidate")),
            int(row.get("promotion_ready_score") or 0),
            safe_float(row.get("total_mark_pnl_usdc")) or -999.0,
        ),
        reverse=True,
    )
    return rows


def build_strategy_v2_forward_evidence(cfg: EngineConfig) -> dict[str, Any]:
    settings = _settings(cfg)
    out_dir = cfg.output_root / OUTPUT_DIRNAME
    log_path = out_dir / PERSISTENCE_FILE
    stake_usdc = float(safe_float(settings.get("forward_evidence_stake_usdc")) or 10.0)
    rows = read_csv_rows(log_path)
    candidate_index = {
        _key(row): row
        for row in read_csv_rows(out_dir / "anchored_edge_candidates.csv")
        if str(row.get("token_id") or row.get("asset_id") or row.get("outcome_token_id") or "").strip()
    }
    for row in rows:
        if not str(row.get("token_id") or row.get("asset_id") or row.get("outcome_token_id") or "").strip():
            candidate_row = candidate_index.get(_key(row))
            if candidate_row:
                row["token_id"] = (
                    candidate_row.get("token_id")
                    or candidate_row.get("asset_id")
                    or candidate_row.get("outcome_token_id")
                    or ""
                )
    timelines: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        timelines[_key(row)].append(row)

    candidates: list[dict[str, Any]] = []
    for (family, market_slug, outcome), timeline in timelines.items():
        candidate = _candidate_from_timeline(
            family,
            market_slug,
            outcome,
            timeline,
            stake_usdc=stake_usdc,
            settings=settings,
        )
        if candidate is not None:
            candidates.append(candidate)
    candidates.sort(
        key=lambda row: (
            str(row.get("signal_cohort") or ""),
            str(row.get("market_slug") or ""),
            str(row.get("outcome") or ""),
        )
    )
    cohort_rows = _cohort_rows(candidates, settings)

    write_csv(out_dir / FORWARD_EVIDENCE_FILE, candidates, fieldnames=CANDIDATE_FIELDS)
    write_csv(out_dir / COHORT_EVIDENCE_FILE, cohort_rows, fieldnames=COHORT_FIELDS)

    total_stake = sum(float(safe_float(row.get("stake_usdc")) or 0.0) for row in candidates)
    total_pnl = sum(float(safe_float(row.get("mark_pnl_usdc")) or 0.0) for row in candidates)
    summary = {
        "status": "computed",
        "generated_at_utc": now_utc(),
        "source_file": str(log_path),
        "candidate_file": str(out_dir / FORWARD_EVIDENCE_FILE),
        "cohort_file": str(out_dir / COHORT_EVIDENCE_FILE),
        "rows_in": len(rows),
        "unique_forward_candidates": len(candidates),
        "cohorts": len(cohort_rows),
        "current_shadow_candidates": sum(1 for row in candidates if str(row.get("latest_status") or "") == "shadow_candidate"),
        "terminal_mark_proxy_candidates": sum(1 for row in candidates if str(row.get("terminal_mark_proxy")).lower() == "true"),
        "resolved_candidates": sum(1 for row in candidates if str(row.get("resolved_evidence")).lower() == "true"),
        "paper_review_candidates": sum(1 for row in cohort_rows if str(row.get("paper_review_candidate")).lower() == "true"),
        "total_stake_usdc": total_stake,
        "total_mark_pnl_usdc": total_pnl,
        "mark_roi": total_pnl / total_stake if total_stake > 0 else 0.0,
        "decision": "promote_to_human_review"
        if any(str(row.get("paper_review_candidate")).lower() == "true" for row in cohort_rows)
        else "collect_more_resolved_forward_evidence"
        if candidates
        else "collect_more_candidates",
        "cohort_summary": cohort_rows[:25],
        "top_candidates": sorted(
            candidates,
            key=lambda row: safe_float(row.get("mark_pnl_usdc")) or -999.0,
            reverse=True,
        )[:25],
        "warnings": {
            "shadow_only": True,
            "not_paper_or_live_trading": True,
            "resolved_evidence_required_for_promotion": True,
            "mark_to_market_is_not_settlement": True,
        },
    }
    write_json(out_dir / SUMMARY_JSON, summary)
    return summary


def run(config_path: str = "polymarket_predictive_config.example.yaml") -> dict[str, Any]:
    cfg = load_config(config_path)
    return build_strategy_v2_forward_evidence(cfg)
