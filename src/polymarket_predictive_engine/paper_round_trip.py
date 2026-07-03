from __future__ import annotations

import json
from collections import Counter, defaultdict, deque
from datetime import timezone
from typing import Any

from .config import EngineConfig, load_config
from .storage import connect_db
from .utils import now_utc, parse_timestamp, read_json, safe_float, write_csv, write_json

OUTPUT_DIRNAME = "polymarket_price_action"
PAPER_ROUND_TRIP_FILE = "paper_broker_round_trip_evidence.csv"
SUMMARY_JSON = "paper_broker_round_trip_summary.json"

ROUND_TRIP_FIELDS = [
    "source",
    "target_action",
    "candidate_reason",
    "edge_lower_bound",
    "signal_cohort",
    "family",
    "market_slug",
    "question",
    "outcome",
    "token_id",
    "market_id",
    "snapshot_match_mode",
    "snapshot_market_id",
    "entry_time_utc",
    "entry_price",
    "entry_signal_bid",
    "entry_signal_ask",
    "entry_signal_spread",
    "entry_signal_timestamp",
    "stake_usdc",
    "quantity",
    "observations",
    "latest_time_utc",
    "latest_bid",
    "latest_ask",
    "latest_midpoint",
    "latest_spread",
    "max_bid",
    "max_bid_time_utc",
    "min_bid",
    "min_bid_time_utc",
    "exit_time_utc",
    "exit_price",
    "exit_quote_bid",
    "exit_quote_ask",
    "exit_quote_spread",
    "exit_quote_timestamp",
    "exit_quote_source",
    "quote_consistency_status",
    "quote_consistency_reason",
    "exit_quote_snapshot_bid_gap",
    "exit_fill_snapshot_bid_gap",
    "exit_reason",
    "round_trip_status",
    "realized_pnl_usdc",
    "realized_roi",
    "realized_monthly_run_rate_usdc",
    "mark_pnl_usdc",
    "mark_roi",
    "mark_monthly_run_rate_usdc",
    "take_profit_return",
    "stop_loss_return",
    "min_profit_usdc",
    "settlement_status",
    "entry_order_id",
    "exit_order_id",
    "entry_fill_id",
    "exit_fill_id",
    "entry_strategy_name",
    "entry_model_version",
    "price_action_entry_source",
    "price_action_evidence_status",
]


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


def _fmt_time(value: Any) -> str:
    ts = parse_timestamp(value)
    if ts is None:
        return ""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _elapsed_hours(start: Any, end: Any) -> float:
    start_ts = parse_timestamp(start)
    end_ts = parse_timestamp(end)
    if start_ts is None or end_ts is None:
        return 1.0 / 60.0
    if start_ts.tzinfo is None:
        start_ts = start_ts.replace(tzinfo=timezone.utc)
    if end_ts.tzinfo is None:
        end_ts = end_ts.replace(tzinfo=timezone.utc)
    seconds = max(60.0, (end_ts.astimezone(timezone.utc) - start_ts.astimezone(timezone.utc)).total_seconds())
    return seconds / 3600.0


def _monthly_run_rate(pnl: float, start: Any, end: Any) -> float:
    return float(pnl) / _elapsed_hours(start, end) * 24.0 * 30.0


def _quote_bucket(row: dict[str, Any]) -> str:
    status = str(row.get("quote_consistency_status") or "").strip() or "unknown"
    if status == "ok":
        return "ok"
    if status == "quote_conflict":
        return "quote_conflict"
    if status.startswith("unverified_"):
        return "unverified"
    return status


def _quote_audit_action(*, quote_conflicts: int, quote_unverified: int, other_blocked: int) -> str:
    if quote_conflicts > 0:
        return "investigate token/market alias or stale exit quote; excluded P&L must not train or prove the goal"
    if quote_unverified > 0:
        return "collect independent bid/ask snapshots through the paper exit horizon before trusting this cohort"
    if other_blocked > 0:
        return "inspect unknown quote-audit status before using this cohort as proof"
    return "quote-consistent; eligible for audited paper feedback subject to existing model gates"


