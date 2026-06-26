from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from .config import EngineConfig
from .shadow_cohort import read_shadow_signal_cohort_pnl
from .utils import now_utc, safe_float, write_csv, write_json
from .worldcup_validation import signal_cohort


def _json_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _latest_mark(con, market_id: str, token_id: str, fallback: float = 0.0) -> float:
    row = con.execute(
        """
        SELECT best_bid, midpoint
        FROM market_snapshots
        WHERE market_id = ? AND token_id = ?
        ORDER BY collected_at DESC, rowid DESC
        LIMIT 1
        """,
        (market_id, token_id),
    ).fetchone()
    if row is not None:
        bid = safe_float(row["best_bid"])
        if bid is not None:
            return max(0.0, min(1.0, bid))
        midpoint = safe_float(row["midpoint"])
        if midpoint is not None:
            return max(0.0, min(1.0, midpoint))
    return max(0.0, min(1.0, fallback))


def _order_cohort(order: dict[str, Any]) -> str:
    payload = _json_payload(order.get("source_signal_json"))
    for key in ("signal_cohort", "validation_cohort", "cohort"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    merged = {**order, **payload}
    return signal_cohort(merged)


def _empty_stats() -> dict[str, Any]:
    return {
        "buy_fills": 0,
        "sell_fills": 0,
        "open_positions": 0,
        "total_buy_cost_usdc": 0.0,
        "sell_proceeds_usdc": 0.0,
        "open_mark_value_usdc": 0.0,
        "open_cost_basis_usdc": 0.0,
    }


def compute_signal_cohort_pnl(con, cfg: EngineConfig) -> dict[str, Any]:
    settings = cfg.raw.get("cohort_promotion", {}) or {}
    minimum_filled_orders = int(settings.get("minimum_filled_orders", 5))
    minimum_settled_orders = int(settings.get("minimum_settled_orders", 1))
    minimum_pnl_usdc = float(settings.get("minimum_pnl_usdc", 0.0))
    minimum_roi = float(settings.get("minimum_roi", 0.0))
    minimum_monthly_run_rate = float(settings.get("minimum_monthly_run_rate_usdc", 0.0))
    minimum_tracking_hours = float(settings.get("minimum_tracking_hours_for_promotion", 0.0))
    allow_shadow = str(settings.get("allow_shadow_evidence_for_promotion", True)).strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
    }
    allow_probationary = str(settings.get("allow_probationary_paper_trading", True)).strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
    }
    probationary_minimum_filled_orders = int(settings.get("probationary_minimum_filled_orders", 3))
    probationary_minimum_settled_orders = int(settings.get("probationary_minimum_settled_orders", 3))
    probationary_minimum_pnl_usdc = float(settings.get("probationary_minimum_pnl_usdc", minimum_pnl_usdc))
    probationary_minimum_roi = float(settings.get("probationary_minimum_roi", minimum_roi))
    probationary_minimum_monthly_run_rate = float(
        settings.get("probationary_minimum_monthly_run_rate_usdc", minimum_monthly_run_rate)
    )
    probationary_minimum_tracking_hours = float(
        settings.get("probationary_minimum_tracking_hours", min(1.0, minimum_tracking_hours))
    )
    probationary_max_stake_usdc = float(settings.get("probationary_max_stake_usdc", 2.0))

    order_rows = [
        dict(row)
        for row in con.execute(
            "SELECT * FROM orders WHERE mode = 'paper' ORDER BY created_at, order_id"
        ).fetchall()
    ]
    order_by_id = {str(row.get("order_id")): row for row in order_rows}
    cohort_by_market_token: dict[tuple[str, str], str] = {}
    stats: dict[str, dict[str, Any]] = defaultdict(_empty_stats)

    for order in order_rows:
        if str(order.get("side")) != "BUY_YES":
            continue
        cohort = _order_cohort(order)
        market_token = (str(order.get("market_id") or ""), str(order.get("token_id") or ""))
        cohort_by_market_token.setdefault(market_token, cohort)

    fill_rows = [
        dict(row)
        for row in con.execute(
            "SELECT * FROM fills ORDER BY created_at, fill_id"
        ).fetchall()
    ]
    for fill in fill_rows:
        order = order_by_id.get(str(fill.get("order_id"))) or {}
        market_token = (str(fill.get("market_id") or ""), str(fill.get("token_id") or ""))
        cohort = cohort_by_market_token.get(market_token) or _order_cohort(order or fill)
        side = str(fill.get("side") or order.get("side") or "")
        gross = safe_float(fill.get("gross_notional_usdc")) or 0.0
        fee = safe_float(fill.get("fee_usdc")) or 0.0
        if side == "BUY_YES":
            stats[cohort]["buy_fills"] += 1
            stats[cohort]["total_buy_cost_usdc"] += gross + fee
        elif side == "SELL_YES":
            stats[cohort]["sell_fills"] += 1
            stats[cohort]["sell_proceeds_usdc"] += gross - fee

    position_rows = [
        dict(row)
        for row in con.execute(
            "SELECT * FROM positions WHERE status = 'open' AND quantity > 0"
        ).fetchall()
    ]
    for position in position_rows:
        market_token = (str(position.get("market_id") or ""), str(position.get("token_id") or ""))
        cohort = cohort_by_market_token.get(market_token) or signal_cohort(position)
        quantity = safe_float(position.get("quantity")) or 0.0
        average_entry = safe_float(position.get("average_entry_price")) or 0.0
        mark = _latest_mark(con, market_token[0], market_token[1], fallback=average_entry)
        cost_basis = safe_float(position.get("cost_basis_usdc")) or 0.0
        stats[cohort]["open_positions"] += 1
        stats[cohort]["open_mark_value_usdc"] += quantity * mark
        stats[cohort]["open_cost_basis_usdc"] += cost_basis

    paper_cohorts: list[dict[str, Any]] = []
    for cohort, row in sorted(stats.items()):
        total_pnl = (
            float(row["sell_proceeds_usdc"])
            + float(row["open_mark_value_usdc"])
            - float(row["total_buy_cost_usdc"])
        )
        total_at_risk = float(row["total_buy_cost_usdc"])
        roi = total_pnl / total_at_risk if total_at_risk > 0 else 0.0
        paper_cohorts.append(
            {
                "signal_cohort": cohort,
                **row,
                "paper_buy_fills": row["buy_fills"],
                "paper_total_pnl_usdc": total_pnl,
                "total_pnl_usdc": total_pnl,
                "roi": roi,
                "promoted": False,
                "promotion_reason": (
                    f"needs >= {minimum_filled_orders} buy fills and P&L > {minimum_pnl_usdc:.2f}"
                ),
            }
        )

    combined: dict[str, dict[str, Any]] = {
        str(row["signal_cohort"]): dict(row)
        for row in paper_cohorts
    }
    shadow_summary = read_shadow_signal_cohort_pnl(cfg) if allow_shadow else {}
    shadow_cohorts = shadow_summary.get("cohorts", []) if isinstance(shadow_summary, dict) else []
    if not isinstance(shadow_cohorts, list):
        shadow_cohorts = []
    for shadow_row in shadow_cohorts:
        if not isinstance(shadow_row, dict):
            continue
        cohort = str(shadow_row.get("signal_cohort") or "")
        if not cohort:
            continue
        row = combined.setdefault(
            cohort,
            {
                "signal_cohort": cohort,
                **_empty_stats(),
                "paper_buy_fills": 0,
                "paper_total_pnl_usdc": 0.0,
                "total_pnl_usdc": 0.0,
                "roi": 0.0,
            },
        )
        row.update(
            {
                "shadow_fills": int(safe_float(shadow_row.get("shadow_fills")) or 0),
                "shadow_sell_fills": int(safe_float(shadow_row.get("shadow_sell_fills")) or 0),
                "shadow_open_positions": int(safe_float(shadow_row.get("shadow_open_positions")) or 0),
                "shadow_total_buy_cost_usdc": safe_float(shadow_row.get("shadow_total_buy_cost_usdc")) or 0.0,
                "shadow_open_mark_value_usdc": safe_float(shadow_row.get("shadow_open_mark_value_usdc")) or 0.0,
                "shadow_total_pnl_usdc": safe_float(shadow_row.get("shadow_total_pnl_usdc")) or 0.0,
                "shadow_roi": safe_float(shadow_row.get("shadow_roi")) or 0.0,
                "shadow_monthly_run_rate_usdc": safe_float(shadow_row.get("shadow_monthly_run_rate_usdc")) or 0.0,
                "evidence_elapsed_hours": safe_float(shadow_row.get("evidence_elapsed_hours")) or 0.0,
            }
        )

    cohorts: list[dict[str, Any]] = []
    for cohort, row in sorted(combined.items()):
        paper_pnl = safe_float(row.get("paper_total_pnl_usdc")) or 0.0
        shadow_pnl = safe_float(row.get("shadow_total_pnl_usdc")) or 0.0
        paper_fills = int(safe_float(row.get("paper_buy_fills") or row.get("buy_fills")) or 0)
        paper_sell_fills = int(safe_float(row.get("sell_fills")) or 0)
        shadow_fills = int(safe_float(row.get("shadow_fills")) or 0)
        shadow_sell_fills = int(safe_float(row.get("shadow_sell_fills")) or 0)
        evidence_fills = paper_fills + (shadow_fills if allow_shadow else 0)
        evidence_settled_fills = paper_sell_fills + (shadow_sell_fills if allow_shadow else 0)
        evidence_pnl = paper_pnl + (shadow_pnl if allow_shadow else 0.0)
        paper_at_risk = safe_float(row.get("total_buy_cost_usdc")) or 0.0
        shadow_at_risk = safe_float(row.get("shadow_total_buy_cost_usdc")) or 0.0
        evidence_at_risk = paper_at_risk + (shadow_at_risk if allow_shadow else 0.0)
        evidence_roi = evidence_pnl / evidence_at_risk if evidence_at_risk > 0 else 0.0
        evidence_monthly_run_rate = safe_float(row.get("shadow_monthly_run_rate_usdc")) or 0.0
        evidence_elapsed_hours = safe_float(row.get("evidence_elapsed_hours")) or 0.0
        promoted = bool(
            evidence_fills >= minimum_filled_orders
            and evidence_settled_fills >= minimum_settled_orders
            and evidence_pnl > minimum_pnl_usdc
            and evidence_roi >= minimum_roi
            and evidence_elapsed_hours >= minimum_tracking_hours
            and evidence_monthly_run_rate >= minimum_monthly_run_rate
        )
        probationary = bool(
            allow_probationary
            and not promoted
            and evidence_fills >= probationary_minimum_filled_orders
            and evidence_settled_fills >= probationary_minimum_settled_orders
            and evidence_pnl > probationary_minimum_pnl_usdc
            and evidence_roi >= probationary_minimum_roi
            and evidence_elapsed_hours >= probationary_minimum_tracking_hours
            and evidence_monthly_run_rate >= probationary_minimum_monthly_run_rate
        )
        readiness = {
            "fills_remaining": max(0, minimum_filled_orders - evidence_fills),
            "settled_fills_remaining": max(0, minimum_settled_orders - evidence_settled_fills),
            "pnl_remaining_usdc": max(0.0, minimum_pnl_usdc - evidence_pnl),
            "roi_remaining": max(0.0, minimum_roi - evidence_roi),
            "tracking_hours_remaining": max(0.0, minimum_tracking_hours - evidence_elapsed_hours),
            "monthly_run_rate_remaining_usdc": max(0.0, minimum_monthly_run_rate - evidence_monthly_run_rate),
        }
        row.update(
            {
                "buy_fills": evidence_fills,
                "settled_fills": evidence_settled_fills,
                "total_pnl_usdc": evidence_pnl,
                "roi": evidence_roi,
                "monthly_run_rate_usdc": evidence_monthly_run_rate,
                "evidence_elapsed_hours": evidence_elapsed_hours,
                "promotion_readiness": readiness,
                "promotion_ready_score": sum(1 for value in readiness.values() if value <= 0),
                "promotion_ready_checks": len(readiness),
                "promotion_evidence_source": (
                    "paper_plus_shadow"
                    if paper_fills and shadow_fills and allow_shadow
                    else "shadow"
                    if shadow_fills and allow_shadow
                    else "paper"
                ),
                "promoted": promoted,
                "probationary": probationary,
                "probationary_max_stake_usdc": probationary_max_stake_usdc if probationary else 0.0,
                "promotion_reason": (
                    "positive cohort evidence"
                    if promoted
                    else "positive probationary cohort evidence"
                    if probationary
                    else (
                        f"needs >= {minimum_filled_orders} evidence fills, "
                        f">= {minimum_settled_orders} settled fills, "
                        f"P&L > {minimum_pnl_usdc:.2f}, ROI >= {minimum_roi:.2%}, "
                        f"evidence age >= {minimum_tracking_hours:.1f}h, "
                        f"and monthly run-rate >= {minimum_monthly_run_rate:.2f}"
                    )
                ),
            }
        )
        cohorts.append(row)

    promotion_watchlist = sorted(
        (
            {
                "signal_cohort": row.get("signal_cohort"),
                "promoted": row.get("promoted"),
                "probationary": row.get("probationary"),
                "probationary_max_stake_usdc": row.get("probationary_max_stake_usdc", 0.0),
                "promotion_ready_score": row.get("promotion_ready_score", 0),
                "promotion_ready_checks": row.get("promotion_ready_checks", 0),
                "promotion_readiness": row.get("promotion_readiness", {}),
                "total_pnl_usdc": row.get("total_pnl_usdc", 0.0),
                "roi": row.get("roi", 0.0),
                "monthly_run_rate_usdc": row.get("monthly_run_rate_usdc", 0.0),
                "buy_fills": row.get("buy_fills", 0),
                "settled_fills": row.get("settled_fills", 0),
                "promotion_reason": row.get("promotion_reason", ""),
            }
            for row in cohorts
            if not row.get("promoted")
        ),
        key=lambda row: (
            int(row.get("promotion_ready_score") or 0),
            float(row.get("monthly_run_rate_usdc") or 0.0),
            float(row.get("total_pnl_usdc") or 0.0),
            float(row.get("roi") or 0.0),
        ),
        reverse=True,
    )[:10]

    return {
        "status": "computed",
        "generated_at_utc": now_utc(),
        "promotion_policy": {
            "minimum_filled_orders": minimum_filled_orders,
            "minimum_settled_orders": minimum_settled_orders,
            "minimum_pnl_usdc": minimum_pnl_usdc,
            "minimum_roi": minimum_roi,
            "minimum_monthly_run_rate_usdc": minimum_monthly_run_rate,
            "minimum_tracking_hours_for_promotion": minimum_tracking_hours,
            "allow_probationary_paper_trading": allow_probationary,
            "probationary_minimum_filled_orders": probationary_minimum_filled_orders,
            "probationary_minimum_settled_orders": probationary_minimum_settled_orders,
            "probationary_max_stake_usdc": probationary_max_stake_usdc,
            "require_positive_forward_pnl": bool(settings.get("require_positive_forward_pnl", True)),
            "allow_shadow_evidence_for_promotion": allow_shadow,
        },
        "shadow_summary_status": shadow_summary.get("status") if isinstance(shadow_summary, dict) else "missing",
        "cohorts": cohorts,
        "promoted_cohorts": [row["signal_cohort"] for row in cohorts if row.get("promoted")],
        "probationary_cohorts": [row["signal_cohort"] for row in cohorts if row.get("probationary")],
        "promotion_watchlist": promotion_watchlist,
    }


def write_signal_cohort_pnl(con, cfg: EngineConfig) -> dict[str, Any]:
    payload = compute_signal_cohort_pnl(con, cfg)
    summary_file = str((cfg.raw.get("cohort_promotion", {}) or {}).get("summary_file", "signal_cohort_pnl.json"))
    write_json(cfg.governance_root / summary_file, payload)
    write_csv(cfg.governance_root / "signal_cohort_pnl.csv", payload.get("cohorts", []))
    return payload
