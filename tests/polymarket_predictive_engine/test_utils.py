from __future__ import annotations

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


def test_legacy_header_refuses_nonempty_new_field_but_allows_empty(tmp_path: Path):
    path = tmp_path / "legacy.csv"
    path.write_bytes(b"stamp,value\r\nold,1\r\n")

    with pytest.raises(ValueError, match=r"wallet_role.*new versioned ledger path"):
        append_csv_rows_matching_existing_header(
            path,
            [{"stamp": "new", "value": 2, "wallet_role": "operator"}],
            fieldnames=["stamp", "value", "wallet_role"],
        )

    append_csv_rows_matching_existing_header(
        path,
        [{"stamp": "new", "value": 2, "wallet_role": ""}],
        fieldnames=["stamp", "value", "wallet_role"],
    )
    assert path.read_bytes().endswith(b"new,2\r\n")
