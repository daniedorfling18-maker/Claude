from __future__ import annotations

from pathlib import Path

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.price_action_microstructure import build_microstructure_edge_lab
from polymarket_predictive_engine.utils import read_csv_rows, write_csv


def _cfg(tmp_path: Path) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "price_action_microstructure": {
                "enabled": True,
                "stake_usdc": 10,
                "lookback_observations": 1,
                "min_future_observations": 1,
                "max_forward_observations": 1,
                "train_fraction": 0.5,
                "take_profit_return": 0.04,
                "stop_loss_return": 0.06,
                "min_profit_usdc": 0.01,
                "min_total_trades": 4,
                "min_validation_trades": 2,
                "min_train_roi": 0,
                "min_validation_roi": 0.03,
                "min_validation_win_rate": 0.5,
                "max_single_token_positive_pnl_share": 1.0,
                "max_relative_spread": 0.05,
                "max_spread_grid": [0.01],
                "min_move_grid": [0.02],
                "min_buy_pressure_grid": [99],
                "min_spread_compression_grid": [0.99],
            },
        },
        path=tmp_path / "cfg.yaml",
    )


def _ws_row(
    i: int,
    bid: float,
    *,
    token: str = "eth-token",
    category: str = "crypto",
    slug: str = "eth-updown-test",
    question: str = "ETH up?",
) -> dict[str, str]:
    ask = bid + 0.01
    return {
        "collected_at_utc": f"2026-06-30T10:{i:02d}:00Z",
        "source_timestamp": str(1000 + i),
        "asset_id": token,
        "market_slug": slug,
        "question": question,
        "category": category,
        "selection": "Up",
        "event_type": "price_change",
        "best_bid": f"{bid:.4f}",
        "best_ask": f"{ask:.4f}",
        "midpoint": f"{(bid + ask) / 2:.4f}",
        "spread": "0.01",
        "price_change_side": "BUY",
        "price_change_size": "100",
    }


def test_microstructure_lab_promotes_bid_momentum_rule_on_validation_bid_ask_profit(tmp_path):
    cfg = _cfg(tmp_path)
    bids = [0.30 + 0.04 * i for i in range(12)]
    write_csv(cfg.output_root / "polymarket_training" / "websocket_market_features.csv", [_ws_row(i, bid) for i, bid in enumerate(bids)])

    summary = build_microstructure_edge_lab(cfg)
    rules = read_csv_rows(cfg.output_root / "polymarket_price_action" / "microstructure_rule_evidence.csv")
    current = read_csv_rows(cfg.output_root / "polymarket_price_action" / "microstructure_current_candidates.csv")

    assert summary["validation_pass_rules"] >= 1
    assert summary["current_candidates"] >= 1
    assert rules[0]["validation_pass"] == "True"
    assert rules[0]["rule_family"] == "bid_momentum_tight"
    assert float(rules[0]["validation_roi"]) > 0.03
    assert current[0]["shadow_only"] == "True"


def test_microstructure_lab_rejects_rule_when_validation_cannot_clear_spread(tmp_path):
    cfg = _cfg(tmp_path)
    # Training has upward moves, validation flattens, so future bids do not beat the entry ask.
    bids = [0.30, 0.34, 0.38, 0.42, 0.46, 0.50, 0.51, 0.52, 0.53, 0.54]
    write_csv(cfg.output_root / "polymarket_training" / "websocket_market_features.csv", [_ws_row(i, bid) for i, bid in enumerate(bids)])

    summary = build_microstructure_edge_lab(cfg)
    rules = read_csv_rows(cfg.output_root / "polymarket_price_action" / "microstructure_rule_evidence.csv")

    assert summary["validation_pass_rules"] == 0
    assert rules[0]["validation_pass"] == "False"
    assert rules[0]["status"] in {
        "insufficient_validation_trades",
        "negative_or_insufficient_validation_roi",
        "validation_win_rate_below_threshold",
    }


