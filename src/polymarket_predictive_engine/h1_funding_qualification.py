"""Exact sharp-qualified H1 and Tier-0 funding prerequisite (WO-93).

WO-50 remains an advisory human decision policy.  This module adds a
tighten-only prerequisite: the exact market named by that policy must have a
fresh, unambiguous sharp anchor inside its proposed maker quote band and
market-specific official-book Tier-0 replay evidence.  Aggregate anchor or
replay badges can never satisfy this contract.
"""

from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Any, Mapping

from .config import EngineConfig
from .utils import now_utc, parse_timestamp, read_csv_rows, read_json, safe_float, write_json


REGISTERED_AT_UTC = "2026-07-16T14:03:26Z"
MAX_ANCHOR_AGE_SECONDS = 6 * 3600
MAX_PRICE_AGE_SECONDS = 300
MAX_REPLAY_AGE_SECONDS = 30 * 60
FUTURE_TIMESTAMP_TOLERANCE_SECONDS = 60
MAX_ANCHOR_DISAGREEMENT = 0.03
MIN_TIER0_EVALUABLE_OPPORTUNITIES = 30
MIN_TIER0_CONFIRMED_FILLS = 10
MIN_TIER0_HORIZON_COVERAGE_RATIO = 0.80
MAX_TIER0_SIMULATION_TO_REALITY_HAIRCUT = 1.0
TIER0_HORIZONS = ("5m", "15m", "60m")


def _positive(value: Any, default: float) -> float:
    parsed = safe_float(value)
    return float(parsed) if parsed is not None and parsed > 0 else float(default)


def _nonnegative(value: Any, default: float) -> float:
    parsed = safe_float(value)
    return float(parsed) if parsed is not None and parsed >= 0 else float(default)


def effective_qualification_settings(cfg: EngineConfig) -> dict[str, Any]:
    """Return tighten-only WO-93 settings.

    Maximums may only become smaller and minimums may only become larger.
    Invalid values fall back to the registered constants.
    """

    raw = cfg.raw.get("decision_policy", {}) if isinstance(cfg.raw.get("decision_policy"), dict) else {}
    coverage = _positive(
        raw.get("h1_tier0_min_horizon_coverage_ratio"),
        MIN_TIER0_HORIZON_COVERAGE_RATIO,
    )
    return {
        "max_anchor_age_seconds": min(
            MAX_ANCHOR_AGE_SECONDS,
            _nonnegative(raw.get("h1_max_anchor_age_seconds"), MAX_ANCHOR_AGE_SECONDS),
        ),
        "max_price_age_seconds": min(
            MAX_PRICE_AGE_SECONDS,
            _nonnegative(raw.get("h1_max_price_age_seconds"), MAX_PRICE_AGE_SECONDS),
        ),
        "max_replay_age_seconds": min(
            MAX_REPLAY_AGE_SECONDS,
            _nonnegative(raw.get("h1_tier0_max_replay_age_seconds"), MAX_REPLAY_AGE_SECONDS),
        ),
        "future_timestamp_tolerance_seconds": min(
            FUTURE_TIMESTAMP_TOLERANCE_SECONDS,
            _nonnegative(
                raw.get("h1_future_timestamp_tolerance_seconds"),
                FUTURE_TIMESTAMP_TOLERANCE_SECONDS,
            ),
        ),
        "max_anchor_disagreement": min(
            MAX_ANCHOR_DISAGREEMENT,
            _nonnegative(raw.get("h1_max_anchor_disagreement"), MAX_ANCHOR_DISAGREEMENT),
        ),
        "min_tier0_evaluable_opportunities": max(
            MIN_TIER0_EVALUABLE_OPPORTUNITIES,
            int(
                _positive(
                    raw.get("h1_tier0_min_evaluable_opportunities"),
                    MIN_TIER0_EVALUABLE_OPPORTUNITIES,
                )
            ),
        ),
        "min_tier0_confirmed_fills": max(
            MIN_TIER0_CONFIRMED_FILLS,
            int(
                _positive(
                    raw.get("h1_tier0_min_confirmed_fills"),
                    MIN_TIER0_CONFIRMED_FILLS,
                )
            ),
        ),
        "min_tier0_horizon_coverage_ratio": min(
            1.0,
            max(
                MIN_TIER0_HORIZON_COVERAGE_RATIO,
                coverage,
            ),
        ),
        "max_tier0_simulation_to_reality_haircut": min(
            MAX_TIER0_SIMULATION_TO_REALITY_HAIRCUT,
            _nonnegative(
                raw.get("h1_tier0_max_simulation_to_reality_haircut"),
                MAX_TIER0_SIMULATION_TO_REALITY_HAIRCUT,
            ),
        ),
    }


