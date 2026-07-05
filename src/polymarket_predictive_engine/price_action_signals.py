from __future__ import annotations

from typing import Any

from .config import EngineConfig, load_config
from .price_action_microstructure import build_latest_microstructure_features
from .utils import boolish, now_utc, read_csv_rows, read_json, safe_float, write_csv, write_json
from .worldcup_validation import classify_market_family, is_worldcup_market

OUTPUT_DIRNAME = "polymarket_price_action"
SCOUT_COHORT_FILE = "price_action_scout_cohort_evidence.csv"
SCOUT_ROUND_TRIP_FILE = "price_action_scout_round_trip_evidence.csv"
SCOUT_ENTRY_FILE = "price_action_scout_entries.csv"
MICROSTRUCTURE_CURRENT_FILE = "microstructure_current_candidates.csv"
MICROSTRUCTURE_TRADE_EVENTS_FILE = "microstructure_trade_events.csv"
MODEL_SUMMARY_JSON = "price_action_model_summary.json"
SIGNALS_FILE = "price_action_paper_signals.csv"
REJECTIONS_FILE = "price_action_paper_rejections.csv"
SUMMARY_JSON = "price_action_paper_signal_summary.json"

SIGNAL_FIELDS = [
    "market_id",
    "market_slug",
    "question",
    "category",
    "event_id",
    "correlation_key",
    "signal_cohort",
    "outcome",
    "token_id",
    "side",
    "strategy_name",
    "market_price",
    "executable_price",
    "model_probability",
    "calibrated_probability",
    "gross_edge_before_slippage",
    "edge",
    "expected_value_per_share",
    "liquidity",
    "spread",
    "best_bid",
    "best_ask",
    "top_bid_size",
    "top_ask_size",
    "bid_depth_1pct",
    "ask_depth_1pct",
    "bid_depth_5pct",
    "ask_depth_5pct",
    "websocket_quote_age_seconds",
    "relative_spread",
    "time_to_close_hours",
    "resolution_risk",
    "slippage",
    "confidence",
    "alpha_probability",
    "edge_lower_bound",
    "model_version",
    "feature_set_version",
    "data_snapshot_timestamp",
    "price_action_signal",
    "price_action_evidence_status",
    "price_action_cohort_realized_roi",
    "price_action_cohort_win_rate",
    "price_action_cohort_closed_trades",
    "price_action_cohort_run_rate_usdc",
    "price_action_entry_source",
    "price_action_latest_bid",
    "price_action_latest_ask",
    "historical_analogue_key",
    "historical_analogue_validation_rows",
    "historical_analogue_positive_rows",
    "historical_analogue_validation_roi",
    "historical_analogue_win_rate",
    "historical_analogue_gate",
    "side_agnostic_historical_analogue_key",
    "side_agnostic_historical_analogue_validation_rows",
    "side_agnostic_historical_analogue_positive_rows",
    "side_agnostic_historical_analogue_validation_roi",
    "side_agnostic_historical_analogue_win_rate",
    "side_agnostic_historical_analogue_gate",
    "exit_policy_id",
    "max_forward_observations",
    "take_profit_return",
    "stop_loss_return",
    "take_profit_min_usdc",
    "minimum_hold_minutes_before_exit",
    "max_hold_minutes_before_exit",
    "max_stake_usdc",
    "priority_score",
]

REJECTION_FIELDS = [
    "market_slug",
    "outcome",
    "token_id",
    "source",
    "family",
    "latest_bid",
    "latest_ask",
    "latest_spread",
    "relative_spread",
    "liquidity",
    "exit_policy_id",
    "signal_cohort",
    "round_trip_status",
    "historical_analogue_key",
    "historical_analogue_validation_rows",
    "historical_analogue_positive_rows",
    "historical_analogue_validation_roi",
    "historical_analogue_win_rate",
    "historical_analogue_gate",
    "side_agnostic_historical_analogue_key",
    "side_agnostic_historical_analogue_validation_rows",
    "side_agnostic_historical_analogue_positive_rows",
    "side_agnostic_historical_analogue_validation_roi",
    "side_agnostic_historical_analogue_win_rate",
    "side_agnostic_historical_analogue_gate",
    "rejection_reason",
]


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    value = cfg.raw.get("price_action_paper", {})
    return value if isinstance(value, dict) else {}


def _enabled(settings: dict[str, Any]) -> bool:
    return boolish(settings.get("enabled", True))


def _token_id(row: dict[str, Any]) -> str:
    return str(row.get("token_id") or row.get("asset_id") or row.get("outcome_token_id") or "").strip()


def _cohort_name(row: dict[str, Any]) -> str:
    return str(row.get("signal_cohort") or "").strip()


GENERIC_FAMILY_VALUES = {
    "",
    "unknown",
    "uncategorised",
    "uncategorized",
    "sport",
    "sports",
    "sports_other",
    "worldcup",
    "world cup",
    "world_cup",
}


def _raw_family(row: dict[str, Any]) -> str:
    return str(row.get("family") or row.get("category") or "").strip()


def _market_family(row: dict[str, Any]) -> str:
    """Return the evidence-family bucket used by price-action gates.

    Liquidity discovery can carry broad or stale labels such as ``worldcup``
    even when the row text is plainly an esports or crypto market.  Use the
    shared metadata classifier to refine only those generic buckets; keep
    already-specific families intact so this cannot launder weak evidence into
    a different, hand-picked cohort.
    """
    raw = _raw_family(row)
    raw_key = raw.lower().replace(" ", "_")
    classified = str(classify_market_family(row) or "").strip()
    if classified and classified != "unknown" and is_worldcup_market(row):
        return classified
    if classified and classified != "unknown" and (not raw or raw_key in GENERIC_FAMILY_VALUES):
        return classified
    return raw or classified or "unknown"


