from __future__ import annotations

from polymarket_predictive_engine.worldcup_validation import signal_cohort


def test_daily_crypto_live_model_gets_separate_cohort():
    cohort = signal_cohort(
        {
            "market_slug": "bitcoin-up-or-down-on-june-26-2026",
            "question": "Bitcoin Up or Down on June 26?",
            "outcome": "Down",
            "crypto_model_status": "scored",
        }
    )

    assert cohort == "exploratory_crypto_updown_live_model|crypto_btc_updown_daily|outcome=down"


def test_daily_crypto_without_live_model_does_not_pollute_5m_rule_cohorts():
    cohort = signal_cohort(
        {
            "market_slug": "xrp-up-or-down-on-june-26-2026",
            "question": "XRP Up or Down on June 26?",
            "outcome": "Down",
        }
    )

    assert cohort == "exploratory_historical_rule|crypto_xrp_updown_daily|outcome=down"


def test_legacy_5m_crypto_rule_cohort_is_preserved():
    cohort = signal_cohort(
        {
            "market_slug": "btc-updown-5m-1782468000",
            "question": "Bitcoin UpDown 5M",
            "outcome": "Up",
        }
    )

    assert cohort == "exploratory_inverse_historical_rule|crypto_btc_updown_5m|outcome=up"
