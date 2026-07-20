"""WO-92 parser regression tests replaying sanitized real endpoint payloads."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import pytest
import requests

from polymarket_predictive_engine import (
    closing_line,
    dutch_arb_monitor,
    event_group_consistency,
    flow_toxicity,
    historical_backfill,
    maker_carry_study,
    maker_fill_replay,
    maker_live_test,
    owner_activity_attribution,
    price_history_collector,
    reconstructed_signal_clv,
    requote_alerts,
    resolution_collector,
    snapshot_label_collector,
    trade_print_collector,
    wallet_intelligence_collector,
    wallet_reconciliation,
)
from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.h2_dutch_evaluator import build_h2_scan_observation
from polymarket_predictive_engine.utils import normalize_external_timestamp


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "recorded"
TOKEN = "11111111111111111111111111111111111111111111111111111111111111111"
CONDITION = "0x2222222222222222222222222222222222222222222222222222222222222222"

# Review-visible inventory: every current consumer of the registered endpoint
# surfaces is exercised below. A new parser must be added here and replay a
# recorded fixture instead of relying only on an authored payload.
RECORDED_PARSER_COVERAGE = {
    "data_api_activity": {
        "maker_live_test._fetch/_usd/_stamp",
        "owner_activity_attribution._trade_fields",
        "wallet_reconciliation._fetch_pages/_activity_cash",
    },
    "data_api_positions": {
        "maker_live_test._fetch",
        "wallet_reconciliation._fetch_pages/_positions_value",
    },
    "data_api_trades": {
        "flow_toxicity._stamp",
        "maker_carry_study._recent_prints",
        "trade_print_collector._trade_items/_print_row",
    },
    "data_api_holders": {
        "wallet_intelligence_collector._holder_payload_rows/_holder_row",
    },
    "data_api_oi": {
        "trade_print_collector._payload_items/_open_interest_row",
    },
    "clob_book_or_books": {
        "dutch_arb_monitor._best_ask",
        "event_group_consistency._book_asks",
        "maker_carry_study._books_from_payload/_best_bid_ask",
        "maker_fill_replay._payload_snapshots/_books_by_token/_official_row",
        "requote_alerts._book_snapshot",
    },
    "clob_prices_history": {
        "closing_line._fetch_price_history_close_line",
        "maker_carry_study._price_series",
        "price_history_collector.normalize_price_history_payload",
        "reconstructed_signal_clv._official_price_history",
    },
    "gamma_markets": {
        "closing_line._fetch_gamma_close_time",
        "dutch_arb_monitor.discover_event_ids",
        "historical_backfill._as_market_list/_market_close_dt",
        "maker_carry_study._rewarded_universe/token/outcome/reward parsers",
        "reconstructed_signal_clv._fetch_gamma_close_time",
        "requote_alerts._enrich_ticket_metadata",
        "resolution_collector.infer_market_resolution_rows",
        "snapshot_label_collector._fetch_gamma_market_by_slug/_classify_market_prices",
    },
}


def _fixture(name: str) -> Any:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


class _Response:
    def __init__(self, payload: Any, *, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def json(self) -> Any:
        return self._payload


def test_recorded_fixture_inventory_and_timestamp_units() -> None:
    expected = {
        "README.md",
        "data_api_activity_2026-07-15.json",
        "data_api_positions_2026-07-15.json",
        "data_api_trades_2026-07-15.json",
        "data_api_oi_2026-07-16.json",
        "clob_book_2026-07-15.json",
        "clob_books_2026-07-15.json",
        "clob_prices_history_2026-07-15.json",
        "gamma_markets_2026-07-15.json",
        "gamma_negrisk_event_2026-07-16.json",
    }
    assert expected <= {path.name for path in FIXTURE_ROOT.iterdir()}
    assert all(RECORDED_PARSER_COVERAGE.values())

    seconds = _fixture("data_api_trades_2026-07-15.json")[0]["timestamp"]
    milliseconds = _fixture("clob_book_2026-07-15.json")["timestamp"]
    iso = _fixture("gamma_markets_2026-07-15.json")[0]["updatedAt"]
    assert normalize_external_timestamp(seconds) == float(seconds)
    assert normalize_external_timestamp(milliseconds) == pytest.approx(1784135162.075)
    assert normalize_external_timestamp(iso) is not None
    assert normalize_external_timestamp("not-a-venue-time") is None
    assert normalize_external_timestamp(float("nan")) is None
    assert normalize_external_timestamp(-1) is None


def test_recorded_data_api_payloads_replay_all_current_parsers(monkeypatch: pytest.MonkeyPatch) -> None:
    activity = _fixture("data_api_activity_2026-07-15.json")
    positions = _fixture("data_api_positions_2026-07-15.json")
    trades = _fixture("data_api_trades_2026-07-15.json")
    open_interest = _fixture("data_api_oi_2026-07-16.json")

    def fake_get(url: str, **kwargs: Any) -> _Response:
        params = kwargs.get("params") or {}
        if url.endswith("/positions"):
            return _Response(positions)
        if url.endswith("/activity"):
            return _Response(activity)
        if url.endswith("/trades"):
            return _Response(trades)
        raise AssertionError(f"unexpected recorded-fixture URL: {url}")

    monkeypatch.setattr(maker_live_test.requests, "get", fake_get)
    settings = {
        "data_api_base_url": "https://data-api.polymarket.com",
        "request_timeout_seconds": 1,
    }
    assert maker_live_test._fetch(settings, "activity", {}) == activity
    assert maker_live_test._fetch(settings, "positions", {}) == positions
    assert maker_live_test._usd(activity[0]) == pytest.approx(3.25)
    assert maker_live_test._stamp(activity[0]) == 1784135092.0

    parsed_trade = owner_activity_attribution._trade_fields(activity[0])
    assert parsed_trade is not None
    assert parsed_trade["timestamp"] == 1784135092.0
    assert parsed_trade["condition_id"] == CONDITION
    assert flow_toxicity._stamp(trades[0]["timestamp"]) == 1784135092.0

    print_rows = trade_print_collector._trade_items(trades)
    print_row = trade_print_collector._print_row(print_rows[0], CONDITION)
    assert print_row is not None
    assert print_row["market"] == CONDITION
    assert print_row["timestamp"] == "1784135092"
    assert print_row["wallet"] == "0x1111111111111111111111111111111111111111"
    assert print_row["market_slug"] == "sanitized-recorded-market"
    assert print_row["event_slug"] == "sanitized-recorded-event"
    assert print_row["outcome"] == "Yes"

    oi_items = trade_print_collector._payload_items(open_interest)
    assert len(oi_items) == 1
    oi_row = trade_print_collector._open_interest_row(
        oi_items[0], CONDITION, "2026-07-16T19:40:00Z"
    )
    assert oi_row == {
        "market": CONDITION,
        "open_interest": pytest.approx(592802.115597),
        "timestamp": "",
        "collected_at_utc": "2026-07-16T19:40:00Z",
    }

    maker_settings = {
        **settings,
        "prints_limit": 100,
    }
    prints = maker_carry_study._recent_prints(maker_settings, CONDITION, TOKEN)
    assert prints == [
        {
            "price": 0.26,
            "size": 12.5,
            "stamp": 1784135092.0,
            "side": "BUY",
            "condition_id": CONDITION,
            "token_id": TOKEN,
        }
    ]
    monkeypatch.setattr(wallet_reconciliation, "_get_json", lambda settings, url, params: positions if url.endswith("/positions") else activity)
    page_settings = {
        "data_api_base_url": "https://data-api.polymarket.com",
        "positions_page_limit": 500,
        "activity_page_limit": 500,
        "max_pages": 2,
    }
    fetched_positions, positions_complete = wallet_reconciliation._fetch_pages(
        page_settings,
        path="positions",
        wallet="0xrecorded",
        limit_key="positions_page_limit",
    )
    fetched_activity, activity_complete = wallet_reconciliation._fetch_pages(
        page_settings,
        path="activity",
        wallet="0xrecorded",
        limit_key="activity_page_limit",
    )
    assert positions_complete and activity_complete
    assert fetched_positions == positions
    assert fetched_activity == activity
    cash = wallet_reconciliation._activity_cash(fetched_activity)
    value, marked, errors = wallet_reconciliation._positions_value(fetched_positions)
    assert cash["reconstructed_cash_usd"] == pytest.approx(-3.2079)
    assert value == pytest.approx(3.25)
    assert len(marked) == 1
    assert errors == []


def test_recorded_holder_token_groups_preserve_wallet_token_and_amount():
    payload = _fixture("data_api_holders_2026-07-16.json")
    rows, schema, groups = wallet_intelligence_collector._holder_payload_rows(payload, limit=20)

    assert schema == "token_groups"
    assert groups == 2
    assert len(rows) == 3
    parsed = wallet_intelligence_collector._holder_row(
        rows[0],
        snapshot_at="2026-07-16T10:00:00Z",
        market=CONDITION,
    )
    assert parsed is not None
    assert parsed["wallet"] == "0x1111111111111111111111111111111111111111"
    assert parsed["token"] == TOKEN
    assert parsed["amount"] == pytest.approx(10469.8)
    assert parsed["size"] == pytest.approx(10469.8)


def test_recorded_clob_books_replay_all_current_book_parsers(monkeypatch: pytest.MonkeyPatch) -> None:
    book = _fixture("clob_book_2026-07-15.json")
    books = _fixture("clob_books_2026-07-15.json")

    mapped = maker_carry_study._books_from_payload(books, [TOKEN])
    assert mapped[TOKEN] == book
    assert maker_carry_study._best_bid_ask(book) == (0.25, 0.26)

    assert maker_fill_replay._payload_snapshots(book) == [book]
    assert maker_fill_replay._books_by_token(books, [TOKEN])[TOKEN] == book
    official = maker_fill_replay._official_row(
        book,
        condition_id=CONDITION,
        token_id=TOKEN,
        collected_at="2026-07-15T18:30:00Z",
    )
    assert official is not None
    assert official["source_timestamp"] == pytest.approx(1784135162.075)
    assert official["best_bid"] == 0.25
    assert official["best_ask"] == 0.26

    as_of = datetime(2026, 7, 15, 18, 30, tzinfo=timezone.utc)
    live = requote_alerts._book_snapshot(book, as_of=as_of)
    assert live is not None
    assert live["best_bid"] == 0.25
    assert live["best_ask"] == 0.26

    monkeypatch.setattr(dutch_arb_monitor, "_get", lambda *args, **kwargs: book)
    assert dutch_arb_monitor._best_ask(TOKEN, 1) == (0.26, 95.25)

    monkeypatch.setattr(event_group_consistency.requests, "get", lambda *args, **kwargs: _Response(book))
    asks = event_group_consistency._book_asks(
        {"clob_base_url": "https://clob.polymarket.com", "request_timeout_seconds": 1},
        TOKEN,
    )
    assert asks == [(0.26, 95.25), (0.27, 310.0)]


def test_recorded_negrisk_event_builds_fee_bearing_exact_h2_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = _fixture("gamma_negrisk_event_2026-07-16.json")
    book = _fixture("clob_book_2026-07-15.json")

    def fake_get(url: str, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return event if url == dutch_arb_monitor.GAMMA_EVENTS else book

    monkeypatch.setattr(dutch_arb_monitor, "_get", fake_get)
    observed_at = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    plan, reason = dutch_arb_monitor.price_event(
        event["id"],
        pause=0,
        max_outcomes=80,
        min_ask_sum=0.85,
        timeout=1,
        observed_at=observed_at,
    )

    assert reason == "ok"
    assert plan is not None
    assert plan.complete is True
    assert plan.resolution_at_utc == "2026-07-29T00:00:00Z"
    assert len(plan.legs) == 5
    assert all(leg.taker_fee_category == "economics" for leg in plan.legs)
    assert all(leg.taker_fee_source == "documented_category_fallback" for leg in plan.legs)
    assert all(leg.taker_fee_per_share == pytest.approx(0.05 * 0.26 * 0.74) for leg in plan.legs)

    exact, exclusion = build_h2_scan_observation(plan, observed_at=observed_at)
    assert exclusion == ""
    assert exact is not None
    assert exact["leg_count"] == 5
    assert exact["qualifying"] is False
    assert exact["complete_common_size_plan"] is True


def test_recorded_price_history_replays_all_current_history_parsers(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _fixture("clob_prices_history_2026-07-15.json")
    normalized = price_history_collector.normalize_price_history_payload(payload)
    assert len(normalized) == 5
    assert normalized[0] == {"timestamp": "2026-07-14T13:00:07Z", "price": 0.505}

    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: _Response(payload))
    first = payload["history"][0]["t"]
    last = payload["history"][-1]["t"]
    close = datetime.fromtimestamp(last + 1, timezone.utc)
    opened = datetime.fromtimestamp(first - 1, timezone.utc)
    line = closing_line._fetch_price_history_close_line(TOKEN, close, opened)
    assert line is not None
    assert line[1] == 0.505

    series = maker_carry_study._price_series(
        {
            "clob_base_url": "https://clob.polymarket.com",
            "request_timeout_seconds": 1,
        },
        TOKEN,
        interval="1d",
        fidelity_minutes=10,
    )
    assert series is not None
    assert [stamp for stamp, _ in series] == sorted(point["t"] for point in payload["history"])

    reconstructed = reconstructed_signal_clv._official_price_history(
        TOKEN,
        datetime.fromtimestamp(first, timezone.utc),
        close,
        {
            "clob_base_url": "https://clob.polymarket.com",
            "lookback_days": 1,
            "lookahead_days": 1,
            "fidelity_seconds": 300,
            "request_timeout_seconds": 1,
        },
    )
    assert len(reconstructed) == 5
    assert {source for _, _, source in reconstructed} == {"clob_prices_history"}


def test_recorded_gamma_markets_replay_all_current_market_parsers(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _fixture("gamma_markets_2026-07-15.json")
    market = payload[0]
    assert historical_backfill._as_market_list(payload) == [market]
    assert historical_backfill._market_close_dt(market) == datetime(2026, 7, 20, tzinfo=timezone.utc)

    assert maker_carry_study._token_ids(market)[0] == TOKEN
    assert maker_carry_study._outcomes(market) == ["Yes", "No"]
    assert maker_carry_study._event_slug(market) == "sanitized-recorded-event"
    assert maker_carry_study._live_pot_usd(market, "2026-07-15") == 2.5
    assert maker_carry_study._candidate_staleness_reasons(
        market,
        as_of=datetime(2026, 7, 15, tzinfo=timezone.utc),
    ) == []

    monkeypatch.setattr(maker_carry_study.requests, "get", lambda *args, **kwargs: _Response(payload))
    # Pin the study clock to the fixture's recorded observation date so staleness
    # is evaluated point-in-time; otherwise the fixture (close 2026-07-20) is
    # flagged stale once the suite runs on/after that date (clock-drift, see #338).
    monkeypatch.setattr(maker_carry_study, "now_utc", lambda: "2026-07-15T00:00:00Z")
    universe, errors, diagnostic = maker_carry_study._rewarded_universe(
        {
            "gamma_base_url": "https://gamma-api.polymarket.com",
            "request_timeout_seconds": 1,
            "request_pause_seconds": 0,
            "universe_pages": 1,
            "page_size": 100,
            "min_daily_pot_usd": 0,
        }
    )
    assert errors == []
    assert diagnostic["excluded_stale"] == 0
    assert universe[0]["token_id"] == TOKEN

    resolution_rows, quality = resolution_collector.infer_market_resolution_rows(market)
    assert len(resolution_rows) == 2
    assert quality[0]["resolution_quality"] == "unresolved_active"

    class _Session:
        def get(self, *args: Any, **kwargs: Any) -> _Response:
            return _Response(payload)

    fetched = snapshot_label_collector._fetch_gamma_market_by_slug(
        "sanitized-recorded-market",
        _Session(),  # type: ignore[arg-type]
    )
    assert fetched == market
    outcomes = json.loads(market["outcomes"])
    tokens = json.loads(market["clobTokenIds"])
    prices = [float(value) for value in json.loads(market["outcomePrices"])]
    state, labels = snapshot_label_collector._classify_market_prices(
        closed=market["closed"],
        active=market["active"],
        accepting_orders=market["acceptingOrders"],
        outcomes=outcomes,
        tokens=tokens,
        prices=prices,
    )
    assert state == "open_not_near_terminal"
    assert labels == []

    enriched = requote_alerts._enrich_ticket_metadata({}, market)
    assert enriched["token_id"] == TOKEN
    assert enriched["outcome"] == "Yes"
    assert enriched["order_price_min_tick_size"] == 0.01
    assert enriched["end_date_utc"] == "2026-07-20T00:00:00Z"

    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: _Response(payload))
    assert closing_line._fetch_gamma_close_time({"market_slug": market["slug"]}) == "2026-07-20T00:00:00Z"
    close = reconstructed_signal_clv._fetch_gamma_close_time(
        {"market_slug": market["slug"]},
        {
            "gamma_base_url": "https://gamma-api.polymarket.com/markets",
            "request_timeout_seconds": 1,
        },
    )
    assert close == datetime(2026, 7, 20, tzinfo=timezone.utc)

    monkeypatch.setattr(dutch_arb_monitor, "_get", lambda *args, **kwargs: payload)
    assert dutch_arb_monitor.discover_event_ids(1, 1) == []


def test_recorded_parsers_remain_read_only() -> None:
    frozen_modules = (
        closing_line,
        dutch_arb_monitor,
        event_group_consistency,
        historical_backfill,
        maker_carry_study,
        maker_fill_replay,
        maker_live_test,
        price_history_collector,
        reconstructed_signal_clv,
        requote_alerts,
        resolution_collector,
        snapshot_label_collector,
        trade_print_collector,
        wallet_reconciliation,
    )
    for module in frozen_modules:
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "create_order(" not in source
        assert "post_order(" not in source