def _quote_audit_breakdown(rows: list[dict[str, Any]], *, key: str, limit: int = 12) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        label = str(row.get(key) or "unknown").strip() or "unknown"
        grouped[label].append(row)

    breakdown: list[dict[str, Any]] = []
    for label, group_rows in grouped.items():
        ok_rows = [row for row in group_rows if _quote_bucket(row) == "ok"]
        conflict_rows = [row for row in group_rows if _quote_bucket(row) == "quote_conflict"]
        unverified_rows = [row for row in group_rows if _quote_bucket(row) == "unverified"]
        other_blocked_rows = [
            row
            for row in group_rows
            if _quote_bucket(row) not in {"ok", "quote_conflict", "unverified"}
        ]
        raw_pnl = sum(float(safe_float(row.get("realized_pnl_usdc")) or 0.0) for row in group_rows)
        audited_pnl = sum(float(safe_float(row.get("realized_pnl_usdc")) or 0.0) for row in ok_rows)
        blocked_statuses = Counter(_quote_bucket(row) for row in group_rows if _quote_bucket(row) != "ok")
        top_status, top_count = ("none", 0) if not blocked_statuses else blocked_statuses.most_common(1)[0]
        breakdown.append(
            {
                key: label,
                "round_trips": len(group_rows),
                "quote_consistent_round_trips": len(ok_rows),
                "quote_conflict_round_trips": len(conflict_rows),
                "quote_unverified_round_trips": len(unverified_rows),
                "quote_other_blocked_round_trips": len(other_blocked_rows),
                "raw_pnl_usdc": raw_pnl,
                "audited_pnl_usdc": audited_pnl,
                "excluded_from_audit_pnl_usdc": raw_pnl - audited_pnl,
                "raw_positive_round_trips": sum(
                    1 for row in group_rows if float(safe_float(row.get("realized_pnl_usdc")) or 0.0) > 0
                ),
                "audited_positive_round_trips": sum(
                    1 for row in ok_rows if float(safe_float(row.get("realized_pnl_usdc")) or 0.0) > 0
                ),
                "top_blocker_status": top_status,
                "top_blocker_count": top_count,
                "recommended_action": _quote_audit_action(
                    quote_conflicts=len(conflict_rows),
                    quote_unverified=len(unverified_rows),
                    other_blocked=len(other_blocked_rows),
                ),
            }
        )
    breakdown.sort(
        key=lambda row: (
            int(safe_float(row.get("quote_conflict_round_trips")) or 0)
            + int(safe_float(row.get("quote_unverified_round_trips")) or 0)
            + int(safe_float(row.get("quote_other_blocked_round_trips")) or 0),
            abs(float(safe_float(row.get("excluded_from_audit_pnl_usdc")) or 0.0)),
            abs(float(safe_float(row.get("raw_pnl_usdc")) or 0.0)),
        ),
        reverse=True,
    )
    return breakdown[:limit]


def _quote_audit_status_counts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("quote_consistency_status") or "unknown")].append(row)
    counts = [
        {
            "quote_consistency_status": status,
            "round_trips": len(group_rows),
            "pnl_usdc": sum(float(safe_float(row.get("realized_pnl_usdc")) or 0.0) for row in group_rows),
            "sample_reason": str(group_rows[0].get("quote_consistency_reason") or ""),
        }
        for status, group_rows in grouped.items()
    ]
    counts.sort(key=lambda row: (str(row["quote_consistency_status"]) == "ok", -int(row["round_trips"])))
    return counts


def _cohort_from_signal(signal: dict[str, Any], fallback: str) -> str:
    for key in ("signal_cohort", "validation_cohort", "cohort"):
        value = str(signal.get(key) or "").strip()
        if value:
            return value
    return fallback


