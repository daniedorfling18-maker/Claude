from __future__ import annotations

import csv
import sys
import tempfile
from pathlib import Path

import pytest

# The auto_pick_match_scoped script lives in scripts/ (not a package), so import via path.
_SCRIPT = Path(__file__).parents[1] / "scripts" / "auto_pick_match_scoped.py"

import importlib.util as _ilu

_spec = _ilu.spec_from_file_location("auto_pick_match_scoped", _SCRIPT)
_mod = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

leaderboard_url_from_pool_url = _mod.leaderboard_url_from_pool_url
_extract_pool_id = _mod._extract_pool_id
_page_is_target_pool = _mod._page_is_target_pool
_is_lb_row = _mod._is_lb_row
_parse_leaderboard = _mod._parse_leaderboard
compute_pool_standing = _mod.compute_pool_standing
norm_team = _mod.norm_team
normalized_score = _mod.normalized_score


# ── normalized_score (smart-odds quota guard helper) ───────────────────────


def test_normalized_score_basic():
    assert normalized_score("2-1") == "2-1"
    assert normalized_score("2 - 1") == "2-1"
    assert normalized_score("02-01") == "2-1"


def test_normalized_score_blank_and_unparseable():
    assert normalized_score("") == ""
    assert normalized_score("None-None") == ""
    assert normalized_score(None) == ""
    assert normalized_score("-") == ""


def test_normalized_score_matches_for_quota_skip():
    # Visible vs card comparison the runner uses to decide already_picked.
    assert normalized_score("1 - 0") == normalized_score("1-0")
    assert normalized_score("2-2") != normalized_score("2-1")


# ── leaderboard_url_from_pool_url ──────────────────────────────────────────


def test_lb_url_pool_view_replaces_view_param():
    url = "https://www.superbru.com/worldcup_predictor/pool_view.php?t=1296&p=13236623&view=matches"
    lb = leaderboard_url_from_pool_url(url)
    assert "view=leaderboard" in lb
    assert "p=13236623" in lb


def test_lb_url_pool_php_replaces_tab():
    url = "https://www.superbru.com/pool.php?p=99999&tab=matches"
    lb = leaderboard_url_from_pool_url(url)
    assert "tab=leaderboard" in lb
    assert "p=99999" in lb


def test_lb_url_preserves_pool_id():
    url = "https://www.superbru.com/worldcup_predictor/pool_view.php?t=1296&p=13236623&view=picks"
    lb = leaderboard_url_from_pool_url(url)
    assert "p=13236623" in lb


def test_lb_url_generic_fallback():
    url = "https://www.superbru.com/some_game/pool.php?p=42"
    lb = leaderboard_url_from_pool_url(url)
    assert "leaderboard" in lb
    assert "p=42" in lb


# ── _extract_pool_id ───────────────────────────────────────────────────────


def test_extract_pool_id_standard():
    assert _extract_pool_id("https://example.com?t=1&p=13236623") == "13236623"


def test_extract_pool_id_missing():
    assert _extract_pool_id("https://example.com?t=1") is None


def test_extract_pool_id_ampersand_prefix():
    assert _extract_pool_id("https://example.com?foo=1&p=9999&bar=2") == "9999"


# ── _page_is_target_pool ───────────────────────────────────────────────────


def test_page_is_target_pool_by_url():
    ctx = {"url": "https://superbru.com?p=13236623&view=leaderboard", "title": "", "h1s": [], "bodySnippet": ""}
    assert _page_is_target_pool(ctx, "13236623", [])


def test_page_is_target_pool_by_keyword():
    ctx = {"url": "https://superbru.com/some_pool", "title": "Moore Infinity FIFA WC 26", "h1s": [], "bodySnippet": ""}
    assert _page_is_target_pool(ctx, None, ["Moore Infinity"])


def test_page_is_target_pool_wrong_pool():
    ctx = {"url": "https://superbru.com?p=99999", "title": "Another Pool", "h1s": [], "bodySnippet": ""}
    assert not _page_is_target_pool(ctx, "13236623", ["Moore Infinity"])


def test_page_is_target_pool_keyword_in_body():
    ctx = {
        "url": "https://superbru.com?p=00000",
        "title": "",
        "h1s": [],
        "bodySnippet": "Welcome to FIFA WC 26 leaderboard",
    }
    assert _page_is_target_pool(ctx, None, ["FIFA WC 26"])


# ── _is_lb_row ─────────────────────────────────────────────────────────────


def test_is_lb_row_valid():
    assert _is_lb_row(["1", "Danie Dorfling", "142"])
    assert _is_lb_row(["5", "Player Name Here", "137.5"])


def test_is_lb_row_invalid_rank():
    assert not _is_lb_row(["Rank", "Player", "Pts"])


