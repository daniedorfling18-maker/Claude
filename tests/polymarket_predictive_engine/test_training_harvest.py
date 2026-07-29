from __future__ import annotations

from collections.abc import Sequence
import json
import os
from pathlib import Path

import yaml

from polymarket_predictive_engine.cli import COMMANDS
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.training_harvest import run_training_harvest
from polymarket_predictive_engine.upstream_latency import record_upstream_latency
from polymarket_predictive_engine.utils import read_json


def _config(tmp_path: Path):
    raw = yaml.safe_load(
        Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8")
    )
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path), path


def _step_name(command: Sequence[str]) -> str:
    parts = list(command)
    if "polymarket_predictive_engine.cli" in parts:
        return parts[parts.index("polymarket_predictive_engine.cli") + 1]
    return Path(parts[1]).name


def test_failed_middle_step_still_runs_retention_and_anchor(tmp_path: Path) -> None:
    cfg, config_path = _config(tmp_path)
    calls: list[str] = []

    def runner(command: Sequence[str], timeout_seconds: int) -> int:
        assert timeout_seconds in {300, 1800}
        name = _step_name(command)
        calls.append(name)
        return 1 if name == "maker-fill-replay" else 0

    payload = run_training_harvest(
        cfg,
        config_path=str(config_path),
        runner=runner,
        clock=lambda: 0.0,
        timestamp=lambda: "2026-07-15T00:00:00Z",
    )

    assert payload["status"] == "partial_failure"
    assert payload["failed_steps"] == ["maker_fill_replay"]
    assert calls[:6] == [
        "backfill-resolved-markets",
        "resolve-websocket-markets",
        "collect-price-history",
        "collect-historical-bid-ask",
        "build-leakage-safe-training",
        "train-skill-model",
    ]
    assert calls[-2:] == ["corpus-retention", "anchor-ledgers"]
    assert calls.index("reward-epoch-sample") == calls.index("maker-carry-study") + 1
    assert payload["mandatory_tail_completed"] is True
    by_step = {row["step"]: row for row in payload["steps"]}
    assert by_step["maker_fill_replay"]["exit_code"] == 1
    assert by_step["corpus_retention"]["status"] == "ok"
    assert by_step["anchor_ledgers"]["status"] == "ok"
    assert all("duration_seconds" in row for row in payload["steps"])

    persisted = read_json(cfg.output_root / "ops_scheduler" / "training_harvest.json")
    assert persisted["paper_trading_invoked"] is False
    assert persisted["live_trading_invoked"] is False


def test_price_history_child_surfaces_collector_coverage(tmp_path: Path) -> None:
    cfg, config_path = _config(tmp_path)

    def runner(command: Sequence[str], timeout_seconds: int) -> int:
        del timeout_seconds
        if _step_name(command) == "collect-price-history":
            summary = cfg.governance_root / "historical_price_history_summary.json"
            summary.parent.mkdir(parents=True, exist_ok=True)
            summary.write_text(json.dumps({
                "requested_tokens": 3,
                "collected_tokens": 0,
                "quality_status_counts": {"ok": 0, "empty_history": 0, "fetch_error": 3},
            }), encoding="utf-8")
        return 0

    payload = run_training_harvest(cfg, config_path=str(config_path), runner=runner, clock=lambda: 0.0)
    row = next(item for item in payload["steps"] if item["step"] == "collect_price_history")
    assert row["exit_code"] == 0
    assert row["coverage"] == {
        "status": "observed",
        "requested_count": 3,
        "collected_count": 0,
        "quality_status_counts": {"ok": 0, "empty_history": 0, "fetch_error": 3},
    }


def test_latency_spool_rows_are_flagged_and_receipt_is_aggregated(tmp_path: Path) -> None:
    cfg, config_path = _config(tmp_path)
    captured: list[dict] = []

    def runner(command: Sequence[str], timeout_seconds: int) -> int:
        del command, timeout_seconds
        record_upstream_latency("https://clob.polymarket.com/prices-history?market=secret", 5.0)
        spool = Path(os.environ["PM_HARVEST_LATENCY_LOG"])
        captured.append(json.loads(spool.read_text(encoding="utf-8").splitlines()[-1]))
        return 0

    payload = run_training_harvest(cfg, config_path=str(config_path), runner=runner, clock=lambda: 0.0)
    assert all(row["paper_trading_invoked"] is False for row in captured)
    assert all(row["live_trading_invoked"] is False for row in captured)
    assert payload["upstream_latency"] == [{
        "host": "clob.polymarket.com", "path_tail": "prices-history",
        "request_count": len(payload["steps"]), "p50_seconds": 5.0, "max_seconds": 5.0,
    }]
    assert not (cfg.output_root / "ops_scheduler" / ".training_harvest_latency.jsonl").exists()


