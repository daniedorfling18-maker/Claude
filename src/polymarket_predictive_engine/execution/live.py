from __future__ import annotations

from ..config import EngineConfig, load_config, require_live_trading_allowed, write_risk_event


def cancel_all_open_orders_safely(cfg: EngineConfig) -> dict[str, str]:
    write_risk_event(cfg, "cancel_all_requested", "Live executor skeleton attempted safe cancel-all path")
    return {"status": "skeleton_only", "message": "No SDK client configured in v1 skeleton"}


def live_trade(cfg: EngineConfig) -> dict[str, str]:
    require_live_trading_allowed(cfg)
    # The safety gate above must pass before any future SDK client can be constructed.
    raise RuntimeError("Live trading skeleton only. Implement official Polymarket SDK client after governance approval.")


def main(config_path: str) -> dict[str, str]:
    return live_trade(load_config(config_path))
