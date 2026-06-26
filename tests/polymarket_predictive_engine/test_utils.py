from __future__ import annotations

from pathlib import Path

from polymarket_predictive_engine.utils import replace_with_retry


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
