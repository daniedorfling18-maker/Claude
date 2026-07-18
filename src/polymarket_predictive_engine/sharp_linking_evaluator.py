"""WO-105 sharp-linking funding evaluator (Route A reconciliation).

Registered by the owner-signed amendment
``docs/OWNER_AMENDMENT_SHARP_LINKING_EVALUATOR.md`` and anticipated by
``docs/EXPERIMENT_REGISTRY.md`` H1 ("a future evaluator must be registered
before it links anchor state to maker markout").

It grades the ONE exact market the WO-50 composition rule would fund - the
``most_recurrent_market`` the decision policy names, confirmed present in the
current study portfolio. Aggregate or funnel counts can never satisfy it. It
publishes ``outputs/maker_carry/sharp_linking_qualification.json`` (atomic) and
feeds the WO-99 eligibility gate as an ADDED precondition.

Qualification requires ALL of (registry H1 §1 sharp-qualified + §2 Tier-0
sufficiency):
  §1 Exact-token sharp anchor - at least one fresh (<= 6h) exact-token joined
     anchor; all fresh anchors agree within 0.03 probability; a fresh (<= 5m)
     executable Polymarket bid/ask exists; and the consensus sharp fair lies
     inside the proposed maker quote band.
  §2 Exact-market Tier-0 replay - the replay is <= 30m old, identifies and
     postdates the exact current portfolio version, uses the official book as
     its primary source, and the candidate's own market row clears the
     registered evaluable / confirmed-fill / coverage / markout-window /
     adverse-haircut minima.

FAIL-CLOSED throughout: missing, stale, future-dated, ambiguous, or malformed
evidence evaluates NOT qualified. Because real Tier-0 coverage is ~0% today,
§2 correctly holds this closed until the WO-104 markout-coverage work matures
on the VPS - that is intended, not a defect.

Registered constants are mechanically TIGHTEN-ONLY: maxima may only shrink,
minima may only grow, and an invalid override falls back to the registered
default. Reporting only: this module never places, amends, cancels, or
authorises an order, and it never loosens a gate.
"""

from __future__ import annotations

import csv
import gzip
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import EngineConfig, load_config
from .utils import now_utc, parse_timestamp, read_csv_rows, read_json, safe_float, write_json

WORK_ORDER = "WO-105"
OUTPUT_RELATIVE = "maker_carry/sharp_linking_qualification.json"

# --- §1 exact-token sharp anchor (registered, tighten-only) ---
MAX_ANCHOR_AGE_SECONDS = 6 * 3600
MAX_ANCHOR_DISAGREEMENT = 0.03
MAX_PM_BOOK_AGE_SECONDS = 5 * 60

# --- §2 exact-market Tier-0 sufficiency (registered, tighten-only) ---
MAX_REPLAY_AGE_SECONDS = 30 * 60
MIN_EVALUABLE_OPPORTUNITIES = 30
MIN_CONFIRMED_FILLS = 10
MIN_COVERAGE_RATIO = 0.80
MIN_MARKOUT_WINDOWS_PER_HORIZON = 10
MAX_ADVERSE_HAIRCUT = 1.0
REQUIRED_PRIMARY_BOOK_SOURCE = "official"
HORIZONS_MINUTES = (5, 15, 60)

SETTINGS_KEY = "sharp_linking_evaluator"


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = cfg.raw.get(SETTINGS_KEY)
    return raw if isinstance(raw, dict) else {}


def _tighter_max(settings: dict[str, Any], key: str, registered: float) -> float:
    """A configured maximum may only shrink the registered one (tighten-only)."""

    value = safe_float(settings.get(key))
    if value is None or value <= 0:
        return registered
    return min(registered, value)


def _tighter_min(settings: dict[str, Any], key: str, registered: float) -> float:
    """A configured minimum may only grow the registered one (tighten-only)."""

    value = safe_float(settings.get(key))
    if value is None or value < 0:
        return registered
    return max(registered, value)


def _condition(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"check": name, "passed": bool(passed), "detail": detail}


