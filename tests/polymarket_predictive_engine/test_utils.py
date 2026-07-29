from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from polymarket_predictive_engine.utils import (
    append_csv_rows,
    append_csv_rows_matching_existing_header,
    replace_with_retry,
)


def test_replace_with_retry_recovers_from_transient_oserror(tmp_path, monkeypatch):
    target = tmp_path / "target.json"
    temp = tmp_path / ".target.tmp"
    temp.write_text("ok", encoding="utf-8")
    original_replace = Path.replace
    calls = 0

    def flaky_replace(self: Path, other: Path):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise OSError(14, "Bad address")
        return original_replace(self, other)

    monkeypatch.setattr(Path, "replace", flaky_replace)

    replace_with_retry(temp, target, attempts=4, delay=0.0)

    assert calls == 3
    assert target.read_text(encoding="utf-8") == "ok"


def test_append_csv_rows_preserves_prefix_and_refuses_schema_migration(tmp_path: Path):
    path = tmp_path / "anchored.csv"
    path.write_bytes(b"stamp,value\r\nold,1\r\n")
    anchored_prefix = path.read_bytes()

    append_csv_rows(path, [{"stamp": "new", "value": 2}], fieldnames=["stamp", "value"])

    assert path.read_bytes().startswith(anchored_prefix)
    before_mismatch = path.read_bytes()
    with pytest.raises(ValueError, match="use a new versioned ledger path"):
        append_csv_rows(
            path,
            [{"stamp": "later", "wallet_role": "operator", "value": 3}],
            fieldnames=["stamp", "wallet_role", "value"],
        )
    assert path.read_bytes() == before_mismatch


def test_legacy_header_drops_nonempty_new_field_loudly_but_empty_silently(tmp_path: Path):
    # WO-128.3 as NARROWED in the register (2026-07-27, #372): the original
    # "refuse and raise" contradicted WO-119's registered tolerance, so the
    # append must still succeed under the legacy header - the loss becomes
    # loud (warning + reported dropped_fields) instead of an exception.
    path = tmp_path / "legacy.csv"
    path.write_bytes(b"stamp,value\r\nold,1\r\n")
    before = path.read_bytes()

    with pytest.warns(UserWarning, match=r"wallet_role.*versioned ledger path"):
        result = append_csv_rows_matching_existing_header(
            path,
            [{"stamp": "new", "value": 2, "wallet_role": "operator"}],
            fieldnames=["stamp", "value", "wallet_role"],
        )
    assert result.dropped_fields == ("wallet_role",)
    assert path.read_bytes().startswith(before), "append must never rewrite history"
    assert path.read_bytes().endswith(b"new,2\r\n"), "the row itself still lands"

    # A field empty in EVERY appended row is a no-op, not data loss: no
    # warning, nothing reported - warning here would train operators to
    # ignore the warning that matters.
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        result = append_csv_rows_matching_existing_header(
            path,
            [{"stamp": "newer", "value": 3, "wallet_role": ""}],
            fieldnames=["stamp", "value", "wallet_role"],
        )
    assert result.dropped_fields == ()
    assert path.read_bytes().endswith(b"newer,3\r\n")