def test_deadline_skips_unstarted_work_but_not_mandatory_tail(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cfg, config_path = _config(tmp_path)
    monkeypatch.setenv("OPS_TRAINING_HARVEST_DEADLINE_SECONDS", "1")
    clock_value = [0.0]
    calls: list[str] = []

    def clock() -> float:
        return clock_value[0]

    def runner(command: Sequence[str], timeout_seconds: int) -> int:
        del timeout_seconds
        calls.append(_step_name(command))
        clock_value[0] += 2.0
        return 0

    payload = run_training_harvest(
        cfg,
        config_path=str(config_path),
        runner=runner,
        clock=clock,
        timestamp=lambda: "2026-07-15T00:00:00Z",
    )

    assert payload["deadline_seconds"] == 1
    assert payload["status"] == "deadline_exceeded"
    assert calls == ["backfill-resolved-markets", "corpus-retention", "anchor-ledgers"]
    assert len(payload["skipped_deadline_steps"]) == 25
    assert payload["mandatory_tail_completed"] is True


def test_scheduler_uses_registered_resilient_harvest_entrypoint() -> None:
    script = Path("scripts/run_vps_ops_scheduler.sh").read_text(encoding="utf-8")

    assert "training-harvest" in COMMANDS
    assert "collect-historical-bid-ask" in COMMANDS
    assert "build-leakage-safe-training" in COMMANDS
    assert "h3-smart-flow-evaluate" in COMMANDS
    assert "h2-dutch-evaluate" in COMMANDS
    assert "OPS_TRAINING_HARVEST_DEADLINE_SECONDS:-21600" in script
    assert "polymarket_predictive_engine.cli training-harvest" in script
    assert '"last_success_utc": now.isoformat() if exit_code == 0' in script
    function = script.split("run_training_harvest() {", 1)[1].split(
        "run_maker_study_intraday() {", 1
    )[0]
    assert "set -e" not in function
    assert "polymarket_predictive_engine.cli backfill-resolved-markets" not in function


def test_scheduler_rearms_harvest_from_successful_completion_not_start() -> None:
    script = Path("scripts/run_vps_ops_scheduler.sh").read_text(encoding="utf-8")
    loop = script.split("while :; do", 1)[1]
    harvest_block = loop.split("if training_harvest_retry_ready; then", 1)[1].split(
        'if [ "$(seconds_since_stamp maker_study_intraday)"', 1
    )[0]
    retry_gate = script.split("training_harvest_retry_ready() {", 1)[1].split(
        "successful_schedule_skip_kind() {", 1
    )[0]
    function = script.split("run_training_harvest() {", 1)[1].split(
        "run_maker_study_intraday() {", 1
    )[0]

    assert "successful_completion_epoch" in script
    assert '$(seconds_since_success_stamp training_harvest)' in retry_gate
    assert '$(seconds_since_completion_stamp training_harvest)' in retry_gate
    assert '"$HARVEST_INTERVAL"' in retry_gate
    assert '"$HARVEST_RETRY_INTERVAL"' in retry_gate
    assert "if training_harvest_retry_ready; then" in loop
    assert "touch_attempt_stamp training_harvest" in harvest_block
    assert "touch_stamp training_harvest" not in harvest_block
    # WO-128.2: re-arming keys off the HARVEST outcome specifically - an
    # anchor-tail failure must not re-run the multi-minute collection, so the
    # success stamp cannot depend on the combined process $CODE.
    assert 'if [ "$HARVEST_CODE" -eq 0 ]; then\n    touch_success_stamp training_harvest' in function
    assert function.index("stamp_status training_harvest") < function.index(
        "touch_success_stamp training_harvest"
    )
    assert 'TRAINING_AGE="$(seconds_since_success_stamp training_harvest)"' in script


def test_harvest_retry_backoff_is_measured_from_completion(tmp_path: Path) -> None:
    out_dir = tmp_path / "ops"
    out_dir.mkdir()
    probe = f"""
OPS_SCHEDULER_LIBRARY_ONLY=1 OPS_SCHEDULER_OUT_DIR='{out_dir}' . scripts/run_vps_ops_scheduler.sh
printf '%s\n' "$(( $(date -u +%s) - HARVEST_INTERVAL - 1 ))" > "$OUT_DIR/last_success_training_harvest"
touch_completion_stamp training_harvest
! training_harvest_retry_ready
"""
    import subprocess
    subprocess.run(["sh", "-c", probe], check=True)


def test_scheduler_stamps_harvest_and_anchor_tail_independently() -> None:
    script = Path("scripts/run_vps_ops_scheduler.sh").read_text(encoding="utf-8")
    function = script.split("run_training_harvest() {", 1)[1].split(
        "run_maker_study_intraday() {", 1
    )[0]

    assert 'row.get("step") != "anchor_ledgers"' in function
    assert 'stamp_status training_harvest "$HARVEST_CODE"' in function
    assert 'stamp_status training_harvest_anchor_tail "$ANCHOR_TAIL_CODE"' in function
    assert 'if [ "$HARVEST_CODE" -eq 0 ]; then\n    touch_success_stamp training_harvest' in function
    assert function.index("stamp_status training_harvest ") < function.index(
        "touch_success_stamp training_harvest"
    )
