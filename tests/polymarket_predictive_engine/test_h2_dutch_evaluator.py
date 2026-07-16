from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from pytest import approx

from polymarket_common.fees import TAKER_FEE_MODEL_VERSION
from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.dutch_arb_monitor import ArbLeg, build_execution_plan
from polymarket_predictive_engine.h2_dutch_evaluator import (
    OBSERVATION_FIELDS,
    OBSERVATION_LOCK,
    OBSERVATION_RELATIVE_PATH,
    REGISTERED_COST_RESERVE_PER_SET,
    REGISTERED_STOP_AT,
    build_h2_scan_observation,
    evaluate_h2_dutch,
    record_h2_scan_observations,
)
from polymarket_predictive_engine.ledger_anchor import anchor_ledgers
from polymarket_predictive_engine.runtime_lock import runtime_lock
from polymarket_predictive_engine.utils import read_csv_rows, write_csv


def _config(tmp_path: Path, h2: dict | None = None) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {
                "data_root": str(tmp_path),
                "output_root": str(tmp_path / "outputs"),
                "database_path": str(tmp_path / "work" / "paper.sqlite"),
            },
            "h2_dutch": h2 or {},
        },
        path=tmp_path / "config.yaml",
    )


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _plan(
    event_id: str,
    observed_at: datetime,
    *,
    asks: tuple[float, float, float] = (0.30, 0.30, 0.35),
    sizes: tuple[float, float, float] = (10.0, 8.0, 12.0),
    resolution_at: datetime | None = None,
) -> object:
    rate = 0.05
    legs = [
        ArbLeg(
            outcome=f"outcome-{index}",
            token_id=f"{event_id}-token-{index}",
            best_ask=ask,
            ask_size=size,
            taker_fee_per_share=rate * ask * (1.0 - ask),
            taker_fee_rate=rate,
            taker_fee_category="sports",
            taker_fee_source="documented_category_fallback",
            taker_fee_enabled=True,
            taker_fee_model_version=TAKER_FEE_MODEL_VERSION,
        )
        for index, (ask, size) in enumerate(zip(asks, sizes))
    ]
    resolution = resolution_at or (observed_at + timedelta(days=30))
    return build_execution_plan(
        event_id,
        f"Event {event_id}",
        legs,
        30,
        resolution_at_utc=_iso(resolution),
    )


def _observation(
    event_id: str,
    observed_at: datetime,
    *,
    qualifying: bool = True,
    resolution_at: datetime | None = None,
) -> dict:
    asks = (0.30, 0.30, 0.35) if qualifying else (0.33, 0.33, 0.33)
    row, reason = build_h2_scan_observation(
        _plan(event_id, observed_at, asks=asks, resolution_at=resolution_at),
        observed_at=observed_at,
    )
    assert reason == ""
    assert row is not None
    assert row["qualifying"] is qualifying
    return row


def _write_observations(cfg: EngineConfig, rows: list[dict]) -> Path:
    path = cfg.output_root / OBSERVATION_RELATIVE_PATH
    write_csv(path, rows, fieldnames=OBSERVATION_FIELDS)
    return path


def test_exact_scan_economics_use_common_depth_fees_and_registered_reserve(
    tmp_path: Path,
) -> None:
    observed_at = datetime(2026, 7, 13, 0, 0, tzinfo=timezone.utc)
    plan = _plan("edge", observed_at, sizes=(10.0, 7.3333, 12.0))
    row, reason = build_h2_scan_observation(plan, observed_at=observed_at)

    assert reason == ""
    assert row is not None
    common_size = 7.3333
    expected_fee_usdc = (
        round(0.05 * 0.30 * 0.70 * common_size, 5) * 2
        + round(0.05 * 0.35 * 0.65 * common_size, 5)
    )
    expected_fees = expected_fee_usdc / common_size
    expected_all_in = 0.95 + expected_fees + 0.002
    expected_net_per_set = 1.0 - expected_all_in
    assert row["common_size"] == common_size
    assert row["entry_taker_fees_per_set"] == approx(expected_fees)
    assert row["entry_taker_fees_usdc"] == approx(expected_fee_usdc)
    assert row["registered_cost_reserve_per_set"] == REGISTERED_COST_RESERVE_PER_SET
    assert row["all_in_cost_per_set"] == approx(expected_all_in)
    assert row["net_profit_usdc"] == approx(expected_net_per_set * common_size)
    assert row["net_return_on_capital"] == approx(expected_net_per_set / expected_all_in)
    assert row["annualised_net_return_on_capital"] == approx(
        (expected_net_per_set / expected_all_in) * 365.0 / 30.0
    )
    assert row["qualifying"] is True
    assert row["paper_trading_invoked"] is False
    assert row["live_trading_invoked"] is False

    cfg = _config(tmp_path)
    first = record_h2_scan_observations(cfg, [plan], observed_at=observed_at)
    second = record_h2_scan_observations(cfg, [plan], observed_at=observed_at)
    assert first["rows_appended"] == 1
    assert second["rows_appended"] == 0
    assert len(read_csv_rows(cfg.output_root / OBSERVATION_RELATIVE_PATH)) == 1


