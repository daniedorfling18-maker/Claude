from __future__ import annotations

from collections import Counter
import re
from typing import Any

from .config import EngineConfig, load_config
from .execution_costs import estimate_execution_cost
from .models.calibration_v2 import joined_feature_label_rows
from .readiness import paper_trade_readiness
from .risk import risk_decision
from .utils import boolish, read_csv_rows, read_json, safe_float, write_csv
from .worldcup_validation import classify_market_family, normalised_correlation_key, signal_cohort


_UNKNOWN_CATEGORIES = {"", "unknown", "uncategorised", "uncategorized"}


def _setting_bool(settings: dict[str, Any], key: str, default: bool = False) -> bool:
    value = settings.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return boolish(value)


def _estimated_slippage(default_slippage: float, prediction: dict[str, Any], *, stake_usdc: float = 1.0) -> float:
    estimate = estimate_execution_cost(
        prediction,
        stake_usdc=stake_usdc,
        flat_slippage=max(0.0, default_slippage),
    )
    value = safe_float(estimate.get("expected_slippage"))
    return max(0.0, default_slippage) if value is None else value


def _same_category_label_counts(cfg: EngineConfig) -> dict[str, int]:
    train_root = cfg.output_root / "polymarket_training"
    try:
        rows = joined_feature_label_rows(str(train_root / "features_v2.csv"), str(train_root / "labels.csv"))
    except Exception:
        return {}
    counts: Counter[str] = Counter()
    for row in rows:
        for category in _same_category_count_keys(row):
            counts[category] += 1
    return dict(counts)


def _same_category_count_keys(row: dict[str, Any]) -> set[str]:
    """Return category aliases for the resolved-label safety gate.

    Older point-in-time feature rows were often stored with ``category=unknown``
    even when their slug/question clearly identifies the family. The trading
    gate should keep requiring same-category labels, but it should use the
    current taxonomy rather than silently undercounting valid historical labels.
    """
    keys: set[str] = set()
    raw = str(row.get("category") or "").strip().lower().replace(" ", "_")
    classified = str(classify_market_family(row) or "").strip().lower()

    def add(value: str) -> None:
        key = str(value or "").strip().lower().replace(" ", "_")
        if key and key not in _UNKNOWN_CATEGORIES:
            keys.add(key)

    add(raw)
    add(classified)

    text = " ".join(
        str(row.get(field) or "").lower()
        for field in ("category", "market_slug", "question", "event_slug", "event_title", "event_id", "outcome")
    )
    family = classified
    if raw.startswith("crypto") or family.startswith("crypto_"):
        keys.add("crypto")
    if raw.startswith("tennis") or family.startswith("tennis_"):
        keys.add("tennis")
    if raw.startswith("worldcup") or family.startswith("worldcup") or "world cup" in text or "fifa_world_cup" in text:
        keys.add("worldcup")
    if raw.startswith("baseball_mlb") or family.startswith("baseball_mlb"):
        keys.add("baseball_mlb_match")
    if raw.startswith("basketball_nba") or family.startswith("basketball_nba"):
        keys.add("basketball_nba_match")
    if raw.startswith("mma") or family == "mma_match":
        keys.add("mma_match")
    if not keys:
        keys.add("unknown")
    return keys


_CRYPTO_UPDOWN_COHORT_RE = re.compile(
    r"crypto_(?P<asset>btc|eth|sol|xrp)_updown_(?P<interval>daily|5m|15m)\|outcome=(?P<outcome>up|down)"
)


def _crypto_updown_cohort_key(cohort: str) -> str:
    match = _CRYPTO_UPDOWN_COHORT_RE.search(str(cohort or ""))
    if not match:
        return ""
    return f"crypto_{match.group('asset')}_updown_{match.group('interval')}|outcome={match.group('outcome')}"

