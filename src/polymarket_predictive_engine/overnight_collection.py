from __future__ import annotations

from typing import Any

from .config import EngineConfig, load_config
from .snapshot_label_collector import collect_snapshot_labels
from .utils import now_utc, write_json
from .websocket_collector import collect_websocket
from .websocket_normaliser import normalize_websocket_file
from .websocket_resolution_collector import collect_websocket_resolutions

FORBIDDEN_COLLECTION_ONLY_STEPS = [
    "build-labels",
    "build-features",
    "build-features-v2",
    "train",
    "train-calibration",
    "train-skill-model",
    "generate-signals",
    "paper-trade",
    "run-paper",
    "live-trade",
]


def run_collection_only_overnight(
    cfg: EngineConfig,
    *,
    websocket_seconds: int = 60,
    websocket_input: str | None = None,
) -> dict[str, Any]:
    """Run the safe overnight loop for data collection only.

    This command is deliberately incapable of invoking labels, features, model
    training, paper execution, or live execution. It only collects and normalises
    current market evidence and then records the snapshot-label status.
    """
    payload: dict[str, Any] = {
        "status": "collection_only",
        "generated_at_utc": now_utc(),
        "live_trading": False,
        "forbidden_steps": FORBIDDEN_COLLECTION_ONLY_STEPS,
    }
    payload["collect_websocket"] = collect_websocket(cfg, websocket_seconds=websocket_seconds)
    rows, quality, normalise = normalize_websocket_file(cfg, input_path=websocket_input)
    payload["normalize_websocket"] = {
        "rows": len(rows),
        "quality_rows": len(quality),
        "summary": normalise,
    }
    payload["resolve_websocket_markets"] = collect_websocket_resolutions(cfg)
    payload["collect_snapshot_labels"] = collect_snapshot_labels(cfg)
    write_json(cfg.governance_root / "overnight_collection_only_summary.json", payload)
    return payload


def main(config_path: str, websocket_seconds: int = 60) -> dict[str, Any]:
    return run_collection_only_overnight(load_config(config_path), websocket_seconds=websocket_seconds)
