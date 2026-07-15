from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from polymarket_predictive_engine import price_history_collector
from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.price_history_collector import collect_price_history, normalize_price_history_payload
from polymarket_predictive_engine.profit_verdict import build_profit_verdict
from polymarket_predictive_engine.utils import read_csv_rows, read_json, write_csv

RECORDED_PRICE_HISTORY = Path("tests/fixtures/recorded/clob_prices_history_2026-07-15.json")


def _cfg(tmp_path: Path, *, max_tokens: int = 500) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "historical_price_history": {
                "max_tokens": max_tokens,
                "request_pause_seconds": 0,
                "progress_every_tokens": 0,
                "request_timeout_seconds": 1,
                "lookback_days_before_close": 30,
                "lookahead_days_after_close": 3,
                "min_close_time": "2024-01-01T00:00:00Z",
            },
        },
        path=tmp_path / "config.yaml",
    )


def _resolution(token_id: str, slug: str, close_time: str, *, category: str = "sports") -> dict[str, Any]:
    return {
        "market_slug": slug,
        "condition_id": f"condition-{token_id}",
        "gamma_market_id": f"gamma-{token_id}",
        "token_id": token_id,
        "outcome": "Yes",
        "category": category,
        "close_time": close_time,
        "resolution_time": close_time,
        "resolution_quality": "clean_settlement",
    }


def _final(
    token_id: str,
    slug: str,
    close_time: str,
    *,
    position_id: str | None = None,
    cohort: str = "sharp_anchor_soccer",
) -> dict[str, Any]:
    return {
        "shadow_position_id": position_id or f"position-{token_id}",
        "signal_cohort": cohort,
        "category": "sports",
        "market_id": f"condition-{token_id}",
        "token_id": token_id,
        "market_slug": slug,
        "question": "Will the home team win?",
        "status": "closed",
        "opened_at": "2026-07-09T00:00:00Z",
        "close_time": close_time,
        "entry_price": 0.4,
        "line_price": 0.6,
        "line_kind": "closing",
        "clv": 0.2,
    }


def _write_resolutions(cfg: EngineConfig, rows: list[dict[str, Any]]) -> None:
    write_csv(cfg.output_root / "polymarket_training" / "historical_resolutions.csv", rows)


def _write_finals(cfg: EngineConfig, rows: list[dict[str, Any]]) -> None:
    write_csv(cfg.governance_root / "closing_line_final_history.csv", rows)


def _fixture_payload() -> dict[str, Any]:
    return json.loads(RECORDED_PRICE_HISTORY.read_text(encoding="utf-8"))


def test_recorded_clob_price_history_payload_replays_real_field_types() -> None:
    rows = normalize_price_history_payload(_fixture_payload())

    assert len(rows) == 5
    assert rows[0] == {"timestamp": "2026-07-14T13:00:07Z", "price": 0.505}
    assert rows[-1] == {"timestamp": "2026-07-15T09:00:05Z", "price": 0.505}


