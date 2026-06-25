from __future__ import annotations

from typing import Any

from ..config import EngineConfig, load_config
from ..utils import write_json


def paper_trade(cfg: EngineConfig) -> dict[str, Any]:
    """Deprecated placeholder entrypoint.

    The previous implementation created empty CSV artefacts and looked like a broker
    path even though it did not use the typed ledger. Keep this command fail-closed
    until a real paper broker writes orders, fills, cash movements, and positions to
    SQLite atomically.
    """
    summary = {
        "mode": "paper",
        "status": "deprecated_placeholder_blocked",
        "approved_for_paper_trading": False,
        "orders": 0,
        "fills": 0,
        "database_path": str(cfg.database_path),
        "blockers": [
            "paper-trade CLI path is deprecated until the typed paper broker is wired to the SQLite ledger",
            "use run-paper or simulate-paper-edge for evidence-only OOS paper analysis",
        ],
    }
    write_json(cfg.output_root / "polymarket_portfolio" / "paper_trading_summary.json", summary)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return paper_trade(load_config(config_path))
