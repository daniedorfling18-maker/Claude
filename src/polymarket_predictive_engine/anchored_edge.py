from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from typing import Any

from .config import EngineConfig, load_config
from .utils import boolish, now_utc, read_csv_rows, safe_float, write_csv, write_json
from .worldcup_validation import is_worldcup_market

OUTPUT_DIRNAME = "polymarket_strategy_v2"
CANDIDATES_FILE = "anchored_edge_candidates.csv"
REPORT_JSON = "anchored_edge_report.json"
REPORT_MD = "anchored_edge_report.md"
ALPHA_VALIDATED_ANCHORS_FILE = "alpha_validated_anchors.csv"
WORLDCUP_VALIDATED_ANCHORS_FILE = "worldcup_validated_anchors.csv"

ACCEPTED_FAMILY_RULES: dict[str, dict[str, Any]] = {
    "macro_rates": {
        "status": "accepted",
        "anchor_required": True,
        "normal_max_spread": 0.02,
        "normal_max_relative_spread": 0.15,
        "normal_min_liquidity": 250.0,
    },
    "crypto_btc_special": {
        "status": "accepted",
        "anchor_required": True,
        "normal_max_spread": 0.02,
        "normal_max_relative_spread": 0.15,
        "normal_min_liquidity": 250.0,
    },
    "crypto_eth_special": {
        "status": "accepted",
        "anchor_required": True,
        "normal_max_spread": 0.02,
        "normal_max_relative_spread": 0.15,
        "normal_min_liquidity": 250.0,
    },
    "crypto_updown_event": {
        "status": "accepted_for_research",
        "anchor_required": True,
        "normal_max_spread": 0.04,
        "normal_max_relative_spread": 0.20,
        "normal_min_liquidity": 100.0,
        "never_promote_from_thin_fast_feedback": True,
    },
    "sports_other": {
        "status": "accepted_with_external_odds_anchor",
        "anchor_required": True,
        "normal_max_spread": 0.02,
        "normal_max_relative_spread": 0.15,
        "normal_min_liquidity": 250.0,
    },
    "worldcup": {
        "status": "accepted_with_external_odds_anchor",
        "anchor_required": True,
        "normal_max_spread": 0.02,
        "normal_max_relative_spread": 0.15,
        "normal_min_liquidity": 250.0,
    },
    "worldcup_2026_winner": {
        "status": "accepted_with_external_odds_anchor",
        "anchor_required": True,
        "normal_max_spread": 0.02,
        "normal_max_relative_spread": 0.15,
        "normal_min_liquidity": 250.0,
    },
    "esports_match": {
        "status": "accepted_with_external_odds_anchor",
        "anchor_required": True,
        "normal_max_spread": 0.02,
        "normal_max_relative_spread": 0.15,
        "normal_min_liquidity": 250.0,
    },
    "tennis_total": {
        "status": "accepted_with_external_odds_anchor",
        "anchor_required": True,
        "normal_max_spread": 0.02,
        "normal_max_relative_spread": 0.15,
        "normal_min_liquidity": 250.0,
    },
    "tennis_itf_total": {
        "status": "accepted_with_external_odds_anchor",
        "anchor_required": True,
        "normal_max_spread": 0.02,
        "normal_max_relative_spread": 0.15,
        "normal_min_liquidity": 250.0,
    },
    "tennis_atp_total": {
        "status": "accepted_with_external_odds_anchor",
        "anchor_required": True,
        "normal_max_spread": 0.02,
        "normal_max_relative_spread": 0.15,
        "normal_min_liquidity": 250.0,
    },
    "ai_model_leader": {
        "status": "research_only_until_anchor_methodology_defined",
        "anchor_required": True,
        "normal_max_spread": 0.02,
        "normal_max_relative_spread": 0.15,
        "normal_min_liquidity": 250.0,
        "research_only": True,
    },
}

BLOCKED_FAMILY_PREFIXES = (
    "unknown",
    "near_miss_learning|unknown",
)

