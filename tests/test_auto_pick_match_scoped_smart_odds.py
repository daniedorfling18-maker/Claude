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


def test_card_fallback_carries_current_pick_from_out_of_window_scan(tmp_path: Path) -> None:
    card = tmp_path / "superbru_final_card.csv"
    with card.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["commence_time", "home_team", "away_team", "locked_pick"])
        writer.writeheader()
        writer.writerow(
            {
                "commence_time": "2026-07-05T20:00:00Z",
                "home_team": "Brazil",
                "away_team": "Norway",
                "locked_pick": "2-0",
            }
        )

    mod = load_module()
    args = argparse.Namespace(
        pick_card_csv=str(card),
        window_minutes=5000,
        late_card_grace_minutes=0,
    )
    ref = datetime(2026, 7, 4, 23, 31, tzinfo=timezone.utc)
    scan_results = [
        {
            "game_id": "game91",
            "game": "Brazil v Norway",
            "home_team": "Brazil",
            "away_team": "Norway",
            "kickoff_utc": "2026-07-04T23:08:00+00:00",
            "kickoff_source": "scoped_text_date_time",
            "minutes_until": -23,
            "current_pick": "2-0",
            "locked": False,
            "inputs_found": True,
            "status": "not_in_window",
        }
    ]

    queued, added = mod.base.merge_pick_card_fallback_queue(args, ref, scan_results=scan_results, queued=[])

    assert len(queued) == 1
    assert added == queued
    assert queued[0]["status"] == "queued_from_pick_card_fallback"
    assert queued[0]["scan_status"] == "not_in_window"
    assert queued[0]["scan_game_id"] == "game91"
    assert queued[0]["current_pick"] == "2-0"
    assert mod.base.normalise_score_pick(queued[0]["current_pick"]) == "2-0"


def test_existing_future_pick_is_frozen_until_revision_window() -> None:
    mod = load_module()
    args = argparse.Namespace(dry_run=False, revision_window_minutes=260)

    assert mod.base.should_keep_existing_pick_until_revision_window(
        {"current_pick": "2-1", "minutes_until": 1250},
        args,
    )
    assert not mod.base.should_keep_existing_pick_until_revision_window(
        {"current_pick": "2-1", "minutes_until": 120},
        args,
    )
    assert not mod.base.should_keep_existing_pick_until_revision_window(
        {"current_pick": "", "minutes_until": 1250},
        args,
    )


def test_superbru_text_kickoff_uses_south_africa_timezone() -> None:
    mod = load_module()

    parsed = mod.base.parse_kickoff(
        "4 Jul 23:00",
        None,
        datetime(2026, 7, 4, 20, 0, tzinfo=timezone.utc),
        page_timezone="Africa/Johannesburg",
    )

    assert parsed == datetime(2026, 7, 4, 21, 0, tzinfo=timezone.utc)


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


def test_extract_match_js_scopes_reads_to_the_requested_game() -> None:
    """Document-wide first-match input reads returned a DIFFERENT game's values
    whenever several pickers shared the page (2026-07-08): current_pick/locked
    read from the wrong row can mark a game already_current and silently skip a
    needed submit. The extractor must scope to #soccer-picker<id> /
    [data-bru-game-id] before falling back to the whole document."""
    module = load_module()

    js = module.base.EXTRACT_MATCH_JS
    assert "soccer-picker" in js
    assert "data-bru-game-id" in js
    assert "replace(/^game/" in js


def test_submit_loop_retries_once_on_transient_login_bounce() -> None:
    """SuperBru bounced the third fresh headless login inside one run back to
    the login page (2026-07-08, Spain v Belgium), failing the whole run at
    2-of-3 verified. A single paused retry absorbs the rate-limit flake while
    a real credential problem still fails loudly on the retry."""
    import inspect

    module = load_module()

    source = inspect.getsource(module.base)
    retry_block = source.split('submit_result.get("status") == "login_failed"', 1)
    assert len(retry_block) == 2, "submit loop must check for login_failed"
    after = retry_block[1][:600]
    assert "asyncio.sleep" in after
    assert "await submit_pick" in after


def test_no_pick_branch_keeps_verified_existing_pick_when_odds_feed_is_dead() -> None:
    """OUT_OF_USAGE_CREDITS (2026-07-08) failed the run while every saved pick
    was verified correct on SuperBru. When no live odds are available the loop
    must READ the row (dry-run probe) and treat a fully saved pick as success
    instead of no_pick_available; a genuinely missing pick stays red."""
    import inspect

    module = load_module()

    source = inspect.getsource(module.base)
    assert "kept_existing_pick_no_live_odds" in source
    assert "current_row_values" in source
    # The success counter must include the kept-pick outcome.
    assert '{"already_current", "kept_existing_pick_no_live_odds"}' in source
