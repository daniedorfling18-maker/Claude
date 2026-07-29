from __future__ import annotations

from collections.abc import Sequence
import json
import subprocess
import sys
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
        assert timeout_seconds in {300, 1200, 1800}
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


def test_collection_timeout_degrades_coverage_and_later_children_run(tmp_path: Path) -> None:
    cfg, config_path = _config(tmp_path)
    calls: list[str] = []

    def runner(command: Sequence[str], timeout_seconds: int) -> int:
        name = _step_name(command)
        calls.append(name)
        if name == "collect-price-history":
            assert timeout_seconds == 1800
            return 124
        return 0

    payload = run_training_harvest(
        cfg,
        config_path=str(config_path),
        runner=runner,
        clock=lambda: 0.0,
        timestamp=lambda: "2026-07-29T13:37:00Z",
    )

    row = next(row for row in payload["steps"] if row["step"] == "collect_price_history")
    assert row["status"] == "timed_out"
    assert row["timeout_degrades_coverage"] is True
    assert payload["status"] == "coverage_degraded"
    assert payload["successful_completion"] is True
    assert "maker-carry-study" in calls
    assert calls[-1] == "anchor-ledgers"


def test_non_timeout_failure_remains_fail_loud(tmp_path: Path) -> None:
    cfg, config_path = _config(tmp_path)

    payload = run_training_harvest(
        cfg,
        config_path=str(config_path),
        runner=lambda command, timeout: 7 if _step_name(command) == "collect-price-history" else 0,
        clock=lambda: 0.0,
        timestamp=lambda: "2026-07-29T13:37:00Z",
    )

    assert payload["status"] == "partial_failure"
    assert payload["successful_completion"] is False
    assert payload["failed_steps"] == ["collect_price_history"]


def test_latency_summary_strips_query_token_and_credentials(tmp_path: Path, monkeypatch) -> None:
    cfg, config_path = _config(tmp_path)
    token_id = "1234567890123456789012345678901234567890"

    def runner(command: Sequence[str], timeout_seconds: int) -> int:
        del command, timeout_seconds
        record_upstream_latency(
            f"https://user:secret@clob.polymarket.com/prices-history?market={token_id}",
            5.125,
        )
        return 0

    payload = run_training_harvest(
        cfg,
        config_path=str(config_path),
        runner=runner,
        clock=lambda: 0.0,
        timestamp=lambda: "2026-07-29T13:37:00Z",
    )

    assert payload["upstream_latency"] == [{
        "host": "clob.polymarket.com",
        "path_tail": "prices-history",
        "request_count": len(payload["steps"]),
        "p50_seconds": 5.125,
        "max_seconds": 5.125,
    }]
    serialized = json.dumps(payload["upstream_latency"])
    assert token_id not in serialized
    assert "secret" not in serialized
    assert "user" not in serialized