DEFAULT_SETTINGS = {
    "watchlist_min_edge_after_penalty": 0.03,
    "shadow_min_edge_after_penalty": 0.05,
    "spread_penalty_weight": 0.50,
    "liquidity_penalty_weight": 0.02,
    "uncertainty_penalty": 0.005,
    "reference_liquidity": 1000.0,
    "threshold_tolerance": 1e-9,
    "promotion_min_candidates": 20,
    "promotion_min_settled": 10,
    "promotion_min_roi": 0.03,
    "promotion_min_monthly_run_rate_usdc": 20.0,
    "full_promotion_min_candidates": 50,
    "full_promotion_min_settled": 30,
    "full_promotion_min_roi": 0.05,
}

VALIDATED_ANCHOR_FIELDS = [
    "token_id",
    "market_slug",
    "outcome",
    "fair_probability",
    "anchor_fair_probability",
    "anchor_source",
    "anchor_timestamp_utc",
    "anchor_path",
    "anchor_type",
    "fundamental_probability",
    "haircut_fundamental_probability",
    "fundamental_edge_after_haircut",
    "bookmaker_cross_check_pass",
    "microstructure_filter_pass",
    "validation_layer_pass",
    "signal_cohort",
]
WORLDCUP_VALIDATED_ANCHOR_FIELDS = VALIDATED_ANCHOR_FIELDS


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = cfg.raw.get("strategy_v2", {}) or {}
    return {**DEFAULT_SETTINGS, **raw}


def _num(row: dict[str, Any], *keys: str, default: float | None = None) -> float | None:
    for key in keys:
        value = safe_float(row.get(key))
        if value is not None:
            return float(value)
    return default


