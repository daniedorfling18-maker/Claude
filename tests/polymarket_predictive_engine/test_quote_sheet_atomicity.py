from __future__ import annotations

from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any

from polymarket_predictive_engine import live_test_decision_policy, maker_carry_study, utils


def _quote_summary() -> dict[str, Any]:
    return {
        "generated_at_utc": "2026-07-15T12:00:00Z",
        "portfolio_net_carry_usd_per_day": 0.0,
        "portfolio_net_carry_usd_per_month": 0.0,
        "portfolio_capital_usd": 0.0,
        "capital_curve": [],
        "capital_for_100_per_month": None,
        "maker_gates": {
            "maker_verdict": "insufficient_evidence",
            "M_A_carry_evidence": {"state": "pending"},
            "M_B_adverse_realism": {"state": "pending"},
        },
        "portfolio": [],
    }


def _decision_policy() -> dict[str, Any]:
    return {
        "indicated_action": "observe_only",
        "ladder_stage_permitted": 0,
        "sizing": {"binding_capital_usd": 0.0},
        "kill_criteria_status": {
            "status": "clear",
            "kill_data_stale": False,
            "kill_input_freshness": {
                "state": "fresh",
                "age_seconds": 0.0,
                "maximum_age_seconds": 1800.0,
            },
        },
    }


def test_quote_sheet_writers_replace_complete_files_under_interleaving(tmp_path, monkeypatch):
    out_root = tmp_path / "maker_carry"
    out_root.mkdir()
    path = out_root / "maker_quote_sheet.md"
    original = "# Existing maker quote sheet\n\nExisting complete content.\n"
    path.write_text(original, encoding="utf-8")

    first_replace_ready = Event()
    release_first_replace = Event()
    call_lock = Lock()
    replacement_texts: list[str] = []
    writer_errors: list[BaseException] = []
    real_replace = utils.os.replace

    def interleaved_replace(source, destination):
        assert Path(destination) == path
        assert Path(source).parent == path.parent
        assert Path(source).name.startswith(f".{path.name}.")
        replacement = Path(source).read_text(encoding="utf-8")
        with call_lock:
            call_index = len(replacement_texts)
            replacement_texts.append(replacement)
        if call_index == 0:
            first_replace_ready.set()
            if not release_first_replace.wait(timeout=5):
                raise TimeoutError("first quote-sheet replacement was not released")
        real_replace(source, destination)

    monkeypatch.setattr(utils.os, "replace", interleaved_replace)

    def write_full_sheet() -> None:
        try:
            maker_carry_study._write_quote_sheet(
                out_root,
                _quote_summary(),
                {"min_daily_payout_usd": 1.0},
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            writer_errors.append(exc)

    full_writer = Thread(target=write_full_sheet, daemon=True)
    full_writer.start()
    assert first_replace_ready.wait(timeout=5)
    assert path.read_text(encoding="utf-8") == original

    live_test_decision_policy._patch_quote_sheet(out_root, _decision_policy())
    patched = path.read_text(encoding="utf-8")
    assert len(replacement_texts) == 2
    assert patched == replacement_texts[1]
    assert patched.startswith("# Existing maker quote sheet\n")
    assert "<!-- decision-policy:start -->" in patched
    assert patched.endswith("Existing complete content.\n")

    release_first_replace.set()
    full_writer.join(timeout=5)
    assert not full_writer.is_alive()
    assert writer_errors == []

    complete_sheet = path.read_text(encoding="utf-8")
    assert complete_sheet == replacement_texts[0]
    assert complete_sheet.startswith("# Maker quote sheet (WO-36)")
    assert complete_sheet.endswith("\n")
    assert list(out_root.glob(f".{path.name}.*.tmp")) == []
