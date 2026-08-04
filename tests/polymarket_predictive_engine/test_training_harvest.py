from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import yaml

from polymarket_predictive_engine.cli import COMMANDS
from polymarket_predictive_engine.training_harvest import (
    MAKER_STUDY_TRIGGER_ENV_VAR,
    MAKER_STUDY_TRIGGER_HARVEST_STEP_VALUE,
    _run_command,
    _steps,
    run_training_harvest,
)
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.utils import read_json

ROOT = Path(__file__).resolve().parents[2]


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

    def runner(command: Sequence[str], timeout_seconds: int, env: dict[str, str] | None = None) -> int:
        del env
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

    def runner(command: Sequence[str], timeout_seconds: int, env: dict[str, str] | None = None) -> int:
        del timeout_seconds, env
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
    assert 'if [ "$HARVEST_CODE" -eq 0 ]; then\n    touch_success_stamp training_harvest' in function
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
    assert 'if [ "$HARVEST_CODE" -eq 0 ]; then\n    touch_success_stamp training_harvest' in function
    assert function.index("stamp_status training_harvest ") < function.index(
        "touch_success_stamp training_harvest"
    )


# ---------------------------------------------------------------------------
# WO-151 §151.1 enumerated tests (2, 8, 9, and the intraday-job half of 1).
# Test 1's remaining half (invoked directly via the CLI -> "cli"), 3, 4, 5, 6,
# 7, 10, 11, and 12 live in test_maker_carry_study.py, which owns the artifact
# these trigger values and window-state fields are written to.
# ---------------------------------------------------------------------------


def test_embedded_maker_carry_study_step_carries_harvest_step_trigger_env(tmp_path: Path) -> None:
    """WO-151 §151.1 test (2): invoked as harvest step 10, it records
    "harvest_step". A null implementation (no env override at all, or the
    override attached to the wrong step/value) fails this immediately."""
    _, config_path = _config(tmp_path)
    steps = {step.name: step for step in _steps(str(config_path))}

    assert steps["maker_carry_study"].env == (
        (MAKER_STUDY_TRIGGER_ENV_VAR, MAKER_STUDY_TRIGGER_HARVEST_STEP_VALUE),
    )
    assert MAKER_STUDY_TRIGGER_ENV_VAR == "MAKER_STUDY_TRIGGER"
    assert MAKER_STUDY_TRIGGER_HARVEST_STEP_VALUE == "harvest_step"
    # Every OTHER step must be untouched - this is a per-step overlay, not a
    # global default forced onto the whole harvest.
    for name, step in steps.items():
        if name != "maker_carry_study":
            assert step.env == (), name


def test_run_training_harvest_passes_step_env_only_to_the_one_step(tmp_path: Path) -> None:
    """Discriminating behavioral counterpart to the structural test above: a
    "trivially wrong" implementation that leaks the env overlay to every step
    (or drops it going through the runner call) fails this test."""
    cfg, config_path = _config(tmp_path)
    captured: dict[str, dict[str, str] | None] = {}

    def runner(command: Sequence[str], timeout_seconds: int, env: dict[str, str] | None = None) -> int:
        del timeout_seconds
        captured[_step_name(command)] = env
        return 0

    run_training_harvest(
        cfg,
        config_path=str(config_path),
        runner=runner,
        clock=lambda: 0.0,
        timestamp=lambda: "2026-07-15T00:00:00Z",
    )

    assert captured["maker-carry-study"] == {
        MAKER_STUDY_TRIGGER_ENV_VAR: MAKER_STUDY_TRIGGER_HARVEST_STEP_VALUE
    }
    assert captured["backfill-resolved-markets"] is None
    assert captured["reward-epoch-sample"] is None
    assert captured["anchor-ledgers"] is None


