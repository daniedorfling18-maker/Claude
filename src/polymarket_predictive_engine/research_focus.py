from __future__ import annotations

from typing import Any

from .goal_planner import build_goal_plan
from .promotion_review import build_promotion_review
from .utils import now_utc, read_csv_rows, read_json, safe_float, write_json
from .worldcup_validation import classify_market_family

CORE_WATCHLIST_COHORTS: dict[str, str] = {}

QUARANTINED_COHORT_FRAGMENTS = (
    "crypto_btc_updown_5m",
    "crypto_sol_updown_5m",
    "crypto_xrp_updown_5m",
    "crypto_updown_5m",
)

DEFAULT_BROAD_BASE_QUERIES = (
    "world cup",
    "tennis",
    "nba",
    "mlb",
    "mma",
    "fed",
    "economy",
    "esports",
    "ai",
    "politics",
    "elections",
    "stocks",
)


def _num(value: Any, default: float = 0.0) -> float:
    parsed = safe_float(value)
    return default if parsed is None else float(parsed)


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "approved", "promoted"}


def _int_setting(settings: dict[str, Any], key: str, default: int) -> int:
    value = safe_float(settings.get(key))
    if value is None:
        return default
    return int(max(0, value))


def _append_unique(values: list[str], query: Any) -> None:
    text = str(query or "").strip()
    if text and text not in values:
        values.append(text)


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
    if "basketball" in text or "nba" in text:
        return "nba"
    if "baseball" in text or "mlb" in text:
        return "mlb"
    if "mma" in text or "ufc" in text:
        return "mma"
    if "esport" in text or "valorant" in text or "cs2" in text or "dota" in text or "league_of_legends" in text or "league of legends" in text:
        return "esports"
    if "macro_rates" in text or "fed" in text or "rates" in text:
        return "fed"
    if "macro_economy" in text or "economy" in text or "inflation" in text:
        return "economy"
    if "ai" in text or "openai" in text or "anthropic" in text:
        return "ai"
    if "politic" in text or "election" in text:
        return "politics"
    if "stock" in text or "equities" in text or "nasdaq" in text:
        return "stocks"
    if "crypto" in text:
        return "bitcoin"
    return ""


def _research_focus_settings(cfg) -> dict[str, Any]:
    settings = cfg.raw.get("research_focus", {})
    return settings if isinstance(settings, dict) else {}


def _query_key(query: str) -> str:
    return " ".join(str(query or "").strip().lower().split())


def _is_updown_query(query: str) -> bool:
    text = f" {_query_key(query)} "
    return "updown" in text or " up/down " in text or " up or down " in text


def _query_family(query: str) -> str:
    text = f" {_query_key(query)} "
    if not text.strip():
        return "empty"
    if _is_updown_query(query):
        return "crypto_updown"
    if "world cup" in text or "worldcup" in text or " fifa " in text:
        return "worldcup"
    if "tennis" in text or " wimbledon " in text or " us open " in text or " atp " in text or " wta " in text:
        return "tennis"
    if " nba " in text or "basketball" in text:
        return "basketball_nba_match"
    if " mlb " in text or "baseball" in text:
        return "baseball_mlb_match"
    if " mma " in text or " ufc " in text or "mixed martial" in text:
        return "mma_match"
    if "fed" in text or "fomc" in text or "interest rate" in text or " rate cut " in text or " rate hike " in text:
        return "macro_rates"
    if "economy" in text or "inflation" in text or " cpi " in text or " gdp " in text or "recession" in text:
        return "macro_economy"
    if "esport" in text or "valorant" in text or "cs2" in text or " dota " in text or "league of legends" in text:
        return "esports"
    if "openai" in text or " ai " in text or "chatgpt" in text or "anthropic" in text or "claude" in text:
        return "ai"
    if "politic" in text or "election" in text or "trump" in text or "president" in text:
        return "politics"
    if "stock" in text or "equities" in text or "nasdaq" in text or "s&p" in text or "s p 500" in text:
        return "equities"
    if "sports" in text or " nfl " in text:
        return "sports"
    if "bitcoin" in text or " btc " in text:
        return "crypto_btc"
    if "ethereum" in text or " eth " in text:
        return "crypto_eth"
    if "solana" in text or " sol " in text:
        return "crypto_sol"
    if " xrp " in text or "ripple" in text:
        return "crypto_xrp"
    family = classify_market_family({"question": query, "market_slug": query, "category": query})
    return family if family and family != "unknown" else "misc"


