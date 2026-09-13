"""WO-169 test 9-10: the recomputation command and the recorded fixture's provenance.

`profit-verdict-reconcile` measures any final-history CSV with no config, no output
root and no network, so the correction can be reproduced from a committed snapshot.
The run of record is on the VPS against the governance ledger; this is diagnostic."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from polymarket_predictive_engine import cli
from polymarket_predictive_engine.credential_guard import _scan_csv
from polymarket_predictive_engine.profit_verdict import DEFAULT_SETTINGS, RECONCILE_DIFFERENCES

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "recorded" / "closing_line_final_history_2026-08-21.csv"
FIXTURE_SHA256 = "4b66d07f1050125dbe01d39220fafc1261929291090b9e565abba4d6b4b17b33"


def test_reconcile_cli_writes_versioned_artifacts_without_config(tmp_path, monkeypatch):
    import polymarket_predictive_engine.profit_verdict as pv

    assert "profit-verdict-reconcile" in cli.COMMANDS

    # No config may be loaded, and no path outside the input and the command's own
    # staging directory may be read: the command must not reach a governance tree.
    def _no_config(*args, **kwargs):  # pragma: no cover - the failure is the assertion
        raise AssertionError("profit-verdict-reconcile must not load a config")

    monkeypatch.setattr(cli, "load_config", _no_config)
    opened: list[str] = []
    real_read = pv.read_csv_rows
    monkeypatch.setattr(pv, "read_csv_rows", lambda path: (opened.append(str(path)), real_read(path))[1])

    out_dir = tmp_path / "wo169_reconciliation"
    workdir = tmp_path / "empty_cwd"
    workdir.mkdir()
    cwd = os.getcwd()
    os.chdir(workdir)
    try:
        code = cli.main([
            "profit-verdict-reconcile",
            "--config", "/nonexistent/config.yaml",
            "--final-history", str(FIXTURE),
            "--output-dir", str(out_dir),
        ])
    finally:
        os.chdir(cwd)
    assert code == 0

    payload = json.loads((out_dir / "reconciliation.json").read_text(encoding="utf-8"))
    assert payload["input"]["sha256"] == FIXTURE_SHA256
    assert payload["input"]["rows"] == 90
    assert payload["legacy"]["per_share_unit_mean"] == -0.013943
    assert payload["corrected"]["per_dollar_unit_mean"] == -0.086501
    assert payload["corrected"]["interval_90"] == [-0.281562, 0.114739]
    assert [entry["name"] for entry in payload["differences"]] == list(RECONCILE_DIFFERENCES)
    assert payload["paper_trading_invoked"] is False and payload["live_trading_invoked"] is False
    assert payload["evidence_class"] == "diagnostic; not verification of record"
    report = (out_dir / "report.md").read_text(encoding="utf-8")
    assert "-0.086501" in report and "not verification of record" in report

    settings_used = payload["settings_used"]
    for key in ("minimum_final_samples", "sign_test_alpha", "exit_cost_haircut_per_dollar", "adverse_selection_haircut_per_dollar", "taker_fee_rate"):
        assert settings_used[key] == DEFAULT_SETTINGS[key]
    assert settings_used["diagnostic_cohort_substrings"] == ["updown", "up_down", "up-down"]

    # Every path read is the staged copy of the input; nothing under an output root,
    # no example config, and with no --positions no positions file at all.
    assert opened, "the command must read the staged ledger"
    for path in opened:
        assert Path(path).name in ("closing_line_final_history.csv", "closing_line_value_positions.csv")
        assert "verdict-reconcile-" in path
    assert not any("closing_line_value_positions.csv" in path for path in opened)
    assert not any(str(tmp_path / "outputs") in path for path in opened)
    assert not any("polymarket_predictive_config" in path for path in opened)

    # A second run must refuse the existing directory and change nothing.
    before = {entry.name: entry.read_bytes() for entry in out_dir.iterdir()}
    assert cli.main([
        "profit-verdict-reconcile",
        "--config", "/nonexistent/config.yaml",
        "--final-history", str(FIXTURE),
        "--output-dir", str(out_dir),
    ]) == 2
    assert {entry.name: entry.read_bytes() for entry in out_dir.iterdir()} == before


def test_recorded_fixture_provenance_and_no_credentials():
    """The fixture is the provenance proof: its README entry names the source commit and
    snapshot, its sha256 matches, and the key-aware scanner finds nothing to redact."""
    readme = (REPO_ROOT / "tests" / "fixtures" / "recorded" / "README.md").read_text(encoding="utf-8")
    assert "closing_line_final_history_2026-08-21.csv" in readme
    assert "origin/vps-telemetry" in readme
    assert "fcebaa2" in readme
    assert "2026-08-21T02:00:09Z" in readme
    raw = FIXTURE.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    assert digest == FIXTURE_SHA256
    assert digest in readme

    # Byte identity is the provenance proof, and the source terminates its lines with
    # CRLF. The repository normalises `*.csv` to LF, so this one path is exempted in
    # `.gitattributes`; without the exemption a fresh checkout hashes differently and
    # the snapshot would no longer be the file the correction was computed on.
    assert raw.count(b"\r\n") == 91
    assert raw.count(b"\n") == raw.count(b"\r\n")
    attributes = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "tests/fixtures/recorded/closing_line_final_history_2026-08-21.csv -text" in attributes

    assert _scan_csv(FIXTURE, REPO_ROOT, tail_rows=None) == []
