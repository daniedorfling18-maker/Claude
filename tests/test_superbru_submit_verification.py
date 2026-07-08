import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "submit_superbru_pick_cdp.py"


def load_module():
    spec = importlib.util.spec_from_file_location("submit_superbru_pick_cdp_for_test", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def row_with_score(home: str, away: str) -> dict:
    return {
        "found": True,
        "inputs": [
            {
                "visible": True,
                "type": "text",
                "maxlength": "2",
                "className": "editable-dropdown soccer-left-score",
                "value": home,
            },
            {
                "visible": True,
                "type": "text",
                "maxlength": "2",
                "className": "editable-dropdown soccer-right-score",
                "value": away,
            },
        ],
    }


def test_saved_score_matches_expected_superbru_readback() -> None:
    mod = load_module()

    assert mod.saved_score_matches(row_with_score("2", "0"), "2", "0")


def test_saved_score_rejects_stale_superbru_readback() -> None:
    mod = load_module()

    assert not mod.saved_score_matches(row_with_score("1", "2"), "0", "1")


def test_force_save_drives_superbru_process_pick_and_confirms_server_state() -> None:
    """SuperBru binds its saving change-handler directly (no delegation), so a
    programmatic value-set can fire "change" into a void and the pick silently
    never reaches ajax/save_pick.php (observed 2026-07-07/08, Spain v Belgium:
    filled 2-0 read back "-" seven runs in a row). The submitter must call
    brupicks.soccer.processPick itself and poll the bru-existing state that only
    a successful server save updates, instead of trusting the auto-save."""
    import inspect

    mod = load_module()

    js = mod.FORCE_SAVE_AND_CONFIRM_JS
    assert "processPick" in js
    assert "bru-existing" in js
    assert "soccer-picker" in js

    source = inspect.getsource(mod.run)
    assert "FORCE_SAVE_AND_CONFIRM_JS" in source
    assert "save_confirmation" in source


def test_selector_prefers_document_unique_game_scoped_selector() -> None:
    """With several pickers on one page, a bare class selector hits the FIRST
    matching input in the DOM — a different game. Observed 2026-07-08: the
    Spain-Belgium submit filled France-Morocco's inputs (game 97 vs 98) for
    8 straight runs, so Spain's pick never reached the server. The row finder
    now emits a game-scoped document-unique selector that must win."""
    mod = load_module()

    scoped = '[data-bru-game-id="98"] input.soccer-left-score'
    inp = {
        "visible": True,
        "type": "text",
        "className": "editable-dropdown soccer-left-score",
        "docSelector": scoped,
    }
    assert mod.selector_for_input(inp) == scoped

    # Legacy fallback stays for pages where no game id exists.
    assert mod.selector_for_input({"className": "soccer-left-score"}) == "input.soccer-left-score"

    # Both row finders (base and alias-aware override) must emit the scoped selector.
    assert "data-bru-game-id" in mod.FIND_ROW_JS
    assert "docSelector" in mod.FIND_ROW_JS
    aliases_text = (ROOT / "scripts" / "submit_superbru_pick_cdp_aliases.py").read_text(encoding="utf-8")
    assert "docSelector" in aliases_text
    assert 'data-bru-game-id=\\"' in aliases_text or 'data-bru-game-id="' in aliases_text

