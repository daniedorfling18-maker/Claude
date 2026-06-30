from __future__ import annotations

from typing import Any

from .config import EngineConfig, load_config
from .utils import now_utc, read_csv_rows, read_json, safe_float, write_json

OUTPUT_FILENAME = "price_action_feedback.json"


def _num(value: Any, default: float = 0.0) -> float:
    parsed = safe_float(value)
    return default if parsed is None else float(parsed)


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "approved", "promoted"}


def _query_from_text(text: str) -> str:
    lowered = text.lower()
    if "near_miss_learning|unknown" in lowered or lowered.startswith("unknown"):
        return ""
    if "btc" in lowered or "bitcoin" in lowered:
        return "btc updown" if "updown" in lowered else "bitcoin"
    if "xrp" in lowered or "ripple" in lowered:
        return "xrp updown" if "updown" in lowered else "xrp"
    if "sol" in lowered or "solana" in lowered:
        return "solana updown" if "updown" in lowered else "solana"
    if "eth" in lowered or "ethereum" in lowered:
        return "eth updown" if "updown" in lowered else "ethereum"
    if "tennis" in lowered:
        return "tennis"
    if "worldcup" in lowered or "world_cup" in lowered or "world cup" in lowered or "sports_other" in lowered:
        return "world cup"
    if "crypto" in lowered:
        return "bitcoin"
    return ""


def _source_priority(source: str) -> int:
    priorities = {
        "paper_broker_forward": 6,
        "microstructure_exit_policy": 5,
        "microstructure_family": 4,
        "microstructure": 3,
        "price_action_scout": 2,
        "strategy_v2_round_trip": 1,
    }
    return priorities.get(source, 0)


def _cohort_priority(row: dict[str, Any]) -> float:
    monthly = _num(row.get("monthly_run_rate_usdc"))
    realized_pnl = _num(row.get("realized_pnl_usdc"))
    realized_roi = _num(row.get("realized_roi"))
    mark_pnl = _num(row.get("total_mark_pnl_usdc"))
    mark_roi = _num(row.get("mark_roi"))
    closed = _num(row.get("closed_trades"))
    validation_trades = _num(row.get("validation_trades"))
    win_rate = _num(row.get("win_rate") or row.get("validation_win_rate"))
    value = (
        _source_priority(str(row.get("source") or "")) * 2.0
        + min(max(monthly, -100.0), 500.0) * 0.02
        + min(max(realized_pnl, -20.0), 50.0) * 0.6
        + min(max(mark_pnl, -20.0), 50.0) * 0.25
        + min(max(realized_roi, -1.0), 2.0) * 8.0
        + min(max(mark_roi, -1.0), 2.0) * 2.0
        + closed * 0.8
        + validation_trades * 0.5
        + win_rate * 2.0
    )
    if _bool(row.get("promotion_ready")):
        value += 50.0
    elif str(row.get("action") or "") == "collect_more_positive_price_action_evidence":
        value += 12.0
    elif str(row.get("action") or "") == "suppress_until_new_thesis":
        value -= 25.0
    return value


