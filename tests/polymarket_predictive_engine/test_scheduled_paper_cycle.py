"""WO-143 tests: `run_paper_cycle`'s `scope` keyword and `scheduled_paper_cycle.py`.

Offline, deterministic; no live API, no network, no Docker. Fixtures mirror
`test_paper_broker_foundation.py`'s `_config`/seed helpers (that file's own
tests are run UNMODIFIED as part of the full suite -- the registered proof
that `scope="full"` stays byte-identical to the pre-WO-143 behaviour).
"""

from __future__ import annotations

import json
import os
import signal
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

import polymarket_predictive_engine.paper_cycle as paper_cycle_module
import polymarket_predictive_engine.scheduled_paper_cycle as scheduled_paper_cycle
from polymarket_predictive_engine import runtime_lock
from polymarket_predictive_engine.cli import main as cli_main
from polymarket_predictive_engine.config import EngineConfig, load_config
from polymarket_predictive_engine.paper_cycle import run_paper_cycle
from polymarket_predictive_engine.scheduled_paper_cycle import run_scheduled_paper_cycle
from polymarket_predictive_engine.storage import connect_db
from polymarket_predictive_engine.utils import read_json, write_csv, write_json


_REGISTERED_RECEIPT_KEY_ORDER = [
    "status",
    "generated_at_utc",
    "started_at_utc",
    "duration_seconds",
    "source",
    "scope",
    "lock_attempts",
    "lock_wait_seconds",
    "cycle_completed",
    "features",
    "predictions",
    "signals_approved",
    "signals_rejected",
    "paper_signals_published",
    "mispricing_alpha_live_summary_generated_at_utc",
    "mispricing_alpha_overlay_refreshed",
    "longshot_status",
    "longshot_candidates",
    "peak_rss_bytes",
    "exit_code",
    "paper_trading_invoked",
    "live_trading_invoked",
]


def _config(
    tmp_path: Path,
    *,
    min_paper_labels: int = 1,
    mispricing_alpha_enabled: bool = True,
    trading_mode: str | None = None,
):
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"] = {
        "data_root": str(tmp_path),
        "output_root": str(tmp_path / "outputs"),
        "database_path": str(tmp_path / "work" / "paper.sqlite"),
    }
    raw["governance_thresholds"]["min_paper_labels"] = min_paper_labels
    raw["paper_trading"]["max_exit_quote_age_minutes"] = 10_000_000
    raw["mispricing_alpha"]["enabled"] = mispricing_alpha_enabled
    if trading_mode is not None:
        raw.setdefault("trading", {})["mode"] = trading_mode
    config_path = tmp_path / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    if trading_mode in {"live", "off"}:
        # validate_config refuses a live/off config outside an already-
        # authorised live-trading environment flag. Construct the
        # EngineConfig directly so this test exercises run_paper_cycle's OWN
        # independent trading-mode guard without touching (or needing) any
        # live-trading environment flag.
        return EngineConfig(raw=raw, path=config_path)
    return load_config(config_path)


def _fresh_stamp(seconds_ago: float = 0.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed_fresh_websocket_observation(cfg, *, seconds_ago: float = 30.0) -> None:
    """Current-observation substrate for WO-143.7(b)'s freshness check.

    That check is UNCONDITIONAL -- it does not care which `source` the cycle
    ran -- so any fixture that expects a scheduled run to reach a completed
    classification must carry a current websocket observation, exactly as the
    deployed host does.

    The market/token ids here are deliberately DISJOINT from
    `_seed_raw_snapshot_fixture`'s, so this file satisfies the freshness gate
    without being matched by `_enrich_with_latest_websocket_quotes` and
    perturbing what a raw_snapshot-sourced cycle scores.
    """

    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            {
                "collected_at_utc": _fresh_stamp(seconds_ago),
                "market_id": "market-freshness-probe",
                "asset_id": "token-freshness-probe",
                "market_slug": "synthetic-freshness-probe",
                "question": "Is the websocket feed currently observing?",
                "category": "worldcup",
                "best_bid": "0.50",
                "best_ask": "0.52",
                "midpoint": "0.51",
                "top_bid_size": "100",
                "top_ask_size": "100",
            }
        ],
    )


def _seed_raw_snapshot_fixture(cfg) -> None:
    # The scheduled wrapper validates websocket observation freshness on every
    # source, so a raw-snapshot fixture still needs a live feed behind it.
    _seed_fresh_websocket_observation(cfg)
    raw_path = cfg.data_root / "outputs" / "polymarket_fixed" / "worldcup" / "ml" / "raw_market_snapshots.csv"
    write_csv(
        raw_path,
        [
            {
                "snapshot_timestamp": "2026-06-25T10:00:00Z",
                "market_id": "market-forward-1",
                "market_slug": "synthetic-forward-market",
                "token_id": "token-forward-yes",
                "question": "Will the synthetic event happen?",
                "category": "worldcup",
                "best_bid": 0.35,
                "best_ask": 0.40,
                "top_bid_size": 1000,
                "top_ask_size": 1000,
                "bid_depth_1pct": 1000,
                "ask_depth_1pct": 1000,
                "bid_depth_5pct": 1000,
                "ask_depth_5pct": 1000,
                "websocket_quote_age_seconds": 30,
                "midpoint": 0.375,
                "liquidity": 1000,
                "close_time": "2026-12-31T00:00:00Z",
            }
        ],
    )
    _seed_labels(cfg)