def _cohort_promotion_index(cfg: EngineConfig) -> dict[str, dict[str, Any]]:
    settings = cfg.raw.get("cohort_promotion", {}) or {}
    summary_file = str(settings.get("summary_file", "signal_cohort_pnl.json"))
    summary = read_json(cfg.governance_root / summary_file, default={}) or {}
    if not isinstance(summary, dict):
        return {}
    cohorts = summary.get("cohorts", [])
    if not isinstance(cohorts, list):
        return {}
    return {
        str(row.get("signal_cohort") or row.get("cohort") or ""): row
        for row in cohorts
        if isinstance(row, dict) and str(row.get("signal_cohort") or row.get("cohort") or "")
    }


def _cohort_is_promoted(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    value = row.get("promoted")
    if isinstance(value, bool):
        return value
    return boolish(value)


def _cohort_has_direct_evidence(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    return bool(
        (safe_float(row.get("buy_fills")) or 0) > 0
        or (safe_float(row.get("settled_fills")) or 0) > 0
        or abs(safe_float(row.get("total_pnl_usdc")) or 0.0) > 1e-9
        or _cohort_ready_score(row) > 0
    )

def _cohort_ready_score(row: dict[str, Any] | None) -> int:
    return int(safe_float((row or {}).get("promotion_ready_score")) or 0)


def _cohort_evidence_proxy_index(cohorts: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index promoted/probationary crypto UpDown evidence by asset+interval+outcome.

    The same behavioural edge can appear under an exploratory historical cohort
    before the live crypto model is scored, then under the live-model cohort once
    the price model is available.  This proxy only bridges identical
    asset/interval/outcome cohorts and only if the source row is already promoted
    or probationary.
    """
    proxy: dict[str, dict[str, Any]] = {}
    for cohort, row in cohorts.items():
        key = _crypto_updown_cohort_key(cohort)
        if not key or not (_cohort_is_promoted(row) or _cohort_is_probationary(row)):
            continue
        current = proxy.get(key)
        if current is None or _cohort_ready_score(row) > _cohort_ready_score(current):
            proxy[key] = row
    return proxy


def _near_miss_evidence_proxy_index(
    cohorts: dict[str, dict[str, Any]],
    *,
    prefix: str = "near_miss_learning",
) -> dict[str, dict[str, Any]]:
    """Index positive near-miss shadow evidence back to its base cohort.

    Near-miss evidence is intentionally stored under a namespaced cohort such
    as ``near_miss_learning|crypto``.  It may only proxy back to ``crypto`` once
    the cohort validation layer has marked that near-miss namespace as
    probationary or promoted from forward shadow P&L.
    """
    prefix_text = str(prefix or "near_miss_learning").strip()
    marker = f"{prefix_text}|"
    proxy: dict[str, dict[str, Any]] = {}
    for cohort, row in cohorts.items():
        cohort_text = str(cohort or "")
        if not cohort_text.startswith(marker):
            continue
        if not (_cohort_is_promoted(row) or _cohort_is_probationary(row)):
            continue
        base_cohort = cohort_text[len(marker) :]
        if not base_cohort:
            continue
        current = proxy.get(base_cohort)
        if current is None or _cohort_ready_score(row) > _cohort_ready_score(current):
            proxy[base_cohort] = row
    return proxy


def _cohort_is_probationary(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    value = row.get("probationary")
    if isinstance(value, bool):
        return value
    return boolish(value)


def generate_signals(
    cfg: EngineConfig,
    readiness: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Generate forward paper signals behind the mechanical paper-readiness gate."""
    readiness = readiness or paper_trade_readiness(cfg)
    predictions = read_csv_rows(cfg.output_root / "polymarket_predictions" / "predictions.csv")
    approved: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    allowed = bool(readiness.get("approved_for_paper_trading"))
    blockers = readiness.get("blockers", []) or readiness.get("paper_trading_blockers", []) or []
    readiness_reason = "; ".join(str(item) for item in blockers) or "paper readiness is not approved"
    default_slippage = float(cfg.raw.get("costs", {}).get("slippage", 0.0))
    alpha_settings = cfg.raw.get("mispricing_alpha", {})
    alpha_enabled = _setting_bool(alpha_settings, "enabled", True)
    edge_field = str(alpha_settings.get("edge_field_for_trading", "edge_lower_bound"))
    require_alpha_candidate = _setting_bool(alpha_settings, "require_alpha_trade_candidate", True)
    require_same_category_labels = bool(
        alpha_enabled
        and _setting_bool(alpha_settings, "require_same_category_labels_for_trading", False)
    )
    minimum_same_category_labels = int(alpha_settings.get("minimum_same_category_label_rows_for_trading", 25))
    same_category_counts = _same_category_label_counts(cfg) if require_same_category_labels else {}
    cohort_settings = cfg.raw.get("cohort_promotion", {}) or {}
    require_positive_cohort = bool(
        alpha_enabled and _setting_bool(cohort_settings, "require_positive_forward_pnl", False)
    )
    allow_promoted_shadow_candidate_trading = bool(
        alpha_enabled and _setting_bool(cohort_settings, "allow_promoted_shadow_candidate_trading", False)
    )
    allow_probationary_paper_trading = bool(
        alpha_enabled and _setting_bool(cohort_settings, "allow_probationary_paper_trading", True)
    )
    allow_cohort_evidence_label_gate = bool(
        alpha_enabled and _setting_bool(cohort_settings, "allow_cohort_evidence_to_satisfy_label_gate", True)
    )
    allow_crypto_updown_cohort_proxy = bool(
        alpha_enabled and _setting_bool(cohort_settings, "allow_crypto_updown_live_model_cohort_proxy", True)
    )
    allow_near_miss_learning_cohort_proxy = bool(
        alpha_enabled and _setting_bool(cohort_settings, "allow_near_miss_learning_cohort_proxy", False)
    )
    near_miss_cohort_prefix = str(cohort_settings.get("near_miss_cohort_prefix", "near_miss_learning"))
    probationary_default_max_stake = float(cohort_settings.get("probationary_max_stake_usdc", 2.0))
    probationary_min_edge = float(cohort_settings.get("probationary_minimum_edge_lower_bound", 0.005))
    promoted_cohorts = _cohort_promotion_index(cfg) if require_positive_cohort else {}
    cohort_proxy_index = (
        _cohort_evidence_proxy_index(promoted_cohorts)
        if require_positive_cohort and allow_crypto_updown_cohort_proxy
        else {}
    )
    near_miss_proxy_index = (
        _near_miss_evidence_proxy_index(promoted_cohorts, prefix=near_miss_cohort_prefix)
        if require_positive_cohort and allow_near_miss_learning_cohort_proxy
        else {}
    )

    for prediction in predictions:
        alpha_edge = safe_float(prediction.get(edge_field))
        raw_edge = safe_float(prediction.get("edge"))
        gross_edge = alpha_edge if alpha_edge is not None else raw_edge
        estimated_slippage = _estimated_slippage(
            default_slippage,
            prediction,
            stake_usdc=safe_float(prediction.get("max_stake_usdc")) or probationary_default_max_stake,
        )
        edge = None if gross_edge is None else gross_edge - estimated_slippage
        alpha_probability = safe_float(prediction.get("alpha_probability"))
        confidence = (
            max(0.0, min(1.0, 2.0 * abs(alpha_probability - 0.5)))
            if alpha_probability is not None
            else prediction.get("confidence", "")
        )
        signal = {
            "market_id": prediction.get("market_id", ""),
            "market_slug": prediction.get("market_slug", ""),
            "question": prediction.get("question", ""),
            "category": prediction.get("category", ""),
            "event_id": prediction.get("event_id", ""),
            "correlation_key": normalised_correlation_key(prediction),
            "signal_cohort": prediction.get("signal_cohort") or signal_cohort(prediction),
            "outcome": prediction.get("outcome", "YES"),
            "token_id": prediction.get("token_id", ""),
            "side": "BUY_YES",
            "strategy_name": "mispricing_alpha_overlay" if alpha_edge is not None else "predictive_directional",
            "market_price": prediction.get("market_midpoint", ""),
            "executable_price": prediction.get("executable_price", ""),
            "model_probability": prediction.get("model_probability", prediction.get("calibrated_probability", "")),
            "calibrated_probability": prediction.get("calibrated_probability", ""),
            "uncertainty_low": prediction.get("uncertainty_low", ""),
            "uncertainty_high": prediction.get("uncertainty_high", ""),
            "gross_edge_before_slippage": 0.0 if gross_edge is None else gross_edge,
            "edge": 0.0 if edge is None else edge,
            "expected_value_per_share": 0.0 if edge is None else edge,
            "liquidity": prediction.get("liquidity", ""),
            "spread": prediction.get("spread", ""),
            "best_bid": prediction.get("best_bid", ""),
            "best_ask": prediction.get("best_ask", ""),
            "top_bid_size": prediction.get("top_bid_size", ""),
            "top_ask_size": prediction.get("top_ask_size", ""),
            "bid_depth_1pct": prediction.get("bid_depth_1pct", ""),
            "ask_depth_1pct": prediction.get("ask_depth_1pct", ""),
            "bid_depth_5pct": prediction.get("bid_depth_5pct", ""),
            "ask_depth_5pct": prediction.get("ask_depth_5pct", ""),
            "book_imbalance": prediction.get("book_imbalance", ""),
            "websocket_quote_age_seconds": prediction.get("websocket_quote_age_seconds", ""),
            "time_to_close_hours": prediction.get("time_to_close_hours", ""),
            "resolution_risk": prediction.get("resolution_risk", 0.0),
            "slippage": estimated_slippage,
            "confidence": confidence,
            "alpha_probability": prediction.get("alpha_probability", ""),
            "alpha_score": prediction.get("alpha_score", ""),
            "alpha_raw_edge": prediction.get("alpha_raw_edge", ""),
            "edge_lower_bound": prediction.get("edge_lower_bound", ""),
            "alpha_total_penalty": prediction.get("alpha_total_penalty", ""),
            "alpha_bias_cell": prediction.get("alpha_bias_cell", ""),
            "alpha_trade_candidate": prediction.get("alpha_trade_candidate", ""),
            "crypto_model_status": prediction.get("crypto_model_status", ""),
            "crypto_model_gate_pass": prediction.get("crypto_model_gate_pass", ""),
            "crypto_model_probability": prediction.get("crypto_model_probability", ""),
            "crypto_model_edge_after_cost": prediction.get("crypto_model_edge_after_cost", ""),
            "shadow_trade_candidate": prediction.get("shadow_trade_candidate", ""),
            "shadow_candidate_reason": prediction.get("shadow_candidate_reason", ""),
            "shadow_priority_score": prediction.get("shadow_priority_score", ""),
            "validation_layer_pass": prediction.get("validation_layer_pass", ""),
            "validation_layer_reason": prediction.get("validation_layer_reason", ""),
            "bookmaker_cross_check_pass": prediction.get("bookmaker_cross_check_pass", ""),
            "fundamental_probability": prediction.get("fundamental_probability", ""),
            "haircut_fundamental_probability": prediction.get("haircut_fundamental_probability", ""),
            "fundamental_edge_after_haircut": prediction.get("fundamental_edge_after_haircut", ""),
            "microstructure_filter_pass": prediction.get("microstructure_filter_pass", ""),
            "price_filter_pass": prediction.get("price_filter_pass", ""),
            "spread_filter_pass": prediction.get("spread_filter_pass", ""),
            "relative_spread": prediction.get("relative_spread", ""),
            "relative_spread_filter_pass": prediction.get("relative_spread_filter_pass", ""),
            "liquidity_filter_pass": prediction.get("liquidity_filter_pass", ""),
            "cross_market_probability_sum": prediction.get("cross_market_probability_sum", ""),
            "cross_market_overround": prediction.get("cross_market_overround", ""),
            "complete_set_discount": prediction.get("complete_set_discount", ""),
            "model_version": prediction.get("model_version", ""),
            "feature_set_version": prediction.get("feature_set_version", ""),
            "data_snapshot_timestamp": prediction.get("prediction_timestamp", ""),
        }
        if not allowed:
            rejected.append({**signal, "rejection_reason": readiness_reason})
            continue
        cohort = str(signal.get("signal_cohort") or "")
        cohort_row = promoted_cohorts.get(cohort) if require_positive_cohort else None
        cohort_evidence_source = cohort
        if require_positive_cohort and allow_crypto_updown_cohort_proxy:
            exact_is_ready = _cohort_is_promoted(cohort_row) or _cohort_is_probationary(cohort_row)
            exact_has_direct_evidence = _cohort_has_direct_evidence(cohort_row)
            if not exact_is_ready and not exact_has_direct_evidence:
                proxy_key = _crypto_updown_cohort_key(cohort)
                proxy_row = cohort_proxy_index.get(proxy_key)
                if proxy_row is not None:
                    cohort_row = proxy_row
                    cohort_evidence_source = str(proxy_row.get("signal_cohort") or proxy_row.get("cohort") or cohort)
        if require_positive_cohort and allow_near_miss_learning_cohort_proxy:
            exact_is_ready = _cohort_is_promoted(cohort_row) or _cohort_is_probationary(cohort_row)
            exact_has_direct_evidence = _cohort_has_direct_evidence(cohort_row)
            if not exact_is_ready and not exact_has_direct_evidence:
                near_miss_proxy_row = near_miss_proxy_index.get(cohort)
                if near_miss_proxy_row is not None:
                    cohort_row = near_miss_proxy_row
                    cohort_evidence_source = str(
                        near_miss_proxy_row.get("signal_cohort") or near_miss_proxy_row.get("cohort") or cohort
                    )
        cohort_promoted = _cohort_is_promoted(cohort_row)
        cohort_probationary = bool(allow_probationary_paper_trading and _cohort_is_probationary(cohort_row))
        signal["cohort_promotion_status"] = (
            "promoted"
            if cohort_promoted
            else "probationary"
            if cohort_probationary
            else "not_promoted"
        ) if require_positive_cohort else ""
        signal["cohort_evidence_source_cohort"] = cohort_evidence_source if require_positive_cohort else ""
        signal["cohort_forward_pnl_usdc"] = "" if not cohort_row else cohort_row.get("total_pnl_usdc", "")
        signal["cohort_filled_orders"] = "" if not cohort_row else cohort_row.get("buy_fills", "")
        signal["cohort_settled_fills"] = "" if not cohort_row else cohort_row.get("settled_fills", "")
        if cohort_probationary:
            max_stake = safe_float(cohort_row.get("probationary_max_stake_usdc")) or probationary_default_max_stake
            signal["probationary_paper_trade"] = True
            signal["max_stake_usdc"] = max_stake
        if str(cohort_evidence_source or "").startswith(f"{near_miss_cohort_prefix}|"):
            signal["near_miss_evidence_proxy"] = True
        promoted_shadow_override = bool(
            allow_promoted_shadow_candidate_trading
            and boolish(prediction.get("shadow_trade_candidate"))
            and (cohort_promoted or cohort_probationary)
        )
        probationary_positive_edge_override = bool(
            cohort_probationary
            and edge is not None
            and edge >= probationary_min_edge
            and boolish(prediction.get("microstructure_filter_pass"))
            and boolish(prediction.get("bookmaker_cross_check_pass"))
        )
        if (
            require_alpha_candidate
            and alpha_edge is not None
            and not boolish(prediction.get("alpha_trade_candidate"))
            and not promoted_shadow_override
            and not probationary_positive_edge_override
        ):
            validation_reason = str(prediction.get("validation_layer_reason") or "").strip()
            rejection_reason = "alpha lower-bound edge below configured minimum"
            if validation_reason:
                rejection_reason = f"{rejection_reason}; {validation_reason}"
            rejected.append({**signal, "rejection_reason": rejection_reason})
            continue
        if promoted_shadow_override:
            signal["promotion_override"] = (
                "probationary_shadow_candidate" if cohort_probationary and not cohort_promoted else "promoted_shadow_candidate"
            )
        if probationary_positive_edge_override:
            signal["promotion_override"] = "probationary_positive_edge_probe"
        category_key = str(signal.get("category") or "unknown").strip().lower()
        same_category_label_rows = int(same_category_counts.get(category_key, 0))
        signal["same_category_label_rows"] = same_category_label_rows
        cohort_evidence_satisfies_label_gate = bool(
            allow_cohort_evidence_label_gate
            and (cohort_promoted or cohort_probationary)
            and boolish(prediction.get("validation_layer_pass"))
            and boolish(prediction.get("microstructure_filter_pass"))
            and boolish(prediction.get("bookmaker_cross_check_pass"))
        )
        signal["cohort_evidence_label_gate_override"] = cohort_evidence_satisfies_label_gate
        if (
            require_same_category_labels
            and same_category_label_rows < minimum_same_category_labels
            and not cohort_evidence_satisfies_label_gate
        ):
            rejected.append(
                {
                    **signal,
                    "rejection_reason": (
                        "same-category validation gate failed: "
                        f"{same_category_label_rows} < {minimum_same_category_labels} resolved labels"
                    ),
                }
            )
            continue
        if require_positive_cohort:
            if not (cohort_promoted or cohort_probationary):
                minimum_fills = int(cohort_settings.get("minimum_filled_orders", 5))
                minimum_pnl = float(cohort_settings.get("minimum_pnl_usdc", 0.0))
                rejected.append(
                    {
                        **signal,
                        "rejection_reason": (
                            "cohort promotion gate failed: "
                            f"{cohort or 'unknown'} needs >= {minimum_fills} fills "
                            f"and P&L > {minimum_pnl:.2f}"
                        ),
                    }
                )
                continue
        decision = risk_decision(cfg, signal)
        signal["risk_decision"] = decision["reason"]
        signal["sizing_decision"] = decision.get("stake_usdc", decision.get("size", 0.0))
        signal["quantity_decision"] = decision.get("quantity", 0.0)
        signal["risk_decision_detail"] = decision
        if decision["approved"]:
            stake = safe_float(signal.get("sizing_decision")) or 0.0
            price = safe_float(signal.get("executable_price")) or 0.0
            fill_price = min(0.999999, price + estimated_slippage) if price > 0 else 0.0
            target_metric_min_price = float(alpha_settings.get("target_metric_min_price", 0.05))
            metric_price = max(fill_price, target_metric_min_price)
            expected_lower_bound_profit = stake * max(0.0, edge or 0.0) / metric_price if metric_price > 0 else 0.0
            signal["expected_lower_bound_profit_usdc"] = expected_lower_bound_profit
            signal["priority_score"] = expected_lower_bound_profit
            signal["approval_reason"] = "paper readiness and pre-trade risk controls approved"
            approved.append(signal)
        else:
            rejected.append({**signal, "rejection_reason": decision["reason"]})
    approved.sort(
        key=lambda item: (
            safe_float(item.get("priority_score")) or 0.0,
            safe_float(item.get("alpha_score")) or 0.0,
            safe_float(item.get("edge")) or 0.0,
        ),
        reverse=True,
    )
    out = cfg.output_root / "polymarket_predictions"
    write_csv(out / "trade_signals.csv", approved)
    write_csv(out / "rejected_signals.csv", rejected)
    return approved, rejected


def main(config_path: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return generate_signals(load_config(config_path))