def test_focus_final_priority_survives_newer_updown_flood_and_is_deduplicated(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path, max_tokens=500)
    focus_close = "2026-07-10T12:00:00Z"
    general_close = "2026-07-10T13:00:00Z"
    resolutions = [_resolution("general-token", "world-cup-general", general_close)]
    updown_start = datetime(2026, 7, 11, tzinfo=timezone.utc)
    for index in range(501):
        opened = updown_start + timedelta(minutes=5 * index)
        resolutions.append(
            _resolution(
                f"updown-{index}",
                f"btc-updown-5m-{int(opened.timestamp())}",
                (opened + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
                category="crypto",
            )
        )
    _write_resolutions(cfg, resolutions)
    _write_finals(
        cfg,
        [
            _final("focus-token", "world-cup-focus", focus_close, position_id="focus-a"),
            _final("focus-token", "world-cup-focus", focus_close, position_id="focus-b"),
        ],
    )
    requested: list[str] = []

    def fake_request(_base_url: str, params: dict[str, Any], _timeout: int) -> dict[str, Any]:
        requested.append(str(params["market"]))
        return _fixture_payload()

    monkeypatch.setattr(price_history_collector, "_request_history", fake_request)

    _, quality, summary = collect_price_history(cfg)

    assert requested == ["focus-token", "general-token"]
    assert summary["priority_tokens_available"] == 1
    assert summary["priority_tokens_requested"] == 1
    assert summary["priority_tokens_missing"] == 0
    assert summary["general_tokens_requested"] == 1
    assert summary["general_updown_tokens_excluded"] == 501
    assert summary["general_updown_tokens_requested"] == 0
    assert summary["paper_trading_invoked"] is False
    assert summary["live_trading_invoked"] is False
    assert {row["token_id"]: row["collection_priority"] for row in quality} == {
        "focus-token": "focus_final",
        "general-token": "general",
    }
    persisted_quality = read_csv_rows(cfg.governance_root / "historical_price_history_quality.csv")
    assert {row["collection_priority"] for row in persisted_quality} == {"focus_final", "general"}


def test_priority_final_grades_end_to_end_after_collector_refresh(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path)
    close = datetime(2026, 7, 10, 12, tzinfo=timezone.utc)
    _write_finals(
        cfg,
        [_final("focus-token", "world-cup-focus", close.isoformat().replace("+00:00", "Z"))],
    )
    payload = {
        "history": [
            {"t": int((close - timedelta(hours=7)).timestamp()), "p": 0.55},
            {"t": int((close - timedelta(hours=5)).timestamp()), "p": 0.60},
        ]
    }
    monkeypatch.setattr(price_history_collector, "_request_history", lambda *_args, **_kwargs: payload)

    collect_price_history(cfg)
    verdict = build_profit_verdict(cfg)
    diagnostic = verdict["pre_event_clv_diagnostic"]

    assert diagnostic["units_total"] == 1
    assert diagnostic["units_gradeable"] == 1
    assert diagnostic["finals_gradeable"] == 1
    assert diagnostic["unit_mean_pre_event_clv"] == 0.15
    assert diagnostic["units"][0]["finals"][0]["reference_line_price"] == 0.55


@pytest.mark.parametrize(
    ("mode", "expected_status"),
    [("empty", "empty_history"), ("error", "fetch_error")],
)
def test_priority_fetch_failure_stays_visible_and_diagnostic_ungradeable(
    tmp_path,
    monkeypatch,
    mode: str,
    expected_status: str,
) -> None:
    cfg = _cfg(tmp_path)
    _write_finals(cfg, [_final("focus-token", "world-cup-focus", "2026-07-10T12:00:00Z")])

    def unavailable(*_args, **_kwargs):
        if mode == "error":
            raise RuntimeError("recorded test outage")
        return {"history": []}

    monkeypatch.setattr(price_history_collector, "_request_history", unavailable)

    _, quality, summary = collect_price_history(cfg)
    diagnostic = build_profit_verdict(cfg)["pre_event_clv_diagnostic"]

    assert quality[0]["collection_priority"] == "focus_final"
    assert quality[0]["status"] == expected_status
    assert summary["priority_tokens_missing"] == 0
    assert summary["priority_tokens_with_history"] == 0
    assert summary["priority_tokens_empty_or_error"] == 1
    assert diagnostic["status"] == "pre_event_clv_ungradeable"
    assert diagnostic["finals_gradeable"] == 0
    assert diagnostic["units"][0]["finals"][0]["reason"] == "no_official_in_band_observation_at_or_before_cutoff"


def test_missing_or_malformed_final_ledger_never_invents_priority(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path)
    _write_resolutions(
        cfg,
        [
            _resolution("general-token", "world-cup-general", "2026-07-10T12:00:00Z"),
            _resolution("updown-token", "eth-updown-5m-1783684800", "2026-07-10T12:05:00Z", category="crypto"),
        ],
    )
    _write_finals(cfg, [{**_final("", "malformed", "2026-07-10T12:00:00Z"), "token_id": ""}])
    monkeypatch.setattr(price_history_collector, "_request_history", lambda *_args, **_kwargs: _fixture_payload())

    _, quality, summary = collect_price_history(cfg)

    assert summary["priority_tokens_available"] == 0
    assert summary["priority_tokens_requested"] == 0
    assert summary["priority_tokens_missing"] == 0
    assert summary["general_updown_tokens_requested"] == 0
    assert [row["token_id"] for row in quality] == ["general-token"]
    assert read_json(cfg.governance_root / "historical_price_history_summary.json")["work_order"] == "WO-91"
