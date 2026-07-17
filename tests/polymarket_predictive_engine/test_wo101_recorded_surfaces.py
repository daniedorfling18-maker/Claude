"""WO-101 recorded-reality replay for previously uncovered external surfaces."""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from polymarket_predictive_engine import (
    cost_ledger,
    crypto_fundamental,
    crypto_updown_model,
    crypto_updown_settlement,
    event_group_consistency,
    external_feed_collector,
    implication_consistency,
    shadow_cohort,
    sharp_anchor,
    sharp_odds_fetch,
    wallet_intelligence_collector,
    wallet_reconciliation,
    websocket_normaliser,
)
from polymarket_predictive_engine.config import load_config


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "recorded"


def _fixture(name: str) -> Any:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


class _Response:
    def __init__(self, payload: Any, *, text: str = "", headers: dict[str, str] | None = None):
        self._payload = payload
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


class _UrlResponse:
    def __init__(self, payload: Any):
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> _UrlResponse:
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def test_recorded_public_market_data_surfaces_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    coingecko = _fixture("coingecko_simple_price_2026-07-16.json")
    deribit = _fixture("deribit_book_summary_2026-07-16.json")
    binance = _fixture("binance_klines_2026-07-16.json")
    coinbase = _fixture("coinbase_candles_2026-07-16.json")

    monkeypatch.setattr(cost_ledger.requests, "get", lambda *args, **kwargs: _Response(coingecko))
    pol_usd = cost_ledger._fetch_pol_usd(
        {
            "coingecko_simple_price_url": "https://api.coingecko.com/api/v3/simple/price",
            "pol_coin_id": "polygon-ecosystem-token",
            "request_timeout_seconds": 1,
        }
    )
    assert pol_usd["status"] == "ok"
    assert pol_usd["price_usd"] == pytest.approx(0.081612)
    assert pol_usd["last_updated_at_epoch"] == 1784243620

    monkeypatch.setattr(crypto_fundamental.requests, "get", lambda *args, **kwargs: _Response(deribit))
    book = crypto_fundamental.fetch_deribit_book("BTC", timeout=1)
    curve = crypto_fundamental.usd_call_curve(book, "BTC", date(2026, 7, 18))
    assert len(book) == 3
    assert [strike for strike, _ in curve] == [55000.0, 56000.0, 57000.0]

    monkeypatch.setattr(
        crypto_updown_model.urllib.request,
        "urlopen",
        lambda *args, **kwargs: _UrlResponse(binance),
    )
    assert crypto_updown_model._fetch_json("https://api.binance.com/api/v3/klines", 1) == binance

    def fake_window_urlopen(request: Any, timeout: int) -> _UrlResponse:
        del timeout
        return _UrlResponse(coinbase if "coinbase" in str(request.full_url) else binance)

    monkeypatch.setattr(crypto_updown_settlement.urllib.request, "urlopen", fake_window_urlopen)
    binance_window = crypto_updown_settlement._fetch_binance_window_prices(
        "btc",
        start_utc=datetime.fromtimestamp(1784243580, tz=timezone.utc),
        interval_minutes=1,
        timeout_seconds=1,
    )
    coinbase_window = crypto_updown_settlement._fetch_coinbase_window_prices(
        "btc",
        start_utc=datetime.fromtimestamp(1784243460, tz=timezone.utc),
        interval_minutes=1,
        timeout_seconds=1,
    )
    assert binance_window == (pytest.approx(63926.87), pytest.approx(63922.8), "binance_5m_klines")
    assert coinbase_window == (pytest.approx(63879.8), pytest.approx(63875.94), "coinbase_300s_candles")

    monkeypatch.setattr(external_feed_collector.requests, "get", lambda *args, **kwargs: _Response(coingecko))
    assert external_feed_collector._fetch_url("https://api.coingecko.com", "json", 1) == [coingecko]


def test_recorded_contracts_and_polygon_rpc_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    contracts = (FIXTURE_ROOT / "official_contracts_excerpt_2026-07-16.html").read_text(encoding="utf-8")
    rpc = _fixture("polygon_rpc_chain_id_2026-07-16.json")
    activity = _fixture("data_api_activity_2026-07-15.json")
    positions = _fixture("data_api_positions_2026-07-15.json")
    account_value = _fixture("data_api_value_2026-07-16.json")
    assert wallet_reconciliation.parse_official_contracts_page(contracts) == {
        "pusd": "0x1111111111111111111111111111111111111111",
        "ctf": "0x2222222222222222222222222222222222222222",
    }
    def fake_get(url: str, *args: Any, **kwargs: Any) -> _Response:
        del args, kwargs
        if url.endswith("/activity"):
            return _Response(activity)
        if url.endswith("/positions"):
            return _Response(positions)
        return _Response(account_value)

    monkeypatch.setattr(wallet_reconciliation.requests, "get", fake_get)
    monkeypatch.setattr(wallet_reconciliation.requests, "post", lambda *args, **kwargs: _Response(rpc))
    monkeypatch.setattr(wallet_reconciliation.time, "sleep", lambda _seconds: None)
    settings = {"request_timeout_seconds": 1, "request_pause_seconds": 0.1}
    assert wallet_reconciliation._get_json(
        settings, "https://data-api.polymarket.com/activity", params={}
    ) == activity
    assert wallet_reconciliation._get_json(
        settings, "https://data-api.polymarket.com/positions", params={}
    ) == positions
    assert wallet_reconciliation._get_json(
        settings, "https://data-api.polymarket.com/value", params={}
    ) == account_value
    result = wallet_reconciliation._rpc(
        {"polygon_rpc_url": "https://polygon-rpc.invalid", "request_timeout_seconds": 1, "request_pause_seconds": 0.1},
        "eth_chainId",
        [],
        request_id=101,
    )
    assert result == "0x89"


