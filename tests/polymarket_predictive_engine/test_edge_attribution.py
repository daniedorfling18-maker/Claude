from __future__ import annotations

from pathlib import Path

from pytest import approx

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.edge_attribution import build_edge_attribution
from polymarket_predictive_engine.utils import read_csv_rows, read_json, write_csv


def _cfg(tmp_path: Path) -> EngineConfig:
    return EngineConfig(
        raw={"paths": {"output_root": str(tmp_path / "outputs")}},
        path=tmp_path / "cfg.yaml",
    )


def test_edge_attribution_decomposes_paper_shadow_clv_and_quote_quality(tmp_path):
    cfg = _cfg(tmp_path)
    write_csv(
        cfg.output_root / "polymarket_price_action" / "paper_broker_round_trip_evidence.csv",
        [
            {
                "signal_cohort": "macro_rates",
                "family": "macro_rates",
                "edge_lower_bound": "0.04",
                "stake_usdc": "10",
                "quantity": "20",
                "entry_price": "0.50",
                "entry_signal_bid": "0.49",
                "exit_quote_spread": "0.02",
                "quote_consistency_status": "quote_consistent",
                "realized_pnl_usdc": "-0.2",
            },
            {
                "signal_cohort": "ai_model_leader",
                "family": "ai_model_leader",
                "edge_lower_bound": "0.05",
                "stake_usdc": "20",
                "quantity": "10",
                "entry_price": "0.30",
                "entry_signal_bid": "0.29",
                "exit_quote_spread": "0.02",
                "quote_consistency_status": "quote_conflict_bid_gap",
                "realized_pnl_usdc": "5.0",
            },
        ],
    )
    write_csv(
        cfg.output_root / "polymarket_shadow" / "shadow_positions.csv",
        [
            {
                "shadow_position_id": "shadow-1",
                "signal_cohort": "macro_rates",
                "category": "macro_rates",
                "status": "closed",
                "quantity": "20",
                "stake_usdc": "10",
                "entry_edge_lower_bound": "0.03",
                "realised_pnl_usdc": "-1.0",
                "unrealised_pnl_usdc": "0",
            }
        ],
    )
    write_csv(
        cfg.governance_root / "closing_line_value_positions.csv",
        [
            {
                "shadow_position_id": "shadow-1",
                "signal_cohort": "macro_rates",
                "line_kind": "closing",
                "clv": "-0.05",
            }
        ],
    )

    payload = build_edge_attribution(cfg)
    written = read_json(cfg.governance_root / "edge_attribution.json")
    csv_rows = read_csv_rows(cfg.governance_root / "edge_attribution_cohorts.csv")

    assert payload["status"] == "ok"
    assert written["paper_trading_invoked"] is False
    assert written["live_trading_invoked"] is False
    assert written["paper_round_trips"] == 2
    assert written["paper_audited_round_trips"] == 1
    assert written["quote_conflict_round_trips"] == 1
    assert written["cohorts_attributed"] == 2
    assert len(csv_rows) == 2

    macro = next(row for row in written["cohorts"] if row["signal_cohort"] == "macro_rates")
    assert macro["paper_audited_pnl_usdc"] == approx(-0.2)
    assert macro["shadow_realized_pnl_usdc"] == approx(-1.0)
    assert macro["entry_edge_usdc"] == approx(0.7)
    assert macro["spread_slippage_cost_usdc"] == approx(0.4)
    assert macro["line_movement_usdc"] == approx(-1.0)
    assert macro["decision_pnl_usdc"] == approx(-1.2)
    assert macro["mean_final_clv"] == approx(-0.05)
    assert macro["primary_drag"] == "spread_slippage"
    assert macro["recommended_action"] == "tighten_liquidity_spread_or_reduce_size"

    ai = next(row for row in written["cohorts"] if row["signal_cohort"] == "ai_model_leader")
    assert ai["paper_realized_pnl_usdc"] == approx(5.0)
    assert ai["paper_audited_pnl_usdc"] == approx(0.0)
    assert ai["paper_unaudited_pnl_usdc"] == approx(5.0)
    assert ai["paper_quote_conflict_round_trips"] == 1
    assert ai["primary_drag"] == "quote_quality"
    assert ai["recommended_action"] == "fix_quote_audit_before_promotion"


def test_edge_attribution_writes_fail_closed_empty_artifact(tmp_path):
    cfg = _cfg(tmp_path)

    payload = build_edge_attribution(cfg)

    assert payload["status"] == "insufficient_evidence"
    assert payload["cohorts_attributed"] == 0
    assert payload["paper_trading_invoked"] is False
    assert payload["live_trading_invoked"] is False
    assert (cfg.governance_root / "edge_attribution.json").exists()
    assert (cfg.governance_root / "edge_attribution_cohorts.csv").exists()
