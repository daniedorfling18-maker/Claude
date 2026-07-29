"""WO-129 fail-closed tests for the VPS paper health shell library."""

from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "check_polymarket_vps_paper.sh"


def _health(*arguments: str) -> subprocess.CompletedProcess[str]:
    quoted = " ".join(arguments)
    return subprocess.run(
        ["sh", "-c", f'PM_HEALTH_LIBRARY_ONLY=1 . "{SCRIPT}"; health_evidence_within_ceiling {quoted}'],
        text=True,
        capture_output=True,
        check=False,
    )


def test_missing_heartbeat_uses_bounded_startup_fallback() -> None:
    assert _health("missing", "missing", "100", "100").returncode == 0
    assert _health("missing", "missing", "93601", "100").returncode != 0
    assert _health("missing", "missing", "100", "1801").returncode != 0


def test_observed_heartbeat_is_authoritative_and_fails_closed() -> None:
    assert _health("900", "ok", "missing", "5000").returncode == 0
    assert _health("901", "ok", "100", "100").returncode != 0
    assert _health("1", "error", "100", "100").returncode != 0
    assert _health("1", "running", "100", "100").returncode != 0
    assert _health("1", "unknown", "100", "100").returncode != 0
    assert _health("nope", "ok", "100", "100").returncode != 0
    assert _health("-1", "ok", "100", "100").returncode != 0


def test_heartbeat_healthy_allowlist_names_only_producer_reachable_statuses() -> None:
    producer = SCRIPT.with_name("run_polymarket_local_live_loop.py")
    tree = ast.parse(producer.read_text(encoding="utf-8"))
    named_payloads: dict[str, ast.Dict] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if isinstance(node.value, ast.Dict):
                for target in targets:
                    if isinstance(target, ast.Name):
                        named_payloads[target.id] = node.value

    statuses: set[str] = set()
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        if len(call.args) < 2 or "local_live_loop_heartbeat.json" not in ast.unparse(call.args[0]):
            continue
        payload = named_payloads.get(call.args[1].id) if isinstance(call.args[1], ast.Name) else call.args[1]
        if isinstance(payload, ast.Dict):
            for key, value in zip(payload.keys, payload.values):
                if isinstance(key, ast.Constant) and key.value == "status" and isinstance(value, ast.Constant):
                    statuses.add(str(value.value))

    assert statuses == {"ok", "error"}
    accepted_statuses = {
        status for status in statuses if _health("1", status, "missing", "5000").returncode == 0
    }
    assert accepted_statuses == {"ok"}


def test_library_only_seam_refuses_executed_mode() -> None:
    result = subprocess.run(
        ["sh", str(SCRIPT)],
        env={**os.environ, "PM_HEALTH_LIBRARY_ONLY": "1"},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "source-only" in result.stderr