def _fill_rows(con) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in con.execute(
            """
            SELECT
              f.rowid AS fill_rowid,
              f.fill_id,
              f.order_id,
              f.created_at,
              f.market_id,
              f.token_id,
              f.side,
              f.fill_price,
              f.quantity,
              f.gross_notional_usdc,
              f.fee_usdc,
              f.slippage_usdc,
              f.fill_model,
              o.category AS order_category,
              o.correlation_key,
              o.strategy_name,
              o.model_version,
              o.prediction_timestamp,
              o.source_signal_json
            FROM fills f
            LEFT JOIN orders o ON o.order_id = f.order_id
            ORDER BY f.created_at, f.rowid
            """
        ).fetchall()
    ]


def _snapshot_rows(con, market_id: str, token_id: str, start: str, end: str) -> tuple[list[dict[str, Any]], str]:
    rows = [
        dict(row)
        for row in con.execute(
            """
            SELECT collected_at, market_id, best_bid, best_ask, midpoint, spread
            FROM market_snapshots
            WHERE market_id = ? AND token_id = ? AND collected_at >= ? AND collected_at <= ?
            ORDER BY collected_at, rowid
            """,
            (market_id, token_id, start, end),
        ).fetchall()
    ]
    if rows:
        return rows, "market_and_token"
    if not token_id:
        return [], "missing"
    rows = [
        dict(row)
        for row in con.execute(
            """
            SELECT collected_at, market_id, best_bid, best_ask, midpoint, spread
            FROM market_snapshots
            WHERE token_id = ? AND collected_at >= ? AND collected_at <= ?
            ORDER BY collected_at, rowid
            """,
            (token_id, start, end),
        ).fetchall()
    ]
    return rows, "token_only_alias" if rows else "missing"


def _snapshot_stats(con, market_id: str, token_id: str, start: str, end: str) -> dict[str, Any]:
    rows, match_mode = _snapshot_rows(con, market_id, token_id, start, end)
    if not rows:
        return {
            "snapshot_match_mode": match_mode,
            "snapshot_market_id": "",
            "observations": 0,
            "latest_time_utc": end,
            "latest_bid": "",
            "latest_ask": "",
            "latest_midpoint": "",
            "latest_spread": "",
            "max_bid": "",
            "max_bid_time_utc": "",
            "min_bid": "",
            "min_bid_time_utc": "",
        }
    bids = [(safe_float(row.get("best_bid")), row.get("collected_at")) for row in rows]
    bids = [(bid, ts) for bid, ts in bids if bid is not None]
    latest = rows[-1]
    max_bid = max(bids, key=lambda item: item[0]) if bids else ("", "")
    min_bid = min(bids, key=lambda item: item[0]) if bids else ("", "")
    return {
        "snapshot_match_mode": match_mode,
        "snapshot_market_id": latest.get("market_id", ""),
        "observations": len(rows),
        "latest_time_utc": latest.get("collected_at") or end,
        "latest_bid": latest.get("best_bid", ""),
        "latest_ask": latest.get("best_ask", ""),
        "latest_midpoint": latest.get("midpoint", ""),
        "latest_spread": latest.get("spread", ""),
        "max_bid": max_bid[0],
        "max_bid_time_utc": max_bid[1],
        "min_bid": min_bid[0],
        "min_bid_time_utc": min_bid[1],
    }


