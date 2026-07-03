from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.smart_flow_clv import build_smart_flow_clv
from polymarket_predictive_engine.utils import read_csv_rows, read_json, write_csv


AS_OF = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)


def _cfg(tmp_path: Path):
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path / "inputs")
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    raw["smart_flow_clv"] = {
        "enabled": True,
        "fills_input": str(tmp_path / "inputs" / "polymarket" / "public_wallet_fills.csv"),
        "minimum_final_samples_per_wallet": 2,
        "bootstrap_iterations": 200,
        "bootstrap_seed": 7,
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def test_smart_flow_clv_scores_wallets_against_later_lines(tmp_path):
    cfg = _cfg(tmp_path)
    fills_path = tmp_path / "inputs" / "polymarket" / "public_wallet_fills.csv"
    features_path = cfg.output_root / "polymarket_training" / "websocket_market_features.csv"
    write_csv(
        fills_path,
        [
            {
                "wallet": "0xAlpha000000000000000000000000000000000001",
                "tx_hash": "a1",
                "token_id": "tok_a1",
                "entry_price": 0.4,
                "quantity": 10,
                "timestamp": "2026-07-03T09:00:00Z",
                "close_time": "2026-07-03T10:00:00Z",
                "category": "macro_rates",
            },
            {
                "wallet": "0xAlpha000000000000000000000000000000000001",
                "tx_hash": "a2",
                "token_id": "tok_a2",
                "entry_price": 0.41,
                "quantity": 10,
                "timestamp": "2026-07-03T09:00:00Z",
                "close_time": "2026-07-03T10:00:00Z",
                "category": "macro_rates",
            },
            {
                "wallet": "0xBeta000000000000000000000000000000000002",
                "tx_hash": "b1",
                "token_id": "tok_b1",
                "entry_price": 0.5,
                "quantity": 5,
                "timestamp": "2026-07-03T09:00:00Z",
                "close_time": "2026-07-03T10:00:00Z",
                "category": "sports_other",
            },
            {
                "wallet": "0xBeta000000000000000000000000000000000002",
                "tx_hash": "b2",
                "token_id": "tok_b2",
                "entry_price": 0.52,
                "quantity": 5,
                "timestamp": "2026-07-03T09:00:00Z",
                "close_time": "2026-07-03T10:00:00Z",
                "category": "sports_other",
            },
            {
                "wallet": "0xIgnored00000000000000000000000000000003",
                "tx_hash": "s1",
                "token_id": "tok_s1",
                "entry_price": 0.5,
                "timestamp": "2026-07-03T09:00:00Z",
                "side": "sell",
            },
        ],
    )
    write_csv(
        features_path,
        [
            {"asset_id": "tok_a1", "source_timestamp": "2026-07-03T09:59:00Z", "best_bid": 0.49, "best_ask": 0.51},
            {"asset_id": "tok_a2", "source_timestamp": "2026-07-03T09:59:00Z", "best_bid": 0.5, "best_ask": 0.52},
            {"asset_id": "tok_b1", "source_timestamp": "2026-07-03T09:59:00Z", "best_bid": 0.43, "best_ask": 0.45},
            {"asset_id": "tok_b2", "source_timestamp": "2026-07-03T09:59:00Z", "best_bid": 0.45, "best_ask": 0.47},
        ],
    )

    summary = build_smart_flow_clv(cfg, as_of=AS_OF)

    assert summary["fills_seen"] == 5
    assert summary["fills_scored"] == 4
    assert summary["final_line_fills"] == 4
    assert summary["positions_skipped"]["not_buy_or_missing_required_fields"] == 1
    assert summary["paper_trading_invoked"] is False
    assert summary["live_trading_invoked"] is False

    wallets = {row["wallet"]: row for row in summary["wallets"]}
    alpha = "0xalpha000000000000000000000000000000000001"
    beta = "0xbeta000000000000000000000000000000000002"
    assert wallets[alpha]["clv_evidence"] == "positive_clv_evidence"
    assert wallets[alpha]["recommended_action"] == "watch_shadow_only"
    assert wallets[alpha]["mean_final_clv"] == 0.1
    assert wallets[beta]["clv_evidence"] == "negative_clv_evidence"
    assert wallets[beta]["recommended_action"] == "suppress_following"
    assert summary["positive_wallets"] == [alpha]

    written = read_json(cfg.governance_root / "smart_flow_clv.json")
    rows = read_csv_rows(cfg.governance_root / "smart_flow_clv_positions.csv")
    assert written["positive_wallets"] == [alpha]
    assert len(rows) == 4


def test_smart_flow_clv_empty_input_fails_closed(tmp_path):
    cfg = _cfg(tmp_path)

    summary = build_smart_flow_clv(cfg, as_of=AS_OF)

    assert summary["status"] == "ok"
    assert summary["fills_seen"] == 0
    assert summary["fills_scored"] == 0
    assert summary["positive_wallets"] == []
    assert summary["watchlist"] == []
    assert summary["paper_trading_invoked"] is False
    assert summary["live_trading_invoked"] is False
