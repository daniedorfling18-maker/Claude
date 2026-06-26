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
