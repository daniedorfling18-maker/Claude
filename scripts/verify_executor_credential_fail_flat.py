#!/usr/bin/env python3
"""WO-73 keyless credential rotation/revocation drill harness.

No real credential is accepted or loaded.  The pre-amendment default target is
an inert stub; a future executor command must satisfy this unchanged contract.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any


DUMMY_NAMES = ("WO73_DUMMY_API_KEY", "WO73_DUMMY_API_SECRET", "WO73_DUMMY_PASSPHRASE")
SENSITIVE_ENV_PARTS = ("PRIVATE", "SECRET", "PASSPHRASE", "PASSWORD", "API_KEY")


def _sanitized_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if not any(part in key.upper() for part in SENSITIVE_ENV_PARTS)
    }


def _last_json_line(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _run(command: list[str], scenario: str) -> dict[str, Any]:
    env = _sanitized_environment()
    for name in DUMMY_NAMES:
        env.pop(name, None)
    if scenario == "invalid":
        for index, name in enumerate(DUMMY_NAMES):
            env[name] = f"invalid-test-only-{index}"
    completed = subprocess.run(command, env=env, text=True, capture_output=True, check=False, timeout=30)
    payload = _last_json_line(completed.stdout)
    defects: list[str] = []
    if completed.returncode == 0:
        defects.append("candidate_returned_success_with_unusable_credentials")
    if str(payload.get("status") or "") != f"flat_{scenario}_credentials":
        defects.append("candidate_did_not_report_expected_flat_state")
    for field in ("open_orders", "position_count"):
        try:
            observed = int(payload[field])
        except (KeyError, TypeError, ValueError):
            observed = -1
        if observed != 0:
            defects.append(f"{field}_not_zero")
    try:
        exposure = float(payload.get("exposure_usd", -1))
    except (TypeError, ValueError):
        exposure = -1
    if exposure != 0:
        defects.append("exposure_usd_not_zero")
    if payload.get("attempted_order_actions") != []:
        defects.append("order_action_attempted")
    if payload.get("halted") is not True:
        defects.append("candidate_not_halted")
    return {
        "scenario": scenario,
        "status": "PASS" if not defects else "FAIL",
        "exit_code": completed.returncode,
        "reported_state": payload.get("status"),
        "open_orders": payload.get("open_orders"),
        "position_count": payload.get("position_count"),
        "exposure_usd": payload.get("exposure_usd"),
        "halted": payload.get("halted"),
        "defects": defects,
        "credential_values_recorded": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    default_stub = Path(__file__).with_name("executor_credential_fail_flat_stub.py")
    parser.add_argument(
        "--candidate-command",
        default=f"{shlex.quote(sys.executable)} {shlex.quote(str(default_stub))}",
        help="candidate argv string; no shell expansion is performed",
    )
    parser.add_argument("--output", default="outputs/execution/credential_rotation_drill.json")
    args = parser.parse_args()
    command = shlex.split(args.candidate_command)
    scenarios = [_run(command, "missing"), _run(command, "invalid")]
    status = "PASS" if all(row["status"] == "PASS" for row in scenarios) else "FAIL"
    payload = {
        "work_order": "WO-73",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": status,
        "candidate_mode": "pre_amendment_stub_or_supplied_command",
        "scenarios": scenarios,
        "real_credentials_loaded": False,
        "credential_values_recorded": False,
        "executor_implemented": False,
        "post_amendment_item_4_implemented": False,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "output": str(output)}, sort_keys=True))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