def test_run_command_merges_env_overlay_over_ambient_environment(monkeypatch) -> None:
    """Exercises the REAL (non-test-double) default runner, _run_command, so a
    "wrong implementation" that forgets to pass env= to subprocess.run, or
    replaces the ambient environment instead of overlaying it, is caught -
    not just the fake runners the other tests substitute in."""
    monkeypatch.setenv("WO151_AMBIENT_PROBE", "ambient-value")
    probe = (
        "import os, sys; "
        "sys.exit(0 if os.environ.get('WO151_AMBIENT_PROBE') == 'ambient-value' "
        "and os.environ.get('WO151_OVERLAY_PROBE') == 'overlay-value' else 1)"
    )
    command = (sys.executable, "-c", probe)

    # No overlay: the ambient var is still visible (byte-identical to the
    # pre-WO-151 subprocess.run(list(command), check=False, timeout=...) call
    # with no env= kwarg at all).
    plain_probe = "import os, sys; sys.exit(0 if os.environ.get('WO151_AMBIENT_PROBE') == 'ambient-value' else 1)"
    assert _run_command((sys.executable, "-c", plain_probe), 5) == 0

    # With an overlay: BOTH the ambient var and the overlay var must be visible.
    assert _run_command(command, 5, env={"WO151_OVERLAY_PROBE": "overlay-value"}) == 0

    # A wrong overlay value must not silently pass.
    assert _run_command(command, 5, env={"WO151_OVERLAY_PROBE": "wrong-value"}) != 0


def test_scheduler_sets_intraday_job_trigger_env_for_dedicated_study_invocation() -> None:
    """WO-151 §151.1 test (1), dedicated-job half: a study run invoked by the
    dedicated job records study_trigger: "intraday_job". A null
    implementation (no env var set at all) fails this."""
    script = Path("scripts/run_vps_ops_scheduler.sh").read_text(encoding="utf-8")
    function = script.split("run_maker_study_intraday() {", 1)[1].split(
        "\n}\n", 1
    )[0]

    assert "MAKER_STUDY_TRIGGER=intraday_job" in function
    assert (
        'MAKER_STUDY_TRIGGER=intraday_job timeout "$HARVEST_TIMEOUT" python -m '
        "polymarket_predictive_engine.cli maker-carry-study --config \"$CONFIG_PATH\""
        in function
    )
    # Only the dedicated study invocation is tagged - the sibling jobs riding
    # the same subshell (reward-epoch-sample, replay collection) are untouched.
    assert "MAKER_STUDY_TRIGGER" not in function.split("maker-carry-study", 1)[1]


