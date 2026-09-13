"""WO-172 tests 1-4: the mirror must say which of its files are extracts.

`push_vps_telemetry.sh` copies a large CSV as its header plus its last 200 rows and skips an
oversized non-CSV entirely. Nothing in the mirror recorded that, so a figure quoted "across 200
closed positions" read as a population when it was a truncation depth."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import write_telemetry_export_manifest as writer  # noqa: E402


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _csv(rows: int, header: str = "a,b,c") -> str:
    return header + "\n" + "".join(f"{i},{i},{i}\n" for i in range(rows))


def _build(tmp_path: Path, **kwargs) -> dict:
    return writer.build_manifest(
        snapshot_dir=tmp_path / "snap",
        repo_root=tmp_path / "repo",
        as_of="2026-08-21T02:00:09Z",
        max_file_kb=kwargs.pop("max_file_kb", 300),
        csv_tail_lines=kwargs.pop("csv_tail_lines", 200),
        whitelist_dirs=kwargs.pop("whitelist_dirs", ["outputs/polymarket_shadow"]),
        **kwargs,
    )


def _entry(manifest: dict, path: str) -> dict:
    matches = [row for row in manifest["files"] if row["path"] == path]
    assert matches, f"{path} absent from {[row['path'] for row in manifest['files']]}"
    return matches[0]


def test_manifest_records_truncation_for_a_capped_csv(tmp_path: Path):
    """A 1,000-row ledger mirrored as header + last 200 must say so, with both counts."""
    source = _write(tmp_path / "repo" / "outputs" / "polymarket_shadow" / "shadow_positions.csv", _csv(1000))
    lines = source.read_text(encoding="utf-8").splitlines()
    _write(tmp_path / "snap" / "telemetry" / "outputs" / "polymarket_shadow" / "shadow_positions.csv",
           lines[0] + "\n" + "\n".join(lines[-200:]) + "\n")

    entry = _entry(_build(tmp_path), "outputs/polymarket_shadow/shadow_positions.csv")
    assert entry["source_lines_after_header"] == 1000
    assert entry["exported_lines_after_header"] == 200
    assert entry["truncated"] is True
    assert entry["truncation_rule"] == "header_plus_tail:200"
    assert entry["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    exported = tmp_path / "snap" / "telemetry" / "outputs" / "polymarket_shadow" / "shadow_positions.csv"
    assert entry["exported_sha256"] == hashlib.sha256(exported.read_bytes()).hexdigest()
    assert entry["source_sha256"] != entry["exported_sha256"]


def test_manifest_records_whole_files_and_skipped_oversized(tmp_path: Path):
    """A whole file, a skipped oversized JSON, a large CSV passed through whole, the renamed host
    manifest, and the manifest's exclusion of itself."""
    _write(tmp_path / "repo" / "outputs" / "polymarket_shadow" / "small.csv", _csv(10))
    _write(tmp_path / "snap" / "telemetry" / "outputs" / "polymarket_shadow" / "small.csv", _csv(10))
    # 400 KB JSON the copier skipped: present at the source, absent from the snapshot.
    _write(tmp_path / "repo" / "outputs" / "polymarket_shadow" / "big.json", json.dumps({"pad": "x" * 410_000}))
    # A CSV well OVER the 300 KB cap whose exported bytes nonetheless match its source. The rule
    # must be derived from the snapshot's own bytes, never from a size boundary: reading the size
    # would label this an extract and its 150 rows a truncation depth.
    big_csv = _csv(150, header=("h" * 400_000))
    assert len(big_csv.encode()) > 300 * 1024
    _write(tmp_path / "repo" / "outputs" / "polymarket_shadow" / "wide.csv", big_csv)
    _write(tmp_path / "snap" / "telemetry" / "outputs" / "polymarket_shadow" / "wide.csv", big_csv)
    _write(tmp_path / "repo" / "outputs" / "performance" / "vps_telemetry_manifest.json", json.dumps({"k": 1}))
    _write(tmp_path / "snap" / "telemetry" / "manifest.json", json.dumps({"k": 1}))
    # A manifest from a previous run sitting in the snapshot must not describe itself.
    _write(tmp_path / "snap" / "telemetry" / writer.MANIFEST_NAME, json.dumps({"stale": True}))

    manifest = _build(tmp_path, whitelist_dirs=["outputs/polymarket_shadow", "outputs/performance"])
    small = _entry(manifest, "outputs/polymarket_shadow/small.csv")
    assert small["truncated"] is False and small["truncation_rule"] == "whole"
    wide = _entry(manifest, "outputs/polymarket_shadow/wide.csv")
    assert wide["truncated"] is False and wide["truncation_rule"] == "whole"
    host = _entry(manifest, "manifest.json")
    assert host["source_path"] == "outputs/performance/vps_telemetry_manifest.json"
    assert writer.MANIFEST_NAME not in {row["path"] for row in manifest["files"]}
    assert manifest["file_count"] == len(manifest["files"])
    skipped = {row["source_path"]: row["reason"] for row in manifest["skipped"]}
    assert skipped["outputs/polymarket_shadow/big.json"] == "oversized_non_csv:400"
    assert manifest["filters"]["max_file_kb"] == 300
    assert manifest["filters"]["csv_tail_lines"] == 200
    assert manifest["filters"]["name_exclusions"] == list(writer.PAYLOAD_EXCLUDED_FRAGMENTS)


