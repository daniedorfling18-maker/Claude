from __future__ import annotations

from typing import Any

from ..config import EngineConfig, load_config
from ..dashboard import render_dashboard
from ..agent_runtime import write_agent_runtime_bundle
from ..paper_broker import run_paper_broker
from ..profit_target import write_profit_target_tracker
from ..utils import now_utc, write_json


def paper_trade(cfg: EngineConfig) -> dict[str, Any]:
    broker = run_paper_broker(cfg)
    try:
        broker["agent_runtime"] = write_agent_runtime_bundle(cfg, broker_summary=broker)
    except Exception as exc:  # noqa: BLE001 - observability failure must not block paper ledger updates
        broker["agent_runtime"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    return broker


def paper_trade_report(cfg: EngineConfig, *, render: bool = True) -> dict[str, Any]:
    """Run the broker and immediately refresh the user-facing scoreboard.

    The continuous local loop already performs this reporting step.  The
    standalone ``paper-trade`` command also needs it, otherwise the broker
    ledger can be newer than the profit target and dashboard, making the bot's
    goal state look worse or better than reality.
    """
    broker = paper_trade(cfg)
    actual_profit_target = write_profit_target_tracker(cfg, broker if isinstance(broker, dict) else {})
    report: dict[str, Any] = {
        "status": broker.get("status", "ran") if isinstance(broker, dict) else "ran",
        "mode": "paper_trade_refresh",
        "broker": broker,
        "actual_profit_target": actual_profit_target,
        "generated_at_utc": now_utc(),
    }
    try:
        report["agent_runtime"] = write_agent_runtime_bundle(cfg, cycle_report=report, broker_summary=broker)
    except Exception as exc:  # noqa: BLE001 - keep paper-trade report alive
        report["agent_runtime"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    write_json(cfg.governance_root / "paper_trade_refresh.json", report)
    if render:
        report["dashboard"] = render_dashboard(cfg, report)
        write_json(cfg.governance_root / "paper_trade_refresh.json", report)
    return report


def main(config_path: str) -> dict[str, Any]:
    return paper_trade_report(load_config(config_path))
