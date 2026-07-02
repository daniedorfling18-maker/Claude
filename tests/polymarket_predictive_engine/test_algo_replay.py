from __future__ import annotations

from pathlib import Path

import pytest

from polymarket_predictive_engine.algo import registry as registry_module
from polymarket_predictive_engine.algo.replay import iter_quote_events, run_replay
from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.execution.intents import OrderIntent
from polymarket_predictive_engine.utils import read_json, write_csv


def _cfg(tmp_path: Path, algo: dict | None = None) -> EngineConfig:
    raw = {"paths": {"output_root": str(tmp_path / "outputs")}}
    if algo is not None:
        raw["algo"] = algo
    return EngineConfig(raw=raw, path=tmp_path / "cfg.yaml")


def _quote(asset: str, when: str, bid: float, ask: float, imbalance: float = 0.8, category: str = "sports_other") -> dict:
    return {
        "source_timestamp": when,
        "collected_at_utc": when,
        "market": f"mkt_{asset}",
        "asset_id": asset,
        "market_slug": f"slug-{asset}",
        "question": f"Q {asset}?",
        "category": category,
        "close_time": "2026-07-03T00:00:00Z",
        "best_bid": bid,
        "best_ask": ask,
        "midpoint": round((bid + ask) / 2, 6),
        "spread": round(ask - bid, 6),
        "book_imbalance": imbalance,
        "top_bid_size": 500,
        "top_ask_size": 100,
    }


def _features(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "features.csv"
    write_csv(path, rows)
    return path


def test_events_are_chronological(tmp_path: Path):
    path = _features(
        tmp_path,
        [
            _quote("a1", "2026-07-02T11:00:00Z", 0.5, 0.51),
            _quote("a1", "2026-07-02T10:00:00Z", 0.4, 0.41),
        ],
    )
    events = iter_quote_events(path)
    assert [event.timestamp_utc for event in events] == ["2026-07-02T10:00:00Z", "2026-07-02T11:00:00Z"]


def test_null_strategy_replay_produces_no_trades(tmp_path: Path):
    cfg = _cfg(tmp_path)
    path = _features(tmp_path, [_quote("a1", "2026-07-02T10:00:00Z", 0.5, 0.51)])
    summary = run_replay(cfg, "null", features_input=path)
    assert summary["status"] == "ok"
    assert summary["events_processed"] == 1
    assert summary["intents_emitted"] == 0
    assert summary["fills"] == 0
    assert summary["paper_trading_invoked"] is False
    assert summary["live_trading_invoked"] is False
    written = read_json(cfg.output_root / "polymarket_algo" / "replay_null_summary.json")
    assert written["strategy"] == "null"
    assert (cfg.output_root / "polymarket_algo" / "replay_null_fills.csv").exists()


def test_join_bid_strategy_fill_and_marking_are_exact(tmp_path: Path):
    cfg = _cfg(tmp_path)
    path = _features(
        tmp_path,
        [
            # e1: tight, bid-heavy book -> rest a BUY at 0.50 (1 USDC / 0.50 = 2 shares), GTD 10:30.
            _quote("a1", "2026-07-02T10:00:00Z", 0.50, 0.51),
            # e2: ask crosses down to the resting limit -> fill 2 @ 0.50 (cost 1.0).
            _quote("a1", "2026-07-02T10:05:00Z", 0.48, 0.50),
            # e3: qualifying quote again, but the open position blocks a new order; mark moves to 0.55.
            _quote("a1", "2026-07-02T10:10:00Z", 0.55, 0.56),
        ],
    )
    summary = run_replay(cfg, "tight_spread_join_bid_shadow", features_input=path)
    assert summary["status"] == "ok"
    assert summary["events_processed"] == 3
    assert summary["intents_emitted"] == 1
    assert summary["fills"] == 1
    assert summary["skipped_open_exposure"] == 2
    assert summary["expiries"] == 0
    assert summary["resting_orders_at_end"] == 0

    position = summary["open_positions"][0]
    assert position["quantity"] == pytest.approx(2.0)
    assert position["cost_usdc"] == pytest.approx(1.0)
    assert position["latest_bid"] == pytest.approx(0.55)
    # 2 shares marked to bid 0.55 = 1.10 against 1.00 cost.
    assert position["mark_pnl_usdc"] == pytest.approx(0.10)
    assert summary["unrealised_mark_to_bid_pnl_usdc"] == pytest.approx(0.10)
    assert summary["by_category"]["sports_other"]["mark_pnl_usdc"] == pytest.approx(0.10)

    fills = read_json(cfg.output_root / "polymarket_algo" / "replay_tight_spread_join_bid_shadow_summary.json")
    assert fills["fills"] == 1


def test_resting_order_expires_without_crossing_ask(tmp_path: Path):
    cfg = _cfg(tmp_path)
    path = _features(
        tmp_path,
        [
            # Rest a BUY at 0.30 with a 30-minute TTL...
            _quote("a2", "2026-07-02T10:00:00Z", 0.30, 0.31),
            # ...and the next quote arrives after expiry with the ask still above the limit.
            _quote("a2", "2026-07-02T11:00:00Z", 0.34, 0.35, imbalance=0.1),
        ],
    )
    summary = run_replay(cfg, "tight_spread_join_bid_shadow", features_input=path)
    assert summary["intents_emitted"] == 1
    assert summary["fills"] == 0
    assert summary["expiries"] == 1
    assert summary["open_positions"] == []


def test_replay_refuses_non_shadow_intents(tmp_path: Path, monkeypatch):
    class PaperEmitter:
        name = "paper_emitter"

        def on_quote(self, event, context):
            return [
                OrderIntent(
                    intent_id="intent_paper",
                    created_at_utc=event.timestamp_utc,
                    market_id=event.market_id,
                    token_id=event.asset_id,
                    side="BUY",
                    quantity=1.0,
                    limit_price=0.5,
                    time_in_force="IOC",
                    expire_at_utc="",
                    execution_policy="cross_spread",
                    max_slippage=0.0,
                    mode="paper",
                    source_strategy=self.name,
                    signal_ref="x",
                )
            ]

        def on_fill(self, fill, context):
            return None

    monkeypatch.setitem(registry_module._REGISTRY, "paper_emitter", PaperEmitter)
    # Config explicitly approves the strategy for paper so the wrapper does not
    # downgrade it - replay must still refuse to execute it.
    cfg = _cfg(tmp_path, algo={"allow_paper_intents": True, "paper_approved_strategies": ["paper_emitter"]})
    path = _features(tmp_path, [_quote("a1", "2026-07-02T10:00:00Z", 0.5, 0.51)])
    summary = run_replay(cfg, "paper_emitter", features_input=path)
    assert summary["status"] == "refused"
    assert "shadow" in summary["reason"]
    assert not (cfg.output_root / "polymarket_algo" / "replay_paper_emitter_summary.json").exists()


def test_cli_lists_algo_replay():
    from polymarket_predictive_engine.cli import COMMANDS

    assert "algo-replay" in COMMANDS