def _strategy_or_scout_row(row: dict[str, str], *, source: str, min_closed: int) -> dict[str, Any]:
    cohort = str(row.get("signal_cohort") or "unknown").strip() or "unknown"
    family = str(row.get("family") or "unknown").strip() or "unknown"
    closed = int(_num(row.get("closed_trades")))
    open_trades = int(_num(row.get("open_trades")))
    realized_pnl = _num(row.get("realized_pnl_usdc"))
    realized_roi = _num(row.get("realized_roi"))
    monthly = _num(row.get("realized_monthly_run_rate_usdc"))
    mark_pnl = _num(row.get("total_mark_pnl_usdc"))
    mark_roi = _num(row.get("mark_roi"))
    review_ready = _bool(row.get("price_action_review_candidate"))
    has_positive_closed = closed > 0 and realized_pnl > 0 and realized_roi > 0
    has_negative_closed = closed >= min_closed and (realized_pnl <= 0 or realized_roi <= 0)
    has_negative_mark = closed == 0 and open_trades > 0 and mark_pnl < 0 and mark_roi < 0

    if review_ready:
        action = "candidate_for_price_action_paper_bridge"
        reason = "Closed bid/ask round-trip cohort evidence cleared the configured review gates."
    elif has_positive_closed:
        action = "collect_more_positive_price_action_evidence"
        reason = "Closed bid/ask round trips are positive, but promotion gates still need more evidence."
    elif has_negative_closed:
        action = "suppress_until_new_thesis"
        reason = "Closed bid/ask round trips are negative or below ROI after spread costs."
    elif has_negative_mark:
        action = "monitor_but_do_not_expand"
        reason = "Open bid/ask marks are currently negative; avoid expanding until marks improve or trades close."
    else:
        action = "collect_bid_ask_marks"
        reason = "The cohort still needs executable bid/ask observations and closed price-action outcomes."

    text = " ".join([cohort, family, str(row.get("status") or ""), str(row.get("reason") or "")])
    return {
        "source": source,
        "cohort": cohort,
        "family": family,
        "recommended_collection_query": _query_from_text(text),
        "promotion_ready": review_ready,
        "action": action,
        "reason": reason,
        "source_status": row.get("status", ""),
        "source_reason": row.get("reason", ""),
        "candidates": int(_num(row.get("candidates"))),
        "closed_trades": closed,
        "open_trades": open_trades,
        "win_rate": _num(row.get("win_rate")),
        "realized_pnl_usdc": realized_pnl,
        "realized_roi": realized_roi,
        "monthly_run_rate_usdc": monthly,
        "total_mark_pnl_usdc": mark_pnl,
        "mark_roi": mark_roi,
        "evidence_type": "settlement_independent_bid_ask_round_trip",
    }


def _microstructure_row(row: dict[str, str], *, min_validation_trades: int, source: str = "microstructure") -> dict[str, Any]:
    rule_family = str(row.get("rule_family") or "unknown").strip() or "unknown"
    rule_id = str(row.get("rule_id") or rule_family).strip() or rule_family
    market_family = str(row.get("market_family") or "").strip()
    signal_cohort = str(row.get("signal_cohort") or "").strip()
    if not signal_cohort:
        signal_cohort = (
            f"price_action_microstructure|{market_family}|{rule_family}"
            if market_family
            else f"price_action_microstructure|{rule_family}"
        )
    validation_trades = int(_num(row.get("validation_trades")))
    validation_pnl = _num(row.get("validation_pnl_usdc"))
    validation_roi = _num(row.get("validation_roi"))
    validation_pass = _bool(row.get("validation_pass"))
    has_positive_validation = validation_trades > 0 and validation_pnl > 0 and validation_roi > 0
    has_negative_validation = validation_trades >= min_validation_trades and validation_pnl <= 0

    if validation_pass:
        action = "candidate_for_forward_shadow_microstructure"
        reason = "Chronological validation cleared the bid/ask microstructure rule gates."
    elif has_positive_validation:
        action = "collect_more_positive_price_action_evidence"
        reason = "Validation split is positive but still below one or more promotion thresholds."
    elif has_negative_validation:
        action = "suppress_until_new_thesis"
        reason = "Validation split did not overcome executable bid/ask costs."
    else:
        action = "collect_more_websocket_microstructure_evidence"
        reason = "Rule needs more validation observations before it can be trusted."

    text = " ".join([market_family, signal_cohort, rule_id, rule_family, str(row.get("status") or ""), str(row.get("reason") or "")])
    return {
        "source": source,
        "cohort": signal_cohort,
        "family": market_family or rule_family,
        "rule_id": rule_id,
        "recommended_collection_query": _query_from_text(text),
        "promotion_ready": validation_pass,
        "action": action,
        "reason": reason,
        "source_status": row.get("status", ""),
        "source_reason": row.get("reason", ""),
        "total_trades": int(_num(row.get("total_trades"))),
        "train_trades": int(_num(row.get("train_trades"))),
        "validation_trades": validation_trades,
        "validation_win_rate": _num(row.get("validation_win_rate")),
        "validation_pnl_usdc": validation_pnl,
        "validation_roi": validation_roi,
        "exit_policy_id": row.get("exit_policy_id", ""),
        "take_profit_return": _num(row.get("take_profit_return")),
        "stop_loss_return": _num(row.get("stop_loss_return")),
        "max_forward_observations": int(_num(row.get("max_forward_observations"))),
        "min_profit_usdc": _num(row.get("min_profit_usdc")),
        "monthly_run_rate_usdc": 0.0,
        "realized_pnl_usdc": validation_pnl,
        "realized_roi": validation_roi,
        "total_mark_pnl_usdc": validation_pnl,
        "mark_roi": validation_roi,
        "evidence_type": "settlement_independent_microstructure_validation",
    }


