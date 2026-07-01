from __future__ import annotations

from typing import Any

from .config import EngineConfig, load_config
from .utils import boolish, now_utc, read_csv_rows, read_json, safe_float, write_csv, write_json

OUTPUT_DIRNAME = "polymarket_price_action"
SCOUT_COHORT_FILE = "price_action_scout_cohort_evidence.csv"
SCOUT_ROUND_TRIP_FILE = "price_action_scout_round_trip_evidence.csv"
SCOUT_ENTRY_FILE = "price_action_scout_entries.csv"
MICROSTRUCTURE_CURRENT_FILE = "microstructure_current_candidates.csv"
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
    "exit_policy_id",
    "signal_cohort",
    "round_trip_status",
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
        if (safe_float(row.get("forward_shadow_pnl_usdc")) or 0.0) <= 0:
            continue
        if (safe_float(row.get("forward_shadow_roi")) or 0.0) <= 0:
            continue
        candidates.append(row)
    candidates.sort(key=lambda item: safe_float(item.get("priority_score")) or 0.0, reverse=True)
    return candidates


def _query_family_prefixes(query: str) -> list[str]:
    query = str(query or "").strip().lower()
    if not query:
        return []
    if "world" in query or "cup" in query:
        return ["sports_other", "worldcup"]
    if "tennis" in query:
        return ["tennis"]
    if "bitcoin" in query or "btc" in query:
        return ["crypto_btc"]
    if "ethereum" in query or query == "eth":
        return ["crypto_eth"]
    if "solana" in query or query == "sol":
        return ["crypto_sol"]
    if "xrp" in query or "ripple" in query:
        return ["crypto_xrp"]
    return []


