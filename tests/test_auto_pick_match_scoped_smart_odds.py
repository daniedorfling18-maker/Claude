from __future__ import annotations

import argparse
import csv
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
SCRIPT = SCRIPT_DIR / "auto_pick_match_scoped_smart_odds.py"


def load_module():
    if str(SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPT_DIR))
    spec = importlib.util.spec_from_file_location("auto_pick_match_scoped_smart_odds_for_test", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_live_wrapper_keeps_locked_card_due_match_queue_but_blanks_score(tmp_path: Path) -> None:
    card = tmp_path / "superbru_final_card.csv"
    with card.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["commence_time", "home_team", "away_team", "locked_pick"])
        writer.writeheader()
        writer.writerow(
            {
                "commence_time": "2026-07-04T17:00:00Z",
                "home_team": "Canada",
                "away_team": "Morocco",
                "locked_pick": "0-2",
            }
        )

    mod = load_module()
    mod.use_live_only_inputs()
    args = argparse.Namespace(
        pick_card_csv=str(card),
        window_minutes=40,
        late_card_grace_minutes=0,
    )
    ref = datetime(2026, 7, 4, 16, 32, tzinfo=timezone.utc)

    queued, added = mod.base.merge_pick_card_fallback_queue(args, ref, scan_results=[], queued=[])

    assert len(queued) == 1
    assert added == queued
    assert queued[0]["home_team"] == "Canada"
    assert queued[0]["away_team"] == "Morocco"
    assert queued[0]["status"] == "queued_from_pick_card_fallback"

    pick_lookup = mod.base.find_pick_from_card(queued[0], str(card))

    assert pick_lookup["status"] == "found"
    assert pick_lookup["pick"] == ""
    assert pick_lookup["card_row"]["locked_pick"] == "0-2"
    assert "live odds recompute is required" in pick_lookup["live_only_note"]


def test_card_fallback_reconciles_duplicate_live_match_with_bad_scan_time(tmp_path: Path) -> None:
    card = tmp_path / "superbru_final_card.csv"
    with card.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["commence_time", "home_team", "away_team", "locked_pick"])
        writer.writeheader()
        writer.writerow(
            {
                "commence_time": "2026-07-04T21:00:00Z",
                "home_team": "Paraguay",
                "away_team": "France",
                "locked_pick": "0-2",
            }
        )

    mod = load_module()
    args = argparse.Namespace(
        pick_card_csv=str(card),
        window_minutes=180,
        late_card_grace_minutes=0,
    )
    ref = datetime(2026, 7, 4, 19, 5, tzinfo=timezone.utc)
    live_queue = [
        {
            "game_id": "game90",
            "game": "Paraguay v France",
            "home_team": "Paraguay",
            "away_team": "France",
            "kickoff_utc": "2026-07-04T19:46:00+00:00",
            "kickoff_source": "scoped_text_date_time",
            "status": "queued",
        }
    ]

    queued, added = mod.base.merge_pick_card_fallback_queue(args, ref, scan_results=[], queued=live_queue)

    assert len(queued) == 1
    assert added == []
    assert queued[0]["game_id"] == "game90"
    assert queued[0]["scan_kickoff_utc"] == "2026-07-04T19:46:00+00:00"
    assert queued[0]["kickoff_utc"] == "2026-07-04T21:00:00+00:00"
    assert queued[0]["kickoff_source"] == "pick_card_fallback_reconciled_scan_time"
    assert queued[0]["pick_card_row"]["locked_pick"] == "0-2"


def test_already_current_counts_as_required_submission_success() -> None:
    mod = load_module()
    args = argparse.Namespace(require_submission=True, dry_run=False)

    result = {
        "queued_count": 1,
        "submitted": 0,
        "already_current_count": 1,
    }

    assert mod.base.normalise_score_pick(" 00 – 02 ") == "0-2"
    assert mod.base.exit_code_for_result(result, args) == 0


def test_required_submission_fails_when_not_submitted_or_current() -> None:
    mod = load_module()
    args = argparse.Namespace(require_submission=True, dry_run=False)

    result = {
        "queued_count": 1,
        "submitted": 0,
        "already_current_count": 0,
    }

    assert mod.base.exit_code_for_result(result, args) == 2
