from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "auto_pick_match_scoped.py"


def load_module():
    spec = importlib.util.spec_from_file_location("auto_pick_match_scoped_for_test", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_leaderboard_url_candidates_cover_superbru_pool_shapes() -> None:
    mod = load_module()

    candidates = mod.leaderboard_url_candidates_from_pool_url(
        "https://www.superbru.com/worldcup_predictor/pool_view.php?t=1296&p=13236623&view=matches"
    )

    assert candidates[0].endswith("/pool_view.php?t=1296&p=13236623&view=leaderboard")
    assert any("/pool.php?" in url and "p=13236623" in url and "tab=leaderboard" in url for url in candidates)
    assert candidates[-1].endswith("view=matches")
    assert len(candidates) == len(set(candidates))


def test_text_leaderboard_parser_handles_div_style_rows() -> None:
    mod = load_module()

    rows = mod._parse_leaderboard_text_rows(
        [
            {"selector": "body_line", "text": "1 15 Danie 29.00"},
            {"selector": "body_line", "text": "2 Alex Smith 27.50 pts"},
            {"selector": "body_line", "text": "Not a leaderboard row"},
        ]
    )

    assert rows == [
        {"rank": 1, "movement_or_yellow_caps": "15", "player": "Danie", "current_points": 29.0},
        {"rank": 2, "movement_or_yellow_caps": "", "player": "Alex Smith", "current_points": 27.5},
    ]
