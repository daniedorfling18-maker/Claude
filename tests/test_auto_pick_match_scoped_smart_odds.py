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
