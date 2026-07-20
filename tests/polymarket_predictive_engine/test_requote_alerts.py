from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from polymarket_predictive_engine import requote_alerts
from polymarket_predictive_engine.cli import COMMANDS
from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.ledger_anchor import DEFAULT_LEDGER_REGISTRY
from polymarket_predictive_engine.requote_alerts import build_requote_alerts
from polymarket_predictive_engine.utils import read_csv_rows, read_json, write_csv, write_json


AS_OF = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)


class _Response:
    def __init__(self, payload: Any):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self.payload


def _cfg(tmp_path: Path, *, notification: bool = False) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "maker_carry_study": {
                "share_model_mid_band_min": 0.10,
                "share_model_mid_band_max": 0.90,
            },
            "requote_alerts": {
                "enabled": True,
                "notification_enabled": notification,
                "query_resolution_status": True,
            },
        },
        path=tmp_path / "cfg.yaml",
    )


def _ticket(**overrides: Any) -> dict[str, Any]:
    row = {
        "question": "Will Team A win?",
        "condition_id": "0xmarket",
        "token_id": "YES",
        "outcome": "Yes",
        "market_url": "https://polymarket.com/event/team-a-team-b",
        "reference_mid_price": 0.50,
        "reference_best_bid": 0.49,
        "reference_best_ask": 0.51,
        "quote_distance": 0.02,
        "quote_bid_price": 0.48,
        "quote_ask_price": 0.52,
        "quote_size_shares": 100,
        "capital_usd": 100,
        "order_price_min_tick_size": 0.01,
        "order_ticket_status": "exact",
        "end_date_utc": "2026-08-01T12:00:00Z",
        "event_start_time_utc": "",
    }
    row.update(overrides)
    return row


def _seed(
    cfg: EngineConfig,
    *,
    ticket: dict[str, Any] | None = None,
    live_mid: float = 0.50,
    live_stamp: str = "2026-07-12T11:59:00Z",
    close_time: str = "2026-08-01T12:00:00Z",
    toxicity: float | None = None,
    kill: list[str] | None = None,
) -> None:
    out = cfg.output_root / "maker_carry"
    out.mkdir(parents=True, exist_ok=True)
    (out / "maker_quote_sheet.md").write_text("# Maker quote sheet\n\nTickets\n", encoding="utf-8")
    write_json(
        out / "maker_carry_study.json",
        {
            "generated_at_utc": "2026-07-12T08:00:00Z",
            "portfolio": [ticket or _ticket()],
        },
    )
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            {
                "collected_at_utc": live_stamp,
                "source_timestamp": live_stamp,
                "asset_id": "YES",
                "best_bid": live_mid - 0.01,
                "best_ask": live_mid + 0.01,
                "midpoint": live_mid,
                "close_time": close_time,
            }
        ],
    )
    if toxicity is not None:
        write_csv(
            out / "flow_toxicity.csv",
            [{"market": "0xmarket", "asset_id": "YES", "toxicity_score": toxicity}],
        )
    write_json(
        out / "decision_policy.json",
        {
            "kill_criteria_status": {
                "status": "triggered" if kill else "clear",
                "triggered": kill or [],
            }
        },
    )


def _gamma(monkeypatch: pytest.MonkeyPatch, *, status: str = "", closed: bool = False) -> None:
    def fake_get(url: str, params: dict[str, Any], timeout: float) -> _Response:
        assert url.endswith("/markets")
        assert params["condition_ids"] == ["0xmarket"]
        return _Response(
            [
                {
                    "conditionId": "0xmarket",
                    "umaResolutionStatus": status,
                    "closed": closed,
                }
            ]
        )

    monkeypatch.setattr(requote_alerts.requests, "get", fake_get)


def test_requote_alerts_is_inert_without_quote_sheet(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, notification=True)

    result = build_requote_alerts(cfg, as_of=AS_OF)

    assert result["status"] == "inert_no_quote_sheet"
    assert result["alert_state"] == "quotes_ok"
    assert result["notification"]["notify"] is False
    assert result["paper_trading_invoked"] is False
    assert result["live_trading_invoked"] is False
    assert result["order_placement_invoked"] is False


def test_quiet_live_state_emits_quotes_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg(tmp_path)
    _seed(cfg)
    _gamma(monkeypatch)

    result = build_requote_alerts(cfg, as_of=AS_OF)

    assert result["alert_state"] == "quotes_ok"
    assert result["markets"][0]["alerts"] == []
    assert result["markets"][0]["live_bid"] == 0.49
    assert result["markets"][0]["live_ask"] == 0.51
    assert result["notification"]["notify"] is False