def _quote_consistency(snapshot: dict[str, Any], exit_quote: dict[str, Any], exit_price: float) -> dict[str, Any]:
    observations = int(safe_float(snapshot.get("observations")) or 0)
    latest_bid = safe_float(snapshot.get("latest_bid"))
    quote_bid = safe_float(exit_quote.get("best_bid"))
    gap = abs(quote_bid - latest_bid) if quote_bid is not None and latest_bid is not None else None
    exit_gap = abs(exit_price - latest_bid) if latest_bid is not None else None
    tolerance = 0.05
    if observations <= 0:
        return {
            "quote_consistency_status": "unverified_missing_snapshot",
            "quote_consistency_reason": "no independent market_snapshots row matched the token during the round trip",
            "exit_quote_snapshot_bid_gap": "",
            "exit_fill_snapshot_bid_gap": "",
        }
    if quote_bid is None:
        if exit_gap is not None and exit_gap <= tolerance:
            return {
                "quote_consistency_status": "ok",
                "quote_consistency_reason": "exit fill price is consistent with independent snapshot bid",
                "exit_quote_snapshot_bid_gap": exit_gap,
                "exit_fill_snapshot_bid_gap": exit_gap,
            }
        return {
            "quote_consistency_status": "unverified_missing_exit_quote",
            "quote_consistency_reason": "exit order has no broker quote payload to compare with independent snapshots",
            "exit_quote_snapshot_bid_gap": "" if exit_gap is None else exit_gap,
            "exit_fill_snapshot_bid_gap": "" if exit_gap is None else exit_gap,
        }
    if gap is not None and gap > tolerance:
        if exit_gap is not None and exit_gap <= tolerance:
            return {
                "quote_consistency_status": "ok",
                "quote_consistency_reason": (
                    "exit fill price is consistent with independent snapshot bid; "
                    "broker quote payload differed and was treated as stale metadata"
                ),
                "exit_quote_snapshot_bid_gap": gap,
                "exit_fill_snapshot_bid_gap": exit_gap,
            }
        return {
            "quote_consistency_status": "quote_conflict",
            "quote_consistency_reason": f"exit quote bid and exit fill price are not both consistent with independent snapshot bid within {tolerance:.2f}",
            "exit_quote_snapshot_bid_gap": gap,
            "exit_fill_snapshot_bid_gap": "" if exit_gap is None else exit_gap,
        }
    return {
        "quote_consistency_status": "ok",
        "quote_consistency_reason": "exit quote bid is consistent with independent snapshot bid",
        "exit_quote_snapshot_bid_gap": "" if gap is None else gap,
        "exit_fill_snapshot_bid_gap": "" if exit_gap is None else exit_gap,
    }


