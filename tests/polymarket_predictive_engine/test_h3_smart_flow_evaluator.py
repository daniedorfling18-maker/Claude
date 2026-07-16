from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from pytest import approx

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.h3_smart_flow_evaluator import (
    REGISTRATION_AT,
    _formal_evaluation,
    _settings,
    evaluate_h3_smart_flow,
)
from polymarket_predictive_engine.utils import read_csv_rows, write_csv


def _config(tmp_path: Path, h3: dict | None = None) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {
                "data_root": str(tmp_path),
                "output_root": str(tmp_path / "outputs"),
                "database_path": str(tmp_path / "work" / "paper.sqlite"),
            },
            "h3_smart_flow": {"enabled": True, **(h3 or {})},
        },
        path=tmp_path / "config.yaml",
    )


def _seed_one_fill(cfg: EngineConfig) -> None:
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [
            {
                "trade_id": "trade-early",
                "market": "market-1",
                "asset_id": "token-1",
                "wallet": "0xabc",
                "side": "BUY",
                "price": 0.40,
                "size": 10,
                "timestamp": "2026-07-13T00:00:00Z",
                "title": "Will the tennis player win?",
                "market_slug": "tennis-player-win",
                "event_slug": "tennis-event",
                "outcome": "Yes",
                "outcome_index": 0,
            },
            {
                "trade_id": "trade-later-same-unit",
                "market": "market-1",
                "asset_id": "token-1",
                "wallet": "0xabc",
                "side": "BUY",
                "price": 0.42,
                "size": 3,
                "timestamp": "2026-07-13T01:00:00Z",
            },
        ],
        fieldnames=[
            "trade_id",
            "market",
            "asset_id",
            "wallet",
            "side",
            "price",
            "size",
            "timestamp",
            "title",
            "market_slug",
            "event_slug",
            "outcome",
            "outcome_index",
        ],
    )
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            {
                "market": "market-1",
                "asset_id": "token-1",
                "source_timestamp": "2026-07-13T23:30:00Z",
                "collected_at_utc": "2026-07-13T23:30:01Z",
                "close_time": "2026-07-14T00:00:00Z",
                "best_bid": 0.55,
                "best_ask": 0.56,
                "midpoint": 0.99,
                "category": "sports",
                "question": "Will the tennis player win?",
                "market_slug": "tennis-player-win",
            }
        ],
        fieldnames=[
            "market",
            "asset_id",
            "source_timestamp",
            "collected_at_utc",
            "close_time",
            "best_bid",
            "best_ask",
            "midpoint",
            "category",
            "question",
            "market_slug",
        ],
    )
    write_csv(
        cfg.output_root / "wallet_intelligence" / "leaderboard_history.csv",
        [
            {
                "snapshot_date": "2026-07-12",
                "snapshot_at_utc": "2026-07-12T23:00:00Z",
                "wallet": "0xabc",
                "rank": 4,
            }
        ],
        fieldnames=["snapshot_date", "snapshot_at_utc", "wallet", "rank"],
    )
    write_csv(
        cfg.output_root / "wallet_intelligence" / "holders_history.csv",
        [
            {
                "snapshot_date": "2026-07-12",
                "snapshot_at_utc": "2026-07-12T23:30:00Z",
                "market": "market-1",
                "token": "token-1",
                "wallet": "0xabc",
                "amount": 100,
            }
        ],
        fieldnames=["snapshot_date", "snapshot_at_utc", "market", "token", "wallet", "amount"],
    )