def test_later_trade_tick_does_not_hide_latest_complete_book(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(tmp_path)
    _seed(cfg)
    feature_path = cfg.output_root / "polymarket_training" / "websocket_market_features.csv"
    rows = read_csv_rows(feature_path)
    rows.append(
        {
            "collected_at_utc": "2026-07-12T11:59:30Z",
            "source_timestamp": "2026-07-12T11:59:30Z",
            "asset_id": "YES",
            "event_type": "last_trade_price",
            "last_trade_price": 0.50,
        }
    )
    write_csv(feature_path, rows)
    _gamma(monkeypatch)

    result = build_requote_alerts(cfg, as_of=AS_OF)

    assert result["alert_state"] == "quotes_ok"
    assert result["markets"][0]["live_bid"] == 0.49
    assert result["markets"][0]["live_ask"] == 0.51


def test_missing_websocket_uses_one_batched_rest_book_and_reaches_quotes_ok(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(tmp_path)
    _seed(cfg)
    (cfg.output_root / "polymarket_training" / "websocket_market_features.csv").unlink()
    calls: list[list[dict[str, str]]] = []

    def fake_get(url: str, params: dict[str, Any], timeout: float) -> _Response:
        assert url.endswith("/markets")
        return _Response([{"conditionId": "0xmarket", "closed": False}])

    def fake_post(
        url: str,
        json: list[dict[str, str]],
        timeout: float,
    ) -> _Response:
        assert url.endswith("/books")
        calls.append(json)
        return _Response(
            [
                {
                    "asset_id": "YES",
                    "market": "0xmarket",
                    "tick_size": "0.01",
                    "bids": [{"price": "0.49", "size": "100"}],
                    "asks": [{"price": "0.51", "size": "100"}],
                }
            ]
        )

    monkeypatch.setattr(requote_alerts.requests, "get", fake_get)
    monkeypatch.setattr(requote_alerts.requests, "post", fake_post)

    result = build_requote_alerts(cfg, as_of=AS_OF)

    assert result["alert_state"] == "quotes_ok"
    assert result["markets"][0]["live_price_source"] == "rest_book_fallback"
    assert result["markets"][0]["live_bid"] == 0.49
    assert result["markets"][0]["live_ask"] == 0.51
    assert result["rest_book_fallback"]["requests_made"] == 1
    assert result["rest_book_fallback"]["books_received"] == 1
    assert calls == [[{"token_id": "YES"}]]


def test_legacy_incomplete_ticket_is_enriched_from_gamma_and_rest_book(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(tmp_path)
    legacy = {
        "question": "Will Team A win?",
        "condition_id": "0xmarket",
        "quote_distance": 0.02,
        "quote_size_shares": 100,
        "capital_usd": 100,
        "order_ticket_status": "incomplete_missing_public_market_metadata_or_tick",
    }
    _seed(cfg, ticket=legacy)
    (cfg.output_root / "polymarket_training" / "websocket_market_features.csv").unlink()

    def fake_get(url: str, params: dict[str, Any], timeout: float) -> _Response:
        assert url.endswith("/markets")
        return _Response(
            [
                {
                    "conditionId": "0xmarket",
                    "question": "Will Team A win?",
                    "slug": "team-a-team-b",
                    "clobTokenIds": '["gamma-yes", "gamma-no"]',
                    "outcomes": '["Yes", "No"]',
                    "orderPriceMinTickSize": 0.01,
                    "eventStartTime": "2026-07-20T12:00:00Z",
                    "endDate": "2026-08-01T12:00:00Z",
                    "closed": False,
                }
            ]
        )

    def fake_post(
        url: str,
        json: list[dict[str, str]],
        timeout: float,
    ) -> _Response:
        assert json == [{"token_id": "gamma-yes"}]
        return _Response(
            [
                {
                    "asset_id": "gamma-yes",
                    "market": "0xmarket",
                    "tick_size": "0.01",
                    "bids": [
                        {"price": "0.10", "size": "100"},
                        {"price": "0.49", "size": "100"},
                    ],
                    "asks": [
                        {"price": "0.90", "size": "100"},
                        {"price": "0.51", "size": "100"},
                    ],
                }
            ]
        )

    monkeypatch.setattr(requote_alerts.requests, "get", fake_get)
    monkeypatch.setattr(requote_alerts.requests, "post", fake_post)

    result = build_requote_alerts(cfg, as_of=AS_OF)
    market = result["markets"][0]

    assert result["alert_state"] == "quotes_ok"
    assert market["order_ticket_status"] == "exact"
    assert market["market_url"] == "https://polymarket.com/market/team-a-team-b"
    assert market["outcome"] == "Yes"
    assert market["token_id"] == "gamma-yes"
    assert market["order_price_min_tick_size"] == 0.01
    assert market["event_start_time_utc"] == "2026-07-20T12:00:00Z"
    assert market["quote_bid_price"] == 0.48
    assert market["quote_ask_price"] == 0.52
    assert market["live_price_source"] == "rest_book_fallback"


def test_real_missing_rest_book_remains_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(tmp_path)
    _seed(cfg)
    (cfg.output_root / "polymarket_training" / "websocket_market_features.csv").unlink()

    def fake_get(url: str, params: dict[str, Any], timeout: float) -> _Response:
        return _Response([{"conditionId": "0xmarket", "closed": False}])

    def fail_post(url: str, json: list[dict[str, str]], timeout: float) -> _Response:
        raise RuntimeError("book unavailable")

    monkeypatch.setattr(requote_alerts.requests, "get", fake_get)
    monkeypatch.setattr(requote_alerts.requests, "post", fail_post)

    result = build_requote_alerts(cfg, as_of=AS_OF)

    # WO-104 item 5: loss of book visibility now fails closed to a PULL, not
    # merely requote advice — a maker blind to the book cannot manage risk.
    assert result["alert_state"] == "pull_quotes_now"
    assert result["status"] == "partial_public_lookup_error"
    assert result["markets"][0]["alerts"][0]["rule"] == "missing_live_bid_ask"
    assert result["rest_book_fallback"]["requests_made"] == 1


def test_mid_drift_beyond_ticket_band_advises_requote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(tmp_path, notification=True)
    _seed(cfg, live_mid=0.53)
    _gamma(monkeypatch)

    result = build_requote_alerts(cfg, as_of=AS_OF)

    assert result["alert_state"] == "requote_advised"
    assert result["markets"][0]["alerts"][0]["rule"] == "mid_drifted_beyond_quote_band"
    assert result["notification"]["eligible"] is False
    assert result["notification"]["notify"] is False


def test_scheduled_event_inside_window_pulls_quotes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(tmp_path)
    _seed(cfg, close_time="2026-07-13T00:00:00Z")
    _gamma(monkeypatch)

    result = build_requote_alerts(cfg, as_of=AS_OF)

    assert result["alert_state"] == "pull_quotes_now"
    assert any(
        item["rule"] == "scheduled_event_within_window"
        for item in result["markets"][0]["alerts"]
    )


def test_toxicity_above_registered_limit_pulls_quotes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(tmp_path)
    _seed(cfg, toxicity=0.91)
    _gamma(monkeypatch)

    result = build_requote_alerts(cfg, as_of=AS_OF)

    assert result["alert_state"] == "pull_quotes_now"
    assert any(
        item["rule"] == "flow_toxicity_above_limit"
        for item in result["markets"][0]["alerts"]
    )


def test_public_uma_proposal_pulls_and_notifies_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(tmp_path, notification=True)
    _seed(cfg)
    _gamma(monkeypatch, status="proposed")

    first = build_requote_alerts(cfg, as_of=AS_OF)
    second = build_requote_alerts(cfg, as_of=AS_OF)

    assert first["alert_state"] == "pull_quotes_now"
    assert first["markets"][0]["uma_resolution_status"] == "proposed"
    assert first["notification"]["eligible"] is True
    assert first["notification"]["notify"] is True
    assert first["notify"] is True
    assert first["body_file"] == first["notification"]["body_file"]
    assert second["notification"]["notify"] is False
    body = Path(first["notification"]["body_file"]).read_text(encoding="utf-8")
    assert "resolution_proposal_or_resolution_detected" in body
    sheet = (cfg.output_root / "maker_carry" / "maker_quote_sheet.md").read_text(encoding="utf-8")
    assert sheet.count("<!-- requote-alerts:start -->") == 1


def test_registered_kill_criterion_overrides_market_state_to_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(tmp_path)
    _seed(cfg, kill=["cumulative_real_net_score"])
    _gamma(monkeypatch)

    result = build_requote_alerts(cfg, as_of=AS_OF)

    assert result["alert_state"] == "STOP"
    assert result["kill_criteria_triggered"] == ["cumulative_real_net_score"]
    assert result["notification"]["eligible"] is True
    assert result["notification"]["notify"] is False


def test_requote_threshold_overrides_cannot_widen_registered_controls(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg.raw["requote_alerts"].update(
        {
            "scheduled_event_hours": 1,
            "max_live_quote_age_seconds": 9999,
            "mid_drift_distance_multiple": 2,
            "toxicity_threshold": 0.99,
            "rest_book_fallback_max_tokens_per_cycle": 999,
        }
    )

    settings = requote_alerts._settings(cfg)

    assert settings["scheduled_event_hours"] == 24
    assert settings["max_live_quote_age_seconds"] == 1800
    assert settings["mid_drift_distance_multiple"] == 1.0
    assert settings["toxicity_threshold"] == 0.9
    assert settings["rest_book_fallback_max_tokens_per_cycle"] == 16


def test_cli_and_vps_cycle_wire_read_only_requote_alerts() -> None:
    script = Path("scripts/run_vps_ops_scheduler.sh").read_text(encoding="utf-8")

    assert "requote-alerts" in COMMANDS
    assert "decision-policy" in script
    assert "requote-alerts" in script
    assert script.index("maker-live-test") < script.index("requote-alerts")
    assert script.index("decision-policy", script.index("run_trade_prints")) < script.index(
        "requote-alerts"
    )
    assert {
        "glob": "maker_carry/requote_alerts.json",
        "mode": "snapshot",
    } in DEFAULT_LEDGER_REGISTRY


def test_persisted_alert_contract_has_no_order_invocation(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)

    build_requote_alerts(cfg, as_of=AS_OF)
    persisted = read_json(cfg.output_root / "maker_carry" / "requote_alerts.json")

    assert persisted["read_only"] is True
    assert persisted["keyless"] is True
    assert persisted["order_placement_invoked"] is False
    assert persisted["order_amendment_invoked"] is False
    assert persisted["order_cancellation_invoked"] is False
