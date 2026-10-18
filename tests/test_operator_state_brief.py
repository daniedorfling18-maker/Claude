"""The operator state brief runs hermetically and prints every section.

The brief is the first read of every operator tick, so a brief that crashes on
one missing source is a tick that never happens. This runs it with
`--no-network` against the real repository and asserts the shape, not the
values: values move daily, the sections do not.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BRIEF = REPO_ROOT / ".claude" / "skills" / "operator-loop" / "state_brief.py"

SECTIONS = (
    "REPOSITORY",
    "LANDING",
    "WORK ORDERS (register)",
    "EVIDENCE (last VPS snapshot, 2026-08-21)",
    "TELEMETRY MIRROR",
    "VENTURE THESIS",
    "ESCALATIONS (owner decisions pending)",
)


def test_state_brief_runs_offline_and_prints_every_section() -> None:
    result = subprocess.run(
        [sys.executable, str(BRIEF), "--no-network", "--repo", str(REPO_ROOT)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    for heading in SECTIONS:
        assert heading in result.stdout, heading
    # The sections that read committed documents must have parsed something.
    assert "by status" in result.stdout
    assert "H1 sharp-anchor maker carry" in result.stdout
    assert "outcome         B" in result.stdout
    # Network sections say so rather than failing.
    assert result.stdout.count("skipped (--no-network)") == 2


def test_state_brief_names_the_thesis_and_the_queue() -> None:
    text = BRIEF.read_text(encoding="utf-8")
    assert 'THESIS = "docs/VENTURE_THESIS.md"' in text
    assert 'ESCALATIONS = ".claude/skills/operator-loop/escalations.md"' in text
    skill = (REPO_ROOT / ".claude" / "skills" / "operator-loop" / "SKILL.md").read_text(encoding="utf-8")
    assert "Outcome B is in" in skill
    assert "One action per tick" in skill
    assert "not armed by this file" in skill