def _round_trip_row(con, buy: dict[str, Any], sell: dict[str, Any], quantity: float) -> dict[str, Any] | None:
    buy_quantity = safe_float(buy.get("quantity")) or 0.0
    sell_quantity = safe_float(sell.get("quantity")) or 0.0
    entry_price = safe_float(buy.get("fill_price"))
    exit_price = safe_float(sell.get("fill_price"))
    if quantity <= 0 or buy_quantity <= 0 or sell_quantity <= 0 or entry_price is None or exit_price is None:
        return None
    entry_cost_per_share = ((safe_float(buy.get("gross_notional_usdc")) or 0.0) + (safe_float(buy.get("fee_usdc")) or 0.0)) / buy_quantity
    exit_credit_per_share = ((safe_float(sell.get("gross_notional_usdc")) or 0.0) - (safe_float(sell.get("fee_usdc")) or 0.0)) / sell_quantity
    stake = entry_cost_per_share * quantity
    proceeds = exit_credit_per_share * quantity
    pnl = proceeds - stake
    roi = pnl / stake if stake > 0 else 0.0
    buy_signal = _json_payload(buy.get("source_signal_json"))
    sell_signal = _json_payload(sell.get("source_signal_json"))
    exit_quote = _json_payload(sell_signal.get("quote"))
    family = str(buy_signal.get("family") or buy_signal.get("category") or buy.get("order_category") or "unknown")
    cohort = _cohort_from_signal(buy_signal, family)
    exit_reason = str(sell_signal.get("reason") or sell.get("strategy_name") or "").replace("paper_exit_", "")
    status = "closed_take_profit" if pnl > 0 else "closed_stop_loss"
    start = _fmt_time(buy.get("created_at"))
    end = _fmt_time(sell.get("created_at"))
    snapshot = _snapshot_stats(con, str(buy.get("market_id") or ""), str(buy.get("token_id") or ""), start, end)
    consistency = _quote_consistency(snapshot, exit_quote, exit_price)
    run_rate = _monthly_run_rate(pnl, start, end)
    return {
        "source": "paper_broker",
        "target_action": "broker_paper_closed_round_trip",
        "candidate_reason": str(buy_signal.get("price_action_entry_source") or buy_signal.get("strategy_name") or buy.get("strategy_name") or "paper_broker_fill"),
        "edge_lower_bound": buy_signal.get("edge_lower_bound", buy_signal.get("edge", "")),
        "signal_cohort": cohort,
        "family": family,
        "market_slug": buy_signal.get("market_slug") or buy.get("market_id") or "",
        "question": buy_signal.get("question", ""),
        "outcome": buy_signal.get("outcome", ""),
        "token_id": buy.get("token_id", ""),
        "market_id": buy.get("market_id", ""),
        "entry_time_utc": start,
        "entry_price": entry_price,
        "entry_signal_bid": buy_signal.get("price_action_latest_bid", buy_signal.get("latest_bid", "")),
        "entry_signal_ask": buy_signal.get("price_action_latest_ask", buy_signal.get("latest_ask", "")),
        "entry_signal_spread": buy_signal.get("price_action_spread", buy_signal.get("latest_spread", "")),
        "entry_signal_timestamp": buy_signal.get("price_action_latest_time_utc", buy_signal.get("latest_time_utc", "")),
        "stake_usdc": stake,
        "quantity": quantity,
        **snapshot,
        "exit_time_utc": end,
        "exit_price": exit_price,
        "exit_quote_bid": exit_quote.get("best_bid", ""),
        "exit_quote_ask": exit_quote.get("best_ask", ""),
        "exit_quote_spread": exit_quote.get("spread", ""),
        "exit_quote_timestamp": exit_quote.get("timestamp", ""),
        "exit_quote_source": exit_quote.get("source", ""),
        **consistency,
        "exit_reason": exit_reason,
        "round_trip_status": status,
        "realized_pnl_usdc": pnl,
        "realized_roi": roi,
        "realized_monthly_run_rate_usdc": run_rate,
        "mark_pnl_usdc": pnl,
        "mark_roi": roi,
        "mark_monthly_run_rate_usdc": run_rate,
        "take_profit_return": buy_signal.get("take_profit_return", ""),
        "stop_loss_return": buy_signal.get("stop_loss_return", ""),
        "min_profit_usdc": buy_signal.get("take_profit_min_usdc", ""),
        "settlement_status": "not_settled_price_action_only",
        "entry_order_id": buy.get("order_id", ""),
        "exit_order_id": sell.get("order_id", ""),
        "entry_fill_id": buy.get("fill_id", ""),
        "exit_fill_id": sell.get("fill_id", ""),
        "entry_strategy_name": buy.get("strategy_name", ""),
        "entry_model_version": buy.get("model_version", ""),
        "price_action_entry_source": buy_signal.get("price_action_entry_source", ""),
        "price_action_evidence_status": buy_signal.get("price_action_evidence_status", ""),
    }


def _profit_baseline(cfg: EngineConfig) -> dict[str, Any]:
    settings = cfg.raw.get("profit_tracking", {}) if isinstance(cfg.raw.get("profit_tracking"), dict) else {}
    baseline_file = str(settings.get("baseline_file", "paper_profit_target_baseline.json"))
    baseline = read_json(cfg.governance_root / baseline_file, default={}) or {}
    return baseline if isinstance(baseline, dict) else {}


def _baseline_start_utc(cfg: EngineConfig) -> str:
    baseline = _profit_baseline(cfg)
    return _fmt_time(baseline.get("created_at_utc") or baseline.get("baseline_at_utc") or baseline.get("generated_at_utc"))


def _latest_account_snapshot(con) -> dict[str, Any]:
    row = con.execute(
        """
        SELECT created_at, cash_usdc, total_exposure_usdc, equity_usdc AS total_equity_usdc
        FROM portfolio_snapshots
        ORDER BY created_at DESC, rowid DESC
        LIMIT 1
        """
    ).fetchone()
    return dict(row) if row is not None else {}


