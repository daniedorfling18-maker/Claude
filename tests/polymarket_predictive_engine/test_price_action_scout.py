from __future__ import annotations

from pathlib import Path

from pytest import approx

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.price_action_scout import build_price_action_scout
from polymarket_predictive_engine.utils import read_csv_rows, write_csv, write_json


def _cfg(tmp_path: Path) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "price_action_scout": {
                "stake_usdc": 10,
                "min_liquidity": 100,
                "max_spread": 0.04,
                "max_new_entries_per_run": 10,
                "include_profit_sprint_targets": True,
                "include_fast_feedback_liquidity": True,
                "take_profit_return": 0.08,
                "stop_loss_return": 0.06,
                "min_profit_usdc": 0.25,
                "min_closed_trades": 2,
            },
        },
        path=tmp_path / "cfg.yaml",
    )


def _watchlist_row(**overrides: str) -> dict[str, str]:
    row = {
        "timestamp": "2026-06-30T10:00:00Z",
        "market_slug": "eth-updown",
        "question": "Ethereum Up or Down?",
        "outcome": "Up",
        "token_id": "eth-token",
        "family": "crypto_eth_updown_event",
        "best_bid": "0.49",
        "best_ask": "0.50",
        "midpoint": "0.495",
        "spread": "0.01",
        "liquidity": "500",
        "time_to_close_hours": "4",
        "tradable_liquidity_candidate": "True",
        "fast_feedback_liquidity_candidate": "True",
        "fast_feedback_filter_reason": "liquid_tight_book_closes_soon",
    }
    row.update(overrides)
    return row


def _websocket_row(collected_at: str, bid: str, token: str = "eth-token") -> dict[str, str]:
    return {
        "collected_at_utc": collected_at,
        "asset_id": token,
        "market_slug": "eth-updown",
        "selection": "Up",
        "best_bid": bid,
        "best_ask": str(float(bid) + 0.01),
        "midpoint": str(float(bid) + 0.005),
        "spread": "0.01",
    }


def test_price_action_scout_tracks_fast_liquidity_round_trip_to_bid_take_profit(tmp_path):
    cfg = _cfg(tmp_path)
    write_csv(cfg.output_root / "polymarket_liquidity_discovery" / "liquidity_watchlist.csv", [_watchlist_row()])
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            _websocket_row("2026-06-30T10:02:00Z", "0.52"),
            _websocket_row("2026-06-30T10:05:00Z", "0.55"),
        ],
    )

    summary = build_price_action_scout(cfg)
    entries = read_csv_rows(cfg.output_root / "polymarket_price_action" / "price_action_scout_entries.csv")
    rows = read_csv_rows(cfg.output_root / "polymarket_price_action" / "price_action_scout_round_trip_evidence.csv")

    assert summary["new_entries"] == 1
    assert summary["closed_trades"] == 1
    assert entries[0]["source"] == "liquidity_fast_feedback"
    assert rows[0]["round_trip_status"] == "closed_take_profit"
    assert float(rows[0]["exit_price"]) == approx(0.55)
    assert float(rows[0]["realized_pnl_usdc"]) == approx(1.0)
    assert float(rows[0]["realized_roi"]) == approx(0.1)


def test_price_action_scout_joins_profit_sprint_targets_to_watchlist_tokens(tmp_path):
    cfg = _cfg(tmp_path)
    write_csv(
        cfg.output_root / "polymarket_liquidity_discovery" / "liquidity_watchlist.csv",
        [_watchlist_row()],
    )
    write_csv(
        cfg.governance_root / "profit_sprint_targets.csv",
        [
            {
                "target_action": "SHADOW_LABEL_GATE",
                "target_score": "91",
                "market_slug": "eth-updown",
                "outcome": "Up",
                "signal_cohort": "exploratory_historical_rule|crypto_eth_updown_event|outcome=up",
                "edge_lower_bound": "0.04",
                "reason": "Positive edge but category label gate is not satisfied.",
            }
        ],
    )
    write_csv(cfg.output_root / "polymarket_training" / "websocket_market_features.csv", [_websocket_row("2026-06-30T10:05:00Z", "0.46")])

    summary = build_price_action_scout(cfg)
    entries = read_csv_rows(cfg.output_root / "polymarket_price_action" / "price_action_scout_entries.csv")
    rows = read_csv_rows(cfg.output_root / "polymarket_price_action" / "price_action_scout_round_trip_evidence.csv")

    assert summary["new_entries"] == 1
    assert entries[0]["source"] == "profit_sprint_target"
    assert entries[0]["target_action"] == "SHADOW_LABEL_GATE"
    assert entries[0]["token_id"] == "eth-token"
    assert rows[0]["round_trip_status"] == "closed_stop_loss"
    assert float(rows[0]["realized_pnl_usdc"]) == approx(-0.8)