def _run_scheduler_snippet(tmp_path: Path, snippet: str, extra_env: dict[str, str] | None = None):
    out_dir = tmp_path / "ops"
    out_dir.mkdir(parents=True, exist_ok=True)
    script = ROOT / "scripts" / "run_vps_ops_scheduler.sh"
    env = {
        **os.environ,
        "OPS_SCHEDULER_LIBRARY_ONLY": "1",
        "OPS_SCHEDULER_OUT_DIR": str(out_dir),
        **(extra_env or {}),
    }
    return out_dir, subprocess.run(
        ["sh", "-c", f'. "$1"; {snippet}', "sh", str(script)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _maker_study_intraday_guard_block(script_text: str) -> str:
    """Extract the REAL, current maker_study_intraday guard block verbatim -
    the exact text the scheduler's own while-loop executes - so behavioral
    tests run production code, not a hand-rolled reimplementation that could
    silently drift from it."""
    start_marker = (
        'if [ "$(seconds_since_stamp maker_study_intraday)" -ge "$MAKER_STUDY_INTRADAY_INTERVAL" ]; then'
    )
    end_marker = 'if [ "$(seconds_since_stamp trade_prints)"'
    after_start = script_text.split(start_marker, 1)[1]
    return start_marker + after_start.split(end_marker, 1)[0]


def test_maker_study_intraday_decoupled_from_broken_or_stale_harvest_stamp(tmp_path: Path) -> None:
    """WO-151 §151.1 test (8): with the harvest stamp absent, unparseable,
    non-finite, and 200000s old, the dedicated job still fires once its own
    interval has elapsed - the decoupling, and the test that proves
    starvation is closed. Runs the REAL, extracted guard block (not a text
    match, not a reimplementation), so a "wrong implementation" that re-adds
    any harvest-age precondition fails this test."""
    script_text = (ROOT / "scripts" / "run_vps_ops_scheduler.sh").read_text(encoding="utf-8")
    guard_block = _maker_study_intraday_guard_block(script_text)
    # The old PRECONDITION must be gone: TRAINING_AGE is no longer compared
    # against the offset window before deciding whether to fire.
    assert '[ "$TRAINING_AGE" -ge "$MAKER_STUDY_INTRADAY_OFFSET_MIN" ]' not in guard_block
    assert "run_maker_study_intraday" in guard_block

    scenarios = (
        "",  # absent: no last_success_training_harvest file at all
        'echo "not-a-number" > "$OPS_SCHEDULER_OUT_DIR/last_success_training_harvest"',  # unparseable
        'echo "nan" > "$OPS_SCHEDULER_OUT_DIR/last_success_training_harvest"',  # non-finite
        # 200000s (55.5h) old: past even the old window's upper bound.
        f'echo "{int(time.time()) - 200000}" > "$OPS_SCHEDULER_OUT_DIR/last_success_training_harvest"',
    )
    for index, harvest_stamp_setup in enumerate(scenarios):
        out_dir, result = _run_scheduler_snippet(
            tmp_path / f"scenario-{index}",
            f'{harvest_stamp_setup or ":"}; '
            "run_maker_study_intraday() { printf fired > \"$OPS_SCHEDULER_OUT_DIR/fired\"; }; "
            f"{guard_block}",
        )
        assert result.returncode == 0, result.stderr
        assert (out_dir / "fired").exists(), (harvest_stamp_setup, result.stderr)


def test_maker_study_intraday_own_stamp_missing_or_corrupt_is_treated_as_due(tmp_path: Path) -> None:
    """Documents the ACTUAL, unchanged behavior of the job's own due-cadence
    stamp (`seconds_since_stamp maker_study_intraday`), which this WO's own
    §151.1 text says is the SOLE surviving condition, reused as-is. Runs the
    REAL, extracted guard block, not a reimplementation.

    ESCALATION (see build report): §151.1 test (9) and its "A2 for every new
    comparison" paragraph literally require the OPPOSITE of what is asserted
    here - "a missing, empty, unparseable ... maker_study_intraday stamp
    leaves the job un-fired ... it never reads as due." That contradicts (a)
    seconds_since_stamp's own registered WO-120 convention (missing/corrupt
    stamp = immediately due, `scripts/run_vps_ops_scheduler.sh:208-222`),
    which §151.1's own prose relies on UNCHANGED for this exact condition,
    and (b) test (8) above, which requires the opposite fail-direction for
    the analogous harvest-stamp case. Implementing test (9) literally would
    also make a lost/corrupted stamp file permanently disable this job (it
    never re-touches its own stamp if it never fires), reintroducing exactly
    the stall class WO-120 was written to close. No bespoke override was
    added; this test guards the safe, existing, unchanged behavior instead."""
    script_text = (ROOT / "scripts" / "run_vps_ops_scheduler.sh").read_text(encoding="utf-8")
    guard_block = _maker_study_intraday_guard_block(script_text)

    scenarios = (
        "",  # absent
        'echo "not-a-number" > "$OPS_SCHEDULER_OUT_DIR/last_maker_study_intraday"',  # unparseable
    )
    for index, own_stamp_setup in enumerate(scenarios):
        out_dir, result = _run_scheduler_snippet(
            tmp_path / f"scenario-{index}",
            f'{own_stamp_setup or ":"}; '
            "run_maker_study_intraday() { printf fired > \"$OPS_SCHEDULER_OUT_DIR/fired\"; }; "
            f"{guard_block}",
        )
        assert result.returncode == 0, result.stderr
        assert (out_dir / "fired").exists(), (own_stamp_setup, result.stderr)