def build_paper_round_trip_evidence(cfg: EngineConfig) -> dict[str, Any]:
    """Export realised paper broker buy/sell pairs as strict price-action evidence."""
    out_dir = cfg.output_root / OUTPUT_DIRNAME
    con = connect_db(cfg.database_path)
    try:
        rows = _fill_rows(con)
        account_snapshot = _latest_account_snapshot(con)
        lots: dict[tuple[str, str], deque[dict[str, Any]]] = defaultdict(deque)
        round_trips: list[dict[str, Any]] = []
        unmatched_sells = 0
        for fill in rows:
            key = (str(fill.get("market_id") or ""), str(fill.get("token_id") or ""))
            side = str(fill.get("side") or "")
            quantity = safe_float(fill.get("quantity")) or 0.0
            if side == "BUY_YES" and quantity > 0:
                lot = dict(fill)
                lot["remaining_quantity"] = quantity
                lots[key].append(lot)
            elif side == "SELL_YES" and quantity > 0:
                remaining = quantity
                while remaining > 1e-9 and lots[key]:
                    lot = lots[key][0]
                    lot_remaining = safe_float(lot.get("remaining_quantity")) or 0.0
                    matched = min(remaining, lot_remaining)
                    row = _round_trip_row(con, lot, fill, matched)
                    if row is not None:
                        round_trips.append(row)
                    lot["remaining_quantity"] = lot_remaining - matched
                    remaining -= matched
                    if (safe_float(lot.get("remaining_quantity")) or 0.0) <= 1e-9:
                        lots[key].popleft()
                if remaining > 1e-9:
                    unmatched_sells += 1
    finally:
        con.close()

    round_trips.sort(key=lambda row: (str(row.get("exit_time_utc") or ""), str(row.get("entry_time_utc") or "")))
    write_csv(out_dir / PAPER_ROUND_TRIP_FILE, round_trips, fieldnames=ROUND_TRIP_FIELDS)
    total_stake = sum(float(safe_float(row.get("stake_usdc")) or 0.0) for row in round_trips)
    total_pnl = sum(float(safe_float(row.get("realized_pnl_usdc")) or 0.0) for row in round_trips)
    positive = [row for row in round_trips if float(safe_float(row.get("realized_pnl_usdc")) or 0.0) > 0]
    supported = [row for row in round_trips if int(safe_float(row.get("observations")) or 0) > 0]
    alias_supported = [row for row in round_trips if row.get("snapshot_match_mode") == "token_only_alias"]
    quote_ok = [row for row in round_trips if row.get("quote_consistency_status") == "ok"]
    quote_conflicts = [row for row in round_trips if row.get("quote_consistency_status") == "quote_conflict"]
    quote_unverified = [row for row in round_trips if str(row.get("quote_consistency_status") or "").startswith("unverified_")]
    audited_stake = sum(float(safe_float(row.get("stake_usdc")) or 0.0) for row in quote_ok)
    audited_pnl = sum(float(safe_float(row.get("realized_pnl_usdc")) or 0.0) for row in quote_ok)
    baseline_start = _baseline_start_utc(cfg)
    baseline = _profit_baseline(cfg)
    baseline_equity = safe_float(baseline.get("baseline_equity_usdc"))
    account_equity = safe_float(account_snapshot.get("total_equity_usdc"))
    account_pnl = account_equity - baseline_equity if account_equity is not None and baseline_equity is not None else None
    baseline_rows = [
        row
        for row in round_trips
        if baseline_start and (parse_timestamp(row.get("exit_time_utc")) or parse_timestamp("1970-01-01T00:00:00Z")) >= (parse_timestamp(baseline_start) or parse_timestamp("9999-01-01T00:00:00Z"))
    ]
    baseline_stake = sum(float(safe_float(row.get("stake_usdc")) or 0.0) for row in baseline_rows)
    baseline_pnl = sum(float(safe_float(row.get("realized_pnl_usdc")) or 0.0) for row in baseline_rows)
    baseline_positive = [row for row in baseline_rows if float(safe_float(row.get("realized_pnl_usdc")) or 0.0) > 0]
    audited_baseline_rows = [row for row in baseline_rows if row.get("quote_consistency_status") == "ok"]
    baseline_quote_conflicts = [row for row in baseline_rows if row.get("quote_consistency_status") == "quote_conflict"]
    baseline_quote_unverified = [
        row
        for row in baseline_rows
        if str(row.get("quote_consistency_status") or "").startswith("unverified_")
    ]
    audited_baseline_stake = sum(float(safe_float(row.get("stake_usdc")) or 0.0) for row in audited_baseline_rows)
    audited_baseline_pnl = sum(float(safe_float(row.get("realized_pnl_usdc")) or 0.0) for row in audited_baseline_rows)
    summary = {
        "status": "computed",
        "generated_at_utc": now_utc(),
        "source": "paper_broker",
        "closed_round_trips": len(round_trips),
        "positive_round_trips": len(positive),
        "negative_round_trips": len(round_trips) - len(positive),
        "realized_pnl_usdc": total_pnl,
        "realized_roi": total_pnl / total_stake if total_stake > 0 else 0.0,
        "stake_usdc": total_stake,
        "snapshot_supported_round_trips": len(supported),
        "snapshot_alias_supported_round_trips": len(alias_supported),
        "snapshot_missing_round_trips": len(round_trips) - len(supported),
        "quote_consistent_round_trips": len(quote_ok),
        "quote_conflict_round_trips": len(quote_conflicts),
        "quote_unverified_round_trips": len(quote_unverified),
        "audited_realized_pnl_usdc": audited_pnl,
        "audited_realized_roi": audited_pnl / audited_stake if audited_stake > 0 else 0.0,
        "audited_stake_usdc": audited_stake,
        "baseline_start_utc": baseline_start,
        "baseline_closed_round_trips": len(baseline_rows),
        "baseline_positive_round_trips": len(baseline_positive),
        "baseline_realized_pnl_usdc": baseline_pnl,
        "baseline_realized_roi": baseline_pnl / baseline_stake if baseline_stake > 0 else 0.0,
        "baseline_stake_usdc": baseline_stake,
        "baseline_quote_conflict_round_trips": len(baseline_quote_conflicts),
        "baseline_quote_unverified_round_trips": len(baseline_quote_unverified),
        "audited_baseline_closed_round_trips": len(audited_baseline_rows),
        "audited_baseline_realized_pnl_usdc": audited_baseline_pnl,
        "audited_baseline_realized_roi": audited_baseline_pnl / audited_baseline_stake if audited_baseline_stake > 0 else 0.0,
        "audited_baseline_stake_usdc": audited_baseline_stake,
        "quote_audit_status_counts": _quote_audit_status_counts(round_trips),
        "quote_audit_by_cohort": _quote_audit_breakdown(round_trips, key="signal_cohort"),
        "quote_audit_by_family": _quote_audit_breakdown(round_trips, key="family"),
        "baseline_quote_audit_by_cohort": _quote_audit_breakdown(baseline_rows, key="signal_cohort"),
        "baseline_quote_audit_by_family": _quote_audit_breakdown(baseline_rows, key="family"),
        "account_snapshot_time_utc": account_snapshot.get("created_at", ""),
        "account_baseline_equity_usdc": baseline_equity,
        "account_equity_usdc": account_equity,
        "account_pnl_since_baseline_usdc": account_pnl,
        "unmatched_sell_fills": unmatched_sells,
        "output_file": str(out_dir / PAPER_ROUND_TRIP_FILE),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
        "notes": [
            "Realised paper broker fills are used only as feedback evidence.",
            "Round-trip totals cover broker fill history; baseline_* fields isolate fills closed after the active profit-target baseline.",
            "account_pnl_since_baseline_usdc is the dashboard-compatible equity delta and can differ from baseline_realized_pnl_usdc if the clean baseline was created after earlier fills or account adjustments.",
            "Snapshot fields show whether each fill pair is backed by exact market/token quotes or token-only alias quote support.",
            "audited_* P&L excludes paper rows where the broker exit quote conflicts with the independent snapshot trail.",
            "The price-action model still applies strict executable bid-vs-entry-ask label hurdles before treating a row as positive.",
        ],
    }
    write_json(out_dir / SUMMARY_JSON, summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Export paper broker fills as price-action round-trip evidence.")
    parser.add_argument("--config", default="polymarket_predictive_config.example.yaml")
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    print(json.dumps(build_paper_round_trip_evidence(cfg), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
