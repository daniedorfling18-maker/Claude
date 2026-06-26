from __future__ import annotations

from datetime import datetime, timedelta, timezone

import polymarket_predictive_engine.crypto_updown_model as crypto_updown_model
from polymarket_predictive_engine.crypto_updown_model import (
    crypto_updown_asset,
    crypto_updown_window,
    is_crypto_updown_contract,
    is_crypto_updown_daily,
    score_crypto_updown_prediction,
)


def test_daily_crypto_updown_model_scores_up_and_down_from_binance_candle_close():
    row = {
        "market_slug": "bitcoin-up-or-down-on-june-26-2026",
        "question": "Bitcoin Up or Down on June 26?",
        "outcome": "Up",
        "close_time": "2026-06-26T16:00:00Z",
        "executable_price": "0.55",
        "spread": "0.01",
    }

    def fake_fetch(url: str, timeout_seconds: float):
        del timeout_seconds
        if "/ticker/price" in url:
            return {"price": "103"}
        if "limit=1" in url:
            # Binance kline shape: open time, open, high, low, close, ...
            return [[0, "99", "101", "98", "100"]]
        closes = [100, 100.2, 99.9, 100.3, 100.1, 100.4, 100.2, 100.5, 100.3, 100.6] * 3
        return [[0, "0", "0", "0", str(close)] for close in closes]

    scored_up = score_crypto_updown_prediction(
        row,
        {"enabled": True, "minimum_elapsed_seconds": 0, "minimum_remaining_seconds": 0},
        fetch_json=fake_fetch,
        now=datetime(2026, 6, 26, 9, 0, tzinfo=timezone.utc),
    )
    scored_down = score_crypto_updown_prediction(
        {**row, "outcome": "Down"},
        {"enabled": True, "minimum_elapsed_seconds": 0, "minimum_remaining_seconds": 0},
        fetch_json=fake_fetch,
        now=datetime(2026, 6, 26, 9, 0, tzinfo=timezone.utc),
    )

    assert crypto_updown_asset(row) == "btc"
    assert scored_up["crypto_model_status"] == "scored"
    assert scored_up["crypto_model_start_price"] == 100.0
    assert scored_up["crypto_model_current_price"] == 103.0
    assert scored_up["crypto_model_probability"] > 0.5
    assert scored_down["crypto_model_probability"] == 1.0 - scored_up["crypto_model_p_up"]


def test_daily_crypto_updown_model_fails_closed_without_close_time():
    scored = score_crypto_updown_prediction(
        {"market_slug": "xrp-up-or-down-on-june-26-2026", "outcome": "Down"},
        {"enabled": True},
        fetch_json=lambda url, timeout: {},
    )

    assert scored["crypto_model_status"] == "missing_close_time"


def test_daily_crypto_updown_model_rejects_short_interval_markets():
    fast = {
        "market_slug": "btc-updown-5m-1782468000",
        "question": "Bitcoin UpDown 5M",
        "outcome": "Up",
    }
    assert not is_crypto_updown_daily(fast)
    assert is_crypto_updown_contract(fast)
    assert crypto_updown_window(fast)["interval"] == "5m"
    assert is_crypto_updown_daily(
        {
            "market_slug": "bitcoin-up-or-down-on-june-26-2026",
            "question": "Bitcoin Up or Down on June 26?",
            "outcome": "Up",
        }
    )


def test_fast_crypto_updown_model_scores_5m_contract_from_slug_window_open():
    start_ts = 1782468000
    start_dt = datetime.fromtimestamp(start_ts, tz=timezone.utc)
    row = {
        "market_slug": f"btc-updown-5m-{start_ts}",
        "question": "Bitcoin UpDown 5M",
        "outcome": "Up",
        "executable_price": "0.45",
        "spread": "0.01",
    }

    def fake_fetch(url: str, timeout_seconds: float):
        del timeout_seconds
        if "/ticker/price" in url:
            return {"price": "103"}
        if "interval=5m" in url and "limit=1" in url:
            # Fast UpDown contracts use the Binance 5m candle "Open" price.
            return [[0, "100", "104", "99", "101"]]
        closes = [100, 100.2, 99.9, 100.3, 100.1, 100.4, 100.2, 100.5, 100.3, 100.6] * 3
        return [[0, "0", "0", "0", str(close)] for close in closes]

    scored = score_crypto_updown_prediction(
        row,
        {
            "enabled": True,
            "fast_minimum_elapsed_seconds": 0,
            "fast_minimum_remaining_seconds": 0,
            "fast_volatility_lookback_minutes": 30,
        },
        fetch_json=fake_fetch,
        now=start_dt + timedelta(minutes=2),
    )

    assert scored["crypto_model_status"] == "scored"
    assert scored["crypto_model_contract_kind"] == "fast"
    assert scored["crypto_model_interval"] == "5m"
    assert scored["crypto_model_start_price"] == 100.0
    assert scored["crypto_model_current_price"] == 103.0
    assert scored["crypto_model_probability"] > 0.5
    assert scored["crypto_model_price_source"] == "binance_5m_open"


def test_daily_crypto_updown_model_caches_asset_window_fetches(monkeypatch):
    crypto_updown_model._SNAPSHOT_CACHE.clear()
    calls: list[str] = []

    def fake_fetch(url: str, timeout_seconds: float):
        del timeout_seconds
        calls.append(url)
        if "/ticker/price" in url:
            return {"price": "103"}
        if "limit=1" in url:
            return [[0, "99", "101", "98", "100"]]
        closes = [100, 100.1, 99.9, 100.2, 100.0] * 5
        return [[0, "0", "0", "0", str(close)] for close in closes]

    monkeypatch.setattr(crypto_updown_model, "_fetch_json", fake_fetch)
    base = {
        "market_slug": "bitcoin-up-or-down-on-june-26-2026",
        "question": "Bitcoin Up or Down on June 26?",
        "close_time": "2026-06-26T16:00:00Z",
        "executable_price": "0.55",
    }
    settings = {"enabled": True, "minimum_elapsed_seconds": 0, "minimum_remaining_seconds": 0, "cache_seconds": 60}
    now = datetime(2026, 6, 26, 9, 0, tzinfo=timezone.utc)

    first = score_crypto_updown_prediction({**base, "outcome": "Up"}, settings, now=now)
    second = score_crypto_updown_prediction({**base, "outcome": "Down"}, settings, now=now)

    assert first["crypto_model_status"] == "scored"
    assert second["crypto_model_status"] == "scored"
    assert first["crypto_model_cache_hit"] is False
    assert second["crypto_model_cache_hit"] is True
    assert len(calls) == 3
