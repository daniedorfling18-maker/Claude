"""Canonical Polymarket V2 taker-fee model.

Polymarket's current venue formula is::

    fee_usdc = shares * fee_rate * price * (1 - price)

Only takers pay.  The protocol rounds the aggregate fee for a fill to five
decimal places.  A market-specific CLOB ``fd.r``/fee-schedule rate is more
authoritative than category inference; when it is unavailable we use the
documented category table.  Unknown or malformed metadata falls back to a
conservative fee rather than silently assuming zero.

Canonical source (retrieved 2026-07-16):
https://docs.polymarket.com/trading/fees
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any, Mapping

TAKER_FEE_MODEL_VERSION = "polymarket-v2-category-price-2026-07-16"
CONSERVATIVE_UNKNOWN_RATE = 0.07

CATEGORY_TAKER_FEE_RATES: Mapping[str, float] = {
    "crypto": 0.07,
    "sports": 0.05,
    "finance": 0.04,
    "politics": 0.04,
    "economics": 0.05,
    "culture": 0.05,
    "weather": 0.05,
    "other": 0.05,
    "general": 0.05,
    "mentions": 0.04,
    "tech": 0.04,
    "geopolitics": 0.0,
}


@dataclass(frozen=True)
class TakerFeeSchedule:
    rate: float
    category: str
    source: str
    enabled: bool
    exponent: float
    taker_only: bool
    model_version: str = TAKER_FEE_MODEL_VERSION

    def audit_fields(self, *, price: float | None = None) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "taker_fee_rate": self.rate,
            "taker_fee_category": self.category,
            "taker_fee_source": self.source,
            "taker_fee_enabled": self.enabled,
            "taker_fee_exponent": self.exponent,
            "taker_fee_taker_only": self.taker_only,
            "taker_fee_model_version": self.model_version,
        }
        if price is not None:
            fields["taker_fee_per_share"] = taker_fee_per_share(price=price, schedule=self)
            fields["taker_fee_per_notional_usdc"] = (
                taker_fee_per_share(price=price, schedule=self) / price if price > 0 else 0.0
            )
        return fields


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _json_mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return None
    return decoded if isinstance(decoded, Mapping) else None


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return None


def _normalise_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def fee_category_for_market(market: Mapping[str, Any] | str) -> str:
    if isinstance(market, str):
        category = _normalise_text(market)
        text = category
    else:
        category = _normalise_text(
            market.get("fee_category")
            or market.get("taker_fee_category")
            or market.get("category")
            or market.get("family")
            or market.get("market_family")
        )
        text = "_".join(
            _normalise_text(market.get(key))
            for key in (
                "category",
                "family",
                "market_family",
                "market_slug",
                "question",
                "event_slug",
                "event_title",
                "signal_cohort",
            )
            if market.get(key)
        )

    combined = f"{category}_{text}"
    if any(token in combined for token in ("worldcup", "world_cup", "fifa")):
        return "sports"
    if any(
        token in combined
        for token in (
            "sports",
            "soccer",
            "football",
            "tennis",
            "basketball",
            "baseball",
            "hockey",
            "cricket",
            "rugby",
            "golf",
            "mma",
            "boxing",
            "esports",
            "nfl",
            "nba",
            "mlb",
            "nhl",
            "atp",
            "wta",
            "itf",
        )
    ):
        return "sports"
    word_tokens = set(combined.split("_"))
    if any(token in combined for token in ("crypto", "bitcoin", "ethereum", "solana")) or word_tokens.intersection(
        {"btc", "eth", "sol", "xrp"}
    ):
        return "crypto"
    if any(token in combined for token in ("geopolitics", "geopolitical", "world_events", "world_event")):
        return "geopolitics"
    if any(token in combined for token in ("economics", "economy", "macro", "interest_rate", "federal_reserve", "fed_rate")):
        return "economics"
    if any(token in combined for token in ("politics", "political", "election", "president", "congress")):
        return "politics"
    if any(token in combined for token in ("finance", "financial", "stock", "equity", "ipo", "earnings")):
        return "finance"
    if any(token in combined for token in ("weather", "temperature", "rainfall", "hurricane")):
        return "weather"
    if any(token in combined for token in ("technology", "tech", "ai_model", "artificial_intelligence")):
        return "tech"
    if any(token in combined for token in ("mentions", "mention", "word_count", "say_during")):
        return "mentions"
    if any(token in combined for token in ("culture", "entertainment", "movie", "music", "celebrity")):
        return "culture"
    if category in CATEGORY_TAKER_FEE_RATES:
        return category
    return "other"


def taker_fee_rate_for_category(category: str) -> float:
    return float(CATEGORY_TAKER_FEE_RATES[fee_category_for_market(category)])


def _market_fee_payload(market: Mapping[str, Any]) -> tuple[Mapping[str, Any] | None, str]:
    for key, source in (
        ("fd", "clob_market_info_fd"),
        ("feeSchedule", "market_fee_schedule"),
        ("fee_schedule", "market_fee_schedule"),
        ("fee_schedule_json", "market_fee_schedule"),
    ):
        if key in market:
            return _json_mapping(market.get(key)), source
    return None, ""


def resolve_taker_fee_schedule(market: Mapping[str, Any] | str) -> TakerFeeSchedule:
    row: Mapping[str, Any] = {"category": market} if isinstance(market, str) else market
    category = fee_category_for_market(row)
    category_rate = taker_fee_rate_for_category(category)
    enabled_value: Any = None
    for key in ("feesEnabled", "fees_enabled", "taker_fee_enabled"):
        if key in row:
            enabled_value = row.get(key)
            break
    explicitly_enabled = _optional_bool(enabled_value)

    payload, payload_source = _market_fee_payload(row)
    rate_value: Any = None
    exponent_value: Any = None
    taker_only_value: Any = None
    malformed_exact = False
    if payload is not None:
        rate_value = payload.get("r", payload.get("rate"))
        exponent_value = payload.get("e", payload.get("exponent"))
        taker_only_value = payload.get("to", payload.get("taker_only"))
        malformed_exact = rate_value not in (None, "") and _number(rate_value) is None
    else:
        for key in ("fee_schedule_rate", "taker_fee_rate", "fee_rate"):
            if key in row and row.get(key) not in (None, ""):
                rate_value = row.get(key)
                payload_source = str(row.get("taker_fee_source") or "flattened_market_fee_rate")
                malformed_exact = _number(rate_value) is None
                break
        exponent_value = row.get("fee_schedule_exponent", row.get("taker_fee_exponent"))
        taker_only_value = row.get("fee_schedule_taker_only", row.get("taker_fee_taker_only"))

    rate = _number(rate_value)
    if rate is not None and not 0.0 <= rate <= 1.0:
        malformed_exact = True
        rate = None
    exponent = _number(exponent_value)
    if exponent is None or exponent <= 0:
        exponent = 1.0
    taker_only = _optional_bool(taker_only_value)
    if taker_only is None:
        taker_only = True

    if malformed_exact:
        rate = max(category_rate, CONSERVATIVE_UNKNOWN_RATE)
        return TakerFeeSchedule(
            rate=rate,
            category=category,
            source="malformed_exact_fee_conservative_fallback",
            enabled=rate > 0,
            exponent=exponent,
            taker_only=taker_only,
        )
    if rate is not None:
        source = payload_source or "explicit_market_fee_rate"
        if explicitly_enabled is False and rate > 0:
            source += "_metadata_conflict_conservative"
        return TakerFeeSchedule(
            rate=rate,
            category=category,
            source=source,
            enabled=rate > 0,
            exponent=exponent,
            taker_only=taker_only,
        )
    if explicitly_enabled is False:
        return TakerFeeSchedule(
            rate=0.0,
            category=category,
            source="explicit_fees_disabled",
            enabled=False,
            exponent=exponent,
            taker_only=taker_only,
        )
    return TakerFeeSchedule(
        rate=category_rate,
        category=category,
        source="documented_category_fallback" if category != "other" else "unknown_category_conservative_fallback",
        enabled=category_rate > 0,
        exponent=exponent,
        taker_only=taker_only,
    )


def _validate_price(value: float) -> float:
    price = float(value)
    if not math.isfinite(price) or not 0.0 < price < 1.0:
        raise ValueError(f"price must be strictly between 0 and 1, got {value!r}")
    return price


def _validate_non_negative(value: float, *, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise ValueError(f"{name} must be finite and non-negative, got {value!r}")
    return number


def taker_fee_per_share(*, price: float, schedule: TakerFeeSchedule) -> float:
    share_price = _validate_price(price)
    if not schedule.enabled or schedule.rate <= 0:
        return 0.0
    return schedule.rate * share_price * (1.0 - share_price)


def polymarket_taker_fee_usdc(
    *,
    shares: float,
    price: float,
    taker_fee_rate: float | None = None,
    fees_enabled: bool = True,
    schedule: TakerFeeSchedule | None = None,
) -> float:
    share_count = _validate_non_negative(shares, name="shares")
    share_price = _validate_price(price)
    if schedule is None:
        rate = _validate_non_negative(taker_fee_rate or 0.0, name="taker_fee_rate")
        enabled = bool(fees_enabled) and rate > 0
    else:
        rate = schedule.rate
        enabled = schedule.enabled
    if not enabled or share_count == 0.0 or rate == 0.0:
        return 0.0
    return round(share_count * rate * share_price * (1.0 - share_price), 5)


def size_taker_buy(
    *,
    total_budget_usdc: float,
    price: float,
    schedule: TakerFeeSchedule,
) -> tuple[float, float, float, float]:
    """Return shares, gross notional, rounded fee, and total debit."""

    budget = _validate_non_negative(total_budget_usdc, name="total_budget_usdc")
    share_price = _validate_price(price)
    if budget == 0:
        return 0.0, 0.0, 0.0, 0.0
    unit_cost = share_price + taker_fee_per_share(price=share_price, schedule=schedule)
    shares = budget / unit_cost
    fee = polymarket_taker_fee_usdc(shares=shares, price=share_price, schedule=schedule)
    gross = shares * share_price
    total = gross + fee
    if total > budget:
        # Aggregate 5dp fee rounding can exceed the continuous approximation
        # by at most half a cent-millionth.  Shrink once and recompute so the
        # paper cash ledger can never go negative from rounding.
        shares = max(0.0, (budget - 0.00001) / unit_cost)
        fee = polymarket_taker_fee_usdc(shares=shares, price=share_price, schedule=schedule)
        gross = shares * share_price
        total = gross + fee
    return shares, gross, fee, total
