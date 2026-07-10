"""WO-49 flow-toxicity conditioning tests."""
from __future__ import annotations

from pathlib import Path

import yaml

from polymarket_predictive_engine import maker_carry_study
from polymarket_predictive_engine.cli import COMMANDS
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.flow_toxicity import build_flow_toxicity
from polymarket_predictive_engine.utils import read_csv_rows, write_csv


def _config(tmp_path: Path):
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    raw["flow_toxicity"] = {
        "enabled": True,
        "volume_bucket_usd": 100,
        "buckets": 10,
        "markout_horizon_minutes": 5,
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def _features(cfg, token: str, points: list[tuple[int, float]]) -> None:
    existing = read_csv_rows(cfg.output_root / "polymarket_training" / "websocket_market_features.csv")
    rows = [*existing, *[{"source_timestamp": stamp, "asset_id": token, "midpoint": price} for stamp, price in points]]
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        rows,
        fieldnames=["source_timestamp", "asset_id", "midpoint"],
    )


def _leaderboard(cfg) -> None:
    write_csv(
        cfg.output_root / "wallet_intelligence" / "leaderboard_history.csv",
        [{"snapshot_date": "2026-07-10", "wallet": "smart1", "rank": "1"}],
        fieldnames=["snapshot_date", "wallet", "rank"],
    )


def test_planted_toxic_flow_scores_above_balanced_flow(tmp_path):
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _features(cfg, "tok-toxic", [(0, 0.5), (10_000, 0.5)])
    _features(cfg, "tok-balanced", [(0, 0.5), (10_000, 0.5)])
    trades = []
    for i in range(20):
        trades.append({"market": "0xtoxic", "asset_id": "tok-toxic", "side": "BUY", "price": 0.5, "size": 100, "timestamp": i})
        side = "BUY" if i % 2 == 0 else "SELL"
        trades.append({"market": "0xbalanced", "asset_id": "tok-balanced", "side": side, "price": 0.5, "size": 100, "timestamp": i})
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        trades,
        fieldnames=["market", "asset_id", "side", "price", "size", "timestamp"],
    )

    summary = build_flow_toxicity(cfg)

    assert summary["status"] == "ok"
    rows = {row["market"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity.csv")}
    assert float(rows["0xtoxic"]["toxicity_score"]) == 1.0
    assert float(rows["0xbalanced"]["toxicity_score"]) == 0.0
    assert float(rows["0xtoxic"]["vpin_raw"]) > float(rows["0xbalanced"]["vpin_raw"])
    assert summary["paper_trading_invoked"] is False
    assert summary["live_trading_invoked"] is False


def test_wallet_tier_markout_split_arithmetic(tmp_path):
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _features(cfg, "tok1", [(0, 0.5), (400, 0.6), (1_000, 0.5), (1_400, 0.4)])
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [
            {"market": "0xcond", "asset_id": "tok1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 100, "counterparty_wallet": "smart1"},
            {"market": "0xcond", "asset_id": "tok1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 1_000, "counterparty_wallet": "crowd1"},
        ],
        fieldnames=["market", "asset_id", "side", "price", "size", "timestamp", "counterparty_wallet"],
    )

    build_flow_toxicity(cfg)

    row = read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity.csv")[0]
    assert float(row["smart_fill_markout"]) == 0.1
    assert float(row["crowd_fill_markout"]) == -0.1
    assert row["smart_fill_count"] == "1"
    assert row["crowd_fill_count"] == "1"


def test_missing_wallet_intelligence_is_tolerated(tmp_path):
    cfg = _config(tmp_path)
    _features(cfg, "tok1", [(0, 0.5), (400, 0.51)])
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [{"market": "0xcond", "asset_id": "tok1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 100}],
        fieldnames=["market", "asset_id", "side", "price", "size", "timestamp"],
    )

    summary = build_flow_toxicity(cfg)

    assert summary["status"] == "ok"
    assert summary["missing_wallet_data"] is True
    row = read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity.csv")[0]
    assert row["crowd_fill_count"] == "1"


def test_quote_sheet_surfaces_toxicity_column_and_rule(tmp_path):
    cfg = _config(tmp_path)
    out = cfg.output_root / "maker_carry"
    write_csv(
        out / "flow_toxicity.csv",
        [{"market": "0xcond", "toxicity_score": 0.95}],
        fieldnames=["market", "toxicity_score"],
    )
    summary = {
        "generated_at_utc": "2026-07-10T00:00:00Z",
        "portfolio_net_carry_usd_per_day": 1.0,
        "portfolio_net_carry_usd_per_month": 30.0,
        "portfolio_capital_usd": 100.0,
        "maker_gates": {"maker_verdict": "insufficient_evidence", "M_A_carry_evidence": {"state": "pending"}, "M_B_adverse_realism": {"state": "pending"}},
        "portfolio": [
            {
                "question": "Synthetic market",
                "condition_id": "0xcond",
                "quote_size_shares": 100,
                "quote_distance": 0.01,
                "capital_usd": 100,
                "net_carry_usd_per_day": 1.0,
                "event_risk_flags": [],
            }
        ],
    }

    maker_carry_study._write_quote_sheet(out, summary, {"min_daily_payout_usd": 1.0})

    sheet = (out / "maker_quote_sheet.md").read_text(encoding="utf-8")
    assert "toxicity" in sheet
    assert "toxicity>0.9" in sheet
    assert "8. Flow toxicity" in sheet


def test_cli_exposes_flow_toxicity():
    assert "flow-toxicity" in COMMANDS
