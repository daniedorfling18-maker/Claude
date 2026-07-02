from __future__ import annotations

from typing import Any

from .goal_planner import build_goal_plan
from .promotion_review import build_promotion_review
from .utils import now_utc, read_csv_rows, read_json, safe_float, write_json

CORE_WATCHLIST_COHORTS: dict[str, str] = {}

QUARANTINED_COHORT_FRAGMENTS = (
    "crypto_btc_updown_5m",
    "crypto_sol_updown_5m",
    "crypto_xrp_updown_5m",
    "crypto_updown_5m",
)


def _num(value: Any, default: float = 0.0) -> float:
    parsed = safe_float(value)
    return default if parsed is None else float(parsed)


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "approved", "promoted"}


def _is_quarantined_fast_crypto(cohort: str) -> bool:
    text = cohort.lower()
    return any(fragment in text for fragment in QUARANTINED_COHORT_FRAGMENTS)


def _readiness_gap(row: dict[str, Any]) -> dict[str, Any]:
    readiness = row.get("promotion_readiness")
    if not isinstance(readiness, dict):
        readiness = {}
    return {
        "fills_remaining": readiness.get("fills_remaining", ""),
        "settled_fills_remaining": readiness.get("settled_fills_remaining", ""),
        "pnl_remaining_usdc": readiness.get("pnl_remaining_usdc", ""),
        "roi_remaining": readiness.get("roi_remaining", ""),
        "monthly_run_rate_remaining_usdc": readiness.get("monthly_run_rate_remaining_usdc", ""),
        "tracking_hours_remaining": readiness.get("tracking_hours_remaining", ""),
    }


def _cohort_query(cohort: str) -> str:
    text = cohort.lower()
    # Unknown near-miss evidence is not actionable until it maps to a real family.
    if text.startswith("near_miss_learning|unknown"):
        return ""
    if "btc" in text or "bitcoin" in text:
        return "btc updown" if "updown" in text else "bitcoin"
    if "xrp" in text or "ripple" in text:
        return "xrp updown" if "updown" in text else "xrp"
    if "sol" in text or "solana" in text:
        return "solana updown" if "updown" in text else "solana"
    if "eth" in text or "ethereum" in text:
        return "eth updown" if "updown" in text else "ethereum"
    if "tennis" in text:
        return "tennis"
    if "worldcup" in text or "world_cup" in text or "world cup" in text:
        return "world cup"
    if "macro_rates" in text or "fed" in text or "rates" in text:
        return "fed"
    if "macro_economy" in text or "economy" in text or "inflation" in text:
        return "economy"
    if "crypto" in text:
        return "bitcoin"
    return ""


def _thesis(cohort: str, row: dict[str, Any]) -> str:
    if _is_quarantined_fast_crypto(cohort):
        return "quarantined_fast_crypto_5m_negative_or_untrusted_evidence"
    if cohort in CORE_WATCHLIST_COHORTS:
        return CORE_WATCHLIST_COHORTS[cohort]
    if cohort.startswith("near_miss_learning|unknown"):
        return "research_only_unknown_near_miss_needs_family_resolution"
    if cohort.startswith("near_miss_learning|"):
        return "near_miss_learning_needs_more_forward_evidence"
    if _bool(row.get("probationary")):
        return "probationary_paper_probe_candidate"
    if _bool(row.get("promoted")):
        return "promoted_candidate_monitor_actual_pnl"
    pnl = _num(row.get("total_pnl_usdc") or row.get("shadow_total_pnl_usdc"))
    roi = _num(row.get("roi") or row.get("shadow_roi"))
    if pnl > 0 and roi > 0:
        return "positive_evidence_needs_more_samples_or_gate_clearance"
    return "collecting_evidence"


