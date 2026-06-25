import sqlite3

import pytest

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.data_quality import data_quality
from polymarket_predictive_engine.execution.paper import paper_trade
from polymarket_predictive_engine.portfolio import portfolio_snapshot, reconciliation_report
from polymarket_predictive_engine.risk import risk_decision
from polymarket_predictive_engine.storage import init_db


def _cfg(tmp_path):
    return EngineConfig(
        raw={
            "paths": {
                "data_root": str(tmp_path),
                "output_root": str(tmp_path / "outputs"),
                "database_path": str(tmp_path / "work" / "ledger.sqlite"),
            },
            "trading": {"mode": "paper"},
            "risk": {
                "bankroll": 1000,
                "minimum_edge": 0.03,
                "minimum_confidence": 0.0,
                "maximum_spread": 0.08,
                "minimum_liquidity": 50,
                "minimum_time_to_close_minutes": 15,
                "maximum_resolution_risk": 0.25,
                "maximum_slippage": 0.02,
                "kelly_cap": 0.01,
            },
            "paper_trading": {"starting_cash": 1000},
            "governance_thresholds": {},
        },
        path=tmp_path / "cfg.yaml",
    )


def test_data_quality_fails_closed_without_raw_snapshots(tmp_path):
    cfg = _cfg(tmp_path)
    with pytest.raises(RuntimeError, match="Data-quality blockers"):
        data_quality(cfg)
    _, summary = data_quality(cfg, allow_warnings=True)
    assert summary["blocker_count"] == 1
    assert summary["training_allowed"] is False


def test_paper_trade_placeholder_is_deprecated(tmp_path):
    cfg = _cfg(tmp_path)
    summary = paper_trade(cfg)
    assert summary["status"] == "deprecated_placeholder_blocked"
    assert summary["approved_for_paper_trading"] is False
    assert summary["orders"] == 0


def test_risk_decision_returns_explicit_execution_units(tmp_path):
    cfg = _cfg(tmp_path)
    decision = risk_decision(
        cfg,
        {
            "market_id": "m1",
            "token_id": "t1",
            "edge": 0.2,
            "calibrated_probability": 0.8,
            "executable_price": 0.55,
            "spread": 0.01,
            "liquidity": 1000,
            "time_to_close_minutes": 60,
        },
        {"bankroll": 1000, "cash": 1000},
    )
    assert decision["approved"] is True
    assert decision["stake_usdc"] > 0
    assert decision["quantity"] > 0
    assert 0 < decision["limit_price"] < 1


def test_portfolio_and_reconciliation_read_sqlite_ledger(tmp_path):
    cfg = _cfg(tmp_path)
    init_db(cfg.database_path)
    con = sqlite3.connect(cfg.database_path)
    try:
        con.execute(
            "INSERT INTO orders(order_id,idempotency_key,created_at,updated_at,mode,status,market_id,token_id,side,limit_price,stake_usdc,quantity) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("o1", "o1", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "paper", "filled", "m1", "t1", "BUY_YES", 0.5, 50, 100),
        )
        con.execute(
            "INSERT INTO fills(fill_id,order_id,idempotency_key,created_at,market_id,token_id,side,fill_price,quantity,gross_notional_usdc,fee_usdc,slippage_usdc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("f1", "o1", "f1", "2026-01-01T00:00:01Z", "m1", "t1", "BUY_YES", 0.5, 100, 50, 0, 0),
        )
        con.execute(
            "INSERT INTO positions(position_id,market_id,token_id,side,quantity,average_entry_price,cost_basis_usdc,realised_pnl_usdc,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("p1", "m1", "t1", "BUY_YES", 100, 0.5, 50, 0, "2026-01-01T00:00:01Z"),
        )
        con.execute(
            "INSERT INTO cash_ledger(cash_entry_id,idempotency_key,created_at,entry_type,amount_usdc,cash_balance_after_usdc,order_id,fill_id,note) VALUES (?,?,?,?,?,?,?,?,?)",
            ("c1", "c1", "2026-01-01T00:00:00Z", "stake", -50, 950, "o1", "f1", "filled"),
        )
        con.commit()
    finally:
        con.close()

    snap = portfolio_snapshot(cfg)
    assert snap["cash"] == 950
    assert snap["position_count"] == 1
    assert snap["total_exposure"] == 50

    report = reconciliation_report(cfg)
    assert report["balanced"] is True
    assert report["positions_tie_to_fills"] is True
