from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .utils import boolish, ensure_dir, load_yaml, now_utc, write_json


DEFAULT_CATEGORIES = ["all", "bitcoin", "crypto", "election", "fed", "finance", "soccer", "sports", "trump", "worldcup"]


@dataclass
class EngineConfig:
    raw: dict[str, Any]
    path: Path

    @property
    def data_root(self) -> Path:
        return Path(self.raw.get("paths", {}).get("data_root", "."))

    @property
    def output_root(self) -> Path:
        return Path(self.raw.get("paths", {}).get("output_root", "outputs"))

    @property
    def governance_root(self) -> Path:
        return self.output_root / "polymarket_model_governance"

    @property
    def database_path(self) -> Path:
        return Path(self.raw.get("paths", {}).get("database_path", "work/polymarket/polymarket_engine.sqlite"))

    @property
    def categories(self) -> list[str]:
        return list(self.raw.get("categories", DEFAULT_CATEGORIES))

    @property
    def trading_mode(self) -> str:
        return str(self.raw.get("trading", {}).get("mode", "paper")).lower()

    @property
    def live_approval_file(self) -> Path:
        return Path(self.raw.get("live_trading", {}).get("approval_file", "config/polymarket_live_approval.yaml"))

    @property
    def min_resolved_markets(self) -> int:
        return int(self.raw.get("governance_thresholds", {}).get("min_resolved_markets", 100))

    @property
    def min_snapshots_per_market(self) -> int:
        return int(self.raw.get("governance_thresholds", {}).get("min_snapshots_per_market", 5))


def _normalise_config_keys(value: Any) -> Any:
    """Strip a stray UTF-8 BOM from mapping keys.

    A config file saved with a BOM (common from Windows editors) parses with the
    BOM glued to the first key - as U+FEFF when read as UTF-8, or as the mojibake
    "\\xef\\xbb\\xbf" when those bytes were decoded as cp1252 somewhere upstream.
    Either form makes ``paths`` look like a missing section, so we normalise keys
    before validation.
    """
    bom = "\ufeff"  # real UTF-8 BOM character
    mojibake_bom = "\u00ef\u00bb\u00bf"  # BOM bytes EF BB BF decoded as cp1252/latin-1
    if isinstance(value, dict):
        cleaned: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str):
                key = key.lstrip(bom)
                if key.startswith(mojibake_bom):
                    key = key.replace(mojibake_bom, "", 1)
            cleaned[key] = _normalise_config_keys(item)
        return cleaned
    if isinstance(value, list):
        return [_normalise_config_keys(item) for item in value]
    return value


def load_config(path: str | Path) -> EngineConfig:
    path = Path(path)
    data = _normalise_config_keys(load_yaml(path))
    cfg = EngineConfig(raw=data, path=path)
    validate_config(cfg)
    return cfg


def validate_config(cfg: EngineConfig) -> None:
    raw = cfg.raw
    required_top = ["paths", "categories", "schema", "prediction_horizons", "model", "calibration", "baselines", "splits", "holdout", "backtest", "costs", "risk", "paper_trading", "live_trading", "governance_thresholds", "trading"]
    missing = [key for key in required_top if key not in raw]
    if missing:
        raise ValueError(f"Missing config sections: {', '.join(missing)}")
    if cfg.trading_mode not in {"paper", "backtest", "live", "off"}:
        raise ValueError("trading.mode must be one of paper, backtest, live, off")
    if cfg.trading_mode == "live" and os.getenv("POLYMARKET_LIVE_TRADING") != "1":
        raise ValueError("trading.mode is live but POLYMARKET_LIVE_TRADING=1 is not set")
    if not cfg.categories:
        raise ValueError("At least one category is required")
    risk = raw.get("risk", {})
    if float(risk.get("minimum_edge", 0.03)) <= 0:
        raise ValueError("risk.minimum_edge must be positive")
    if float(risk.get("maximum_spread", 0.08)) <= 0:
        raise ValueError("risk.maximum_spread must be positive")


def config_check(path: str | Path) -> dict[str, Any]:
    cfg = load_config(path)
    ensure_dir(cfg.governance_root)
    status = {
        "status": "ok",
        "config_path": str(Path(path)),
        "data_root": str(cfg.data_root),
        "output_root": str(cfg.output_root),
        "categories": cfg.categories,
        "trading_mode": cfg.trading_mode,
        "live_env_opt_in": os.getenv("POLYMARKET_LIVE_TRADING") == "1",
        "kill_switch_active": kill_switch_active(),
    }
    write_json(cfg.governance_root / "config_check.json", status)
    return status


def kill_switch_active() -> bool:
    return os.getenv("POLYMARKET_KILL_SWITCH") == "1"


def assert_no_kill_switch(cfg: EngineConfig, context: str = "order") -> None:
    if kill_switch_active():
        write_risk_event(cfg, "kill_switch", f"Blocked {context} because POLYMARKET_KILL_SWITCH=1")
        raise RuntimeError("POLYMARKET_KILL_SWITCH=1, no new orders may be placed")


def live_trading_allowed(cfg: EngineConfig) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if kill_switch_active():
        reasons.append("kill switch active")
    if cfg.trading_mode != "live":
        reasons.append("config trading.mode is not live")
    if os.getenv("POLYMARKET_LIVE_TRADING") != "1":
        reasons.append("POLYMARKET_LIVE_TRADING=1 not set")
    if not cfg.live_approval_file.exists():
        reasons.append(f"human approval file missing: {cfg.live_approval_file}")
    return (len(reasons) == 0, reasons)


def require_live_trading_allowed(cfg: EngineConfig) -> None:
    allowed, reasons = live_trading_allowed(cfg)
    if not allowed:
        write_risk_event(cfg, "live_trading_blocked", "; ".join(reasons))
        raise RuntimeError("Live trading is not approved: " + "; ".join(reasons))


def write_risk_event(cfg: EngineConfig, event_type: str, message: str) -> Path:
    path = cfg.governance_root / "risk_events.jsonl"
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as f:
        import json
        f.write(json.dumps({"timestamp": now_utc(), "event_type": event_type, "message": message}, sort_keys=True) + "\n")
    return path
