from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.longshot_bias import build_longshot_bias_scan, scan_longshot_bias_candidates
from polymarket_predictive_engine.utils import read_csv_rows, read_json, write_csv


def _cfg(tmp_path: Path, *, emit_shadow: bool = False) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "risk": {
                "minimum_entry_price": 0.05,
                "maximum_entry_price": 0.90,
            },
            "longshot_bias": {
                "emit_shadow_positions": emit_shadow,
                "min_price": 0.02,
                "max_price": 0.12,
                "min_time_to_close_hours": 168,
                "minimum_liquidity": 500,
                "max_candidates": 10,
            },
            "shadow_cohort_validation": {
                "enabled": True,
                "stake_usdc": 10,
                "candidate_limit_per_cycle": 10,
                "positions_file": "shadow_positions.csv",
                "fills_file": "shadow_fills.csv",
                "summary_file": "shadow_signal_cohort_pnl.json",
            },
        },
        path=tmp_path / "cfg.yaml",
    )


def _write_watchlist(cfg: EngineConfig, rows: list[dict[str, object]]) -> None:
    write_csv(
        cfg.output_root / "polymarket_liquidity_discovery" / "liquidity_watchlist.csv",
        rows,
    )


def _yes_no_market(
    market_id: str,
    *,
    yes_ask: float,
    no_ask: float,
    time_to_close_hours: float = 240,
    liquidity: float = 1000,
    slug: str | None = None,
) -> list[dict[str, object]]:
    slug = slug or f"{market_id}-slug"
    base = {
        "timestamp": "2026-07-03T08:00:00Z",
        "market_id": market_id,
        "market_slug": slug,
        "question": f"Will {market_id} happen?",
        # Derive close_time from the requested time-to-close so the fixture stays
        # valid regardless of the wall-clock date the suite runs on. A hard-coded
        # calendar date silently expired once "now" reached it, recomputing
        # time_to_close as <=0 and dropping the candidate (clock-drift, see #338).
        "close_time": (
            datetime.now(timezone.utc) + timedelta(hours=time_to_close_hours)
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "time_to_close_hours": time_to_close_hours,
        "liquidity": liquidity,
        "family": "macro_economy",
    }
    return [
        {
            **base,
            "outcome": "Yes",
            "token_id": f"{market_id}-yes",
            "best_bid": max(0.001, yes_ask - 0.01),
            "best_ask": yes_ask,
            "midpoint": yes_ask - 0.005,
            "spread": 0.01,
        },
        {
            **base,
            "outcome": "No",
            "token_id": f"{market_id}-no",
            "best_bid": max(0.001, no_ask - 0.01),
            "best_ask": no_ask,
            "midpoint": no_ask - 0.005,
            "spread": 0.01,
        },
    ]


def test_longshot_bias_scan_selects_exact_no_side_candidates(tmp_path):
    cfg = _cfg(tmp_path)
    _write_watchlist(
        cfg,
        [
            *_yes_no_market("macro-longshot", yes_ask=0.08, no_ask=0.89, liquidity=1200),
            *_yes_no_market("tennis-longshot", yes_ask=0.11, no_ask=0.90, liquidity=900, slug="will-sinner-win-obscure-cup"),
            *_yes_no_market("too-expensive-yes", yes_ask=0.18, no_ask=0.83, liquidity=2000),
            *_yes_no_market("too-expensive-no", yes_ask=0.08, no_ask=0.93, liquidity=2000),
        ],
    )

    candidates, summary = scan_longshot_bias_candidates(cfg)

    assert [row["market_id"] for row in candidates] == ["tennis-longshot", "macro-longshot"]
    assert [row["token_id"] for row in candidates] == ["tennis-longshot-no", "macro-longshot-no"]
    assert candidates[0]["outcome"] == "No"
    assert candidates[0]["signal_cohort"].startswith("structural|longshot_no|")
    assert candidates[0]["shadow_trade_candidate"] is True
    assert candidates[0]["shadow_source"] == "longshot_bias"
    assert candidates[0]["executable_price"] == 0.9
    assert candidates[0]["decision_use"] == "shadow_clv_learning_target_not_paper_trade_authorisation"
    assert summary["candidates"] == 2
    assert summary["paper_trading_invoked"] is False
    assert summary["live_trading_invoked"] is False
    assert summary["skip_counts"]["yes_price_above_max"] == 1
    assert summary["skip_counts"]["no_entry_price_above_maximum"] == 1
    assert summary["settings"]["maximum_entry_price"] == 0.9


def test_longshot_bias_excludes_deep_longshot_below_min_and_fast_market(tmp_path):
    cfg = _cfg(tmp_path)
    _write_watchlist(
        cfg,
        [
            *_yes_no_market("too-deep", yes_ask=0.01, no_ask=0.99, liquidity=2000),
            *_yes_no_market("too-fast", yes_ask=0.08, no_ask=0.93, time_to_close_hours=24, liquidity=2000),
            *_yes_no_market("too-thin", yes_ask=0.08, no_ask=0.89, liquidity=100),
        ],
    )

    candidates, summary = scan_longshot_bias_candidates(cfg)

    assert candidates == []
    assert summary["skip_counts"] == {
        "fast_market_excluded": 1,
        "liquidity_below_minimum": 1,
        "yes_price_below_min": 1,
    }


def test_longshot_bias_build_writes_artifacts_and_can_emit_shadow_position(tmp_path):
    cfg = _cfg(tmp_path, emit_shadow=True)
    _write_watchlist(cfg, _yes_no_market("macro-longshot", yes_ask=0.08, no_ask=0.85, liquidity=1200))

    payload = build_longshot_bias_scan(cfg)

    assert payload["candidates"] == 1
    assert payload["emit_shadow_positions"] is True
    assert payload["shadow_update"]["opened_this_cycle"] == 1
    assert payload["paper_trading_invoked"] is False
    assert payload["live_trading_invoked"] is False
    saved = read_json(cfg.output_root / "polymarket_longshot_bias" / "longshot_bias_summary.json")
    rows = read_csv_rows(cfg.output_root / "polymarket_longshot_bias" / "longshot_bias_candidates.csv")
    positions = read_csv_rows(cfg.output_root / "polymarket_shadow" / "shadow_positions.csv")
    assert saved["candidates"] == 1
    assert len(rows) == 1
    assert len(positions) == 1
    assert positions[0]["signal_cohort"].startswith("structural|longshot_no|")
    assert positions[0]["shadow_source"] == "longshot_bias"
    assert float(positions[0]["entry_price"]) <= 0.90


def test_longshot_bias_shadow_emit_refuses_no_side_outside_entry_band(tmp_path):
    cfg = _cfg(tmp_path, emit_shadow=True)
    _write_watchlist(cfg, _yes_no_market("expensive-no", yes_ask=0.08, no_ask=0.94, liquidity=1200))

    payload = build_longshot_bias_scan(cfg)

    assert payload["candidates"] == 0
    assert payload["skip_counts"]["no_entry_price_above_maximum"] == 1
    assert payload["shadow_update"]["opened_this_cycle"] == 0
    assert read_csv_rows(cfg.output_root / "polymarket_longshot_bias" / "longshot_bias_candidates.csv") == []