def _timestamp_age(
    value: Any,
    *,
    as_of: datetime,
    future_tolerance_seconds: float,
) -> tuple[datetime | None, float | None, str | None]:
    stamp = parse_timestamp(value)
    if stamp is None:
        return None, None, "missing_or_invalid_timestamp"
    signed_age = (as_of - stamp).total_seconds()
    if signed_age < -future_tolerance_seconds:
        return stamp, signed_age, "timestamp_is_in_the_future"
    return stamp, max(0.0, signed_age), None


def _predates_registration(stamp: datetime | None) -> bool:
    boundary = parse_timestamp(REGISTERED_AT_UTC)
    return stamp is None or boundary is None or stamp <= boundary


def _current_portfolio_market(
    study: Mapping[str, Any], condition_id: str
) -> dict[str, Any] | None:
    portfolio = study.get("portfolio") if isinstance(study.get("portfolio"), list) else []
    for row in portfolio:
        if isinstance(row, Mapping) and str(row.get("condition_id") or "").strip() == condition_id:
            return dict(row)
    return None


def _latest_prediction(cfg: EngineConfig, token_id: str) -> dict[str, str] | None:
    latest: dict[str, str] | None = None
    latest_stamp: datetime | None = None
    for row in read_csv_rows(cfg.output_root / "polymarket_predictions" / "predictions.csv"):
        if str(row.get("token_id") or row.get("asset_id") or "").strip() != token_id:
            continue
        stamp = parse_timestamp(row.get("prediction_timestamp") or row.get("generated_at_utc"))
        if latest is None or (stamp is not None and (latest_stamp is None or stamp > latest_stamp)):
            latest = row
            latest_stamp = stamp
    return latest