def _text(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _setting_strings(settings: dict[str, Any], key: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    value = settings.get(key)
    if value is None:
        value = default
    if isinstance(value, (str, Path)):
        values = [value]
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        values = []
    return tuple(
        str(item).strip().lower().replace("\\", "/")
        for item in values
        if str(item).strip()
    )


def _source_matches_any_fragment(source: str, fragments: tuple[str, ...]) -> bool:
    if not source or not fragments:
        return False
    normalised = source.strip().lower().replace("\\", "/")
    return any(fragment in normalised for fragment in fragments)


def _family(row: dict[str, Any]) -> str:
    value = _text(row, "category", "family", "signal_cohort")
    if "|" in value and value.startswith("near_miss_learning|"):
        return value
    return value.lower() or "unknown"


def _token_key(row: dict[str, Any]) -> str:
    return _text(row, "token_id", "asset_id", "outcome_token_id")


def _market_key(row: dict[str, Any]) -> str:
    return _text(row, "market_slug", "market_id", "condition_id", "question")


def _outcome_key(row: dict[str, Any]) -> str:
    return _text(row, "outcome", "side")


def _anchor_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (_token_key(row), _market_key(row), _outcome_key(row).lower())


def _load_configured_anchor_rows(cfg: EngineConfig) -> list[dict[str, Any]]:
    settings = _settings(cfg)
    configured = settings.get("anchor_probability_paths")
    if configured is None:
        configured = [
            cfg.output_root / "polymarket_training" / "sharp_fundamental_probabilities.csv",
            cfg.output_root / "polymarket_training" / "crypto_fundamental_probabilities.csv",
            Path("inputs/polymarket/strategy_v2_manual_anchors.csv"),
        ]
    if isinstance(configured, (str, Path)):
        configured = [configured]

    rows: list[dict[str, Any]] = []
    for raw_path in configured:
        if raw_path in {None, ""}:
            continue
        path = Path(str(raw_path))
        if not path.is_absolute():
            path = cfg.path.parent / path
        for row in read_csv_rows(path):
            probability = _num(
                row,
                "fair_probability",
                "probability",
                "fundamental_probability",
                "model_probability",
                "anchor_probability",
            )
            if probability is None or not 0.0 <= probability <= 1.0:
                continue
            rows.append(
                {
                    **row,
                    "anchor_fair_probability": probability,
                    "anchor_source": _text(row, "anchor_source", "source", "model_source") or str(path),
                    "anchor_timestamp_utc": _text(row, "anchor_timestamp_utc", "timestamp", "generated_at_utc"),
                    "anchor_path": str(path),
                }
            )
    return rows


def _validated_alpha_anchor_rows(cfg: EngineConfig) -> list[dict[str, Any]]:
    """Build conservative Strategy V2 anchors from alpha-validated sharp probabilities.

    The mispricing-alpha layer already attaches bookmaker/fundamental probabilities and applies the
    configured haircut. Strategy V2 should only consume those probabilities when the bookmaker
    cross-check passed and the row is either World Cup related or comes from an explicitly allowed
    independent sharp-anchor source. Using the haircutted probability makes the anchor intentionally
    more conservative than the raw fundamental estimate.
    """
    source_path = cfg.output_root / "polymarket_predictions" / "mispricing_alpha_scores.csv"
    shadow_settings = cfg.raw.get("shadow_cohort_validation", {}) or {}
    non_worldcup_source_fragments = _setting_strings(
        shadow_settings,
        "allowed_non_worldcup_fundamental_source_fragments",
        ("sharp_fundamental_probabilities.csv",),
    )
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in read_csv_rows(source_path):
        if not boolish(row.get("bookmaker_cross_check_pass")):
            continue
        probability = _num(row, "haircut_fundamental_probability", "fundamental_probability")
        if probability is None or not 0.0 <= probability <= 1.0:
            continue
        is_worldcup = is_worldcup_market(row)
        fundamental_source = _text(row, "fundamental_source") or "bookmaker_fundamental"
        if not is_worldcup and not _source_matches_any_fragment(fundamental_source, non_worldcup_source_fragments):
            continue
        token_id = _token_key(row)
        market_slug = _market_key(row)
        outcome = _outcome_key(row)
        if not token_id and not (market_slug and outcome):
            continue
        key = (token_id, market_slug, outcome.lower())
        if key in seen:
            continue
        seen.add(key)
        timestamp = _text(row, "prediction_timestamp", "generated_at_utc") or now_utc()
        anchor_type = "worldcup_validated_haircut" if is_worldcup else "alpha_validated_haircut"
        anchor_source_prefix = "validated_worldcup_haircut" if is_worldcup else "validated_alpha_haircut"
        rows.append(
            {
                "token_id": token_id,
                "market_slug": market_slug,
                "outcome": outcome,
                "fair_probability": probability,
                "anchor_fair_probability": probability,
                "anchor_source": f"{anchor_source_prefix}:{fundamental_source}",
                "anchor_timestamp_utc": timestamp,
                "anchor_path": str(source_path),
                "anchor_type": anchor_type,
                "fundamental_probability": row.get("fundamental_probability", ""),
                "haircut_fundamental_probability": row.get("haircut_fundamental_probability", ""),
                "fundamental_edge_after_haircut": row.get("fundamental_edge_after_haircut", ""),
                "bookmaker_cross_check_pass": row.get("bookmaker_cross_check_pass", ""),
                "microstructure_filter_pass": row.get("microstructure_filter_pass", ""),
                "validation_layer_pass": row.get("validation_layer_pass", ""),
                "signal_cohort": row.get("signal_cohort", ""),
            }
        )
    rows.sort(key=lambda item: (str(item.get("market_slug") or ""), str(item.get("outcome") or "")))
    return rows


def _validated_worldcup_anchor_rows(cfg: EngineConfig) -> list[dict[str, Any]]:
    """Backward-compatible view of the World Cup subset of alpha-validated anchors."""
    return [row for row in _validated_alpha_anchor_rows(cfg) if row.get("anchor_type") == "worldcup_validated_haircut"]


def _anchor_index(anchor_rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    indexed: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in anchor_rows:
        key = _anchor_key(row)
        token_id, market_slug, outcome = key
        if token_id:
            indexed[(token_id, "", "")] = row
            if outcome:
                indexed[(token_id, "", outcome)] = row
        if market_slug and outcome:
            indexed[("", market_slug, outcome)] = row
        if market_slug:
            indexed[("", market_slug, "")] = row
        indexed[key] = row
    return indexed


def _find_anchor(row: dict[str, Any], anchors: dict[tuple[str, str, str], dict[str, Any]]) -> dict[str, Any] | None:
    token_id, market_slug, outcome = _anchor_key(row)

    # If a prediction row has an explicit outcome, require an outcome-specific anchor.
    # Do not fall back to market-level anchors, because that can leak a Yes anchor onto No.
    keys: list[tuple[str, str, str]] = []
    if outcome:
        if token_id and market_slug:
            keys.append((token_id, market_slug, outcome))
        if token_id:
            keys.append((token_id, "", outcome))
        if market_slug:
            keys.append(("", market_slug, outcome))
    else:
        if token_id and market_slug:
            keys.append((token_id, market_slug, ""))
        if token_id:
            keys.append((token_id, "", ""))
        if market_slug:
            keys.append(("", market_slug, ""))

    for key in keys:
        if key in anchors:
            return anchors[key]
    return None


def _price(row: dict[str, Any]) -> float | None:
    return _num(row, "executable_price", "executable_buy_price", "best_ask", "market_price", "market_midpoint")


def _relative_spread(spread: float | None, price: float | None) -> float | None:
    if spread is None or price is None or price <= 0:
        return None
    return spread / price


def _family_rule(family: str) -> dict[str, Any] | None:
    if family in ACCEPTED_FAMILY_RULES:
        return ACCEPTED_FAMILY_RULES[family]
    if family.startswith("tennis_") and family.endswith("_total"):
        return ACCEPTED_FAMILY_RULES["tennis_total"]
    return None


def _blocked_family_reason(family: str) -> str:
    family = str(family or "unknown").lower()
    if family == "unknown" or family.startswith("unknown"):
        return "family_unknown"
    if family.startswith("near_miss_learning|unknown"):
        return "metadata_blocked_unknown_near_miss"
    return ""


def _edge_after_penalties(
    *,
    anchor_probability: float,
    executable_price: float,
    spread: float | None,
    liquidity: float | None,
    settings: dict[str, Any],
) -> tuple[float, dict[str, float]]:
    raw_edge = anchor_probability - executable_price
    spread_penalty = max(0.0, spread or 0.0) * float(settings["spread_penalty_weight"])
    reference_liquidity = max(1.0, float(settings["reference_liquidity"]))
    liquidity_penalty = 0.0
    if liquidity is None:
        liquidity_penalty = float(settings["liquidity_penalty_weight"])
    else:
        liquidity_penalty = max(0.0, 1.0 - min(1.0, liquidity / reference_liquidity)) * float(
            settings["liquidity_penalty_weight"]
        )
    uncertainty_penalty = float(settings["uncertainty_penalty"])
    return raw_edge - spread_penalty - liquidity_penalty - uncertainty_penalty, {
        "anchor_raw_edge": raw_edge,
        "spread_penalty": spread_penalty,
        "liquidity_penalty": liquidity_penalty,
        "uncertainty_penalty": uncertainty_penalty,
    }


def _candidate_status(
    *,
    family: str,
    rule: dict[str, Any] | None,
    anchor: dict[str, Any] | None,
    price: float | None,
    spread: float | None,
    relative_spread: float | None,
    liquidity: float | None,
    edge_after_penalty: float | None,
    settings: dict[str, Any],
) -> tuple[str, list[str]]:
    blockers: list[str] = []
    family_block = _blocked_family_reason(family)
    if family_block:
        blockers.append(family_block)
    if rule is None:
        blockers.append("family_not_accepted")
    elif rule.get("research_only"):
        blockers.append("family_research_only_until_anchor_methodology_defined")
    if anchor is None:
        blockers.append("missing_independent_anchor")
    if price is None or not 0.0 < price < 1.0:
        blockers.append("missing_executable_price")
    tolerance = float(settings.get("threshold_tolerance", 1e-9))
    if rule is not None:
        max_spread = safe_float(rule.get("normal_max_spread"))
        max_relative_spread = safe_float(rule.get("normal_max_relative_spread"))
        min_liquidity = safe_float(rule.get("normal_min_liquidity"))
        if max_spread is not None and (spread is None or spread > max_spread + tolerance):
            blockers.append("spread_above_strategy_v2_limit")
        if max_relative_spread is not None and (relative_spread is None or relative_spread > max_relative_spread + tolerance):
            blockers.append("relative_spread_above_strategy_v2_limit")
        if min_liquidity is not None and (liquidity is None or liquidity < min_liquidity - tolerance):
            blockers.append("liquidity_below_strategy_v2_limit")
    if edge_after_penalty is None:
        blockers.append("edge_not_computable")
    elif edge_after_penalty < float(settings["watchlist_min_edge_after_penalty"]) - tolerance:
        blockers.append("edge_after_penalty_below_watchlist_minimum")

    if blockers:
        return "rejected", blockers
    if edge_after_penalty is not None and edge_after_penalty >= float(settings["shadow_min_edge_after_penalty"]) - tolerance:
        return "shadow_candidate", []
    return "watchlist", []


def build_anchored_edge_candidates(cfg: EngineConfig) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    settings = _settings(cfg)
    prediction_rows = read_csv_rows(cfg.output_root / "polymarket_predictions" / "predictions.csv")
    output_dir = cfg.output_root / OUTPUT_DIRNAME
    output_dir.mkdir(parents=True, exist_ok=True)
    alpha_validated_anchor_rows = _validated_alpha_anchor_rows(cfg)
    worldcup_validated_anchor_rows = [
        row for row in alpha_validated_anchor_rows if row.get("anchor_type") == "worldcup_validated_haircut"
    ]
    write_csv(
        output_dir / ALPHA_VALIDATED_ANCHORS_FILE,
        alpha_validated_anchor_rows,
        fieldnames=VALIDATED_ANCHOR_FIELDS,
    )
    write_csv(
        output_dir / WORLDCUP_VALIDATED_ANCHORS_FILE,
        worldcup_validated_anchor_rows,
        fieldnames=WORLDCUP_VALIDATED_ANCHOR_FIELDS,
    )
    anchor_rows = _load_configured_anchor_rows(cfg) + alpha_validated_anchor_rows
    anchors = _anchor_index(anchor_rows)
    candidates: list[dict[str, Any]] = []

    for row in prediction_rows:
        family = _family(row)
        rule = _family_rule(family)
        anchor = _find_anchor(row, anchors)
        price = _price(row)
        spread = _num(row, "spread")
        liquidity = _num(row, "liquidity")
        relative_spread = _relative_spread(spread, price)
        anchor_probability = None if anchor is None else safe_float(anchor.get("anchor_fair_probability"))
        edge_after_penalty = None
        penalty_parts = {
            "anchor_raw_edge": "",
            "spread_penalty": "",
            "liquidity_penalty": "",
            "uncertainty_penalty": "",
        }
        if anchor_probability is not None and price is not None:
            edge_after_penalty, penalty_parts = _edge_after_penalties(
                anchor_probability=float(anchor_probability),
                executable_price=price,
                spread=spread,
                liquidity=liquidity,
                settings=settings,
            )
        status, blockers = _candidate_status(
            family=family,
            rule=rule,
            anchor=anchor,
            price=price,
            spread=spread,
            relative_spread=relative_spread,
            liquidity=liquidity,
            edge_after_penalty=edge_after_penalty,
            settings=settings,
        )
        candidates.append(
            {
                "generated_at_utc": now_utc(),
                "strategy_version": "polymarket_strategy_v2_anchored_edge",
                "family": family,
                "family_status": "" if rule is None else str(rule.get("status", "")),
                "status": status,
                "blockers": "; ".join(blockers),
                "market_slug": _market_key(row),
                "question": _text(row, "question"),
                "outcome": _outcome_key(row),
                "token_id": _token_key(row),
                "anchor_fair_probability": "" if anchor_probability is None else anchor_probability,
                "anchor_source": "" if anchor is None else _text(anchor, "anchor_source"),
                "anchor_timestamp_utc": "" if anchor is None else _text(anchor, "anchor_timestamp_utc"),
                "anchor_path": "" if anchor is None else _text(anchor, "anchor_path"),
                "anchor_type": "" if anchor is None else _text(anchor, "anchor_type"),
                "fundamental_edge_after_haircut": "" if anchor is None else _text(anchor, "fundamental_edge_after_haircut"),
                "bookmaker_cross_check_pass": "" if anchor is None else _text(anchor, "bookmaker_cross_check_pass"),
                "executable_price": "" if price is None else price,
                "liquidity": "" if liquidity is None else liquidity,
                "spread": "" if spread is None else spread,
                "relative_spread": "" if relative_spread is None else relative_spread,
                **penalty_parts,
                "risk_adjusted_anchor_edge": "" if edge_after_penalty is None else edge_after_penalty,
                "model_probability": row.get("model_probability", row.get("calibrated_probability", "")),
                "market_probability": row.get("market_probability", row.get("market_midpoint", row.get("midpoint", ""))),
                "alpha_trade_candidate": row.get("alpha_trade_candidate", ""),
                "validation_layer_pass": row.get("validation_layer_pass", ""),
            }
        )

    candidates.sort(
        key=lambda row: safe_float(row.get("risk_adjusted_anchor_edge")) or -999.0,
        reverse=True,
    )
    write_csv(output_dir / CANDIDATES_FILE, candidates)
    report = _build_report(
        candidates,
        anchor_rows,
        settings,
        alpha_validated_anchor_rows=len(alpha_validated_anchor_rows),
        worldcup_validated_anchor_rows=len(worldcup_validated_anchor_rows),
    )
    write_json(output_dir / REPORT_JSON, report)
    _write_markdown(output_dir / REPORT_MD, report)
    return candidates, report


def _family_rows(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        by_family[str(row.get("family") or "unknown")].append(row)
    rows = []
    for family, family_rows in by_family.items():
        anchored_rows = [row for row in family_rows if safe_float(row.get("anchor_fair_probability")) is not None]
        candidate_rows = [row for row in family_rows if row.get("status") in {"watchlist", "shadow_candidate"}]
        shadow_rows = [row for row in family_rows if row.get("status") == "shadow_candidate"]
        edges = [safe_float(row.get("risk_adjusted_anchor_edge")) for row in anchored_rows]
        clean_edges = [float(item) for item in edges if item is not None]
        rows.append(
            {
                "family": family,
                "rows": len(family_rows),
                "anchored_rows": len(anchored_rows),
                "anchored_candidates": len(candidate_rows),
                "shadow_candidates": len(shadow_rows),
                "median_edge": median(clean_edges) if clean_edges else None,
                "best_edge": max(clean_edges) if clean_edges else None,
                "status_counts": dict(Counter(str(row.get("status") or "unknown") for row in family_rows).most_common()),
                "top_blockers": dict(Counter(str(row.get("blockers") or "none") for row in family_rows).most_common(5)),
                "action": _family_action(candidate_rows, shadow_rows),
            }
        )
    rows.sort(key=lambda row: (row["shadow_candidates"], row["anchored_candidates"], row["anchored_rows"], row.get("best_edge") or -999.0), reverse=True)
    return rows


def _family_action(candidate_rows: list[dict[str, Any]], shadow_rows: list[dict[str, Any]]) -> str:
    if len(shadow_rows) >= 20:
        return "review_shadow_evidence_before_any_promotion"
    if shadow_rows:
        return "collect_more_shadow_evidence"
    if candidate_rows:
        return "watchlist_only_until_edge_or_microstructure_improves"
    return "no_actionable_anchored_edge"


def _build_report(
    candidates: list[dict[str, Any]],
    anchor_rows: list[dict[str, Any]],
    settings: dict[str, Any],
    *,
    alpha_validated_anchor_rows: int = 0,
    worldcup_validated_anchor_rows: int = 0,
) -> dict[str, Any]:
    status_counts = Counter(str(row.get("status") or "unknown") for row in candidates)
    blocker_counts = Counter(str(row.get("blockers") or "none") for row in candidates)
    family_summary = _family_rows(candidates)
    anchored_rows = [row for row in candidates if safe_float(row.get("anchor_fair_probability")) is not None]
    anchored_rejections = [row for row in anchored_rows if row.get("status") == "rejected"]
    anchored_rejections.sort(key=lambda row: safe_float(row.get("risk_adjusted_anchor_edge")) if safe_float(row.get("risk_adjusted_anchor_edge")) is not None else -999.0, reverse=True)
    shadow_count = status_counts.get("shadow_candidate", 0)
    watchlist_count = status_counts.get("watchlist", 0)
    if shadow_count > 0:
        decision = "candidate_family_found"
        recommended_action = "Keep Strategy V2 shadow-only and collect settled evidence for the candidate families."
    elif watchlist_count > 0:
        decision = "collect_more_evidence"
        recommended_action = "Anchored watchlist exists, but no row clears shadow edge and microstructure filters yet."
    else:
        decision = "collect_more_evidence"
        recommended_action = "No actionable anchored edge found; improve anchors/classification before trading."
    return {
        "status": "ok",
        "strategy_version": "polymarket_strategy_v2_anchored_edge",
        "generated_at_utc": now_utc(),
        "decision": decision,
        "recommended_action": recommended_action,
        "settings": settings,
        "rows_scored": len(candidates),
        "anchor_rows_loaded": len(anchor_rows),
        "alpha_validated_anchor_rows": alpha_validated_anchor_rows,
        "alpha_validated_anchor_file": str(Path(OUTPUT_DIRNAME) / ALPHA_VALIDATED_ANCHORS_FILE),
        "worldcup_validated_anchor_rows": worldcup_validated_anchor_rows,
        "worldcup_validated_anchor_file": str(Path(OUTPUT_DIRNAME) / WORLDCUP_VALIDATED_ANCHORS_FILE),
        "anchored_rows": len(anchored_rows),
        "status_counts": dict(status_counts.most_common()),
        "top_blockers": dict(blocker_counts.most_common(10)),
        "family_summary": family_summary,
        "top_candidates": [row for row in candidates if row.get("status") in {"shadow_candidate", "watchlist"}][:25],
        "top_anchored_rejections": anchored_rejections[:25],
        "warnings": {
            "unknown_rows": sum(1 for row in candidates if str(row.get("family") or "").startswith("unknown")),
            "missing_anchor_rows": sum(1 for row in candidates if "missing_independent_anchor" in str(row.get("blockers") or "")),
            "metadata_blocked_rows": sum(1 for row in candidates if "metadata_blocked" in str(row.get("blockers") or "")),
            "shadow_only": True,
        },
    }


def _fmt(value: Any, digits: int = 4) -> str:
    number = safe_float(value)
    if number is None:
        return "-"
    return f"{number:.{digits}f}"


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Polymarket Strategy V2 Anchored Edge Report",
        "",
        f"Generated: {report['generated_at_utc']}",
        "",
        f"Decision: **{report['decision']}**",
        "",
        report["recommended_action"],
        "",
        "## Summary",
        "",
        f"- Rows scored: {report['rows_scored']}",
        f"- Anchor rows loaded: {report['anchor_rows_loaded']}",
        f"- Alpha-validated anchors: {report.get('alpha_validated_anchor_rows', report.get('worldcup_validated_anchor_rows', 0))}",
        f"- World Cup validated anchors: {report.get('worldcup_validated_anchor_rows', 0)}",
        f"- Rows matched to anchors: {report.get('anchored_rows', 0)}",
        f"- Status counts: `{report['status_counts']}`",
        f"- Top blockers: `{report['top_blockers']}`",
        "",
        "## Family summary",
        "",
        "| Family | Rows | Anchored rows | Actionable | Shadow | Median edge | Best edge | Action |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in report.get("family_summary", [])[:20]:
        lines.append(
            "| {family} | {rows} | {anchored_rows} | {anchored} | {shadow} | {median_edge} | {best_edge} | {action} |".format(
                family=row.get("family", ""),
                rows=row.get("rows", 0),
                anchored_rows=row.get("anchored_rows", 0),
                anchored=row.get("anchored_candidates", 0),
                shadow=row.get("shadow_candidates", 0),
                median_edge=_fmt(row.get("median_edge")),
                best_edge=_fmt(row.get("best_edge")),
                action=row.get("action", ""),
            )
        )
    lines.extend([
        "",
        "## Top candidates",
        "",
        "| Family | Market | Outcome | Anchor fair | Price | Edge after penalty | Status |",
        "|---|---|---|---:|---:|---:|---|",
    ])
    for row in report.get("top_candidates", [])[:25]:
        lines.append(
            "| {family} | {market} | {outcome} | {anchor} | {price} | {edge} | {status} |".format(
                family=row.get("family", ""),
                market=str(row.get("market_slug", ""))[:80],
                outcome=row.get("outcome", ""),
                anchor=_fmt(row.get("anchor_fair_probability")),
                price=_fmt(row.get("executable_price")),
                edge=_fmt(row.get("risk_adjusted_anchor_edge")),
                status=row.get("status", ""),
            )
        )
    lines.extend([
        "",
        "## Top anchored rejections / near misses",
        "",
        "| Family | Market | Outcome | Anchor fair | Price | Edge after penalty | Blockers |",
        "|---|---|---|---:|---:|---:|---|",
    ])
    for row in report.get("top_anchored_rejections", [])[:25]:
        lines.append(
            "| {family} | {market} | {outcome} | {anchor} | {price} | {edge} | {blockers} |".format(
                family=row.get("family", ""),
                market=str(row.get("market_slug", ""))[:80],
                outcome=row.get("outcome", ""),
                anchor=_fmt(row.get("anchor_fair_probability")),
                price=_fmt(row.get("executable_price")),
                edge=_fmt(row.get("risk_adjusted_anchor_edge")),
                blockers=str(row.get("blockers", ""))[:120],
            )
        )
    lines.extend(
        [
            "",
            "## Warnings",
            "",
            f"```json\n{report.get('warnings', {})}\n```",
            "",
            "This report is shadow-only research evidence. It is not permission to paper trade or live trade.",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run(config_path: str = "polymarket_predictive_config.example.yaml") -> dict[str, Any]:
    cfg = load_config(config_path)
    candidates, report = build_anchored_edge_candidates(cfg)
    return {
        "status": "ok",
        "strategy_version": "polymarket_strategy_v2_anchored_edge",
        "generated_at_utc": report["generated_at_utc"],
        "rows_scored": len(candidates),
        "anchor_rows_loaded": report["anchor_rows_loaded"],
        "anchored_rows": report["anchored_rows"],
        "alpha_validated_anchor_rows": report.get("alpha_validated_anchor_rows", 0),
        "worldcup_validated_anchor_rows": report.get("worldcup_validated_anchor_rows", 0),
        "status_counts": report["status_counts"],
        "decision": report["decision"],
        "recommended_action": report["recommended_action"],
        "candidates_file": str(cfg.output_root / OUTPUT_DIRNAME / CANDIDATES_FILE),
        "report_json": str(cfg.output_root / OUTPUT_DIRNAME / REPORT_JSON),
        "report_md": str(cfg.output_root / OUTPUT_DIRNAME / REPORT_MD),
    }