def _seed_labels(cfg) -> None:
    training = cfg.output_root / "polymarket_training"
    write_csv(
        training / "labels.csv",
        [
            {
                "market_id": "training-market-1",
                "token_id": "training-token-1",
                "prediction_timestamp": "2026-01-01T00:00:00Z",
                "target": 1,
                "label_source": "clean_settlement",
            }
        ],
    )
    write_csv(
        training / "market_resolutions.csv",
        [
            {
                "condition_id": "training-market-1",
                "market_slug": "training-market-1",
                "token_id": "training-token-1",
                "target": 1,
                "resolution_quality": "clean_settlement",
            }
        ],
    )


def _seed_calibration_model(cfg) -> None:
    mapping = {
        str(bucket): {
            "probability_min": bucket / 10,
            "probability_max": (bucket + 1) / 10,
            "calibrated_probability": 0.90 if bucket == 3 else (bucket + 0.5) / 10,
            "row_count": 10,
        }
        for bucket in range(10)
    }
    write_json(
        cfg.output_root / "polymarket_models" / "calibration_v2.json",
        {
            "model_version": "synthetic-calibrator-v1",
            "feature_set_version": "pm-point-in-time-v2",
            "bucket_count": 10,
            "bucket_mapping": mapping,
            "trained_at": "2026-06-24T00:00:00Z",
        },
    )


def _seed_websocket_fixture(cfg, collected_at_utc: str = "2026-07-02T12:00:00Z") -> None:
    # WO-143.7(b) added the `collected_at_utc` parameter; its default is the
    # original hard-coded stamp, so every pre-existing caller is unchanged.
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            {
                "collected_at_utc": collected_at_utc,
                "market_id": "market-ws-1",
                "asset_id": "token-ws-1",
                "market_slug": "synthetic-ws-market-1",
                "question": "Will the synthetic WS event 1 happen?",
                "category": "worldcup",
                "best_bid": "0.40",
                "best_ask": "0.42",
                "midpoint": "0.41",
                "top_bid_size": "100",
                "top_ask_size": "100",
            },
            {
                "collected_at_utc": collected_at_utc,
                "market_id": "market-ws-2",
                "asset_id": "token-ws-2",
                "market_slug": "synthetic-ws-market-2",
                "question": "Will the synthetic WS event 2 happen?",
                "category": "worldcup",
                "best_bid": "0.30",
                "best_ask": "0.32",
                "midpoint": "0.31",
                "top_bid_size": "100",
                "top_ask_size": "100",
            },
        ],
    )
    _seed_labels(cfg)


def _happy_cfg(tmp_path: Path):
    cfg = _config(tmp_path)
    _seed_raw_snapshot_fixture(cfg)
    _seed_calibration_model(cfg)
    return cfg


def _write_foreign_prediction_cycle_lock(cfg) -> Path:
    path = runtime_lock.runtime_lock_path(cfg, "prediction_cycle")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "name": "prediction_cycle",
                "pid": os.getpid() + 1,
                "process_started_at_utc": runtime_lock._PROCESS_STARTED_AT_UTC,
                "acquired_at_utc": runtime_lock._PROCESS_STARTED_AT_UTC,
            }
        ),
        encoding="utf-8",
    )
    return path


# --- 143.1: `run_paper_cycle`'s `scope` keyword -----------------------------


def test_scope_full_default_is_byte_identical_behaviour(tmp_path):
    # Registered regression (1): default scope="full" writes
    # forward_paper_cycle.json, the dashboard, broker, and shadow -- every
    # pre-existing test_paper_broker_foundation.py test passes UNMODIFIED as
    # part of the same full-suite run (this WO touches nothing in that file).
    cfg = _happy_cfg(tmp_path)

    report = run_paper_cycle(cfg, source="raw_snapshot")

    assert report["scope"] == "full"
    assert report["status"] == "ran"
    assert (cfg.governance_root / "forward_paper_cycle.json").exists()
    assert not (cfg.governance_root / "scheduled_paper_cycle_report.json").exists()
    assert report["dashboard"].get("status") != "skipped_scoring_only"
    assert report["broker"].get("status") != "skipped_scoring_only"
    assert report["shadow_cohort"].get("status") != "skipped_scoring_only"
    assert report["cohort_pnl"].get("status") != "skipped_scoring_only"
    assert report["agent_runtime"].get("status") != "skipped_scoring_only"


def test_invalid_scope_raises_before_lock_or_any_write(tmp_path):
    # Registered test (15).
    cfg = _happy_cfg(tmp_path)

    with pytest.raises(ValueError):
        run_paper_cycle(cfg, source="raw_snapshot", scope="not_a_real_scope")

    assert not runtime_lock.runtime_lock_path(cfg, "prediction_cycle").exists()
    assert not (cfg.governance_root / "forward_paper_cycle.json").exists()
    assert not (cfg.governance_root / "scheduled_paper_cycle_report.json").exists()


