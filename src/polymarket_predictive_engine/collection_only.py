from __future__ import annotations

from typing import Any

from .config import EngineConfig
from .snapshot_label_collector import collect_snapshot_labels
from .websocket_collector import collect_websocket
from .websocket_normaliser import normalize_websocket_file
from .websocket_resolution_collector import collect_websocket_resolutions


def run_collection_only(
    cfg: EngineConfig,
    *,
    websocket_seconds: int = 60,
) -> dict[str, Any]:
    """Safe overnight collection path; deliberately contains no training operation."""
    collected = collect_websocket(cfg, websocket_seconds=websocket_seconds)
    normalized: dict[str, Any] = {}
    if collected.get("status") == "collected":
        _, _, normalized = normalize_websocket_file(cfg)
    resolutions = collect_websocket_resolutions(cfg)
    labels = collect_snapshot_labels(cfg)
    return {
        "status": "collection_only_complete",
        "training_invoked": False,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
        "collect_websocket": collected,
        "normalize_websocket": normalized,
        "resolve_websocket_markets": resolutions,
        "collect_snapshot_labels": labels,
    }