def _sharp_qualification(
    cfg: EngineConfig,
    market: Mapping[str, Any] | None,
    *,
    as_of: datetime,
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    blockers: list[str] = []
    condition_id = str((market or {}).get("condition_id") or "").strip()
    token_id = str((market or {}).get("token_id") or "").strip()
    quote_bid = safe_float((market or {}).get("quote_bid_price"))
    quote_ask = safe_float((market or {}).get("quote_ask_price"))
    if market is None:
        blockers.append("funding_candidate_not_in_current_portfolio")
    if not token_id:
        blockers.append("funding_candidate_token_id_missing")
    if quote_bid is None or quote_ask is None or not (0 < quote_bid < quote_ask < 1):
        blockers.append("proposed_maker_quote_band_invalid")

    audit_path = cfg.governance_root / "sharp_anchor_mapping_audit.csv"
    joined = [
        row
        for row in read_csv_rows(audit_path)
        if token_id
        and str(row.get("token_id") or "").strip() == token_id
        and str(row.get("mapping_status") or "").strip().lower() == "joined"
    ]
    fresh: list[tuple[dict[str, str], float, float]] = []
    for row in joined:
        probability = safe_float(row.get("anchor_probability"))
        stamp, age, timestamp_error = _timestamp_age(
            row.get("anchor_timestamp_utc"),
            as_of=as_of,
            future_tolerance_seconds=float(settings["future_timestamp_tolerance_seconds"]),
        )
        if (
            probability is not None
            and 0 < probability < 1
            and timestamp_error is None
            and not _predates_registration(stamp)
            and age is not None
            and age <= float(settings["max_anchor_age_seconds"])
        ):
            fresh.append((row, float(probability), age))
    if not audit_path.exists():
        blockers.append("sharp_mapping_audit_missing")
    elif not joined:
        blockers.append("no_exact_token_sharp_join")
    elif not fresh:
        blockers.append("no_fresh_valid_exact_token_sharp_join")

    probabilities = [item[1] for item in fresh]
    probability_span = max(probabilities) - min(probabilities) if probabilities else None
    anchor_probability = median(probabilities) if probabilities else None
    if probability_span is not None and probability_span > float(settings["max_anchor_disagreement"]):
        blockers.append("sharp_sources_disagree")

    prediction = _latest_prediction(cfg, token_id) if token_id else None
    price_timestamp = None
    price_age = None
    best_bid = safe_float((prediction or {}).get("best_bid"))
    best_ask = safe_float((prediction or {}).get("best_ask") or (prediction or {}).get("executable_price"))
    if prediction is None:
        blockers.append("current_polymarket_prediction_missing")
    else:
        price_timestamp, price_age, timestamp_error = _timestamp_age(
            prediction.get("prediction_timestamp") or prediction.get("generated_at_utc"),
            as_of=as_of,
            future_tolerance_seconds=float(settings["future_timestamp_tolerance_seconds"]),
        )
        if timestamp_error is not None:
            blockers.append(f"polymarket_price_{timestamp_error}")
        elif _predates_registration(price_timestamp):
            blockers.append("polymarket_price_not_post_registration")
        elif price_age is None or price_age > float(settings["max_price_age_seconds"]):
            blockers.append("polymarket_bid_ask_stale")
        if best_bid is None or best_ask is None or not (0 < best_bid < best_ask < 1):
            blockers.append("current_executable_bid_ask_invalid")

    fair_inside_quote_band = bool(
        anchor_probability is not None
        and quote_bid is not None
        and quote_ask is not None
        and quote_bid <= anchor_probability <= quote_ask
    )
    if anchor_probability is not None and not fair_inside_quote_band:
        blockers.append("sharp_fair_outside_proposed_maker_quote_band")

    return {
        "state": "pass" if not blockers else "fail",
        "blockers": blockers,
        "condition_id": condition_id,
        "token_id": token_id,
        "mapping_audit_path": str(audit_path),
        "exact_token_join_rows": len(joined),
        "fresh_exact_token_join_rows": len(fresh),
        "anchor_probability": None if anchor_probability is None else round(anchor_probability, 6),
        "anchor_probability_span": None if probability_span is None else round(probability_span, 6),
        "latest_anchor_age_seconds": min((item[2] for item in fresh), default=None),
        "proposed_quote_bid": quote_bid,
        "proposed_quote_ask": quote_ask,
        "fair_inside_proposed_quote_band": fair_inside_quote_band,
        "current_best_bid": best_bid,
        "current_best_ask": best_ask,
        "current_price_timestamp_utc": None if price_timestamp is None else price_timestamp.isoformat(),
        "current_price_age_seconds": price_age,
    }


def _tier0_qualification(
    cfg: EngineConfig,
    study: Mapping[str, Any],
    market: Mapping[str, Any] | None,
    *,
    as_of: datetime,
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    blockers: list[str] = []
    replay_path = cfg.output_root / "maker_carry" / "maker_fill_replay.json"
    replay = read_json(replay_path, default={}) or {}
    if not isinstance(replay, dict):
        replay = {}
    condition_id = str((market or {}).get("condition_id") or "").strip()
    token_id = str((market or {}).get("token_id") or "").strip()
    replay_stamp, replay_age, timestamp_error = _timestamp_age(
        replay.get("generated_at_utc"),
        as_of=as_of,
        future_tolerance_seconds=float(settings["future_timestamp_tolerance_seconds"]),
    )
    study_stamp = parse_timestamp(study.get("generated_at_utc"))
    if not replay_path.exists():
        blockers.append("tier0_replay_artifact_missing")
    if timestamp_error is not None:
        blockers.append(f"tier0_replay_{timestamp_error}")
    elif replay_age is None or replay_age > float(settings["max_replay_age_seconds"]):
        blockers.append("tier0_replay_stale")
    if study_stamp is None:
        blockers.append("maker_study_timestamp_missing_or_invalid")
    elif _predates_registration(study_stamp):
        blockers.append("maker_study_not_post_registration")
    elif replay_stamp is not None and replay_stamp < study_stamp:
        blockers.append("tier0_replay_predates_current_portfolio")
    if replay_stamp is not None and _predates_registration(replay_stamp):
        blockers.append("tier0_replay_not_post_registration")
    if str(replay.get("portfolio_generated_at_utc") or "") != str(study.get("generated_at_utc") or ""):
        blockers.append("tier0_replay_portfolio_version_mismatch")
    if str(replay.get("status") or "") != "ok":
        blockers.append("tier0_replay_status_not_ok")
    if str(replay.get("primary_book_source") or "") != "official":
        blockers.append("tier0_primary_source_not_official")

    per_market = replay.get("per_market_coverage") if isinstance(replay.get("per_market_coverage"), list) else []
    matches = [
        dict(row)
        for row in per_market
        if isinstance(row, Mapping)
        and str(row.get("condition_id") or "").strip() == condition_id
        and str(row.get("asset_id") or row.get("token_id") or "").strip() == token_id
    ]
    if len(matches) != 1:
        blockers.append("tier0_exact_market_evidence_missing_or_ambiguous")
    evidence = matches[0] if len(matches) == 1 else {}
    evaluable = int(safe_float(evidence.get("last_in_queue_evaluable_opportunities")) or 0)
    confirmed = int(safe_float(evidence.get("confirmed_fills")) or 0)
    if evaluable < int(settings["min_tier0_evaluable_opportunities"]):
        blockers.append("tier0_evaluable_opportunity_floor_not_met")
    if confirmed < int(settings["min_tier0_confirmed_fills"]):
        blockers.append("tier0_confirmed_fill_floor_not_met")
    if str(evidence.get("confirmed_fill_ratio_status") or "") != "observed":
        blockers.append("tier0_confirmed_fill_ratio_not_observed")

    horizon_coverage = evidence.get("by_horizon") if isinstance(evidence.get("by_horizon"), dict) else {}
    markouts = (
        evidence.get("realized_markout_distribution")
        if isinstance(evidence.get("realized_markout_distribution"), dict)
        else {}
    )
    for horizon in TIER0_HORIZONS:
        coverage_row = horizon_coverage.get(horizon) if isinstance(horizon_coverage.get(horizon), dict) else {}
        ratio = safe_float(coverage_row.get("coverage_ratio"))
        if ratio is None or ratio < float(settings["min_tier0_horizon_coverage_ratio"]):
            blockers.append(f"tier0_{horizon}_coverage_floor_not_met")
        distribution = markouts.get(horizon) if isinstance(markouts.get(horizon), dict) else {}
        count = int(safe_float(distribution.get("count")) or 0)
        if count < int(settings["min_tier0_confirmed_fills"]):
            blockers.append(f"tier0_{horizon}_markout_floor_not_met")

    haircut = safe_float(evidence.get("simulation_to_reality_haircut"))
    if haircut is None:
        blockers.append("tier0_market_haircut_missing")
    elif haircut > float(settings["max_tier0_simulation_to_reality_haircut"]):
        blockers.append("tier0_market_haircut_exceeds_registered_maximum")

    return {
        "state": "pass" if not blockers else "fail",
        "blockers": blockers,
        "replay_path": str(replay_path),
        "replay_generated_at_utc": replay.get("generated_at_utc"),
        "replay_age_seconds": replay_age,
        "primary_book_source": replay.get("primary_book_source"),
        "exact_market_rows": len(matches),
        "last_in_queue_evaluable_opportunities": evaluable,
        "confirmed_fills": confirmed,
        "confirmed_fill_ratio": safe_float(evidence.get("confirmed_fill_ratio")),
        "horizon_coverage": horizon_coverage,
        "realized_markout_distribution": markouts,
        "simulation_to_reality_haircut": haircut,
    }


def evaluate_h1_funding_qualification(
    cfg: EngineConfig,
    study: Mapping[str, Any],
    funding_candidate_condition_id: str,
    *,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate and atomically publish the exact WO-93 prerequisite."""

    generated_at = now_utc()
    as_of = observed_at or parse_timestamp(generated_at)
    if as_of is None:
        raise RuntimeError("H1 funding qualification clock did not produce a valid timestamp")
    settings = effective_qualification_settings(cfg)
    condition_id = str(funding_candidate_condition_id or "").strip()
    market = _current_portfolio_market(study, condition_id) if condition_id else None
    sharp = _sharp_qualification(cfg, market, as_of=as_of, settings=settings)
    tier0 = _tier0_qualification(cfg, study, market, as_of=as_of, settings=settings)
    blockers = [f"sharp:{item}" for item in sharp["blockers"]]
    blockers.extend(f"tier0:{item}" for item in tier0["blockers"])
    if not condition_id:
        blockers.insert(0, "funding_candidate_condition_id_missing")
    blockers = list(dict.fromkeys(blockers))
    payload = {
        "status": "qualified" if not blockers else "blocked",
        "state": "pass" if not blockers else "fail",
        "registered_at_utc": REGISTERED_AT_UTC,
        "generated_at_utc": generated_at,
        "hypothesis": "H1_sharp_anchor_maker_carry",
        "funding_candidate_condition_id": condition_id,
        "funding_candidate_token_id": str((market or {}).get("token_id") or ""),
        "funding_candidate_in_current_portfolio": market is not None,
        "sharp_qualification": sharp,
        "tier0_qualification": tier0,
        "thresholds": settings,
        "blockers": blockers,
        "funding_evidence_supported": not blockers,
        "fail_safe": (
            "Missing, stale, future-dated, ambiguous, malformed, under-covered, or adverse "
            "sharp/Tier-0 evidence forces funding evidence to fail and the WO-50 capital cap to zero."
        ),
        "advisory_only": True,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
        "order_placement_invoked": False,
    }
    write_json(cfg.output_root / "maker_carry" / "h1_funding_qualification.json", payload)
    return payload
