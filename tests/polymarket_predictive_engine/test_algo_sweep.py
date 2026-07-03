from __future__ import annotations

from pathlib import Path

from pytest import approx

from polymarket_predictive_engine.algo.sweep import (
    DECISION_INSUFFICIENT_EVENTS,
    DECISION_NO_CANDIDATE,
    DECISION_VALIDATED,
    run_algo_sweep,
)
from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.utils import csv_columns, read_json, write_csv


def _cfg(tmp_path: Path, sweep: dict | None = None) -> EngineConfig:
    raw: dict = {"paths": {"output_root": str(tmp_path / "outputs")}}
    if sweep is not None:
        raw["algo_sweep"] = sweep
    return EngineConfig(raw=raw, path=tmp_path / "cfg.yaml")


def _quote(asset: str, when: str, bid: float, ask: float, imbalance: float = 0.8) -> dict:
    return {
        "source_timestamp": when,
        "collected_at_utc": when,
        "market": f"mkt_{asset}",
        "asset_id": asset,
        "market_slug": f"slug-{asset}",
        "question": f"Q {asset}?",
        "category": "sports_other",
        "close_time": "2026-07-03T00:00:00Z",
        "best_bid": bid,
        "best_ask": ask,
        "midpoint": round((bid + ask) / 2, 6),
        "spread": round(ask - bid, 6),
        "book_imbalance": imbalance,
        "top_bid_size": 500,
        "top_ask_size": 100,
    }


def _profitable_pattern(asset: str, t0: str, t1: str, t2: str) -> list[dict]:
    """Join bid at 0.50, fill when the ask crosses down, mark up to 0.55."""
    return [
        _quote(asset, t0, 0.50, 0.51),
        _quote(asset, t1, 0.48, 0.50),
        _quote(asset, t2, 0.55, 0.56),
    ]


def _fixture_events(tmp_path: Path) -> Path:
    rows = (
        _profitable_pattern("a1", "2026-07-02T10:00:00Z", "2026-07-02T10:05:00Z", "2026-07-02T10:10:00Z")
        + _profitable_pattern("a2", "2026-07-02T10:01:00Z", "2026-07-02T10:06:00Z", "2026-07-02T10:11:00Z")
        + _profitable_pattern("v1", "2026-07-02T11:00:00Z", "2026-07-02T11:05:00Z", "2026-07-02T11:10:00Z")
    )
    path = tmp_path / "features.csv"
    write_csv(path, rows)
    return path


def _multi_strategy_fixture_events(tmp_path: Path) -> Path:
    rows = [
        # Tight-spread training lead: emits at t0, fills at t1, marks up at t2.
        _quote("tight-train", "2026-07-02T10:00:00Z", 0.50, 0.51, imbalance=0.8),
        _quote("tight-train", "2026-07-02T10:01:00Z", 0.48, 0.50, imbalance=0.8),
        _quote("tight-train", "2026-07-02T10:02:00Z", 0.57, 0.58, imbalance=0.8),
        # Bid-momentum training lead: low imbalance prevents the tight-spread strategy,
        # then a bid jump emits, fills, and marks up.
        _quote("bid-train", "2026-07-02T10:03:00Z", 0.20, 0.21, imbalance=0.4),
        _quote("bid-train", "2026-07-02T10:04:00Z", 0.25, 0.26, imbalance=0.4),
        _quote("bid-train", "2026-07-02T10:05:00Z", 0.25, 0.25, imbalance=0.4),
        _quote("bid-train", "2026-07-02T10:06:00Z", 0.45, 0.46, imbalance=0.4),
        _quote("quiet-train", "2026-07-02T10:07:00Z", 0.30, 0.35, imbalance=0.4),
        _quote("quiet-train", "2026-07-02T10:08:00Z", 0.30, 0.35, imbalance=0.4),
        # Validation window: bid-momentum repeats out of sample.
        _quote("bid-val", "2026-07-02T11:00:00Z", 0.20, 0.21, imbalance=0.4),
        _quote("bid-val", "2026-07-02T11:01:00Z", 0.25, 0.26, imbalance=0.4),
        _quote("bid-val", "2026-07-02T11:02:00Z", 0.25, 0.25, imbalance=0.4),
        _quote("bid-val", "2026-07-02T11:03:00Z", 0.35, 0.36, imbalance=0.4),
    ]
    path = tmp_path / "multi_strategy_features.csv"
    write_csv(path, rows)
    return path


SWEEP_SETTINGS = {
    "minimum_events": 5,
    "minimum_train_fills": 2,
    "minimum_validation_fills": 1,
    "train_fraction": 0.7,
    "tight_spread_maximums": [0.02],
    "minimum_book_imbalances": [0.55, 0.95],
}


