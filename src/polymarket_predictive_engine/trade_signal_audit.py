from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from .config import EngineConfig, load_config
from .utils import boolish, now_utc, read_csv_rows, read_json, safe_float, write_json

OUTPUT_FILENAME = "trade_signal_audit.json"


def _num(value: Any, default: float = 0.0) -> float:
    parsed = safe_float(value)
    return default if parsed is None else float(parsed)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    text = _text(value)
    if not text:
        return {}
    try:
        decoded = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _weighted_price(rows: list[dict[str, str]]) -> float:
    quantity = sum(_num(row.get("quantity")) for row in rows)
    if quantity <= 0:
        return 0.0
    notional = sum(_num(row.get("gross_notional_usdc")) for row in rows)
    return notional / quantity


def _signal_trade_thesis(signal: dict[str, Any]) -> dict[str, Any]:
    """Translate a paper signal into executable trading math.

    The bot buys at the ask and can only exit at the bid. A signal is not
    tradeable unless the expected price move is large enough to overcome this
    spread plus any configured profit target.
    """
    ask = _num(signal.get("executable_price"))
    bid = _num(signal.get("price_action_latest_bid") or signal.get("market_price"))
    spread = _num(signal.get("spread"), max(0.0, ask - bid) if ask and bid else 0.0)
    max_stake = _num(signal.get("max_stake_usdc"), _num(signal.get("stake_usdc"), 0.0))
    estimated_quantity = max_stake / ask if ask > 0 and max_stake > 0 else 0.0
    take_profit_return = _num(signal.get("take_profit_return"))
    take_profit_min = _num(signal.get("take_profit_min_usdc"))
    stop_loss_return = _num(signal.get("stop_loss_return"))
    required_by_return = ask * (1.0 + take_profit_return) if ask > 0 else 0.0
    required_by_min_profit = (
        ask + (take_profit_min / estimated_quantity)
        if ask > 0 and estimated_quantity > 0 and take_profit_min > 0
        else required_by_return
    )
    required_exit_bid = max(required_by_return, required_by_min_profit)
    break_even_bid = ask if ask > 0 else 0.0
    stop_loss_bid = ask * (1.0 - stop_loss_return) if ask > 0 else 0.0
    current_bid_gap_to_profit = required_exit_bid - bid if required_exit_bid and bid else 0.0
    spread_cost_usdc = estimated_quantity * spread if estimated_quantity > 0 else 0.0
    return {
        "market_slug": signal.get("market_slug") or signal.get("market_id", ""),
        "outcome": signal.get("outcome", ""),
        "signal_cohort": signal.get("signal_cohort", ""),
        "entry_ask": ask,
        "current_exit_bid": bid,
        "spread": spread,
        "relative_spread": _num(signal.get("relative_spread"), spread / ask if ask > 0 else 0.0),
        "max_stake_usdc": max_stake,
        "estimated_quantity": estimated_quantity,
        "estimated_spread_cost_usdc": spread_cost_usdc,
        "break_even_exit_bid": break_even_bid,
        "required_take_profit_bid": required_exit_bid,
        "current_bid_gap_to_profit": current_bid_gap_to_profit,
        "stop_loss_bid": stop_loss_bid,
        "take_profit_return": take_profit_return,
        "take_profit_min_usdc": take_profit_min,
        "stop_loss_return": stop_loss_return,
        "max_hold_minutes_before_exit": _num(signal.get("max_hold_minutes_before_exit")),
        "evidence_status": signal.get("price_action_evidence_status", ""),
        "entry_source": signal.get("price_action_entry_source", ""),
        "edge_proxy": signal.get("edge", ""),
        "tradeable_interpretation": (
            "Buy at ask only if the cohort evidence supports a move to the required exit bid before the stop or horizon."
        ),
    }