def _priority(row: dict[str, Any]) -> float:
    score = _num(row.get("promotion_ready_score"))
    checks = max(1.0, _num(row.get("promotion_ready_checks"), 6.0))
    pnl = _num(row.get("total_pnl_usdc") or row.get("shadow_total_pnl_usdc"))
    roi = _num(row.get("roi") or row.get("shadow_roi"))
    monthly = _num(row.get("monthly_run_rate_usdc") or row.get("shadow_monthly_run_rate_usdc"))
    fills = _num(row.get("buy_fills") or row.get("shadow_fills"))
    settled = _num(row.get("settled_fills") or row.get("shadow_sell_fills") or row.get("sell_fills"))
    value = 20.0 * (score / checks) + min(roi, 2.0) * 4.0 + min(max(pnl, -10.0), 25.0) * 0.2 + min(monthly, 500.0) * 0.005 + fills * 0.2 + settled * 0.3
    if _bool(row.get("probationary")):
        value += 25.0
    if _bool(row.get("promoted")):
        value += 35.0
    if pnl <= 0 or roi <= 0:
        value *= 0.4
    return value


def _include_focus_row(cohort: str, row: dict[str, Any]) -> bool:
    if _is_quarantined_fast_crypto(cohort):
        return False
    if cohort in CORE_WATCHLIST_COHORTS:
        return True
    if cohort.startswith("near_miss_learning|unknown"):
        return False
    if cohort.startswith("near_miss_learning|"):
        return True
    if _bool(row.get("probationary")) or _bool(row.get("promoted")):
        return True
    pnl = _num(row.get("total_pnl_usdc") or row.get("shadow_total_pnl_usdc"))
    roi = _num(row.get("roi") or row.get("shadow_roi"))
    settled = _num(row.get("settled_fills") or row.get("shadow_sell_fills") or row.get("sell_fills"))
    score = _num(row.get("promotion_ready_score"))
    return pnl > 0 and roi > 0 and (settled >= 1 or score >= 4)


def _focus_row(row: dict[str, Any]) -> dict[str, Any]:
    cohort = str(row.get("signal_cohort") or row.get("cohort") or "unknown")
    return {
        "cohort": cohort,
        "thesis": _thesis(cohort, row),
        "status": "promoted" if _bool(row.get("promoted")) else "probationary" if _bool(row.get("probationary")) else "collecting_evidence",
        "priority_score": round(_priority(row), 4),
        "buy_fills": row.get("buy_fills", row.get("shadow_fills")),
        "settled_fills": row.get("settled_fills", row.get("shadow_sell_fills", row.get("sell_fills"))),
        "total_pnl_usdc": row.get("total_pnl_usdc", row.get("shadow_total_pnl_usdc")),
        "roi": row.get("roi", row.get("shadow_roi")),
        "monthly_run_rate_usdc": row.get("monthly_run_rate_usdc", row.get("shadow_monthly_run_rate_usdc")),
        "promotion_ready_score": row.get("promotion_ready_score"),
        "promotion_ready_checks": row.get("promotion_ready_checks"),
        "gap": _readiness_gap(row),
        "recommended_collection_query": _cohort_query(cohort),
        "do_not_trade_reason": row.get("promotion_reason", "not promoted yet"),
    }


def _load_cohort_rows(cfg) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for filename in ("signal_cohort_pnl.json", "shadow_signal_cohort_pnl.json"):
        payload = read_json(cfg.governance_root / filename, default={}) or {}
        cohorts = payload.get("cohorts", []) if isinstance(payload, dict) else []
        if isinstance(cohorts, list):
            rows.extend(row for row in cohorts if isinstance(row, dict))
    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        cohort = str(row.get("signal_cohort") or row.get("cohort") or "unknown")
        existing = deduped.get(cohort)
        if existing is None or _priority(row) > _priority(existing):
            deduped[cohort] = row
    return list(deduped.values())


