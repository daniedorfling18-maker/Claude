from __future__ import annotations

from pathlib import Path

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.price_action_model import train_price_action_model
from polymarket_predictive_engine.utils import read_csv_rows, write_csv


def _cfg(tmp_path: Path) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "price_action_model": {
                "minimum_rows": 40,
                "minimum_validation_rows": 20,
                "minimum_selected_train_trades": 8,
                "minimum_selected_validation_trades": 6,
                "minimum_selected_train_roi": 0.02,
                "minimum_selected_validation_roi": 0.02,
                "minimum_selected_train_win_rate": 0.55,
                "minimum_selected_validation_win_rate": 0.55,
                "minimum_selected_validation_roi_ci_low": -0.05,
                "minimum_expected_roi_to_trade": 0.02,
                "probability_threshold_grid": [0.50, 0.55, 0.60],
                "bootstrap_iterations": 100,
                "l2": 0.25,
            },
            "price_action_microstructure": {
                "lookback_observations": 1,
                "max_rows_per_token": 500,
            },
        },
        path=tmp_path / "cfg.yaml",
    )


def _event(i: int, *, split: str, profitable: bool, token: str | None = None) -> dict[str, str]:
    entry_bid = 0.49
    entry_ask = 0.50
    exit_bid = 0.58 if profitable else 0.47
    stake = 10.0
    quantity = stake / entry_ask
    pnl = (exit_bid - entry_ask) * quantity
    signal = 0.08 if profitable else -0.03
    return {
        "split": split,
        "token_id": token or f"token-{i % 10}",
        "market_slug": f"market-{i % 14}",
        "question": "Will this market reprice up?",
        "family": "crypto_btc_updown_daily",
        "outcome": "Up",
        "entry_time_utc": f"2026-07-01T00:{i % 60:02d}:00Z",
        "entry_bid": f"{entry_bid:.4f}",
        "entry_ask": f"{entry_ask:.4f}",
        "entry_midpoint": "0.4950",
        "entry_spread": "0.0100",
        "relative_spread": "0.0200",
        "lookback_observations": "1",
        "bid_move_abs": f"{signal:.4f}",
        "mid_move_abs": f"{signal:.4f}",
        "ask_move_abs": f"{signal:.4f}",
        "spread_change": "0.0000",
        "net_buy_events": "3" if profitable else "-2",
        "net_buy_size": "150" if profitable else "-60",
        "current_side": "BUY" if profitable else "SELL",
        "current_price_change_size": "50" if profitable else "20",
        "exit_time_utc": f"2026-07-01T00:{(i + 1) % 60:02d}:30Z",
        "exit_bid": f"{exit_bid:.4f}",
        "exit_reason": "take_profit" if profitable else "fixed_horizon",
        "pnl_usdc": f"{pnl:.6f}",
        "roi": f"{pnl / stake:.6f}",
        "stake_usdc": f"{stake:.2f}",
        "observations_to_exit": "1",
    }


def _ws_row(i: int, *, profitable_pattern: bool, token: str, bid: float | None = None) -> dict[str, str]:
    if bid is None:
        bid = 0.49 + (0.08 if profitable_pattern else -0.03)
    ask = bid + 0.01
    return {
        "collected_at_utc": f"2026-07-01T01:{i:02d}:00Z",
        "source_timestamp": str(1000 + i),
        "asset_id": token,
        "market_slug": f"live-market-{token}",
        "question": "BTC up?",
        "category": "crypto_btc_updown_daily",
        "selection": "Up",
        "event_type": "price_change",
        "best_bid": f"{bid:.4f}",
        "best_ask": f"{ask:.4f}",
        "midpoint": f"{(bid + ask) / 2:.4f}",
        "spread": "0.0100",
        "price_change_side": "BUY" if profitable_pattern else "SELL",
        "price_change_size": "100" if profitable_pattern else "10",
    }


