from __future__ import annotations

from pathlib import Path

from pytest import approx

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.portfolio import portfolio_snapshot
from polymarket_predictive_engine.storage import connect_db
from polymarket_predictive_engine.utils import now_utc, read_json


def _cfg(tmp_path: Path) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {
                "output_root": str(tmp_path / "outputs"),
                "database_path": str(tmp_path / "paper.sqlite"),
            },
            "paper_trading": {"starting_cash": 1000},
            "risk": {"bankroll": 1000},
        },
        path=tmp_path / "config.yaml",
    )


def _insert_position(
    con,
    *,
    position_id: str,
    market_id: str,
    token_id: str,
    category: str,
    correlation_key: str,
    quantity: float,
    average_entry_price: float,
    mark: float,
) -> None:
    timestamp = now_utc()
    cost_basis = quantity * average_entry_price
    con.execute(
        """
        INSERT INTO positions(
            position_id, market_id, token_id, side, category, correlation_key,
            quantity, average_entry_price, cost_basis_usdc, realised_pnl_usdc,
            status, updated_at
        ) VALUES (?, ?, ?, 'BUY_YES', ?, ?, ?, ?, ?, 0, 'open', ?)
        """,
        (
            position_id,
            market_id,
            token_id,
            category,
            correlation_key,
            quantity,
            average_entry_price,
            cost_basis,
            timestamp,
        ),
    )
    con.execute(
        """
        INSERT INTO market_snapshots(
            snapshot_id, idempotency_key, collected_at, market_id, token_id,
            best_bid, best_ask, midpoint, spread, liquidity, raw_payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0.02, 1000, '{}')
        """,
        (
            f"snapshot-{position_id}",
            f"snapshot-key-{position_id}",
            timestamp,
            market_id,
            token_id,
            mark - 0.01,
            mark + 0.01,
            mark,
        ),
    )


def test_portfolio_snapshot_reports_var_and_correlated_exposure(tmp_path):
    cfg = _cfg(tmp_path)
    con = connect_db(cfg.database_path)
    try:
        with con:
            _insert_position(
                con,
                position_id="pos-1",
                market_id="market-fed-1",
                token_id="token-fed-yes",
                category="macro_rates",
                correlation_key="fed-july-2026",
                quantity=10,
                average_entry_price=0.50,
                mark=0.40,
            )
            _insert_position(
                con,
                position_id="pos-2",
                market_id="market-fed-2",
                token_id="token-fed-no",
                category="macro_rates",
                correlation_key="fed-july-2026",
                quantity=20,
                average_entry_price=0.25,
                mark=0.30,
            )
            _insert_position(
                con,
                position_id="pos-3",
                market_id="market-tennis-1",
                token_id="token-tennis-yes",
                category="tennis",
                correlation_key="tennis-final-2026",
                quantity=10,
                average_entry_price=0.80,
                mark=0.72,
            )
    finally:
        con.close()

    snapshot = portfolio_snapshot(cfg)
    risk_state = read_json(cfg.output_root / "polymarket_portfolio" / "risk_state.json")
    risk = risk_state["portfolio_risk"]

    assert snapshot["position_count"] == 3
    assert risk["open_positions"] == 3
    assert risk["marked_positions"] == 3
    assert risk["unmarked_positions"] == 0
    assert risk["total_cost_usdc"] == 18.0
    assert risk["exposure_by_correlation_key"][0] == {
        "key": "fed-july-2026",
        "cost_basis_usdc": 10.0,
    }
    assert risk["exposure_by_category"][0] == {
        "key": "macro_rates",
        "cost_basis_usdc": 10.0,
    }
    # Returns are [-20%, +20%, -10%]. Historical 95% VaR is 19%;
    # CVaR tail contains the -20% observation.
    assert risk["var_95_return"] == approx(0.19)
    assert risk["cvar_95_return"] == approx(0.20)
    assert risk["var_95_usdc"] == approx(3.42)
    assert risk["cvar_95_usdc"] == approx(3.60)
    assert risk["worst_position_return_pct"] == approx(-20.0)
    assert risk["paper_trading_invoked"] is False
    assert risk["live_trading_invoked"] is False
    assert risk_state["portfolio_risk"] == risk
