from __future__ import annotations

from pathlib import Path

from pytest import approx

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.strategy_v2_evidence import build_strategy_v2_forward_evidence
from polymarket_predictive_engine.utils import read_csv_rows, write_csv


def _cfg(tmp_path: Path) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "strategy_v2": {
                "forward_evidence_stake_usdc": 10,
                "promotion_min_candidates": 2,
                "promotion_min_settled": 1,
                "promotion_min_roi": 0.03,
                "promotion_min_monthly_run_rate_usdc": 20,
            },
        },
        path=tmp_path / "cfg.yaml",
    )


def test_strategy_v2_forward_evidence_marks_candidate_from_entry_to_latest(tmp_path):
    cfg = _cfg(tmp_path)
    write_csv(
        cfg.output_root / "polymarket_strategy_v2" / "anchored_edge_persistence_log.csv",
        [
            {
                "logged_at_utc": "2026-06-30T10:00:00Z",
                "family": "macro_rates",
                "market_slug": "fed-july",
                "outcome": "Yes",
                "status": "shadow_candidate",
                "executable_price": "0.20",
                "risk_adjusted_anchor_edge": "0.10",
                "anchor_fair_probability": "0.30",
            },
            {
                "logged_at_utc": "2026-06-30T12:00:00Z",
                "family": "macro_rates",
                "market_slug": "fed-july",
                "outcome": "Yes",
                "status": "shadow_candidate",
                "executable_price": "0.30",
                "risk_adjusted_anchor_edge": "0.09",
                "anchor_fair_probability": "0.30",
            },
        ],
    )

    summary = build_strategy_v2_forward_evidence(cfg)
    rows = read_csv_rows(cfg.output_root / "polymarket_strategy_v2" / "strategy_v2_forward_evidence.csv")
    cohorts = read_csv_rows(cfg.output_root / "polymarket_strategy_v2" / "strategy_v2_cohort_forward_evidence.csv")

    assert summary["unique_forward_candidates"] == 1
    assert rows[0]["signal_cohort"] == "strategy_v2|macro_rates"
    assert float(rows[0]["quantity"]) == approx(50.0)
    assert float(rows[0]["mark_pnl_usdc"]) == approx(5.0)
    assert float(rows[0]["mark_roi"]) == approx(0.5)
    assert float(cohorts[0]["total_mark_pnl_usdc"]) == approx(5.0)
    assert cohorts[0]["paper_review_candidate"] == "False"
    assert cohorts[0]["status"] == "collect_resolved_forward_evidence"


def test_strategy_v2_evidence_marks_latest_rejection_without_opening_new_trade(tmp_path):
    cfg = _cfg(tmp_path)
    write_csv(
        cfg.output_root / "polymarket_strategy_v2" / "anchored_edge_persistence_log.csv",
        [
            {
                "logged_at_utc": "2026-06-30T10:00:00Z",
                "family": "sports_other",
                "market_slug": "portugal-r16",
                "outcome": "Yes",
                "status": "shadow_candidate",
                "executable_price": "0.70",
                "risk_adjusted_anchor_edge": "0.08",
            },
            {
                "logged_at_utc": "2026-06-30T13:00:00Z",
                "family": "sports_other",
                "market_slug": "portugal-r16",
                "outcome": "Yes",
                "status": "rejected",
                "blockers": "liquidity_below_strategy_v2_limit",
                "executable_price": "0.65",
                "risk_adjusted_anchor_edge": "0.04",
            },
        ],
    )

    summary = build_strategy_v2_forward_evidence(cfg)
    rows = read_csv_rows(cfg.output_root / "polymarket_strategy_v2" / "strategy_v2_forward_evidence.csv")
    cohorts = read_csv_rows(cfg.output_root / "polymarket_strategy_v2" / "strategy_v2_cohort_forward_evidence.csv")

    assert summary["current_shadow_candidates"] == 0
    assert rows[0]["latest_status"] == "rejected"
    assert rows[0]["latest_blockers"] == "liquidity_below_strategy_v2_limit"
    assert float(rows[0]["mark_pnl_usdc"]) < 0
    assert cohorts[0]["current_shadow_candidates"] == "0"


def test_strategy_v2_evidence_never_promotes_mark_to_market_without_resolution(tmp_path):
    cfg = _cfg(tmp_path)
    write_csv(
        cfg.output_root / "polymarket_strategy_v2" / "anchored_edge_persistence_log.csv",
        [
            {
                "logged_at_utc": "2026-06-30T10:00:00Z",
                "family": "macro_rates",
                "market_slug": "fed-july",
                "outcome": "Yes",
                "status": "shadow_candidate",
                "executable_price": "0.20",
                "risk_adjusted_anchor_edge": "0.10",
            },
            {
                "logged_at_utc": "2026-06-30T12:00:00Z",
                "family": "macro_rates",
                "market_slug": "fed-july",
                "outcome": "Yes",
                "status": "shadow_candidate",
                "executable_price": "0.99",
                "risk_adjusted_anchor_edge": "0.09",
            },
            {
                "logged_at_utc": "2026-06-30T10:00:00Z",
                "family": "macro_rates",
                "market_slug": "fed-sept",
                "outcome": "Yes",
                "status": "shadow_candidate",
                "executable_price": "0.20",
                "risk_adjusted_anchor_edge": "0.10",
            },
            {
                "logged_at_utc": "2026-06-30T12:00:00Z",
                "family": "macro_rates",
                "market_slug": "fed-sept",
                "outcome": "Yes",
                "status": "shadow_candidate",
                "executable_price": "0.40",
                "risk_adjusted_anchor_edge": "0.09",
            },
        ],
    )

    summary = build_strategy_v2_forward_evidence(cfg)
    cohorts = read_csv_rows(cfg.output_root / "polymarket_strategy_v2" / "strategy_v2_cohort_forward_evidence.csv")

    assert summary["terminal_mark_proxy_candidates"] == 1
    assert summary["resolved_candidates"] == 0
    assert summary["paper_review_candidates"] == 0
    assert cohorts[0]["paper_review_candidate"] == "False"
    assert cohorts[0]["status"] == "collect_resolved_forward_evidence"


def test_strategy_v2_evidence_backfills_token_id_from_current_candidates(tmp_path):
    cfg = _cfg(tmp_path)
    write_csv(
        cfg.output_root / "polymarket_strategy_v2" / "anchored_edge_candidates.csv",
        [
            {
                "family": "macro_rates",
                "market_slug": "fed-july",
                "outcome": "Yes",
                "token_id": "fed-token",
            }
        ],
    )
    write_csv(
        cfg.output_root / "polymarket_strategy_v2" / "anchored_edge_persistence_log.csv",
        [
            {
                "logged_at_utc": "2026-06-30T10:00:00Z",
                "family": "macro_rates",
                "market_slug": "fed-july",
                "outcome": "Yes",
                "status": "shadow_candidate",
                "executable_price": "0.20",
                "risk_adjusted_anchor_edge": "0.10",
            },
            {
                "logged_at_utc": "2026-06-30T10:10:00Z",
                "family": "macro_rates",
                "market_slug": "fed-july",
                "outcome": "Yes",
                "status": "shadow_candidate",
                "executable_price": "0.21",
                "risk_adjusted_anchor_edge": "0.09",
            },
        ],
    )

    build_strategy_v2_forward_evidence(cfg)
    rows = read_csv_rows(cfg.output_root / "polymarket_strategy_v2" / "strategy_v2_forward_evidence.csv")

    assert rows[0]["token_id"] == "fed-token"