def _round_trip_event(i: int, *, split: str, profitable: bool) -> dict[str, str]:
    entry = 0.30 if profitable else 0.70
    exit_bid = 0.37 if profitable else 0.66
    stake = 10.0
    quantity = stake / entry
    pnl = (exit_bid - entry) * quantity
    return {
        "split": split,
        "signal_cohort": "price_action_scout|profit_sprint|macro_rates",
        "family": "macro_rates",
        "market_slug": f"round-trip-market-{i % 16}",
        "outcome": "Yes",
        "token_id": f"round-trip-token-{i}",
        "entry_time_utc": f"2026-07-01T02:{i % 60:02d}:00Z",
        "entry_price": f"{entry:.4f}",
        "stake_usdc": f"{stake:.2f}",
        "quantity": f"{quantity:.8f}",
        "observations": "12",
        "latest_time_utc": f"2026-07-01T02:{(i + 2) % 60:02d}:00Z",
        "latest_bid": f"{exit_bid:.4f}",
        "latest_ask": f"{exit_bid + 0.01:.4f}",
        "latest_midpoint": f"{exit_bid + 0.005:.4f}",
        "latest_spread": "0.0100",
        "max_bid": f"{exit_bid:.4f}",
        "min_bid": f"{min(entry, exit_bid):.4f}",
        "exit_time_utc": f"2026-07-01T02:{(i + 2) % 60:02d}:00Z",
        "exit_price": f"{exit_bid:.4f}",
        "exit_reason": "take_profit" if profitable else "stop_loss",
        "round_trip_status": "closed_take_profit" if profitable else "closed_stop_loss",
        "realized_pnl_usdc": f"{pnl:.6f}",
        "realized_roi": f"{pnl / stake:.6f}",
        "mark_pnl_usdc": f"{pnl:.6f}",
        "mark_roi": f"{pnl / stake:.6f}",
        "take_profit_return": "0.08",
        "stop_loss_return": "0.06",
        "min_profit_usdc": "0.25",
        "settlement_status": "not_settled_price_action_only",
    }


def test_price_action_model_trains_on_future_bid_repricing_and_scores_current_rows(tmp_path):
    cfg = _cfg(tmp_path)
    events = []
    for i in range(40):
        events.append(_event(i, split="train", profitable=i % 2 == 0))
    for i in range(40, 80):
        events.append(_event(i, split="validation", profitable=i % 2 == 0))
    write_csv(cfg.output_root / "polymarket_price_action" / "microstructure_trade_events.csv", events)
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            _ws_row(0, profitable_pattern=False, token="live-loser", bid=0.50),
            _ws_row(1, profitable_pattern=False, token="live-loser"),
            _ws_row(2, profitable_pattern=True, token="live-winner", bid=0.49),
            _ws_row(3, profitable_pattern=True, token="live-winner"),
        ],
    )

    summary = train_price_action_model(cfg)
    current = read_csv_rows(cfg.output_root / "polymarket_price_action" / "price_action_model_current_candidates.csv")
    validation = read_csv_rows(cfg.output_root / "polymarket_price_action" / "price_action_model_validation_predictions.csv")

    assert summary["status"] == "trained"
    assert summary["trading_objective"] == "predict_future_executable_bid_reprices_above_entry_ask"
    assert summary["promotion_ready"] is True
    assert summary["validation_selected"]["selected_roi"] > summary["validation_buy_all_baseline"]["roi"]
    assert summary["chosen_probability_threshold"] >= 0.5
    assert summary["current_rows_scored"] == 2
    assert summary["current_model_candidates"] >= 1
    assert current
    assert float(current[0]["predicted_expected_roi"]) >= 0.02
    assert any(float(row["future_bid_edge"]) > 0 and row["target"] == "1" for row in validation)


def test_price_action_model_blocks_when_validation_does_not_beat_costs(tmp_path):
    cfg = _cfg(tmp_path)
    events = []
    for i in range(40):
        events.append(_event(i, split="train", profitable=i % 2 == 0))
    for i in range(40, 80):
        events.append(_event(i, split="validation", profitable=False))
    write_csv(cfg.output_root / "polymarket_price_action" / "microstructure_trade_events.csv", events)

    summary = train_price_action_model(cfg)

    assert summary["status"] == "trained"
    assert summary["promotion_ready"] is False
    assert summary["current_model_candidates"] == 0
    assert summary["validation_blockers"]


