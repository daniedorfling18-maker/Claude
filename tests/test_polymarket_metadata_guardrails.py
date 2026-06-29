from __future__ import annotations

from pathlib import Path

from polymarket_predictive_engine.cohort_validation import _promotion_metadata_blocker
from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.promotion_review import _review_row


def _cfg() -> EngineConfig:
    return EngineConfig(
        raw={
            "cohort_promotion": {
                "minimum_filled_orders": 5,
                "minimum_settled_orders": 3,
                "minimum_pnl_usdc": 0.0,
                "minimum_roi": 0.03,
                "minimum_tracking_hours_for_promotion": 2,
                "minimum_monthly_run_rate_usdc": 20,
                "probationary_minimum_filled_orders": 3,
                "probationary_minimum_settled_orders": 3,
                "probationary_minimum_pnl_usdc": 0.0,
                "probationary_minimum_roi": 0.03,
                "probationary_minimum_tracking_hours": 1,
                "probationary_minimum_monthly_run_rate_usdc": 20,
            }
        },
        path=Path("test-config.yaml"),
    )


def test_unknown_near_miss_has_promotion_metadata_blocker() -> None:
    assert _promotion_metadata_blocker("near_miss_learning|unknown") == "unresolved_unknown_near_miss_metadata"
    assert _promotion_metadata_blocker("unknown") == "unresolved_unknown_near_miss_metadata"
    assert _promotion_metadata_blocker("near_miss_learning|crypto") == ""


def test_promotion_review_marks_unknown_near_miss_metadata_blocked() -> None:
    row = {
        "signal_cohort": "near_miss_learning|unknown",
        "buy_fills": 4,
        "settled_fills": 3,
        "total_pnl_usdc": 3.2,
        "roi": 0.08,
        "monthly_run_rate_usdc": 58.0,
        "evidence_elapsed_hours": 39.6,
        "promotion_ready_score": 5,
        "promotion_ready_checks": 6,
        "metadata_valid": False,
        "metadata_blocker": "unresolved_unknown_near_miss_metadata",
        "promoted": False,
        "probationary": False,
    }

    reviewed = _review_row(_cfg(), row)

    assert reviewed["status"] == "metadata_blocked"
    assert reviewed["metadata_valid"] is False
    assert reviewed["metadata_blocker"] == "unresolved_unknown_near_miss_metadata"
    assert reviewed["promoted"] is False
    assert reviewed["probationary"] is False
    assert "do not promote or probe" in reviewed["next_action"]