def test_price_action_scout_tracks_current_positive_analogue_targets(tmp_path):
    cfg = _cfg(tmp_path)
    write_json(
        cfg.governance_root / "research_focus.json",
        {
            "generated_at_utc": "2026-06-30T10:00:00Z",
            "price_action_current_positive_analogues": {
                "state": "learning_targets_available",
                "paper_only": True,
                "trade_authorisation": "no_trade_without_governed_price_action_signal",
                "targets": [
                    {
                        "market_slug": "will-the-fed-increase-interest-rates-by-50-bps-after-the-july-2026-meeting",
                        "question": "Will the Fed increase interest rates by 50+ bps after the July 2026 meeting?",
                        "family": "macro_rates",
                        "outcome": "Yes",
                        "token_id": "fed-token",
                        "latest_bid": 0.49,
                        "latest_ask": 0.50,
                        "latest_spread": 0.01,
                        "validation_roi": 0.004,
                        "robust_validation_roi_gap": 0.026,
                    }
                ],
            },
        },
    )
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [_websocket_row("2026-06-30T10:05:00Z", "0.55", token="fed-token")],
    )

    summary = build_price_action_scout(cfg)
    entries = read_csv_rows(cfg.output_root / "polymarket_price_action" / "price_action_scout_entries.csv")
    rows = read_csv_rows(cfg.output_root / "polymarket_price_action" / "price_action_scout_round_trip_evidence.csv")

    assert summary["current_positive_analogue_targets"] == 1
    assert summary["new_entries"] == 1
    assert entries[0]["source"] == "current_positive_analogue"
    assert entries[0]["signal_cohort"] == "price_action_scout|current_positive_analogue|macro_rates"
    assert entries[0]["target_action"] == "FORWARD_SHADOW_ANALOGUE"
    assert "shadow-only" in entries[0]["candidate_reason"]
    assert rows[0]["round_trip_status"] == "closed_take_profit"
    assert float(rows[0]["exit_price"]) == approx(0.55)
    assert float(rows[0]["realized_pnl_usdc"]) == approx(1.0)


def test_price_action_scout_tracks_paper_confirmation_blocker_targets(tmp_path):
    cfg = _cfg(tmp_path)
    write_json(
        cfg.governance_root / "research_focus.json",
        {
            "generated_at_utc": "2026-06-30T10:00:00Z",
            "price_action_paper_confirmation_blockers": {
                "state": "in_band_historical_analogue_gaps",
                "decision_use": "collection_only_not_trade_authorisation",
                "trade_authorisation": "no_trade_until_governed_paper_signal_exists",
                "targets": [
                    {
                        "market_slug": "btc-updown-15m-1783174500",
                        "question": "Bitcoin Up or Down - July 4, 10:15AM-10:30AM ET",
                        "family": "crypto_btc_updown_15m",
                        "outcome": "Up",
                        "token_id": "btc-blocker-token",
                        "latest_bid": 0.49,
                        "latest_ask": 0.50,
                        "latest_spread": 0.01,
                        "historical_analogue_gate": "no_positive_historical_analogue_examples",
                        "historical_analogue_validation_roi": -0.016,
                        "priority_bucket": 3,
                        "decision_use": "in_band_historical_analogue_gap_collection_target",
                        "entry_band_wait": False,
                    }
                ],
            },
        },
    )
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [_websocket_row("2026-06-30T10:05:00Z", "0.55", token="btc-blocker-token")],
    )

    summary = build_price_action_scout(cfg)
    entries = read_csv_rows(cfg.output_root / "polymarket_price_action" / "price_action_scout_entries.csv")
    rows = read_csv_rows(cfg.output_root / "polymarket_price_action" / "price_action_scout_round_trip_evidence.csv")

    assert summary["paper_confirmation_blocker_targets"] == 1
    assert summary["new_entries"] == 1
    assert entries[0]["source"] == "paper_confirmation_blocker"
    assert entries[0]["signal_cohort"] == (
        "price_action_scout|paper_confirmation_blocker|"
        "crypto_btc_updown_15m|no_positive_historical_analogue_examples"
    )
    assert entries[0]["target_action"] == "FORWARD_SHADOW_PAPER_CONFIRMATION_BLOCKER"
    assert "shadow-only" in entries[0]["candidate_reason"]
    assert "historical_analogue_gate=no_positive_historical_analogue_examples" in entries[0]["candidate_reason"]
    assert rows[0]["round_trip_status"] == "closed_take_profit"
    assert float(rows[0]["exit_price"]) == approx(0.55)
    assert float(rows[0]["realized_pnl_usdc"]) == approx(1.0)