def _feedback_collection_queries(price_action_feedback: dict[str, Any]) -> list[str]:
    queries: list[str] = []
    for raw_query in price_action_feedback.get("collection_queries", []) or []:
        query = str(raw_query or "").strip()
        if query and query not in queries:
            queries.append(query)
    for key in ("paper_confirmation_preview", "promotion_candidate_preview"):
        rows = price_action_feedback.get(key, [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            query = str(row.get("recommended_collection_query") or "").strip()
            if query and query not in queries:
                queries.append(query)
    return queries


def _model_validation_gap_queries(price_action_model: dict[str, Any]) -> list[str]:
    validation_gap = price_action_model.get("validation_gap")
    if not isinstance(validation_gap, dict):
        return []
    if str(validation_gap.get("state") or "") != "needs_positive_validation_examples":
        return []
    queries: list[str] = []
    for raw_query in validation_gap.get("collection_queries", []) or []:
        query = str(raw_query or "").strip()
        if query and query not in queries:
            queries.append(query)
    return queries


def _historical_breadth_queries(price_action_paper: dict[str, Any]) -> list[str]:
    breadth = price_action_paper.get("historical_breadth_scan")
    if not isinstance(breadth, dict):
        return []
    if str(breadth.get("state") or "") not in {
        "positive_validation_pockets_not_robust",
        "robust_positive_historical_buckets_found",
    }:
        return []
    queries: list[str] = []
    for raw_query in breadth.get("recommended_collection_queries", []) or []:
        query = str(raw_query or "").strip()
        if query and query not in queries:
            queries.append(query)
    for row in breadth.get("top_near_positive_buckets", []) or []:
        if not isinstance(row, dict):
            continue
        query = str(row.get("recommended_collection_query") or "").strip()
        if query and query not in queries:
            queries.append(query)
    return queries


def _near_miss_collection_query(row: dict[str, Any]) -> str:
    text = " ".join(
        str(row.get(key) or "")
        for key in (
            "market_slug",
            "question",
            "title",
            "category",
            "signal_cohort",
            "outcome",
        )
    ).lower()
    if not text.strip():
        return ""

    # Check esports before World Cup because some esports rows can inherit noisy categories.
    if any(
        marker in text
        for marker in (
            "esport",
            "valorant",
            "val-",
            "map handicap",
            "karmine",
            "team vitality",
            "edward gaming",
            "league of legends",
            "counter-strike",
            "cs2",
            "dota",
        )
    ):
        return "esports"
    if any(marker in text for marker in ("fed", "fomc", "rate cut", "rate hike", "interest rate")):
        return "fed"
    if any(marker in text for marker in ("cpi", "inflation", "gdp", "unemployment", "recession", "economy")):
        return "economy"
    if any(marker in text for marker in ("worldcup", "world cup", "fifa")):
        return "world cup"
    if any(marker in text for marker in ("tennis", "sinner", "wimbledon", "us-open", "us open")):
        return "tennis"
    if "bitcoin" in text or "btc" in text:
        return "btc updown" if "updown" in text or " up/down" in text or " up or down" in text else "bitcoin"
    if "ethereum" in text or "eth" in text:
        return "eth updown" if "updown" in text or " up/down" in text or " up or down" in text else "ethereum"
    if "solana" in text or " sol" in text or "sol-" in text:
        return "solana updown" if "updown" in text or " up/down" in text or " up or down" in text else "solana"
    if "xrp" in text or "ripple" in text:
        return "xrp updown" if "updown" in text or " up/down" in text or " up or down" in text else "xrp"
    if "openai" in text:
        return "openai"
    return ""


def _near_miss_priority(row: dict[str, Any]) -> float:
    score = _num(row.get("near_miss_priority_score"))
    edge = _num(row.get("edge_lower_bound"))
    liquidity = max(0.0, _num(row.get("liquidity")))
    spread = max(0.0, _num(row.get("current_spread")))
    query = _near_miss_collection_query(row)
    crypto_queries = {"bitcoin", "btc updown", "ethereum", "eth updown", "solana", "solana updown", "xrp", "xrp updown"}
    non_crypto_bonus = 0.02 if query and query not in crypto_queries else 0.0
    return score + edge * 0.5 + min(liquidity, 50_000.0) * 0.000001 - spread * 0.25 + non_crypto_bonus


def _near_miss_candidate_queries(cfg, *, max_queries: int = 6) -> list[str]:
    rows = read_csv_rows(cfg.output_root / "polymarket_predictions" / "near_miss_learning_candidates.csv")
    ranked = sorted(rows, key=_near_miss_priority, reverse=True)
    queries: list[str] = []
    for row in ranked:
        query = _near_miss_collection_query(row)
        if query and query not in queries:
            queries.append(query)
        if len(queries) >= max_queries:
            break
    return queries


def _current_analogue_scan_needs_breadth(price_action_paper: dict[str, Any]) -> bool:
    scan = price_action_paper.get("current_historical_analogue_scan")
    if isinstance(scan, dict):
        return _num(scan.get("current_rows")) > 0 and _num(scan.get("positive_matches")) <= 0
    breadth = price_action_paper.get("historical_breadth_scan")
    if isinstance(breadth, dict):
        return str(breadth.get("state") or "") == "no_positive_historical_breadth_after_spread"
    return False


def _current_positive_analogue_targets(price_action_paper: dict[str, Any], *, max_targets: int = 6) -> list[dict[str, Any]]:
    """Current executable positive analogues are collection targets, not trade approvals."""
    scan = price_action_paper.get("current_historical_analogue_scan")
    if not isinstance(scan, dict):
        return []
    rows = scan.get("positive_preview")
    if not isinstance(rows, list):
        return []
    breadth = price_action_paper.get("historical_breadth_scan")
    thresholds = breadth.get("thresholds", {}) if isinstance(breadth, dict) else {}
    robust_min_roi = _num(
        scan.get("minimum_robust_validation_roi")
        or thresholds.get("min_validation_roi")
        or 0.03
    )
    targets: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        query = str(row.get("recommended_collection_query") or "").strip()
        if not query:
            query = _near_miss_collection_query(row)
        if not query:
            query = _cohort_query(str(row.get("family") or row.get("signal_cohort") or ""))
        validation_roi = _num(row.get("historical_analogue_validation_roi"))
        validation_rows = _num(row.get("historical_analogue_validation_rows"))
        positive_rows = _num(row.get("historical_analogue_positive_rows"))
        targets.append(
            {
                "market_slug": row.get("market_slug", ""),
                "question": row.get("question", ""),
                "family": row.get("family", ""),
                "outcome": row.get("outcome", ""),
                "token_id": row.get("token_id", ""),
                "recommended_collection_query": query,
                "latest_bid": row.get("latest_bid", ""),
                "latest_ask": row.get("latest_ask", ""),
                "latest_spread": row.get("latest_spread", ""),
                "historical_analogue_key": row.get("historical_analogue_key", ""),
                "validation_rows": validation_rows,
                "positive_rows": positive_rows,
                "validation_roi": validation_roi,
                "win_rate": row.get("historical_analogue_win_rate", ""),
                "minimum_robust_validation_roi": robust_min_roi,
                "robust_validation_roi_gap": max(0.0, robust_min_roi - validation_roi),
                "decision_use": "forward_shadow_learning_target_not_trade_authorisation",
                "next_action": "Collect fresh bid/ask repricing around this family/market until robust breadth and paper-confirmation gates clear.",
            }
        )
    targets.sort(
        key=lambda item: (
            _num(item.get("validation_roi")),
            _num(item.get("positive_rows")),
            _num(item.get("validation_rows")),
            -_num(item.get("latest_spread"), 999.0),
        ),
        reverse=True,
    )
    return targets[:max_targets]


def _current_positive_analogue_queries(targets: list[dict[str, Any]]) -> list[str]:
    queries: list[str] = []
    for row in targets:
        query = str(row.get("recommended_collection_query") or "").strip()
        if query and query not in queries:
            queries.append(query)
    return queries


def _price_action_model_needs_data(price_action_model: dict[str, Any]) -> bool:
    decision = str(price_action_model.get("decision") or "")
    status = str(price_action_model.get("status") or "")
    return bool(
        status in {"insufficient_data", "missing"}
        or decision.startswith("collect_more_bid_ask_price_action")
        or price_action_model.get("promotion_ready") is False
    )


def build_research_focus(cfg) -> dict[str, Any]:
    governance = cfg.governance_root
    focus_rows = [_focus_row(row) for row in _load_cohort_rows(cfg) if _include_focus_row(str(row.get("signal_cohort") or row.get("cohort") or "unknown"), row)]
    focus_rows.sort(key=lambda item: _num(item.get("priority_score")), reverse=True)

    promotion_review = build_promotion_review(cfg)
    goal_plan = build_goal_plan(cfg)
    price_action_feedback = read_json(governance / "price_action_feedback.json", default={}) or {}
    if not isinstance(price_action_feedback, dict):
        price_action_feedback = {}
    price_action_model = read_json(cfg.output_root / "polymarket_price_action" / "price_action_model_summary.json", default={}) or {}
    if not isinstance(price_action_model, dict):
        price_action_model = {}
    price_action_paper = read_json(cfg.output_root / "polymarket_price_action" / "price_action_paper_signal_summary.json", default={}) or {}
    if not isinstance(price_action_paper, dict):
        price_action_paper = {}
    feedback_queries = _feedback_collection_queries(price_action_feedback)
    validation_gap_queries = _model_validation_gap_queries(price_action_model)
    historical_breadth_queries = _historical_breadth_queries(price_action_paper)
    near_miss_queries = _near_miss_candidate_queries(cfg)
    analogue_scan_needs_breadth = _current_analogue_scan_needs_breadth(price_action_paper)
    current_positive_targets = _current_positive_analogue_targets(price_action_paper)
    current_positive_queries = _current_positive_analogue_queries(current_positive_targets)
    model_needs_repricing_data = _price_action_model_needs_data(price_action_model) and bool(feedback_queries)
    feedback_positive = bool(
        _num(price_action_feedback.get("promotion_candidates"))
        or _num(price_action_feedback.get("positive_collect_candidates"))
        or _num(price_action_feedback.get("paper_confirmation_candidates"))
    )
    feedback_broaden = str(price_action_feedback.get("learning_state") or "") == "suppress_negative_price_action_and_broaden"

    if validation_gap_queries:
        validation_gap = price_action_model.get("validation_gap", {})
        blockers = price_action_model.get("validation_blockers", []) or price_action_model.get("blockers", [])
        blocker_text = "; ".join(str(item) for item in blockers) if isinstance(blockers, list) else str(blockers or "")
        next_action = (
            "Strict price-action model has positive train repricing examples but no positive validation examples; "
            f"focus fresh websocket/scout collection on {', '.join(validation_gap_queries[:4])}."
            + (f" Gap: {validation_gap.get('reason')}." if isinstance(validation_gap, dict) and validation_gap.get("reason") else "")
            + (f" Model blocker: {blocker_text}." if blocker_text else "")
        )
    elif model_needs_repricing_data:
        blockers = price_action_model.get("validation_blockers", []) or price_action_model.get("blockers", [])
        blocker_text = "; ".join(str(item) for item in blockers) if isinstance(blockers, list) else str(blockers or "")
        if current_positive_queries:
            target = current_positive_targets[0] if current_positive_targets else {}
            roi = _num(target.get("validation_roi"))
            gap = _num(target.get("robust_validation_roi_gap"))
            next_action = (
                "A current executable market has a positive historical analogue after bid/ask costs, but it is still a "
                f"forward-shadow learning target, not a trade approval; prioritise {', '.join(current_positive_queries[:4])} "
                f"collection. Best current analogue validation ROI={roi:.4f}; robust ROI gap={gap:.4f}."
                + (f" Model blocker: {blocker_text}." if blocker_text else "")
            )
        elif historical_breadth_queries:
            breadth = price_action_paper.get("historical_breadth_scan", {})
            next_action = (
                "Strict price-action model has near-positive historical buckets but they are not robust yet; "
                f"prioritise {', '.join(historical_breadth_queries[:4])} bid/ask collection before generic scans."
                + (f" Breadth state: {breadth.get('state')}." if isinstance(breadth, dict) else "")
                + (f" Model blocker: {blocker_text}." if blocker_text else "")
            )
        elif analogue_scan_needs_breadth and near_miss_queries:
            next_action = (
                "Strict price-action model found no profitable current analogue after bid/ask costs; "
                f"broaden evidence collection into near-miss markets: {', '.join(near_miss_queries[:4])}."
                + (f" Model blocker: {blocker_text}." if blocker_text else "")
            )
        else:
            next_action = (
                "Strict price-action model needs profitable ask-to-future-bid training examples; "
                f"focus websocket collection on {', '.join(feedback_queries[:4])}."
                + (f" Model blocker: {blocker_text}." if blocker_text else "")
            )
    elif focus_rows:
        top = focus_rows[0]
        next_action = (
            f"Focus discovery on {top['recommended_collection_query']} for {top['cohort']}; "
            f"thesis={top['thesis']}; remaining gates={top.get('gap', {})}."
        )
    elif feedback_positive:
        next_action = str(price_action_feedback.get("next_action") or "Prioritise positive price-action cohorts until bid/ask gates clear.")
    else:
        next_action = (
            "Keep collecting bid/ask repricing evidence across liquid event families; "
            "no actionable family has enough fresh positive validation evidence yet."
        )

    collection_queries = []
    if validation_gap_queries:
        for query in validation_gap_queries:
            if query and query not in collection_queries:
                collection_queries.append(query)
    if model_needs_repricing_data:
        for query in current_positive_queries:
            if query and query not in collection_queries:
                collection_queries.append(query)
        for query in historical_breadth_queries:
            if query and query not in collection_queries:
                collection_queries.append(query)
        if analogue_scan_needs_breadth:
            for query in near_miss_queries:
                if query and query not in collection_queries:
                    collection_queries.append(query)
        for query in feedback_queries:
            if query and query not in collection_queries:
                collection_queries.append(query)
    for row in focus_rows:
        query = str(row.get("recommended_collection_query") or "").strip()
        if query and query not in collection_queries:
            collection_queries.append(query)
    if feedback_positive or feedback_broaden:
        for query in feedback_queries:
            query = str(query or "").strip()
            if query and query not in collection_queries:
                collection_queries.append(query)
    if not collection_queries:
        feedback_queries = [str(query or "").strip() for query in price_action_feedback.get("collection_queries", [])]
        collection_queries = [query for query in feedback_queries if query] or ["bitcoin", "ethereum", "world cup", "tennis"]

    payload = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "summary": next_action,
        "watchlist": focus_rows,
        "collection_queries": collection_queries,
        "suppressed_queries": price_action_feedback.get("suppressed_queries", []),
        "price_action_model": {
            "status": price_action_model.get("status"),
            "decision": price_action_model.get("decision"),
            "promotion_ready": price_action_model.get("promotion_ready"),
            "training_events": price_action_model.get("training_events"),
            "train_rows": price_action_model.get("train_rows"),
            "validation_rows": price_action_model.get("validation_rows"),
            "train_positive_targets": price_action_model.get("train_positive_targets"),
            "validation_positive_targets": price_action_model.get("validation_positive_targets"),
            "validation_gap": price_action_model.get("validation_gap", {}),
            "validation_gap_queries": validation_gap_queries,
            "blockers": price_action_model.get("blockers", []),
            "validation_gap_needs_collection": bool(validation_gap_queries),
            "model_needs_repricing_data": model_needs_repricing_data or bool(validation_gap_queries),
            "historical_breadth_queries": historical_breadth_queries,
            "current_positive_analogue_queries": current_positive_queries,
            "near_miss_candidate_queries": near_miss_queries,
            "analogue_scan_needs_breadth": analogue_scan_needs_breadth,
        },
        "price_action_current_positive_analogues": {
            "state": "learning_targets_available" if current_positive_targets else "none",
            "targets": current_positive_targets,
            "collection_queries": current_positive_queries,
            "paper_only": True,
            "trade_authorisation": "no_trade_without_governed_price_action_signal",
        },
        "price_action_historical_breadth": price_action_paper.get("historical_breadth_scan", {}),
        "price_action_feedback": {
            "status": price_action_feedback.get("status"),
            "learning_state": price_action_feedback.get("learning_state"),
            "next_action": price_action_feedback.get("next_action"),
            "promotion_candidates": price_action_feedback.get("promotion_candidates"),
            "positive_collect_candidates": price_action_feedback.get("positive_collect_candidates"),
            "suppressed_candidates": price_action_feedback.get("suppressed_candidates"),
            "best_positive_monthly_run_rate_usdc": price_action_feedback.get("best_positive_monthly_run_rate_usdc"),
            "monthly_goal_gap_usdc": price_action_feedback.get("monthly_goal_gap_usdc"),
            "top_cohorts": price_action_feedback.get("top_cohorts", [])[:10],
        },
        "promotion_review": {
            "status": promotion_review.get("status"),
            "top_actionable": promotion_review.get("top_actionable", [])[:10],
        },
        "goal_plan": {
            "status": goal_plan.get("status"),
            "main_gap": goal_plan.get("main_gap"),
            "recommended_action": goal_plan.get("recommended_action"),
            "target_monthly_profit_usdc": goal_plan.get("target_monthly_profit_usdc"),
            "actual_pnl_since_baseline_usdc": goal_plan.get("actual_pnl_since_baseline_usdc"),
            "required_daily_from_here_usdc": goal_plan.get("required_daily_from_here_usdc"),
        },
    }
    write_json(governance / "research_focus.json", payload)
    return payload