def _latest_official_book_row(cfg: EngineConfig, condition_id: str) -> dict[str, str] | None:
    """Newest official-book observation for the market, or None.

    Reads the append-only per-market gzip book archive
    ``outputs/maker_carry/official_books/<condition_id>.csv.gz`` and returns the
    row with the most recent parseable observation timestamp. Fail-soft: any
    read/parse problem yields None so the caller fails the freshness check
    closed rather than trusting a partial file.
    """

    path = cfg.output_root / "maker_carry" / "official_books" / f"{condition_id}.csv.gz"
    if not path.exists() or path.stat().st_size == 0:
        return None
    best: dict[str, str] | None = None
    best_stamp: datetime | None = None
    try:
        with gzip.open(path, "rt", encoding="utf-8-sig", errors="replace", newline="") as handle:
            for raw in csv.DictReader(handle):
                row = {str(k): ("" if v is None else str(v)) for k, v in raw.items()}
                stamp = parse_timestamp(row.get("collected_at_utc") or row.get("observation_timestamp"))
                if stamp is None:
                    continue
                if best_stamp is None or stamp > best_stamp:
                    best_stamp = stamp
                    best = row
    except (OSError, csv.Error):
        return None
    return best


def _per_market_row(replay: dict[str, Any], *, condition_id: str, token_id: str) -> dict[str, Any] | None:
    rows = replay.get("per_market_coverage")
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("condition_id") or "").strip() == condition_id and condition_id:
            return row
        if str(row.get("asset_id") or "").strip() == token_id and token_id:
            return row
    return None