def test_scoring_only_receipt_isolation_forward_paper_cycle_sentinel(tmp_path):
    # Registered test (2).
    cfg = _happy_cfg(tmp_path)
    sentinel_path = cfg.governance_root / "forward_paper_cycle.json"
    sentinel_path.parent.mkdir(parents=True, exist_ok=True)
    sentinel_path.write_text('{"sentinel": "untouched"}', encoding="utf-8")
    sentinel_bytes = sentinel_path.read_bytes()

    report = run_paper_cycle(cfg, source="raw_snapshot", scope="scoring_only")

    assert sentinel_path.read_bytes() == sentinel_bytes
    scheduled_report_path = cfg.governance_root / "scheduled_paper_cycle_report.json"
    assert scheduled_report_path.exists()
    on_disk = read_json(scheduled_report_path)
    assert on_disk["status"] == report["status"]
    assert report["scope"] == "scoring_only"


def test_scoring_only_leaves_dashboard_sentinels_untouched(tmp_path):
    # Registered test (3).
    cfg = _happy_cfg(tmp_path)
    dashboard_dir = cfg.output_root / "polymarket_dashboard"
    dashboard_dir.mkdir(parents=True, exist_ok=True)
    data_path = dashboard_dir / "dashboard_data.json"
    html_path = dashboard_dir / "index.html"
    data_path.write_text('{"sentinel": true}', encoding="utf-8")
    html_path.write_text("<html>sentinel</html>", encoding="utf-8")
    data_bytes = data_path.read_bytes()
    html_bytes = html_path.read_bytes()

    report = run_paper_cycle(cfg, source="raw_snapshot", scope="scoring_only")

    assert data_path.read_bytes() == data_bytes
    assert html_path.read_bytes() == html_bytes
    assert report["dashboard"] == {"status": "skipped_scoring_only"}


def test_scoring_only_leaves_shadow_ledger_sentinels_untouched(tmp_path):
    # Registered test (4).
    cfg = _happy_cfg(tmp_path)
    shadow_dir = cfg.output_root / "polymarket_shadow"
    shadow_dir.mkdir(parents=True, exist_ok=True)
    positions_path = shadow_dir / "shadow_positions.csv"
    fills_path = shadow_dir / "shadow_fills.csv"
    positions_path.write_text("sentinel\n", encoding="utf-8")
    fills_path.write_text("sentinel\n", encoding="utf-8")
    positions_bytes = positions_path.read_bytes()
    fills_bytes = fills_path.read_bytes()

    report = run_paper_cycle(cfg, source="raw_snapshot", scope="scoring_only")

    assert positions_path.read_bytes() == positions_bytes
    assert fills_path.read_bytes() == fills_bytes
    assert report["shadow_cohort"] == {"status": "skipped_scoring_only"}


def test_scoring_only_leaves_portfolio_sentinel_untouched_and_skips_five_blocks(tmp_path):
    # Registered test (5): portfolio sentinel untouched and all five skipped
    # blocks read skipped_scoring_only (shadow_cohort; broker + its two
    # profit-target keys as one block; cohort_pnl; dashboard; agent_runtime).
    cfg = _happy_cfg(tmp_path)
    portfolio_dir = cfg.output_root / "polymarket_portfolio"
    portfolio_dir.mkdir(parents=True, exist_ok=True)
    summary_path = portfolio_dir / "paper_trading_summary.json"
    fills_path = portfolio_dir / "paper_fills.csv"
    summary_path.write_text('{"sentinel": true}', encoding="utf-8")
    fills_path.write_text("sentinel\n", encoding="utf-8")
    summary_bytes = summary_path.read_bytes()
    fills_bytes = fills_path.read_bytes()

    report = run_paper_cycle(cfg, source="raw_snapshot", scope="scoring_only")

    assert summary_path.read_bytes() == summary_bytes
    assert fills_path.read_bytes() == fills_bytes
    assert report["broker"] == {"status": "skipped_scoring_only"}
    assert report["monthly_profit_target"] == {"status": "skipped_scoring_only"}
    assert report["actual_profit_target"] == {"status": "skipped_scoring_only"}
    assert report["shadow_cohort"] == {"status": "skipped_scoring_only"}
    assert report["cohort_pnl"] == {"status": "skipped_scoring_only"}
    assert report["dashboard"] == {"status": "skipped_scoring_only"}
    assert report["agent_runtime"] == {"status": "skipped_scoring_only"}


def test_scoring_only_websocket_source_persists_predictions_idempotently(tmp_path):
    # Registered test (6).
    cfg = _config(tmp_path)
    _seed_websocket_fixture(cfg)
    _seed_calibration_model(cfg)

    first = run_paper_cycle(cfg, source="websocket", scope="scoring_only")
    assert first["predictions"] == 2

    con = connect_db(cfg.database_path)
    try:
        count_first = con.execute("SELECT COUNT(*) FROM model_predictions").fetchone()[0]
    finally:
        con.close()
    assert count_first == 2

    second = run_paper_cycle(cfg, source="websocket", scope="scoring_only")
    assert second["predictions"] == 2

    con = connect_db(cfg.database_path)
    try:
        count_second = con.execute("SELECT COUNT(*) FROM model_predictions").fetchone()[0]
    finally:
        con.close()
    assert count_second == 2


# --- 143.2: scheduled_paper_cycle.py ----------------------------------------


