from __future__ import annotations

import random
from pathlib import Path
from typing import Any

from pytest import approx

from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.execution.intents import intent_from_dict, validate_intent
from polymarket_predictive_engine.execution_costs import estimate_execution_cost
from polymarket_predictive_engine.risk import kelly_fraction, risk_decision, shrunk_kelly_fraction


SEED = 20260702
SAMPLES = 200


def _cfg(tmp_path: Path):
    path = tmp_path / "config.yaml"
    text = Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8")
    text = text.replace("output_root: outputs", f"output_root: {tmp_path.as_posix()}/outputs")
    text = text.replace("database_path: work/polymarket/polymarket_engine.sqlite", f"database_path: {tmp_path.as_posix()}/engine.sqlite")
    path.write_text(text, encoding="utf-8")
    return load_config(path)


def _float_cap(value: Any) -> float:
    if value == "" or value is None:
        return float("inf")
    return float(value)


def test_kelly_shrinkage_is_monotone_and_capped():
    rng = random.Random(SEED)
    for _ in range(SAMPLES):
        price = rng.uniform(0.03, 0.95)
        probability = rng.uniform(price + 0.001, 0.999)
        cap = rng.uniform(0.001, 0.20)

        plain = kelly_fraction(probability, price, cap)
        assert 0.0 <= plain <= cap

        last = plain
        for shrinkage in (0.0, 0.10, 0.25, 0.50, 0.75, 1.0):
            shrunk = shrunk_kelly_fraction(probability, price, cap, shrinkage=shrinkage)
            assert 0.0 <= shrunk <= cap
            assert shrunk <= plain + 1e-12
            assert shrunk <= last + 1e-12
            last = shrunk


def test_execution_costs_fail_closed_when_quotes_are_stale_or_depth_shrinks():
    rng = random.Random(SEED)
    for _ in range(SAMPLES):
        price = rng.uniform(0.08, 0.88)
        flat_slippage = rng.uniform(0.0, 0.025)
        stake = rng.uniform(1.0, 50.0)
        row = {
            "best_ask": price,
            "spread": rng.uniform(0.002, 0.08),
            "top_ask_size": rng.uniform(0.0, 100.0),
            "ask_depth_1pct": rng.uniform(0.0, 150.0),
            "ask_depth_5pct": rng.uniform(0.0, 250.0),
            "websocket_quote_age_seconds": rng.choice([121.0, 600.0, 3600.0]),
        }

        stale = estimate_execution_cost(row, stake_usdc=stake, flat_slippage=flat_slippage)
        assert stale["quote_is_fresh"] is False
        assert stale["expected_slippage"] + 1e-12 >= stale["flat_slippage"]

        no_depth = {key: value for key, value in row.items() if key not in {"top_ask_size", "ask_depth_1pct", "ask_depth_5pct"}}
        stripped = estimate_execution_cost(no_depth, stake_usdc=stake, flat_slippage=flat_slippage)
        assert stripped["expected_slippage"] + 1e-12 >= stale["expected_slippage"]
        assert _float_cap(stripped["max_stake_at_acceptable_impact_usdc"]) <= _float_cap(
            stale["max_stake_at_acceptable_impact_usdc"]
        )


def _approved_signal(rng: random.Random) -> dict[str, Any]:
    price = rng.uniform(0.10, 0.85)
    return {
        "market_id": f"market-{rng.randrange(1_000_000)}",
        "token_id": f"token-{rng.randrange(1_000_000)}",
        "edge": rng.uniform(0.05, 0.18),
        "confidence": rng.uniform(0.78, 0.99),
        "spread": rng.uniform(0.001, 0.02),
        "liquidity": rng.uniform(500.0, 20_000.0),
        "executable_price": price,
        "alpha_probability": min(0.99, price + rng.uniform(0.08, 0.20)),
        "slippage": rng.uniform(0.0, 0.005),
        "time_to_close_minutes": rng.uniform(30.0, 10_000.0),
        "best_ask": price,
        "top_ask_size": 10_000.0,
        "ask_depth_1pct": 10_000.0,
        "ask_depth_5pct": 10_000.0,
        "websocket_quote_age_seconds": rng.uniform(0.0, 60.0),
    }


def test_risk_decision_respects_stake_envelope_and_worse_inputs_do_not_promote_rejections(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("POLYMARKET_KILL_SWITCH", raising=False)
    cfg = _cfg(tmp_path)
    rng = random.Random(SEED)
    bankroll = float(cfg.raw["risk"]["bankroll"])
    kelly_cap = float(cfg.raw["risk"]["kelly_cap"])

    for _ in range(SAMPLES):
        signal = _approved_signal(rng)
        decision = risk_decision(cfg, signal)
        assert decision["approved"], decision
        assert decision["stake_usdc"] <= kelly_cap * bankroll + 1e-6
        assert decision["quantity"] * decision["limit_price"] == approx(decision["stake_usdc"], abs=1e-4)

        maybe_rejected = {
            **signal,
            "edge": rng.uniform(-0.02, 0.04),
            "confidence": rng.uniform(0.0, 0.70),
            "spread": rng.uniform(0.001, 0.08),
            "liquidity": rng.uniform(1.0, 500.0),
        }
        base = risk_decision(cfg, maybe_rejected)
        worse_variants = [
            {**maybe_rejected, "spread": float(maybe_rejected["spread"]) + rng.uniform(0.001, 0.20)},
            {**maybe_rejected, "liquidity": max(0.0, float(maybe_rejected["liquidity"]) * rng.uniform(0.0, 0.95))},
            {**maybe_rejected, "slippage": float(maybe_rejected["slippage"]) + rng.uniform(0.001, 0.20)},
        ]
        if not base["approved"]:
            for worse in worse_variants:
                assert not risk_decision(cfg, worse)["approved"]


def test_intent_fuzz_returns_violations_instead_of_raising_and_never_accepts_live_mode():
    rng = random.Random(SEED)
    atoms: list[Any] = [
        "",
        "x",
        "BUY",
        "SELL",
        "live",
        "paper",
        "shadow",
        "IOC",
        "GTD",
        "cross_spread",
        "join_bid",
        "work_midpoint",
        "2026-07-03T08:00:00Z",
        None,
        -1,
        0,
        0.5,
        1,
        2.5,
        [],
        {"nested": "value"},
    ]
    keys = [
        "intent_id",
        "created_at_utc",
        "market_id",
        "token_id",
        "side",
        "quantity",
        "limit_price",
        "time_in_force",
        "expire_at_utc",
        "execution_policy",
        "max_slippage",
        "mode",
        "source_strategy",
        "signal_ref",
    ]

    for _ in range(SAMPLES):
        payload = {key: rng.choice(atoms) for key in keys if rng.random() < 0.90}
        intent = intent_from_dict(payload)
        violations = validate_intent(intent)
        assert isinstance(violations, list)
        if not violations:
            assert intent.mode != "live"