def _broad_base_queries(cfg) -> list[str]:
    settings = _research_focus_settings(cfg)
    raw = settings.get("broad_base_queries")
    if raw is None:
        paper_scan = cfg.raw.get("paper_market_scan", {}) if isinstance(cfg.raw.get("paper_market_scan"), dict) else {}
        raw = paper_scan.get("broad_repricing_queries") or DEFAULT_BROAD_BASE_QUERIES
    if isinstance(raw, tuple):
        raw = list(raw)
    elif not isinstance(raw, list):
        raw = [raw]
    queries: list[str] = []
    seen: set[str] = set()
    for item in raw:
        query = str(item or "").strip()
        key = _query_key(query)
        if not query or not key or key in seen:
            continue
        queries.append(query)
        seen.add(key)
    return queries or list(DEFAULT_BROAD_BASE_QUERIES)


def _guard_collection_queries(cfg, proposed_queries: list[str]) -> tuple[list[str], dict[str, Any]]:
    settings = _research_focus_settings(cfg)
    max_per_family = max(1, _int_setting(settings, "max_queries_per_family", 2))
    min_distinct_families = max(1, _int_setting(settings, "min_distinct_families", 4))
    max_updown_queries = max(0, _int_setting(settings, "max_updown_queries", 1))

    raw_queries: list[str] = []
    seen_raw: set[str] = set()
    for query in proposed_queries:
        clean = str(query or "").strip()
        key = _query_key(clean)
        if not clean or not key or key in seen_raw:
            continue
        raw_queries.append(clean)
        seen_raw.add(key)

    guarded: list[str] = []
    guarded_keys: set[str] = set()
    family_counts: dict[str, int] = {}
    rejected: list[dict[str, Any]] = []
    updown_count = 0

    def add_query(query: str, *, source: str) -> tuple[bool, str]:
        nonlocal updown_count
        key = _query_key(query)
        if not key or key in guarded_keys:
            return False, "duplicate"
        family = _query_family(query)
        if _is_updown_query(query) and updown_count >= max_updown_queries:
            return False, "max_updown_queries"
        if family_counts.get(family, 0) >= max_per_family:
            return False, "max_queries_per_family"
        guarded.append(query)
        guarded_keys.add(key)
        family_counts[family] = family_counts.get(family, 0) + 1
        if _is_updown_query(query):
            updown_count += 1
        return True, source

    for query in raw_queries:
        added, reason = add_query(query, source="raw")
        if not added and reason != "duplicate":
            rejected.append({"query": query, "family": _query_family(query), "reason": reason})

    broad_queries = _broad_base_queries(cfg)
    target_len = max(len(raw_queries), min_distinct_families)
    broad_fill: list[str] = []
    attempts = max(1, target_len * max(1, len(broad_queries)) * 2)
    index = 0
    while (
        (len(guarded) < target_len or len(family_counts) < min_distinct_families)
        and index < attempts
    ):
        candidate = broad_queries[index % len(broad_queries)]
        index += 1
        added, _reason = add_query(candidate, source="broad_base")
        if added:
            broad_fill.append(candidate)
        if len(guarded_keys) >= len(seen_raw) + len({_query_key(query) for query in broad_queries if _query_key(query)}):
            break

    guard = {
        "enabled": True,
        "settings": {
            "max_queries_per_family": max_per_family,
            "min_distinct_families": min_distinct_families,
            "max_updown_queries": max_updown_queries,
        },
        "raw_collection_queries": raw_queries,
        "guarded_collection_queries": guarded,
        "broad_base_queries": broad_queries,
        "broad_fill_queries": broad_fill,
        "rejected_queries": rejected,
        "family_counts": dict(sorted(family_counts.items())),
        "distinct_families": len(family_counts),
        "updown_queries": [query for query in guarded if _is_updown_query(query)],
        "updown_query_count": updown_count,
        "decision_use": "collection_rebalancing_only_not_trade_authorisation",
    }
    return guarded, guard


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
    if any(marker in text for marker in (" nba ", "basketball", "celtics", "knicks", "lakers", "warriors")):
        return "nba"
    if any(marker in text for marker in (" mlb ", "baseball", "yankees", "red sox", "dodgers", "mets")):
        return "mlb"
    if any(marker in text for marker in (" mma ", "ufc", "mixed martial")):
        return "mma"
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


def _price_action_label_hurdles(cfg) -> tuple[float, float]:
    settings = cfg.raw.get("price_action_model", {}) if isinstance(cfg.raw.get("price_action_model"), dict) else {}
    explicit_return = safe_float(settings.get("minimum_profitable_return_label"))
    if explicit_return is None:
        min_return = max(
            0.0,
            _num(settings.get("minimum_expected_roi_to_trade"), 0.03),
            _num(settings.get("minimum_selected_validation_roi"), 0.03),
        )
    else:
        min_return = max(0.0, float(explicit_return))
    min_edge = max(0.0, _num(settings.get("minimum_bid_edge_abs_label")))
    return min_return, min_edge