def _query_terms(query: str) -> list[str]:
    text = query.lower().strip()
    if not text:
        return []
    if "btc" in text or "bitcoin" in text:
        return ["btc", "bitcoin"]
    if "sol" in text or "solana" in text:
        return ["sol", "solana"]
    if "eth" in text or "ethereum" in text:
        return ["eth", "ethereum"]
    if "xrp" in text or "ripple" in text:
        return ["xrp", "ripple"]
    if "world" in text or "cup" in text:
        return ["world", "cup", "mexico", "portugal"]
    if "tennis" in text:
        return ["tennis", "sinner", "alcaraz"]
    return [part for part in text.replace("_", " ").replace("-", " ").split() if part]


def _matches_target(row: dict[str, Any], target: dict[str, Any]) -> bool:
    cohort = _text(target.get("cohort")).lower()
    query = _text(target.get("recommended_collection_query")).lower()
    terms = _query_terms(query)
    row_text = " ".join(
        [
            _text(row.get("signal_cohort")),
            _text(row.get("family")),
            _text(row.get("market_slug")),
            _text(row.get("question")),
            _text(row.get("outcome")),
        ]
    ).lower()
    if cohort and cohort in row_text:
        return True
    return bool(terms and any(term in row_text for term in terms))


def _is_executable_candidate(row: dict[str, Any]) -> bool:
    ask = safe_float(row.get("latest_ask"))
    bid = safe_float(row.get("latest_bid"))
    return bool(str(row.get("round_trip_status") or "") == "open_marked" and ask is not None and bid is not None and 0 < ask < 1 and 0 < bid < 1)


