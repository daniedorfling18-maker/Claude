from __future__ import annotations

from pathlib import Path

import pytest

from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.execution.intents import (
    OrderIntent,
    downgrade_to_shadow,
    intent_from_dict,
    intent_from_risk_decision,
    intent_to_dict,
    intent_to_paper_signal,
    require_valid,
    validate_intent,
)
from polymarket_predictive_engine.risk import risk_decision


def _intent(**overrides) -> OrderIntent:
    base = dict(
        intent_id="intent_abc",
        created_at_utc="2026-07-02T10:00:00Z",
        market_id="m1",
        token_id="t1",
        side="BUY",
        quantity=10.0,
        limit_price=0.42,
        time_in_force="IOC",
        expire_at_utc="",
        execution_policy="cross_spread",
        max_slippage=0.01,
        mode="shadow",
        source_strategy="test_strategy",
        signal_ref="m1|t1|2026-07-02T10:00:00Z",
    )
    base.update(overrides)
    return OrderIntent(**base)


def test_valid_intent_passes():
    assert validate_intent(_intent()) == []
    assert require_valid(_intent()) is not None


@pytest.mark.parametrize(
    "overrides, fragment",
    [
        ({"side": "SHORT"}, "side"),
        ({"quantity": 0.0}, "quantity"),
        ({"quantity": -5.0}, "quantity"),
        ({"limit_price": 0.0}, "limit_price"),
        ({"limit_price": 1.0}, "limit_price"),
        ({"time_in_force": "FOK"}, "time_in_force"),
        ({"time_in_force": "GTD", "expire_at_utc": ""}, "expire_at_utc"),
        ({"time_in_force": "GTD", "expire_at_utc": "not-a-time"}, "expire_at_utc"),
        ({"execution_policy": "sweep"}, "execution_policy"),
        ({"max_slippage": -0.01}, "max_slippage"),
        ({"mode": "live"}, "mode"),
        ({"mode": "LIVE"}, "mode"),
        ({"market_id": ""}, "market_id"),
        ({"source_strategy": ""}, "source_strategy"),
        ({"intent_id": ""}, "intent_id"),
    ],
)
def test_invalid_intents_are_rejected(overrides, fragment):
    violations = validate_intent(_intent(**overrides))
    assert violations, f"expected violations for {overrides}"
    assert any(fragment in violation for violation in violations)
    with pytest.raises(ValueError):
        require_valid(_intent(**overrides))


def test_gtd_with_expiry_is_valid():
    intent = _intent(time_in_force="GTD", expire_at_utc="2026-07-02T12:00:00Z")
    assert validate_intent(intent) == []


def test_dict_round_trip():
    intent = _intent(time_in_force="GTD", expire_at_utc="2026-07-02T12:00:00Z")
    assert intent_from_dict(intent_to_dict(intent)) == intent


def test_downgrade_to_shadow():
    assert downgrade_to_shadow(_intent(mode="paper")).mode == "shadow"
    shadow = _intent(mode="shadow")
    assert downgrade_to_shadow(shadow) is shadow


def _example_config(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    return load_config(cfg_path)


def _book(price: float = 0.4) -> dict[str, float]:
    return {
        "best_ask": price,
        "top_ask_size": 1000.0,
        "ask_depth_1pct": 1000.0,
        "ask_depth_5pct": 1000.0,
        "websocket_quote_age_seconds": 30.0,
    }


def test_intent_from_risk_decision_round_trip(tmp_path: Path):
    cfg = _example_config(tmp_path)
    signal = {
        "market_id": "m1",
        "token_id": "t1",
        "prediction_timestamp": "2026-07-02T10:00:00Z",
        "edge": 0.10,
        "confidence": 0.9,
        "spread": 0.01,
        "liquidity": 1000,
        "executable_price": 0.4,
        "calibrated_probability": 0.55,
        "time_to_close_hours": 24,
        **_book(0.4),
    }
    decision = risk_decision(cfg, signal)
    assert decision["approved"]

    intent = intent_from_risk_decision(signal, decision)
    assert intent is not None
    assert intent.mode == "shadow"
    assert intent.side == "BUY"
    assert intent.limit_price == decision["limit_price"]
    assert intent.quantity == decision["quantity"]
    assert validate_intent(intent) == []

    row = intent_to_paper_signal(intent)
    for key in ("market_id", "token_id", "side", "prediction_timestamp", "strategy_name", "model_version", "executable_price", "max_stake_usdc"):
        assert key in row, f"paper signal row missing {key}"
    assert row["side"] == "BUY_YES"
    assert row["max_stake_usdc"] == pytest.approx(decision["stake_usdc"], abs=1e-6)
    assert row["source_intent_mode"] == "shadow"


def test_rejected_decision_yields_no_intent():
    assert intent_from_risk_decision({"market_id": "m1", "token_id": "t1"}, {"approved": False, "reason": "edge below minimum"}) is None


def test_live_mode_cannot_be_created_via_adapter(tmp_path: Path):
    cfg = _example_config(tmp_path)
    signal = {
        "market_id": "m1",
        "token_id": "t1",
        "edge": 0.10,
        "confidence": 0.9,
        "spread": 0.01,
        "liquidity": 1000,
        "executable_price": 0.4,
        "calibrated_probability": 0.55,
        "time_to_close_hours": 24,
        **_book(0.4),
    }
    decision = risk_decision(cfg, signal)
    with pytest.raises(ValueError, match="mode"):
        intent_from_risk_decision(signal, decision, mode="live")
