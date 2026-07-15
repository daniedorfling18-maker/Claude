"""Mechanical guards for WO-92 boundary normalization and shared writes."""

from __future__ import annotations

import inspect
from pathlib import Path
import re

from polymarket_predictive_engine import (
    hourly_adverse_study,
    live_test_decision_policy,
    maker_carry_study,
    requote_alerts,
    wallet_reconciliation,
)


PACKAGE = Path("src/polymarket_predictive_engine")

TIMESTAMP_BOUNDARY_MODULES = (
    "maker_live_test.py",
    "owner_activity_attribution.py",
    "flow_toxicity.py",
    "trade_print_collector.py",
    "wallet_reconciliation.py",
)

# The quote sheet is reached by the daily harvest and by the recurring safety
# pulse. Every known patcher is included, including the manual hourly study,
# so a newly scheduled path cannot reintroduce a torn write silently.
SHARED_WRITE_AUDIT = (
    ("harvest", "maker_carry/maker_quote_sheet.md", maker_carry_study._write_quote_sheet),
    (
        "harvest+safety_pulse",
        "maker_carry/maker_quote_sheet.md",
        live_test_decision_policy._patch_quote_sheet,
    ),
    ("safety_pulse", "maker_carry/maker_quote_sheet.md", requote_alerts._patch_quote_sheet),
    ("harvest", "maker_carry/maker_quote_sheet.md", wallet_reconciliation._patch_quote_sheet),
    ("manual_secondary", "maker_carry/maker_quote_sheet.md", hourly_adverse_study._patch_quote_sheet),
    ("safety_pulse", "maker_carry/requote_alert_notification.md", requote_alerts._notification),
)


def test_external_timestamp_ingestion_has_one_normalization_boundary() -> None:
    ad_hoc = re.compile(
        r"safe_float\s*\(\s*(?:row|trade|item)\.get\(\s*['\"]timestamp['\"]\s*\)\s*\)"
    )
    for name in TIMESTAMP_BOUNDARY_MODULES:
        source = (PACKAGE / name).read_text(encoding="utf-8")
        assert "normalize_external_timestamp" in source, name
        assert ad_hoc.search(source) is None, name
        assert "10_000_000_000" not in source, name

    helper = (PACKAGE / "utils.py").read_text(encoding="utf-8")
    assert "def normalize_external_timestamp" in helper
    assert "10_000_000_000" in helper


def test_every_shared_output_writer_in_the_schedule_audit_is_atomic() -> None:
    schedules = set()
    artifacts = set()
    for schedule, artifact, writer in SHARED_WRITE_AUDIT:
        schedules.update(schedule.split("+"))
        artifacts.add(artifact)
        source = inspect.getsource(writer)
        assert "write_text_atomic(" in source, writer.__qualname__
        assert ".write_text(" not in source, writer.__qualname__
        assert "open(\"w\")" not in source, writer.__qualname__

    assert {"harvest", "safety_pulse"} <= schedules
    assert "maker_carry/maker_quote_sheet.md" in artifacts


def test_schedule_paths_match_the_shared_write_audit() -> None:
    harvest = (PACKAGE / "training_harvest.py").read_text(encoding="utf-8")
    scheduler = Path("scripts/run_vps_ops_scheduler.sh").read_text(encoding="utf-8")

    for command in ("maker-carry-study", "decision-policy", "reconcile-wallet"):
        assert command in harvest
    for command in ("decision-policy", "requote-alerts"):
        assert command in scheduler
