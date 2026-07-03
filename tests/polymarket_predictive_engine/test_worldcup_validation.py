from __future__ import annotations

from polymarket_predictive_engine.worldcup_validation import classify_market_family, signal_cohort


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


def test_unknown_fed_sequence_market_gets_macro_rates_family():
    row = {
        "category": "unknown",
        "market_slug": "will-the-fed-cutpausecut-in-the-next-three-decisions-julsepoct-20260617191211938",
        "question": "Will the Fed Cut-Pause-Cut in the next three decisions (Jul-Sep-Oct)?",
        "outcome": "Yes",
    }

    assert classify_market_family(row) == "macro_rates"
    assert signal_cohort(row) == "macro_rates"


def test_unknown_liquid_event_families_are_resolved_for_research_buckets():
    cases = [
        (
            {
                "category": "unknown",
                "market_slug": "will-google-have-the-best-ai-model-at-the-end-of-july-2026",
                "question": "Will Google have the best AI model at the end of July 2026?",
            },
            "ai_model_leader",
        ),
        (
            {
                "category": "unknown",
                "market_slug": "will-elena-rybakina-be-the-2026-womens-wimbledon-winner",
                "question": "Will Elena Rybakina be the 2026 Women's Wimbledon Winner?",
            },
            "tennis_tennis_winner",
        ),
        (
            {
                "category": "unknown",
                "market_slug": "lol-hle1-tsw-2026-07-02-game1",
                "question": "LoL: Hanwha Life Esports vs Team Secret Whales - Game 1 Winner",
            },
            "esports_match",
        ),
        (
            {
                "category": "unknown",
                "market_slug": "hok-ttg-tesa-2026-07-02-game-handicap-home-1pt5",
                "question": "Game Handicap: TES.A (-1.5) vs Talent Gaming (+1.5)",
            },
            "esports_match",
        ),
        (
            {
                "category": "unknown",
                "market_slug": "will-microsoft-be-the-largest-company-in-the-world-by-market-cap-on-december-31",
                "question": "Will Microsoft be the largest company in the world by market cap on December 31?",
            },
            "equities_macro",
        ),
        (
            {
                "category": "",
                "market_slug": "bitcoin-above-62k-on-july-2-2026",
                "question": "Will the price of Bitcoin be above $62,000 on July 2?",
            },
            "crypto_btc_special",
        ),
    ]

    for row, expected in cases:
        assert classify_market_family(row) == expected
        assert signal_cohort(row) == expected


def test_broadened_sharp_sports_markets_get_specific_families():
    cases = [
        (
            {
                "category": "sports",
                "market_slug": "will-the-boston-celtics-beat-the-new-york-knicks-on-july-3",
                "question": "NBA: Will the Boston Celtics beat the New York Knicks on July 3?",
            },
            "basketball_nba_match",
        ),
        (
            {
                "category": "sports_other",
                "market_slug": "will-the-yankees-beat-the-red-sox-on-july-3",
                "question": "MLB: Will the New York Yankees beat the Boston Red Sox?",
            },
            "baseball_mlb_match",
        ),
        (
            {
                "category": "sports",
                "market_slug": "will-alex-pereira-win-ufc-320",
                "question": "UFC: Will Alex Pereira win his fight?",
            },
            "mma_match",
        ),
        (
            {
                "category": "tennis",
                "market_slug": "will-jannik-sinner-win-on-july-3",
                "question": "Tennis: Will Jannik Sinner win on July 3?",
            },
            "tennis_match",
        ),
    ]

    for row, expected in cases:
        assert classify_market_family(row) == expected
        assert signal_cohort(row) == expected