def test_clear_same_day_and_missing_scan_episode_semantics(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    start = datetime(2026, 7, 13, 0, 0, tzinfo=timezone.utc)
    resolution = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    rows = [
        _observation("A", start, resolution_at=resolution),
        _observation("A", start + timedelta(minutes=15), resolution_at=resolution),
        _observation("A", start + timedelta(minutes=30), resolution_at=resolution),
        _observation("A", start + timedelta(minutes=45), qualifying=False, resolution_at=resolution),
        _observation("A", start + timedelta(minutes=60), resolution_at=resolution),
        _observation("B", start + timedelta(minutes=5), resolution_at=resolution),
        # A missing 15/30-minute observation breaks persistence but never proves a clear.
        _observation("B", start + timedelta(minutes=50), resolution_at=resolution),
        _observation("D", start + timedelta(minutes=10), resolution_at=resolution),
        _observation("D", start + timedelta(minutes=25), qualifying=False, resolution_at=resolution),
        _observation("D", start + timedelta(days=1, minutes=10), resolution_at=resolution),
    ]
    _write_observations(cfg, rows)

    result = evaluate_h2_dutch(cfg, as_of=start + timedelta(days=2))
    episodes = read_csv_rows(cfg.output_root / "h2_dutch" / "h2_episode_state.csv")

    assert result["status"] == "collecting"
    assert result["formal_evaluation_started"] is False
    assert result["independent_episodes"] == 4
    assert result["persistent_episodes_current"] == 1
    assert result["coverage"]["same_event_day_recurrences_excluded"] == 1
    assert result["coverage"]["persistence_gap_resets"] == 1
    assert len([row for row in episodes if row["event_id"] == "B"]) == 1
    assert len([row for row in episodes if row["event_id"] == "D"]) == 2
    assert next(row for row in episodes if row["event_id"] == "A")[
        "persistent_three_scans"
    ] == "True"


def test_malformed_economics_and_lock_contention_fail_closed(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    observed_at = datetime(2026, 7, 13, 0, 0, tzinfo=timezone.utc)
    row = _observation("tampered", observed_at)
    row["net_profit_usdc"] = 999
    _write_observations(cfg, [row])

    malformed = evaluate_h2_dutch(cfg, as_of=observed_at + timedelta(hours=1))
    assert malformed["status"] == "collecting"
    assert malformed["independent_episodes"] == 0
    assert malformed["coverage"]["inconsistent_net_profit_usdc"] == 1

    with runtime_lock(cfg, OBSERVATION_LOCK, stale_after_seconds=300) as lock:
        assert lock.acquired is True
        contended = evaluate_h2_dutch(cfg, as_of=observed_at + timedelta(hours=1))
    assert contended["status"] == "insufficient_lock_contended"
    assert contended["paper_trading_invoked"] is False
    assert contended["live_trading_invoked"] is False


def test_calendar_null_sample_is_suppressed_without_threshold_tuning(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        {
            "stop_episodes": 1,
            "stop_days": 1,
            "minimum_episodes": 1,
            "minimum_calendar_span_days": 1,
            "minimum_persistent_episodes": 0,
            "registered_cost_reserve_per_set": 0,
            "maximum_positive_event_profit_share": 1,
        },
    )
    observed_at = datetime(2026, 7, 13, 0, 0, tzinfo=timezone.utc)
    _write_observations(cfg, [_observation("null", observed_at, qualifying=False)])

    result = evaluate_h2_dutch(cfg, as_of=REGISTERED_STOP_AT)

    assert result["status"] == "suppressed"
    assert result["formal_evaluation_started"] is True
    assert result["stop_reason"] == "calendar_deadline"
    assert result["formal_sample_episodes"] == 0
    assert result["critical_settings"]["stop_episodes"] == 100
    assert result["critical_settings"]["minimum_episodes"] == 30
    assert result["critical_settings"]["minimum_calendar_span_days"] == 14
    assert result["critical_settings"]["minimum_persistent_episodes"] == 10
    assert result["critical_settings"]["registered_cost_reserve_per_set"] == 0.002
    assert result["critical_settings"]["maximum_positive_event_profit_share"] == 0.35
    assert result["recommended_action"] == "suppress"


def test_planted_exact_edge_waits_for_anchor_then_becomes_shadow_candidate(
    tmp_path: Path,
) -> None:
    cfg = _config(tmp_path)
    start = datetime(2026, 7, 13, 0, 0, tzinfo=timezone.utc)
    rows: list[dict] = []
    starts: list[datetime] = []
    for index in range(100):
        stamp = start + timedelta(days=index // 5, hours=(index % 5) * 2)
        starts.append(stamp)
        resolution = stamp.replace(hour=0, minute=0) + timedelta(days=90)
        rows.append(_observation(f"event-{index:03d}", stamp, resolution_at=resolution))
        if index < 10:
            rows.append(
                _observation(
                    f"event-{index:03d}",
                    stamp + timedelta(minutes=15),
                    resolution_at=resolution,
                )
            )
            rows.append(
                _observation(
                    f"event-{index:03d}",
                    stamp + timedelta(minutes=30),
                    resolution_at=resolution,
                )
            )
    _write_observations(cfg, rows)
    as_of = max(starts) + timedelta(minutes=1)

    before_anchor = evaluate_h2_dutch(cfg, as_of=as_of)
    final_path = cfg.output_root / "h2_dutch" / "h2_final_episodes_v1.csv"
    frozen_before = read_csv_rows(final_path)

    assert before_anchor["status"] == "awaiting_verified_anchor"
    assert before_anchor["stop_reason"] == "first_100_independent_episodes"
    assert before_anchor["formal_sample_episodes"] == 100
    assert before_anchor["support"]["statistical_support_gate_pass"] is True
    assert before_anchor["support"]["persistent_three_scan_episodes"] == 10
    assert before_anchor["support"]["calendar_span_days"] >= 14
    assert before_anchor["support"]["event_clustered_ci_low_90"] > 0
    assert before_anchor["support"]["maximum_positive_event_profit_share"] == approx(0.01)
    assert len(frozen_before) == 100

    anchor = anchor_ledgers(cfg, anchor_date=as_of.date().isoformat())
    assert anchor["status"] == "ok"
    after_anchor = evaluate_h2_dutch(cfg, as_of=as_of + timedelta(minutes=1))

    assert after_anchor["status"] == "shadow_research_candidate"
    assert after_anchor["recommended_action"] == "shadow_research_candidate"
    assert after_anchor["evidence_anchor_pass"] is True
    assert after_anchor["fdr_status"] == "not_applicable_single_registered_primary_test"
    assert after_anchor["paper_trading_invoked"] is False
    assert after_anchor["live_trading_invoked"] is False

    # Rewriting source observations cannot revise the already frozen sample.
    rows[0] = _observation(
        "event-000",
        starts[0],
        resolution_at=starts[0].replace(hour=0, minute=0) + timedelta(days=90),
    )
    rows[0]["event_title"] = "Changed source title"
    _write_observations(cfg, rows)
    rerun = evaluate_h2_dutch(cfg, as_of=as_of + timedelta(minutes=2))
    assert rerun["status"] == "shadow_research_candidate"
    assert read_csv_rows(final_path) == frozen_before
