from pathlib import Path
import os
import pytest

from polymarket_predictive_engine.config import load_config, live_trading_allowed, kill_switch_active
from polymarket_predictive_engine.risk import risk_decision


def write_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(Path("polymarket_predictive_config.example.yaml").read_text(), encoding="utf-8")
    return cfg


def test_config_defaults_to_paper(tmp_path):
    cfg = load_config(write_config(tmp_path))
    assert cfg.trading_mode == "paper"


def test_live_trading_dual_opt_in_gate(tmp_path, monkeypatch):
    path = write_config(tmp_path)
    text = path.read_text().replace("mode: paper", "mode: live")
    path.write_text(text)
    monkeypatch.delenv("POLYMARKET_LIVE_TRADING", raising=False)
    with pytest.raises(ValueError):
        load_config(path)


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("POLYMARKET_KILL_SWITCH", "1")
    assert kill_switch_active()


def test_risk_rejects_low_edge(tmp_path):
    cfg = load_config(write_config(tmp_path))
    decision = risk_decision(cfg, {"edge": 0.01, "confidence": 0.9, "spread": 0.01, "liquidity": 1000, "executable_price": 0.5, "calibrated_probability": 0.51})
    assert not decision["approved"]


def test_risk_approves_with_kelly_cap(tmp_path):
    cfg = load_config(write_config(tmp_path))
    decision = risk_decision(cfg, {"edge": 0.10, "confidence": 0.9, "spread": 0.01, "liquidity": 1000, "executable_price": 0.4, "calibrated_probability": 0.55})
    assert decision["approved"]
    assert decision["size"] <= cfg.raw["risk"]["kelly_cap"] * cfg.raw["risk"]["bankroll"]