def test_held_foreign_lock_skips_after_bounded_retries_with_no_writes(tmp_path, monkeypatch):
    # Registered test (7).
    cfg = _happy_cfg(tmp_path)
    sentinel_path = cfg.output_root / "polymarket_predictions" / "predictions.csv"
    write_csv(sentinel_path, [{"market_id": "sentinel", "token_id": "sentinel"}])
    sentinel_bytes = sentinel_path.read_bytes()

    _write_foreign_prediction_cycle_lock(cfg)
    monkeypatch.setattr(scheduled_paper_cycle, "LOCK_WAIT_MAX_SECONDS", 0.05)
    monkeypatch.setattr(scheduled_paper_cycle, "LOCK_WAIT_SLEEP_SECONDS", 0.01)

    receipt = run_scheduled_paper_cycle(cfg, source="raw_snapshot")

    assert receipt["status"] == "skipped_prediction_cycle_lock"
    assert receipt["exit_code"] == 75
    assert receipt["lock_attempts"] >= 2
    assert sentinel_path.read_bytes() == sentinel_bytes


def test_lock_absent_after_happy_run(tmp_path):
    # Registered test (8).
    cfg = _happy_cfg(tmp_path)

    receipt = run_scheduled_paper_cycle(cfg, source="raw_snapshot")

    assert receipt["status"] == "ran"
    assert not runtime_lock.runtime_lock_path(cfg, "prediction_cycle").exists()


def test_sigterm_unwinds_prediction_cycle_lock_and_restores_handler(tmp_path, monkeypatch):
    # Registered test (9).
    cfg = _happy_cfg(tmp_path)
    previous_handler = signal.getsignal(signal.SIGTERM)

    def _boom(*args, **kwargs):
        os.kill(os.getpid(), signal.SIGTERM)
        for _ in range(10_000_000):
            pass
        raise AssertionError("SIGTERM handler did not interrupt before this line")

    monkeypatch.setattr(paper_cycle_module, "build_features_v2", _boom)

    with pytest.raises(SystemExit):
        run_scheduled_paper_cycle(cfg, source="raw_snapshot")

    assert signal.getsignal(signal.SIGTERM) == previous_handler
    assert not runtime_lock.runtime_lock_path(cfg, "prediction_cycle").exists()


def test_overlay_disabled_is_loud_exit_one_not_silent_success(tmp_path):
    # Registered test (10).
    cfg = _config(tmp_path, mispricing_alpha_enabled=False)
    _seed_raw_snapshot_fixture(cfg)
    _seed_calibration_model(cfg)
    stale_summary_path = cfg.governance_root / "mispricing_alpha_live_summary.json"
    write_json(stale_summary_path, {"status": "scored", "generated_at_utc": "2026-01-01T00:00:00Z"})
    stale_bytes = stale_summary_path.read_bytes()

    receipt = run_scheduled_paper_cycle(cfg, source="raw_snapshot")

    assert receipt["status"] == "blocked_overlay_disabled"
    assert receipt["exit_code"] == 1
    assert receipt["mispricing_alpha_overlay_refreshed"] is False
    assert stale_summary_path.read_bytes() == stale_bytes


def test_readiness_blocked_cycle_is_exit_zero_not_a_failure(tmp_path):
    # Registered test (11): a readiness-blocked cycle is a legitimate
    # observation, not a scheduler failure.
    cfg = _config(tmp_path, min_paper_labels=2)
    _seed_raw_snapshot_fixture(cfg)
    _seed_calibration_model(cfg)

    receipt = run_scheduled_paper_cycle(cfg, source="raw_snapshot")

    assert receipt["status"] == "blocked_readiness"
    assert receipt["exit_code"] == 0
    assert receipt["cycle_completed"] is True


def test_missing_calibration_model_is_blocked_inputs(tmp_path):
    # Registered test (12).
    cfg = _config(tmp_path)
    _seed_raw_snapshot_fixture(cfg)
    # Deliberately no calibration model: load_prediction_models raises.

    receipt = run_scheduled_paper_cycle(cfg, source="raw_snapshot")

    assert receipt["status"] == "blocked_inputs"
    assert receipt["exit_code"] == 1
    assert not (cfg.output_root / "polymarket_predictions" / "predictions.csv").exists()


def test_trading_mode_live_is_blocked_inputs_with_no_lock_and_no_scoring_artifact(tmp_path):
    # Registered test (13).
    cfg = _config(tmp_path, trading_mode="live")

    receipt = run_scheduled_paper_cycle(cfg, source="raw_snapshot")

    assert receipt["status"] == "blocked_inputs"
    assert receipt["exit_code"] == 1
    assert not runtime_lock.runtime_lock_path(cfg, "prediction_cycle").exists()
    assert not (cfg.output_root / "polymarket_predictions" / "predictions.csv").exists()


def test_receipt_key_order_matches_registered_22_keys(tmp_path):
    # Registered test (14).
    cfg = _happy_cfg(tmp_path)

    receipt = run_scheduled_paper_cycle(cfg, source="raw_snapshot")

    assert list(receipt.keys()) == _REGISTERED_RECEIPT_KEY_ORDER
    on_disk = read_json(cfg.output_root / "polymarket_model_governance" / "scheduled_paper_cycle.json")
    assert list(on_disk.keys()) == _REGISTERED_RECEIPT_KEY_ORDER
    assert receipt["paper_trading_invoked"] is False
    assert receipt["live_trading_invoked"] is False
    assert receipt["peak_rss_bytes"] > 0


