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