def test_schema_sha256_changes_only_when_the_header_changes(tmp_path: Path):
    """The schema hash tracks the header, not the data, and its key is exempt from the
    credential scanner — a 64-hex value under an unexempted key would block every push."""
    from polymarket_predictive_engine.credential_guard import _scan_json

    _write(tmp_path / "repo" / "outputs" / "polymarket_shadow" / "a.csv", _csv(3))
    _write(tmp_path / "snap" / "telemetry" / "outputs" / "polymarket_shadow" / "a.csv", _csv(3))
    _write(tmp_path / "repo" / "outputs" / "polymarket_shadow" / "b.csv", _csv(9))
    _write(tmp_path / "snap" / "telemetry" / "outputs" / "polymarket_shadow" / "b.csv", _csv(9))
    _write(tmp_path / "repo" / "outputs" / "polymarket_shadow" / "c.csv", _csv(3, header="a,b,renamed"))
    _write(tmp_path / "snap" / "telemetry" / "outputs" / "polymarket_shadow" / "c.csv", _csv(3, header="a,b,renamed"))

    manifest = _build(tmp_path)
    a = _entry(manifest, "outputs/polymarket_shadow/a.csv")["schema_sha256"]
    b = _entry(manifest, "outputs/polymarket_shadow/b.csv")["schema_sha256"]
    c = _entry(manifest, "outputs/polymarket_shadow/c.csv")["schema_sha256"]
    assert a == b != c

    target = tmp_path / "snap" / "telemetry" / writer.MANIFEST_NAME
    target.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert _scan_json(target, tmp_path) == []


def test_full_mode_refuses_a_truncated_entry(tmp_path: Path):
    """WO-172 item 2: a research export is whole by definition, and the manifest asserts it."""
    source = _write(tmp_path / "repo" / "outputs" / "polymarket_shadow" / "shadow_positions.csv", _csv(50))
    lines = source.read_text(encoding="utf-8").splitlines()
    _write(tmp_path / "snap" / "telemetry" / "outputs" / "polymarket_shadow" / "shadow_positions.csv",
           lines[0] + "\n" + "\n".join(lines[-5:]) + "\n")
    with pytest.raises(RuntimeError, match="requires whole files"):
        _build(tmp_path, mode=writer.MODE_FULL)