# --- 143.3: CLI --------------------------------------------------------------


def test_cli_scheduled_paper_cycle_returns_registered_exit_codes(tmp_path, monkeypatch):
    # Registered test (16).
    happy_cfg = _happy_cfg(tmp_path / "happy")
    code = cli_main(["scheduled-paper-cycle", "--config", str(happy_cfg.path), "--paper-source", "raw_snapshot"])
    assert code == 0

    locked_cfg = _happy_cfg(tmp_path / "locked")
    _write_foreign_prediction_cycle_lock(locked_cfg)
    monkeypatch.setattr(scheduled_paper_cycle, "LOCK_WAIT_MAX_SECONDS", 0.05)
    monkeypatch.setattr(scheduled_paper_cycle, "LOCK_WAIT_SLEEP_SECONDS", 0.01)
    code = cli_main(["scheduled-paper-cycle", "--config", str(locked_cfg.path), "--paper-source", "raw_snapshot"])
    assert code == 75

    overlay_disabled_cfg = _config(tmp_path / "overlay_disabled", mispricing_alpha_enabled=False)
    _seed_raw_snapshot_fixture(overlay_disabled_cfg)
    _seed_calibration_model(overlay_disabled_cfg)
    code = cli_main(
        ["scheduled-paper-cycle", "--config", str(overlay_disabled_cfg.path), "--paper-source", "raw_snapshot"]
    )
    assert code == 1


def test_existing_paper_cycle_cli_command_is_unchanged(tmp_path):
    # The existing paper-cycle command stays byte-identical: still defaults
    # to scope="full" and writes forward_paper_cycle.json.
    cfg = _happy_cfg(tmp_path)
    code = cli_main(["paper-cycle", "--config", str(cfg.path), "--paper-source", "raw_snapshot"])
    assert code == 0
    assert (cfg.governance_root / "forward_paper_cycle.json").exists()


# --- 143.7: review-round amendment ------------------------------------------


def _websocket_cfg(tmp_path: Path, *, collected_at_utc: str, **kwargs):
    cfg = _config(tmp_path, **kwargs)
    _seed_websocket_fixture(cfg, collected_at_utc=collected_at_utc)
    _seed_calibration_model(cfg)
    return cfg


def test_143_7a_every_writing_path_carries_both_invocation_literals(tmp_path):
    # Registered test (1): the artifact written on the success path carries
    # both literals, and so does the artifact written on EACH blocked path.
    # AGENTS.md L131-135; the 22-key scheduler receipt carrying them is a
    # different artifact and does not discharge this requirement.
    report_name = "scheduled_paper_cycle_report.json"

    # Success path.
    happy = _happy_cfg(tmp_path / "happy")
    run_paper_cycle(happy, source="raw_snapshot", scope="scoring_only")
    payload = read_json(happy.governance_root / report_name)
    assert payload["paper_trading_invoked"] is False
    assert payload["live_trading_invoked"] is False

    # Blocked path: trading.mode is not paper/backtest.
    mode_cfg = _config(tmp_path / "mode", trading_mode="live")
    run_paper_cycle(mode_cfg, source="raw_snapshot", scope="scoring_only")
    payload = read_json(mode_cfg.governance_root / report_name)
    assert payload["status"] == "blocked"
    assert payload["paper_trading_invoked"] is False
    assert payload["live_trading_invoked"] is False

    # Blocked path: feature/model load raises (no calibration model seeded).
    inputs_cfg = _config(tmp_path / "inputs")
    _seed_raw_snapshot_fixture(inputs_cfg)
    run_paper_cycle(inputs_cfg, source="raw_snapshot", scope="scoring_only")
    payload = read_json(inputs_cfg.governance_root / report_name)
    assert payload["status"] == "blocked"
    assert payload["paper_trading_invoked"] is False
    assert payload["live_trading_invoked"] is False

    # Blocked path: prediction_cycle lock already held.
    lock_cfg = _happy_cfg(tmp_path / "locked")
    _write_foreign_prediction_cycle_lock(lock_cfg)
    run_paper_cycle(lock_cfg, source="raw_snapshot", scope="scoring_only")
    payload = read_json(lock_cfg.governance_root / report_name)
    assert payload["status"] == "skipped_existing_prediction_cycle"
    assert payload["paper_trading_invoked"] is False
    assert payload["live_trading_invoked"] is False


def test_143_7a_full_scope_also_carries_the_flags_per_registered_exception(tmp_path):
    # The §143.1 "byte-identical" clause was amended: AGENTS.md L131-135 wins
    # and the flags must NOT be skipped on the full path.
    cfg = _happy_cfg(tmp_path)

    run_paper_cycle(cfg, source="raw_snapshot")

    payload = read_json(cfg.governance_root / "forward_paper_cycle.json")
    assert payload["paper_trading_invoked"] is False
    assert payload["live_trading_invoked"] is False


