from __future__ import annotations

from pathlib import Path

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.shadow_cohort import update_shadow_cohort_evidence
from polymarket_predictive_engine.utils import read_csv_rows


def _cfg(tmp_path: Path) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {
                "data_root": str(tmp_path),
                "output_root": str(tmp_path / "outputs"),
                "database_path": str(tmp_path / "work" / "paper.sqlite"),
            },
            "shadow_cohort_validation": {
                "enabled": True,
                "stake_usdc": 10,
                "candidate_limit_per_cycle": 4,
                "maximum_open_positions": 10,
                "settle_resolved_markets": False,
                "settlement_only_fast_crypto_updown": True,
            },
            "cohort_promotion": {
                "minimum_filled_orders": 5,
                "minimum_settled_orders": 3,
                "minimum_pnl_usdc": 0.0,
                "minimum_roi": 0.03,
                "minimum_tracking_hours_for_promotion": 2,
                "minimum_monthly_run_rate_usdc": 20,
            },
            "costs": {"slippage": 0.0},
        },
        path=tmp_path / "config.yaml",
    )


def test_fast_crypto_updown_shadow_positions_hold_to_final_settlement(tmp_path):
    cfg = _cfg(tmp_path)

    update_shadow_cohort_evidence(
        cfg,
        [
            {
                "market_id": "m-btc-5m",
                "market_slug": "btc-updown-5m-2000000000",
                "token_id": "t-btc-down",
                "outcome": "Down",
                "category": "crypto",
                "signal_cohort": "exploratory_crypto_updown_live_model|crypto_btc_updown_5m|outcome=down",
                "shadow_trade_candidate": True,
                "shadow_candidate_reason": "shadow_eligible",
                "executable_price": "0.10",
                "best_bid": "0.10",
                "spread": "0.01",
                "liquidity": "1000",
                "edge_lower_bound": "0.10",
                "prediction_timestamp": "2026-06-27T04:00:00Z",
            }
        ],
    )

    summary = update_shadow_cohort_evidence(
        cfg,
        [
            {
                "market_id": "m-btc-5m",
                "market_slug": "btc-updown-5m-2000000000",
                "token_id": "t-btc-down",
                "outcome": "Down",
                "category": "crypto",
                "signal_cohort": "exploratory_crypto_updown_live_model|crypto_btc_updown_5m|outcome=down",
                "shadow_trade_candidate": True,
                "executable_price": "0.30",
                "best_bid": "0.30",
                "spread": "0.01",
                "liquidity": "1000",
                "edge_lower_bound": "0.10",
                "prediction_timestamp": "2026-06-27T04:01:00Z",
            }
        ],
    )

    positions = read_csv_rows(cfg.output_root / "polymarket_shadow" / "shadow_positions.csv")
    fills = read_csv_rows(cfg.output_root / "polymarket_shadow" / "shadow_fills.csv")

    assert summary["closed_this_cycle"] == 0
    assert positions[0]["status"] == "open"
    assert positions[0]["settlement_policy"] == "final_settlement_only"
    assert positions[0]["close_reason"] == ""
    assert positions[0]["close_time"] == "2033-05-18T03:38:20Z"
    assert float(positions[0]["return_pct"]) >= 1.0
    assert [row["side"] for row in fills] == ["BUY_SHADOW"]