def _current_positive_analogue_targets(
    cfg,
    price_action_paper: dict[str, Any],
    *,
    max_targets: int = 6,
) -> dict[str, Any]:
    """Current executable positive analogues are collection targets, not trade approvals."""
    scan = price_action_paper.get("current_historical_analogue_scan")
    if not isinstance(scan, dict):
        return {"targets": [], "blocked_targets": []}
    rows = scan.get("positive_preview")
    if not isinstance(rows, list):
        return {"targets": [], "blocked_targets": []}
    breadth = price_action_paper.get("historical_breadth_scan")
    thresholds = breadth.get("thresholds", {}) if isinstance(breadth, dict) else {}
    robust_min_roi = _num(
        scan.get("minimum_robust_validation_roi")
        or thresholds.get("min_validation_roi")
        or 0.03
    )
    min_label_return, min_label_edge = _price_action_label_hurdles(cfg)
    targets: list[dict[str, Any]] = []
    blocked_targets: list[dict[str, Any]] = []
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
        ask = safe_float(row.get("latest_ask"))
        max_possible_edge = max(0.0, 1.0 - ask) if ask is not None else 0.0
        max_possible_return = max_possible_edge / ask if ask and ask > 0 else 0.0
        can_clear_label_hurdle = bool(
            ask is not None
            and 0 < ask < 1
            and max_possible_edge > min_label_edge
            and max_possible_return >= min_label_return
        )
        target = {
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
            "model_label_minimum_return": min_label_return,
            "model_label_minimum_bid_edge": min_label_edge,
            "max_possible_bid_edge": max_possible_edge,
            "max_possible_return": max_possible_return,
            "can_clear_model_label_hurdle": can_clear_label_hurdle,
            "decision_use": "forward_shadow_learning_target_not_trade_authorisation",
            "next_action": "Collect fresh bid/ask repricing around this family/market until robust breadth and paper-confirmation gates clear.",
        }
        if can_clear_label_hurdle:
            targets.append(target)
        else:
            target["block_reason"] = "latest ask is too high to clear the current model's positive-label return hurdle even at a $1.00 bid"
            target["decision_use"] = "diagnostic_only_no_model_label_headroom"
            target["next_action"] = "Do not reserve scout/websocket capacity for this exact row; collect lower-ask analogues with enough upside headroom."
            blocked_targets.append(target)
    targets.sort(
        key=lambda item: (
            _num(item.get("validation_roi")),
            _num(item.get("positive_rows")),
            _num(item.get("validation_rows")),
            -_num(item.get("latest_spread"), 999.0),
        ),
        reverse=True,
    )
    blocked_targets.sort(
        key=lambda item: (
            _num(item.get("max_possible_return")),
            _num(item.get("validation_roi")),
            _num(item.get("positive_rows")),
        ),
        reverse=True,
    )
    return {
        "targets": targets[:max_targets],
        "blocked_targets": blocked_targets[:max_targets],
        "model_label_minimum_return": min_label_return,
        "model_label_minimum_bid_edge": min_label_edge,
    }


def _current_positive_analogue_queries(targets: list[dict[str, Any]]) -> list[str]:
    queries: list[str] = []
    for row in targets:
        query = str(row.get("recommended_collection_query") or "").strip()
        if query and query not in queries:
            queries.append(query)
    return queries


def _side_missing_analogue_targets(price_action_paper: dict[str, Any], *, max_targets: int = 6) -> dict[str, Any]:
    """Rows with positive side-agnostic history need side/context collection, not approval."""
    scans: list[dict[str, Any]] = []
    for key in ("current_historical_analogue_scan", "paper_confirmation_current_historical_analogue"):
        scan = price_action_paper.get(key)
        if isinstance(scan, dict):
            scans.append(scan)
    targets: list[dict[str, Any]] = []
    for scan in scans:
        rows = scan.get("blocked_preview")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("historical_analogue_gate") or "") != "side_missing_positive_historical_analogue_shadow_only":
                continue
            query = str(row.get("recommended_collection_query") or "").strip()
            if not query:
                query = _near_miss_collection_query(row)
            if not query:
                query = _cohort_query(str(row.get("family") or row.get("signal_cohort") or ""))
            if not query:
                continue
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
                    "side_agnostic_historical_analogue_key": row.get("side_agnostic_historical_analogue_key", ""),
                    "side_agnostic_validation_rows": _num(row.get("side_agnostic_historical_analogue_validation_rows")),
                    "side_agnostic_positive_rows": _num(row.get("side_agnostic_historical_analogue_positive_rows")),
                    "side_agnostic_validation_roi": _num(row.get("side_agnostic_historical_analogue_validation_roi")),
                    "side_agnostic_win_rate": row.get("side_agnostic_historical_analogue_win_rate", ""),
                    "decision_use": "collect_trade_flow_side_context_only_not_trade_authorisation",
                    "next_action": (
                        "Collect fresh websocket/trade-flow side context for this family/price/spread bucket; "
                        "side-agnostic history is positive but cannot authorise paper probes."
                    ),
                }
            )
    targets.sort(
        key=lambda item: (
            _num(item.get("side_agnostic_validation_roi")),
            _num(item.get("side_agnostic_positive_rows")),
            _num(item.get("side_agnostic_validation_rows")),
            -_num(item.get("latest_spread"), 999.0),
        ),
        reverse=True,
    )
    queries: list[str] = []
    for target in targets:
        query = str(target.get("recommended_collection_query") or "").strip()
        if query and query not in queries:
            queries.append(query)
    return {
        "state": "needs_trade_flow_side_context" if targets else "none",
        "targets": targets[:max_targets],
        "collection_queries": queries,
        "trade_authorisation": "no_trade_side_agnostic_history_is_shadow_only",
        "paper_only": True,
    }