def test_143_7b_absent_websocket_file_is_blocked_inputs_not_success(tmp_path):
    # Registered test (2): absent websocket_market_features.csv -> blocked_inputs
    # at exit 1. A nonzero exit is exactly what stops the scheduler refreshing
    # last_success_utc (stamp_status refreshes it only on exit 0).
    cfg = _config(tmp_path)
    _seed_calibration_model(cfg)
    _seed_labels(cfg)
    assert not (cfg.output_root / "polymarket_training" / "websocket_market_features.csv").exists()

    receipt = run_scheduled_paper_cycle(cfg, source="websocket")

    assert receipt["status"] == "blocked_inputs"
    assert receipt["exit_code"] == 1
    assert receipt["cycle_completed"] is False


def test_143_7b_empty_but_present_websocket_file_is_blocked_inputs(tmp_path):
    # Registered test (3).
    cfg = _config(tmp_path)
    _seed_calibration_model(cfg)
    _seed_labels(cfg)
    ws_path = cfg.output_root / "polymarket_training" / "websocket_market_features.csv"
    ws_path.parent.mkdir(parents=True, exist_ok=True)
    ws_path.write_text("", encoding="utf-8")

    receipt = run_scheduled_paper_cycle(cfg, source="websocket")

    assert receipt["status"] == "blocked_inputs"
    assert receipt["exit_code"] == 1
    assert receipt["cycle_completed"] is False


def test_143_7b_websocket_file_older_than_ceiling_is_blocked_inputs(tmp_path):
    # Registered test (4): older than the registered ceiling yields
    # blocked_inputs EVEN THOUGH it parses and scores. The fresh control below
    # proves the age check -- not a zero prediction count -- is what blocks it.
    stale_cfg = _websocket_cfg(
        tmp_path / "stale",
        collected_at_utc=_fresh_stamp(scheduled_paper_cycle.MAX_WEBSOCKET_OBSERVATION_AGE_SECONDS + 600),
    )

    stale_receipt = run_scheduled_paper_cycle(stale_cfg, source="websocket")

    assert stale_receipt["status"] == "blocked_inputs"
    assert stale_receipt["exit_code"] == 1
    assert stale_receipt["cycle_completed"] is False
    # It really did parse and score: the same fixture, freshly stamped, is not
    # blocked on inputs and completes.
    assert stale_receipt["predictions"] == 2

    fresh_cfg = _websocket_cfg(tmp_path / "fresh", collected_at_utc=_fresh_stamp(30))
    fresh_receipt = run_scheduled_paper_cycle(fresh_cfg, source="websocket")

    assert fresh_receipt["status"] != "blocked_inputs"
    assert fresh_receipt["cycle_completed"] is True
    assert fresh_receipt["predictions"] == 2


def test_143_7b_non_finite_websocket_timestamp_is_blocked_inputs_never_fresh(tmp_path):
    # Registered fail-closed requirement: safe_float("nan") returns NaN and
    # `nan <= ceiling` is False, so an unguarded comparison would read a
    # corrupt timestamp as FRESH -- the exact hole item (b) closes.
    for bad_stamp in ("nan", "inf", "-inf"):
        cfg = _websocket_cfg(tmp_path / f"nonfinite-{bad_stamp}", collected_at_utc=bad_stamp)

        receipt = run_scheduled_paper_cycle(cfg, source="websocket")

        assert receipt["status"] == "blocked_inputs", bad_stamp
        assert receipt["exit_code"] == 1, bad_stamp
        assert receipt["cycle_completed"] is False, bad_stamp


def test_143_7b_observation_age_is_unmeasurable_for_every_bad_input_class(tmp_path):
    # One assertion per registered input class, directly on the helper, so the
    # fail-closed contract is proved independently of what the cycle scores.
    absent = _config(tmp_path / "absent")
    assert scheduled_paper_cycle._websocket_observation_age_seconds(absent) is None

    empty = _config(tmp_path / "empty")
    ws_path = empty.output_root / "polymarket_training" / "websocket_market_features.csv"
    ws_path.parent.mkdir(parents=True, exist_ok=True)
    ws_path.write_text("", encoding="utf-8")
    assert scheduled_paper_cycle._websocket_observation_age_seconds(empty) is None

    malformed = _config(tmp_path / "malformed")
    ws_path = malformed.output_root / "polymarket_training" / "websocket_market_features.csv"
    ws_path.parent.mkdir(parents=True, exist_ok=True)
    ws_path.write_text("not,a,known,header\n1,2,3,4\n", encoding="utf-8")
    assert scheduled_paper_cycle._websocket_observation_age_seconds(malformed) is None

    for bad_stamp in ("not-a-timestamp", "nan", "inf", "-inf", ""):
        unparseable = _config(tmp_path / f"bad-{bad_stamp or 'blank'}")
        _seed_websocket_fixture(unparseable, collected_at_utc=bad_stamp)
        assert scheduled_paper_cycle._websocket_observation_age_seconds(unparseable) is None, bad_stamp

    measurable = _config(tmp_path / "measurable")
    _seed_websocket_fixture(measurable, collected_at_utc=_fresh_stamp(30))
    age = scheduled_paper_cycle._websocket_observation_age_seconds(measurable)
    assert age is not None and 0.0 <= age < 300.0


