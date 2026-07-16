from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import yaml

from polymarket_predictive_engine.cli import COMMANDS
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.training_harvest import run_training_harvest
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
    assert calls[-2:] == ["corpus-retention", "anchor-ledgers"]
    assert payload["mandatory_tail_completed"] is True
    by_step = {row["step"]: row for row in payload["steps"]}
    assert by_step["maker_fill_replay"]["exit_code"] == 1
    assert by_step["corpus_retention"]["status"] == "ok"
    assert by_step["anchor_ledgers"]["status"] == "ok"

    persisted = read_json(cfg.output_root / "ops_scheduler" / "training_harvest.json")
    assert persisted["paper_trading_invoked"] is False
    assert persisted["live_trading_invoked"] is False


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
    assert len(payload["skipped_deadline_steps"]) == 23
    assert payload["mandatory_tail_completed"] is True


def test_scheduler_uses_registered_resilient_harvest_entrypoint() -> None:
    script = Path("scripts/run_vps_ops_scheduler.sh").read_text(encoding="utf-8")

    assert "training-harvest" in COMMANDS
    assert "h3-smart-flow-evaluate" in COMMANDS
    assert "h2-dutch-evaluate" in COMMANDS
    assert "collect-historical-bid-ask" in COMMANDS
    assert "build-leakage-safe-training" in COMMANDS
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
    assert 'if [ "$CODE" -eq 0 ]; then\n    touch_success_stamp training_harvest' in function
    assert function.index("stamp_status training_harvest") < function.index(
        "touch_success_stamp training_harvest"
    )
    assert 'TRAINING_AGE="$(seconds_since_success_stamp training_harvest)"' in script