def test_push_script_calls_the_writer_with_arguments_and_fails_closed():
    """WO-172 test 4, static: the writer runs after the copy loop and before the staging add, it
    is given the values in effect rather than its own defaults, and a failure pushes nothing."""
    text = (REPO_ROOT / "scripts" / "push_vps_telemetry.sh").read_text(encoding="utf-8")
    call = text.index("write_telemetry_export_manifest.py")
    assert text.index("copy_capped \"${abs#") < call < text.index("add -f telemetry")
    for argument in ("--snapshot-dir", "--repo-root", "--as-of", "--max-file-kb", "--csv-tail-lines", "--whitelist-dir"):
        assert argument in text, argument
    assert "timeout 300 python3" in text
    assert 'PUSH_STATUS="manifest_failed"' in text
    assert text.index('PUSH_STATUS="manifest_failed"') < text.index("add -f telemetry")
    # The registered fail-closed shape is the status AND the non-zero exit, in that order, before
    # anything is staged.
    failure = text.index('PUSH_STATUS="manifest_failed"')
    assert text.index("exit 1", failure) < text.index("add -f telemetry")
    # The script must still not name the heavy corpora the WO-121 guard excludes.
    for fragment in ("polymarket_training", "websocket_capture", "trade_prints_capture"):
        assert fragment not in text


def test_export_script_verifies_copies_and_never_names_the_training_corpora():
    """WO-172 item 2, static: whole ledgers, verified, scanned, renamed into place only on success."""
    text = (REPO_ROOT / "scripts" / "export_research_ledgers.sh").read_text(encoding="utf-8")
    for ledger in (
        "outputs/polymarket_shadow/shadow_positions.csv",
        "outputs/polymarket_model_governance/closing_line_final_history.csv",
        "outputs/polymarket_model_governance/closing_line_value_positions.csv",
        "outputs/maker_carry/maker_carry_history.csv",
        "outputs/maker_carry/maker_live_test_history.csv",
        "outputs/polymarket_model_governance/edge_strategy_search.csv",
    ):
        assert ledger in text, ledger
    assert "resolution_corpus" not in text and "polymarket_training" not in text
    assert "--mode full" in text
    assert "--scan-credentials \"$TMP_DIR\"" in text
    # The rename happens after both checks, and failure deletes only this run's directory.
    assert text.index("--scan-credentials \"$TMP_DIR\"") < text.index('mv "$TMP_DIR"')
    assert 'rm -rf "$TMP_DIR"' in text
    # Build-review finding: clearing the trap BEFORE the rename left the temporary directory
    # behind when the rename itself failed, against "on any failure it deletes only that freshly
    # created temporary directory". The trap must be cleared only once the rename has succeeded.
    assert text.index('mv "$TMP_DIR"') < text.index("trap - EXIT")


def test_a_vanished_source_is_unproven_not_whole(tmp_path: Path):
    """WO-172 delta 1, from the build line audit: a source that disappeared between the copy and
    the manifest walk read `truncated = false`, so `--mode full`'s "truncated false on every entry,
    asserted" passed on an entry whose completeness nothing could demonstrate."""
    _write(tmp_path / "snap" / "telemetry" / "outputs" / "polymarket_shadow" / "gone.csv", _csv(10))
    manifest = _build(tmp_path)
    entry = _entry(manifest, "outputs/polymarket_shadow/gone.csv")
    assert entry["truncation_rule"] == "source_absent"
    assert entry["truncated"] is None
    with pytest.raises(RuntimeError, match="readable source"):
        _build(tmp_path, mode=writer.MODE_FULL)


def test_a_readable_file_absent_from_the_snapshot_is_not_called_size_unreadable(tmp_path: Path):
    """WO-172 delta 1: the skip reason said the size could not be read after a successful stat."""
    _write(tmp_path / "repo" / "outputs" / "polymarket_shadow" / "tiny.json", json.dumps({"k": 1}))
    _write(tmp_path / "snap" / "telemetry" / "outputs" / "polymarket_shadow" / "kept.csv", _csv(2))
    _write(tmp_path / "repo" / "outputs" / "polymarket_shadow" / "kept.csv", _csv(2))
    skipped = {row["source_path"]: row["reason"] for row in _build(tmp_path)["skipped"]}
    assert skipped["outputs/polymarket_shadow/tiny.json"].startswith("absent_from_snapshot:")
    assert "size_unreadable" not in skipped.values()
