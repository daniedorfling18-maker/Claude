from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_superbru_fixtures as cf  # noqa: E402


# ── _field_matches ─────────────────────────────────────────────────────────


def test_field_matches_wildcard():
    assert cf._field_matches("*", 15)
    assert cf._field_matches("*", 0)


def test_field_matches_exact():
    assert cf._field_matches("22", 22)
    assert not cf._field_matches("22", 23)


def test_field_matches_range():
    assert cf._field_matches("22-28", 25)
    assert cf._field_matches("22-28", 22)
    assert cf._field_matches("22-28", 28)
    assert not cf._field_matches("22-28", 29)


def test_field_matches_list():
    assert cf._field_matches("1,15,22", 15)
    assert not cf._field_matches("1,15,22", 10)


# ── suggest_cron ───────────────────────────────────────────────────────────


def test_suggest_cron_fires_25_min_before():
    kickoff = datetime(2026, 6, 25, 18, 0, tzinfo=timezone.utc)
    expr = cf.suggest_cron(kickoff)
    parts = expr.split()
    assert len(parts) == 5
    minute, hour = int(parts[0]), int(parts[1])
    fire = datetime(2026, 6, 25, hour, minute, tzinfo=timezone.utc)
    lead_min = (kickoff - fire).total_seconds() / 60.0
    assert lead_min == pytest.approx(cf.LEAD_MINUTES)


def test_suggest_cron_midnight_kickoff():
    kickoff = datetime(2026, 6, 28, 0, 0, tzinfo=timezone.utc)
    expr = cf.suggest_cron(kickoff)
    parts = expr.split()
    assert int(parts[2]) == 27  # fires on Jun 27 (day before)


# ── covered_by ─────────────────────────────────────────────────────────────


def _cron(minute: int, hour: int, dom: str, month: str) -> dict:
    expr = f"{minute:02d} {hour} {dom} {month} *"
    return {"expr": expr, "minute": str(minute), "hour": str(hour), "dom": dom, "month": month, "dow": "*"}


def test_covered_by_exact_cron():
    kickoff = datetime(2026, 6, 25, 18, 0, tzinfo=timezone.utc)
    cron = _cron(35, 17, "25", "6")  # fires at 17:35 on Jun 25 → 25 min before
    assert cf.covered_by([cron], kickoff) == cron["expr"]


def test_covered_by_range_cron():
    kickoff = datetime(2026, 7, 5, 20, 0, tzinfo=timezone.utc)
    cron = _cron(35, 19, "1-19", "7")  # fires at 19:35 Jul 1-19
    assert cf.covered_by([cron], kickoff) is not None


def test_covered_by_no_match():
    kickoff = datetime(2026, 6, 25, 18, 0, tzinfo=timezone.utc)
    wrong_cron = _cron(35, 17, "26", "6")  # wrong day
    assert cf.covered_by([wrong_cron], kickoff) is None


def test_covered_by_post_midnight_kickoff():
    # Kickoff at Jun 22 02:00 UTC. Cron fires at Jun 21 01:35 UTC.
    kickoff = datetime(2026, 6, 22, 2, 0, tzinfo=timezone.utc)
    cron = _cron(35, 1, "21-25", "6")  # fires Jun 21-25 at 01:35
    assert cf.covered_by([cron], kickoff) is not None


def test_covered_by_too_early():
    # Cron fires more than COVER_MAX_LEAD minutes before kickoff
    kickoff = datetime(2026, 6, 25, 18, 0, tzinfo=timezone.utc)
    cron = _cron(0, 16, "25", "6")  # fires at 16:00 → 120 min before
    assert cf.covered_by([cron], kickoff) is None


def test_covered_by_after_kickoff():
    # Cron fires after kickoff (negative lead)
    kickoff = datetime(2026, 6, 25, 18, 0, tzinfo=timezone.utc)
    cron = _cron(30, 18, "25", "6")  # fires at 18:30 → 30 min AFTER
    assert cf.covered_by([cron], kickoff) is None


# ── parse_workflow_crons ───────────────────────────────────────────────────


def test_parse_workflow_crons_extracts_all():
    content = """
name: Test
on:
  schedule:
    - cron: '35 17 25 6 *'
    - cron: '35 20 25 6 *'
  workflow_dispatch:
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
"""
    with tempfile.NamedTemporaryFile(suffix=".yml", mode="w", encoding="utf-8", delete=False) as f:
        f.write(content)
        p = Path(f.name)
    try:
        crons = cf.parse_workflow_crons(p)
        assert len(crons) == 2
        assert crons[0]["hour"] == "17"
        assert crons[1]["hour"] == "20"
    finally:
        p.unlink()


def test_parse_workflow_crons_empty():
    content = "name: Test\non:\n  workflow_dispatch:\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n"
    with tempfile.NamedTemporaryFile(suffix=".yml", mode="w", encoding="utf-8", delete=False) as f:
        f.write(content)
        p = Path(f.name)
    try:
        crons = cf.parse_workflow_crons(p)
        assert crons == []
    finally:
        p.unlink()


# ── insert_crons deduplication ─────────────────────────────────────────────


def test_insert_crons_deduplicates():
    content = """name: T
on:
  schedule:
    - cron: '35 17 25 6 *'
  workflow_dispatch:
jobs:
  t:
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
"""
    with tempfile.TemporaryDirectory() as tmpdir:
        wf = Path(tmpdir) / "auto_pick.yml"
        wf.write_text(content, encoding="utf-8")
        # Try inserting duplicate + new
        applied = cf.insert_crons(wf, [("35 17 25 6 *", "# duplicate"), ("35 20 26 6 *", "# new")])
        assert "35 17 25 6 *" not in applied
        assert "35 20 26 6 *" in applied
        # File should contain the new entry but not the duplicate twice
        text = wf.read_text(encoding="utf-8")
        assert text.count("35 17 25 6 *") == 1
        assert "35 20 26 6 *" in text