def _confirmation_candidate_for_row(row: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    row_cohort = _cohort_name(row).lower()
    family = str(row.get("family") or row.get("category") or "").strip().lower()
    market_slug = str(row.get("market_slug") or "").strip().lower()
    text = " ".join([row_cohort, family, market_slug])
    for candidate in candidates:
        cohort = str(candidate.get("cohort") or "").strip().lower()
        query = str(candidate.get("recommended_collection_query") or "").strip().lower()
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


def _reject(row: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "market_slug": row.get("market_slug", ""),
        "outcome": row.get("outcome", ""),
        "token_id": _token_id(row),
        "exit_policy_id": row.get("exit_policy_id", ""),
        "signal_cohort": _cohort_name(row),
        "round_trip_status": row.get("round_trip_status", ""),
        "rejection_reason": reason,
    }


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
    if str(row.get("source") or "") == "paper_confirmation_candidate" and max_hold_minutes is None:
        configured_horizon = safe_float(settings.get("paper_confirmation_max_hold_minutes_before_exit"))
        max_hold_minutes = float(configured_horizon) if configured_horizon is not None and configured_horizon > 0 else 120.0
    max_stake = float(safe_float(settings.get("max_stake_usdc")) or 2.0)
    if str(row.get("source") or "") == "paper_confirmation_candidate":
        confirmation_max = safe_float(settings.get("paper_confirmation_max_stake_usdc"))
        if confirmation_max is not None and confirmation_max > 0:
            max_stake = min(max_stake, float(confirmation_max))
    min_edge = float(safe_float(settings.get("minimum_price_edge")) or 0.005)
    max_edge = float(safe_float(settings.get("maximum_price_edge")) or 0.08)

    target_edge = ask * take_profit_return
    realized_roi = safe_float(cohort.get("realized_roi") or cohort.get("forward_shadow_roi") or cohort.get("forward_paper_roi"))
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
    if not str(liquidity or "").strip() and str(row.get("source") or "") == "microstructure_current_candidate":
        liquidity = settings.get("microstructure_liquidity_proxy", "")
    priority_score = max_stake * edge / max(ask, 0.05)
    return {
        "market_id": market_slug or token,
        "market_slug": market_slug,
        "question": entry.get("question", ""),
        "category": str(row.get("family") or entry.get("family") or "price_action"),
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
        "relative_spread": "" if relative_spread is None else relative_spread,
        "time_to_close_hours": entry.get("time_to_close_hours", ""),
        "resolution_risk": 0.0,
        "slippage": 0.0,
        "confidence": confidence,
        "alpha_probability": probability_proxy,
        "edge_lower_bound": edge,
        "model_version": "price_action_round_trip_v1",
        "feature_set_version": "websocket_bid_ask_v1",
        "data_snapshot_timestamp": data_timestamp,
        "price_action_signal": True,
        "price_action_evidence_status": (
            "trusted_shadow_requires_broker_paper_confirmation"
            if str(row.get("source") or "") == "paper_confirmation_candidate"
            else "cohort_bid_ask_round_trip_approved"
        ),
        "price_action_cohort_realized_roi": cohort.get(
            "realized_roi",
            cohort.get("forward_shadow_roi", cohort.get("forward_paper_roi", "")),
        ),
        "price_action_cohort_win_rate": cohort.get("win_rate", cohort.get("validation_win_rate", "")),
        "price_action_cohort_closed_trades": cohort.get("closed_trades", cohort.get("validation_trades", "")),
        "price_action_cohort_run_rate_usdc": cohort.get(
            "realized_monthly_run_rate_usdc",
            cohort.get("monthly_run_rate_usdc", ""),
        ),
        "price_action_entry_source": row.get("source", ""),
        "price_action_latest_bid": bid,
        "price_action_latest_ask": ask,
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


def _summary_decision(signals: list[dict[str, Any]], paper_confirmation: list[dict[str, Any]], rejections: list[dict[str, Any]]) -> str:
    if signals:
        return "signals_ready_for_paper_broker"
    if paper_confirmation:
        rejection_reasons = {str(row.get("rejection_reason") or "") for row in rejections}
        if any(
            reason in rejection_reasons
            for reason in {
                "candidate is not currently open for a fresh paper entry",
                "missing executable websocket bid/ask",
                "spread above price-action paper limit",
                "relative spread above price-action paper limit",
            }
        ):
            return "trusted_shadow_edge_waiting_for_fresh_executable_candidate"
        return "trusted_shadow_edge_has_no_matching_current_candidate"
    return "no_price_action_paper_signals_until_positive_cohort_evidence"


def build_price_action_paper_signals(cfg: EngineConfig) -> dict[str, Any]:
    """Compile governed price-action evidence into paper broker signals.

    This is intentionally paper-only and settlement-independent: it only promotes
    cohorts whose bid/ask round-trip evidence already passed the price-action
    review gate. Negative or incomplete cohorts remain rejections.
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
    by_token = _entry_index(entries)

    signals: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    max_signals = int(safe_float(settings.get("max_signals_per_run")) or 8)
    max_spread = float(safe_float(settings.get("max_spread")) or 0.04)
    max_relative_spread = float(safe_float(settings.get("max_relative_spread")) or 0.15)

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
        if spread is None or spread > max_spread:
            rejections.append(_reject(row, "spread above price-action paper limit"))
            continue
        if relative_spread is None or relative_spread > max_relative_spread:
            rejections.append(_reject(row, "relative spread above price-action paper limit"))
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

    signals.sort(key=lambda item: safe_float(item.get("priority_score")) or 0.0, reverse=True)
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
        "paper_confirmation_signals": sum(
            1
            for signal in signals
            if signal.get("price_action_evidence_status") == "trusted_shadow_requires_broker_paper_confirmation"
        ),
        "source_round_trip_rows": len(round_trip_rows),
        "source_microstructure_current_rows": len(microstructure_current),
        "signal_file": str(out_dir / SIGNALS_FILE),
        "rejection_file": str(out_dir / REJECTIONS_FILE),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
        "decision": _summary_decision(signals, paper_confirmation, rejections),
        "warnings": {
            "paper_only": True,
            "live_trading_invoked": False,
            "does_not_wait_for_settlement": True,
            "requires_positive_bid_ask_cohort_evidence": True,
        },
        "top_signals": signals[:10],
        "top_rejections": rejections[:10],
    }
    write_json(out_dir / SUMMARY_JSON, summary)
    return summary


def run(config_path: str = "polymarket_predictive_config.example.yaml") -> dict[str, Any]:
    cfg = load_config(config_path)
    return build_price_action_paper_signals(cfg)
