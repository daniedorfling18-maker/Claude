from __future__ import annotations

import csv
import importlib.util
from concurrent.futures import Future
from pathlib import Path

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.utils import read_csv_rows, read_json


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_polymarket_local_live_loop.py"


def _load_loop_module():
    spec = importlib.util.spec_from_file_location("run_polymarket_local_live_loop", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def test_websocket_features_are_enriched_with_scanner_market_context(tmp_path):
    loop = _load_loop_module()
    cfg = EngineConfig(
        raw={"paths": {"output_root": str(tmp_path / "outputs")}},
        path=tmp_path / "cfg.yaml",
    )
    _write_csv(
        cfg.output_root / "polymarket" / "market_snapshot.csv",
        [
            {
                "token_id": "asset-btc-down",
                "condition_id": "condition-btc",
                "market_slug": "bitcoin-up-or-down-on-june-26-2026",
                "question": "Bitcoin Up or Down on June 26?",
                "outcome": "Down",
                "close_time": "2026-06-26T16:00:00Z",
                "tick_size": "0.01",
                "event_slug": "bitcoin-up-or-down",
                "event_title": "Bitcoin Up or Down",
            }
        ],
    )

    enriched = loop.enrich_websocket_features_with_scanner_metadata(
        cfg,
        [
            {
                "collected_at_utc": "2026-06-26T10:00:00Z",
                "market": "condition-btc",
                "asset_id": "asset-btc-down",
                "event_type": "price_change",
                "best_bid": 0.41,
                "best_ask": 0.43,
                "midpoint": 0.42,
                "spread": 0.02,
            }
        ],
    )

    assert enriched[0]["question"] == "Bitcoin Up or Down on June 26?"
    assert enriched[0]["outcome"] == "Down"
    assert enriched[0]["close_time"] == "2026-06-26T16:00:00Z"
    assert enriched[0]["category"] == "crypto"

    persisted = read_csv_rows(cfg.output_root / "polymarket_training" / "websocket_market_features.csv")
    assert persisted[0]["market_slug"] == "bitcoin-up-or-down-on-june-26-2026"
    assert persisted[0]["outcome"] == "Down"
    summary = read_json(cfg.governance_root / "websocket_metadata_enrichment_summary.json")
    assert summary["metadata_hits"] == 1


def test_websocket_asset_discovery_prefers_fresh_scanner_context(tmp_path, monkeypatch):
    loop = _load_loop_module()
    monkeypatch.setenv("POLYMARKET_MODEL_PROBABILITIES_CSV", str(tmp_path / "missing_model_probabilities.csv"))
    cfg = EngineConfig(
        raw={"paths": {"output_root": str(tmp_path / "outputs")}},
        path=tmp_path / "cfg.yaml",
    )
    _write_csv(
        cfg.output_root / "polymarket_predictions" / "predictions.csv",
        [
            {"token_id": "old-prediction-1", "market_id": "old-1"},
            {"token_id": "old-prediction-2", "market_id": "old-2"},
        ],
    )
    _write_csv(
        cfg.output_root / "polymarket" / "market_snapshot.csv",
        [
            {"token_id": "fresh-scanner-1", "condition_id": "fresh-1"},
            {"token_id": "fresh-scanner-2", "condition_id": "fresh-2"},
        ],
    )

    asset_ids, sources = loop.discover_websocket_asset_ids(cfg, max_assets=2)

    assert asset_ids == ["fresh-scanner-1", "fresh-scanner-2"]
    assert sources == {
        "fresh-scanner-1": "scanner_snapshot",
        "fresh-scanner-2": "scanner_snapshot",
    }


def test_fast_updown_assets_come_from_positive_5m_cohort_evidence(tmp_path):
    loop = _load_loop_module()
    cfg = EngineConfig(
        raw={"paths": {"output_root": str(tmp_path / "outputs")}},
        path=tmp_path / "cfg.yaml",
    )
    governance = cfg.governance_root
    governance.mkdir(parents=True, exist_ok=True)
    (governance / "signal_cohort_pnl.json").write_text(
        """
        {
          "cohorts": [
            {
              "signal_cohort": "exploratory_inverse_historical_rule|crypto_btc_updown_5m|outcome=up",
              "promotion_ready_score": 5,
              "total_pnl_usdc": 4.5,
              "roi": 0.15
            },
            {
              "signal_cohort": "exploratory_historical_rule|crypto_eth_updown_5m|outcome=down",
              "promotion_ready_score": 1,
              "total_pnl_usdc": 9.0,
              "roi": 0.2
            },
            {
              "signal_cohort": "exploratory_historical_rule|crypto_xrp_updown_daily|outcome=down",
              "promotion_ready_score": 6,
              "total_pnl_usdc": 99.0,
              "roi": 0.5
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    assert loop._positive_fast_updown_assets(cfg) == ["btc"]


def test_websocket_asset_discovery_prefers_fast_updown_snapshot(tmp_path, monkeypatch):
    loop = _load_loop_module()
    monkeypatch.setenv("POLYMARKET_MODEL_PROBABILITIES_CSV", str(tmp_path / "missing_model_probabilities.csv"))
    cfg = EngineConfig(
        raw={"paths": {"output_root": str(tmp_path / "outputs")}},
        path=tmp_path / "cfg.yaml",
    )
    _write_csv(
        loop._fast_updown_snapshot_path(cfg),
        [
            {"token_id": "fast-5m-token", "market_slug": "btc-updown-5m-1782500000"},
        ],
    )
    _write_csv(
        cfg.output_root / "polymarket" / "market_snapshot.csv",
        [
            {"token_id": "scanner-token", "condition_id": "scanner-market"},
        ],
    )

    asset_ids, sources = loop.discover_websocket_asset_ids(cfg, max_assets=2)

    assert asset_ids == ["fast-5m-token", "scanner-token"]
    assert sources["fast-5m-token"] == "fast_updown_5m"


def test_websocket_asset_discovery_includes_shadow_positions(tmp_path, monkeypatch):
    loop = _load_loop_module()
    monkeypatch.setenv("POLYMARKET_MODEL_PROBABILITIES_CSV", str(tmp_path / "missing_model_probabilities.csv"))
    cfg = EngineConfig(
        raw={"paths": {"output_root": str(tmp_path / "outputs")}},
        path=tmp_path / "cfg.yaml",
    )
    _write_csv(
        cfg.output_root / "polymarket_shadow" / "shadow_positions.csv",
        [
            {
                "market_id": "shadow-market",
                "token_id": "shadow-token",
                "status": "open",
            }
        ],
    )

    asset_ids, sources = loop.discover_websocket_asset_ids(cfg, max_assets=2)

    assert asset_ids == ["shadow-token"]
    assert sources["shadow-token"] == "shadow_positions"


def test_websocket_marks_are_merged_into_shadow_prediction_rows():
    loop = _load_loop_module()

    merged = loop._merge_websocket_marks_into_predictions(
        [
            {
                "market_id": "m1",
                "token_id": "t1",
                "executable_price": "0.50",
                "best_bid": "0.49",
                "liquidity": "10",
            }
        ],
        [
            {
                "asset_id": "t1",
                "collected_at_utc": "2026-06-27T04:00:00Z",
                "best_bid": "0.41",
                "best_ask": "0.43",
                "midpoint": "0.42",
                "spread": "0.02",
                "top_bid_size": "100",
                "top_ask_size": "150",
            }
        ],
    )

    assert merged[0]["best_bid"] == 0.41
    assert merged[0]["executable_price"] == 0.43
    assert merged[0]["market_midpoint"] == 0.42
    assert merged[0]["liquidity"] == 250.0


def test_degraded_discovery_refresh_keeps_fast_updown_fresh(tmp_path, monkeypatch):
    loop = _load_loop_module()
    cfg = EngineConfig(
        raw={"paths": {"output_root": str(tmp_path / "outputs")}},
        path=tmp_path / "cfg.yaml",
    )
    monkeypatch.setattr(
        loop,
        "refresh_fast_updown_snapshot",
        lambda _cfg: {"status": "ok", "tokens": 4, "markets_found": 2},
    )

    summary = loop._degraded_discovery_refresh(
        cfg,
        {"skip_discovery_cycle": True, "reason": "memory_above_limit"},
        reason="scheduled_discovery_due",
    )

    assert summary["status"] == "ran"
    assert summary["mode"] == "degraded_fast_updown_refresh"
    assert summary["live_data"] is True
    assert summary["fast_updown"]["tokens"] == 4
    assert summary["scan"]["status"] == "skipped_resource_guard"


def test_degraded_discovery_refresh_can_be_disabled(tmp_path, monkeypatch):
    loop = _load_loop_module()
    cfg = EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "runtime_resource_guard": {"allow_degraded_fast_updown_refresh": False},
        },
        path=tmp_path / "cfg.yaml",
    )
    called = False

    def fail_if_called(_cfg):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(loop, "refresh_fast_updown_snapshot", fail_if_called)

    summary = loop._degraded_discovery_refresh(
        cfg,
        {"skip_discovery_cycle": True, "reason": "memory_above_limit"},
        reason="scheduled_discovery_due",
    )

    assert called is False
    assert summary["status"] == "skipped_resource_guard"
    assert summary["mode"] == "degraded_discovery_disabled"


def test_degraded_prediction_refresh_setting_defaults_to_enabled(tmp_path):
    loop = _load_loop_module()
    cfg = EngineConfig(
        raw={"paths": {"output_root": str(tmp_path / "outputs")}},
        path=tmp_path / "cfg.yaml",
    )

    assert loop._degraded_prediction_refresh_enabled(cfg, {"skip_prediction_cycle": True}) is True
    assert loop._degraded_prediction_refresh_enabled(cfg, {"skip_prediction_cycle": False}) is False
    assert loop._degraded_prediction_cycle_seconds(cfg, 15.0) == 60.0


def test_degraded_prediction_refresh_can_be_disabled(tmp_path):
    loop = _load_loop_module()
    cfg = EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "runtime_resource_guard": {
                "allow_degraded_websocket_prediction_refresh": False,
                "degraded_prediction_cycle_seconds": 30,
            },
        },
        path=tmp_path / "cfg.yaml",
    )

    assert loop._degraded_prediction_refresh_enabled(cfg, {"skip_prediction_cycle": True}) is False
    assert loop._degraded_prediction_cycle_seconds(cfg, 15.0) == 30.0


def test_degraded_prediction_cycle_wraps_canonical_paper_cycle(tmp_path, monkeypatch):
    loop = _load_loop_module()
    cfg = EngineConfig(
        raw={"paths": {"output_root": str(tmp_path / "outputs")}},
        path=tmp_path / "cfg.yaml",
    )
    monkeypatch.setattr(loop, "load_config", lambda _path: cfg)

    def fake_paper_cycle(_cfg, *, source):
        return {
            "status": "ran",
            "source": source,
            "features": 12,
            "predictions": 6,
            "signals_approved": 1,
            "signals_rejected": 5,
            "broker": {"equity": 1001.25},
        }

    monkeypatch.setattr(loop, "run_paper_cycle", fake_paper_cycle)

    summary = loop._run_degraded_prediction_cycle(
        config_path=tmp_path / "cfg.yaml",
        paper_source="websocket",
        guard={"skip_prediction_cycle": True, "reason": "memory_above_limit"},
    )

    assert summary["status"] == "ran"
    assert summary["mode"] == "degraded_websocket_prediction_refresh"
    assert summary["source"] == "websocket"
    assert summary["signals_approved"] == 1
    assert summary["resource_guard"]["reason"] == "memory_above_limit"


def test_refresh_fast_updown_snapshot_fetches_positive_cohort_slots(tmp_path, monkeypatch):
    loop = _load_loop_module()
    cfg = EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "paper_market_scan": {"fast_updown_5m_slots_ahead": 0},
        },
        path=tmp_path / "cfg.yaml",
    )
    cfg.governance_root.mkdir(parents=True, exist_ok=True)
    (cfg.governance_root / "signal_cohort_pnl.json").write_text(
        """
        {"cohorts": [{
          "signal_cohort": "exploratory_inverse_historical_rule|crypto_btc_updown_5m|outcome=up",
          "promotion_ready_score": 5,
          "total_pnl_usdc": 4.5,
          "roi": 0.15
        }]}
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(loop, "_fast_updown_candidate_slugs", lambda _cfg, now_dt=None: ["btc-updown-5m-1782500000"])
    monkeypatch.setattr(
        loop,
        "fetch_gamma_market",
        lambda slug, timeout_seconds=8: {
            "slug": slug,
            "active": True,
            "closed": False,
            "enableOrderBook": True,
            "conditionId": "condition-btc",
            "question": "Bitcoin UpDown 5M",
            "endDate": "2099-01-01T00:05:00Z",
            "outcomes": '["Up","Down"]',
            "clobTokenIds": '["token-up","token-down"]',
            "outcomePrices": '["0.51","0.49"]',
        },
    )
    monkeypatch.setattr(
        loop.scanner.BotConfig,
        "from_env",
        classmethod(lambda cls: object()),
    )
    monkeypatch.setattr(
        loop.scanner,
        "get_orderbook",
        lambda _cfg, token: loop.scanner.Book(
            token_id=token.token_id,
            best_bid=0.49,
            best_ask=0.51,
            bid_size=123,
            ask_size=456,
            spread=0.02,
            tick_size="0.01",
            min_order_size=1,
            neg_risk=False,
        ),
    )

    summary = loop.refresh_fast_updown_snapshot(cfg)
    rows = read_csv_rows(loop._fast_updown_snapshot_path(cfg))

    assert summary["markets_found"] == 1
    assert summary["tokens"] == 2
    assert rows[0]["market_slug"] == "btc-updown-5m-1782500000"
    assert rows[0]["token_id"] == "token-up"


def test_discovery_scheduler_uses_its_own_iteration_counter(monkeypatch, tmp_path):
    loop = _load_loop_module()
    seen: list[int] = []
    liquidity_modes: list[str] = []
    cfg = EngineConfig(
        raw={"paths": {"output_root": str(tmp_path / "outputs")}},
        path=tmp_path / "cfg.yaml",
    )

    def fake_scan_once(_cfg, *, scan_sequence):
        seen.append(scan_sequence)
        return {"tokens": 7, "snapshot_path": str(tmp_path / "snapshot.csv")}

    monkeypatch.setattr(loop, "load_config", lambda _path: cfg)
    monkeypatch.setattr(loop.discovery_loop, "force_paper_environment", lambda: None)
    monkeypatch.setattr(loop.discovery_loop, "ensure_scanner_runtime_files", lambda: None)
    monkeypatch.setattr(loop.discovery_loop, "_resource_guard", lambda _cfg: {"skip_cycle": False})
    monkeypatch.setattr(loop.discovery_loop, "_scheduled", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(loop.discovery_loop, "scan_once", fake_scan_once)
    monkeypatch.setattr(loop, "refresh_fast_updown_snapshot", lambda _cfg: {"status": "ok", "tokens": 2})
    monkeypatch.setattr(
        loop.discovery_loop,
        "run_liquidity_discovery",
        lambda _cfg, *, mode: liquidity_modes.append(mode) or {"status": "computed", "mode": mode, "tokens_scanned": 9},
    )
    monkeypatch.setattr(loop.discovery_loop, "ingest_scanner_snapshot", lambda *_args, **_kwargs: {"status": "ok"})
    monkeypatch.setattr(loop.discovery_loop, "_run_settlement_only_cycle", lambda _cfg: {"status": "settlement_only"})

    discovery_iteration, summary = loop._run_discovery_iteration(
        config_path=tmp_path / "cfg.yaml",
        optimize_model=False,
        discovery_iteration=0,
        paper_source="raw_snapshot",
    )
    discovery_iteration, summary = loop._run_discovery_iteration(
        config_path=tmp_path / "cfg.yaml",
        optimize_model=False,
        discovery_iteration=discovery_iteration,
        paper_source="raw_snapshot",
    )

    assert seen == [1, 2]
    assert discovery_iteration == 2
    assert summary["scan"]["tokens"] == 7
    assert liquidity_modes == ["targeted-evidence", "targeted-evidence"]
    assert summary["liquidity_discovery"]["mode"] == "targeted-evidence"
    assert summary["scanner_iteration"] == 4
    assert summary["mode"] == "background_lightweight_discovery"


def test_first_discovery_refresh_is_due_immediately_after_start():
    loop = _load_loop_module()

    assert loop._initial_discovery_due_timestamp(300, now=1234.5) == 1234.5
    assert loop._initial_discovery_due_timestamp(0, now=1234.5) == float("inf")


def test_prediction_cycle_status_is_background_friendly():
    loop = _load_loop_module()

    running = loop._running_prediction_summary(paper_source="websocket", started_at_utc="2026-06-26T14:00:00Z")
    done = loop._summarise_prediction_cycle(
        {
            "status": "ran",
            "generated_at_utc": "2026-06-26T14:01:00Z",
            "features": 10,
            "predictions": 4,
            "signals_approved": 1,
            "signals_rejected": 3,
            "broker": {"equity": 1000.5},
        },
        paper_source="websocket",
        started_at_utc="2026-06-26T14:00:00Z",
    )

    assert loop.build_parser().parse_args([]).prediction_cycle_seconds == 15.0
    assert loop.build_parser().parse_args([]).governance_refresh_seconds == 120.0
    assert loop.build_parser().parse_args([]).discovery_governance_block_seconds == 180.0
    assert running == {
        "status": "running",
        "source": "websocket",
        "started_at_utc": "2026-06-26T14:00:00Z",
    }
    assert done["status"] == "ran"
    assert done["predictions"] == 4
    assert done["equity"] == 1000.5


def test_stale_prediction_future_does_not_block_governance_forever():
    loop = _load_loop_module()
    future: Future[dict[str, object]] = Future()
    assert future.set_running_or_notify_cancel()

    assert loop._prediction_blocks_governance(
        future,
        prediction_started_ts=100.0,
        max_block_seconds=90.0,
        now_ts=150.0,
    )
    assert not loop._prediction_blocks_governance(
        future,
        prediction_started_ts=100.0,
        max_block_seconds=90.0,
        now_ts=191.0,
    )
    assert not loop._prediction_blocks_governance(
        future,
        prediction_started_ts=100.0,
        max_block_seconds=0.0,
        now_ts=101.0,
    )
    assert not loop._background_job_blocks_governance(
        future,
        started_ts=100.0,
        max_block_seconds=90.0,
        now_ts=191.0,
    )


def test_stale_background_jobs_do_not_block_discovery_forever():
    loop = _load_loop_module()
    prediction_future: Future[dict[str, object]] = Future()
    governance_future: Future[dict[str, object]] = Future()
    assert prediction_future.set_running_or_notify_cancel()
    assert governance_future.set_running_or_notify_cancel()

    assert loop._background_jobs_block_discovery(
        prediction_future=prediction_future,
        prediction_started_ts=100.0,
        prediction_block_seconds=90.0,
        governance_future=governance_future,
        governance_started_ts=120.0,
        governance_block_seconds=90.0,
        now_ts=150.0,
    )
    assert not loop._background_jobs_block_discovery(
        prediction_future=prediction_future,
        prediction_started_ts=100.0,
        prediction_block_seconds=90.0,
        governance_future=governance_future,
        governance_started_ts=120.0,
        governance_block_seconds=90.0,
        now_ts=211.0,
    )


def test_stale_discovery_does_not_block_prediction_forever():
    loop = _load_loop_module()
    discovery_future: Future[dict[str, object]] = Future()
    governance_future: Future[dict[str, object]] = Future()
    assert discovery_future.set_running_or_notify_cancel()
    assert governance_future.set_running_or_notify_cancel()

    assert loop._background_jobs_block_prediction(
        discovery_future=discovery_future,
        discovery_started_ts=100.0,
        discovery_block_seconds=90.0,
        governance_future=governance_future,
        governance_started_ts=120.0,
        governance_block_seconds=90.0,
        now_ts=150.0,
    )
    assert not loop._background_jobs_block_prediction(
        discovery_future=discovery_future,
        discovery_started_ts=100.0,
        discovery_block_seconds=90.0,
        governance_future=governance_future,
        governance_started_ts=120.0,
        governance_block_seconds=90.0,
        now_ts=211.0,
    )
    assert not loop._background_jobs_block_prediction(
        discovery_future=discovery_future,
        discovery_started_ts=100.0,
        discovery_block_seconds=0.0,
        governance_future=None,
        governance_started_ts=0.0,
        governance_block_seconds=90.0,
        now_ts=101.0,
    )


def test_high_memory_guard_reduces_effective_websocket_assets():
    loop = _load_loop_module()

    guard = {"degrade_live_loop": True, "degraded_max_websocket_assets": 80}

    assert loop._effective_max_assets(250, guard) == 80
    assert loop._effective_max_assets(20, guard) == 20
    assert loop._effective_max_assets(250, {"degrade_live_loop": False}) == 250


def test_resource_skipped_prediction_summary_keeps_websocket_live_message():
    loop = _load_loop_module()

    summary = loop._resource_skipped_prediction_summary(
        paper_source="websocket",
        guard={"skip_cycle": True, "reason": "memory_above_limit"},
    )

    assert summary["status"] == "skipped_resource_guard"
    assert summary["source"] == "websocket"
    assert "websocket marking remains live" in summary["message"]
