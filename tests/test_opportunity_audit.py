from __future__ import annotations

from polymarket_predictive_engine.opportunity_audit import _cohort_action


def test_metadata_blocked_unknown_is_not_actionable() -> None:
    status, action, score = _cohort_action(
        {
            "cohort": "near_miss_learning|unknown",
            "metadata_blocker": "unresolved_unknown_near_miss_metadata",
            "buy_fills": 4,
            "settled_fills": 3,
            "total_pnl_usdc": 3.2,
            "roi": 0.08,
            "monthly_run_rate_usdc": 50,
            "missing_full_gates": ["fills"],
        }
    )

    assert status == "blocked_metadata"
    assert "do not promote" in action.lower()
    assert score < 0


def test_negative_settled_cohort_is_kill_or_quarantine() -> None:
    status, action, _ = _cohort_action(
        {
            "cohort": "exploratory_historical_rule|crypto_updown_5m|outcome=down",
            "buy_fills": 26,
            "settled_fills": 26,
            "total_pnl_usdc": -72.25,
            "roi": -0.2779,
            "monthly_run_rate_usdc": -622,
            "missing_full_gates": ["pnl", "roi", "run_rate"],
        }
    )

    assert status == "kill_or_quarantine"
    assert "stop" in action.lower()


def test_roi_short_cohort_is_near_but_not_promotable() -> None:
    status, action, score = _cohort_action(
        {
            "cohort": "exploratory_inverse_historical_rule|crypto_btc_updown_5m|outcome=up",
            "buy_fills": 6,
            "settled_fills": 4,
            "total_pnl_usdc": 0.53,
            "roi": 0.0137,
            "monthly_run_rate_usdc": 42,
            "missing_full_gates": ["roi"],
        }
    )

    assert status == "near_but_roi_short"
    assert "do not loosen" in action.lower()
    assert score > 0


def test_high_roi_thin_positive_needs_more_evidence() -> None:
    status, action, score = _cohort_action(
        {
            "cohort": "exploratory_historical_rule|crypto_xrp_updown_5m|outcome=down",
            "buy_fills": 2,
            "settled_fills": 2,
            "total_pnl_usdc": 21.09,
            "roi": 1.05,
            "monthly_run_rate_usdc": 199,
            "missing_full_gates": ["fills", "settled"],
        }
    )

    assert status == "thin_positive"
    assert "too thin" in action.lower()
    assert score > 0
