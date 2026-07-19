"""WO-36 maker-carry actuarial study: is quoting for liquidity rewards a
credible route to $100/month?

Polymarket's live fee schedule (verified 2026-07-09) taxes takers at
rate x p x (1-p) per share while makers pay ZERO fees and earn daily
liquidity rewards under a quadratic scoring rule S(v, s) = ((v-s)/v)^2 x size,
paid at 00:00 UTC per market from a public per-market pot. That asymmetry is
the structural finding of the fee audit: the taker cost stack (~1.6-2.1c per
dollar on sports) does not exist on the maker side. This module measures -
never trades - whether the reward income minus expected adverse selection
clears the $100/month target (= $3.33/day).

Model, per candidate market (all inputs from free, key-less public APIs):

- Pot: Gamma ``clobRewards.rewardsDailyRate`` summed over live reward configs.
- Our hypothetical quote: ``rewards_min_size`` shares, both sides, at distance
  s = fraction x v from mid, where v = rewards_max_spread. The fraction is
  SWEPT per market over ``quote_distance_fractions``: tighter quotes earn
  quadratically more reward share but eat more fills, so each market has its
  own optimum - the row keeps whichever fraction maximises net carry.
- Reward share: published-v2 liquidity scoring over the market and its
  complement (bids on m + asks on m', and the opposite side), with the c=3
  single-sided rule inside the eligible mid band and strict double-sided
  scoring outside it. The old same-token min(bid, ask) share is retained as a
  one-release audit column only.
- Adverse selection: every mid move larger than s across one bar is assumed
  to fill our full quote on the wrong side at a per-share loss of |move| - s
  (reaction time = one bar). Charged as the WORSE of two windows - 24h of
  1-minute bars (microstructure noise) and 7 days of 10-minute bars (news-day
  gaps the calm last-24h window hides).
- Thin-book guard: a raw reward share above ``max_trusted_reward_share`` means
  almost nobody is resting inside the band right now (observed live on
  in-game esports books). Competition is endogenous - an empty band is a
  danger signal, not free money - so such rows are flagged untrusted and
  excluded from the portfolio.
- Markout charge (WO-36 step 2, registered 2026-07-09): bar moves approximate
  pick-offs; executed prints MEASURE them. Every real trade that swept
  through our hypothetical quote level gets a markout - where the mid stood
  ``markout_horizon_minutes`` after the fill versus our fill price - scaled
  by our queue share against the resting depth in the band. The candidate is
  charged the WORST of the bar windows and the markout estimate.
- Payout floor: Polymarket pays no reward below $1/market/day, so the sized
  portfolio only admits quotes whose gross reward clears that floor.

MAKER GATES (pre-registered 2026-07-09, before any accrued trend history;
clarified same day, also pre-history: M-A counts distinct UTC DAYS, not runs,
so re-running the study intraday can never fast-forward the clock):
  M-A carry evidence  - trusted net carry at/above target on at least
                        ``gate_min_runs_at_target`` distinct UTC days,
                        including the latest run.
    M-A.1 amendment (2026-07-18; authorization = owner merge of this
    frozen-surface PR): a UTC day counts only when its LAST published_v2 run
    met target - an intraday spike that later faded cannot bank the day.
    Closes the external-audit cherry-pick finding. Tighten-only: can only
    reduce the day count.
  M-B adverse realism - every portfolio market carries a MEASURED markout
                        charge (empirical fills, not just bar approximations).
    M-B.1 amendment (2026-07-18; authorization = owner merge of this
    frozen-surface PR): M-B additionally requires each portfolio market's OWN
    recent official-book Tier-0 last-in-queue coverage (evaluable/confirmed-
    fill/coverage/markout-window/haircut minima), read from the prior cycle's
    replay. Closes the audit finding that M-B could pass on a print-markout
    estimate with zero Tier-0 coverage (observed adverse ran 2.08x estimate).
    Tighten-only AND with the markout condition; fail-closed.
  M-C payout floor    - enforced by construction in the sizing loop.
All three passing yields ``evidence_supported_pending_human_decision`` -
never an order. Acting on the daily quote sheet is a human decision made
outside this system; the repo remains paper-only either way.

HONESTY CLAUSE (stamped into every report): simulated maker fills cannot be
verified - queue position, intra-minute jumps, and competitor reaction are
invisible - so the net-carry estimate is an UPPER BOUND for the share model
and an approximation for the pick-off charge. A YES here justifies a deeper
study and nothing else. Measurement only: no labels, no gates, no order
placement of any kind. The live-trading gates in AGENTS.md are untouched.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import re
import time
from pathlib import Path
from typing import Any

import requests

from .config import EngineConfig, load_config
from .utils import (
    normalize_external_timestamp,
    now_utc,
    parse_timestamp,
    read_csv_rows,
    read_json,
    safe_float,
    write_csv,
    write_json,
    write_text_atomic,
)

DEFAULT_GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
DEFAULT_CLOB_BASE_URL = "https://clob.polymarket.com"
DEFAULT_DATA_API_BASE_URL = "https://data-api.polymarket.com"

MAKER_GATES_REGISTERED_AT_UTC = "2026-07-09T13:00:00Z"

# M-B.1 amendment (2026-07-18) registered Tier-0 minima. These MIRROR the
# WO-105 evaluator's §2 registered thresholds so M-B and the funding evaluator
# use one consistent bar; kept as local constants to avoid an upstream->
# downstream import. Mechanically tighten-only: maxima may only shrink, minima
# may only grow (see `_mb_tighter_max`/`_mb_tighter_min`); invalid overrides
# fall back to the registered default. The replay-age bound here is coarse
# (M-B is a necessary-not-sufficient gate); exact-version + 30-min freshness is
# separately enforced at the funding decision by the evaluator.
MB_TIER0_REQUIRED_SOURCE = "official"
MB_TIER0_MAX_REPLAY_AGE_SECONDS = 26 * 3600
MB_TIER0_MIN_EVALUABLE = 30
MB_TIER0_MIN_CONFIRMED_FILLS = 10
MB_TIER0_MIN_COVERAGE_RATIO = 0.80
MB_TIER0_MIN_MARKOUT_WINDOWS = 10
MB_TIER0_MAX_HAIRCUT = 1.0
MB_TIER0_HORIZONS = (5, 15, 60)

MAKER_POLICY_DEFAULTS: dict[str, Any] = {
    "max_trusted_reward_share": 0.05,
    "max_size_multiple": 5,
    "capital_cap_usd": 500.0,
    "target_net_usd_per_day": 3.33,
    "min_daily_payout_usd": 1.0,
    "gate_min_runs_at_target": 7,
    "share_model_c": 3.0,
    "share_model_mid_band_min": 0.10,
    "share_model_mid_band_max": 0.90,
}

MAKER_GATE_REGISTRATION: list[dict[str, Any]] = [
    {
        "id": "M_A_carry_evidence",
        "registered_at_utc": MAKER_GATES_REGISTERED_AT_UTC,
        "rule": "Trusted net carry must meet the daily target on the required number of distinct UTC days, including the latest run.",
        "threshold_config_key": "gate_min_runs_at_target",
        "counting": "distinct_utc_days",
        "share_model_scope": "published_v2_only",
    },
    {
        "id": "M_B_adverse_realism",
        "registered_at_utc": MAKER_GATES_REGISTERED_AT_UTC,
        "rule": "Every portfolio market must carry a measured empirical markout charge.",
    },
    {
        "id": "M_C_payout_floor",
        "registered_at_utc": MAKER_GATES_REGISTERED_AT_UTC,
        "rule": "Every sized market must clear the venue's daily reward payout floor.",
        "threshold_config_key": "min_daily_payout_usd",
        "enforcement": "pass_by_construction",
    },
]

# WO-64 source of truth: both the human quote sheet and the generated IPS
# render these structured rules. No consumer may parse generated prose.
QUOTE_SHEET_STANDING_RULES: list[dict[str, Any]] = [
    {
        "number": 1,
        "title": "Scheduled announcement",
        "text_template": ("Never quote through a scheduled announcement; pull flagged rows at least 24h before the event and stay out until it settles."),
    },
    {
        "number": 2,
        "title": "Minimum-size start",
        "text_template": "Start at minimum size for a full reward day before any size-up.",
    },
    {
        "number": 3,
        "title": "Payout floor",
        "text_template": ("Rewards below ${min_daily_payout_usd}/market/day pay NOTHING; stay above the floor."),
    },
    {
        "number": 4,
        "title": "Fill-model breach",
        "text_template": ("If realised fills exceed the modelled band-crossing rate, stop: faster flow is beating the markout model."),
    },
    {
        "number": 5,
        "title": "Daily refresh",
        "text_template": "Re-read the sheet daily because reward pots and competition move with the calendar.",
    },
    {
        "number": 6,
        "title": "Inventory skew",
        "text_template": (
            "Once filled on one side, requote to REDUCE the position, never to add; unhedged binary inventory at "
            "resolution is a directional bet, not market making."
        ),
    },
    {
        "number": 7,
        "title": "Band discipline",
        "text_template": ("Quote only while the mid is inside [0.10, 0.90]; exit as price leaves the band and do not chase it."),
    },
    {
        "number": 8,
        "title": "Flow toxicity",
        "text_template": (
            "Do not initiate quotes where toxicity_score > 0.9. This conditions human action only; the registered "
            "study charge is unchanged absent a later dated tightening."
        ),
    },
    {
        "number": 9,
        "title": "Resolution risk",
        "text_template": (
            "Only quote markets with objective, verifiable resolution sources and no open clarifications; exit "
            "immediately if a proposal on a held market is disputed."
        ),
    },
]

# Markets whose question suggests scheduled binary announcements: quoting
# through the event is the classic maker blow-up, so the quote sheet flags it.
EVENT_RISK_KEYWORDS = (
    "fed",
    "rate",
    "cpi",
    "meeting",
    "announce",
    "decision",
    "election",
    "vote",
    "jobs report",
    "opec",
    "earnings",
)

RESOLUTION_HIGH_KEYWORDS = (
    "announce",
    "announcement",
    "officially",
    "deal",
    "agreement",
    "ceasefire",
    "blockade",
    "considers",
    "attempts",
    "intends",
    "meeting",
    "talks",
)
RESOLUTION_CORPUS_SAMPLE_FLOOR = 50
RESOLUTION_MIN_CLEAN_SHARE = 0.90

STALE_RESOLUTION_STATUSES = {"proposed", "disputed"}
_MONTH_NUMBERS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
_MONTH_PATTERN = "|".join(sorted(_MONTH_NUMBERS, key=len, reverse=True))
_TITLE_MONTH_DAY_RE = re.compile(
    rf"\b(?P<month>{_MONTH_PATTERN})\.?\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(?P<year>20\d{{2}}))?\b",
    re.IGNORECASE,
)
_TITLE_DAY_MONTH_RE = re.compile(
    rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<month>{_MONTH_PATTERN})\.?(?:,?\s+(?P<year>20\d{{2}}))?\b",
    re.IGNORECASE,
)
_TITLE_ISO_DATE_RE = re.compile(r"\b(?P<year>20\d{2})-(?P<month>\d{1,2})-(?P<day>\d{1,2})\b")

CANDIDATE_FIELDS = [
    "question",
    "market_id",
    "condition_id",
    "market_slug",
    "event_slug",
    "market_url",
    "token_id",
    "complement_token_id",
    "outcome",
    "complement_outcome",
    "end_date_utc",
    "event_start_time_utc",
    "uma_resolution_status",
    "order_price_min_tick_size",
    "neg_risk",
    "fee_type",
    "fees_enabled",
    "volume_24h_usd",
    "pot_usd_per_day",
    "pot_rank",
    "yield_rank",
    "expected_gross_at_min_size",
    "rewards_min_size_shares",
    "rewards_max_spread_cents",
    "mid_price",
    "best_bid",
    "best_ask",
    "quote_bid_price",
    "quote_ask_price",
    "quote_distance",
    "quote_distance_fraction",
    "competitor_score_bid",
    "competitor_score_ask",
    "our_score_per_side",
    "share_model",
    "share_model_legacy",
    "band_eligible",
    "estimated_reward_share",
    "gross_reward_usd_per_day",
    "history_points",
    "pickoff_events_per_day",
    "adverse_usd_per_day_1min_24h",
    "adverse_usd_per_day_10min_7d",
    "adverse_usd_per_day_markout",
    "band_crossing_prints_per_day",
    "markout_measured",
    "adverse_selection_usd_per_day",
    "supplementary_rebate_usd_per_day",
    "supplementary_holding_usd_per_day",
    "supplementary_income_usd_per_day",
    "resolution_risk",
    "resolution_risk_class",
    "resolution_risk_reason",
    "resolution_risk_sample_markets",
    "resolution_risk_clean_share",
    "net_carry_usd_per_day",
    "capital_usd",
    "estimate_quality",
]


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = cfg.raw.get("maker_carry_study", {}) if isinstance(cfg.raw.get("maker_carry_study"), dict) else {}
    merged = {
        "enabled": True,
        "gamma_base_url": DEFAULT_GAMMA_BASE_URL,
        "clob_base_url": DEFAULT_CLOB_BASE_URL,
        "data_api_base_url": DEFAULT_DATA_API_BASE_URL,
        "universe_pages": 5,
        "page_size": 100,
        "min_daily_pot_usd": 25.0,
        "max_book_candidates": 20,
        # 2026-07-11 WO-56: widen coverage by pre-screening rewarded
        # markets by achievable gross at min quote size, then run the
        # expensive history/markout study on the yield-ranked shortlist.
        "yield_scan_max_markets": 200,
        # Distances to sweep per market (fractions of the qualifying band):
        # tighter earns quadratically more share, wider survives more noise.
        "quote_distance_fractions": [0.25, 0.5, 0.75],
        "reaction_minutes": 1,
        # A raw share above this means the band is nearly empty of resting
        # competition (seen live on in-game esports books): untrusted.
        "max_trusted_reward_share": 0.05,
        # Net carry is ~linear in quote size while our share is small, so the
        # capital-constrained portfolio sizes quotes up - but never beyond
        # this multiple of rewards_min_size, past which the full-size-fill
        # pick-off model stops being conservative.
        "max_size_multiple": 5,
        "capital_cap_usd": 500.0,
        "target_net_usd_per_day": 3.33,
        # 2026-07-11 WO-57: supplementary planning curve only. The
        # registered maker metric continues to use capital_cap_usd above.
        "capital_curve_enabled": True,
        "capital_curve_caps_usd": [250, 500, 1000, 2000, 5000],
        # Markout (empirical adverse selection from executed prints).
        "markout_horizon_minutes": 5,
        "markout_min_prints": 20,
        "prints_limit": 500,
        # Polymarket pays no reward accrual below this per market per day.
        "min_daily_payout_usd": 1.0,
        # Gate M-A: daily runs at/above target required for the maker verdict.
        "gate_min_runs_at_target": 7,
        # WO-45 supplementary maker income (reporting only, excluded from all
        # registered M-gates and portfolio net-carry calculations).
        "fee_rate_by_type": {"sports_fees_v2": 0.05},
        "fee_rate_when_enabled_unknown": 0.07,
        "maker_rebate_share_by_fee_type": {"sports_fees_v2": 0.15},
        "maker_rebate_share_when_enabled_unknown": 0.25,
        "holding_reward_apr": 0.04,
        "request_timeout_seconds": 20,
        "request_pause_seconds": 0.15,
        "share_model_c": 3.0,
        "share_model_mid_band_min": 0.10,
        "share_model_mid_band_max": 0.90,
    }
    # Policy-bearing defaults come from one structured source so the generated
    # IPS and the enforced study cannot drift apart.
    merged.update(MAKER_POLICY_DEFAULTS)
    merged.update({k: v for k, v in raw.items() if v is not None})
    return merged


def maker_policy_settings(cfg: EngineConfig) -> dict[str, Any]:
    settings = _settings(cfg)
    return {key: settings[key] for key in MAKER_POLICY_DEFAULTS}


def maker_gate_registration(gate_id: str) -> dict[str, Any]:
    return next(row for row in MAKER_GATE_REGISTRATION if row["id"] == gate_id)


def quote_sheet_standing_rules(settings: dict[str, Any]) -> list[dict[str, Any]]:
    values = {"min_daily_payout_usd": settings.get("min_daily_payout_usd", 1.0)}
    return [
        {
            "number": int(rule["number"]),
            "title": str(rule["title"]),
            "text": str(rule["text_template"]).format(**values),
        }
        for rule in QUOTE_SHEET_STANDING_RULES
    ]


def _live_pot_usd(market: dict[str, Any], today: str) -> float:
    total = 0.0
    for reward in market.get("clobRewards") or []:
        if not isinstance(reward, dict):
            continue
        rate = safe_float(reward.get("rewardsDailyRate")) or 0.0
        end_date = str(reward.get("endDate") or "")
        if rate > 0 and (not end_date or end_date >= today):
            total += rate
    return total


def _inferred_title_date(month: int, day: int, *, as_of: datetime, close_at: datetime | None) -> date | None:
    """Resolve a title date without a year against the closest sensible year.

    Polymarket questions commonly say ``on July 9`` without a year.  The
    venue close timestamp is the strongest year hint; otherwise choose the
    date nearest to the current UTC date so a December scan does not reject a
    forthcoming January market as eleven months old.
    """

    reference = (close_at or as_of).date()
    candidates: list[date] = []
    for year in (reference.year - 1, reference.year, reference.year + 1):
        try:
            candidates.append(date(year, month, day))
        except ValueError:
            continue
    return min(candidates, key=lambda value: abs((value - reference).days)) if candidates else None


def _title_dates(question: str, *, as_of: datetime, close_at: datetime | None) -> list[date]:
    dates: set[date] = set()
    occupied: list[tuple[int, int]] = []
    for match in _TITLE_ISO_DATE_RE.finditer(str(question or "")):
        try:
            dates.add(date(int(match.group("year")), int(match.group("month")), int(match.group("day"))))
            occupied.append(match.span())
        except ValueError:
            continue

    for pattern in (_TITLE_MONTH_DAY_RE, _TITLE_DAY_MONTH_RE):
        for match in pattern.finditer(str(question or "")):
            if any(start <= match.start() < end for start, end in occupied):
                continue
            month = _MONTH_NUMBERS[match.group("month").lower().rstrip(".")]
            day = int(match.group("day"))
            year_text = match.group("year")
            try:
                parsed = (
                    date(int(year_text), month, day)
                    if year_text
                    else _inferred_title_date(month, day, as_of=as_of, close_at=close_at)
                )
            except ValueError:
                parsed = None
            if parsed is not None:
                dates.add(parsed)
    return sorted(dates)


def _candidate_staleness_reasons(market: dict[str, Any], *, as_of: datetime) -> list[str]:
    """Return tighten-only WO-80 exclusion reasons for a rewarded market."""

    as_of = as_of.astimezone(timezone.utc)
    close_seconds = normalize_external_timestamp(market.get("endDateIso") or market.get("endDate"))
    close_at = datetime.fromtimestamp(close_seconds, timezone.utc) if close_seconds is not None else None

    reasons: list[str] = []
    if close_at is not None and close_at < as_of:
        reasons.append("venue_close_time_past")

    title_dates = _title_dates(str(market.get("question") or ""), as_of=as_of, close_at=close_at)
    # A range remains live through its latest explicit title date.  This
    # still rejects the observed single-day July 9 market on July 13 while
    # avoiding false rejection of an in-progress July 9-20 window.
    if title_dates and max(title_dates) < as_of.date():
        reasons.append("title_date_past")

    resolution_status = str(
        market.get("umaResolutionStatus") or market.get("uma_resolution_status") or ""
    ).strip().lower()
    if resolution_status in STALE_RESOLUTION_STATUSES:
        reasons.append(f"resolution_{resolution_status}")
    return reasons


def _word_in(text: str, word: str) -> bool:
    return bool(re.search(rf"\b{re.escape(word)}\b", text))


def _base_resolution_class(question: str) -> dict[str, str]:
    """Classify objective resolution risk from the market wording alone.

    The order is intentional: specific objective classes win before broad
    subjective/high-risk words such as "meeting" or "decision" fire. Corpus
    evidence may later escalate LOW to MEDIUM, never downgrade it.
    """

    text = re.sub(r"[^a-z0-9$%.]+", " ", str(question or "").lower()).strip()
    if ("fed" in text or "fomc" in text or "federal reserve" in text) and any(
        token in text for token in ("rate", "interest", "hike", "cut", "hold", "no change", "decision")
    ):
        return {
            "resolution_risk": "low",
            "resolution_risk_class": "fed_rate_decision",
            "resolution_risk_reason": "objective Fed/rate decision wording",
        }
    if any(token in text for token in ("match", "game")) and any(token in text for token in ("winner", "win", "beat", "defeat")):
        return {
            "resolution_risk": "low",
            "resolution_risk_class": "match_game_winner",
            "resolution_risk_reason": "objective match/game winner wording",
        }
    if (
        any(token in text for token in ("above", "below", "over", "under"))
        and re.search(r"\d", text)
        and any(token in text for token in ("close", "closing", "end", "finish", "price"))
    ):
        return {
            "resolution_risk": "low",
            "resolution_risk_class": "numeric_close_above_below",
            "resolution_risk_reason": "objective numeric close above/below wording",
        }
    if "election" in text and any(token in text for token in ("result", "winner", "win")):
        return {
            "resolution_risk": "low",
            "resolution_risk_class": "official_election_result",
            "resolution_risk_reason": "objective election result wording",
        }
    for keyword in RESOLUTION_HIGH_KEYWORDS:
        if _word_in(text, keyword):
            return {
                "resolution_risk": "high",
                "resolution_risk_class": f"subjective_{keyword}",
                "resolution_risk_reason": f"subjective/dispute-prone wording contains '{keyword}'",
            }
    return {
        "resolution_risk": "medium",
        "resolution_risk_class": "other",
        "resolution_risk_reason": "no pre-registered low/high resolution-risk keyword class matched",
    }


def _resolution_quality_class_stats(cfg: EngineConfig) -> dict[str, dict[str, float]]:
    rows = read_csv_rows(cfg.governance_root / "resolution_quality_report.csv")
    by_class: dict[str, dict[str, float]] = {}
    seen: set[str] = set()
    for row in rows:
        quality = str(row.get("resolution_quality") or "").strip().lower()
        if not quality or quality == "unresolved_active":
            continue
        text = str(row.get("question") or row.get("market_slug") or row.get("condition_id") or "")
        if not text:
            continue
        key = str(row.get("market_slug") or row.get("condition_id") or text)
        if key in seen:
            continue
        seen.add(key)
        class_name = _base_resolution_class(text)["resolution_risk_class"]
        bucket = by_class.setdefault(class_name, {"markets": 0.0, "clean": 0.0})
        bucket["markets"] += 1
        if quality == "clean_settlement":
            bucket["clean"] += 1
    for bucket in by_class.values():
        markets = max(bucket["markets"], 1.0)
        bucket["clean_share"] = bucket["clean"] / markets
    return by_class


def _resolution_risk_for_question(
    question: str,
    class_stats: dict[str, dict[str, float]] | None = None,
) -> dict[str, Any]:
    base = _base_resolution_class(question)
    stats = (class_stats or {}).get(str(base["resolution_risk_class"]), {})
    sample = int(stats.get("markets", 0) or 0)
    clean_share = stats.get("clean_share")
    if (
        base["resolution_risk"] == "low"
        and sample >= RESOLUTION_CORPUS_SAMPLE_FLOOR
        and clean_share is not None
        and float(clean_share) < RESOLUTION_MIN_CLEAN_SHARE
    ):
        base = {
            **base,
            "resolution_risk": "medium",
            "resolution_risk_reason": (
                f"{base['resolution_risk_reason']}; escalated by corpus clean-settlement share {float(clean_share):.3f} over {sample} markets"
            ),
        }
    return {
        **base,
        "resolution_risk_sample_markets": sample,
        "resolution_risk_clean_share": "" if clean_share is None else round(float(clean_share), 4),
    }


def _token_ids(market: dict[str, Any]) -> list[str]:
    raw = market.get("clobTokenIds")
    tokens: list[Any]
    if isinstance(raw, str):
        try:
            tokens = json.loads(raw)
        except (ValueError, TypeError):
            return []
    elif isinstance(raw, list):
        tokens = raw
    else:
        return []
    return [str(token).strip() for token in tokens if str(token).strip()]


def _outcomes(market: dict[str, Any]) -> list[str]:
    raw = market.get("outcomes")
    if isinstance(raw, str):
        try:
            values = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    elif isinstance(raw, list):
        values = raw
    else:
        return []
    return [str(value).strip() for value in values if str(value).strip()]


def _event_slug(market: dict[str, Any]) -> str:
    direct = str(market.get("eventSlug") or "").strip()
    if direct:
        return direct
    events = market.get("events") if isinstance(market.get("events"), list) else []
    for event in events:
        if isinstance(event, dict) and str(event.get("slug") or "").strip():
            return str(event["slug"]).strip()
    return ""


def _market_url(market: dict[str, Any]) -> str:
    event_slug = _event_slug(market)
    market_slug = str(market.get("slug") or "").strip()
    if event_slug:
        return f"https://polymarket.com/event/{event_slug}"
    if market_slug:
        return f"https://polymarket.com/market/{market_slug}"
    return ""


def _quote_prices(
    mid: float,
    distance: float,
    tick_size: float | None,
) -> tuple[float | None, float | None]:
    """Round away from the mid so a human ticket never quotes tighter."""

    if tick_size is None or not 0 < tick_size < 1:
        return None, None
    tick = Decimal(str(tick_size))
    midpoint = Decimal(str(mid))
    offset = Decimal(str(distance))
    bid = ((midpoint - offset) / tick).to_integral_value(rounding=ROUND_FLOOR) * tick
    ask = ((midpoint + offset) / tick).to_integral_value(rounding=ROUND_CEILING) * tick
    if bid <= 0 or ask >= 1 or bid >= ask:
        return None, None
    decimals = max(0, -tick.normalize().as_tuple().exponent)
    return round(float(bid), decimals), round(float(ask), decimals)


def _first_token_id(market: dict[str, Any]) -> str:
    tokens = _token_ids(market)
    return tokens[0] if tokens else ""


def _rewarded_universe(
    settings: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    """Active markets carrying a live maker-reward pot, largest pots first."""
    base = str(settings["gamma_base_url"]).rstrip("/")
    timeout = float(settings["request_timeout_seconds"])
    pause = float(settings["request_pause_seconds"])
    observed_at = now_utc()
    today = observed_at[:10]
    as_of = parse_timestamp(observed_at) or datetime.now(timezone.utc)
    universe: list[dict[str, Any]] = []
    errors: list[str] = []
    excluded_stale = 0
    excluded_by_reason: dict[str, int] = {}
    excluded_examples: list[dict[str, Any]] = []
    for page in range(int(settings["universe_pages"])):
        try:
            response = requests.get(
                f"{base}/markets",
                params={
                    "closed": "false",
                    "order": "volume24hr",
                    "ascending": "false",
                    "limit": int(settings["page_size"]),
                    "offset": page * int(settings["page_size"]),
                },
                timeout=timeout,
            )
            response.raise_for_status()
            markets = response.json()
        except Exception as exc:
            errors.append(f"gamma page {page}: {type(exc).__name__}: {exc}")
            continue
        if not isinstance(markets, list) or not markets:
            break
        for market in markets:
            if not isinstance(market, dict):
                continue
            pot = _live_pot_usd(market, today)
            min_size = safe_float(market.get("rewardsMinSize")) or 0.0
            max_spread_cents = safe_float(market.get("rewardsMaxSpread")) or 0.0
            token_ids = _token_ids(market)
            outcomes = _outcomes(market)
            token_id = token_ids[0] if token_ids else ""
            complement_token_id = token_ids[1] if len(token_ids) > 1 else ""
            if pot < float(settings["min_daily_pot_usd"]) or min_size <= 0 or max_spread_cents <= 0 or not token_id:
                continue
            stale_reasons = _candidate_staleness_reasons(market, as_of=as_of)
            if stale_reasons:
                excluded_stale += 1
                for reason in stale_reasons:
                    excluded_by_reason[reason] = excluded_by_reason.get(reason, 0) + 1
                if len(excluded_examples) < 10:
                    excluded_examples.append(
                        {
                            "market_id": str(market.get("id") or "").strip(),
                            "question": str(market.get("question") or "").strip(),
                            "reasons": stale_reasons,
                        }
                    )
                continue
            universe.append(
                {
                    "question": str(market.get("question") or "").strip(),
                    "market_id": str(market.get("id") or "").strip(),
                    "condition_id": str(market.get("conditionId") or "").strip(),
                    "market_slug": str(market.get("slug") or "").strip(),
                    "event_slug": _event_slug(market),
                    "market_url": _market_url(market),
                    "token_id": token_id,
                    "complement_token_id": complement_token_id,
                    "outcome": outcomes[0] if outcomes else "",
                    "complement_outcome": outcomes[1] if len(outcomes) > 1 else "",
                    "end_date_utc": str(
                        market.get("endDateIso") or market.get("endDate") or ""
                    ).strip(),
                    "event_start_time_utc": str(
                        market.get("eventStartTime") or market.get("gameStartTime") or ""
                    ).strip(),
                    "uma_resolution_status": str(
                        market.get("umaResolutionStatus") or ""
                    ).strip().lower(),
                    "order_price_min_tick_size": safe_float(
                        market.get("orderPriceMinTickSize")
                    ),
                    "neg_risk": bool(market.get("negRisk")),
                    "fee_type": str(market.get("feeType") or "").strip(),
                    "fees_enabled": bool(market.get("feesEnabled")),
                    "volume_24h_usd": round(safe_float(market.get("volume24hr")) or 0.0, 2),
                    "pot_usd_per_day": round(pot, 2),
                    "rewards_min_size_shares": min_size,
                    "rewards_max_spread_cents": max_spread_cents,
                }
            )
        if pause:
            time.sleep(pause)
    universe.sort(key=lambda row: row["pot_usd_per_day"], reverse=True)
    for rank, row in enumerate(universe, start=1):
        row["pot_rank"] = rank
        row["yield_rank"] = ""
        row["expected_gross_at_min_size"] = ""
    return universe, errors, {
        "excluded_stale": excluded_stale,
        "excluded_stale_by_reason": excluded_by_reason,
        "excluded_stale_examples": excluded_examples,
    }


def _quadratic_score(distance: float, v: float, size: float) -> float:
    if v <= 0 or distance < 0 or distance > v:
        return 0.0
    return ((v - distance) / v) ** 2 * size


def _best_bid_ask(book: dict[str, Any]) -> tuple[float | None, float | None]:
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    bid_prices = [price for level in bids if (price := safe_float(level.get("price"))) is not None]
    ask_prices = [price for level in asks if (price := safe_float(level.get("price"))) is not None]
    return (max(bid_prices) if bid_prices else None, min(ask_prices) if ask_prices else None)


def _book_levels_score(
    levels: list[dict[str, Any]],
    *,
    mid: float,
    v: float,
) -> tuple[float, float]:
    score = 0.0
    depth = 0.0
    for level in levels:
        price = safe_float(level.get("price"))
        size = safe_float(level.get("size"))
        if price is None or size is None:
            continue
        distance = abs(mid - price)
        score += _quadratic_score(distance, v, size)
        if distance <= v:
            depth += size
    return score, depth


def _published_q_score(q_one: float, q_two: float, *, mid: float, settings: dict[str, Any]) -> float:
    band_min = float(settings["share_model_mid_band_min"])
    band_max = float(settings["share_model_mid_band_max"])
    if not (band_min <= mid <= band_max):
        return min(q_one, q_two)
    c = max(float(settings["share_model_c"]), 1.0)
    return max(min(q_one, q_two), max(q_one, q_two) / c)


def _books_from_payload(payload: Any, token_ids: list[str]) -> dict[str, dict[str, Any]]:
    books: dict[str, dict[str, Any]] = {}
    rows: list[Any]
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        for key in ("books", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                rows = value
                break
        else:
            rows = [payload]
    else:
        rows = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        token = str(row.get("asset_id") or row.get("token_id") or row.get("market") or "").strip()
        if not token and index < len(token_ids):
            token = token_ids[index]
        if token:
            books[token] = row
    return books


def _fetch_books(settings: dict[str, Any], token_ids: list[str]) -> dict[str, dict[str, Any]]:
    base = str(settings["clob_base_url"]).rstrip("/")
    timeout = float(settings["request_timeout_seconds"])
    token_ids = [token for token in token_ids if token]
    books: dict[str, dict[str, Any]] = {}
    if len(token_ids) >= 2:
        try:
            response = requests.post(
                f"{base}/books",
                json=[{"token_id": token} for token in token_ids],
                timeout=timeout,
            )
            response.raise_for_status()
            books.update(_books_from_payload(response.json(), token_ids))
        except Exception:
            books = {}
    pause = float(settings.get("request_pause_seconds") or 0.0)
    missing = [token for token in token_ids if token not in books]
    for index, token in enumerate(missing):
        try:
            response = requests.get(f"{base}/book", params={"token_id": token}, timeout=timeout)
            response.raise_for_status()
            books[token] = response.json()
        except Exception:
            pass
        if pause and index < len(missing) - 1:
            time.sleep(max(pause, 0.1))
    return books


def _book_competition_from_books(
    settings: dict[str, Any],
    token_id: str,
    complement_token_id: str,
    v: float,
    books: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Published-v2 reward score for the market + complement book pair."""
    book = books.get(token_id)
    complement = books.get(complement_token_id) if complement_token_id else None
    if not book:
        return None
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    if not bids or not asks:
        return None
    best_bid, best_ask = _best_bid_ask(book)
    if best_bid is None or best_ask is None or best_ask <= best_bid:
        return None
    mid = (best_bid + best_ask) / 2

    yes_bid_score, bid_depth = _book_levels_score(bids, mid=mid, v=v)
    yes_ask_score, ask_depth = _book_levels_score(asks, mid=mid, v=v)
    complement_bid_score = 0.0
    complement_ask_score = 0.0
    if complement:
        comp_bids = complement.get("bids") or []
        comp_asks = complement.get("asks") or []
        # Complement token prices are converted to the YES probability axis.
        complement_ask_score, _ = _book_levels_score(
            [{"price": 1.0 - (safe_float(level.get("price")) or 0.0), "size": level.get("size")} for level in comp_asks],
            mid=mid,
            v=v,
        )
        complement_bid_score, _ = _book_levels_score(
            [{"price": 1.0 - (safe_float(level.get("price")) or 0.0), "size": level.get("size")} for level in comp_bids],
            mid=mid,
            v=v,
        )
    q_one = yes_bid_score + complement_ask_score
    q_two = yes_ask_score + complement_bid_score
    published_pool = _published_q_score(q_one, q_two, mid=mid, settings=settings)
    legacy_pool = min(yes_bid_score, yes_ask_score)
    band_min = float(settings["share_model_mid_band_min"])
    band_max = float(settings["share_model_mid_band_max"])
    return {
        "mid": mid,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "tick_size": safe_float(book.get("tick_size")),
        "bid_score": q_one,
        "ask_score": q_two,
        "legacy_bid_score": yes_bid_score,
        "legacy_ask_score": yes_ask_score,
        "published_pool_score": published_pool,
        "legacy_pool_score": legacy_pool,
        "band_eligible": band_min <= mid <= band_max,
        "bid_depth": bid_depth,
        "ask_depth": ask_depth,
    }