def _paper_broker_forward_row(row: dict[str, str], *, min_closed: int) -> dict[str, Any]:
    """Convert broker paper P&L into the same feedback language as shadow marks.

    This is the most important settlement-independent signal for the trading
    objective: it measures whether the system could buy at the observed paper
    entry and later sell/mark the position at a better executable level.
    """
    cohort = str(row.get("signal_cohort") or "unknown").strip() or "unknown"
    closed = int(_num(row.get("settled_fills") or row.get("sell_fills")))
    open_trades = int(_num(row.get("open_positions")))
    buy_fills = int(_num(row.get("buy_fills") or row.get("paper_buy_fills")))
    pnl = _num(row.get("total_pnl_usdc") or row.get("paper_total_pnl_usdc"))
    roi = _num(row.get("roi") or row.get("paper_roi"))
    monthly = _num(row.get("monthly_run_rate_usdc"))
    promoted = _bool(row.get("promoted"))
    probationary = _bool(row.get("probationary"))
    has_positive_forward = buy_fills > 0 and pnl > 0 and roi > 0
    has_negative_closed = closed >= min_closed and (pnl <= 0 or roi <= 0)
    has_negative_open_mark = closed == 0 and open_trades > 0 and pnl < 0 and roi < 0

    if promoted:
        action = "candidate_for_live_promotion_review"
        reason = (
            "Forward paper/shadow P&L cleared the cohort promotion gate; live trading still needs "
            "the separate live approvals and kill-switch gates."
        )
    elif probationary:
        action = "continue_probationary_paper"
        reason = "Forward paper evidence is positive enough for probationary paper sizing, not live trading."
    elif has_positive_forward:
        action = "collect_more_positive_price_action_evidence"
        reason = "Forward paper buy/sell or mark-to-market P&L is positive; collect more live paper evidence."
    elif has_negative_closed:
        action = "suppress_until_new_thesis"
        reason = "Forward paper closed trades did not overcome bid/ask costs."
    elif has_negative_open_mark:
        action = "monitor_but_do_not_expand"
        reason = "Open forward paper marks are negative; avoid expanding until marks improve or exits close."
    else:
        action = "collect_forward_paper_evidence"
        reason = "Cohort needs forward paper fills, exits, and marks before trust can increase."

    text = " ".join([cohort, str(row.get("promotion_reason") or "")])
    return {
        "source": "paper_broker_forward",
        "cohort": cohort,
        "family": str(row.get("family") or "").strip() or "paper_forward",
        "recommended_collection_query": _query_from_text(text),
        "promotion_ready": promoted or probationary,
        "probationary": probationary,
        "promoted": promoted,
        "action": action,
        "reason": reason,
        "source_status": "promoted" if promoted else "probationary" if probationary else "paper_forward",
        "source_reason": row.get("promotion_reason", ""),
        "buy_fills": buy_fills,
        "closed_trades": closed,
        "open_trades": open_trades,
        "paper_sell_fills": int(_num(row.get("sell_fills") or row.get("paper_sell_fills"))),
        "shadow_fills": int(_num(row.get("shadow_fills"))),
        "shadow_sell_fills": int(_num(row.get("shadow_sell_fills"))),
        "realized_pnl_usdc": pnl,
        "realized_roi": roi,
        "monthly_run_rate_usdc": monthly,
        "total_mark_pnl_usdc": pnl,
        "mark_roi": roi,
        "forward_paper_pnl_usdc": pnl,
        "forward_paper_roi": roi,
        "promotion_ready_score": int(_num(row.get("promotion_ready_score"))),
        "promotion_ready_checks": int(_num(row.get("promotion_ready_checks"))),
        "promotion_readiness": row.get("promotion_readiness", ""),
        "promotion_evidence_source": row.get("promotion_evidence_source", ""),
        "metadata_valid": row.get("metadata_valid", ""),
        "metadata_blocker": row.get("metadata_blocker", ""),
        "evidence_type": "forward_paper_bid_ask_trade_pnl",
    }


