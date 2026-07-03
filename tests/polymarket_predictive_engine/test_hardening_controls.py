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


def _book(price: float) -> dict[str, float]:
    return {
        "best_ask": price,
        "top_ask_size": 1000.0,
        "ask_depth_1pct": 1000.0,
        "ask_depth_5pct": 1000.0,
        "websocket_quote_age_seconds": 30.0,
    }


def test_data_quality_fails_closed_without_raw_snapshots(tmp_path):
    cfg = _cfg(tmp_path)
    with pytest.raises(RuntimeError, match="Data-quality blockers"):
        data_quality(cfg)
    _, summary = data_quality(cfg, allow_warnings=True)
    assert summary["blocker_count"] == 1
    assert summary["training_allowed"] is False


def test_paper_trade_runs_gated_broker_and_blocks_when_not_ready(tmp_path):
    # paper_trade now runs the gated paper broker (not an unsafe placeholder). With no readiness
    # evidence it must block entries on the readiness gate rather than place any order. The broker
    # may still monitor exits while blocked so open paper probes can close on their risk policy.
    cfg = _cfg(tmp_path)
    summary = paper_trade(cfg)
    assert summary["status"] == "monitoring_exits_only_readiness_gate_blocked"
    assert summary["approved_for_paper_trading"] is False
    assert summary["entry_gate_blocked"] is True
    assert summary["exit_monitoring_when_blocked"] is True
    assert summary["orders_filled"] == 0


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
                **_book(0.55),
            },
        {"bankroll": 1000, "cash": 1000},
    )
    assert decision["approved"] is True
    assert decision["stake_usdc"] > 0
    assert decision["quantity"] > 0
    assert 0 < decision["limit_price"] < 1


def test_risk_decision_caps_stake_to_depth_aware_execution_capacity(tmp_path):
    cfg = _cfg(tmp_path)
    decision = risk_decision(
        cfg,
        {
            "market_id": "m-depth",
            "token_id": "t-depth",
            "edge": 0.2,
            "calibrated_probability": 0.8,
            "executable_price": 0.50,
            "best_ask": 0.50,
            "spread": 0.01,
            "liquidity": 1000,
            "time_to_close_minutes": 60,
            "top_ask_size": 1,
            "ask_depth_1pct": 3,
            "ask_depth_5pct": 4,
        },
        {"bankroll": 1000, "cash": 1000},
    )

    assert decision["approved"] is True
    assert decision["stake_usdc"] <= 1.5
    assert decision["risk_checks"]["execution_depth_stake_cap_usdc"] == 1.5
    assert decision["risk_checks"]["execution_cost_estimate"]["status"] in {"top_of_book_fill", "within_1pct_depth"}


def test_fast_market_risk_overrides_allow_tiny_paper_probe(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.raw["risk"]["minimum_confidence"] = 0.65
    cfg.raw["risk"]["fast_market_overrides"] = {
        "enabled": True,
        "minimum_edge": 0.005,
        "minimum_confidence": 0.05,
        "maximum_spread": 0.06,
        "minimum_liquidity": 5,
        "minimum_time_to_close_minutes": 0.25,
        "maximum_stake_usdc": 2,
    }
    decision = risk_decision(
        cfg,
        {
            "market_id": "m-fast",
            "market_slug": "xrp-updown-5m-1782489000",
            "token_id": "t-fast",
            "edge": 0.01,
            "alpha_probability": 0.578,
                "executable_price": 0.495,
                "spread": 0.01,
                "liquidity": 5,
                "time_to_close_minutes": 3,
                "max_stake_usdc": 2,
                **_book(0.495),
            },
        {"bankroll": 1000, "cash": 1000},
    )

    assert decision["approved"] is True
    assert decision["risk_profile"] == "fast_market_paper_probe"
    assert decision["stake_usdc"] <= 0.25

def test_promoted_fast_market_can_step_up_to_promoted_cap(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.raw["risk"]["minimum_confidence"] = 0.65
    cfg.raw["risk"]["fast_market_overrides"] = {
        "enabled": True,
        "minimum_edge": 0.005,
        "minimum_confidence": 0.05,
        "maximum_spread": 0.06,
        "minimum_liquidity": 5,
        "minimum_time_to_close_minutes": 0.25,
        "maximum_stake_usdc": 2,
        "promoted_maximum_stake_usdc": 5,
    }
    base_signal = {
        "market_id": "m-fast",
        "market_slug": "btc-updown-5m-1782489000",
        "token_id": "t-fast",
        "edge": 0.03,
        "alpha_probability": 0.60,
        "executable_price": 0.50,
        "spread": 0.01,
        "liquidity": 1000,
        "time_to_close_minutes": 3,
        **_book(0.50),
    }

    probationary = risk_decision(
        cfg,
        {**base_signal, "cohort_promotion_status": "probationary", "max_stake_usdc": 2},
        {"bankroll": 1000, "cash": 1000},
    )
    promoted = risk_decision(
        cfg,
        {**base_signal, "cohort_promotion_status": "promoted"},
        {"bankroll": 1000, "cash": 1000},
    )

    assert probationary["approved"] is True
    assert probationary["stake_usdc"] == 2
    assert probationary["risk_profile"] == "fast_market_paper_probe"
    assert promoted["approved"] is True
    assert promoted["stake_usdc"] == 5
    assert promoted["risk_profile"] == "fast_market_promoted"

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



def test_features_v2_blocks_without_clean_labels(tmp_path):
    from polymarket_predictive_engine.features_v2 import build_features_v2

    cfg = _cfg(tmp_path)
    with pytest.raises(RuntimeError, match="no clean resolved binary labels"):
        build_features_v2(cfg, allow_unlabelled_research=False)

    assert build_features_v2(cfg, allow_unlabelled_research=True) == []


def test_promotion_gate_requires_forward_paper_evidence_for_live(tmp_path):
    import json

    from polymarket_predictive_engine.readiness import paper_live_promotion_gate

    cfg = _cfg(tmp_path)
    cfg.raw["governance_thresholds"] = {
        "min_resolved_labels": 0,
        "target_resolved_labels": 0,
        "min_forward_paper_trades": 1,
    }

    cfg.governance_root.mkdir(parents=True, exist_ok=True)
    cfg.live_approval_file.parent.mkdir(parents=True, exist_ok=True)
    cfg.live_approval_file.write_text("approved", encoding="utf-8")

    (cfg.governance_root / "market_relative_validation_summary.json").write_text(
        json.dumps(
            {
                "status": "approved",
                "approved_for_paper_trading": True,
                "model_copies_market_midpoint": False,
                "oos": {"sample_size": 10, "markets": 5},
            }
        ),
        encoding="utf-8",
    )

    payload = paper_live_promotion_gate(cfg)

    assert payload["approved_for_live_trading"] is False
    assert payload["forward_paper_evidence"]["present"] is False
    assert any("forward paper evidence" in blocker for blocker in payload["live_blockers"])