def test_is_lb_row_invalid_points():
    assert not _is_lb_row(["1", "Danie", "pts"])


def test_is_lb_row_too_short():
    assert not _is_lb_row(["1", "100"])


def test_is_lb_row_no_alpha_in_player():
    assert not _is_lb_row(["1", "12345", "100"])


# ── _parse_leaderboard ─────────────────────────────────────────────────────


def _table(matrix: list[list[str]]) -> dict:
    return {"matrix": matrix}


def test_parse_leaderboard_extracts_rows():
    tables = [_table([
        ["Rank", "Player", "Points"],
        ["1", "Alice", "142"],
        ["2", "Bob", "140"],
        ["3", "Charlie", "135"],
    ])]
    rows = _parse_leaderboard(tables)
    assert len(rows) == 3
    assert rows[0]["rank"] == 1
    assert rows[0]["player"] == "Alice"
    assert rows[0]["current_points"] == 142.0


def test_parse_leaderboard_picks_largest_table():
    small = _table([["1", "Alice", "142"], ["2", "Bob", "140"]])
    big = _table([
        ["1", "Alice", "142"], ["2", "Bob", "140"], ["3", "C", "135"], ["4", "D", "130"],
    ])
    rows = _parse_leaderboard([small, big])
    assert len(rows) == 4


def test_parse_leaderboard_empty():
    assert _parse_leaderboard([]) == []


# ── compute_pool_standing ──────────────────────────────────────────────────


_LB = [
    {"rank": 1, "player": "Danie", "current_points": 142.0},
    {"rank": 2, "player": "Alice", "current_points": 139.0},
    {"rank": 3, "player": "Bob", "current_points": 131.0},
]


def test_compute_pool_standing_leading():
    standing = compute_pool_standing(_LB, "Danie", chaser_range=8.0)
    assert standing["status"] == "leading"
    assert standing["rank"] == 1
    assert standing["leader_gap"] == pytest.approx(3.0)
    assert standing["chasers_in_range"] == 1  # Alice at 3 pts behind (≤8)


def test_compute_pool_standing_leading_comfortable():
    lb = [
        {"rank": 1, "player": "Danie", "current_points": 160.0},
        {"rank": 2, "player": "Alice", "current_points": 140.0},
    ]
    standing = compute_pool_standing(lb, "Danie", chaser_range=8.0)
    assert standing["status"] == "leading"
    assert standing["leader_gap"] == pytest.approx(20.0)
    assert standing["chasers_in_range"] == 0


def test_compute_pool_standing_chasing():
    standing = compute_pool_standing(_LB, "Alice", chaser_range=8.0)
    assert standing["status"] == "chasing"
    assert standing["rank"] == 2
    assert standing["gap_to_leader"] == pytest.approx(3.0)


def test_compute_pool_standing_player_not_found():
    standing = compute_pool_standing(_LB, "NoBody", chaser_range=8.0)
    assert standing["status"] == "player_not_found"


def test_compute_pool_standing_empty_leaderboard():
    assert compute_pool_standing([], "Danie")["status"] == "unavailable"


def test_compute_pool_standing_partial_name_match():
    lb = [{"rank": 1, "player": "Danie Dorfling", "current_points": 142.0}]
    standing = compute_pool_standing(lb, "Danie", chaser_range=5.0)
    assert standing["status"] == "leading"


# ── Oddspedia grid injection ───────────────────────────────────────────────


_inject_oddspedia_grid = _mod._inject_oddspedia_grid


def _dummy_match(home: str = "Netherlands", away: str = "Sweden") -> object:
    """Minimal MatchOdds-like object using a simple namespace."""
    import dataclasses

    from superbru_score_engine.ingest import MatchOdds

    return MatchOdds(
        match_id="test",
        commence_time="2026-06-20T17:00:00Z",
        home_team=home,
        away_team=away,
        markets={},
    )


def _write_grid_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _grid_row(home: str, away: str, score_key: str, prob: float) -> dict:
    h, a = score_key.split("-")
    return {
        "match_id": f"{home.lower()}-{away.lower()}",
        "commence_time": "2026-06-20T17:00:00Z",
        "home_team": home,
        "away_team": away,
        "winner_home_pct": "50.0",
        "winner_draw_pct": "25.0",
        "winner_away_pct": "25.0",
        "best_odds_value": "5",
        "best_odds_bookie": "bet365",
        "best_odds_scoreline": "1 : 0",
        "source_url_type": "url",
        "source_url": "https://oddspedia.com/test",
        "current_url": "https://oddspedia.com/test",
        "source_name": "nuxt_state_event",
        "score_key": score_key,
        "home_goals": h,
        "away_goals": a,
        "score_bucket": "exact",
        "probability_pct": str(prob),
    }