def _edge_attribution_query(row: dict[str, Any]) -> str:
    cohort = str(row.get("signal_cohort") or row.get("cohort") or "").strip()
    if not cohort:
        cohort = str(row.get("family") or "").strip()
    if not cohort or _is_quarantined_fast_crypto(cohort):
        return ""
    query = _cohort_query(cohort)
    if query:
        return query
    family = str(row.get("family") or "").strip()
    return _cohort_query(family) if family else ""


def _cohort_name(row: dict[str, Any]) -> str:
    return str(row.get("signal_cohort") or row.get("cohort") or row.get("family") or "unknown").strip() or "unknown"


def _attribution_class(row: dict[str, Any]) -> str:
    explicit = str(row.get("attribution_class") or "").strip()
    if explicit:
        return explicit
    primary_drag = str(row.get("primary_drag") or "").strip()
    recommended_action = str(row.get("recommended_action") or "").strip()
    if primary_drag in {"spread_slippage", "quote_quality"}:
        return "cost_dominated"
    if primary_drag in {"adverse_line_movement", "model_edge_failed_to_transfer"}:
        return "model_direction_not_confirmed"
    if primary_drag == "positive_forward_edge" or recommended_action == "collect_confirmation_until_governance_threshold":
        return "positive_edge_confirmed"
    return ""