def _append_unique(items: list[str], value: str) -> None:
    value = value.strip()
    if value and value not in items:
        items.append(value)


def build_price_action_feedback(cfg: EngineConfig) -> dict[str, Any]:
    """Consolidate settlement-independent price-action evidence into a feedback loop.

    This is the bridge between "we observed price movement" and "what should the
    local research loop do next?". It is deliberately read-only: no paper/live
    trading is invoked and no thresholds are weakened.
    """
    price_root = cfg.output_root / "polymarket_price_action"
    strategy_root = cfg.output_root / "polymarket_strategy_v2"
    governance_root = cfg.governance_root
    settings = cfg.raw.get("price_action_feedback", {}) if isinstance(cfg.raw.get("price_action_feedback"), dict) else {}
    scout_settings = cfg.raw.get("price_action_scout", {}) if isinstance(cfg.raw.get("price_action_scout"), dict) else {}
    strategy_settings = cfg.raw.get("strategy_v2", {}) if isinstance(cfg.raw.get("strategy_v2"), dict) else {}
    micro_settings = cfg.raw.get("price_action_microstructure", {}) if isinstance(cfg.raw.get("price_action_microstructure"), dict) else {}
    min_closed = int(
        _num(settings.get("min_closed_trades_for_suppression"))
        or _num(scout_settings.get("min_closed_trades"))
        or _num(strategy_settings.get("round_trip_min_closed_trades"))
        or 5
    )
    min_validation_trades = int(
        _num(settings.get("min_validation_trades_for_suppression"))
        or _num(micro_settings.get("min_validation_trades"))
        or 3
    )
    target_monthly_profit = _num(
        cfg.raw.get("profit_tracking", {}).get("target_monthly_profit_usdc")
        if isinstance(cfg.raw.get("profit_tracking"), dict)
        else None,
        100.0,
    )

    cohorts: list[dict[str, Any]] = []
    for row in read_csv_rows(strategy_root / "strategy_v2_round_trip_cohort_evidence.csv"):
        cohorts.append(_strategy_or_scout_row(row, source="strategy_v2_round_trip", min_closed=min_closed))
    for row in read_csv_rows(price_root / "price_action_scout_cohort_evidence.csv"):
        cohorts.append(_strategy_or_scout_row(row, source="price_action_scout", min_closed=min_closed))
    for row in read_csv_rows(price_root / "microstructure_rule_evidence.csv"):
        cohorts.append(_microstructure_row(row, min_validation_trades=min_validation_trades))
    for row in read_csv_rows(price_root / "microstructure_family_rule_evidence.csv"):
        cohorts.append(_microstructure_row(row, min_validation_trades=min_validation_trades, source="microstructure_family"))
    for row in read_csv_rows(price_root / "microstructure_exit_policy_evidence.csv"):
        cohorts.append(_microstructure_row(row, min_validation_trades=min_validation_trades, source="microstructure_exit_policy"))
    for row in read_csv_rows(governance_root / "signal_cohort_pnl.csv"):
        cohorts.append(_paper_broker_forward_row(row, min_closed=min_closed))

    for row in cohorts:
        row["priority_score"] = round(_cohort_priority(row), 4)

    cohorts.sort(key=lambda row: _num(row.get("priority_score")), reverse=True)
    promotion_candidates = [row for row in cohorts if _bool(row.get("promotion_ready"))]
    positive_collect = [row for row in cohorts if str(row.get("action") or "") == "collect_more_positive_price_action_evidence"]
    suppressed = [row for row in cohorts if str(row.get("action") or "") == "suppress_until_new_thesis"]
    forward_paper = [row for row in cohorts if str(row.get("source") or "") == "paper_broker_forward"]
    positive_forward_paper = [
        row
        for row in forward_paper
        if _num(row.get("forward_paper_pnl_usdc")) > 0 and _num(row.get("forward_paper_roi")) > 0
    ]

    collection_queries: list[str] = []
    for row in promotion_candidates + positive_collect:
        _append_unique(collection_queries, str(row.get("recommended_collection_query") or ""))
    if not collection_queries:
        # Fallback stays broad and non-promotional when there is no positive price-action evidence yet.
        for query in ("world cup", "tennis", "bitcoin", "ethereum"):
            _append_unique(collection_queries, query)

    suppressed_queries: list[str] = []
    for row in suppressed:
        _append_unique(suppressed_queries, str(row.get("recommended_collection_query") or ""))

    best_run_rate = max((_num(row.get("monthly_run_rate_usdc")) for row in promotion_candidates + positive_collect), default=0.0)
    best_forward_paper_run_rate = max((_num(row.get("monthly_run_rate_usdc")) for row in positive_forward_paper), default=0.0)
    bridge_summary = read_json(price_root / "price_action_paper_signal_summary.json", default={}) or {}
    if promotion_candidates:
        learning_state = "price_action_candidates_ready_for_governed_paper_bridge"
        next_action = "Run the price-action paper bridge and keep entries paper-only until broker evidence is positive."
    elif positive_collect:
        learning_state = "collect_more_positive_price_action_evidence"
        next_action = "Prioritise websocket collection for positive price-action cohorts until closed round-trip gates clear."
    elif suppressed:
        learning_state = "suppress_negative_price_action_and_broaden"
        next_action = "Do not expand negative cohorts; broaden discovery and wait for a new positive bid/ask thesis."
    else:
        learning_state = "collect_initial_price_action_evidence"
        next_action = "Keep collecting websocket bid/ask marks; no cohort has enough executable price-action evidence yet."

    payload = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
        "target_monthly_profit_usdc": target_monthly_profit,
        "best_positive_monthly_run_rate_usdc": best_run_rate,
        "monthly_goal_gap_usdc": max(target_monthly_profit - best_run_rate, 0.0),
        "learning_state": learning_state,
        "next_action": next_action,
        "collection_queries": collection_queries,
        "suppressed_queries": suppressed_queries,
        "cohorts_evaluated": len(cohorts),
        "promotion_candidates": len(promotion_candidates),
        "positive_collect_candidates": len(positive_collect),
        "suppressed_candidates": len(suppressed),
        "forward_paper_cohorts": len(forward_paper),
        "forward_paper_positive_cohorts": len(positive_forward_paper),
        "forward_paper_promoted_cohorts": len([row for row in forward_paper if _bool(row.get("promoted"))]),
        "best_forward_paper_monthly_run_rate_usdc": best_forward_paper_run_rate,
        "forward_paper_goal_gap_usdc": max(target_monthly_profit - best_forward_paper_run_rate, 0.0),
        "paper_bridge_signals": bridge_summary.get("signals", 0) if isinstance(bridge_summary, dict) else 0,
        "top_cohorts": cohorts[:20],
        "forward_paper_preview": forward_paper[:10],
        "promotion_candidate_preview": promotion_candidates[:10],
        "positive_collect_preview": positive_collect[:10],
        "suppressed_preview": suppressed[:10],
        "warnings": {
            "settlement_independent": True,
            "uses_entry_ask_and_exit_bid": True,
            "shadow_only_controller": True,
            "does_not_authorise_live_trading": True,
            "does_not_weaken_governance_gates": True,
        },
    }
    write_json(governance_root / OUTPUT_FILENAME, payload)
    return payload


def run(config_path: str = "polymarket_predictive_config.example.yaml") -> dict[str, Any]:
    cfg = load_config(config_path)
    return build_price_action_feedback(cfg)
