from __future__ import annotations

from typing import Any

from .config import EngineConfig, load_config
from .price_action_microstructure import build_latest_microstructure_features
from .utils import boolish, now_utc, read_csv_rows, read_json, safe_float, write_csv, write_json

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


def _historical_analogue_key(row: dict[str, Any]) -> str:
    family = str(row.get("family") or row.get("category") or "unknown").strip() or "unknown"
    ask = safe_float(row.get("entry_ask") or row.get("latest_ask") or row.get("best_ask"))
    bid = safe_float(row.get("entry_bid") or row.get("latest_bid") or row.get("best_bid"))
    spread = safe_float(row.get("entry_spread") or row.get("latest_spread") or row.get("spread"))
    if spread is None and ask is not None and bid is not None:
        spread = max(0.0, ask - bid)
    side = str(row.get("current_side") or row.get("price_change_side") or "").strip().upper()
    return f"{family}|ask={_price_bucket(ask)}|spread={_spread_bucket(spread)}|side={side}"


def _historical_analogue_stats(cfg: EngineConfig, settings: dict[str, Any]) -> dict[str, dict[str, Any]]:
    min_rows = int(safe_float(settings.get("paper_confirmation_min_historical_analogue_validation_rows")) or 3)
    min_positive = int(safe_float(settings.get("paper_confirmation_min_historical_analogue_positive_rows")) or 1)
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in read_csv_rows(cfg.output_root / OUTPUT_DIRNAME / MICROSTRUCTURE_TRADE_EVENTS_FILE):
        if str(row.get("split") or "").strip().lower() != "validation":
            continue
        grouped.setdefault(_historical_analogue_key(row), []).append(row)
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
        }
    return stats


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
    }


def _historical_analogue_for_row(
    row: dict[str, Any],
    stats: dict[str, dict[str, Any]],
    settings: dict[str, Any],
) -> dict[str, Any]:
    key = _historical_analogue_key(row)
    return stats.get(key) or _empty_historical_analogue(key, settings)


def _attach_historical_analogue(row: dict[str, Any], analogue: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "historical_analogue_key": analogue.get("key", ""),
        "historical_analogue_validation_rows": analogue.get("validation_rows", 0),
        "historical_analogue_positive_rows": analogue.get("positive_rows", 0),
        "historical_analogue_validation_roi": analogue.get("validation_roi", 0.0),
        "historical_analogue_win_rate": analogue.get("win_rate", 0.0),
        "historical_analogue_gate": analogue.get("state", ""),
    }


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
        analogue = _historical_analogue_for_row(feature, analogue_stats, settings)
        if require_positive_history and analogue.get("state") != "positive_historical_analogue":
            blocked += 1
            state = str(analogue.get("state") or "unknown")
            blocked_by_state[state] = blocked_by_state.get(state, 0) + 1
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
                "family": feature.get("family", ""),
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
    state = "historical_analogue_current_candidates_ready" if selected else "no_positive_historical_analogue_current_candidate"
    return selected, {
        "state": state,
        "require_positive_historical_analogue": require_positive_history,
        "fresh_matches": fresh_matches,
        "approved": len(candidates),
        "selected": len(selected),
        "blocked": blocked,
        "blocked_by_state": blocked_by_state,
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
    if qualifying:
        return True, {"diagnostics": diagnostics, "missed_positive_examples": qualifying, "evidence": evidence}
    evidence_ready = (
        int(safe_float(evidence.get("validation_positive_rows")) or 0)
        >= int(safe_float(evidence.get("minimum_validation_positive_examples")) or 1)
        and (safe_float(evidence.get("validation_roi")) or 0.0) > 0
    )
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
            "model_missed_low_price_positive_examples"
            if missed
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
        return ["sports_other", "worldcup"]
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
    family = str(row.get("family") or row.get("category") or "").strip().lower()
    market_slug = str(row.get("market_slug") or "").strip().lower()
    text = " ".join([row_cohort, family, market_slug])
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
    if not str(liquidity or "").strip() and source in {"microstructure_current_candidate", "paper_confirmation_current_candidate", "low_price_tick_probe"}:
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
        ):
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
        "low_price_tick_probe_evidence": low_price_tick_evidence,
        "low_price_tick_probe_candidates": len(low_price_tick),
        "low_price_tick_probe_signals": sum(
            1
            for signal in signals
            if signal.get("price_action_evidence_status") == "low_price_tick_requires_broker_paper_confirmation"
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
