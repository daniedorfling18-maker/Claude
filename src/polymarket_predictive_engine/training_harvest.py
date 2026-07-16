"""WO-85 resilient orchestration for the daily training harvest.

The harvest is collection/reporting infrastructure only.  It runs each
registered child command independently, records every outcome, enforces a
tighten-only whole-job start deadline, and always runs the retention and
ledger-anchor tail even when an earlier child fails.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import EngineConfig
from .utils import now_utc, write_json


WORK_ORDER = "WO-85"
OUTPUT_FILE = "ops_scheduler/training_harvest.json"

# Registered 2026-07-15 under WO-85. Configuration may make the deadline
# shorter, never longer. A child already started is allowed to finish under
# its own bounded timeout; work beyond the deadline is skipped rather than
# killed mid-write. The mandatory safety tail remains exempt from skipping.
REGISTERED_MAX_DEADLINE_SECONDS = 6 * 60 * 60
DEFAULT_STEP_TIMEOUT_SECONDS = 30 * 60
DEFAULT_PRINT_STEP_TIMEOUT_SECONDS = 5 * 60


@dataclass(frozen=True)
class HarvestStep:
    name: str
    command: tuple[str, ...]
    timeout_kind: str = "harvest"
    mandatory_tail: bool = False


def _engine_command(name: str, config_path: str) -> tuple[str, ...]:
    return (
        sys.executable,
        "-m",
        "polymarket_predictive_engine.cli",
        name,
        "--config",
        config_path,
    )


def _steps(config_path: str) -> list[HarvestStep]:
    regular = [
        HarvestStep("backfill_resolved_markets", _engine_command("backfill-resolved-markets", config_path)),
        HarvestStep("collect_price_history", _engine_command("collect-price-history", config_path)),
        HarvestStep("reconstructed_clv_study", _engine_command("reconstructed-clv-study", config_path)),
        HarvestStep("drift_scan", _engine_command("drift-scan", config_path)),
        HarvestStep("collect_wallet_intel", _engine_command("collect-wallet-intel", config_path)),
        HarvestStep("maker_carry_study", _engine_command("maker-carry-study", config_path)),
        HarvestStep(
            "collect_maker_replay_data",
            _engine_command("collect-maker-replay-data", config_path),
            timeout_kind="prints",
        ),
        HarvestStep("backfill_trade_prints", _engine_command("backfill-trade-prints", config_path)),
        HarvestStep("h3_smart_flow_evaluate", _engine_command("h3-smart-flow-evaluate", config_path)),
        HarvestStep("maker_fill_replay", _engine_command("maker-fill-replay", config_path)),
        HarvestStep("flow_toxicity", _engine_command("flow-toxicity", config_path)),
        HarvestStep("decision_policy", _engine_command("decision-policy", config_path)),
        HarvestStep("reconcile_wallet", _engine_command("reconcile-wallet", config_path)),
        HarvestStep("sync_cost_ledger", _engine_command("sync-cost-ledger", config_path)),
        HarvestStep("stage_day", _engine_command("stage-day", config_path)),
        HarvestStep("calibration_bias_study", _engine_command("calibration-bias-study", config_path)),
        HarvestStep("performance_factsheet", _engine_command("performance-factsheet", config_path)),
        HarvestStep("render_ips", _engine_command("render-ips", config_path)),
        HarvestStep(
            "governed_paper_audit",
            (sys.executable, "scripts/audit_polymarket_local_history.py", config_path),
        ),
        HarvestStep("operating_state", _engine_command("operating-state", config_path)),
    ]
    # These are deliberately the final two steps. They are attempted even
    # after any earlier failure or deadline skip so disk and evidence-chain
    # safety cannot be tail-starved again.
    return regular + [
        HarvestStep(
            "corpus_retention",
            _engine_command("corpus-retention", config_path),
            mandatory_tail=True,
        ),
        HarvestStep(
            "anchor_ledgers",
            _engine_command("anchor-ledgers", config_path),
            mandatory_tail=True,
        ),
    ]


def _positive_int(value: str | None, default: int) -> int:
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _settings() -> dict[str, int]:
    configured_deadline = _positive_int(
        os.environ.get("OPS_TRAINING_HARVEST_DEADLINE_SECONDS"),
        REGISTERED_MAX_DEADLINE_SECONDS,
    )
    return {
        "deadline_seconds": min(configured_deadline, REGISTERED_MAX_DEADLINE_SECONDS),
        "harvest_timeout_seconds": _positive_int(
            os.environ.get("OPS_TRAINING_HARVEST_TIMEOUT_SECONDS"),
            DEFAULT_STEP_TIMEOUT_SECONDS,
        ),
        "prints_timeout_seconds": _positive_int(
            os.environ.get("OPS_TRADE_PRINTS_TIMEOUT_SECONDS"),
            DEFAULT_PRINT_STEP_TIMEOUT_SECONDS,
        ),
    }


def _run_command(command: Sequence[str], timeout_seconds: int) -> int:
    try:
        completed = subprocess.run(list(command), check=False, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        return 124
    return int(completed.returncode)


def _public_command(step: HarvestStep) -> str:
    # Commands contain only executable/module names and the public config
    # path. No environment values or secrets are persisted.
    return " ".join(step.command)


def run_training_harvest(
    cfg: EngineConfig,
    *,
    config_path: str,
    runner: Callable[[Sequence[str], int], int] | None = None,
    clock: Callable[[], float] | None = None,
    timestamp: Callable[[], str] | None = None,
) -> dict[str, Any]:
    """Run a legible, bounded harvest and persist progress after every step."""

    execute = runner or _run_command
    monotonic = clock or time.monotonic
    timestamp_fn = timestamp or now_utc
    settings = _settings()
    artifact_path = cfg.output_root / OUTPUT_FILE
    started_at = timestamp_fn()
    started_clock = monotonic()
    rows: list[dict[str, Any]] = []
    payload: dict[str, Any] = {
        "work_order": WORK_ORDER,
        "status": "running",
        "started_at_utc": started_at,
        "generated_at_utc": started_at,
        "completed_at_utc": None,
        "deadline_seconds": settings["deadline_seconds"],
        "deadline_policy": (
            "ordinary steps not started after the registered deadline; an in-flight step finishes under "
            "its bounded timeout; corpus_retention and anchor_ledgers are always attempted"
        ),
        "steps": rows,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    write_json(artifact_path, payload)

    for step in _steps(config_path):
        elapsed_before = max(0.0, monotonic() - started_clock)
        timeout_seconds = (
            settings["prints_timeout_seconds"]
            if step.timeout_kind == "prints"
            else settings["harvest_timeout_seconds"]
        )
        row: dict[str, Any] = {
            "step": step.name,
            "command": _public_command(step),
            "mandatory_tail": step.mandatory_tail,
            "timeout_seconds": timeout_seconds,
            "started_at_utc": None,
            "completed_at_utc": None,
            "duration_seconds": 0.0,
            "exit_code": None,
            "status": "pending",
        }
        rows.append(row)
        if elapsed_before >= settings["deadline_seconds"] and not step.mandatory_tail:
            row.update(
                {
                    "status": "skipped_deadline",
                    "completed_at_utc": timestamp_fn(),
                    "skip_reason": "whole-job start deadline exhausted before this step",
                }
            )
            payload["generated_at_utc"] = timestamp_fn()
            write_json(artifact_path, payload)
            continue

        row["started_at_utc"] = timestamp_fn()
        step_started = monotonic()
        try:
            exit_code = int(execute(step.command, timeout_seconds))
            error_type = None
        except Exception as exc:  # recorded fail-loud; mandatory tail must still run
            exit_code = 1
            error_type = type(exc).__name__
        row.update(
            {
                "completed_at_utc": timestamp_fn(),
                "duration_seconds": round(max(0.0, monotonic() - step_started), 3),
                "exit_code": exit_code,
                "status": "ok" if exit_code == 0 else ("timed_out" if exit_code == 124 else "failed"),
            }
        )
        if error_type:
            row["error_type"] = error_type
        payload["generated_at_utc"] = timestamp_fn()
        write_json(artifact_path, payload)

    failed = [row["step"] for row in rows if row["status"] in {"failed", "timed_out"}]
    skipped = [row["step"] for row in rows if row["status"] == "skipped_deadline"]
    mandatory_tail = [row for row in rows if row["mandatory_tail"]]
    if failed:
        status = "partial_failure"
    elif skipped:
        status = "deadline_exceeded"
    else:
        status = "ok"
    completed_at = timestamp_fn()
    payload.update(
        {
            "status": status,
            "generated_at_utc": completed_at,
            "completed_at_utc": completed_at,
            "duration_seconds": round(max(0.0, monotonic() - started_clock), 3),
            "failed_steps": failed,
            "skipped_deadline_steps": skipped,
            "successful_steps": sum(row["status"] == "ok" for row in rows),
            "mandatory_tail_completed": all(row["status"] == "ok" for row in mandatory_tail),
            "successful_completion": status == "ok",
        }
    )
    write_json(artifact_path, payload)
    return payload
