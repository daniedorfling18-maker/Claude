from __future__ import annotations

from polymarket_predictive_engine.strategy_search import _market_family


def test_classifies_crypto_updown_asset_interval() -> None:
    row = {
        "market_slug": "btc-updown-5m-1782628200",
        "question": "Bitcoin Up or Down - June 28, 2:30AM-2:35AM ET",
        "category": "crypto",
    }

    assert _market_family(row) == "crypto_btc_updown_5m"


def test_classifies_esports_match() -> None:
    row = {
        "market_slug": "cs2-mana1-subtop-2026-06-28-game2",
        "question": "Counter-Strike: ex-MANA eSports vs Subtop De France - Map 2 Winner",
        "event_title": "Counter-Strike European Pro League",
    }

    assert _market_family(row) == "esports_match"


def test_classifies_macro_rates() -> None:
    row = {
        "market_slug": "will-the-fed-increase-interest-rates-by-25-bps-after-the-july-2026-meeting",
        "question": "Will the Fed increase interest rates by 25 bps after the July 2026 meeting?",
    }

    assert _market_family(row) == "macro_rates"


def test_raw_hex_market_without_metadata_stays_unknown() -> None:
    row = {
        "market_slug": "0x23b2c8baf686e251432729afd930cd7a19cfffda92c3bb7e180d7c01caed3054",
        "question": "",
        "category": "",
    }

    assert _market_family(row) == "unknown"