def _clv_cohort_rows(closing_line_value: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = closing_line_value.get("cohorts", []) if isinstance(closing_line_value, dict) else []
    if not isinstance(rows, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        cohort = _cohort_name(row)
        if cohort != "unknown":
            out[cohort] = row
    return out


def _positive_clv_cohorts(closing_line_value: dict[str, Any]) -> set[str]:
    positive = closing_line_value.get("positive_clv_cohorts", []) if isinstance(closing_line_value, dict) else []
    if not isinstance(positive, list):
        return set()
    return {str(cohort or "").strip() for cohort in positive if str(cohort or "").strip()}


def _research_evidence_inputs(
    edge_attribution: dict[str, Any],
    closing_line_value: dict[str, Any],
    algo_sweep: dict[str, Any],
) -> dict[str, Any]:
    """Explain collection-only priority movement from post-trade evidence artifacts."""
    edge_rows = edge_attribution.get("cohorts", []) if isinstance(edge_attribution, dict) else []
    if not isinstance(edge_rows, list):
        edge_rows = []
    clv_by_cohort = _clv_cohort_rows(closing_line_value)
    positive_clv = _positive_clv_cohorts(closing_line_value)

    adjustments: dict[str, dict[str, Any]] = {}

    def upsert(
        cohort: str,
        *,
        priority_delta: float,
        reason: str,
        attribution_class: str = "",
        clv_evidence: str = "",
        query: str = "",
    ) -> None:
        clean = str(cohort or "").strip()
        if not clean or clean == "unknown" or _is_quarantined_fast_crypto(clean):
            return
        row = adjustments.setdefault(
            clean,
            {
                "cohort": clean,
                "priority_delta": 0.0,
                "reasons": [],
                "attribution_class": attribution_class,
                "clv_evidence": clv_evidence,
                "recommended_collection_query": query or _cohort_query(clean),
                "movement": "unchanged",
                "decision_use": "collection_ordering_only_not_trade_authorisation",
            },
        )
        row["priority_delta"] = round(float(row.get("priority_delta") or 0.0) + priority_delta, 4)
        if reason and reason not in row["reasons"]:
            row["reasons"].append(reason)
        if attribution_class and not row.get("attribution_class"):
            row["attribution_class"] = attribution_class
        if clv_evidence and not row.get("clv_evidence"):
            row["clv_evidence"] = clv_evidence
        if query and not row.get("recommended_collection_query"):
            row["recommended_collection_query"] = query
        row["movement"] = "raised" if row["priority_delta"] > 0 else "lowered" if row["priority_delta"] < 0 else "unchanged"

    for edge_row in edge_rows:
        if not isinstance(edge_row, dict):
            continue
        cohort = _cohort_name(edge_row)
        attribution_class = _attribution_class(edge_row)
        clv_row = clv_by_cohort.get(cohort, {})
        clv_evidence = str(edge_row.get("clv_evidence") or clv_row.get("clv_evidence") or "").strip()
        query = _edge_attribution_query(edge_row) or _cohort_query(cohort)
        if attribution_class in {"cost_dominated", "positive_edge_confirmed"}:
            upsert(
                cohort,
                priority_delta=12.0,
                reason=f"attribution_class={attribution_class}",
                attribution_class=attribution_class,
                clv_evidence=clv_evidence,
                query=query,
            )
        elif attribution_class == "model_direction_not_confirmed" and clv_evidence == "negative_clv_evidence":
            upsert(
                cohort,
                priority_delta=-12.0,
                reason="attribution_class=model_direction_not_confirmed with negative_clv_evidence",
                attribution_class=attribution_class,
                clv_evidence=clv_evidence,
                query=query,
            )

    for cohort in positive_clv:
        query = _cohort_query(cohort)
        upsert(
            cohort,
            priority_delta=8.0,
            reason="positive_clv_cohort",
            clv_evidence=str((clv_by_cohort.get(cohort) or {}).get("clv_evidence") or "positive_clv_evidence"),
            query=query,
        )

    moved = sorted(
        adjustments.values(),
        key=lambda row: (
            _num(row.get("priority_delta")),
            1 if row.get("recommended_collection_query") else 0,
            str(row.get("cohort") or ""),
        ),
        reverse=True,
    )
    raised = [row for row in moved if _num(row.get("priority_delta")) > 0]
    lowered = [row for row in moved if _num(row.get("priority_delta")) < 0]
    added_queries: list[str] = []
    for row in raised:
        query = str(row.get("recommended_collection_query") or "").strip()
        if query and query not in added_queries:
            added_queries.append(query)

    sweep_decision = str(algo_sweep.get("decision") or "") if isinstance(algo_sweep, dict) else ""
    sweep_note = ""
    if sweep_decision == "sweep_candidate_validated_shadow_only":
        selected = algo_sweep.get("selected", {}) if isinstance(algo_sweep, dict) else {}
        if not isinstance(selected, dict):
            selected = {}
        selected_params = {}
        for key in (
            "strategy",
            "tight_spread_maximum",
            "minimum_book_imbalance",
            "validation_fills",
            "validation_pnl_usdc",
        ):
            value = selected.get(key)
            if value is not None and value != "":
                selected_params[key] = value
        params_text = ", ".join(f"{key}={value}" for key, value in selected_params.items()) or "selected parameters unavailable"
        sweep_note = f"Algo sweep validated a shadow-only research lead: {params_text}."

    return {
        "decision_use": "collection_ordering_only_not_trade_authorisation",
        "edge_attribution_status": edge_attribution.get("status") if isinstance(edge_attribution, dict) else None,
        "closing_line_value_status": closing_line_value.get("status") if isinstance(closing_line_value, dict) else None,
        "algo_sweep_status": algo_sweep.get("status") if isinstance(algo_sweep, dict) else None,
        "positive_clv_cohorts": sorted(positive_clv),
        "negative_clv_cohorts": sorted(
            cohort
            for cohort, row in clv_by_cohort.items()
            if str(row.get("clv_evidence") or "") == "negative_clv_evidence"
        ),
        "priority_adjustments": moved,
        "raised_priority_cohorts": raised,
        "lowered_priority_cohorts": lowered,
        "collection_queries_added": added_queries,
        "sweep_decision": sweep_decision,
        "sweep_selected": algo_sweep.get("selected", {}) if isinstance(algo_sweep, dict) else {},
        "sweep_note": sweep_note,
    }


def _edge_attribution_focus(edge_attribution: dict[str, Any], *, max_rows: int = 6) -> dict[str, Any]:
    cohorts = edge_attribution.get("cohorts", []) if isinstance(edge_attribution, dict) else []
    if not isinstance(cohorts, list):
        cohorts = []
    cost_driven: list[dict[str, Any]] = []
    model_driven: list[dict[str, Any]] = []
    positive: list[dict[str, Any]] = []
    for row in cohorts:
        if not isinstance(row, dict):
            continue
        primary_drag = str(row.get("primary_drag") or "")
        attribution_class = _attribution_class(row)
        recommended_action = str(row.get("recommended_action") or "")
        query = _edge_attribution_query(row)
        compact = {
            "cohort": _cohort_name(row),
            "family": row.get("family") or "unknown",
            "decision_pnl_usdc": row.get("decision_pnl_usdc"),
            "total_pnl_usdc": row.get("total_pnl_usdc"),
            "entry_edge_usdc": row.get("entry_edge_usdc"),
            "line_movement_usdc": row.get("line_movement_usdc"),
            "spread_slippage_cost_usdc": row.get("spread_slippage_cost_usdc"),
            "execution_cost_usdc": row.get("execution_cost_usdc"),
            "mean_final_clv": row.get("mean_final_clv"),
            "primary_drag": primary_drag,
            "attribution_class": attribution_class,
            "recommended_action": recommended_action,
            "recommended_collection_query": query,
            "decision_use": "post_trade_feedback_for_collection_not_trade_authorisation",
        }
        if attribution_class == "cost_dominated":
            cost_driven.append(compact)
        elif attribution_class == "model_direction_not_confirmed":
            model_driven.append(compact)
        elif attribution_class == "positive_edge_confirmed":
            positive.append(compact)

    def _sort_key(item: dict[str, Any]) -> tuple[float, float, float]:
        return (
            _num(item.get("decision_pnl_usdc")),
            _num(item.get("line_movement_usdc")),
            -_num(item.get("spread_slippage_cost_usdc")),
        )

    positive.sort(key=_sort_key, reverse=True)
    cost_driven.sort(
        key=lambda item: (
            _num(item.get("spread_slippage_cost_usdc") or item.get("execution_cost_usdc")),
            abs(_num(item.get("decision_pnl_usdc") or item.get("total_pnl_usdc"))),
        ),
        reverse=True,
    )
    model_driven.sort(key=lambda item: (_num(item.get("decision_pnl_usdc")), _num(item.get("mean_final_clv"))))
    queries: list[str] = []
    for bucket in (positive, cost_driven):
        for row in bucket:
            query = str(row.get("recommended_collection_query") or "").strip()
            if query and query not in queries:
                queries.append(query)
            if len(queries) >= max_rows:
                break
        if len(queries) >= max_rows:
            break
    return {
        "status": edge_attribution.get("status") if isinstance(edge_attribution, dict) else None,
        "primary_drag_counts": edge_attribution.get("primary_drag_counts", {}) if isinstance(edge_attribution, dict) else {},
        "collection_queries": queries,
        "positive_forward_edge": positive[:max_rows],
        "cost_driven": cost_driven[:max_rows],
        "model_driven": model_driven[:max_rows],
        "top_negative": edge_attribution.get("top_negative_cohorts", [])[:max_rows] if isinstance(edge_attribution, dict) else [],
    }


def _quote_audit_focus(paper_round_trip_summary: dict[str, Any], *, max_rows: int = 6) -> dict[str, Any]:
    """Turn quote-audit blockers into collection targets, not trading permissions."""
    if not isinstance(paper_round_trip_summary, dict):
        paper_round_trip_summary = {}
    rows = paper_round_trip_summary.get("baseline_quote_audit_by_cohort")
    source = "baseline_quote_audit_by_cohort"
    if not isinstance(rows, list) or not rows:
        rows = paper_round_trip_summary.get("quote_audit_by_cohort")
        source = "quote_audit_by_cohort"
    if not isinstance(rows, list):
        rows = []

    blockers: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cohort = _cohort_name(row)
        family = str(row.get("family") or "").strip()
        if not cohort or cohort == "unknown" or _is_quarantined_fast_crypto(cohort):
            continue
        quote_conflict_round_trips = int(_num(row.get("quote_conflict_round_trips")))
        quote_unverified_round_trips = int(_num(row.get("quote_unverified_round_trips")))
        quote_other_blocked_round_trips = int(_num(row.get("quote_other_blocked_round_trips")))
        entry_snapshot_missing_round_trips = int(
            _num(
                row.get("proof_entry_snapshot_missing_round_trips")
                if row.get("proof_entry_snapshot_missing_round_trips") not in (None, "")
                else row.get("entry_snapshot_missing_round_trips")
            )
        )
        exit_snapshot_missing_round_trips = int(
            _num(
                row.get("proof_exit_snapshot_missing_round_trips")
                if row.get("proof_exit_snapshot_missing_round_trips") not in (None, "")
                else row.get("exit_snapshot_missing_round_trips")
            )
        )
        proof_snapshot_missing_round_trips = entry_snapshot_missing_round_trips + exit_snapshot_missing_round_trips
        blocked_round_trips = (
            quote_conflict_round_trips
            + quote_unverified_round_trips
            + quote_other_blocked_round_trips
            + proof_snapshot_missing_round_trips
        )
        excluded_pnl = _num(row.get("excluded_from_audit_pnl_usdc"))
        if blocked_round_trips <= 0 and abs(excluded_pnl) <= 1e-9:
            continue
        query = _cohort_query(cohort) or _cohort_query(family)
        blockers.append(
            {
                "cohort": cohort,
                "family": family or "unknown",
                "recommended_collection_query": query,
                "round_trips": int(_num(row.get("round_trips"))),
                "quote_consistent_round_trips": int(_num(row.get("quote_consistent_round_trips"))),
                "quote_conflict_round_trips": quote_conflict_round_trips,
                "quote_unverified_round_trips": quote_unverified_round_trips,
                "quote_other_blocked_round_trips": quote_other_blocked_round_trips,
                "entry_snapshot_missing_round_trips": entry_snapshot_missing_round_trips,
                "exit_snapshot_missing_round_trips": exit_snapshot_missing_round_trips,
                "proof_snapshot_missing_round_trips": proof_snapshot_missing_round_trips,
                "raw_pnl_usdc": _num(row.get("raw_pnl_usdc")),
                "audited_pnl_usdc": _num(row.get("audited_pnl_usdc")),
                "excluded_from_audit_pnl_usdc": excluded_pnl,
                "top_blocker_status": row.get("top_blocker_status") or "",
                "top_blocker_count": int(_num(row.get("top_blocker_count"))),
                "recommended_action": row.get("recommended_action")
                or (
                    "collect entry and exit bid/ask snapshots before using this cohort as profit-target proof"
                    if proof_snapshot_missing_round_trips > 0
                    else "collect independent bid/ask snapshots through entry and exit before using this cohort as proof"
                ),
                "decision_use": "quote_audit_repair_collection_only_not_trade_authorisation",
            }
        )
    blockers.sort(
        key=lambda row: (
            _num(row.get("quote_conflict_round_trips"))
            + _num(row.get("quote_unverified_round_trips"))
            + _num(row.get("quote_other_blocked_round_trips"))
            + _num(row.get("proof_snapshot_missing_round_trips")),
            abs(_num(row.get("excluded_from_audit_pnl_usdc"))),
            abs(_num(row.get("raw_pnl_usdc"))),
        ),
        reverse=True,
    )
    queries: list[str] = []
    for row in blockers:
        query = str(row.get("recommended_collection_query") or "").strip()
        if query and query not in queries:
            queries.append(query)
        if len(queries) >= max_rows:
            break
    return {
        "status": "quote_audit_blockers_present" if blockers else "ok_or_no_quote_audit_blockers",
        "source": source,
        "collection_queries": queries,
        "blocked_cohorts": blockers[:max_rows],
        "blocked_cohort_count": len(blockers),
        "excluded_from_audit_pnl_usdc": sum(_num(row.get("excluded_from_audit_pnl_usdc")) for row in blockers),
        "decision_use": "collection_only_not_trade_authorisation",
    }


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

    promotion_review = build_promotion_review(cfg)
    goal_plan = build_goal_plan(cfg)
    price_action_feedback = read_json(governance / "price_action_feedback.json", default={}) or {}
    if not isinstance(price_action_feedback, dict):
        price_action_feedback = {}
    edge_attribution = read_json(governance / "edge_attribution.json", default={}) or {}
    if not isinstance(edge_attribution, dict):
        edge_attribution = {}
    edge_focus = _edge_attribution_focus(edge_attribution)
    paper_round_trip_summary = read_json(cfg.output_root / "polymarket_price_action" / "paper_broker_round_trip_summary.json", default={}) or {}
    if not isinstance(paper_round_trip_summary, dict):
        paper_round_trip_summary = {}
    quote_audit_focus = _quote_audit_focus(paper_round_trip_summary)
    closing_line_value = read_json(governance / "closing_line_value.json", default={}) or {}
    if not isinstance(closing_line_value, dict):
        closing_line_value = {}
    algo_sweep = read_json(cfg.output_root / "polymarket_algo" / "algo_sweep_summary.json", default={}) or {}
    if not isinstance(algo_sweep, dict):
        algo_sweep = {}
    evidence_inputs = _research_evidence_inputs(edge_attribution, closing_line_value, algo_sweep)
    priority_adjustments = {
        str(row.get("cohort") or ""): _num(row.get("priority_delta"))
        for row in evidence_inputs.get("priority_adjustments", [])
        if isinstance(row, dict)
    }
    for row in focus_rows:
        delta = priority_adjustments.get(str(row.get("cohort") or ""))
        if not delta:
            continue
        base_priority = _num(row.get("priority_score"))
        row["base_priority_score"] = row.get("priority_score")
        row["priority_score"] = round(max(0.0, base_priority + delta), 4)
        row["research_evidence_priority_delta"] = round(delta, 4)
    focus_rows.sort(key=lambda item: _num(item.get("priority_score")), reverse=True)

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
    current_positive_payload = _current_positive_analogue_targets(cfg, price_action_paper)
    current_positive_targets = current_positive_payload.get("targets", [])
    current_positive_blocked_targets = current_positive_payload.get("blocked_targets", [])
    current_positive_queries = _current_positive_analogue_queries(current_positive_targets)
    side_missing_payload = _side_missing_analogue_targets(price_action_paper)
    side_missing_queries = side_missing_payload.get("collection_queries", [])
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
        elif current_positive_blocked_targets:
            blocked = current_positive_blocked_targets[0]
            next_action = (
                "Current positive analogues exist, but their asks are too high to ever clear the model's "
                f"positive-label hurdle; best max possible return={_num(blocked.get('max_possible_return')):.4f} "
                f"vs required {_num(blocked.get('model_label_minimum_return')):.4f}. "
                "Do not waste scout capacity on these exact rows; collect lower-ask analogues instead."
                + (f" Model blocker: {blocker_text}." if blocker_text else "")
            )
        elif side_missing_queries:
            target = (side_missing_payload.get("targets") or [{}])[0]
            roi = _num(target.get("side_agnostic_validation_roi"))
            next_action = (
                "Current rows have positive side-agnostic historical analogues, but missing BUY/SELL side context keeps "
                f"paper gates closed; prioritise {', '.join(side_missing_queries[:4])} side/context collection. "
                f"Best side-agnostic validation ROI={roi:.4f}."
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
    elif edge_focus.get("positive_forward_edge"):
        best = edge_focus["positive_forward_edge"][0]
        next_action = (
            f"Edge attribution shows positive forward P&L for {best.get('cohort')}; "
            f"collect confirmation rows via {best.get('recommended_collection_query') or 'the same family'} until governance thresholds clear."
        )
    elif edge_focus.get("cost_driven"):
        worst = edge_focus["cost_driven"][0]
        next_action = (
            f"Edge attribution says {worst.get('cohort')} is cost/quote-quality constrained, not automatically a bad thesis; "
            "collect tighter-spread/liquid analogues and fix quote audit before any promotion."
        )
    elif edge_focus.get("model_driven"):
        worst = edge_focus["model_driven"][0]
        next_action = (
            f"Edge attribution says {worst.get('cohort')} losses are model/line-movement driven; "
            "suppress this thesis until a new feature/anchor explains the adverse movement."
        )
    elif quote_audit_focus.get("blocked_cohorts"):
        worst = quote_audit_focus["blocked_cohorts"][0]
        next_action = (
            f"Quote audit is excluding paper P&L for {worst.get('cohort')}; "
            f"collect fresh independent bid/ask snapshots via {worst.get('recommended_collection_query') or 'the same family'} "
            "before using this cohort for the $100/month proof."
        )
    elif evidence_inputs.get("collection_queries_added"):
        next_action = (
            "Post-trade attribution/CLV evidence found collection leads; prioritise "
            f"{', '.join(evidence_inputs.get('collection_queries_added', [])[:4])} while keeping all trade gates closed."
        )
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
    for query in evidence_inputs.get("collection_queries_added", []) or []:
        query = str(query or "").strip()
        if query and query not in collection_queries:
            collection_queries.append(query)
    if model_needs_repricing_data:
        for query in current_positive_queries:
            if query and query not in collection_queries:
                collection_queries.append(query)
        for query in side_missing_queries:
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
    for query in edge_focus.get("collection_queries", []) or []:
        query = str(query or "").strip()
        if query and query not in collection_queries:
            collection_queries.append(query)
    for query in quote_audit_focus.get("collection_queries", []) or []:
        query = str(query or "").strip()
        if query and query not in collection_queries:
            collection_queries.append(query)
    notes: list[str] = []
    if evidence_inputs.get("sweep_note"):
        notes.append(str(evidence_inputs["sweep_note"]))
    if quote_audit_focus.get("blocked_cohorts"):
        notes.append(
            "Quote-audit blockers are being routed into collection queries; this is proof repair only and does not authorise paper/live trades."
        )
    if not collection_queries:
        feedback_queries = [str(query or "").strip() for query in price_action_feedback.get("collection_queries", [])]
        collection_queries = [query for query in feedback_queries if query] or ["bitcoin", "ethereum", "world cup", "tennis"]

    raw_collection_queries = list(collection_queries)
    collection_queries, collection_query_guard = _guard_collection_queries(cfg, collection_queries)

    payload = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
        "summary": next_action,
        "notes": notes,
        "watchlist": focus_rows,
        "raw_collection_queries": raw_collection_queries,
        "collection_queries": collection_queries,
        "collection_query_guard": collection_query_guard,
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
            "side_missing_analogue_queries": side_missing_queries,
            "near_miss_candidate_queries": near_miss_queries,
            "analogue_scan_needs_breadth": analogue_scan_needs_breadth,
        },
        "price_action_side_missing_analogues": side_missing_payload,
        "price_action_current_positive_analogues": {
            "state": (
                "learning_targets_available"
                if current_positive_targets
                else "blocked_by_model_label_headroom"
                if current_positive_blocked_targets
                else "none"
            ),
            "targets": current_positive_targets,
            "blocked_targets": current_positive_blocked_targets,
            "collection_queries": current_positive_queries,
            "model_label_minimum_return": current_positive_payload.get("model_label_minimum_return"),
            "model_label_minimum_bid_edge": current_positive_payload.get("model_label_minimum_bid_edge"),
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
        "edge_attribution": edge_focus,
        "quote_audit_focus": quote_audit_focus,
        "evidence_inputs": evidence_inputs,
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
