from pathlib import Path
import os
import pytest

from polymarket_predictive_engine.config import load_config, live_trading_allowed, kill_switch_active
from polymarket_predictive_engine.risk import risk_decision


def executable_book(price: float = 0.4) -> dict[str, float]:
    return {
        "best_ask": price,
        "top_ask_size": 1000.0,
        "ask_depth_1pct": 1000.0,
        "ask_depth_5pct": 1000.0,
        "websocket_quote_age_seconds": 30.0,
    }


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
    decision = risk_decision(
        cfg,
        {
            "edge": 0.10,
            "confidence": 0.9,
            "spread": 0.01,
            "liquidity": 1000,
            "executable_price": 0.4,
            "calibrated_probability": 0.55,
            "time_to_close_hours": 24,
            **executable_book(0.4),
        },
    )
    assert decision["approved"]
    assert decision["size"] <= cfg.raw["risk"]["kelly_cap"] * cfg.raw["risk"]["bankroll"]


def test_risk_sizes_from_alpha_probability_when_present(tmp_path):
    cfg = load_config(write_config(tmp_path))
    decision = risk_decision(
        cfg,
        {
            "edge": 0.10,
            "confidence": 0.9,
            "spread": 0.01,
            "liquidity": 1000,
            "executable_price": 0.4,
            "calibrated_probability": 0.20,
            "alpha_probability": 0.55,
            "time_to_close_hours": 24,
            **executable_book(0.4),
        },
    )
    assert decision["approved"]
    assert decision["size"] > 0


def test_load_config_tolerates_byte_order_mark(tmp_path):
    # Baseline config text with any leading BOM removed.
    base = Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8-sig")

    # A real UTF-8 BOM glued to the front (typical of a Windows editor save).
    real_bom = tmp_path / "real_bom.yaml"
    real_bom.write_text("\ufeff" + base, encoding="utf-8")
    assert load_config(real_bom).trading_mode == "paper"

    # The mojibake form: BOM bytes EF BB BF decoded as cp1252 then re-saved as
    # UTF-8 - exactly what produced the "Missing config sections: paths" error.
    mojibake_bom = tmp_path / "mojibake_bom.yaml"
    mojibake_bom.write_text("\u00ef\u00bb\u00bf" + base, encoding="utf-8")
    assert load_config(mojibake_bom).trading_mode == "paper"


def test_example_config_has_no_byte_order_mark():
    # The committed example config must not carry a BOM; the test helpers read it
    # with the platform default encoding, which mangles a BOM on Windows.
    assert Path("polymarket_predictive_config.example.yaml").read_bytes()[:3] != b"\xef\xbb\xbf"


def test_risk_rejects_entries_outside_price_band(tmp_path):
    cfg = load_config(write_config(tmp_path))
    base = {
        "edge": 0.10,
        "confidence": 0.9,
        "spread": 0.01,
        "liquidity": 1000,
        "calibrated_probability": 0.99,
        "time_to_close_hours": 24,
        **executable_book(0.4),
    }
    favourite = risk_decision(cfg, {**base, "executable_price": 0.95})
    assert not favourite["approved"]
    assert "entry price" in favourite["reason"]
    longshot = risk_decision(cfg, {**base, "executable_price": 0.02, "calibrated_probability": 0.2})
    assert not longshot["approved"]
    assert "entry price" in longshot["reason"]
    inside = risk_decision(cfg, {**base, "executable_price": 0.4, "calibrated_probability": 0.55})
    assert inside["approved"]


def test_price_band_cannot_be_widened_by_override_profiles(tmp_path):
    # The band reads base risk config only; a fast-market override attempting
    # to widen it must have no effect.
    path = write_config(tmp_path)
    text = path.read_text().replace(
        "fast_market_overrides:\n    enabled: true",
        "fast_market_overrides:\n    enabled: true\n    maximum_entry_price: 0.99",
    )
    path.write_text(text)
    cfg = load_config(path)
    decision = risk_decision(
        cfg,
        {
            "edge": 0.10,
            "confidence": 0.9,
            "spread": 0.01,
            "liquidity": 1000,
            "executable_price": 0.95,
            "calibrated_probability": 0.99,
            "time_to_close_hours": 24,
            "market_slug": "eth-updown-15m-1782929700",
        },
    )
    assert not decision["approved"]
    assert "entry price" in decision["reason"]