def test_price_action_scout_tracks_label_headroom_research_targets(tmp_path):
    cfg = _cfg(tmp_path)
    write_csv(
        cfg.governance_root / "websocket_liquidity_targets.csv",
        [
            {
                "timestamp": "2026-06-30T10:00:00Z",
                "market_slug": "will-fed-cut-rates-in-july",
                "question": "Will the Fed cut rates in July?",
                "outcome": "Yes",
                "token_id": "headroom-token",
                "family": "macro_rates",
                "best_bid": "0.49",
                "best_ask": "0.50",
                "midpoint": "0.495",
                "spread": "0.01",
                "liquidity": "500",
                "feedback_broaden_target": "True",
            },
            {
                "timestamp": "2026-06-30T10:00:00Z",
                "market_slug": "will-fed-cut-rates-by-50bps-in-july",
                "question": "Will the Fed cut rates by 50 bps in July?",
                "outcome": "Yes",
                "token_id": "too-expensive-token",
                "family": "macro_rates",
                "best_bid": "0.98",
                "best_ask": "0.99",
                "midpoint": "0.985",
                "spread": "0.01",
                "liquidity": "500",
                "feedback_broaden_target": "True",
            },
        ],
    )
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [_websocket_row("2026-06-30T10:05:00Z", "0.55", token="headroom-token")],
    )

    summary = build_price_action_scout(cfg)
    entries = read_csv_rows(cfg.output_root / "polymarket_price_action" / "price_action_scout_entries.csv")
    rows = read_csv_rows(cfg.output_root / "polymarket_price_action" / "price_action_scout_round_trip_evidence.csv")

    assert summary["label_headroom_research_targets"] == 1
    assert summary["new_entries"] == 1
    assert entries[0]["source"] == "label_headroom_research"
    assert entries[0]["signal_cohort"] == "price_action_scout|label_headroom|macro_rates"
    assert entries[0]["target_action"] == "FORWARD_SHADOW_HEADROOM"
    assert "model_label_hurdle" in entries[0]["candidate_reason"]
    assert rows[0]["round_trip_status"] == "closed_take_profit"
    assert float(rows[0]["exit_price"]) == approx(0.55)
    assert float(rows[0]["realized_pnl_usdc"]) == approx(1.0)


def test_price_action_scout_rejects_spread_toxic_low_price_books(tmp_path):
    cfg = _cfg(tmp_path)
    write_csv(
        cfg.output_root / "polymarket_liquidity_discovery" / "liquidity_watchlist.csv",
        [
            _watchlist_row(
                token_id="wide-token",
                best_bid="0.08",
                best_ask="0.10",
                midpoint="0.09",
                spread="0.02",
                liquidity="1000",
                relative_spread="0.2222222222",
            )
        ],
    )

    summary = build_price_action_scout(cfg)
    entries = read_csv_rows(cfg.output_root / "polymarket_price_action" / "price_action_scout_entries.csv")

    assert summary["new_entries"] == 0
    assert summary["ledger_entries"] == 0
    assert entries == []


def test_price_action_scout_dedupes_persistent_entries_across_cycles(tmp_path):
    cfg = _cfg(tmp_path)
    write_csv(cfg.output_root / "polymarket_liquidity_discovery" / "liquidity_watchlist.csv", [_watchlist_row()])
    write_csv(cfg.output_root / "polymarket_training" / "websocket_market_features.csv", [_websocket_row("2026-06-30T10:05:00Z", "0.51")])

    first = build_price_action_scout(cfg)
    second = build_price_action_scout(cfg)
    entries = read_csv_rows(cfg.output_root / "polymarket_price_action" / "price_action_scout_entries.csv")

    assert first["new_entries"] == 1
    assert second["new_entries"] == 0
    assert len(entries) == 1


def test_price_action_scout_excludes_existing_entries_that_violate_current_policy(tmp_path):
    cfg = _cfg(tmp_path)
    write_csv(
        cfg.output_root / "polymarket_price_action" / "price_action_scout_entries.csv",
        [
            {
                "source": "liquidity_fast_feedback",
                "signal_cohort": "price_action_scout|fast_liquidity|esports_match",
                "family": "esports_match",
                "market_slug": "wide-book",
                "outcome": "Longshot",
                "token_id": "wide-token",
                "first_seen_at_utc": "2026-06-30T10:00:00Z",
                "entry_price": "0.10",
                "stake_usdc": "10",
                "quantity": "100",
                "liquidity": "1000",
                "spread": "0.02",
            }
        ],
    )
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [_websocket_row("2026-06-30T10:05:00Z", "0.08", token="wide-token")],
    )

    summary = build_price_action_scout(cfg)

    assert summary["ledger_entries"] == 1
    assert summary["policy_eligible_entries"] == 0
    assert summary["policy_excluded_entries"] == 1
    assert summary["round_trip_candidates"] == 0
