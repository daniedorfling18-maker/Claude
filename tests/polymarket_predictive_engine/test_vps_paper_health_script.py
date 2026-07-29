"""WO-129 fail-closed tests for the VPS paper health shell library."""

from __future__ import annotations

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
    assert _health("900", "running", "missing", "5000").returncode == 0
    assert _health("901", "running", "100", "100").returncode != 0
    assert _health("1", "error", "100", "100").returncode != 0
    assert _health("nope", "running", "100", "100").returncode != 0
    assert _health("-1", "running", "100", "100").returncode != 0


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