def _book_competition(settings: dict[str, Any], token_id: str, complement_token_id: str, v: float) -> dict[str, Any] | None:
    token_ids = [token_id]
    if complement_token_id:
        token_ids.append(complement_token_id)
    books = _fetch_books(settings, token_ids)
    return _book_competition_from_books(settings, token_id, complement_token_id, v, books)


def _share_from_published_score(
    settings: dict[str, Any],
    competition: dict[str, Any],
    our_score: float,
) -> tuple[float, float]:
    q_one = float(competition["bid_score"])
    q_two = float(competition["ask_score"])
    mid = float(competition["mid"])
    pool = float(competition["published_pool_score"])
    total = _published_q_score(q_one + our_score, q_two + our_score, mid=mid, settings=settings)
    marginal = max(0.0, total - pool)
    share = marginal / total if total > 0 else 0.0
    return share, marginal


def _legacy_share(competition: dict[str, Any], our_score: float) -> float:
    pool = float(competition.get("legacy_pool_score") or 0.0)
    return our_score / (our_score + pool) if (our_score + pool) > 0 else 0.0


def _yield_first_shortlist(
    settings: dict[str, Any],
    universe: list[dict[str, Any]],
    fractions: list[float],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    max_candidates = int(settings["max_book_candidates"])
    max_scan = max(max_candidates, int(settings.get("yield_scan_max_markets") or max_candidates))
    scan_universe = universe[:max_scan]
    if not scan_universe:
        return [], {
            "universe_scan_mode": "yield_first_v1",
            "yield_scan_considered_markets": 0,
            "yield_scan_scored_markets": 0,
            "yield_scan_selected_markets": 0,
            "yield_scan_fallback": False,
        }

    token_ids: list[str] = []
    for market in scan_universe:
        for token in (market.get("token_id"), market.get("complement_token_id")):
            token = str(token or "").strip()
            if token and token not in token_ids:
                token_ids.append(token)

    try:
        books = _fetch_books(settings, token_ids)
        scored: list[tuple[float, dict[str, Any]]] = []
        for market in scan_universe:
            v = float(market["rewards_max_spread_cents"]) / 100.0
            quote_size = float(market["rewards_min_size_shares"])
            competition = _book_competition_from_books(
                settings,
                str(market["token_id"]),
                str(market.get("complement_token_id") or ""),
                v,
                books,
            )
            if competition is None:
                continue
            best_gross = 0.0
            for fraction in fractions:
                quote_distance = fraction * v
                raw_our_score = _quadratic_score(quote_distance, v, quote_size)
                if raw_our_score <= 0:
                    continue
                share, _ = _share_from_published_score(settings, competition, raw_our_score)
                best_gross = max(best_gross, float(market["pot_usd_per_day"]) * share)
            if best_gross > 0:
                scored.append((best_gross, market))
    except Exception as exc:  # noqa: BLE001 - prescreen must fail soft to registered pot ranking
        selected = [dict(row) for row in universe[:max_candidates]]
        return selected, {
            "universe_scan_mode": "pot_rank_fallback",
            "yield_scan_considered_markets": len(scan_universe),
            "yield_scan_scored_markets": 0,
            "yield_scan_selected_markets": len(selected),
            "yield_scan_fallback": True,
            "yield_scan_error": f"{type(exc).__name__}: {exc}",
        }

    if not scored:
        selected = [dict(row) for row in universe[:max_candidates]]
        return selected, {
            "universe_scan_mode": "pot_rank_fallback",
            "yield_scan_considered_markets": len(scan_universe),
            "yield_scan_scored_markets": 0,
            "yield_scan_selected_markets": len(selected),
            "yield_scan_fallback": True,
            "yield_scan_error": "no prescreen books scored",
        }

    scored.sort(key=lambda item: item[0], reverse=True)
    selected: list[dict[str, Any]] = []
    selected_keys: set[str] = set()
    for yield_rank, (gross, market) in enumerate(scored, start=1):
        if len(selected) >= max_candidates:
            break
        row = dict(market)
        row["yield_rank"] = yield_rank
        row["expected_gross_at_min_size"] = round(gross, 4)
        selected.append(row)
        selected_keys.add(str(row.get("condition_id") or row.get("token_id") or ""))

    for market in universe:
        if len(selected) >= max_candidates:
            break
        key = str(market.get("condition_id") or market.get("token_id") or "")
        if key not in selected_keys:
            selected.append(dict(market))
            selected_keys.add(key)

    return selected, {
        "universe_scan_mode": "yield_first_v1",
        "yield_scan_considered_markets": len(scan_universe),
        "yield_scan_scored_markets": len(scored),
        "yield_scan_selected_markets": len(selected),
        "yield_scan_fallback": False,
    }


def _legacy_book_competition(settings: dict[str, Any], token_id: str, v: float) -> dict[str, Any] | None:
    """Kept for one-release audit continuity; not used for the live estimate."""
    try:
        response = requests.get(
            f"{str(settings['clob_base_url']).rstrip('/')}/book",
            params={"token_id": token_id},
            timeout=float(settings["request_timeout_seconds"]),
        )
        response.raise_for_status()
        book = response.json()
    except Exception:
        return None
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    if not bids or not asks:
        return None
    best_bid = safe_float(bids[-1].get("price"))
    best_ask = safe_float(asks[-1].get("price"))
    if best_bid is None or best_ask is None or best_ask <= best_bid:
        return None
    mid = (best_bid + best_ask) / 2
    scores = {"bid": 0.0, "ask": 0.0}
    depth = {"bid": 0.0, "ask": 0.0}
    for side, levels in (("bid", bids), ("ask", asks)):
        for level in levels:
            price = safe_float(level.get("price"))
            size = safe_float(level.get("size"))
            if price is None or size is None:
                continue
            distance = abs(mid - price)
            scores[side] += _quadratic_score(distance, v, size)
            if distance <= v:
                depth[side] += size
    return {
        "mid": mid,
        "bid_score": scores["bid"],
        "ask_score": scores["ask"],
        "bid_depth": depth["bid"],
        "ask_depth": depth["ask"],
    }


def _market_fee_rate(row: dict[str, Any], settings: dict[str, Any]) -> float:
    if not row.get("fees_enabled"):
        return 0.0
    by_type = settings.get("fee_rate_by_type") or {}
    fee_type = str(row.get("fee_type") or "")
    rate = safe_float(by_type.get(fee_type))
    if rate is not None:
        return rate
    return float(settings["fee_rate_when_enabled_unknown"])


def _maker_rebate_share(row: dict[str, Any], settings: dict[str, Any]) -> float:
    if not row.get("fees_enabled"):
        return 0.0
    by_type = settings.get("maker_rebate_share_by_fee_type") or {}
    fee_type = str(row.get("fee_type") or "")
    share = safe_float(by_type.get(fee_type))
    if share is not None:
        return share
    return float(settings["maker_rebate_share_when_enabled_unknown"])


def _supplementary_income(row: dict[str, Any], settings: dict[str, Any], depth: dict[str, float]) -> dict[str, float]:
    """Uncounted maker income streams from rebates + holding rewards.

    These numbers are deliberately excluded from `net_carry_usd_per_day` and
    every registered maker gate. They exist so the funding decision can see
    the full income picture without silently loosening the evidence rules.
    """
    capital = safe_float(row.get("capital_usd")) or 0.0
    quote_size = safe_float(row.get("rewards_min_size_shares")) or 0.0
    mid = safe_float(row.get("mid_price")) or 0.0
    crossings = safe_float(row.get("band_crossing_prints_per_day")) or 0.0
    bid_queue_share = quote_size / (quote_size + max(depth.get("bid", 0.0), 0.0)) if quote_size > 0 else 0.0
    ask_queue_share = quote_size / (quote_size + max(depth.get("ask", 0.0), 0.0)) if quote_size > 0 else 0.0
    expected_fills_per_day = crossings * ((bid_queue_share + ask_queue_share) / 2.0)
    fee_equivalent_per_fill = quote_size * _market_fee_rate(row, settings) * mid * (1.0 - mid)
    rebate = expected_fills_per_day * fee_equivalent_per_fill * _maker_rebate_share(row, settings)
    holding = (float(settings["holding_reward_apr"]) / 365.0) * capital
    return {
        "supplementary_rebate_usd_per_day": round(rebate, 6),
        "supplementary_holding_usd_per_day": round(holding, 6),
        "supplementary_income_usd_per_day": round(rebate + holding, 6),
    }


def _price_series(settings: dict[str, Any], token_id: str, *, interval: str, fidelity_minutes: int) -> list[tuple[float, float]] | None:
    """(unix seconds, price) mid series from the public prices-history feed."""
    try:
        response = requests.get(
            f"{str(settings['clob_base_url']).rstrip('/')}/prices-history",
            params={"market": token_id, "interval": interval, "fidelity": fidelity_minutes},
            timeout=float(settings["request_timeout_seconds"]),
        )
        response.raise_for_status()
        history = response.json().get("history") or []
    except Exception:
        return None
    series: list[tuple[float, float]] = []
    for point in history:
        if not isinstance(point, dict):
            continue
        stamp = normalize_external_timestamp(point.get("t"))
        price = safe_float(point.get("p"))
        if stamp is not None and price is not None:
            series.append((stamp, price))
    series.sort(key=lambda item: item[0])
    return series


def _mid_at(series: list[tuple[float, float]], stamp: float, tolerance_seconds: float) -> float | None:
    """Nearest mid at/just before ``stamp`` within tolerance (series sorted)."""
    from bisect import bisect_right

    index = bisect_right(series, (stamp, float("inf"))) - 1
    if index < 0:
        return None
    point_stamp, price = series[index]
    return price if stamp - point_stamp <= tolerance_seconds else None


def _pickoff_from_series(
    series: list[tuple[float, float]] | None,
    quote_distance: float,
    quote_size: float,
    *,
    fidelity_minutes: int,
    min_points: int,
) -> dict[str, float] | None:
    """Pick-off charge per day from one price-history window.

    Every |move| > quote_distance across one bar is assumed to trade through
    our full resting size on the wrong side; the per-share loss is the move's
    excess over our distance from mid. Bars hide faster jumps, so this is an
    approximation, not a floor.
    """
    if series is None or len(series) < min_points:
        return None
    prices = [price for _, price in series]
    events = 0
    loss_usd = 0.0
    for previous, current in zip(prices, prices[1:]):
        excess = abs(current - previous) - quote_distance
        if excess > 0:
            events += 1
            loss_usd += excess * quote_size
    span_days = max((len(prices) - 1) * fidelity_minutes / 1440.0, 1e-9)
    return {
        "history_points": len(prices),
        "pickoff_events_per_day": round(events / span_days, 2),
        "adverse_usd_per_day": round(loss_usd / span_days, 4),
    }


def _recent_prints(settings: dict[str, Any], condition_id: str) -> list[dict[str, float]]:
    """Executed trades (price/size/aggressor side/stamp) from the data-API."""
    try:
        response = requests.get(
            f"{str(settings['data_api_base_url']).rstrip('/')}/trades",
            params={"market": condition_id, "limit": int(settings["prints_limit"])},
            timeout=float(settings["request_timeout_seconds"]),
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return []
    trades = payload if isinstance(payload, list) else payload.get("trades") or payload.get("data") or []
    prints: list[dict[str, float]] = []
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        price = safe_float(trade.get("price"))
        size = safe_float(trade.get("size"))
        stamp = normalize_external_timestamp(trade.get("timestamp") or trade.get("matchTime"))
        side = str(trade.get("side") or "").upper()
        if price is None or size is None or stamp is None or side not in {"BUY", "SELL"}:
            continue
        prints.append({"price": price, "size": size, "stamp": stamp, "side": side})
    return prints


def _markout_adverse(
    settings: dict[str, Any],
    prints: list[dict[str, float]],
    series: list[tuple[float, float]] | None,
    quote_distance: float,
    quote_size: float,
    depth: dict[str, float],
) -> dict[str, Any] | None:
    """Empirical pick-off charge from prints that swept through our level.

    A SELL print at/below mid - d would have filled our bid; a BUY print
    at/above mid + d would have filled our ask. The per-share loss is our
    fill price versus the mid ``markout_horizon_minutes`` later, weighted by
    our queue share q / (q + resting band depth on that side). Signed means
    can be negative (spread capture beats momentum); the CHARGE floors at 0.
    """
    if series is None or len(prints) < int(settings["markout_min_prints"]):
        return None
    horizon_seconds = float(settings["markout_horizon_minutes"]) * 60.0
    tolerance = 120.0
    crossing = 0
    loss_usd = 0.0
    stamps: list[float] = []
    for record in prints:
        stamps.append(record["stamp"])
        mid_then = _mid_at(series, record["stamp"], tolerance)
        mid_later = _mid_at(series, record["stamp"] + horizon_seconds, tolerance)
        if mid_then is None or mid_later is None:
            continue
        if record["side"] == "SELL" and record["price"] <= mid_then - quote_distance:
            fill_price = mid_then - quote_distance
            per_share = fill_price - mid_later  # we bought; positive = loss
            queue_share = quote_size / (quote_size + depth.get("bid", 0.0))
        elif record["side"] == "BUY" and record["price"] >= mid_then + quote_distance:
            fill_price = mid_then + quote_distance
            per_share = mid_later - fill_price  # we sold; positive = loss
            queue_share = quote_size / (quote_size + depth.get("ask", 0.0))
        else:
            continue
        crossing += 1
        loss_usd += per_share * min(record["size"], quote_size) * queue_share
    span_days = max((max(stamps) - min(stamps)) / 86400.0, 1.0 / 24.0)
    return {
        "prints_seen": len(prints),
        "band_crossing_prints_per_day": round(crossing / span_days, 2),
        "adverse_usd_per_day_markout": round(max(0.0, loss_usd / span_days), 4),
    }


def _adverse_selection(
    settings: dict[str, Any],
    fast_series: list[tuple[float, float]] | None,
    slow_series: list[tuple[float, float]] | None,
    prints: list[dict[str, float]],
    quote_distance: float,
    quote_size: float,
    depth: dict[str, float],
) -> dict[str, Any] | None:
    """Charge the WORST of the three estimates (series/prints pre-fetched so
    the distance sweep re-prices without re-downloading).

    The calm-last-24h failure mode is real (observed live: a market flat for a
    day, then a news gap): the 7-day/10-minute window prices event risk that
    the 1-minute window misses. The markout estimate measures what executed
    prints actually did to the passive side and is the only EMPIRICAL leg.
    """
    fast_fidelity = max(1, int(settings["reaction_minutes"]))
    fast = _pickoff_from_series(fast_series, quote_distance, quote_size, fidelity_minutes=fast_fidelity, min_points=30)
    slow = _pickoff_from_series(slow_series, quote_distance, quote_size, fidelity_minutes=10, min_points=30)
    if fast is None and slow is None:
        return None
    markout = _markout_adverse(settings, prints, fast_series, quote_distance, quote_size, depth)
    charges = [row["adverse_usd_per_day"] for row in (fast, slow) if row is not None]
    if markout is not None:
        charges.append(markout["adverse_usd_per_day_markout"])
    primary = fast or slow
    return {
        "history_points": primary["history_points"],
        "pickoff_events_per_day": primary["pickoff_events_per_day"],
        "adverse_usd_per_day_1min_24h": fast["adverse_usd_per_day"] if fast else None,
        "adverse_usd_per_day_10min_7d": slow["adverse_usd_per_day"] if slow else None,
        "adverse_usd_per_day_markout": markout["adverse_usd_per_day_markout"] if markout else None,
        "band_crossing_prints_per_day": markout["band_crossing_prints_per_day"] if markout else None,
        "markout_measured": markout is not None,
        "adverse_selection_usd_per_day": max(charges),
        "both_windows": fast is not None and slow is not None,
    }


def _size_portfolio(
    settings: dict[str, Any],
    candidates: list[dict[str, Any]],
    capital_cap_usd: float,
) -> tuple[list[dict[str, Any]], float, float]:
    """Run the registered greedy maker sizing at one capital cap.

    WO-57 calls this same function for supplementary planning caps so the
    registered $500 result and every M-gate retain exactly one implementation.
    """
    portfolio: list[dict[str, Any]] = []
    capital = 0.0
    max_multiple = max(1, int(settings["max_size_multiple"]))
    trusted = [
        row
        for row in candidates
        if (row.get("net_carry_usd_per_day") or 0) > 0
        and row.get("estimate_quality") == "book_and_history"
        and row.get("band_eligible") is True
        and row.get("resolution_risk") != "high"
    ]
    payout_floor = float(settings["min_daily_payout_usd"])
    for row in sorted(trusted, key=lambda candidate: candidate["net_carry_usd_per_day"], reverse=True):
        ours = row["our_score_per_side"]
        pool_implied = ours / row["estimated_reward_share"] - ours if row["estimated_reward_share"] > 0 else 0.0
        best: tuple[float, int] | None = None
        for k in range(1, max_multiple + 1):
            if capital + k * row["capital_usd"] > capital_cap_usd:
                break
            share_k = (k * ours) / (k * ours + pool_implied) if (k * ours + pool_implied) > 0 else 0.0
            gross_k = row["pot_usd_per_day"] * share_k
            if gross_k < payout_floor:
                # Gate M-C by construction: accruals below Polymarket's $1/day
                # minimum are never paid, so this size earns exactly nothing.
                continue
            net_k = gross_k - k * row["adverse_selection_usd_per_day"]
            if best is None or net_k > best[0]:
                best = (net_k, k)
        if best is None or best[0] <= 0:
            continue
        net_k, k = best
        portfolio.append(
            {
                "question": row["question"],
                "market_id": row.get("market_id", ""),
                "condition_id": row["condition_id"],
                "market_slug": row.get("market_slug", ""),
                "event_slug": row.get("event_slug", ""),
                "market_url": row.get("market_url", ""),
                "token_id": row.get("token_id", ""),
                "complement_token_id": row.get("complement_token_id", ""),
                "outcome": row.get("outcome", ""),
                "complement_outcome": row.get("complement_outcome", ""),
                "end_date_utc": row.get("end_date_utc", ""),
                "event_start_time_utc": row.get("event_start_time_utc", ""),
                "uma_resolution_status": row.get("uma_resolution_status", ""),
                "size_multiple": k,
                "quote_size_shares": k * row["rewards_min_size_shares"],
                "reference_mid_price": row.get("mid_price"),
                "reference_best_bid": row.get("best_bid"),
                "reference_best_ask": row.get("best_ask"),
                "quote_distance": row["quote_distance"],
                "quote_distance_fraction": row["quote_distance_fraction"],
                "order_price_min_tick_size": row.get("order_price_min_tick_size"),
                "quote_bid_price": row.get("quote_bid_price"),
                "quote_ask_price": row.get("quote_ask_price"),
                "capital_usd": round(k * row["capital_usd"], 2),
                "net_carry_usd_per_day": round(net_k, 4),
                "uncounted_supplementary_income_usd_per_day": round(k * (row.get("supplementary_income_usd_per_day") or 0.0), 4),
                "uncounted_rebate_usd_per_day": round(k * (row.get("supplementary_rebate_usd_per_day") or 0.0), 4),
                "uncounted_holding_usd_per_day": round(k * (row.get("supplementary_holding_usd_per_day") or 0.0), 4),
                "markout_measured": bool(row.get("markout_measured")),
                "resolution_risk": row.get("resolution_risk", "medium"),
                "resolution_risk_class": row.get("resolution_risk_class", "other"),
                "event_risk_flags": [keyword for keyword in EVENT_RISK_KEYWORDS if keyword in row["question"].lower()],
                "order_ticket_status": (
                    "exact"
                    if row.get("market_url")
                    and row.get("token_id")
                    and row.get("outcome")
                    and safe_float(row.get("quote_bid_price")) is not None
                    and safe_float(row.get("quote_ask_price")) is not None
                    else "incomplete_missing_public_market_metadata_or_tick"
                ),
            }
        )
        capital += k * row["capital_usd"]

    net_total = round(sum(row["net_carry_usd_per_day"] for row in portfolio), 2)
    return portfolio, capital, net_total


def _capital_curve(
    settings: dict[str, Any],
    candidates: list[dict[str, Any]],
    target_net_usd_per_day: float,
) -> tuple[list[dict[str, Any]], float | None]:
    enabled = str(settings.get("capital_curve_enabled", True)).strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    if not enabled:
        return [], None
    caps = sorted({float(cap) for cap in (settings.get("capital_curve_caps_usd") or [250, 500, 1000, 2000, 5000]) if (safe_float(cap) or 0.0) > 0})
    curve: list[dict[str, Any]] = []
    for cap in caps:
        portfolio, capital_used, net_per_day = _size_portfolio(settings, candidates, cap)
        curve.append(
            {
                "capital_cap_usd": round(cap, 2),
                "capital_used_usd": round(capital_used, 2),
                "portfolio_markets": len(portfolio),
                "net_usd_per_day": net_per_day,
                "net_usd_per_month": round(net_per_day * 30, 2),
                "net_per_day_per_capital_used": (round(net_per_day / capital_used, 8) if capital_used > 0 else None),
            }
        )
    capital_for_target = next(
        (row["capital_cap_usd"] for row in curve if row["net_usd_per_day"] >= target_net_usd_per_day),
        None,
    )
    return curve, capital_for_target


def _mb_finite(value: Any) -> float | None:
    """safe_float that also rejects non-finite (NaN/inf) values.

    A NaN in a `<`/`>` threshold comparison makes it False, which would let an
    unknown Tier-0 value pass a fail-closed gate; treat non-finite as missing.
    """

    parsed = safe_float(value)
    if parsed is None or not math.isfinite(parsed):
        return None
    return parsed


def _mb_tighter_max(settings: dict[str, Any], key: str, registered: float) -> float:
    """A configured maximum may only shrink the registered one (tighten-only).

    Zero is a valid (extreme) tightening for these non-negative maxima and is
    honored; only a negative or non-finite override falls back to the default.
    """

    value = _mb_finite(settings.get(key))
    if value is None or value < 0:
        return registered
    return min(registered, value)


def _mb_tighter_min(settings: dict[str, Any], key: str, registered: float) -> float:
    """A configured minimum may only grow the registered one (tighten-only)."""

    value = _mb_finite(settings.get(key))
    if value is None or value < 0:
        return registered
    return max(registered, value)


def _mb_tier0_coverage_sufficient(
    replay: dict[str, Any],
    portfolio: list[dict[str, Any]],
    *,
    study_generated_at: str,
    settings: dict[str, Any],
) -> bool:
    """M-B.1 (2026-07-18): every portfolio market must carry its OWN sufficient
    Tier-0 last-in-queue coverage from a recent official-book replay.

    Fail-closed and tighten-only. The study reads the PRIOR pipeline cycle's
    ``maker_fill_replay.json`` (this cycle's replay runs afterward), so an
    exact-portfolio-version match is impossible here; instead the replay must
    be official-sourced and recent (age bounded against the study clock), and
    every current portfolio market must have a per-market coverage row clearing
    the registered minima. Exact-version and 30-minute freshness are separately
    enforced at the funding decision by the WO-105 evaluator. A portfolio
    market with missing, stale, wrong-source, or insufficient Tier-0 coverage
    evaluates M-B as pending; the requirement can only withhold a pass, never
    create one.
    """

    if not portfolio or not isinstance(replay, dict):
        return False
    if str(replay.get("primary_book_source") or "").strip().lower() != MB_TIER0_REQUIRED_SOURCE:
        return False
    study_stamp = parse_timestamp(study_generated_at)
    replay_stamp = parse_timestamp(replay.get("generated_at_utc"))
    if study_stamp is None or replay_stamp is None:
        return False
    max_age = _mb_tighter_max(settings, "mb_tier0_max_replay_age_seconds", MB_TIER0_MAX_REPLAY_AGE_SECONDS)
    age = (study_stamp - replay_stamp).total_seconds()
    if age < 0 or age > max_age:
        return False
    rows = replay.get("per_market_coverage")
    if not isinstance(rows, list):
        return False
    by_market: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        condition_id = str(row.get("condition_id") or "").strip()
        asset_id = str(row.get("asset_id") or "").strip()
        if condition_id:
            by_market[condition_id] = row
        if asset_id:
            by_market[asset_id] = row
    min_evaluable = _mb_tighter_min(settings, "mb_tier0_min_evaluable", MB_TIER0_MIN_EVALUABLE)
    min_confirmed = _mb_tighter_min(settings, "mb_tier0_min_confirmed_fills", MB_TIER0_MIN_CONFIRMED_FILLS)
    min_coverage = _mb_tighter_min(settings, "mb_tier0_min_coverage_ratio", MB_TIER0_MIN_COVERAGE_RATIO)
    min_markouts = _mb_tighter_min(settings, "mb_tier0_min_markout_windows", MB_TIER0_MIN_MARKOUT_WINDOWS)
    max_haircut = _mb_tighter_max(settings, "mb_tier0_max_haircut", MB_TIER0_MAX_HAIRCUT)
    for entry in portfolio:
        condition_id = str(entry.get("condition_id") or "").strip()
        token_id = str(entry.get("token_id") or "").strip()
        row = by_market.get(condition_id) or by_market.get(token_id)
        if row is None:
            return False
        evaluable = _mb_finite(row.get("last_in_queue_evaluable_opportunities"))
        confirmed = _mb_finite(row.get("confirmed_fills"))
        coverage = _mb_finite(row.get("coverage_ratio"))
        haircut = _mb_finite(row.get("simulation_to_reality_haircut"))
        if evaluable is None or evaluable < min_evaluable:
            return False
        if confirmed is None or confirmed < min_confirmed:
            return False
        if coverage is None or coverage < min_coverage:
            return False
        if haircut is None or haircut > max_haircut:
            return False
        by_horizon = row.get("by_horizon")
        if not isinstance(by_horizon, dict):
            return False
        for horizon in MB_TIER0_HORIZONS:
            windows = _mb_finite((by_horizon.get(f"{horizon}m") or {}).get("windows_covered"))
            if windows is None or windows < min_markouts:
                return False
    return True


def _distinct_days_at_target(
    prior_runs: list[dict[str, Any]],
    target: float,
    *,
    current_day: str,
    latest_at_target: bool,
) -> set[str]:
    """Distinct UTC days whose LAST published_v2 run met the target.

    2026-07-18 maker-gate amendment M-A.1 (authorization = owner merge of this
    frozen-surface PR): closes the intraday cherry-picking the external audit
    flagged. A day counts only when
    its last published_v2 observation is at/above target, not when any intraday
    spike touched target. The current run is by construction today's last
    observation, so it governs today's membership regardless of an earlier
    spike. Tighten-only: it can only reduce the day count. The published_v2
    restriction (2026-07-11 amendment) is preserved.
    """
    last_run_by_day: dict[str, dict[str, Any]] = {}
    for run in prior_runs:
        stamp = str(run.get("generated_at_utc") or "")
        day = stamp[:10]
        if not day:
            continue
        previous = last_run_by_day.get(day)
        if previous is None or stamp >= str(previous.get("generated_at_utc") or ""):
            last_run_by_day[day] = run
    days_at_target = {
        day
        for day, run in last_run_by_day.items()
        if (safe_float(run.get("portfolio_net_carry_usd_per_day")) or 0.0) >= target
        and str(run.get("share_model") or "") == "published_v2"
    }
    days_at_target.discard("")
    if current_day:
        if latest_at_target:
            days_at_target.add(current_day)
        else:
            days_at_target.discard(current_day)
    return days_at_target


def run_maker_carry_study(cfg: EngineConfig) -> dict[str, Any]:
    settings = _settings(cfg)
    out_root = cfg.output_root / "maker_carry"
    summary_path = out_root / "maker_carry_study.json"
    summary: dict[str, Any] = {
        "status": "disabled",
        "generated_at_utc": now_utc(),
        "work_order": "WO-36",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    if str(settings.get("enabled", True)).strip().lower() in {"0", "false", "no"}:
        write_json(summary_path, summary)
        return summary

    universe, errors, stale_diagnostic = _rewarded_universe(settings)
    pause = float(settings["request_pause_seconds"])
    candidates: list[dict[str, Any]] = []
    resolution_class_stats = _resolution_quality_class_stats(cfg)
    fractions = sorted({float(f) for f in (settings.get("quote_distance_fractions") or [0.5]) if 0 < float(f) < 1}) or [0.5]
    measurement_universe, yield_scan = _yield_first_shortlist(settings, universe, fractions)
    if yield_scan.get("yield_scan_error"):
        errors.append(f"yield prescreen: {yield_scan['yield_scan_error']}")
    for market in measurement_universe:
        resolution_risk = _resolution_risk_for_question(market.get("question", ""), resolution_class_stats)
        v = market["rewards_max_spread_cents"] / 100.0
        quote_size = market["rewards_min_size_shares"]
        competition = _book_competition(settings, market["token_id"], market.get("complement_token_id", ""), v)
        if competition is None:
            continue
        depth = {"bid": competition["bid_depth"], "ask": competition["ask_depth"]}
        # Fetch market data ONCE; the distance sweep below re-prices freely.
        fast_series = _price_series(settings, market["token_id"], interval="1d", fidelity_minutes=max(1, int(settings["reaction_minutes"])))
        slow_series = _price_series(settings, market["token_id"], interval="1w", fidelity_minutes=10)
        prints = _recent_prints(settings, market["condition_id"])

        best_row: dict[str, Any] | None = None
        for fraction in fractions:
            quote_distance = fraction * v
            raw_our_score = _quadratic_score(quote_distance, v, quote_size)
            if raw_our_score <= 0:
                continue
            share, our_score = _share_from_published_score(settings, competition, raw_our_score)
            legacy_share = _legacy_share(competition, raw_our_score)
            gross = market["pot_usd_per_day"] * share
            adverse = _adverse_selection(settings, fast_series, slow_series, prints, quote_distance, quote_size, depth)
            row = {
                **market,
                **resolution_risk,
                "mid_price": round(competition["mid"], 4),
                "best_bid": competition["best_bid"],
                "best_ask": competition["best_ask"],
                "quote_distance": round(quote_distance, 4),
                "quote_distance_fraction": fraction,
                "competitor_score_bid": round(competition["bid_score"], 1),
                "competitor_score_ask": round(competition["ask_score"], 1),
                "our_score_per_side": round(our_score, 1),
                "share_model": "published_v2",
                "share_model_legacy": round(legacy_share, 5),
                "band_eligible": bool(competition["band_eligible"]),
                "estimated_reward_share": round(share, 5),
                "gross_reward_usd_per_day": round(gross, 4),
                # Bid collateral plus inventory to quote the ask, both ~size x price.
                "capital_usd": round(quote_size * 2 * competition["mid"], 2),
            }
            ticket_tick = safe_float(competition.get("tick_size")) or safe_float(
                market.get("order_price_min_tick_size")
            )
            quote_bid, quote_ask = _quote_prices(
                float(competition["mid"]),
                quote_distance,
                ticket_tick,
            )
            row.update(
                {
                    "order_price_min_tick_size": ticket_tick,
                    "quote_bid_price": quote_bid,
                    "quote_ask_price": quote_ask,
                }
            )
            if adverse is None:
                row.update(
                    {
                        "history_points": 0,
                        "pickoff_events_per_day": None,
                        "adverse_usd_per_day_1min_24h": None,
                        "adverse_usd_per_day_10min_7d": None,
                        "adverse_usd_per_day_markout": None,
                        "band_crossing_prints_per_day": None,
                        "markout_measured": False,
                        "adverse_selection_usd_per_day": None,
                        "net_carry_usd_per_day": None,
                        "estimate_quality": "no_price_history",
                    }
                )
            else:
                both_windows = adverse.pop("both_windows")
                row.update(adverse)
                row["net_carry_usd_per_day"] = round(gross - adverse["adverse_selection_usd_per_day"], 4)
                if not competition["band_eligible"]:
                    row["estimate_quality"] = "band_ineligible"
                elif share > float(settings["max_trusted_reward_share"]):
                    row["estimate_quality"] = "thin_book_untrusted"
                elif not both_windows:
                    row["estimate_quality"] = "single_window_history"
                else:
                    row["estimate_quality"] = "book_and_history"
            row.update(_supplementary_income(row, settings, depth))
            if best_row is None or (row.get("net_carry_usd_per_day") or -1e18) > (best_row.get("net_carry_usd_per_day") or -1e18):
                best_row = row
        if best_row is not None:
            candidates.append(best_row)
        if pause:
            time.sleep(pause)

    # Registered sizing is still evaluated only at capital_cap_usd. WO-57
    # reuses this exact function for supplementary planning caps below.
    cap = float(settings["capital_cap_usd"])
    portfolio, capital, net_total = _size_portfolio(settings, candidates, cap)
    target = float(settings["target_net_usd_per_day"])
    capital_curve, capital_for_target = _capital_curve(settings, candidates, target)
    top_portfolio = portfolio[0] if portfolio else {}

    # MAKER GATES - pre-registered 2026-07-09 (see module docstring). The
    # trend ledger is read BEFORE this run appends. Evidence counts distinct
    # UTC DAYS at/above target - intraday re-runs can never fast-forward the
    # clock, and a single day can never satisfy M-A.
    history_path = out_root / "maker_carry_history.csv"
    prior_runs = read_csv_rows(history_path)
    # 2026-07-11 dated tightening (pre-commitment audit): only days measured
    # under the CURRENT published share model count toward M-A. The 2026-07-10
    # legacy-model day cleared target under a share model later shown to
    # overstate shares 3-9x (WO-46); letting it count would allow the gate to
    # pass on 6 honest days plus 1 discredited one. Tighten-only: this can
    # only reduce the day count, never raise it.
    latest_at_target = bool(portfolio) and net_total >= target
    today = str(summary["generated_at_utc"])[:10]
    days_at_target = _distinct_days_at_target(
        prior_runs, target, current_day=today, latest_at_target=latest_at_target
    )
    runs_at_target = len(days_at_target)
    required_runs = int(settings["gate_min_runs_at_target"])
    gate_a_state = "pass" if latest_at_target and runs_at_target >= required_runs else "pending"
    # M-B.1 amendment (2026-07-18; authorization = owner merge of this
    # frozen-surface PR): M-B now ALSO requires each portfolio market's own
    # recent official-book Tier-0 coverage, not just a measured print markout.
    # Reads the PRIOR pipeline cycle's replay (this cycle's runs afterward);
    # tighten-only AND with the existing markout condition, fail-closed.
    replay = read_json(out_root / "maker_fill_replay.json", default={}) or {}
    mb_tier0_ok = _mb_tier0_coverage_sufficient(
        replay, portfolio, study_generated_at=str(summary["generated_at_utc"]), settings=settings
    )
    gate_b_state = (
        "pass"
        if portfolio and all(entry["markout_measured"] for entry in portfolio) and mb_tier0_ok
        else "pending"
    )
    maker_verdict = "evidence_supported_pending_human_decision" if gate_a_state == "pass" and gate_b_state == "pass" else "insufficient_evidence"
    maker_gates = {
        "registered_at_utc": MAKER_GATES_REGISTERED_AT_UTC,
        "M_A_carry_evidence": {
            "registered_rule": maker_gate_registration("M_A_carry_evidence")["rule"],
            "state": gate_a_state,
            "runs_at_or_above_target": runs_at_target,
            "counting": "distinct_utc_days",
            "share_model_scope": "published_v2_only",
            "required_runs": required_runs,
            "latest_run_at_target": latest_at_target,
        },
        "M_B_adverse_realism": {
            "registered_rule": maker_gate_registration("M_B_adverse_realism")["rule"],
            "state": gate_b_state,
            "note": (
                "every portfolio market must carry a MEASURED markout charge (empirical fills) "
                "AND (M-B.1 amendment) its own recent official-book Tier-0 coverage."
            ),
            "mb1_tier0_coverage_sufficient": mb_tier0_ok,
        },
        "M_C_payout_floor": {
            "registered_rule": maker_gate_registration("M_C_payout_floor")["rule"],
            "state": "pass_by_construction",
            "min_daily_payout_usd": float(settings["min_daily_payout_usd"]),
        },
        "maker_verdict": maker_verdict,
        "governance_note": (
            "A supported verdict NEVER places or authorises orders. Acting on the quote sheet is a "
            "human decision taken outside this system; the repo stays paper-only regardless."
        ),
    }

    summary.update(
        {
            "status": "ok" if candidates else ("failed" if errors else "no_candidates"),
            "universe_scan_mode": yield_scan["universe_scan_mode"],
            "universe_rewarded_markets": len(universe),
            "universe_pot_usd_per_day": round(sum(m["pot_usd_per_day"] for m in universe), 2),
            "excluded_stale": stale_diagnostic["excluded_stale"],
            "excluded_stale_by_reason": stale_diagnostic["excluded_stale_by_reason"],
            "excluded_stale_examples": stale_diagnostic["excluded_stale_examples"],
            "yield_scan_considered_markets": yield_scan["yield_scan_considered_markets"],
            "yield_scan_scored_markets": yield_scan["yield_scan_scored_markets"],
            "yield_scan_selected_markets": yield_scan["yield_scan_selected_markets"],
            "yield_scan_fallback": yield_scan["yield_scan_fallback"],
            "candidates_measured": len(candidates),
            "candidates_thin_book_untrusted": sum(1 for r in candidates if r.get("estimate_quality") == "thin_book_untrusted"),
            "candidates_resolution_high_risk": sum(1 for r in candidates if r.get("resolution_risk") == "high"),
            "portfolio_markets": len(portfolio),
            "portfolio": portfolio,
            "portfolio_capital_usd": round(capital, 2),
            "portfolio_net_carry_usd_per_day": net_total,
            "portfolio_net_carry_usd_per_month": round(net_total * 30, 2),
            "capital_curve": capital_curve,
            "capital_for_100_per_month": capital_for_target,
            "top_portfolio_market": top_portfolio.get("condition_id", ""),
            "top_portfolio_question": top_portfolio.get("question", ""),
            "portfolio_uncounted_supplementary_income_usd_per_day": round(sum(r.get("uncounted_supplementary_income_usd_per_day", 0.0) for r in portfolio), 4),
            "portfolio_uncounted_rebate_usd_per_day": round(sum(r.get("uncounted_rebate_usd_per_day", 0.0) for r in portfolio), 4),
            "portfolio_uncounted_holding_usd_per_day": round(sum(r.get("uncounted_holding_usd_per_day", 0.0) for r in portfolio), 4),
            "target_net_usd_per_day": target,
            "clears_100_per_month_target": bool(portfolio) and net_total >= target,
            "share_model": "published_v2",
            "maker_gates": maker_gates,
            "assumptions": {
                "universe_scan_mode": yield_scan["universe_scan_mode"],
                "universe_scan_mode_note": (
                    "2026-07-11 WO-56 coverage change only: rewarded markets are pre-screened by "
                    "pot x published-rule min-size share before the existing expensive history/markout study. "
                    "The registered gate metric remains the sized portfolio net carry at the $500 cap."
                ),
                "staleness_guard": (
                    "2026-07-14 WO-80 tighten-only universe filter: otherwise eligible rewarded markets "
                    "are excluded when their latest explicit title date or venue close time is past, or "
                    "when UMA resolution is proposed/disputed. Registered M-gates are unchanged."
                ),
                "quote_size_shares": "rewards_min_size per market, both sides",
                "quote_distance": f"per-market best of {fractions} x rewards_max_spread from mid",
                "share_model": "published_v2",
                "share_model_details": (
                    "market + complement books; Q_min=max(min(Q1,Q2), max(Q1,Q2)/c) inside "
                    f"[{settings['share_model_mid_band_min']}, {settings['share_model_mid_band_max']}], "
                    "strict min outside band; our hypothetical quote enters both sides symmetrically"
                ),
                "share_model_legacy": "one-release audit column retained in candidates as old min(same-token bid, ask) share",
                "adverse_selection_model": (
                    f"charge = worst of 24h@{settings['reaction_minutes']}min bars, 7d@10min bars, and the "
                    f"empirical markout of band-crossing prints at {settings['markout_horizon_minutes']}min, "
                    f"queue-share weighted against resting band depth"
                ),
                "thin_book_guard": f"raw share > {settings['max_trusted_reward_share']} excluded from portfolio",
                "supplementary_income": (
                    "rebates + holding rewards are reported as uncounted income only; they are excluded from portfolio_net_carry_usd_per_day and every M-gate"
                ),
                "capital_curve": (
                    "2026-07-11 WO-57 planning aid only: each listed cap reruns the registered "
                    "greedy sizing, but no curve value is read by an M-gate or policy."
                ),
                "resolution_risk": (
                    "WO-51 screen: high subjective/UMA-dispute-prone wording is excluded from the "
                    "quote portfolio; corpus evidence may only escalate LOW classes to MEDIUM."
                ),
            },
            "honesty_clause": (
                "Simulated maker fills are unverifiable (queue position, intra-minute jumps, competitor "
                "reaction). Net carry is an UPPER BOUND on the reward-share side and an approximation on "
                "the pick-off side. A YES here justifies deeper study only - never order placement."
            ),
            "errors": errors[:10],
        }
    )
    write_csv(out_root / "maker_carry_candidates.csv", candidates, fieldnames=CANDIDATE_FIELDS)
    write_json(summary_path, summary)
    # Daily trend ledger: pots, competition, and estimated carry move with the
    # calendar (esp. across the WC window), so keep a one-row-per-run history.
    history_fields = [
        "generated_at_utc",
        "share_model",
        "universe_scan_mode",
        "universe_rewarded_markets",
        "universe_pot_usd_per_day",
        "portfolio_markets",
        "portfolio_capital_usd",
        "portfolio_net_carry_usd_per_day",
        "top_portfolio_market",
        "top_portfolio_question",
        "clears_100_per_month_target",
    ]
    prior_runs.append({field: summary.get(field) for field in history_fields})
    write_csv(history_path, prior_runs, fieldnames=history_fields)
    _write_quote_sheet(out_root, summary, settings)
    return summary


def _wallet_reconciliation_quote_alert(out_root: Path) -> str:
    reconciliation = read_json(out_root.parent / "performance" / "wallet_reconciliation.json", default={}) or {}
    if not isinstance(reconciliation, dict):
        return ""
    discrepancy = safe_float(reconciliation.get("unexplained_discrepancy_usd"))
    threshold = safe_float(reconciliation.get("discrepancy_threshold_usd")) or 1.0
    if (
        str(reconciliation.get("reconciliation_status") or "") == "DISCREPANCY"
        and discrepancy is not None
        and discrepancy > threshold
    ):
        return f"> 🔴 **WALLET RECONCILIATION DISCREPANCY — ${discrepancy:.2f} unexplained. Human review required.**"
    return ""


def _write_quote_sheet(out_root: Path, summary: dict[str, Any], settings: dict[str, Any]) -> None:
    """Human-readable daily quote sheet - research output, never an order.

    Exists so a human can act on a supported maker verdict OUTSIDE this
    system with their own judgement and capital. The system itself remains
    paper-only and never touches an exchange."""
    gates = summary.get("maker_gates", {})
    toxicity_rows = {str(row.get("market") or ""): row for row in read_csv_rows(out_root / "flow_toxicity.csv") if row.get("market")}
    lines = [
        "# Maker quote sheet (WO-36) - RESEARCH OUTPUT, NOT ADVICE",
        "",
        f"Generated: {summary.get('generated_at_utc')}",
        f"Maker verdict: **{gates.get('maker_verdict', 'insufficient_evidence')}** "
        f"(M-A {gates.get('M_A_carry_evidence', {}).get('state')}, "
        f"M-B {gates.get('M_B_adverse_realism', {}).get('state')})",
        f"Estimated portfolio net carry: ${summary.get('portfolio_net_carry_usd_per_day')}/day "
        f"(~${summary.get('portfolio_net_carry_usd_per_month')}/month) on "
        f"${summary.get('portfolio_capital_usd')} capital - UPPER BOUND, see honesty clause.",
        f"Uncounted supplementary income shown separately: ${summary.get('portfolio_uncounted_supplementary_income_usd_per_day', 0.0)}/day "
        "(rebates + holding rewards; NOT included in gates or net carry).",
        "Capital curve (planning aid - uncounted, not a gate input): "
        + "; ".join(f"${row['capital_cap_usd']:.0f} cap -> ${row['net_usd_per_day']:.2f}/day" for row in (summary.get("capital_curve") or []))
        + (
            f"; first cap meeting $100/month target: ${summary['capital_for_100_per_month']:.0f}."
            if summary.get("capital_for_100_per_month") is not None
            else "; $100/month target not reached by the largest measured cap."
        ),
        "",
        "This system places NO orders. Acting on this sheet is a human decision,",
        "with human money, outside the bot's paper-only governance.",
        "",
        "## Exact human order tickets (WO-66)",
        "",
        "Each row is a decision-support ticket only: BUY the named outcome at the bid and SELL it at the ask. Re-check the live alert before touching the UI.",
        "",
        "| market URL | outcome side | bid | ask | size (shares/order) | capital | reference mid | est net/day | resolution risk | toxicity | risk flags | ticket |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|---:|---|---|",
    ]
    reconciliation_alert = _wallet_reconciliation_quote_alert(out_root)
    if reconciliation_alert:
        lines[2:2] = [reconciliation_alert, ""]
    for entry in summary.get("portfolio", []) or []:
        toxicity = toxicity_rows.get(str(entry.get("condition_id") or ""))
        toxicity_score = safe_float(toxicity.get("toxicity_score")) if toxicity else None
        toxicity_label = f"{toxicity_score:.2f}" if toxicity_score is not None else "-"
        resolution_label = f"{entry.get('resolution_risk', 'medium')} ({entry.get('resolution_risk_class', 'other')})"
        toxicity_flags = ["toxicity>0.9"] if toxicity_score is not None and toxicity_score > 0.9 else []
        flags = ", ".join([*(entry.get("event_risk_flags") or []), *toxicity_flags]) or "-"
        market_url = str(entry.get("market_url") or "").strip()
        market_label = str(entry.get("question") or "market")[:60].replace("|", "/")
        market_link = f"[{market_label}]({market_url})" if market_url else market_label
        outcome = str(entry.get("outcome") or "-").replace("|", "/")
        bid = safe_float(entry.get("quote_bid_price"))
        ask = safe_float(entry.get("quote_ask_price"))
        bid_label = f"{bid:.4f}" if bid is not None else "-"
        ask_label = f"{ask:.4f}" if ask is not None else "-"
        lines.append(
            f"| {market_link} | {outcome} | {bid_label} | {ask_label} | "
            f"{entry['quote_size_shares']:.0f} | ${entry['capital_usd']} | "
            f"{entry.get('reference_mid_price', '-')} | ${entry['net_carry_usd_per_day']} | "
            f"{resolution_label} | {toxicity_label} | {flags} | {entry.get('order_ticket_status', '-')} |"
        )
    lines += ["", "Standing rules (non-negotiable if a human ever acts on this):"]
    lines.extend(f"{rule['number']}. {rule['title']}: {rule['text']}" for rule in quote_sheet_standing_rules(settings))
    write_text_atomic(out_root / "maker_quote_sheet.md", "\n".join(lines) + "\n")


def main(config_path: str) -> dict[str, Any]:
    return run_maker_carry_study(load_config(config_path))