def test_microstructure_lab_validates_rules_inside_each_market_family(tmp_path):
    cfg = _cfg(tmp_path)
    sports_bids = [0.30, 0.34, 0.38, 0.42, 0.46, 0.50]
    crypto_bids = [0.30, 0.34, 0.38, 0.42, 0.46, 0.44]
    rows = [
        _ws_row(
            i,
            bid,
            token="world-cup-token",
            category="sports_other",
            slug="world-cup-final-test",
            question="World Cup final winner?",
        )
        for i, bid in enumerate(sports_bids)
    ] + [
        _ws_row(
            i,
            bid,
            token="btc-token",
            category="crypto",
            slug="btc-updown-test",
            question="BTC up?",
        )
        for i, bid in enumerate(crypto_bids)
    ]
    write_csv(cfg.output_root / "polymarket_training" / "websocket_market_features.csv", rows)

    summary = build_microstructure_edge_lab(cfg)
    family_rules = read_csv_rows(cfg.output_root / "polymarket_price_action" / "microstructure_family_rule_evidence.csv")
    current = read_csv_rows(cfg.output_root / "polymarket_price_action" / "microstructure_current_candidates.csv")

    sports_bid_rules = [
        row
        for row in family_rules
        if row["market_family"] == "sports_other" and row["rule_family"] == "bid_momentum_tight"
    ]
    crypto_bid_rules = [
        row
        for row in family_rules
        if row["market_family"] == "crypto" and row["rule_family"] == "bid_momentum_tight"
    ]

    assert summary["family_validation_pass_rules"] >= 1
    assert sports_bid_rules
    assert sports_bid_rules[0]["validation_pass"] == "True"
    assert sports_bid_rules[0]["signal_cohort"] == "price_action_microstructure|sports_other|bid_momentum_tight"
    assert crypto_bid_rules
    assert crypto_bid_rules[0]["validation_pass"] == "False"
    assert any(row["signal_cohort"] == "price_action_microstructure|sports_other|bid_momentum_tight" for row in current)


def test_microstructure_lab_discovers_family_exit_policy_that_clears_spread(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.raw["price_action_microstructure"].update(
        {
            "take_profit_return": 0.20,
            "stop_loss_return": 0.50,
            "max_forward_observations": 2,
            "min_total_trades": 4,
            "min_validation_trades": 2,
            "min_move_grid": [0.035],
            "exit_policy_grid": [
                {
                    "exit_policy_id": "quick_3pct_1obs",
                    "take_profit_return": 0.03,
                    "stop_loss_return": 0.20,
                    "max_forward_observations": 1,
                    "min_profit_usdc": 0.01,
                }
            ],
        }
    )
    pattern = [0.30, 0.34, 0.37, 0.30]
    bids = pattern * 5 + [0.34]
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            _ws_row(
                i,
                bid,
                token="world-cup-exit-token",
                category="sports_other",
                slug="world-cup-exit-test",
                question="World Cup exit-policy test?",
            )
            for i, bid in enumerate(bids)
        ],
    )

    summary = build_microstructure_edge_lab(cfg)
    exit_rows = read_csv_rows(cfg.output_root / "polymarket_price_action" / "microstructure_exit_policy_evidence.csv")
    current = read_csv_rows(cfg.output_root / "polymarket_price_action" / "microstructure_current_candidates.csv")
    quick_passes = [
        row
        for row in exit_rows
        if row["exit_policy_id"] == "quick_3pct_1obs"
        and row["market_family"] == "sports_other"
        and row["rule_family"] == "bid_momentum_tight"
        and row["validation_pass"] == "True"
    ]

    assert summary["exit_policy_validation_pass_rules"] >= 1
    assert quick_passes
    assert quick_passes[0]["signal_cohort"] == "price_action_microstructure|sports_other|bid_momentum_tight|exit=quick_3pct_1obs"
    assert any(row["exit_policy_id"] == "quick_3pct_1obs" for row in current)
