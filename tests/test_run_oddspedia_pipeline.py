from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_oddspedia_pipeline.py"


def load_module():
    spec = importlib.util.spec_from_file_location("run_oddspedia_pipeline_for_test", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_oddspedia_source_unavailable_mode_writes_status_and_exits_zero(tmp_path, monkeypatch) -> None:
    mod = load_module()
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_oddspedia_pipeline.py", "--allow-source-unavailable", "--locked-picks-csv", "locked.csv"],
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1))

    assert mod.main() == 0

    status_path = tmp_path / "outputs" / "oddspedia_url_discovery" / "oddspedia_pipeline_status.json"
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["status"] == "source_unavailable"
    assert payload["returncode"] == 1
    assert payload["paper_trading_invoked"] is False
    assert payload["live_trading_invoked"] is False


def test_oddspedia_source_failure_is_strict_without_fail_soft_flag(tmp_path, monkeypatch) -> None:
    mod = load_module()
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["run_oddspedia_pipeline.py"])
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1))

    with pytest.raises(subprocess.CalledProcessError):
        mod.main()