def _paper_confirmation_target_status(
    targets: list[dict[str, Any]],
    candidate_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    statuses: list[dict[str, Any]] = []
    for target in targets:
        matches = [row for row in candidate_rows if _matches_target(row, target)]
        executable = [row for row in matches if _is_executable_candidate(row)]
        statuses.append(
            {
                "cohort": target.get("cohort", ""),
                "recommended_collection_query": target.get("recommended_collection_query", ""),
                "source": target.get("source", ""),
                "action": target.get("action", ""),
                "forward_shadow_pnl_usdc": _num(target.get("forward_shadow_pnl_usdc")),
                "forward_shadow_roi": _num(target.get("forward_shadow_roi")),
                "monthly_run_rate_usdc": _num(target.get("monthly_run_rate_usdc")),
                "trusted_for_goal": target.get("trusted_for_goal"),
                "current_candidate_rows": len(matches),
                "current_executable_rows": len(executable),
                "missing_fresh_candidate": len(executable) == 0,
                "current_candidate_preview": [
                    {
                        "signal_cohort": row.get("signal_cohort", ""),
                        "market_slug": row.get("market_slug", ""),
                        "outcome": row.get("outcome", ""),
                        "round_trip_status": row.get("round_trip_status", ""),
                        "latest_bid": row.get("latest_bid", ""),
                        "latest_ask": row.get("latest_ask", ""),
                    }
                    for row in executable[:5]
                ],
            }
        )
    return statuses


def _exit_diagnosis(exit_reason: str, realised_pnl: float) -> str:
    if exit_reason == "fixed_horizon":
        if realised_pnl < 0:
            return (
                "The probe did not reprice enough before its max-hold horizon; closing at bid records "
                "the failed signal instead of hiding the loss."
            )
        return "The probe reached its max-hold horizon and closed with positive bid/ask P&L."
    if exit_reason == "stop_loss":
        return "The bid fell through the configured stop-loss level for this signal."
    if exit_reason == "hard_stop_loss":
        return "The bid breached the portfolio hard-stop guardrail."
    if exit_reason == "take_profit":
        return "The bid reached the configured take-profit level after spread costs."
    if exit_reason == "alpha_edge_deteriorated":
        return "The model edge deteriorated enough that the normal paper exit rule closed the position."
    return "Closed by the paper broker exit policy."


def _recent_exit_rows(fills: list[dict[str, str]], orders: list[dict[str, str]], positions: list[dict[str, str]]) -> list[dict[str, Any]]:
    orders_by_id = {row.get("order_id", ""): row for row in orders}
    position_by_key = {(row.get("market_id", ""), row.get("token_id", "")): row for row in positions}
    grouped: dict[tuple[str, str], dict[str, list[dict[str, str]]]] = defaultdict(lambda: {"buys": [], "sells": []})
    for fill in fills:
        key = (fill.get("market_id", ""), fill.get("token_id", ""))
        if fill.get("side") == "BUY_YES":
            grouped[key]["buys"].append(fill)
        elif fill.get("side") == "SELL_YES":
            grouped[key]["sells"].append(fill)

    exits: list[dict[str, Any]] = []
    for key, rows in grouped.items():
        sells = sorted(rows["sells"], key=lambda row: row.get("created_at", ""))
        buys = sorted(rows["buys"], key=lambda row: row.get("created_at", ""))
        if not sells:
            continue
        buy_cost = sum(_num(row.get("gross_notional_usdc")) + _num(row.get("fee_usdc")) for row in buys)
        sell_credit = sum(_num(row.get("gross_notional_usdc")) - _num(row.get("fee_usdc")) for row in sells)
        quantity = sum(_num(row.get("quantity")) for row in sells)
        realised = sell_credit - buy_cost
        last_sell = sells[-1]
        first_buy = buys[0] if buys else {}
        sell_order = orders_by_id.get(last_sell.get("order_id", ""), {})
        buy_order = orders_by_id.get(first_buy.get("order_id", ""), {})
        source_signal = _json_payload(buy_order.get("source_signal_json"))
        exit_reason = _text(sell_order.get("strategy_name")).removeprefix("paper_exit_")
        entry_price = _weighted_price(buys)
        exit_price = _weighted_price(sells)
        break_even_bid = buy_cost / quantity if quantity > 0 else 0.0
        exits.append(
            {
                "market_id": key[0],
                "market_slug": source_signal.get("market_slug") or key[0],
                "outcome": source_signal.get("outcome", ""),
                "signal_cohort": source_signal.get("signal_cohort", ""),
                "entry_source": source_signal.get("price_action_entry_source", ""),
                "evidence_status": source_signal.get("price_action_evidence_status", ""),
                "first_buy_at": first_buy.get("created_at", ""),
                "last_sell_at": last_sell.get("created_at", ""),
                "entry_price": entry_price,
                "exit_price": exit_price,
                "break_even_exit_bid": break_even_bid,
                "exit_bid_gap_to_break_even": exit_price - break_even_bid,
                "buy_cost_usdc": buy_cost,
                "sell_credit_usdc": sell_credit,
                "realised_pnl_usdc": realised,
                "loss_making_exit": realised < 0,
                "exit_reason": exit_reason,
                "max_hold_minutes_before_exit": _num(source_signal.get("max_hold_minutes_before_exit")),
                "take_profit_return": _num(source_signal.get("take_profit_return")),
                "stop_loss_return": _num(source_signal.get("stop_loss_return")),
                "position_status": position_by_key.get(key, {}).get("status", ""),
                "diagnosis": _exit_diagnosis(exit_reason, realised),
            }
        )
    exits.sort(key=lambda row: row.get("last_sell_at", ""), reverse=True)
    return exits


def build_trade_signal_audit(cfg: EngineConfig) -> dict[str, Any]:
    price_root = cfg.output_root / "polymarket_price_action"
    portfolio_root = cfg.output_root / "polymarket_portfolio"
    signals = read_csv_rows(price_root / "price_action_paper_signals.csv")
    rejections = read_csv_rows(price_root / "price_action_paper_rejections.csv")
    round_trip_rows = read_csv_rows(price_root / "price_action_scout_round_trip_evidence.csv")
    signal_summary = read_json(price_root / "price_action_paper_signal_summary.json", default={}) or {}
    if not isinstance(signal_summary, dict):
        signal_summary = {}
    feedback = read_json(cfg.governance_root / "price_action_feedback.json", default={}) or {}
    if not isinstance(feedback, dict):
        feedback = {}
    confirmation_targets = [
        row
        for row in feedback.get("paper_confirmation_preview", [])
        if isinstance(row, dict) and boolish(row.get("trusted_for_goal"))
    ] if isinstance(feedback.get("paper_confirmation_preview"), list) else []
    confirmation_target_status = _paper_confirmation_target_status(confirmation_targets, round_trip_rows)

    exits = _recent_exit_rows(
        read_csv_rows(portfolio_root / "paper_fills.csv"),
        read_csv_rows(portfolio_root / "paper_orders.csv"),
        read_csv_rows(portfolio_root / "positions.csv"),
    )
    rejection_counts = Counter(_text(row.get("rejection_reason")) or "unknown" for row in rejections)
    loss_exits = [row for row in exits if row.get("loss_making_exit")]
    current_signal_theses = [_signal_trade_thesis(signal) for signal in signals]

    if current_signal_theses:
        verdict = "current_signals_require_broker_or_manual_review"
        next_action = "Review entry ask, required exit bid, spread, horizon, and cohort evidence before paper entry."
    elif any(row.get("missing_fresh_candidate") for row in confirmation_target_status):
        verdict = "trusted_edge_missing_fresh_candidate"
        missing_queries = [
            _text(row.get("recommended_collection_query"))
            for row in confirmation_target_status
            if row.get("missing_fresh_candidate") and _text(row.get("recommended_collection_query"))
        ]
        query_text = ", ".join(dict.fromkeys(missing_queries))
        next_action = (
            f"Collect fresh websocket/price-action candidates for: {query_text}."
            if query_text
            else "Collect fresh websocket/price-action candidates for trusted confirmation cohorts."
        )
    elif confirmation_target_status:
        verdict = "trusted_edge_current_candidates_blocked_by_gate"
        next_action = (
            "Do not enter; fresh executable candidates exist for trusted confirmation cohorts, "
            "but none has passed the governed signal thesis. Use rejection reasons and recent exits "
            "to tighten model, cohort, spread, and risk gates rather than forcing turnover."
        )
    elif rejections:
        verdict = "no_current_trade_signal"
        next_action = "Do not enter; current candidates do not satisfy the governed evidence gate."
    else:
        verdict = "waiting_for_candidates"
        next_action = "Collect fresh websocket candidates before evaluating new paper entries."
    if loss_exits:
        next_action += " Recent losing exits should tighten the signal thesis before any sizing increase."

    payload = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
        "verdict": verdict,
        "next_action": next_action,
        "signal_file_rows": len(signals),
        "summary_signal_rows": signal_summary.get("signals"),
        "signal_summary_mismatch": safe_float(signal_summary.get("signals")) is not None
        and int(_num(signal_summary.get("signals"))) != len(signals),
        "rejection_file_rows": len(rejections),
        "rejection_reason_counts": [
            {"reason": reason, "count": count} for reason, count in rejection_counts.most_common()
        ],
        "current_signal_theses": current_signal_theses[:25],
        "paper_confirmation_targets": confirmation_target_status,
        "missing_confirmation_target_count": sum(1 for row in confirmation_target_status if row.get("missing_fresh_candidate")),
        "missing_confirmation_queries": [
            row.get("recommended_collection_query")
            for row in confirmation_target_status
            if row.get("missing_fresh_candidate") and row.get("recommended_collection_query")
        ],
        "rejection_preview": rejections[:25],
        "recent_exit_count": len(exits),
        "recent_loss_exit_count": len(loss_exits),
        "recent_realised_pnl_usdc": sum(_num(row.get("realised_pnl_usdc")) for row in exits),
        "recent_exit_preview": exits[:25],
        "trading_principles": [
            "Entry uses executable ask; exit uses executable bid.",
            "Spread is an immediate hurdle, not edge.",
            "A price-action signal must specify the bid needed for take-profit, stop-loss, and max holding time.",
            "Fixed-horizon losses are evidence that the repricing thesis failed in that window.",
            "No paper entry should run when the signal CSV has zero rows.",
        ],
    }
    write_json(cfg.governance_root / OUTPUT_FILENAME, payload)
    return payload


def run(config_path: str = "polymarket_predictive_config.example.yaml") -> dict[str, Any]:
    cfg = load_config(config_path)
    return build_trade_signal_audit(cfg)