def _run_scheduler_harvest_probe(tmp_path: Path, payload: dict) -> tuple[list[str], list[str]]:
    out_dir = tmp_path / "ops"
    out_dir.mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python"
    fake_python.write_text(
        "#!/bin/sh\nif [ \"$1\" != \"-m\" ]; then exec " + sys.executable
        + " \"$@\"; fi\nprintf '%s\\n' '" + json.dumps(payload)
        + f"' >'{out_dir}/training_harvest.json'\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    probe = f"""
OPS_SCHEDULER_LIBRARY_ONLY=1 OPS_SCHEDULER_OUT_DIR='{out_dir}' PATH='{fake_bin}':$PATH . scripts/run_vps_ops_scheduler.sh
wait_with_safety_pulses() {{ wait "$1"; }}
stamp_status() {{ printf '%s:%s\n' "$1" "$2" >>'{tmp_path}/stamps'; }}
touch_success_stamp() {{ printf '%s\n' "$1" >>'{tmp_path}/success'; }}
run_training_harvest
"""
    subprocess.run(["sh", "-c", probe], check=True, cwd=Path.cwd())
    stamps = (tmp_path / "stamps").read_text(encoding="utf-8").splitlines()
    success_path = tmp_path / "success"
    successes = success_path.read_text(encoding="utf-8").splitlines() if success_path.exists() else []
    return stamps, successes


def test_scheduler_coverage_timeout_stamps_row_without_refreshing_success(tmp_path: Path) -> None:
    stamps, successes = _run_scheduler_harvest_probe(tmp_path, {
        "coverage_degraded_steps": ["collect_price_history"],
        "steps": [
            {"step": "collect_price_history", "status": "timed_out", "exit_code": 124, "timeout_degrades_coverage": True},
            {"step": "maker_carry_study", "status": "ok", "exit_code": 0},
            {"step": "anchor_ledgers", "status": "ok", "exit_code": 0},
        ],
    })
    assert stamps == ["training_harvest:0", "training_harvest_anchor_tail:0"]
    assert successes == []


def test_scheduler_clean_harvest_refreshes_success_stamp(tmp_path: Path) -> None:
    stamps, successes = _run_scheduler_harvest_probe(tmp_path, {
        "coverage_degraded_steps": [],
        "steps": [{"step": "anchor_ledgers", "status": "ok", "exit_code": 0}],
    })
    assert stamps == ["training_harvest:0", "training_harvest_anchor_tail:0"]
    assert successes == ["training_harvest"]


def test_scheduler_non_timeout_failure_does_not_refresh_success(tmp_path: Path) -> None:
    stamps, successes = _run_scheduler_harvest_probe(tmp_path, {
        "coverage_degraded_steps": [],
        "steps": [
            {"step": "collect_price_history", "status": "failed", "exit_code": 7},
            {"step": "anchor_ledgers", "status": "ok", "exit_code": 0},
        ],
    })
    assert stamps == ["training_harvest:1", "training_harvest_anchor_tail:0"]
    assert successes == []


def test_scheduler_rederives_timeout_allowlist_and_boolean_flag(tmp_path: Path) -> None:
    stamps, successes = _run_scheduler_harvest_probe(tmp_path, {
        "coverage_degraded_steps": [],
        "steps": [
            {"step": "collect_price_history", "status": "timed_out", "exit_code": 124, "timeout_degrades_coverage": "false"},
            {"step": "decision_policy", "status": "timed_out", "exit_code": 124, "timeout_degrades_coverage": True},
            {"step": "anchor_ledgers", "status": "ok", "exit_code": 0},
        ],
    })
    assert stamps == ["training_harvest:1", "training_harvest_anchor_tail:0"]
    assert successes == []


def test_operator_tightening_controls_collection_child_timeout(tmp_path: Path, monkeypatch) -> None:
    cfg, config_path = _config(tmp_path)
    monkeypatch.setenv("OPS_TRAINING_HARVEST_TIMEOUT_SECONDS", "300")
    observed: dict[str, int] = {}

    def runner(command: Sequence[str], timeout_seconds: int) -> int:
        observed[_step_name(command)] = timeout_seconds
        return 0

    run_training_harvest(cfg, config_path=str(config_path), runner=runner, clock=lambda: 0.0)
    assert observed["collect-price-history"] == 300
    assert observed["maker-carry-study"] == 300


def test_degraded_harvest_retries_after_retry_interval_same_day(tmp_path: Path) -> None:
    out_dir = tmp_path / "ops"
    out_dir.mkdir()
    now = int(subprocess.check_output(["date", "-u", "+%s"], text=True).strip())
    (out_dir / "last_success_training_harvest").write_text(str(now - 90_000), encoding="utf-8")
    (out_dir / "last_attempt_training_harvest").write_text(str(now - 1_801), encoding="utf-8")
    subprocess.run(
        ["sh", "-c", f"OPS_SCHEDULER_LIBRARY_ONLY=1 OPS_SCHEDULER_OUT_DIR='{out_dir}' . scripts/run_vps_ops_scheduler.sh; training_harvest_retry_ready"],
        check=True,
        cwd=Path.cwd(),
    )


def test_latency_summary_rejects_hex_tails_and_clamps_nonfinite(tmp_path: Path) -> None:
    from polymarket_predictive_engine.training_harvest import _latency_summary

    path = tmp_path / "latency.jsonl"
    rows = [
        {"host": "example.com", "path_tail": "a" * 32, "duration_seconds": 1},
        {"host": "example.com", "path_tail": "b" * 64, "duration_seconds": 1},
        {"host": "example.com", "path_tail": "prices-history", "duration_seconds": float("inf")},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    assert _latency_summary(path) == [{
        "host": "example.com", "path_tail": "prices-history", "request_count": 1,
        "p50_seconds": 0.0, "max_seconds": 0.0,
    }]

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
    assert '$(seconds_since_attempt_stamp training_harvest)' in retry_gate
    assert '"$HARVEST_INTERVAL"' in retry_gate
    assert '"$HARVEST_RETRY_INTERVAL"' in retry_gate
    assert "if training_harvest_retry_ready; then" in loop
    assert "touch_attempt_stamp training_harvest" in harvest_block
    assert "touch_stamp training_harvest" not in harvest_block
    # WO-128.2: re-arming keys off the HARVEST outcome specifically - an
    # anchor-tail failure must not re-run the multi-minute collection, so the
    # success stamp cannot depend on the combined process $CODE.
    assert 'if [ "$HARVEST_CODE" -eq 0 ] && [ "$COVERAGE_DEGRADED" -eq 0 ]; then' in function
    assert function.index("stamp_status training_harvest") < function.index(
        "touch_success_stamp training_harvest"
    )
    assert 'TRAINING_AGE="$(seconds_since_success_stamp training_harvest)"' in script


def test_scheduler_stamps_harvest_and_anchor_tail_independently() -> None:
    script = Path("scripts/run_vps_ops_scheduler.sh").read_text(encoding="utf-8")
    function = script.split("run_training_harvest() {", 1)[1].split(
        "run_maker_study_intraday() {", 1
    )[0]

    assert 'row.get("step") != "anchor_ledgers"' in function
    assert 'stamp_status training_harvest "$HARVEST_CODE"' in function
    assert 'stamp_status training_harvest_anchor_tail "$ANCHOR_TAIL_CODE"' in function
    assert 'if [ "$HARVEST_CODE" -eq 0 ] && [ "$COVERAGE_DEGRADED" -eq 0 ]; then' in function
    assert function.index("stamp_status training_harvest ") < function.index(
        "touch_success_stamp training_harvest"
    )