def test_143_7b_registered_ceiling_is_tighten_only(tmp_path):
    # A configured value may only SHRINK the registered maximum; a larger,
    # negative, or non-finite override falls back to the registered default.
    registered = scheduled_paper_cycle.MAX_WEBSOCKET_OBSERVATION_AGE_SECONDS
    assert registered == 1800.0

    cfg = _config(tmp_path)
    assert scheduled_paper_cycle._websocket_observation_max_age_seconds(cfg) == pytest.approx(
        registered, abs=1e-12
    )

    cfg.raw["scheduled_paper_cycle"] = {"max_websocket_observation_age_seconds": 600}
    assert scheduled_paper_cycle._websocket_observation_max_age_seconds(cfg) == pytest.approx(
        600.0, abs=1e-12
    )

    for widening in (999999, -1, float("nan"), float("inf"), "abc", None):
        cfg.raw["scheduled_paper_cycle"] = {"max_websocket_observation_age_seconds": widening}
        assert scheduled_paper_cycle._websocket_observation_max_age_seconds(cfg) == pytest.approx(
            registered, abs=1e-12
        ), widening


def test_143_7c_disabled_overlay_publishes_no_signal_file(tmp_path):
    # Registered test (5): with mispricing_alpha.enabled false, trade_signals.csv
    # is NOT written and its pre-existing content is byte-unchanged, and the
    # status is blocked_overlay_disabled at exit 1. Detection must happen BEFORE
    # generate_signals, because exit 1 cannot retract a file already on disk.
    cfg = _config(tmp_path, mispricing_alpha_enabled=False)
    _seed_raw_snapshot_fixture(cfg)
    _seed_calibration_model(cfg)
    signals_path = cfg.output_root / "polymarket_predictions" / "trade_signals.csv"
    signals_path.parent.mkdir(parents=True, exist_ok=True)
    signals_path.write_text("sentinel-not-written-by-this-run\n", encoding="utf-8")
    sentinel_bytes = signals_path.read_bytes()

    receipt = run_scheduled_paper_cycle(cfg, source="raw_snapshot")

    assert receipt["status"] == "blocked_overlay_disabled"
    assert receipt["exit_code"] == 1
    assert signals_path.read_bytes() == sentinel_bytes
    assert receipt["signals_approved"] == 0
    assert receipt["paper_signals_published"] == 0


def test_143_7c_disabled_overlay_creates_no_signal_file_where_none_existed(tmp_path):
    # The same path must not CREATE the file either.
    cfg = _config(tmp_path, mispricing_alpha_enabled=False)
    _seed_raw_snapshot_fixture(cfg)
    _seed_calibration_model(cfg)
    signals_path = cfg.output_root / "polymarket_predictions" / "trade_signals.csv"
    assert not signals_path.exists()

    receipt = run_scheduled_paper_cycle(cfg, source="raw_snapshot")

    assert receipt["status"] == "blocked_overlay_disabled"
    assert not signals_path.exists()


def test_143_7c_enabled_overlay_still_publishes_signals(tmp_path):
    # The tightening must not suppress the normal path: with the overlay
    # enabled and refreshing, generate_signals still runs and writes.
    cfg = _happy_cfg(tmp_path)

    receipt = run_scheduled_paper_cycle(cfg, source="raw_snapshot")

    assert receipt["status"] == "ran"
    assert (cfg.output_root / "polymarket_predictions" / "trade_signals.csv").exists()


def test_143_7e_first_attempt_acquisition_reports_near_zero_lock_wait(tmp_path):
    # Registered test (7): lock_wait_seconds under one second on an
    # uncontended run, while duration_seconds reflects the real runtime.
    cfg = _happy_cfg(tmp_path)

    receipt = run_scheduled_paper_cycle(cfg, source="raw_snapshot")

    assert receipt["lock_attempts"] == 1
    assert receipt["lock_wait_seconds"] < 1.0
    assert receipt["duration_seconds"] >= receipt["lock_wait_seconds"]


def test_143_7e_second_attempt_success_reports_about_one_sleep_interval(tmp_path, monkeypatch):
    # Registered test (8): a contended run that succeeds on its SECOND attempt
    # reports approximately one sleep interval, not the full duration.
    cfg = _happy_cfg(tmp_path)
    lock_path = _write_foreign_prediction_cycle_lock(cfg)
    monkeypatch.setattr(scheduled_paper_cycle, "LOCK_WAIT_MAX_SECONDS", 30.0)
    monkeypatch.setattr(scheduled_paper_cycle, "LOCK_WAIT_SLEEP_SECONDS", 0.2)

    real_sleep = scheduled_paper_cycle.time.sleep

    def _release_then_sleep(seconds):
        # Free the lock during the first inter-attempt sleep so the second
        # attempt acquires it.
        if lock_path.exists():
            lock_path.unlink()
        real_sleep(seconds)

    monkeypatch.setattr(scheduled_paper_cycle.time, "sleep", _release_then_sleep)

    receipt = run_scheduled_paper_cycle(cfg, source="raw_snapshot")

    assert receipt["lock_attempts"] == 2
    assert receipt["status"] == "ran"
    # Only the contended first attempt plus its sleep is counted, never the
    # successful attempt's scoring runtime.
    assert receipt["lock_wait_seconds"] < receipt["duration_seconds"]
    assert receipt["lock_wait_seconds"] < 2.0