def test_inject_oddspedia_grid_injects_market():
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = Path(tmpdir) / "grid.csv"
        _write_grid_csv(csv_path, [
            _grid_row("Netherlands", "Sweden", "1-0", 12.5),
            _grid_row("Netherlands", "Sweden", "0-0", 5.25),
            _grid_row("Netherlands", "Sweden", "2-0", 8.0),
        ])
        match = _dummy_match("Netherlands", "Sweden")
        injected = _inject_oddspedia_grid(match, {}, csv_path)
        assert "correct_score" in injected.markets
        assert len(injected.markets["correct_score"]) == 1
        outcomes = injected.markets["correct_score"][0].outcomes
        assert len(outcomes) == 3
        labels = {o.name for o in outcomes}
        assert "1-0" in labels and "0-0" in labels


def test_inject_oddspedia_grid_no_match_returns_unchanged():
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = Path(tmpdir) / "grid.csv"
        _write_grid_csv(csv_path, [_grid_row("Brazil", "Germany", "1-0", 10.0)])
        match = _dummy_match("Netherlands", "Sweden")
        injected = _inject_oddspedia_grid(match, {}, csv_path)
        assert "correct_score" not in injected.markets


def test_inject_oddspedia_grid_missing_file_returns_unchanged():
    match = _dummy_match("Netherlands", "Sweden")
    injected = _inject_oddspedia_grid(match, {}, Path("/nonexistent/grid.csv"))
    assert injected is match


_derive_risk_tier = _mod._derive_risk_tier
_derive_confidence_tier = _mod._derive_confidence_tier


def test_derive_tiers_match_report_outputs_definitions():
    # The defensive overlay must see the same tier semantics as predictions.csv / the card.
    from superbru_score_engine.report.outputs import _confidence_tier, _risk_tier

    class _Rec:
        def __init__(self, p_outcome):
            self.p_outcome = p_outcome

    class _Cand:
        def __init__(self, ev):
            self.expected_points = ev

    class _Pred:
        def __init__(self, p_outcome, ev_gap):
            self.recommended = _Rec(p_outcome)
            self.top_candidates = (_Cand(1.0), _Cand(1.0 - ev_gap))

    for p_outcome in (0.40, 0.55, 0.60, 0.65, 0.80):
        # risk tier depends only on p_outcome
        assert _derive_risk_tier(p_outcome) == _risk_tier(p_outcome)
        for ev_gap in (0.0, 0.015, 0.025, 0.05):
            pred = _Pred(p_outcome, ev_gap)
            assert _derive_confidence_tier(p_outcome, ev_gap) == _confidence_tier(pred)


def test_inject_oddspedia_grid_canonical_alias_match():
    # Odds feed canonicalises "USA" -> "United States"; the grid CSV still says "USA".
    # The injection must still match via canonical_team_key, not bare alnum fold.
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = Path(tmpdir) / "grid.csv"
        _write_grid_csv(csv_path, [
            _grid_row("USA", "Australia", "2-0", 12.0),
            _grid_row("USA", "Australia", "1-0", 10.0),
        ])
        match = _dummy_match("United States", "Australia")  # canonicalised home name
        injected = _inject_oddspedia_grid(match, {}, csv_path)
        assert "correct_score" in injected.markets
        assert len(injected.markets["correct_score"][0].outcomes) == 2


def test_inject_oddspedia_grid_unicode_alias_match():
    # "Curaçao" (odds, with cedilla) vs "Curacao" (grid). canonical_team_key folds both.
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = Path(tmpdir) / "grid.csv"
        _write_grid_csv(csv_path, [_grid_row("Curacao", "Ivory Coast", "0-2", 9.0)])
        match = _dummy_match("Curaçao", "Ivory Coast")
        injected = _inject_oddspedia_grid(match, {}, csv_path)
        assert "correct_score" in injected.markets


def test_inject_oddspedia_grid_swapped_orientation():
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = Path(tmpdir) / "grid.csv"
        # CSV has home=Sweden, away=Netherlands (reversed)
        _write_grid_csv(csv_path, [
            _grid_row("Sweden", "Netherlands", "0-1", 12.5),
            _grid_row("Sweden", "Netherlands", "0-0", 5.25),
        ])
        match = _dummy_match("Netherlands", "Sweden")
        injected = _inject_oddspedia_grid(match, {}, csv_path)
        assert "correct_score" in injected.markets
        outcomes = injected.markets["correct_score"][0].outcomes
        # Swapped: "0-1" in CSV (Sweden 0, Netherlands 1) → label "1-0" (Netherlands home wins)
        labels = {o.name for o in outcomes}
        assert "1-0" in labels