def test_sweep_selects_on_train_and_validates_out_of_sample(tmp_path: Path):
    cfg = _cfg(tmp_path, sweep=SWEEP_SETTINGS)
    path = _fixture_events(tmp_path)

    summary = run_algo_sweep(cfg, features_input=path)

    # 9 events, train_fraction 0.7 -> first 6 events (a1+a2 patterns) train,
    # last 3 (v1 pattern) validation.
    assert summary["train_events"] == 6
    assert summary["validation_events"] == 3
    assert summary["combos_tested"] == 2
    assert summary["decision"] == DECISION_VALIDATED

    selected = summary["selected"]
    assert selected["minimum_book_imbalance"] == approx(0.55)
    assert selected["train_fills"] == 2
    # Each fill: 2 shares at 0.50 (1 USDC), marked to 0.55 -> +0.10 per position.
    assert selected["train_pnl_usdc"] == approx(0.20)
    assert selected["validation_fills"] == 1
    assert selected["validation_pnl_usdc"] == approx(0.10)

    by_imbalance = {combo["minimum_book_imbalance"]: combo for combo in summary["combos"]}
    # The 0.95-imbalance combo never fires (fixture imbalance is 0.8).
    assert by_imbalance[0.95]["train_fills"] == 0
    assert by_imbalance[0.95]["train_candidate"] is False
    assert by_imbalance[0.95]["selected"] is False

    assert summary["paper_trading_invoked"] is False
    assert summary["live_trading_invoked"] is False
    written = read_json(cfg.output_root / "polymarket_algo" / "algo_sweep_summary.json")
    assert written["decision"] == DECISION_VALIDATED
    assert (cfg.output_root / "polymarket_algo" / "algo_sweep_combos.csv").exists()


def test_sweep_runs_configured_strategy_grids_and_selects_global_best(tmp_path: Path):
    cfg = _cfg(
        tmp_path,
        sweep={
            "minimum_events": 10,
            "minimum_train_fills": 1,
            "minimum_validation_fills": 1,
            "train_fraction": 0.7,
            "strategies": {
                "tight_spread_join_bid_shadow": {
                    "tight_spread_maximum": [0.02],
                    "minimum_book_imbalance": [0.55],
                },
                "bid_momentum_tight_shadow": {
                    "min_bid_move": [0.01],
                    "tight_spread_maximum": [0.02],
                },
            },
        },
    )
    path = _multi_strategy_fixture_events(tmp_path)

    summary = run_algo_sweep(cfg, features_input=path)

    assert summary["decision"] == DECISION_VALIDATED
    assert summary["combos_tested"] == 2
    assert summary["train_candidates"] == 2
    assert summary["strategies"] == ["bid_momentum_tight_shadow", "tight_spread_join_bid_shadow"]

    selected = summary["selected"]
    assert selected["strategy"] == "bid_momentum_tight_shadow"
    assert selected["params"] == '{"min_bid_move":0.01,"tight_spread_maximum":0.02}'
    assert selected["train_fills"] == 1
    assert selected["validation_fills"] == 1
    assert selected["train_pnl_usdc"] == approx(0.8)
    assert selected["validation_pnl_usdc"] == approx(0.4)
    assert selected["selected"] is True

    by_strategy = summary["by_strategy"]
    assert set(by_strategy) == {"tight_spread_join_bid_shadow", "bid_momentum_tight_shadow"}
    assert by_strategy["bid_momentum_tight_shadow"]["decision"] == DECISION_VALIDATED
    assert by_strategy["bid_momentum_tight_shadow"]["selected"]["strategy"] == "bid_momentum_tight_shadow"
    assert by_strategy["tight_spread_join_bid_shadow"]["selected"]["strategy"] == "tight_spread_join_bid_shadow"
    assert by_strategy["tight_spread_join_bid_shadow"]["train_candidates"] == 1

    combos = read_json(cfg.output_root / "polymarket_algo" / "algo_sweep_summary.json")["combos"]
    assert {combo["strategy"] for combo in combos} == {"tight_spread_join_bid_shadow", "bid_momentum_tight_shadow"}
    assert "strategy" in csv_columns(cfg.output_root / "polymarket_algo" / "algo_sweep_combos.csv")
    assert "params" in csv_columns(cfg.output_root / "polymarket_algo" / "algo_sweep_combos.csv")


def test_sweep_fails_closed_without_enough_events(tmp_path: Path):
    cfg = _cfg(tmp_path, sweep={**SWEEP_SETTINGS, "minimum_events": 50})
    path = _fixture_events(tmp_path)
    summary = run_algo_sweep(cfg, features_input=path)
    assert summary["decision"] == DECISION_INSUFFICIENT_EVENTS
    assert summary["selected"] == {}


def test_sweep_reports_no_candidate_when_nothing_fills_enough(tmp_path: Path):
    cfg = _cfg(tmp_path, sweep={**SWEEP_SETTINGS, "minimum_book_imbalances": [0.95]})
    path = _fixture_events(tmp_path)
    summary = run_algo_sweep(cfg, features_input=path)
    assert summary["decision"] == DECISION_NO_CANDIDATE
    assert summary["selected"] == {}
    assert summary["train_candidates"] == 0


def test_algo_sweep_cli_defaults_to_tradable_probe_strategy(tmp_path: Path, monkeypatch):
    from polymarket_predictive_engine import cli

    cfg = _cfg(tmp_path, sweep=SWEEP_SETTINGS)
    calls: list[tuple[str, str | None]] = []

    monkeypatch.setattr(cli, "load_config", lambda _path: cfg)
    monkeypatch.setattr(
        cli,
        "run_algo_sweep",
        lambda _cfg, strategy_name, features_input=None: calls.append((strategy_name, features_input))
        or {"status": "ok", "paper_trading_invoked": False, "live_trading_invoked": False},
    )

    assert cli.main(["algo-sweep", "--config", str(tmp_path / "cfg.yaml")]) == 0
    assert calls == [("tight_spread_join_bid_shadow", None)]
