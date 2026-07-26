"""WO-92 clock-advance properties for every registered existing window."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

import pytest

from polymarket_predictive_engine import (
    corpus_retention,
    degraded_state_watchdog,
    live_test_decision_policy,
    maker_carry_study,
    maker_live_test,
    requote_alerts,
    websocket_normaliser,
)
from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.utils import write_csv, write_json


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "recorded"


def _cfg(tmp_path: Path, raw: dict[str, Any] | None = None) -> EngineConfig:
    settings: dict[str, Any] = {"paths": {"output_root": str(tmp_path / "outputs")}}
    settings.update(raw or {})
    return EngineConfig(raw=settings, path=tmp_path / "config.yaml")


def _utc(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_maker_live_reward_and_fill_leave_the_24h_window_after_clock_advance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(tmp_path)
    activity = json.loads(
        (FIXTURE_ROOT / "data_api_activity_2026-07-15.json").read_text(encoding="utf-8")
    )
    event = activity[0]
    stamp = float(event["timestamp"])

    def fake_fetch(settings: dict[str, Any], path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if path == "positions":
            return []
        return [event]

    monkeypatch.setattr(maker_live_test, "_fetch", fake_fetch)
    settings = {"activity_limit": 100, "fill_alert_multiple": 2.0}
    inside = maker_live_test._wallet_score(
        cfg,
        settings,
        wallet_role="operator",
        wallet_address="0xrecorded",
        generated_at=_iso(_utc(stamp + 86_399)),
    )
    outside = maker_live_test._wallet_score(
        cfg,
        settings,
        wallet_role="operator",
        wallet_address="0xrecorded",
        generated_at=_iso(_utc(stamp + 86_401)),
    )

    assert inside["rewards_usd_last_24h"] == pytest.approx(3.25)
    assert inside["fills_last_24h_raw"] == 1
    assert outside["rewards_usd_last_24h"] == 0.0
    assert outside["fills_last_24h_raw"] == 0


def test_decision_policy_kill_input_crosses_freshness_boundary() -> None:
    observed = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
    # WO-118: an observation only qualifies for freshness when it carries the
    # kill input, so the boundary fixture provides one.
    live = {"generated_at_utc": _iso(observed), "net_score_usd": 1.0}
    settings = {"kill_input_max_age_seconds": 1800}
    activation = {"active": True, "reasons": ["test_live_stage"]}

    inside = live_test_decision_policy._kill_input_freshness(
        live,
        [],
        settings,
        activation,
        as_of=observed + timedelta(seconds=1800),
    )
    outside = live_test_decision_policy._kill_input_freshness(
        live,
        [],
        settings,
        activation,
        as_of=observed + timedelta(seconds=1801),
    )

    assert inside["state"] == "fresh"
    assert inside["kill_data_stale"] is False
    assert outside["state"] == "stale"
    assert outside["kill_data_stale"] is True


def test_watchdog_completion_crosses_registered_freshness_boundary(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    observed = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
    maximum = degraded_state_watchdog.REGISTERED_JOB_FRESHNESS_MAX_SECONDS["training_harvest"]
    write_json(
        cfg.output_root / "ops_scheduler" / "status.json",
        {
            "jobs": {
                "training_harvest": {
                    "last_exit_code": 0,
                    "last_success_utc": _iso(observed),
                }
            }
        },
    )

    inside, _ = degraded_state_watchdog._evaluate_scheduler_freshness(
        cfg,
        {},
        {},
        _iso(observed + timedelta(seconds=maximum)),
    )
    outside, incidents = degraded_state_watchdog._evaluate_scheduler_freshness(
        cfg,
        {},
        {},
        _iso(observed + timedelta(seconds=maximum + 1)),
    )
    inside_row = next(row for row in inside["jobs"] if row["job"] == "training_harvest")
    outside_row = next(row for row in outside["jobs"] if row["job"] == "training_harvest")

    assert inside_row["state"] == "fresh"
    assert outside_row["state"] == "stale"
    assert incidents


def test_requote_live_book_crosses_staleness_boundary() -> None:
    observed = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
    ticket = {
        "condition_id": "0xmarket",
        "token_id": "token",
        "order_ticket_status": "exact",
        "reference_mid_price": 0.5,
        "quote_distance": 0.1,
    }
    live = {"_stamp": observed, "best_bid": 0.49, "best_ask": 0.51, "midpoint": 0.5}
    settings = {
        "max_live_quote_age_seconds": 1800,
        "quote_band_min": 0.05,
        "quote_band_max": 0.95,
        "mid_drift_distance_multiple": 1.0,
        "scheduled_event_hours": 24,
        "toxicity_threshold": 0.9,
    }
    common = {
        "live": live,
        "toxicity": {},
        "gamma_market": {},
        "resolved": set(),
        "settings": settings,
    }

    inside = requote_alerts._ticket_alerts(
        ticket,
        as_of=observed + timedelta(seconds=1800),
        **common,
    )
    outside = requote_alerts._ticket_alerts(
        ticket,
        as_of=observed + timedelta(seconds=1801),
        **common,
    )

    assert not any(row["rule"] == "stale_live_bid_ask" for row in inside["alerts"])
    assert any(row["rule"] == "stale_live_bid_ask" for row in outside["alerts"])


def test_candidate_leaves_live_universe_after_close_clock_boundary() -> None:
    closes = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
    market = {"question": "Will the recorded event occur?", "endDate": _iso(closes)}

    inside = maker_carry_study._candidate_staleness_reasons(
        market,
        as_of=closes - timedelta(seconds=1),
    )
    outside = maker_carry_study._candidate_staleness_reasons(
        market,
        as_of=closes + timedelta(seconds=1),
    )

    assert "venue_close_time_past" not in inside
    assert "venue_close_time_past" in outside


def test_corpus_row_crosses_retention_age_boundary_and_malformed_stays_fail_safe(
    tmp_path: Path,
) -> None:
    cfg = _cfg(tmp_path)
    observed = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
    path = cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv"
    write_csv(
        path,
        [
            {"trade_id": "recorded", "timestamp": _iso(observed)},
            {"trade_id": "malformed", "timestamp": "not-a-time"},
        ],
    )
    kwargs = {
        "corpus": "trade_prints",
        "path": path,
        "raw_days": 1.0,
        "timestamp_fields": ("timestamp",),
        "dry_run": True,
    }

    inside = corpus_retention._retire_rows(
        cfg,
        current=observed + timedelta(days=1),
        **kwargs,
    )
    outside = corpus_retention._retire_rows(
        cfg,
        current=observed + timedelta(days=1, seconds=1),
        **kwargs,
    )

    assert inside["rows_archived"] == 0
    assert inside["rows_retained"] == 2
    assert outside["rows_archived"] == 1
    assert outside["rows_retained"] == 1


def test_websocket_retention_uses_run_clock_not_newest_observation(tmp_path: Path) -> None:
    cfg = _cfg(
        tmp_path,
        {
            "websocket_market_data": {
                "retain_existing_features": True,
                "feature_retention_hours": 24,
                "max_feature_rows": 0,
                "training_archive_enabled": False,
            }
        },
    )
    observed = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
    row = {
        "collected_at_utc": _iso(observed),
        "source_timestamp": str(int(observed.timestamp() * 1000)),
        "market": "0xmarket",
        "asset_id": "token",
        "event_type": "book",
        "best_bid": 0.49,
        "best_ask": 0.51,
    }
    path = tmp_path / "missing_features.csv"

    inside, _ = websocket_normaliser._retained_feature_rows(
        cfg,
        features_path=path,
        new_features=[row],
        as_of=observed + timedelta(hours=24),
    )
    outside, diagnostic = websocket_normaliser._retained_feature_rows(
        cfg,
        features_path=path,
        new_features=[row],
        as_of=observed + timedelta(hours=24, seconds=1),
    )

    assert len(inside) == 1
    assert outside == []
    assert diagnostic["feature_retention_cutoff_utc"] == "2026-07-15T12:00:01Z"
