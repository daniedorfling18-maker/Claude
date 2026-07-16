from __future__ import annotations

import json
from pathlib import Path

import pytest

from polymarket_common.fees import (
    CONSERVATIVE_UNKNOWN_RATE,
    polymarket_taker_fee_usdc,
    resolve_taker_fee_schedule,
    size_taker_buy,
    taker_fee_per_share,
    taker_fee_rate_for_category,
)
from scripts.polymarket_mispricing_bot import extract_tokens, token_snapshot_row


def test_recorded_v2_clob_market_info_drives_exact_fee_schedule() -> None:
    payload = json.loads(
        Path("tests/fixtures/recorded/clob_market_info_v2_2026-07-16.json").read_text(encoding="utf-8")
    )
    schedule = resolve_taker_fee_schedule({**payload, "category": "tennis"})

    assert schedule.rate == 0.05
    assert schedule.category == "sports"
    assert schedule.source == "clob_market_info_fd"
    assert schedule.enabled is True
    assert schedule.exponent == 1
    assert schedule.taker_only is True


def test_recorded_gamma_shape_retains_fee_metadata_in_scanner_snapshot() -> None:
    markets = json.loads(Path("tests/fixtures/recorded/gamma_markets_2026-07-15.json").read_text(encoding="utf-8"))
    tokens = extract_tokens(
        [
            {
                "slug": "sanitized-recorded-event",
                "title": "Sanitized recorded event",
                "category": "politics",
                "markets": markets,
            }
        ]
    )

    assert len(tokens) == 2
    assert tokens[0].category == "politics"
    assert tokens[0].fees_enabled is False
    snapshot = token_snapshot_row(tokens[0], None, None)
    assert snapshot["category"] == "politics"
    assert snapshot["fees_enabled"] is False


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("crypto_btc_updown_5m", 0.07),
        ("tennis_tennis_winner", 0.05),
        ("worldcup_match", 0.05),
        ("macro_economy", 0.05),
        ("finance", 0.04),
        ("politics", 0.04),
        ("ai_model_leader", 0.04),
        ("weather", 0.05),
        ("geopolitics", 0.0),
        ("unknown", 0.05),
    ],
)
def test_category_schedule_is_current_and_fine_family_aware(category: str, expected: float) -> None:
    assert taker_fee_rate_for_category(category) == expected


def test_explicit_fee_disabled_and_exact_zero_override_category_fallback() -> None:
    disabled = resolve_taker_fee_schedule({"category": "crypto", "feesEnabled": False})
    exact_zero = resolve_taker_fee_schedule({"category": "crypto", "fd": {"r": 0, "e": 1, "to": True}})

    assert disabled.rate == 0
    assert disabled.source == "explicit_fees_disabled"
    assert exact_zero.rate == 0
    assert exact_zero.source == "clob_market_info_fd"


@pytest.mark.parametrize("malformed", ["bad", -0.1, 2.0, float("nan")])
def test_malformed_exact_rate_fails_to_conservative_nonzero_fee(malformed: object) -> None:
    schedule = resolve_taker_fee_schedule({"category": "geopolitics", "fd": {"r": malformed}})

    assert schedule.rate == CONSERVATIVE_UNKNOWN_RATE
    assert schedule.enabled is True
    assert schedule.source == "malformed_exact_fee_conservative_fallback"


def test_fee_curve_is_symmetric_and_peaks_at_half() -> None:
    schedule = resolve_taker_fee_schedule("crypto")
    prices = [index / 100 for index in range(1, 100)]
    fees = [taker_fee_per_share(price=price, schedule=schedule) for price in prices]

    assert max(fees) == taker_fee_per_share(price=0.5, schedule=schedule)
    for price in prices:
        assert taker_fee_per_share(price=price, schedule=schedule) == pytest.approx(
            taker_fee_per_share(price=1.0 - price, schedule=schedule)
        )


def test_aggregate_fill_fee_rounds_to_five_decimals() -> None:
    schedule = resolve_taker_fee_schedule("sports")
    fee = polymarket_taker_fee_usdc(shares=100, price=0.5, schedule=schedule)

    assert fee == 1.25


@pytest.mark.parametrize("price", [0.01, 0.05, 0.5, 0.95, 0.99])
def test_taker_buy_sizing_never_exceeds_total_budget(price: float) -> None:
    schedule = resolve_taker_fee_schedule("crypto")
    shares, gross, fee, total = size_taker_buy(total_budget_usdc=10.0, price=price, schedule=schedule)

    assert shares > 0
    assert total == pytest.approx(gross + fee)
    assert total <= 10.0
    assert 10.0 - total <= 0.00002
