from __future__ import annotations

from typing import Any

from ..config import EngineConfig, load_config
from ..paper_broker import run_paper_broker


def paper_trade(cfg: EngineConfig) -> dict[str, Any]:
    return run_paper_broker(cfg)


def main(config_path: str) -> dict[str, Any]:
    return paper_trade(load_config(config_path))