def test_price_action_model_ingests_strict_scout_round_trip_training_events(tmp_path):
    cfg = _cfg(tmp_path)
    events = []
    for i in range(24):
        events.append(_round_trip_event(i, split="train", profitable=i % 2 == 0))
    for i in range(24, 48):
        events.append(_round_trip_event(i, split="validation", profitable=i % 2 == 0))
    write_csv(cfg.output_root / "polymarket_price_action" / "price_action_scout_round_trip_evidence.csv", events)

    summary = train_price_action_model(cfg)
    validation = read_csv_rows(cfg.output_root / "polymarket_price_action" / "price_action_model_validation_predictions.csv")

    assert summary["status"] == "trained"
    assert summary["training_events"] == 48
    assert summary["training_event_sources"]["microstructure_trade_event"]["prepared_rows"] == 0
    assert summary["training_event_sources"]["price_action_scout_round_trip"]["prepared_rows"] == 48
    assert summary["training_event_sources"]["price_action_scout_round_trip"]["positive_targets"] == 24
    assert summary["validation_selected"]["selected_roi"] > summary["validation_buy_all_baseline"]["roi"]
    assert any(row["target"] == "1" for row in validation)


def test_price_action_model_keeps_unsplit_round_trip_rows_when_microstructure_has_explicit_split(tmp_path):
    cfg = _cfg(tmp_path)
    micro_events = []
    for i in range(20):
        micro_events.append(_event(i, split="train", profitable=False))
    for i in range(20, 40):
        micro_events.append(_event(i, split="validation", profitable=False))
    before_boundary = _round_trip_event(100, split="", profitable=True)
    before_boundary.pop("split")
    before_boundary["entry_time_utc"] = "2026-07-01T00:10:30Z"
    after_boundary = _round_trip_event(101, split="", profitable=False)
    after_boundary.pop("split")
    after_boundary["entry_time_utc"] = "2026-07-01T00:50:30Z"
    write_csv(cfg.output_root / "polymarket_price_action" / "microstructure_trade_events.csv", micro_events)
    write_csv(cfg.output_root / "polymarket_price_action" / "price_action_scout_round_trip_evidence.csv", [before_boundary, after_boundary])

    summary = train_price_action_model(cfg)

    assert summary["training_event_sources"]["price_action_scout_round_trip"]["prepared_rows"] == 2
    assert summary["training_events"] == summary["train_rows"] + summary["validation_rows"]
    assert summary["train_rows"] == 21
    assert summary["validation_rows"] == 21
    assert summary["validation_gap"]["state"] == "needs_positive_validation_examples"
    assert "fed" in summary["validation_gap"]["collection_queries"]


def test_price_action_model_uses_observed_open_marked_round_trips_as_mark_to_bid_labels(tmp_path):
    cfg = _cfg(tmp_path)
    rows = []
    for i in range(24):
        row = _round_trip_event(i, split="train", profitable=i % 2 == 0)
        row["round_trip_status"] = "open_marked"
        row["exit_price"] = ""
        rows.append(row)
    for i in range(24, 48):
        row = _round_trip_event(i, split="validation", profitable=i % 2 == 0)
        row["round_trip_status"] = "open_marked"
        row["exit_price"] = ""
        rows.append(row)
    write_csv(cfg.output_root / "polymarket_price_action" / "price_action_scout_round_trip_evidence.csv", rows)

    summary = train_price_action_model(cfg)

    assert summary["status"] == "trained"
    assert summary["training_event_sources"]["price_action_scout_round_trip"]["prepared_rows"] == 48
    assert summary["training_event_sources"]["price_action_scout_round_trip"]["positive_targets"] == 24


def test_price_action_model_ignores_open_marked_round_trips_with_too_few_observations(tmp_path):
    cfg = _cfg(tmp_path)
    rows = [_round_trip_event(i, split="train", profitable=True) for i in range(4)]
    for row in rows:
        row["round_trip_status"] = "open_marked"
        row["exit_price"] = ""
        row["observations"] = "2"
    write_csv(cfg.output_root / "polymarket_price_action" / "price_action_scout_round_trip_evidence.csv", rows)

    summary = train_price_action_model(cfg)

    assert summary["status"] == "insufficient_data"
    assert summary["training_event_sources"]["price_action_scout_round_trip"]["raw_rows"] == 4
    assert summary["training_event_sources"]["price_action_scout_round_trip"]["prepared_rows"] == 0
