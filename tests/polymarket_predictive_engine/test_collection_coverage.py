from __future__ import annotations

from pathlib import Path

from polymarket_predictive_engine.cli import COMMANDS
from polymarket_predictive_engine.collection_coverage import build_collection_coverage
from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.utils import read_csv_rows, write_csv


def _cfg(tmp_path: Path) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "collection_coverage": {"pre_close_window_minutes": 30},
        },
        path=tmp_path / "cfg.yaml",
    )


def _write_fixture(cfg: EngineConfig) -> None:
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            {
                "asset_id": "covered-token",
                "source_timestamp": "2026-07-03T11:20:00Z",
                "category": "fed",
                "question": "Will the Fed cut interest rates in July 2026?",
                "market_slug": "fed-cut-covered",
            },
            {
                "asset_id": "covered-token",
                "source_timestamp": "2026-07-03T11:25:00Z",
                "category": "fed",
                "question": "Will the Fed cut interest rates in July 2026?",
                "market_slug": "fed-cut-covered",
            },
            {
                "asset_id": "uncovered-token",
                "source_timestamp": "2026-07-03T10:00:00Z",
                "category": "fed",
                "question": "Will the Fed cut interest rates in July 2026?",
                "market_slug": "fed-cut-uncovered",
            },
        ],
    )
    write_csv(
        cfg.output_root / "polymarket_shadow" / "shadow_positions.csv",
        [
            {
                "shadow_position_id": "pos-covered",
                "token_id": "covered-token",
                "close_time": "2026-07-03T11:30:00Z",
                "category": "fed",
                "question": "Will the Fed cut interest rates in July 2026?",
                "market_slug": "fed-cut-covered",
                "signal_cohort": "macro_rates",
                "status": "closed",
            },
            {
                "shadow_position_id": "pos-uncovered",
                "token_id": "uncovered-token",
                "close_time": "2026-07-03T11:30:00Z",
                "category": "fed",
                "question": "Will the Fed cut interest rates in July 2026?",
                "market_slug": "fed-cut-uncovered",
                "signal_cohort": "macro_rates",
                "status": "open",
            },
        ],
    )


def test_collection_coverage_reports_missing_pre_close_positions(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write_fixture(cfg)

    payload = build_collection_coverage(cfg)

    assert payload["status"] == "ok"
    assert payload["quote_rows_seen"] == 3
    assert payload["quote_assets_seen"] == 2
    assert payload["positions_with_pre_close_quote"] == 1
    assert payload["positions_missing_pre_close_quote"] == 1
    assert payload["missing_pre_close_positions"] == [
        {
            "shadow_position_id": "pos-uncovered",
            "family": "macro_rates",
            "signal_cohort": "macro_rates",
            "market_slug": "fed-cut-uncovered",
            "token_id": "uncovered-token",
            "close_time": "2026-07-03T11:30:00Z",
            "reason": "missing_pre_close_quote",
        }
    ]
    assert payload["missing_by_reason"] == {"missing_pre_close_quote": 1}
    assert payload["missing_by_family"] == {"macro_rates": 1}
    assert payload["families"] == [
        {
            "family": "macro_rates",
            "quote_rows": 3,
            "distinct_assets": 2,
            "assets_with_gap_samples": 1,
            "first_quote_timestamp": "2026-07-03T10:00:00Z",
            "last_quote_timestamp": "2026-07-03T11:25:00Z",
            "median_gap_seconds": 300.0,
        }
    ]
    assert payload["paper_trading_invoked"] is False
    assert payload["live_trading_invoked"] is False

    missing_csv = read_csv_rows(cfg.governance_root / "collection_coverage_missing_positions.csv")
    assert [row["shadow_position_id"] for row in missing_csv] == ["pos-uncovered"]


def test_collection_coverage_cli_is_registered() -> None:
    assert "collection-coverage" in COMMANDS