def test_recorded_gamma_public_search_replays(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _fixture("gamma_public_search_2026-07-16.json")
    markets = sharp_anchor._public_search_markets(payload)
    assert [market["groupItemTitle"] for market in markets] == ["Spain", "England"]

    monkeypatch.setattr(sharp_anchor.requests, "get", lambda *args, **kwargs: _Response(payload))
    mapping = sharp_anchor._worldcup_winner_tokens_from_public_search(
        {
            "worldcup_public_search_enabled": True,
            "worldcup_public_search_queries": ["world cup winner"],
            "worldcup_public_search_timeout_seconds": 1,
        }
    )
    assert "11111111111111111111111111111111111111111111111111111111111111111" in mapping.values()

    monkeypatch.setattr(
        shadow_cohort.urllib.request,
        "urlopen",
        lambda *args, **kwargs: _UrlResponse(payload),
    )
    market = shadow_cohort._public_search_market_by_slug(
        "will-spain-win-the-2026-fifa-world-cup-963",
        timeout_seconds=1,
    )
    assert market is not None
    assert market["groupItemTitle"] == "Spain"


def test_recorded_odds_api_catalogue_and_events_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    sports = _fixture("the_odds_api_sports_2026-07-16.json")
    events = _fixture("the_odds_api_events_2026-07-13.json")
    rows = sharp_odds_fetch.parse_odds_api_events(events)
    assert [(row["outcome"], row["decimal_odds"]) for row in rows] == [
        ("France", 2.37),
        ("Spain", 3.31),
        ("Draw", 3.24),
    ]
    assert {row["bookmaker"] for row in rows} == {"pinnacle"}

    monkeypatch.setattr(
        sharp_odds_fetch.requests,
        "get",
        lambda *args, **kwargs: _Response(sports, headers={"x-requests-remaining": "recorded"}),
    )
    keys, status = sharp_odds_fetch._provider_sports(
        base_url="https://api.the-odds-api.com/v4",
        api_key="sanitized-fixture-key",
        timeout=1,
    )
    assert keys == {"americanfootball_cfl", "americanfootball_ncaaf"}
    assert status["status"] == "ok"


def test_recorded_wallet_leaderboard_replays(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _fixture("data_api_leaderboard_2026-07-16.json")
    monkeypatch.setattr(wallet_intelligence_collector.requests, "get", lambda *args, **kwargs: _Response(payload))
    rows, metadata, error = wallet_intelligence_collector._fetch_leaderboard(
        {
            "base_url": "https://data-api.polymarket.com",
            "request_timeout_seconds": 1,
            "leaderboard_limit": 2,
            "leaderboard_paths": ["/v1/leaderboard"],
        },
        "2026-07-16T23:15:00Z",
    )
    assert error == ""
    assert metadata["path"] == "/v1/leaderboard"
    assert [row["rank"] for row in rows] == ["1", "2"]
    assert rows[0]["wallet"] == "0x1111111111111111111111111111111111111111"


def test_recorded_websocket_book_replays_real_string_envelope() -> None:
    capture = _fixture("clob_websocket_book_2026-07-16.json")[0]
    rows, quality = websocket_normaliser.normalize_websocket_message(
        capture["message"],
        capture["collected_at_utc"],
    )
    assert quality == []
    assert len(rows) == 1
    assert rows[0]["best_bid"] == 0.02
    assert rows[0]["best_ask"] == 0.97
    assert rows[0]["source_timestamp"] == "1784243575021"


def _scanner_config(tmp_path: Path):
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    raw["event_group_consistency"].update({"enabled": True, "event_pages": 1, "request_pause_seconds": 0})
    raw["implication_networks"].update({"enabled": True, "event_pages": 1, "request_pause_seconds": 0})
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def test_recorded_gamma_event_replays_network_scanners(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    event = _fixture("gamma_negrisk_event_2026-07-16.json")
    book = _fixture("clob_book_2026-07-15.json")
    cfg = _scanner_config(tmp_path)

    def fake_get(url: str, *args: Any, **kwargs: Any) -> _Response:
        del args, kwargs
        return _Response(book if url.endswith("/book") else [event])

    monkeypatch.setattr(event_group_consistency.requests, "get", fake_get)
    monkeypatch.setattr(implication_consistency.requests, "get", fake_get)
    event_result = event_group_consistency.scan_event_groups(cfg)
    implication_result = implication_consistency.scan_implication_networks(cfg)
    assert event_result["status"] == "ok"
    # The recorded event omits the event-level negRisk flag, so the scanner
    # conservatively excludes it even though its markets form a neg-risk set.
    assert event_result["neg_risk_groups_scanned"] == 0
    assert implication_result["status"] == "ok"
    assert implication_result["events_scanned"] == 1
    assert event_result["paper_trading_invoked"] is False
    assert implication_result["live_trading_invoked"] is False
