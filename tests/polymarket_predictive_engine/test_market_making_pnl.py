import csv

from pytest import approx

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.market_making_pnl import (
    breakeven_markout,
    estimate_adverse_markout,
    evaluate_market_making,
    market_making_pnl,
)


def test_net_pnl_nets_spread_against_adverse_selection():
    pnl = market_making_pnl(half_spread=0.02, adverse_markout=0.01, fills=10, shares_per_fill=10)
    assert pnl["net_edge_per_share"] == approx(0.01)
    assert pnl["gross_capture_usdc"] == approx(2.0)
    assert pnl["adverse_cost_usdc"] == approx(1.0)
    assert pnl["net_pnl_usdc"] == approx(1.0)
    assert pnl["profitable"] is True


def test_unprofitable_when_adverse_exceeds_half_spread():
    pnl = market_making_pnl(half_spread=0.005, adverse_markout=0.02, fills=10, shares_per_fill=10)
    assert pnl["net_edge_per_share"] < 0
    assert pnl["profitable"] is False


def test_breakeven_markout_includes_rebate():
    assert breakeven_markout(0.02) == approx(0.02)
    assert breakeven_markout(0.02, maker_rebate_rate=0.01, mid=0.5) == approx(0.025)


def _write(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def test_estimate_adverse_markout_from_history(tmp_path):
    cfg = EngineConfig(raw={"paths": {"output_root": str(tmp_path / "outputs")}}, path=tmp_path / "cfg.yaml")
    snaps = [
        {"token_id": "t", "timestamp": "2026-01-01T00:00:00Z", "midpoint": "0.50"},
        {"token_id": "t", "timestamp": "2026-01-01T00:30:00Z", "midpoint": "0.52"},
        {"token_id": "t", "timestamp": "2026-01-01T01:00:00Z", "midpoint": "0.51"},
    ]
    _write(tmp_path / "outputs" / "polymarket_training" / "historical_price_snapshots.csv", snaps,
           ["token_id", "timestamp", "midpoint"])
    est = estimate_adverse_markout(cfg, horizon_steps=1)
    assert est["observations"] == 2
    assert est["median_markout"] == approx(0.015)        # |0.02| and |0.01| -> median 0.015


def test_evaluate_market_making_ranks_by_net_edge(tmp_path):
    cfg = EngineConfig(raw={"paths": {"output_root": str(tmp_path / "outputs")}}, path=tmp_path / "cfg.yaml")
    books = [
        {"asset_id": "WIDE", "collected_at_utc": "2026-06-24T00:00:00Z", "market": "m1", "best_bid": "0.47", "best_ask": "0.53"},
        {"asset_id": "TIGHT", "collected_at_utc": "2026-06-24T00:00:00Z", "market": "m2", "best_bid": "0.499", "best_ask": "0.501"},
    ]
    _write(tmp_path / "outputs" / "polymarket_training" / "websocket_market_features.csv", books,
           ["asset_id", "collected_at_utc", "market", "best_bid", "best_ask"])
    rows, summary = evaluate_market_making(cfg, adverse_markout=0.01, fills_per_market=10, shares_per_fill=10)
    assert summary["markets_evaluated"] == 2
    assert summary["profitable_markets"] == 1            # only the wide spread beats 0.01 adverse
    assert rows[0]["asset_id"] == "WIDE"                 # ranked first by net P&L
    assert summary["live_trading"] is False