def _approved_cohorts(cohort_rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    approved: dict[str, dict[str, str]] = {}
    for row in cohort_rows:
        cohort = _cohort_name(row)
        if not cohort:
            continue
        if boolish(row.get("price_action_review_candidate")):
            approved[cohort] = row
    return approved


def _approved_feedback_microstructure_cohorts(cfg: EngineConfig) -> dict[str, dict[str, Any]]:
    payload = read_json(cfg.governance_root / "price_action_feedback.json", default={}) or {}
    if not isinstance(payload, dict):
        return {}
    approved: dict[str, dict[str, Any]] = {}
    rows: list[Any] = []
    for key in ("promotion_candidate_preview", "top_cohorts"):
        value = payload.get(key)
        if isinstance(value, list):
            rows.extend(value)
    for row in rows:
        if not isinstance(row, dict):
            continue
        source = str(row.get("source") or "")
        if source not in {"microstructure", "microstructure_family", "microstructure_exit_policy"}:
            continue
        cohort = str(row.get("cohort") or "").strip()
        if not cohort or not boolish(row.get("promotion_ready")):
            continue
        if str(row.get("action") or "") != "candidate_for_forward_shadow_microstructure":
            continue
        current = approved.get(cohort)
        current_score = safe_float(current.get("priority_score")) if current else None
        new_score = safe_float(row.get("priority_score"))
        if current is None or (new_score or 0.0) >= (current_score or 0.0):
            approved[cohort] = row
    return approved


def _paper_confirmation_candidates(cfg: EngineConfig, settings: dict[str, Any]) -> list[dict[str, Any]]:
    if not boolish(settings.get("paper_confirmation_enabled", True)):
        return []
    payload = read_json(cfg.governance_root / "price_action_feedback.json", default={}) or {}
    if not isinstance(payload, dict):
        return []
    rows = payload.get("paper_confirmation_preview", [])
    if not isinstance(rows, list):
        return []
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cohort = str(row.get("cohort") or "").strip()
        if not cohort:
            continue
        if not boolish(row.get("trusted_for_goal")):
            continue
        if str(row.get("forward_edge_blocker") or "").strip():
            continue
        shadow_ok = (safe_float(row.get("forward_shadow_pnl_usdc")) or 0.0) > 0 and (
            safe_float(row.get("forward_shadow_roi")) or 0.0
        ) > 0
        paper_ok = (safe_float(row.get("forward_paper_pnl_usdc")) or 0.0) > 0 and (
            safe_float(row.get("forward_paper_roi")) or 0.0
        ) > 0
        if not (shadow_ok or paper_ok):
            continue
        candidates.append(row)
    candidates.sort(key=lambda item: safe_float(item.get("priority_score")) or 0.0, reverse=True)
    return candidates


def _price_bucket(price: float | None) -> str:
    if price is None:
        return "na"
    if price < 0.05:
        return "<5c"
    if price < 0.10:
        return "5-10c"
    if price < 0.20:
        return "10-20c"
    if price < 0.40:
        return "20-40c"
    if price < 0.60:
        return "40-60c"
    return "60c+"


def _spread_bucket(spread: float | None) -> str:
    if spread is None:
        return "na"
    if spread <= 0.001:
        return "<=0.1c"
    if spread <= 0.005:
        return "<=0.5c"
    if spread <= 0.01:
        return "<=1c"
    if spread <= 0.02:
        return "<=2c"
    return ">2c"


def _historical_side(row: dict[str, Any]) -> str:
    return str(row.get("current_side") or row.get("price_change_side") or "").strip().upper()


def _historical_analogue_key(row: dict[str, Any], *, side_override: str | None = None) -> str:
    family = _market_family(row)
    ask = safe_float(row.get("entry_ask") or row.get("latest_ask") or row.get("best_ask"))
    bid = safe_float(row.get("entry_bid") or row.get("latest_bid") or row.get("best_bid"))
    spread = safe_float(row.get("entry_spread") or row.get("latest_spread") or row.get("spread"))
    if spread is None and ask is not None and bid is not None:
        spread = max(0.0, ask - bid)
    side = side_override if side_override is not None else _historical_side(row)
    return f"{family}|ask={_price_bucket(ask)}|spread={_spread_bucket(spread)}|side={side}"


def _historical_analogue_stats(cfg: EngineConfig, settings: dict[str, Any]) -> dict[str, dict[str, Any]]:
    min_rows = int(safe_float(settings.get("paper_confirmation_min_historical_analogue_validation_rows")) or 3)
    min_positive = int(safe_float(settings.get("paper_confirmation_min_historical_analogue_positive_rows")) or 1)
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in read_csv_rows(cfg.output_root / OUTPUT_DIRNAME / MICROSTRUCTURE_TRADE_EVENTS_FILE):
        if str(row.get("split") or "").strip().lower() != "validation":
            continue
        grouped.setdefault(_historical_analogue_key(row), []).append(row)
        grouped.setdefault(_historical_analogue_key(row, side_override="ANY"), []).append(row)
    stats: dict[str, dict[str, Any]] = {}
    for key, rows in grouped.items():
        stake = sum(safe_float(row.get("stake_usdc")) or 0.0 for row in rows)
        pnl = sum(safe_float(row.get("pnl_usdc")) or 0.0 for row in rows)
        positives = [row for row in rows if (safe_float(row.get("roi")) or 0.0) > 0]
        roi = pnl / stake if stake > 0 else 0.0
        win_rate = len(positives) / len(rows) if rows else 0.0
        state = "positive_historical_analogue"
        if len(rows) < min_rows:
            state = "insufficient_historical_analogue_rows"
        elif len(positives) < min_positive:
            state = "no_positive_historical_analogue_examples"
        elif pnl <= 0 or roi <= 0:
            state = "historical_analogue_not_profitable_after_spread"
        stats[key] = {
            "key": key,
            "state": state,
            "validation_rows": len(rows),
            "positive_rows": len(positives),
            "validation_pnl_usdc": pnl,
            "validation_roi": roi,
            "win_rate": win_rate,
            "minimum_validation_rows": min_rows,
            "minimum_positive_rows": min_positive,
            "side_agnostic": key.endswith("|side=ANY"),
        }
    return stats


def _move_bucket(value: float | None) -> str:
    if value is None:
        return "na"
    if value >= 0.02:
        return "up>=2c"
    if value >= 0.005:
        return "up0.5-2c"
    if value > -0.005:
        return "flat"
    if value > -0.02:
        return "down0.5-2c"
    return "down>=2c"


def _pressure_bucket(row: dict[str, Any]) -> str:
    net_events = safe_float(row.get("net_buy_events")) or 0.0
    net_size = safe_float(row.get("net_buy_size")) or 0.0
    if net_events >= 2 or net_size >= 100:
        return "buy_pressure"
    if net_events <= -2 or net_size <= -100:
        return "sell_pressure"
    return "neutral"


def _trade_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    stake = sum(safe_float(row.get("stake_usdc")) or 0.0 for row in rows)
    pnl = sum(safe_float(row.get("pnl_usdc")) or 0.0 for row in rows)
    wins = sum(1 for row in rows if (safe_float(row.get("pnl_usdc")) or 0.0) > 0)
    positive_by_token: dict[str, float] = {}
    for row in rows:
        token = _token_id(row)
        positive_by_token[token] = positive_by_token.get(token, 0.0) + max(0.0, safe_float(row.get("pnl_usdc")) or 0.0)
    positive_total = sum(positive_by_token.values())
    return {
        "rows": len(rows),
        "stake_usdc": stake,
        "pnl_usdc": pnl,
        "roi": pnl / stake if stake > 0 else 0.0,
        "win_rate": wins / len(rows) if rows else 0.0,
        "positive_rows": wins,
        "top_positive_token_pnl_share": max(positive_by_token.values()) / positive_total if positive_total > 0 else 0.0,
    }


def _historical_breadth_components(row: dict[str, Any]) -> dict[str, str]:
    family = _market_family(row)
    ask = safe_float(row.get("entry_ask") or row.get("latest_ask") or row.get("best_ask"))
    spread = safe_float(row.get("entry_spread") or row.get("latest_spread") or row.get("spread"))
    side = str(row.get("current_side") or row.get("price_change_side") or "").strip().upper() or "NONE"
    return {
        "family": family,
        "ask_bucket": _price_bucket(ask),
        "spread_bucket": _spread_bucket(spread),
        "side": side,
        "bid_move_bucket": _move_bucket(safe_float(row.get("bid_move_abs"))),
        "pressure_bucket": _pressure_bucket(row),
    }


def _historical_breadth_query(key_parts: tuple[str, ...]) -> str:
    text = " ".join(key_parts).lower()
    if "btc" in text or "bitcoin" in text:
        return "btc updown" if "updown" in text else "bitcoin"
    if "xrp" in text or "ripple" in text:
        return "xrp updown"
    if "sol" in text or "solana" in text:
        return "solana updown" if "updown" in text else "solana"
    if "eth" in text or "ethereum" in text:
        return "eth updown" if "updown" in text else "ethereum"
    if "esports" in text:
        return "esports"
    if "macro_rates" in text or "fed" in text or "rates" in text:
        return "fed"
    if "macro_economy" in text or "economy" in text or "inflation" in text:
        return "economy"
    if "tennis" in text:
        return "tennis"
    if "worldcup" in text or "world cup" in text or "sports_other" in text:
        return "world cup"
    if "sports" in text:
        return "sports"
    if "crypto" in text:
        return "bitcoin"
    return ""


def _historical_breadth_scan(cfg: EngineConfig, settings: dict[str, Any]) -> dict[str, Any]:
    """Cross-check all labelled buy-at-ask/sell-at-bid events for robust pockets.

    The exact current analogue gate is intentionally strict. This broader scan
    answers the operator's next question: across the whole 3-day historical
    event tape, are there any coarse market/price/spread/order-flow buckets
    whose train and validation results both survive bid/ask costs?
    """
    rows = read_csv_rows(cfg.output_root / OUTPUT_DIRNAME / MICROSTRUCTURE_TRADE_EVENTS_FILE)
    train_rows = [row for row in rows if str(row.get("split") or "").strip().lower() == "train"]
    validation_rows = [row for row in rows if str(row.get("split") or "").strip().lower() == "validation"]
    min_train_rows = int(safe_float(settings.get("historical_breadth_min_train_rows")) or 3)
    min_validation_rows = int(
        safe_float(settings.get("historical_breadth_min_validation_rows"))
        or safe_float(settings.get("paper_confirmation_min_historical_analogue_validation_rows"))
        or 3
    )
    min_validation_roi = float(safe_float(settings.get("historical_breadth_min_validation_roi")) or 0.03)
    min_train_roi = float(safe_float(settings.get("historical_breadth_min_train_roi")) or 0.0)
    max_concentration = float(safe_float(settings.get("historical_breadth_max_single_token_positive_pnl_share")) or 0.70)
    specs: list[tuple[str, tuple[str, ...]]] = [
        ("family_price_spread", ("family", "ask_bucket", "spread_bucket")),
        ("family_price_spread_side", ("family", "ask_bucket", "spread_bucket", "side")),
        (
            "family_price_spread_move_pressure",
            ("family", "ask_bucket", "spread_bucket", "bid_move_bucket", "pressure_bucket"),
        ),
        ("price_spread_move_pressure", ("ask_bucket", "spread_bucket", "bid_move_bucket", "pressure_bucket")),
        ("family_price", ("family", "ask_bucket")),
    ]
    grouped: dict[str, dict[tuple[str, ...], dict[str, list[dict[str, Any]]]]] = {
        spec_id: {} for spec_id, _ in specs
    }
    for row in rows:
        split = str(row.get("split") or "").strip().lower()
        if split not in {"train", "validation"}:
            continue
        components = _historical_breadth_components(row)
        for spec_id, names in specs:
            key = tuple(components[name] for name in names)
            bucket = grouped[spec_id].setdefault(key, {"train": [], "validation": []})
            bucket[split].append(row)

    robust_buckets: list[dict[str, Any]] = []
    near_positive_buckets: list[dict[str, Any]] = []
    spec_summaries: list[dict[str, Any]] = []
    positive_validation_buckets = 0
    for spec_id, buckets in grouped.items():
        spec_robust: list[dict[str, Any]] = []
        spec_positive_validation = 0
        for key, split_rows in buckets.items():
            train_metrics = _trade_metrics(split_rows.get("train", []))
            validation_metrics = _trade_metrics(split_rows.get("validation", []))
            if validation_metrics["pnl_usdc"] > 0 and validation_metrics["positive_rows"] > 0:
                positive_validation_buckets += 1
                spec_positive_validation += 1
            blockers: list[str] = []
            if train_metrics["rows"] < min_train_rows:
                blockers.append(f"train_rows<{min_train_rows}")
            if validation_metrics["rows"] < min_validation_rows:
                blockers.append(f"validation_rows<{min_validation_rows}")
            if train_metrics["roi"] < min_train_roi:
                blockers.append("train_roi_below_minimum")
            if validation_metrics["pnl_usdc"] <= 0:
                blockers.append("validation_pnl_not_positive")
            if validation_metrics["roi"] < min_validation_roi:
                blockers.append("validation_roi_below_minimum")
            if validation_metrics["top_positive_token_pnl_share"] > max_concentration:
                blockers.append("positive_pnl_concentrated_in_one_token")
            passed = (
                train_metrics["rows"] >= min_train_rows
                and validation_metrics["rows"] >= min_validation_rows
                and train_metrics["roi"] >= min_train_roi
                and validation_metrics["pnl_usdc"] > 0
                and validation_metrics["roi"] >= min_validation_roi
                and validation_metrics["top_positive_token_pnl_share"] <= max_concentration
            )
            query = _historical_breadth_query(key)
            if validation_metrics["pnl_usdc"] > 0 and validation_metrics["positive_rows"] > 0 and not passed:
                near_positive_buckets.append(
                    {
                        "spec_id": spec_id,
                        "key": "|".join(key),
                        "recommended_collection_query": query,
                        "blockers": blockers,
                        "train_rows": train_metrics["rows"],
                        "train_roi": train_metrics["roi"],
                        "validation_rows": validation_metrics["rows"],
                        "validation_positive_rows": validation_metrics["positive_rows"],
                        "validation_pnl_usdc": validation_metrics["pnl_usdc"],
                        "validation_roi": validation_metrics["roi"],
                        "validation_win_rate": validation_metrics["win_rate"],
                        "validation_top_positive_token_pnl_share": validation_metrics["top_positive_token_pnl_share"],
                    }
                )
            if not passed:
                continue
            payload = {
                "spec_id": spec_id,
                "key": "|".join(key),
                "recommended_collection_query": query,
                "train_rows": train_metrics["rows"],
                "train_roi": train_metrics["roi"],
                "validation_rows": validation_metrics["rows"],
                "validation_positive_rows": validation_metrics["positive_rows"],
                "validation_pnl_usdc": validation_metrics["pnl_usdc"],
                "validation_roi": validation_metrics["roi"],
                "validation_win_rate": validation_metrics["win_rate"],
                "validation_top_positive_token_pnl_share": validation_metrics["top_positive_token_pnl_share"],
            }
            spec_robust.append(payload)
            robust_buckets.append(payload)
        spec_robust.sort(
            key=lambda item: (
                safe_float(item.get("validation_roi")) or -999.0,
                safe_float(item.get("validation_pnl_usdc")) or -999.0,
                safe_float(item.get("validation_rows")) or 0.0,
            ),
            reverse=True,
        )
        spec_summaries.append(
            {
                "spec_id": spec_id,
                "tested_buckets": len(buckets),
                "positive_validation_buckets": spec_positive_validation,
                "robust_positive_buckets": len(spec_robust),
                "top_robust_buckets": spec_robust[:5],
            }
        )
    robust_buckets.sort(
        key=lambda item: (
            safe_float(item.get("validation_roi")) or -999.0,
            safe_float(item.get("validation_pnl_usdc")) or -999.0,
            safe_float(item.get("validation_rows")) or 0.0,
            ),
        reverse=True,
    )
    near_positive_buckets.sort(
        key=lambda item: (
            -len(item.get("blockers", [])),
            safe_float(item.get("validation_roi")) or -999.0,
            safe_float(item.get("validation_pnl_usdc")) or -999.0,
            safe_float(item.get("validation_rows")) or 0.0,
        ),
        reverse=True,
    )
    recommended_queries: list[str] = []
    for bucket in robust_buckets + near_positive_buckets:
        query = str(bucket.get("recommended_collection_query") or "").strip()
        if query and query not in recommended_queries:
            recommended_queries.append(query)
    validation_metrics = _trade_metrics(validation_rows)
    if not rows:
        state = "no_historical_trade_events"
        next_action = "Collect websocket bid/ask variation before promoting any repricing model."
    elif robust_buckets:
        state = "robust_positive_historical_buckets_found"
        next_action = "Route robust buckets through current executable signal and forward paper-confirmation gates."
    elif near_positive_buckets:
        state = "positive_validation_pockets_not_robust"
        target = ", ".join(recommended_queries[:4]) if recommended_queries else "the near-positive families"
        next_action = (
            f"Collect more {target} bid/ask rows matching the near-positive historical buckets; "
            "positives exist but fail train/size/concentration gates."
        )
    else:
        state = "no_positive_historical_breadth_after_spread"
        next_action = "Do not force trades; the current all-event historical tape is negative after bid/ask costs."
    return {
        "state": state,
        "decision_use": "All-event historical check: buy at ask, later sell/mark at bid, grouped by price/spread/family/order-flow.",
        "next_action": next_action,
        "total_events": len(rows),
        "train_events": len(train_rows),
        "validation_events": len(validation_rows),
        "validation_positive_rows": validation_metrics["positive_rows"],
        "validation_pnl_usdc": validation_metrics["pnl_usdc"],
        "validation_roi": validation_metrics["roi"],
        "validation_win_rate": validation_metrics["win_rate"],
        "positive_validation_buckets": positive_validation_buckets,
        "robust_positive_buckets": len(robust_buckets),
        "near_positive_buckets": len(near_positive_buckets),
        "recommended_collection_queries": recommended_queries,
        "thresholds": {
            "min_train_rows": min_train_rows,
            "min_validation_rows": min_validation_rows,
            "min_train_roi": min_train_roi,
            "min_validation_roi": min_validation_roi,
            "max_single_token_positive_pnl_share": max_concentration,
        },
        "specs": spec_summaries,
        "top_robust_buckets": robust_buckets[:10],
        "top_near_positive_buckets": near_positive_buckets[:10],
        "paper_only": True,
    }


def _empty_historical_analogue(key: str, settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": key,
        "state": "no_historical_analogue_rows",
        "validation_rows": 0,
        "positive_rows": 0,
        "validation_pnl_usdc": 0.0,
        "validation_roi": 0.0,
        "win_rate": 0.0,
        "minimum_validation_rows": int(safe_float(settings.get("paper_confirmation_min_historical_analogue_validation_rows")) or 3),
        "minimum_positive_rows": int(safe_float(settings.get("paper_confirmation_min_historical_analogue_positive_rows")) or 1),
        "side_agnostic": False,
    }


def _historical_analogue_for_row(
    row: dict[str, Any],
    stats: dict[str, dict[str, Any]],
    settings: dict[str, Any],
) -> dict[str, Any]:
    key = _historical_analogue_key(row)
    exact = stats.get(key)
    if exact is not None:
        return exact
    if _historical_side(row):
        return _empty_historical_analogue(key, settings)

    side_agnostic_key = _historical_analogue_key(row, side_override="ANY")
    side_agnostic = stats.get(side_agnostic_key)
    if side_agnostic is None:
        return _empty_historical_analogue(key, settings)
    if side_agnostic.get("state") != "positive_historical_analogue":
        return _empty_historical_analogue(key, settings)
    return {
        **_empty_historical_analogue(key, settings),
        "state": "side_missing_positive_historical_analogue_shadow_only",
        "side_agnostic_key": side_agnostic_key,
        "side_agnostic_state": side_agnostic.get("state", ""),
        "side_agnostic_validation_rows": side_agnostic.get("validation_rows", 0),
        "side_agnostic_positive_rows": side_agnostic.get("positive_rows", 0),
        "side_agnostic_validation_pnl_usdc": side_agnostic.get("validation_pnl_usdc", 0.0),
        "side_agnostic_validation_roi": side_agnostic.get("validation_roi", 0.0),
        "side_agnostic_win_rate": side_agnostic.get("win_rate", 0.0),
        "side_agnostic_shadow_only": True,
    }


def _attach_historical_analogue(row: dict[str, Any], analogue: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "historical_analogue_key": analogue.get("key", ""),
        "historical_analogue_validation_rows": analogue.get("validation_rows", 0),
        "historical_analogue_positive_rows": analogue.get("positive_rows", 0),
        "historical_analogue_validation_roi": analogue.get("validation_roi", 0.0),
        "historical_analogue_win_rate": analogue.get("win_rate", 0.0),
        "historical_analogue_gate": analogue.get("state", ""),
        "side_agnostic_historical_analogue_key": analogue.get("side_agnostic_key", ""),
        "side_agnostic_historical_analogue_validation_rows": analogue.get("side_agnostic_validation_rows", ""),
        "side_agnostic_historical_analogue_positive_rows": analogue.get("side_agnostic_positive_rows", ""),
        "side_agnostic_historical_analogue_validation_roi": analogue.get("side_agnostic_validation_roi", ""),
        "side_agnostic_historical_analogue_win_rate": analogue.get("side_agnostic_win_rate", ""),
        "side_agnostic_historical_analogue_gate": analogue.get("side_agnostic_state", ""),
    }


def _historical_blocker_collectability(item: dict[str, Any]) -> tuple[int, float, float, float, float]:
    """Rank blocked paper-confirmation rows by whether more data can change them.

    Large buckets with many validation rows, zero positives, and negative ROI
    are mature negative evidence.  They should remain visible for audit, but
    they should not consume the scarce "collect next" slots ahead of buckets
    that are under-sampled, side-missing, or near-profitable after spread.
    """
    candidate_gate = str(item.get("candidate_gate") or "").strip()
    historical_gate = str(item.get("historical_analogue_gate") or candidate_gate).strip()
    validation_rows = safe_float(item.get("historical_analogue_validation_rows")) or 0.0
    positive_rows = safe_float(item.get("historical_analogue_positive_rows")) or 0.0
    validation_roi = safe_float(item.get("historical_analogue_validation_roi")) or 0.0
    spread = safe_float(item.get("latest_spread")) or 999.0
    mature_rows = 50.0

    if candidate_gate == "entry_price_outside_risk_band":
        bucket = 0
        evidence_gap = 0.0
    elif historical_gate == "side_missing_positive_historical_analogue_shadow_only":
        bucket = 6
        evidence_gap = max(0.0, mature_rows - validation_rows)
    elif historical_gate in {"no_historical_analogue_rows", "insufficient_historical_analogue_rows"}:
        bucket = 5
        evidence_gap = max(0.0, mature_rows - validation_rows)
    elif historical_gate == "no_positive_historical_analogue_examples":
        if validation_rows >= mature_rows and positive_rows <= 0 and validation_roi <= 0:
            bucket = 1
            evidence_gap = 0.0
        else:
            bucket = 4
            evidence_gap = max(0.0, mature_rows - validation_rows)
    elif historical_gate == "historical_analogue_not_profitable_after_spread":
        if positive_rows > 0 and validation_roi > -0.02:
            bucket = 3
        else:
            bucket = 1
        evidence_gap = max(0.0, mature_rows - validation_rows)
    else:
        bucket = 2
        evidence_gap = max(0.0, mature_rows - validation_rows)
    return (
        bucket,
        evidence_gap,
        positive_rows,
        validation_roi,
        -spread,
    )


def _paper_confirmation_blocked_preview_sort_key(item: dict[str, Any]) -> tuple[int, float, float, float, float]:
    return _historical_blocker_collectability(item)


def _balanced_blocked_preview(
    rows: list[dict[str, Any]],
    *,
    limit: int = 10,
    max_first_pass_per_family: int = 2,
) -> list[dict[str, Any]]:
    """Return a compact blocker preview without hiding minority families.

    This is reporting/collection guidance only.  It deliberately does not
    change approval gates; it prevents the operator-facing "next evidence"
    list from being monopolised by the first large, liquid family.
    """
    ranked = sorted(rows, key=_paper_confirmation_blocked_preview_sort_key, reverse=True)
    selected: list[dict[str, Any]] = []
    selected_indices: set[int] = set()
    family_counts: dict[str, int] = {}

    def family_key(row: dict[str, Any]) -> str:
        return _market_family(row)

    for index, row in enumerate(ranked):
        family = family_key(row)
        if family_counts.get(family, 0) >= max_first_pass_per_family:
            continue
        selected.append(row)
        selected_indices.add(index)
        family_counts[family] = family_counts.get(family, 0) + 1
        if len(selected) >= limit:
            return selected

    for index, row in enumerate(ranked):
        if index in selected_indices:
            continue
        selected.append(row)
        if len(selected) >= limit:
            break
    return selected


def _paper_confirmation_current_candidates(
    cfg: EngineConfig,
    settings: dict[str, Any],
    paper_confirmation: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Match trusted shadow confirmation theses to fresh executable bid/ask rows.

    The original paper-confirmation bridge only promoted already-open scout
    round-trip rows. That was conservative, but it could leave a trusted
    shadow thesis idle when the live websocket book had a fresh matching market.
    This helper remains evidence-only: it creates paper-confirmation probes
    from current best bid/ask rows only after the trusted shadow feedback gate
    has already identified the cohort.
    """
    if not paper_confirmation:
        return [], {
            "state": "no_trusted_shadow_confirmation_candidates",
            "fresh_matches": 0,
            "approved": 0,
            "blocked": 0,
        }
    max_candidates = int(safe_float(settings.get("paper_confirmation_max_current_candidates")) or 8)
    require_positive_history = boolish(settings.get("paper_confirmation_require_positive_historical_analogue", True))
    analogue_stats = _historical_analogue_stats(cfg, settings)
    latest_rows = build_latest_microstructure_features(cfg)
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    fresh_matches = 0
    blocked = 0
    blocked_by_state: dict[str, int] = {}
    blocked_by_family: dict[str, int] = {}
    blocked_preview: list[dict[str, Any]] = []
    approved_analogue_keys: set[str] = set()
    for feature in latest_rows:
        confirmation = _confirmation_candidate_for_row(feature, paper_confirmation)
        if confirmation is None:
            continue
        fresh_matches += 1
        token = _token_id(feature)
        cohort = str(confirmation.get("cohort") or "").strip()
        if not token or not cohort:
            continue
        ask = safe_float(feature.get("entry_ask"))
        bid = safe_float(feature.get("entry_bid"))
        if ask is None or bid is None or not 0 < bid < ask < 1:
            continue
        key = (token, cohort)
        if key in seen:
            continue
        seen.add(key)
        spread = safe_float(feature.get("entry_spread"))
        if spread is None:
            spread = max(0.0, ask - bid)
        relative_spread = _relative_spread(spread, ask)
        family = _market_family(feature)
        analogue = _historical_analogue_for_row(feature, analogue_stats, settings)
        entry_band_rejection = _entry_price_band_rejection(cfg, ask)
        if entry_band_rejection:
            blocked += 1
            state = "entry_price_outside_risk_band"
            blocked_by_state[state] = blocked_by_state.get(state, 0) + 1
            blocked_by_family[family] = blocked_by_family.get(family, 0) + 1
            blocked_preview.append(
                _attach_historical_analogue(
                    {
                        "source": "paper_confirmation_current_candidate",
                        "signal_cohort": cohort,
                        "token_id": token,
                        "market_slug": feature.get("market_slug", ""),
                        "question": feature.get("question", ""),
                        "family": family,
                        "outcome": feature.get("outcome", ""),
                        "latest_bid": bid,
                        "latest_ask": ask,
                        "latest_spread": spread,
                        "relative_spread": "" if relative_spread is None else relative_spread,
                        "candidate_gate": state,
                        "rejection_reason": entry_band_rejection,
                    },
                    analogue,
                )
            )
            continue
        if require_positive_history and analogue.get("state") != "positive_historical_analogue":
            blocked += 1
            state = str(analogue.get("state") or "unknown")
            blocked_by_state[state] = blocked_by_state.get(state, 0) + 1
            blocked_by_family[family] = blocked_by_family.get(family, 0) + 1
            blocked_preview.append(
                _attach_historical_analogue(
                    {
                        "source": "paper_confirmation_current_candidate",
                        "signal_cohort": cohort,
                        "token_id": token,
                        "market_slug": feature.get("market_slug", ""),
                        "question": feature.get("question", ""),
                        "family": family,
                        "outcome": feature.get("outcome", ""),
                        "latest_bid": bid,
                        "latest_ask": ask,
                        "latest_spread": spread,
                        "relative_spread": "" if relative_spread is None else relative_spread,
                        "candidate_gate": state,
                    },
                    analogue,
                )
            )
            continue
        analogue_key = str(analogue.get("key") or "")
        approved_analogue_keys.add(analogue_key)
        candidates.append(
            _attach_historical_analogue(
                {
                "source": "paper_confirmation_current_candidate",
                "round_trip_status": "open_marked",
                "signal_cohort": cohort,
                "token_id": token,
                "market_slug": feature.get("market_slug", ""),
                "question": feature.get("question", ""),
                "family": family,
                "outcome": feature.get("outcome", ""),
                "latest_time_utc": feature.get("entry_time_utc", ""),
                "latest_bid": bid,
                "latest_ask": ask,
                "latest_midpoint": feature.get("entry_midpoint", ""),
                "latest_spread": spread,
                "relative_spread": "" if relative_spread is None else relative_spread,
                "take_profit_return": confirmation.get("take_profit_return", ""),
                "stop_loss_return": confirmation.get("stop_loss_return", ""),
                "min_profit_usdc": confirmation.get("min_profit_usdc", ""),
                "max_hold_minutes_before_exit": confirmation.get("max_hold_minutes_before_exit", ""),
                "priority_score": safe_float(confirmation.get("priority_score")) or 0.0,
                "shadow_only": True,
                },
                analogue,
            )
        )
    candidates.sort(
        key=lambda item: (
            safe_float(item.get("priority_score")) or 0.0,
            -(safe_float(item.get("latest_spread")) or 999.0),
        ),
        reverse=True,
    )
    selected = candidates[:max_candidates]
    if selected:
        state = "historical_analogue_current_candidates_ready"
    elif blocked_by_state.get("entry_price_outside_risk_band", 0) > 0:
        state = "current_candidates_blocked_by_entry_price_band"
    else:
        state = "no_positive_historical_analogue_current_candidate"
    return selected, {
        "state": state,
        "require_positive_historical_analogue": require_positive_history,
        "fresh_matches": fresh_matches,
        "approved": len(candidates),
        "selected": len(selected),
        "blocked": blocked,
        "blocked_by_state": blocked_by_state,
        "blocked_by_family": dict(sorted(blocked_by_family.items())),
        "blocked_preview": _balanced_blocked_preview(blocked_preview, limit=10),
        "blocked_preview_selection": "balanced_by_family_then_gate_strength",
        "positive_historical_analogue_keys": len(approved_analogue_keys),
    }


def _is_low_price_tick_row(row: dict[str, Any], settings: dict[str, Any]) -> bool:
    ask = safe_float(row.get("entry_ask") or row.get("latest_ask"))
    bid = safe_float(row.get("entry_bid") or row.get("latest_bid"))
    spread = safe_float(row.get("entry_spread") or row.get("latest_spread"))
    if spread is None and ask is not None and bid is not None:
        spread = max(0.0, ask - bid)
    relative_spread = _relative_spread(spread, ask)
    min_price = float(safe_float(settings.get("low_price_tick_min_ask")) or 0.01)
    max_price = float(safe_float(settings.get("low_price_tick_max_ask")) or 0.15)
    max_spread = float(safe_float(settings.get("low_price_tick_max_spread")) or 0.01)
    max_relative_spread = float(safe_float(settings.get("low_price_tick_max_relative_spread")) or 0.25)
    min_one_cent_return = float(safe_float(settings.get("low_price_tick_min_one_cent_return")) or 0.03)
    if ask is None or bid is None or not min_price <= ask <= max_price or not 0 < bid < ask:
        return False
    if spread is None or spread > max_spread:
        return False
    if relative_spread is None or relative_spread > max_relative_spread:
        return False
    if 0.01 / max(ask, 0.01) < min_one_cent_return:
        return False
    if boolish(settings.get("low_price_tick_exclude_updown", True)):
        market_slug = str(row.get("market_slug") or "").lower()
        if "updown" in market_slug or "up-or-down" in market_slug:
            return False
    return True


def _current_historical_analogue_scan(
    cfg: EngineConfig,
    settings: dict[str, Any],
    *,
    analogue_stats: dict[str, dict[str, Any]] | None = None,
    latest_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Score every current executable bid/ask row against historical analogues.

    This is an opportunity-coverage diagnostic, not a trade authoriser. A row
    can match a profitable ask-to-future-bid analogue and still need the
    separate cohort/paper-confirmation governance gate before it becomes a
    paper signal.
    """
    stats = analogue_stats if analogue_stats is not None else _historical_analogue_stats(cfg, settings)
    rows = latest_rows if latest_rows is not None else build_latest_microstructure_features(cfg)
    blocked_by_state: dict[str, int] = {}
    family_counts: dict[str, int] = {}
    positive_by_family: dict[str, int] = {}
    positive_preview: list[dict[str, Any]] = []
    blocked_preview: list[dict[str, Any]] = []
    positive_keys: set[str] = set()
    positive_matches = 0
    blocked = 0
    robust_min_validation_roi = float(safe_float(settings.get("historical_breadth_min_validation_roi")) or 0.03)

    for row in rows:
        family = _market_family(row)
        family_counts[family] = family_counts.get(family, 0) + 1
        analogue = _historical_analogue_for_row(row, stats, settings)
        state = str(analogue.get("state") or "unknown")
        if state == "positive_historical_analogue":
            positive_matches += 1
            positive_by_family[family] = positive_by_family.get(family, 0) + 1
            key = str(analogue.get("key") or "")
            if key:
                positive_keys.add(key)
            validation_roi = safe_float(analogue.get("validation_roi")) or 0.0
            recommended_query = _historical_breadth_query(
                (
                    family,
                    str(row.get("market_slug") or ""),
                    str(row.get("question") or ""),
                )
            )
            positive_preview.append(
                {
                    "market_slug": row.get("market_slug", ""),
                    "question": row.get("question", ""),
                    "family": family,
                    "outcome": row.get("outcome", ""),
                    "token_id": _token_id(row),
                    "latest_bid": row.get("entry_bid", ""),
                    "latest_ask": row.get("entry_ask", ""),
                    "latest_spread": row.get("entry_spread", ""),
                    "historical_analogue_key": key,
                    "historical_analogue_validation_rows": analogue.get("validation_rows", 0),
                    "historical_analogue_positive_rows": analogue.get("positive_rows", 0),
                    "historical_analogue_validation_roi": validation_roi,
                    "historical_analogue_win_rate": analogue.get("win_rate", 0.0),
                    "minimum_robust_validation_roi": robust_min_validation_roi,
                    "robust_validation_roi_gap": max(0.0, robust_min_validation_roi - validation_roi),
                    "recommended_collection_query": recommended_query,
                    "trade_authorisation": "diagnostic_only_requires_governed_signal",
                    "forward_shadow_action": (
                        "collect_forward_shadow_bid_ask_evidence_until_robust_and_paper_confirmation_gates_clear"
                    ),
                }
            )
        else:
            blocked += 1
            blocked_by_state[state] = blocked_by_state.get(state, 0) + 1
            key = str(analogue.get("key") or "")
            blocked_preview.append(
                {
                    "market_slug": row.get("market_slug", ""),
                    "question": row.get("question", ""),
                    "family": family,
                    "outcome": row.get("outcome", ""),
                    "token_id": _token_id(row),
                    "latest_bid": row.get("entry_bid", ""),
                    "latest_ask": row.get("entry_ask", ""),
                    "latest_spread": row.get("entry_spread", ""),
                    "historical_analogue_key": key,
                    "historical_analogue_gate": state,
                    "historical_analogue_validation_rows": analogue.get("validation_rows", 0),
                    "historical_analogue_positive_rows": analogue.get("positive_rows", 0),
                    "historical_analogue_validation_roi": analogue.get("validation_roi", 0.0),
                    "historical_analogue_win_rate": analogue.get("win_rate", 0.0),
                }
            )

    positive_preview.sort(
        key=lambda item: (
            safe_float(item.get("historical_analogue_validation_roi")) or -999.0,
            safe_float(item.get("historical_analogue_positive_rows")) or 0.0,
            -(safe_float(item.get("latest_spread")) or 999.0),
        ),
        reverse=True,
    )
    positive_validation_buckets = sum(
        1
        for analogue in stats.values()
        if str(analogue.get("state") or "") == "positive_historical_analogue"
        and not boolish(analogue.get("side_agnostic"))
    )
    if not rows:
        state = "no_current_bid_ask_rows"
        next_action = "Collect fresh websocket bid/ask rows before evaluating current repricing opportunities."
    elif positive_matches:
        state = "current_positive_historical_analogue_available"
        next_action = (
            "Treat current positive analogues as forward-shadow learning targets only; collect fresh bid/ask "
            "movement until robust historical breadth and paper-confirmation governance clear."
        )
    else:
        state = "no_current_positive_historical_analogue"
        next_action = "Broaden live market coverage and keep collecting bid/ask variation; current rows do not match profitable validation buckets."
    return {
        "state": state,
        "decision_use": "Shows whether the current live universe contains historically profitable buy-at-ask / sell-at-bid setups after spread.",
        "next_action": next_action,
        "current_rows": len(rows),
        "positive_matches": positive_matches,
        "blocked": blocked,
        "blocked_by_state": dict(sorted(blocked_by_state.items())),
        "family_counts": dict(sorted(family_counts.items())),
        "positive_by_family": dict(sorted(positive_by_family.items())),
        "validation_buckets": len(stats),
        "positive_validation_buckets": positive_validation_buckets,
        "positive_historical_analogue_keys": len(positive_keys),
        "minimum_robust_validation_roi": robust_min_validation_roi,
        "positive_preview": positive_preview[:10],
        "blocked_preview": sorted(
            blocked_preview,
            key=lambda item: (
                safe_float(item.get("historical_analogue_validation_roi")) or -999.0,
                safe_float(item.get("historical_analogue_positive_rows")) or 0.0,
                safe_float(item.get("historical_analogue_validation_rows")) or 0.0,
                -(safe_float(item.get("latest_spread")) or 999.0),
            ),
            reverse=True,
        )[:20],
        "paper_only": True,
        "trade_authorisation": "diagnostic_only_requires_governed_signal",
    }


def _low_price_tick_evidence(cfg: EngineConfig, settings: dict[str, Any]) -> dict[str, Any]:
    rows = read_csv_rows(cfg.output_root / OUTPUT_DIRNAME / MICROSTRUCTURE_TRADE_EVENTS_FILE)
    filtered = [row for row in rows if _is_low_price_tick_row(row, settings)]
    validation = [row for row in filtered if str(row.get("split") or "").lower() == "validation"]
    positives = [row for row in filtered if (safe_float(row.get("roi")) or 0.0) > 0]
    validation_positives = [row for row in validation if (safe_float(row.get("roi")) or 0.0) > 0]
    pnl = sum(safe_float(row.get("pnl_usdc")) or 0.0 for row in filtered)
    stake = sum(safe_float(row.get("stake_usdc")) or 0.0 for row in filtered)
    validation_pnl = sum(safe_float(row.get("pnl_usdc")) or 0.0 for row in validation)
    validation_stake = sum(safe_float(row.get("stake_usdc")) or 0.0 for row in validation)
    min_validation_positives = int(safe_float(settings.get("low_price_tick_min_validation_positive_examples")) or 1)
    validation_roi = validation_pnl / validation_stake if validation_stake > 0 else 0.0
    state = "collect_more_low_price_tick_evidence"
    if not filtered:
        state = "no_low_price_tick_rows"
    elif len(validation_positives) < min_validation_positives:
        state = "no_positive_validation_low_price_tick_examples"
    elif validation_roi <= 0:
        state = "low_price_tick_validation_roi_not_positive"
    else:
        state = "low_price_tick_validation_candidate"
    return {
        "state": state,
        "rows": len(filtered),
        "positive_rows": len(positives),
        "validation_rows": len(validation),
        "validation_positive_rows": len(validation_positives),
        "roi": pnl / stake if stake > 0 else 0.0,
        "validation_roi": validation_roi,
        "minimum_validation_positive_examples": min_validation_positives,
        "top_positive_examples": validation_positives[:5] or positives[:5],
    }


def _low_price_tick_probe_enabled(
    summary: dict[str, Any],
    settings: dict[str, Any],
    evidence: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    if not boolish(settings.get("low_price_tick_probe_enabled", True)):
        return False, {}
    diagnostics = summary.get("validation_rank_diagnostics") if isinstance(summary.get("validation_rank_diagnostics"), dict) else {}
    missed = diagnostics.get("missed_positive_examples") if isinstance(diagnostics.get("missed_positive_examples"), list) else []
    min_low_price = float(safe_float(settings.get("low_price_tick_min_convexity")) or 0.02)
    qualifying = [
        row
        for row in missed
        if isinstance(row, dict)
        and (safe_float(row.get("low_price_convexity")) or 0.0) >= min_low_price
        and (safe_float(row.get("roi")) or 0.0) > 0
    ]
    evidence_ready = (
        int(safe_float(evidence.get("validation_positive_rows")) or 0)
        >= int(safe_float(evidence.get("minimum_validation_positive_examples")) or 1)
        and (safe_float(evidence.get("validation_roi")) or 0.0) > 0
    )
    if qualifying:
        allow_negative_validation_exploration = boolish(
            settings.get("low_price_tick_allow_missed_positive_without_positive_validation_roi", False)
        )
        if evidence_ready or allow_negative_validation_exploration:
            return True, {"diagnostics": diagnostics, "missed_positive_examples": qualifying, "evidence": evidence}
        return False, {
            "diagnostics": diagnostics,
            "missed_positive_examples": qualifying,
            "evidence": evidence,
            "blocked_by_negative_validation": True,
        }
    return evidence_ready, {"diagnostics": diagnostics, "missed_positive_examples": qualifying, "evidence": evidence}


def _family_allowed_by_low_price_lesson(row: dict[str, Any], lesson: dict[str, Any], settings: dict[str, Any]) -> bool:
    missed = lesson.get("missed_positive_examples") if isinstance(lesson.get("missed_positive_examples"), list) else []
    if not missed:
        return False
    family = str(row.get("family") or row.get("category") or "").strip().lower()
    slug = str(row.get("market_slug") or "").strip().lower()
    families = {str(item.get("family") or "").strip().lower() for item in missed if isinstance(item, dict)}
    if family and family in families:
        return True
    markets = [str(item.get("market_slug") or "").strip().lower() for item in missed if isinstance(item, dict)]
    return any(token and token.split("-on-")[0] in slug for token in markets)


def _low_price_tick_probe_candidates(cfg: EngineConfig, settings: dict[str, Any]) -> list[dict[str, Any]]:
    summary = read_json(cfg.output_root / OUTPUT_DIRNAME / MODEL_SUMMARY_JSON, default={}) or {}
    if not isinstance(summary, dict):
        return []
    evidence = _low_price_tick_evidence(cfg, settings)
    enabled, lesson = _low_price_tick_probe_enabled(summary, settings, evidence)
    if not enabled:
        return []
    max_candidates = int(safe_float(settings.get("low_price_tick_max_candidates")) or 4)
    min_one_cent_return = float(safe_float(settings.get("low_price_tick_min_one_cent_return")) or 0.03)
    rows = build_latest_microstructure_features(cfg)
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if not _is_low_price_tick_row(row, settings):
            continue
        ask = safe_float(row.get("entry_ask"))
        bid = safe_float(row.get("entry_bid"))
        spread = safe_float(row.get("entry_spread"))
        if spread is None and ask is not None and bid is not None:
            spread = max(0.0, ask - bid)
        relative_spread = _relative_spread(spread, ask)
        one_cent_return = 0.01 / max(ask, 0.01)
        if not _family_allowed_by_low_price_lesson(row, lesson, settings) and evidence.get("state") != "low_price_tick_validation_candidate":
            continue
        signal_cohort = f"low_price_tick_probe|{row.get('family') or 'unknown'}|one_cent_return>={min_one_cent_return:.2f}"
        candidates.append(
            {
                "source": "low_price_tick_probe",
                "round_trip_status": "open_marked",
                "signal_cohort": signal_cohort,
                "token_id": row.get("token_id", ""),
                "market_slug": row.get("market_slug", ""),
                "question": row.get("question", ""),
                "family": row.get("family", ""),
                "outcome": row.get("outcome", ""),
                "latest_time_utc": row.get("entry_time_utc", ""),
                "latest_bid": bid,
                "latest_ask": ask,
                "latest_midpoint": row.get("entry_midpoint", ""),
                "latest_spread": spread,
                "relative_spread": relative_spread,
                "take_profit_return": max(
                    min_one_cent_return,
                    float(safe_float(settings.get("low_price_tick_take_profit_return")) or 0.04),
                ),
                "stop_loss_return": float(safe_float(settings.get("low_price_tick_stop_loss_return")) or 0.08),
                "min_profit_usdc": float(safe_float(settings.get("low_price_tick_min_profit_usdc")) or 0.01),
                "max_hold_minutes_before_exit": float(
                    safe_float(settings.get("low_price_tick_max_hold_minutes_before_exit")) or 45.0
                ),
                "one_cent_return": one_cent_return,
                "low_price_convexity": max(0.0, 0.15 - ask),
                "priority_score": one_cent_return / max(relative_spread, 0.001),
                "shadow_only": True,
            }
        )
    candidates.sort(
        key=lambda item: (
            safe_float(item.get("priority_score")) or 0.0,
            safe_float(item.get("one_cent_return")) or 0.0,
        ),
        reverse=True,
    )
    return candidates[:max_candidates]


def _low_price_tick_probe_state(cfg: EngineConfig, settings: dict[str, Any]) -> dict[str, Any]:
    summary = read_json(cfg.output_root / OUTPUT_DIRNAME / MODEL_SUMMARY_JSON, default={}) or {}
    if not isinstance(summary, dict):
        summary = {}
    evidence = _low_price_tick_evidence(cfg, settings)
    enabled, lesson = _low_price_tick_probe_enabled(summary, settings, evidence)
    missed = lesson.get("missed_positive_examples") if isinstance(lesson.get("missed_positive_examples"), list) else []
    return {
        **evidence,
        "enabled_by_governance": bool(enabled),
        "diagnostic_missed_positive_examples": len(missed),
        "gate_reason": (
            "model_missed_low_price_positive_but_validation_lane_negative"
            if missed and not enabled
            else "model_missed_low_price_positive_examples"
            if missed and enabled
            else "validated_low_price_tick_evidence"
            if enabled
            else evidence.get("state", "collect_more_low_price_tick_evidence")
        ),
    }


def _query_family_prefixes(query: str) -> list[str]:
    query = str(query or "").strip().lower()
    if not query:
        return []
    if "world" in query or "cup" in query:
        return ["sports_other", "worldcup", "soccer_match", "world-cup", "fifa"]
    if "esports" in query or "e-sports" in query or "e sports" in query:
        return ["esports"]
    if "tennis" in query:
        return ["tennis"]
    if "bitcoin" in query or "btc" in query:
        return ["crypto_btc"]
    if "ethereum" in query or "eth" in query.split():
        return ["crypto_eth"]
    if "solana" in query or query == "sol":
        return ["crypto_sol"]
    if "xrp" in query or "ripple" in query:
        return ["crypto_xrp"]
    return []


def _candidate_outcome_matches_row(candidate: dict[str, Any], row: dict[str, Any]) -> bool:
    cohort = str(candidate.get("cohort") or "").strip().lower()
    expected = ""
    marker = "outcome="
    if marker in cohort:
        expected = cohort.split(marker, 1)[1].split("|", 1)[0].strip().lower()
    if not expected:
        return True
    actual = str(row.get("outcome") or row.get("selection") or "").strip().lower()
    if not actual:
        return True
    aliases = {
        "up": {"up", "yes"},
        "down": {"down", "no"},
        "yes": {"yes", "up"},
        "no": {"no", "down"},
    }
    return actual in aliases.get(expected, {expected})


def _confirmation_candidate_for_row(row: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    row_cohort = _cohort_name(row).lower()
    family = _market_family(row).lower()
    raw_family = _raw_family(row).lower()
    market_slug = str(row.get("market_slug") or "").strip().lower()
    question = str(row.get("question") or "").strip().lower()
    category = str(row.get("category") or "").strip().lower()
    text = " ".join([row_cohort, family, raw_family, category, market_slug, question])
    for candidate in candidates:
        cohort = str(candidate.get("cohort") or "").strip().lower()
        query = str(candidate.get("recommended_collection_query") or "").strip().lower()
        if not _candidate_outcome_matches_row(candidate, row):
            continue
        if cohort and (cohort == family or cohort in row_cohort or cohort in text):
            return candidate
        prefixes = _query_family_prefixes(query)
        if prefixes and any(family.startswith(prefix) or prefix in text for prefix in prefixes):
            return candidate
    return None


def _entry_index(entry_rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    by_token: dict[str, dict[str, str]] = {}
    for row in entry_rows:
        token = _token_id(row)
        if not token:
            continue
        current = by_token.get(token)
        if current is None or (safe_float(row.get("liquidity")) or 0.0) > (safe_float(current.get("liquidity")) or 0.0):
            by_token[token] = row
    return by_token


def _relative_spread(spread: float | None, price: float | None) -> float | None:
    if spread is None or price is None or price <= 0:
        return None
    return spread / price


def _entry_price_band_rejection(cfg: EngineConfig, price: float | None) -> str:
    """Mirror the broker's base-config entry band before writing paper signals.

    The broker remains authoritative and still re-checks every order.  This
    earlier check prevents impossible paper-signal rows from being advertised
    as broker-ready when the risk layer will deterministically reject them.
    """
    if price is None:
        return ""
    risk = cfg.raw.get("risk", {})
    if not isinstance(risk, dict):
        risk = {}
    minimum_entry_price = float(safe_float(risk.get("minimum_entry_price")) or 0.05)
    maximum_entry_price = float(safe_float(risk.get("maximum_entry_price")) or 0.90)
    if not minimum_entry_price <= price <= maximum_entry_price:
        return f"entry price {price:.4f} outside configured band [{minimum_entry_price:.2f}, {maximum_entry_price:.2f}]"
    return ""


def _reject(row: dict[str, Any], reason: str) -> dict[str, Any]:
    bid = safe_float(row.get("latest_bid") or row.get("entry_bid") or row.get("best_bid"))
    ask = safe_float(row.get("latest_ask") or row.get("entry_ask") or row.get("best_ask"))
    spread = safe_float(row.get("latest_spread") or row.get("entry_spread") or row.get("spread"))
    if spread is None and bid is not None and ask is not None:
        spread = max(0.0, ask - bid)
    relative_spread = _relative_spread(spread, ask)
    liquidity = row.get("liquidity", "")
    return {
        "market_slug": row.get("market_slug", ""),
        "outcome": row.get("outcome", ""),
        "token_id": _token_id(row),
        "source": row.get("source", ""),
        "family": _market_family(row),
        "latest_bid": "" if bid is None else bid,
        "latest_ask": "" if ask is None else ask,
        "latest_spread": "" if spread is None else spread,
        "relative_spread": "" if relative_spread is None else relative_spread,
        "liquidity": liquidity,
        "exit_policy_id": row.get("exit_policy_id", ""),
        "signal_cohort": _cohort_name(row),
        "round_trip_status": row.get("round_trip_status", ""),
        "historical_analogue_key": row.get("historical_analogue_key", ""),
        "historical_analogue_validation_rows": row.get("historical_analogue_validation_rows", ""),
        "historical_analogue_positive_rows": row.get("historical_analogue_positive_rows", ""),
        "historical_analogue_validation_roi": row.get("historical_analogue_validation_roi", ""),
        "historical_analogue_win_rate": row.get("historical_analogue_win_rate", ""),
        "historical_analogue_gate": row.get("historical_analogue_gate", ""),
        "side_agnostic_historical_analogue_key": row.get("side_agnostic_historical_analogue_key", ""),
        "side_agnostic_historical_analogue_validation_rows": row.get("side_agnostic_historical_analogue_validation_rows", ""),
        "side_agnostic_historical_analogue_positive_rows": row.get("side_agnostic_historical_analogue_positive_rows", ""),
        "side_agnostic_historical_analogue_validation_roi": row.get("side_agnostic_historical_analogue_validation_roi", ""),
        "side_agnostic_historical_analogue_win_rate": row.get("side_agnostic_historical_analogue_win_rate", ""),
        "side_agnostic_historical_analogue_gate": row.get("side_agnostic_historical_analogue_gate", ""),
        "rejection_reason": reason,
    }


def _preferred_numeric_metric(row: dict[str, Any], *keys: str) -> float | str:
    fallback: float | None = None
    for key in keys:
        value = safe_float(row.get(key))
        if value is None:
            continue
        if fallback is None:
            fallback = value
        if value > 0:
            return value
    return "" if fallback is None else fallback


def _build_signal(
    row: dict[str, str],
    *,
    cohort: dict[str, str],
    entry: dict[str, str],
    settings: dict[str, Any],
) -> dict[str, Any] | None:
    token = _token_id(row)
    ask = safe_float(row.get("latest_ask"))
    bid = safe_float(row.get("latest_bid"))
    if not token or ask is None or not 0 < ask < 1:
        return None
    if bid is None or bid <= 0:
        return None

    spread = safe_float(row.get("latest_spread"))
    if spread is None:
        spread = max(0.0, ask - bid)
    relative_spread = _relative_spread(spread, ask)

    take_profit_return = float(safe_float(row.get("take_profit_return")) or safe_float(settings.get("take_profit_return")) or 0.08)
    stop_loss_return = float(safe_float(row.get("stop_loss_return")) or safe_float(settings.get("stop_loss_return")) or 0.06)
    min_profit = float(safe_float(row.get("min_profit_usdc")) or safe_float(settings.get("take_profit_min_usdc")) or 0.25)
    min_hold = float(safe_float(settings.get("minimum_hold_minutes_before_exit")) or 0.0)
    max_forward_observations = safe_float(row.get("max_forward_observations"))
    observation_minutes = float(safe_float(settings.get("observation_minutes")) or 1.0)
    max_hold_minutes = safe_float(row.get("max_hold_minutes_before_exit"))
    if max_hold_minutes is None and max_forward_observations is not None and max_forward_observations > 0:
        max_hold_minutes = float(max_forward_observations) * observation_minutes
    source = str(row.get("source") or "")
    if source in {"paper_confirmation_candidate", "paper_confirmation_current_candidate", "low_price_tick_probe"} and max_hold_minutes is None:
        configured_horizon = safe_float(settings.get("paper_confirmation_max_hold_minutes_before_exit"))
        max_hold_minutes = float(configured_horizon) if configured_horizon is not None and configured_horizon > 0 else 120.0
    max_stake = float(safe_float(settings.get("max_stake_usdc")) or 2.0)
    if source in {"paper_confirmation_candidate", "paper_confirmation_current_candidate"}:
        confirmation_max = safe_float(settings.get("paper_confirmation_max_stake_usdc"))
        if confirmation_max is not None and confirmation_max > 0:
            max_stake = min(max_stake, float(confirmation_max))
    if source == "low_price_tick_probe":
        probe_max = safe_float(settings.get("low_price_tick_max_stake_usdc"))
        if probe_max is not None and probe_max > 0:
            max_stake = min(max_stake, float(probe_max))
    min_edge = float(safe_float(settings.get("minimum_price_edge")) or 0.005)
    max_edge = float(safe_float(settings.get("maximum_price_edge")) or 0.08)
    if source == "low_price_tick_probe":
        min_edge = float(safe_float(settings.get("low_price_tick_min_edge")) or 0.001)
        max_edge = float(safe_float(settings.get("low_price_tick_max_edge")) or 0.02)

    target_edge = ask * take_profit_return
    realized_roi = safe_float(
        _preferred_numeric_metric(cohort, "realized_roi", "forward_shadow_roi", "forward_paper_roi")
    )
    if realized_roi is not None and realized_roi > 0:
        target_edge = max(target_edge, ask * min(realized_roi, take_profit_return * 2.0))
    edge = max(min_edge, min(max_edge, target_edge))
    probability_proxy = max(0.001, min(0.999, ask + edge))
    confidence = safe_float(cohort.get("win_rate") or cohort.get("validation_win_rate"))
    if confidence is None or confidence <= 0:
        confidence = float(safe_float(settings.get("default_confidence")) or 0.7)

    market_slug = str(row.get("market_slug") or entry.get("market_slug") or "")
    outcome = str(row.get("outcome") or entry.get("outcome") or "")
    signal_cohort = _cohort_name(row)
    data_timestamp = str(row.get("latest_time_utc") or now_utc())
    liquidity = entry.get("liquidity", "")
    if not str(liquidity or "").strip() and source in {"microstructure_current_candidate", "paper_confirmation_current_candidate", "low_price_tick_probe"}:
        liquidity = settings.get("microstructure_liquidity_proxy", "")
    priority_score = max_stake * edge / max(ask, 0.05)
    return {
        "market_id": market_slug or token,
        "market_slug": market_slug,
        "question": entry.get("question", ""),
        "category": _market_family(row) if _raw_family(row) else _market_family(entry),
        "event_id": "",
        "correlation_key": market_slug or token,
        "signal_cohort": signal_cohort,
        "outcome": outcome,
        "token_id": token,
        "side": "BUY_YES",
        "strategy_name": "price_action_round_trip",
        "market_price": "" if bid is None else bid,
        "executable_price": ask,
        "model_probability": probability_proxy,
        "calibrated_probability": probability_proxy,
        "gross_edge_before_slippage": edge,
        "edge": edge,
        "expected_value_per_share": edge,
        "liquidity": liquidity,
        "spread": spread,
        "best_bid": "" if bid is None else bid,
        "best_ask": ask,
        "top_bid_size": row.get("top_bid_size") or entry.get("top_bid_size", ""),
        "top_ask_size": row.get("top_ask_size") or entry.get("top_ask_size", ""),
        "bid_depth_1pct": row.get("bid_depth_1pct") or entry.get("bid_depth_1pct", ""),
        "ask_depth_1pct": row.get("ask_depth_1pct") or entry.get("ask_depth_1pct", ""),
        "bid_depth_5pct": row.get("bid_depth_5pct") or entry.get("bid_depth_5pct", ""),
        "ask_depth_5pct": row.get("ask_depth_5pct") or entry.get("ask_depth_5pct", ""),
        "websocket_quote_age_seconds": row.get("websocket_quote_age_seconds") or entry.get("websocket_quote_age_seconds", ""),
        "relative_spread": "" if relative_spread is None else relative_spread,
        "time_to_close_hours": entry.get("time_to_close_hours", ""),
        "resolution_risk": 0.0,
        "slippage": 0.0,
        "confidence": confidence,
        "alpha_probability": probability_proxy,
        "edge_lower_bound": edge,
        "model_version": "price_action_round_trip_v1",
        "feature_set_version": "low_price_tick_v1" if source == "low_price_tick_probe" else "websocket_bid_ask_v1",
        "data_snapshot_timestamp": data_timestamp,
        "price_action_signal": True,
        "price_action_evidence_status": (
            "trusted_shadow_requires_broker_paper_confirmation"
            if source in {"paper_confirmation_candidate", "paper_confirmation_current_candidate"}
            else "low_price_tick_requires_broker_paper_confirmation"
            if source == "low_price_tick_probe"
            else "cohort_bid_ask_round_trip_approved"
        ),
        "price_action_cohort_realized_roi": _preferred_numeric_metric(
            cohort,
            "realized_roi",
            "forward_shadow_roi",
            "forward_paper_roi",
        ),
        "price_action_cohort_win_rate": cohort.get("win_rate", cohort.get("validation_win_rate", "")),
        "price_action_cohort_closed_trades": cohort.get("closed_trades", cohort.get("validation_trades", "")),
        "price_action_cohort_run_rate_usdc": cohort.get(
            "realized_monthly_run_rate_usdc",
            cohort.get("monthly_run_rate_usdc", ""),
        ),
        "price_action_entry_source": source,
        "price_action_latest_bid": bid,
        "price_action_latest_ask": ask,
        "historical_analogue_key": row.get("historical_analogue_key", ""),
        "historical_analogue_validation_rows": row.get("historical_analogue_validation_rows", ""),
        "historical_analogue_positive_rows": row.get("historical_analogue_positive_rows", ""),
        "historical_analogue_validation_roi": row.get("historical_analogue_validation_roi", ""),
        "historical_analogue_win_rate": row.get("historical_analogue_win_rate", ""),
        "historical_analogue_gate": row.get("historical_analogue_gate", ""),
        "exit_policy_id": row.get("exit_policy_id", ""),
        "max_forward_observations": "" if max_forward_observations is None else max_forward_observations,
        "take_profit_return": take_profit_return,
        "stop_loss_return": stop_loss_return,
        "take_profit_min_usdc": min_profit,
        "minimum_hold_minutes_before_exit": min_hold,
        "max_hold_minutes_before_exit": "" if max_hold_minutes is None else max_hold_minutes,
        "max_stake_usdc": max_stake,
        "priority_score": priority_score,
    }


def _mutually_exclusive_key(signal: dict[str, Any]) -> str:
    """Return the market-level key where only one outcome should be probed.

    A binary Polymarket question can expose multiple outcome tokens. Opening
    both sides is not a directional prediction; it usually locks in spread loss.
    Keep this exact-market scoped so related but non-exclusive threshold markets
    (for example BTC above 58k and BTC above 60k) can still be evaluated
    independently.
    """
    return str(signal.get("correlation_key") or signal.get("market_slug") or signal.get("market_id") or "").strip().lower()


def _dedupe_mutually_exclusive_signals(signals: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    seen: set[str] = set()
    for signal in signals:
        key = _mutually_exclusive_key(signal)
        if key and key in seen:
            rejections.append(_reject(signal, "mutually exclusive paper signal already selected for market"))
            continue
        if key:
            seen.add(key)
        selected.append(signal)
    return selected, rejections


def _summary_decision(
    signals: list[dict[str, Any]],
    paper_confirmation: list[dict[str, Any]],
    low_price_tick: list[dict[str, Any]],
    rejections: list[dict[str, Any]],
    paper_confirmation_current_analogue: dict[str, Any] | None = None,
) -> str:
    if signals:
        return "signals_ready_for_paper_broker"
    if low_price_tick:
        return "low_price_tick_probe_waiting_for_fresh_executable_candidate"
    if paper_confirmation:
        analogue = paper_confirmation_current_analogue or {}
        if (
            int(safe_float(analogue.get("fresh_matches")) or 0) > 0
            and int(safe_float(analogue.get("selected")) or 0) <= 0
            and int(safe_float(analogue.get("blocked")) or 0) > 0
        ):
            blocked_by_state = analogue.get("blocked_by_state") if isinstance(analogue.get("blocked_by_state"), dict) else {}
            if blocked_by_state.get("entry_price_outside_risk_band"):
                return "trusted_shadow_edge_waiting_for_fresh_executable_candidate"
            return "trusted_shadow_edge_blocked_by_negative_historical_analogue"
        rejection_reasons = {str(row.get("rejection_reason") or "") for row in rejections}
        if any(
            reason in rejection_reasons
            for reason in {
                "candidate is not currently open for a fresh paper entry",
                "missing executable websocket bid/ask",
                "spread above price-action paper limit",
                "relative spread above price-action paper limit",
            }
        ) or any(reason.startswith("entry price ") for reason in rejection_reasons):
            return "trusted_shadow_edge_waiting_for_fresh_executable_candidate"
        return "trusted_shadow_edge_has_no_matching_current_candidate"
    return "no_price_action_paper_signals_until_positive_cohort_evidence"


def build_price_action_paper_signals(cfg: EngineConfig) -> dict[str, Any]:
    """Compile governed price-action evidence into paper broker signals.

    This is intentionally paper-only and settlement-independent: it promotes
    only cohorts whose executable bid/ask repricing evidence already passed the
    price-action review gate. That evidence can come from synthetic websocket
    variation, scout marks, or paper-confirmation exits; negative or incomplete
    cohorts remain rejections.
    """
    settings = _settings(cfg)
    out_dir = cfg.output_root / OUTPUT_DIRNAME
    if not _enabled(settings):
        summary = {
            "status": "disabled",
            "generated_at_utc": now_utc(),
            "signals": 0,
            "rejections": 0,
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        }
        write_csv(out_dir / SIGNALS_FILE, [], fieldnames=SIGNAL_FIELDS)
        write_csv(out_dir / REJECTIONS_FILE, [], fieldnames=REJECTION_FIELDS)
        write_json(out_dir / SUMMARY_JSON, summary)
        return summary

    cohort_rows = read_csv_rows(out_dir / SCOUT_COHORT_FILE)
    round_trip_rows = read_csv_rows(out_dir / SCOUT_ROUND_TRIP_FILE)
    entries = read_csv_rows(out_dir / SCOUT_ENTRY_FILE)
    microstructure_current = read_csv_rows(out_dir / MICROSTRUCTURE_CURRENT_FILE)
    approved = _approved_cohorts(cohort_rows)
    approved_microstructure = _approved_feedback_microstructure_cohorts(cfg)
    paper_confirmation = _paper_confirmation_candidates(cfg, settings)
    low_price_tick_evidence = _low_price_tick_probe_state(cfg, settings)
    paper_confirmation_current, paper_confirmation_current_analogue = _paper_confirmation_current_candidates(
        cfg,
        settings,
        paper_confirmation,
    )
    low_price_tick = _low_price_tick_probe_candidates(cfg, settings)
    by_token = _entry_index(entries)

    signals: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    max_signals = int(safe_float(settings.get("max_signals_per_run")) or 8)
    max_spread = float(safe_float(settings.get("max_spread")) or 0.04)
    max_relative_spread = float(safe_float(settings.get("max_relative_spread")) or 0.15)
    require_confirmation_history = boolish(settings.get("paper_confirmation_require_positive_historical_analogue", True))
    historical_analogue_stats = _historical_analogue_stats(cfg, settings)
    current_historical_analogue_scan = _current_historical_analogue_scan(
        cfg,
        settings,
        analogue_stats=historical_analogue_stats,
    )
    historical_breadth_scan = _historical_breadth_scan(cfg, settings)

    for row in round_trip_rows:
        cohort_name = _cohort_name(row)
        token = _token_id(row)
        cohort_payload: dict[str, Any] | None = approved.get(cohort_name)
        if cohort_payload is None:
            confirmation = _confirmation_candidate_for_row(row, paper_confirmation)
            if confirmation is not None:
                row = {
                    **row,
                    "source": "paper_confirmation_candidate",
                    "signal_cohort": confirmation.get("cohort", cohort_name),
                }
                cohort_name = _cohort_name(row)
                cohort_payload = confirmation
        if cohort_payload is None:
            rejections.append(_reject(row, "price-action cohort has not passed positive bid/ask evidence gate"))
            continue
        if str(row.get("round_trip_status") or "") != "open_marked":
            rejections.append(_reject(row, "candidate is not currently open for a fresh paper entry"))
            continue
        ask = safe_float(row.get("latest_ask"))
        bid = safe_float(row.get("latest_bid"))
        spread = safe_float(row.get("latest_spread"))
        if spread is None and ask is not None and bid is not None:
            spread = max(0.0, ask - bid)
        relative_spread = _relative_spread(spread, ask)
        if ask is None or bid is None or not 0 < ask < 1 or not 0 < bid < 1:
            rejections.append(_reject(row, "missing executable websocket bid/ask"))
            continue
        entry_band_rejection = _entry_price_band_rejection(cfg, ask)
        if entry_band_rejection:
            rejections.append(_reject(row, entry_band_rejection))
            continue
        if spread is None or spread > max_spread:
            rejections.append(_reject(row, "spread above price-action paper limit"))
            continue
        if relative_spread is None or relative_spread > max_relative_spread:
            rejections.append(_reject(row, "relative spread above price-action paper limit"))
            continue
        if str(row.get("source") or "") == "paper_confirmation_candidate":
            analogue = _historical_analogue_for_row(row, historical_analogue_stats, settings)
            row = _attach_historical_analogue(row, analogue)
            if require_confirmation_history and analogue.get("state") != "positive_historical_analogue":
                rejections.append(
                    _reject(
                        row,
                        f"paper-confirmation candidate blocked by historical analogue: {analogue.get('state')}",
                    )
                )
                continue
        signal = _build_signal(row, cohort=cohort_payload, entry=by_token.get(token, {}), settings=settings)
        if signal is None:
            rejections.append(_reject(row, "could not build executable price-action signal"))
            continue
        signals.append(signal)

    for raw_row in microstructure_current:
        row = {
            **raw_row,
            "source": raw_row.get("source") or "microstructure_current_candidate",
            "round_trip_status": raw_row.get("round_trip_status") or "open_marked",
        }
        cohort_name = _cohort_name(row)
        if cohort_name not in approved_microstructure:
            rejections.append(_reject(row, "microstructure cohort has not passed governed feedback promotion gate"))
            continue
        ask = safe_float(row.get("latest_ask"))
        bid = safe_float(row.get("latest_bid"))
        spread = safe_float(row.get("latest_spread"))
        if spread is None and ask is not None and bid is not None:
            spread = max(0.0, ask - bid)
        relative_spread = _relative_spread(spread, ask)
        if ask is None or bid is None or not 0 < ask < 1 or not 0 < bid < 1:
            rejections.append(_reject(row, "missing executable websocket bid/ask"))
            continue
        entry_band_rejection = _entry_price_band_rejection(cfg, ask)
        if entry_band_rejection:
            rejections.append(_reject(row, entry_band_rejection))
            continue
        if spread is None or spread > max_spread:
            rejections.append(_reject(row, "spread above price-action paper limit"))
            continue
        if relative_spread is None or relative_spread > max_relative_spread:
            rejections.append(_reject(row, "relative spread above price-action paper limit"))
            continue
        signal = _build_signal(row, cohort=approved_microstructure[cohort_name], entry=row, settings=settings)
        if signal is None:
            rejections.append(_reject(row, "could not build executable microstructure price-action signal"))
            continue
        signals.append(signal)

    for raw_row in paper_confirmation_current:
        row = {
            **raw_row,
            "source": raw_row.get("source") or "paper_confirmation_current_candidate",
            "round_trip_status": raw_row.get("round_trip_status") or "open_marked",
        }
        confirmation = _confirmation_candidate_for_row(row, paper_confirmation)
        if confirmation is None:
            rejections.append(_reject(row, "paper-confirmation current candidate lost trusted shadow backing"))
            continue
        ask = safe_float(row.get("latest_ask"))
        bid = safe_float(row.get("latest_bid"))
        spread = safe_float(row.get("latest_spread"))
        if spread is None and ask is not None and bid is not None:
            spread = max(0.0, ask - bid)
            row["latest_spread"] = spread
        relative_spread = _relative_spread(spread, ask)
        if ask is None or bid is None or not 0 < ask < 1 or not 0 < bid < 1:
            rejections.append(_reject(row, "missing executable websocket bid/ask"))
            continue
        entry_band_rejection = _entry_price_band_rejection(cfg, ask)
        if entry_band_rejection:
            rejections.append(_reject(row, entry_band_rejection))
            continue
        if spread is None or spread > max_spread:
            rejections.append(_reject(row, "spread above price-action paper limit"))
            continue
        if relative_spread is None or relative_spread > max_relative_spread:
            rejections.append(_reject(row, "relative spread above price-action paper limit"))
            continue
        signal = _build_signal(row, cohort=confirmation, entry=row, settings=settings)
        if signal is None:
            rejections.append(_reject(row, "could not build executable paper-confirmation current signal"))
            continue
        signals.append(signal)

    for raw_row in low_price_tick:
        row = {
            **raw_row,
            "source": raw_row.get("source") or "low_price_tick_probe",
            "round_trip_status": raw_row.get("round_trip_status") or "open_marked",
        }
        ask = safe_float(row.get("latest_ask"))
        bid = safe_float(row.get("latest_bid"))
        spread = safe_float(row.get("latest_spread"))
        if ask is None or bid is None or not 0 < ask < 1 or not 0 < bid < ask:
            rejections.append(_reject(row, "low-price tick probe missing executable bid/ask"))
            continue
        if spread is None:
            spread = max(0.0, ask - bid)
            row["latest_spread"] = spread
        entry_band_rejection = _entry_price_band_rejection(cfg, ask)
        if entry_band_rejection:
            rejections.append(_reject(row, entry_band_rejection))
            continue
        cohort_payload = {
            "forward_shadow_roi": row.get("one_cent_return", ""),
            "forward_shadow_pnl_usdc": "",
            "win_rate": "",
            "validation_win_rate": "",
            "closed_trades": "",
            "monthly_run_rate_usdc": "",
        }
        signal = _build_signal(row, cohort=cohort_payload, entry=row, settings=settings)
        if signal is None:
            rejections.append(_reject(row, "could not build executable low-price tick probe"))
            continue
        signals.append(signal)

    signals.sort(key=lambda item: safe_float(item.get("priority_score")) or 0.0, reverse=True)
    signals, mutually_exclusive_rejections = _dedupe_mutually_exclusive_signals(signals)
    rejections.extend(mutually_exclusive_rejections)
    signals = signals[:max_signals]
    write_csv(out_dir / SIGNALS_FILE, signals, fieldnames=SIGNAL_FIELDS)
    write_csv(out_dir / REJECTIONS_FILE, rejections, fieldnames=REJECTION_FIELDS)

    summary = {
        "status": "computed",
        "generated_at_utc": now_utc(),
        "signals": len(signals),
        "rejections": len(rejections),
        "approved_price_action_cohorts": len(approved),
        "approved_microstructure_cohorts": len(approved_microstructure),
        "paper_confirmation_candidates": len(paper_confirmation),
        "paper_confirmation_current_candidates": len(paper_confirmation_current),
        "paper_confirmation_current_historical_analogue": paper_confirmation_current_analogue,
        "current_historical_analogue_scan": current_historical_analogue_scan,
        "historical_breadth_scan": historical_breadth_scan,
        "low_price_tick_probe_evidence": low_price_tick_evidence,
        "low_price_tick_probe_candidates": len(low_price_tick),
        "low_price_tick_probe_signals": sum(
            1
            for signal in signals
            if signal.get("price_action_evidence_status") == "low_price_tick_requires_broker_paper_confirmation"
        ),
        "entry_price_band_rejections": sum(
            1
            for rejection in rejections
            if str(rejection.get("rejection_reason") or "").startswith("entry price ")
        )
        + int(
            safe_float(
                (
                    paper_confirmation_current_analogue.get("blocked_by_state", {})
                    if isinstance(paper_confirmation_current_analogue.get("blocked_by_state"), dict)
                    else {}
                ).get("entry_price_outside_risk_band")
            )
            or 0
        ),
        "paper_confirmation_signals": sum(
            1
            for signal in signals
            if signal.get("price_action_evidence_status") == "trusted_shadow_requires_broker_paper_confirmation"
        ),
        "mutually_exclusive_signal_rejections": len(mutually_exclusive_rejections),
        "source_round_trip_rows": len(round_trip_rows),
        "source_microstructure_current_rows": len(microstructure_current),
        "signal_file": str(out_dir / SIGNALS_FILE),
        "rejection_file": str(out_dir / REJECTIONS_FILE),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
        "decision": _summary_decision(
            signals,
            paper_confirmation,
            low_price_tick,
            rejections,
            paper_confirmation_current_analogue,
        ),
        "warnings": {
            "paper_only": True,
            "live_trading_invoked": False,
            "does_not_wait_for_settlement": True,
            "requires_positive_bid_ask_cohort_evidence": True,
            "low_price_tick_probes_are_evidence_only": True,
        },
        "top_signals": signals[:10],
        "top_rejections": rejections[:10],
    }
    write_json(out_dir / SUMMARY_JSON, summary)
    return summary


def run(config_path: str = "polymarket_predictive_config.example.yaml") -> dict[str, Any]:
    cfg = load_config(config_path)
    return build_price_action_paper_signals(cfg)