def test_143_7f_scoring_only_reports_zero_shadow_candidates_forwarded(tmp_path):
    # Under scoring_only the shadow update is skipped, so nothing was
    # forwarded; the count must not claim otherwise, and the skip stays
    # visible in the sibling shadow_cohort block.
    cfg = _happy_cfg(tmp_path)

    report = run_paper_cycle(cfg, source="raw_snapshot", scope="scoring_only")

    assert report["longshot_bias"]["shadow_candidates_forwarded"] == 0
    assert report["shadow_cohort"] == {"status": "skipped_scoring_only"}


def test_143_7f_full_scope_still_reports_the_real_forward_count(tmp_path):
    # The honesty fix must not zero a real forward on the full path.
    cfg = _happy_cfg(tmp_path)

    report = run_paper_cycle(cfg, source="raw_snapshot")

    assert report["shadow_cohort"].get("status") != "skipped_scoring_only"
    assert isinstance(report["longshot_bias"]["shadow_candidates_forwarded"], int)


def test_143_7b_freshness_check_is_unconditional_across_every_source(tmp_path):
    # PINS THE FIX: an earlier draft ran the freshness check only when
    # source == "websocket" and defaulted the verdict to True otherwise -- a
    # freshness gate defaulting to FRESH. The deployed scheduler hardcodes
    # --paper-source websocket (run_vps_ops_scheduler.sh:682), so that hole
    # was unreachable but not closed: any other source classified `ran` at
    # exit 0 having validated nothing. A non-websocket source must be held to
    # the SAME observation contract.
    for source in ("raw_snapshot", "websocket", "historical", "all"):
        # Absent websocket observation.
        absent = _config(tmp_path / f"absent-{source}")
        _seed_calibration_model(absent)
        _seed_labels(absent)
        raw_path = (
            absent.data_root / "outputs" / "polymarket_fixed" / "worldcup" / "ml" / "raw_market_snapshots.csv"
        )
        write_csv(
            raw_path,
            [
                {
                    "snapshot_timestamp": "2026-06-25T10:00:00Z",
                    "market_id": "market-forward-1",
                    "market_slug": "synthetic-forward-market",
                    "token_id": "token-forward-yes",
                    "question": "Will the synthetic event happen?",
                    "category": "worldcup",
                    "best_bid": 0.35,
                    "best_ask": 0.40,
                    "top_bid_size": 1000,
                    "top_ask_size": 1000,
                    "bid_depth_1pct": 1000,
                    "ask_depth_1pct": 1000,
                    "bid_depth_5pct": 1000,
                    "ask_depth_5pct": 1000,
                    "websocket_quote_age_seconds": 30,
                    "midpoint": 0.375,
                    "liquidity": 1000,
                    "close_time": "2026-12-31T00:00:00Z",
                }
            ],
        )
        assert not (absent.output_root / "polymarket_training" / "websocket_market_features.csv").exists()

        receipt = run_scheduled_paper_cycle(absent, source=source)

        assert receipt["status"] == "blocked_inputs", source
        assert receipt["exit_code"] == 1, source
        assert receipt["cycle_completed"] is False, source

        # Present but stale beyond the registered ceiling.
        stale = _config(tmp_path / f"stale-{source}")
        _seed_raw_snapshot_fixture(stale)
        _seed_calibration_model(stale)
        _seed_fresh_websocket_observation(
            stale,
            seconds_ago=scheduled_paper_cycle.MAX_WEBSOCKET_OBSERVATION_AGE_SECONDS + 600,
        )

        receipt = run_scheduled_paper_cycle(stale, source=source)

        assert receipt["status"] == "blocked_inputs", source
        assert receipt["exit_code"] == 1, source
        assert receipt["cycle_completed"] is False, source


def test_143_7b_future_dated_observation_is_unmeasurable_not_maximally_fresh(tmp_path):
    # A timestamp corrupted to the future (or a clock-skewed collector row)
    # produced a negative age that clamped to 0.0 -- maximally fresh, forever,
    # and unreachable by any ceiling because the corrupt row is always
    # "newer" than every honest one.
    #
    # There is NO tolerance window. The 60s allowance this test used to assert
    # was unregistered for this gate and left a narrower copy of the same
    # fail-open: a row 1-60s ahead still clamped to 0.0 and still outranked
    # every honest row, so one corrupt observation could mask a wholly stale
    # corpus. The collector shares this scheduler's host, so there is no skew
    # to tolerate.
    assert not hasattr(scheduled_paper_cycle, "FUTURE_OBSERVATION_TOLERANCE_SECONDS")

    # Even ONE second ahead is unmeasurable, never fresh.
    for seconds_ahead in (1, 30, 60, 180, 86_400, 365 * 24 * 3600):
        future = _config(tmp_path / f"future-{int(seconds_ahead)}")
        _seed_websocket_fixture(future, collected_at_utc=_fresh_stamp(-seconds_ahead))
        assert scheduled_paper_cycle._websocket_observation_age_seconds(future) is None, seconds_ahead

    # End to end: a far-future observation blocks the cycle rather than
    # reporting a successful run.
    cfg = _websocket_cfg(tmp_path / "future-cycle", collected_at_utc="2099-01-01T00:00:00Z")

    receipt = run_scheduled_paper_cycle(cfg, source="websocket")

    assert receipt["status"] == "blocked_inputs"
    assert receipt["exit_code"] == 1
    assert receipt["cycle_completed"] is False
