from __future__ import annotations

import csv
import importlib.util
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


def test_discovery_scheduler_uses_its_own_iteration_counter(monkeypatch, tmp_path):
    loop = _load_loop_module()
    seen: list[int] = []
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
    assert summary["scanner_iteration"] == 4
    assert summary["mode"] == "background_lightweight_discovery"


def test_first_discovery_refresh_is_due_immediately_after_start():
    loop = _load_loop_module()

    assert loop._initial_discovery_due_timestamp(300, now=1234.5) == 1234.5
    assert loop._initial_discovery_due_timestamp(0, now=1234.5) == float("inf")