def evaluate_sharp_linking(
    cfg: EngineConfig,
    *,
    as_of: datetime | None = None,
) -> tuple[bool, list[dict[str, Any]], dict[str, Any]]:
    """Return (qualified, per-check rows, summary) for the exact fund candidate."""

    now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    settings = _settings(cfg)
    out_root = cfg.output_root / "maker_carry"
    policy = read_json(out_root / "decision_policy.json", default={}) or {}
    study = read_json(out_root / "maker_carry_study.json", default={}) or {}
    replay = read_json(out_root / "maker_fill_replay.json", default={}) or {}
    anchors = read_csv_rows(cfg.output_root / "polymarket_training" / "sharp_fundamental_probabilities.csv")

    checks: list[dict[str, Any]] = []

    # --- Candidate identity: the exact market WO-50 would fund ---
    candidate_id = str(
        (policy.get("composition_stability") or {}).get("most_recurrent_market") or ""
    ).strip()
    portfolio = study.get("portfolio") if isinstance(study.get("portfolio"), list) else []
    candidate = next(
        (
            row
            for row in portfolio
            if isinstance(row, dict) and str(row.get("condition_id") or "").strip() == candidate_id
        ),
        None,
    )
    token_id = str((candidate or {}).get("token_id") or "").strip()
    if not token_id and candidate_id:
        for row in read_csv_rows(out_root / "maker_carry_candidates.csv"):
            if str(row.get("condition_id") or "").strip() == candidate_id:
                token_id = str(row.get("token_id") or "").strip()
                break
    checks.append(
        _condition(
            "candidate_market_identified",
            bool(candidate_id) and candidate is not None and bool(token_id),
            f"candidate={candidate_id or 'missing'}; token={token_id or 'missing'}; "
            f"in_portfolio={candidate is not None}",
        )
    )

    summary: dict[str, Any] = {
        "candidate_condition_id": candidate_id or None,
        "candidate_token_id": token_id or None,
    }

    # ===================== §1 exact-token sharp anchor =====================
    max_anchor_age = _tighter_max(settings, "max_anchor_age_seconds", MAX_ANCHOR_AGE_SECONDS)
    max_disagreement = _tighter_max(settings, "max_anchor_disagreement", MAX_ANCHOR_DISAGREEMENT)
    max_book_age = _tighter_max(settings, "max_pm_book_age_seconds", MAX_PM_BOOK_AGE_SECONDS)

    fresh_fairs: list[float] = []
    if token_id:
        for row in anchors:
            if str(row.get("token_id") or "").strip() != token_id:
                continue
            stamp = parse_timestamp(row.get("anchor_timestamp_utc"))
            prob = safe_float(row.get("probability"))
            if stamp is None or prob is None:
                continue
            age = (now - stamp.astimezone(timezone.utc)).total_seconds()
            if age < 0 or age > max_anchor_age:
                continue
            fresh_fairs.append(prob)
    checks.append(
        _condition(
            "exact_token_fresh_anchor",
            bool(fresh_fairs),
            f"fresh_joined_anchors={len(fresh_fairs)} (max age {max_anchor_age:g}s)",
        )
    )

    disagreement = (max(fresh_fairs) - min(fresh_fairs)) if len(fresh_fairs) >= 2 else 0.0
    checks.append(
        _condition(
            "fresh_anchors_agree",
            bool(fresh_fairs) and disagreement <= max_disagreement,
            f"anchor_disagreement={disagreement:.4f} (max {max_disagreement})",
        )
    )

    consensus_fair = (sum(fresh_fairs) / len(fresh_fairs)) if fresh_fairs else None
    band_lo = safe_float((candidate or {}).get("quote_bid_price"))
    band_hi = safe_float((candidate or {}).get("quote_ask_price"))
    inside_band = (
        consensus_fair is not None
        and band_lo is not None
        and band_hi is not None
        and band_hi > band_lo
        and band_lo <= consensus_fair <= band_hi
    )
    checks.append(
        _condition(
            "consensus_fair_inside_quote_band",
            inside_band,
            f"fair={round(consensus_fair, 6) if consensus_fair is not None else 'none'}; "
            f"band=[{band_lo}, {band_hi}]",
        )
    )
    summary["consensus_sharp_fair"] = round(consensus_fair, 6) if consensus_fair is not None else None
    summary["maker_quote_band"] = [band_lo, band_hi]

    book_row = _latest_official_book_row(cfg, candidate_id) if candidate_id else None
    pm_bid = safe_float((book_row or {}).get("best_bid"))
    pm_ask = safe_float((book_row or {}).get("best_ask"))
    book_stamp = parse_timestamp((book_row or {}).get("collected_at_utc") or (book_row or {}).get("observation_timestamp"))
    book_age = (now - book_stamp.astimezone(timezone.utc)).total_seconds() if book_stamp is not None else None
    fresh_book = (
        book_row is not None
        and pm_bid is not None
        and pm_ask is not None
        and pm_ask > pm_bid
        and book_age is not None
        and 0 <= book_age <= max_book_age
    )
    checks.append(
        _condition(
            "fresh_executable_pm_book",
            fresh_book,
            f"bid={pm_bid}; ask={pm_ask}; age={round(book_age, 1) if book_age is not None else 'none'}s "
            f"(max {max_book_age:g})",
        )
    )

    # ===================== §2 exact-market Tier-0 sufficiency =====================
    max_replay_age = _tighter_max(settings, "max_replay_age_seconds", MAX_REPLAY_AGE_SECONDS)
    min_evaluable = _tighter_min(settings, "min_evaluable_opportunities", MIN_EVALUABLE_OPPORTUNITIES)
    min_confirmed = _tighter_min(settings, "min_confirmed_fills", MIN_CONFIRMED_FILLS)
    min_coverage = _tighter_min(settings, "min_coverage_ratio", MIN_COVERAGE_RATIO)
    min_markouts = _tighter_min(settings, "min_markout_windows_per_horizon", MIN_MARKOUT_WINDOWS_PER_HORIZON)
    max_haircut = _tighter_max(settings, "max_adverse_haircut", MAX_ADVERSE_HAIRCUT)

    replay_generated = parse_timestamp(replay.get("generated_at_utc"))
    replay_age = (now - replay_generated.astimezone(timezone.utc)).total_seconds() if replay_generated is not None else None
    checks.append(
        _condition(
            "tier0_replay_fresh",
            replay_age is not None and 0 <= replay_age <= max_replay_age,
            f"replay_age={round(replay_age, 1) if replay_age is not None else 'none'}s (max {max_replay_age:g})",
        )
    )

    study_generated = str(study.get("generated_at_utc") or "").strip()
    replay_portfolio_version = str(replay.get("portfolio_generated_at_utc") or "").strip()
    study_stamp = parse_timestamp(study_generated)
    postdates = (
        replay_generated is not None
        and study_stamp is not None
        and replay_generated.astimezone(timezone.utc) >= study_stamp.astimezone(timezone.utc)
    )
    checks.append(
        _condition(
            "tier0_matches_current_portfolio_version",
            bool(study_generated) and replay_portfolio_version == study_generated and postdates,
            f"study_version={study_generated or 'missing'}; replay_portfolio_version="
            f"{replay_portfolio_version or 'missing'}; postdates={postdates}",
        )
    )

    primary_source = str(replay.get("primary_book_source") or "").strip().lower()
    checks.append(
        _condition(
            "tier0_primary_source_official",
            primary_source == REQUIRED_PRIMARY_BOOK_SOURCE,
            f"primary_book_source={primary_source or 'missing'}",
        )
    )

    market_row = _per_market_row(replay, condition_id=candidate_id, token_id=token_id)
    evaluable = safe_float((market_row or {}).get("last_in_queue_evaluable_opportunities"))
    confirmed = safe_float((market_row or {}).get("confirmed_fills"))
    coverage_ratio = safe_float((market_row or {}).get("coverage_ratio"))
    haircut = safe_float((market_row or {}).get("simulation_to_reality_haircut"))
    by_horizon = (market_row or {}).get("by_horizon") if isinstance((market_row or {}).get("by_horizon"), dict) else {}

    checks.append(
        _condition(
            "tier0_market_row_present",
            market_row is not None,
            f"per_market_coverage_row={'found' if market_row is not None else 'missing'}",
        )
    )
    checks.append(
        _condition(
            "tier0_min_evaluable_opportunities",
            evaluable is not None and evaluable >= min_evaluable,
            f"evaluable={evaluable if evaluable is not None else 'missing'} (min {min_evaluable:g})",
        )
    )
    checks.append(
        _condition(
            "tier0_min_confirmed_fills",
            confirmed is not None and confirmed >= min_confirmed,
            f"confirmed_fills={confirmed if confirmed is not None else 'missing'} (min {min_confirmed:g})",
        )
    )
    checks.append(
        _condition(
            "tier0_min_coverage_ratio",
            coverage_ratio is not None and coverage_ratio >= min_coverage,
            f"coverage_ratio={coverage_ratio if coverage_ratio is not None else 'missing'} (min {min_coverage:g})",
        )
    )

    horizon_windows: dict[str, Any] = {}
    horizons_ok = bool(by_horizon)
    for horizon in HORIZONS_MINUTES:
        covered = safe_float((by_horizon.get(f"{horizon}m") or {}).get("windows_covered"))
        horizon_windows[f"{horizon}m"] = covered
        if covered is None or covered < min_markouts:
            horizons_ok = False
    checks.append(
        _condition(
            "tier0_markout_windows_each_horizon",
            horizons_ok,
            f"windows_covered={horizon_windows} (min {min_markouts:g} at each of 5/15/60m)",
        )
    )
    checks.append(
        _condition(
            "tier0_adverse_haircut_within_ceiling",
            haircut is not None and haircut <= max_haircut,
            f"simulation_to_reality_haircut={haircut if haircut is not None else 'missing'} (max {max_haircut:g})",
        )
    )

    summary["tier0"] = {
        "evaluable_opportunities": evaluable,
        "confirmed_fills": confirmed,
        "coverage_ratio": coverage_ratio,
        "markout_windows_covered": horizon_windows,
        "simulation_to_reality_haircut": haircut,
    }

    qualified = bool(checks) and all(row["passed"] for row in checks)
    return qualified, checks, summary