def test_clock_advance_grades_bid_not_midpoint_and_freezes_append_only_row(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    _seed_one_fill(cfg)

    before = evaluate_h3_smart_flow(
        cfg,
        as_of=datetime(2026, 7, 13, 23, 59, 59, tzinfo=timezone.utc),
    )
    assert before["status"] == "collecting"
    assert before["final_fills"] == 0
    assert before["coverage"]["market_not_closed"] == 1
    assert before["coverage"]["duplicate_wallet_token_day"] == 1

    after = evaluate_h3_smart_flow(
        cfg,
        as_of=datetime(2026, 7, 14, 0, 0, 1, tzinfo=timezone.utc),
    )
    assert after["status"] == "collecting"
    assert after["formal_evaluation_started"] is False
    assert after["final_fills"] == 1
    rows = read_csv_rows(cfg.output_root / "h3_smart_flow" / "h3_final_fills.csv")
    assert rows[0]["final_bid"] == "0.55"
    assert rows[0]["gross_clv_per_share"] == "0.15"
    assert rows[0]["net_clv_per_share"] == str(round(0.15 - 0.012 - 0.012375 - 0.005 - 0.005, 8))
    assert rows[0]["rank_cohort"] == "rank:1_10"
    assert rows[0]["top_holder_observed"] == "True"
    assert rows[0]["paper_trading_invoked"] == "False"

    # A later rerun with changed source data cannot revise an already frozen
    # final observation.
    features_path = cfg.output_root / "polymarket_training" / "websocket_market_features.csv"
    changed = read_csv_rows(features_path)
    changed[0]["best_bid"] = "0.80"
    write_csv(features_path, changed, fieldnames=list(changed[0]))
    rerun = evaluate_h3_smart_flow(
        cfg,
        as_of=datetime(2026, 7, 14, 1, 0, 0, tzinfo=timezone.utc),
    )
    assert rerun["new_final_fills_appended"] == 0
    assert read_csv_rows(cfg.output_root / "h3_smart_flow" / "h3_final_fills.csv")[0]["final_bid"] == "0.55"


def _final_rows(*, positive: bool) -> list[dict]:
    rows: list[dict] = []
    for index in range(100):
        validation_index = index - 60
        if positive or index < 60:
            value = 0.03 + (index % 3) * 0.001
        else:
            value = 0.03 if validation_index % 2 == 0 else -0.03
        rows.append(
            {
                "trade_id": f"trade-{index:03d}",
                "wallet": "0xedge",
                "market": f"market-{index % 20:02d}",
                "entry_timestamp_utc": _iso_for_index(index),
                "price_cohort": "price:middle_0.20_0.80",
                "rank_cohort": "",
                "top_holder_observed": False,
                "market_family": f"family-{index % 4}",
                "net_clv_per_share": value,
            }
        )
    return rows


def _iso_for_index(index: int) -> str:
    return (REGISTRATION_AT + timedelta(hours=index + 1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_planted_edge_passes_only_on_untouched_validation_and_null_is_suppressed(tmp_path: Path) -> None:
    settings = _settings(_config(tmp_path))

    cohorts, passing, split = _formal_evaluation(_final_rows(positive=True), settings)
    middle = next(row for row in cohorts if row["cohort"] == "price:middle_0.20_0.80")
    assert split["discovery_fills"] == 60
    assert split["validation_fills"] == 40
    assert middle["validation_fills"] == 40
    assert middle["validation_markets"] == 20
    assert middle["cluster_bootstrap_ci_low_90"] > 0
    assert middle["bh_fdr_pass"] is True
    assert middle["maximum_positive_family_share"] <= 0.35
    assert middle["support_gate_pass"] is True
    assert "price:middle_0.20_0.80" in passing

    null_cohorts, null_passing, _ = _formal_evaluation(_final_rows(positive=False), settings)
    null_middle = next(row for row in null_cohorts if row["cohort"] == "price:middle_0.20_0.80")
    assert null_middle["support_gate_pass"] is False
    assert null_passing == []


def test_calendar_stop_stays_sealed_until_clock_crosses_registered_boundary(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    deadline = REGISTRATION_AT + timedelta(days=90)

    before = evaluate_h3_smart_flow(cfg, as_of=deadline - timedelta(seconds=1))
    assert before["status"] == "collecting"
    assert before["formal_evaluation_started"] is False

    after = evaluate_h3_smart_flow(cfg, as_of=deadline)
    assert after["status"] == "suppressed"
    assert after["formal_evaluation_started"] is True
    assert after["stop_reason"] == "calendar_deadline"
    assert after["passing_cohorts"] == []


def test_configuration_cannot_widen_registered_h3_contract(tmp_path: Path) -> None:
    settings = _settings(
        _config(
            tmp_path,
            {
                "entry_min": 0.0,
                "entry_max": 0.99,
                "stop_fills": 1,
                "stop_days": 1,
                "final_quote_max_age_seconds": 999999,
                "intelligence_max_age_seconds": 999999,
                "minimum_validation_fills": 1,
                "minimum_validation_markets": 1,
                "minimum_cohort_fills": 1,
                "fdr_alpha": 0.99,
                "maximum_family_concentration": 0.99,
                "fixed_exit_cost_per_share": 0.0,
                "adverse_cost_per_share": 0.0,
                "bootstrap_iterations": 1,
            },
        )
    )
    assert settings["entry_min"] == 0.05
    assert settings["entry_max"] == 0.90
    assert settings["stop_fills"] == 100
    assert settings["stop_days"] == 90
    assert settings["final_quote_max_age_seconds"] == 3600
    assert settings["intelligence_max_age_seconds"] == 129600
    assert settings["minimum_validation_fills"] == 30
    assert settings["minimum_validation_markets"] == 10
    assert settings["minimum_cohort_fills"] == 20
    assert settings["fdr_alpha"] == approx(0.10)
    assert settings["maximum_family_concentration"] == approx(0.35)
    assert settings["fixed_exit_cost_per_share"] == approx(0.005)
    assert settings["adverse_cost_per_share"] == approx(0.005)
    assert settings["bootstrap_iterations"] == 1000