def run_sharp_linking_evaluator(
    cfg: EngineConfig,
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    path = cfg.output_root / OUTPUT_RELATIVE
    qualified, checks, summary = evaluate_sharp_linking(cfg, as_of=as_of)
    first_failing = next((row["check"] for row in checks if not row["passed"]), None)
    payload = {
        "work_order": WORK_ORDER,
        "generated_at_utc": now_utc(),
        "qualified": qualified,
        "candidate_condition_id": summary.get("candidate_condition_id"),
        "candidate_token_id": summary.get("candidate_token_id"),
        "first_failing_check": first_failing,
        "checks": checks,
        "summary": summary,
        "registered_constants": {
            "max_anchor_age_seconds": MAX_ANCHOR_AGE_SECONDS,
            "max_anchor_disagreement": MAX_ANCHOR_DISAGREEMENT,
            "max_pm_book_age_seconds": MAX_PM_BOOK_AGE_SECONDS,
            "max_replay_age_seconds": MAX_REPLAY_AGE_SECONDS,
            "min_evaluable_opportunities": MIN_EVALUABLE_OPPORTUNITIES,
            "min_confirmed_fills": MIN_CONFIRMED_FILLS,
            "min_coverage_ratio": MIN_COVERAGE_RATIO,
            "min_markout_windows_per_horizon": MIN_MARKOUT_WINDOWS_PER_HORIZON,
            "max_adverse_haircut": MAX_ADVERSE_HAIRCUT,
            "required_primary_book_source": REQUIRED_PRIMARY_BOOK_SOURCE,
        },
        "note": (
            "Route A sharp-linking funding precondition; fail-closed and tighten-only. "
            "Reporting only - no gate, sizing, or order surface is changed by this artifact."
        ),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    write_json(path, payload)
    return payload


def main(config_path: str) -> dict[str, Any]:
    return run_sharp_linking_evaluator(load_config(config_path))
