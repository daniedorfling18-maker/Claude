"""WO-120: evidence-producer CLI commands fail loud on worker status "failed".

Only anchor-ledgers (WO-115), refresh-governance, and training-harvest used to
convert an internal failure status into a nonzero exit; every other
scheduler-consumed producer exited 0 on status "failed", so the scheduler
stamped success while collection was dead. These tests pin the per-command
zero-exit allowlists (registered benign states stay 0; "failed" and unknown
statuses return 1), following the test_refresh_governance monkeypatch pattern.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from polymarket_predictive_engine import cli as engine_cli
from polymarket_predictive_engine.config import load_config


@pytest.fixture()
def cfg_path(tmp_path: Path, monkeypatch) -> Path:
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


CASES = [
    # (command, worker attribute, benign statuses -> 0, loud statuses -> 1)
    (
        "maker-carry-study",
        "run_maker_carry_study",
        ["ok", "no_candidates", "disabled"],
        ["failed", "surprise_future_status"],
    ),
    (
        "collect-trade-prints",
        "collect_trade_prints",
        ["ok", "partial", "skipped_all_completed", "disabled"],
        ["failed", "surprise_future_status"],
    ),
    (
        "collect-maker-replay-data",
        "collect_maker_replay_data",
        ["ok", "partial", "no_portfolio", "disabled"],
        ["failed", "surprise_future_status"],
    ),
    (
        "maker-fill-replay",
        "run_maker_fill_replay",
        ["ok", "no_portfolio", "no_replay_data", "insufficient_coverage", "disabled"],
        ["failed", "surprise_future_status"],
    ),
    (
        "snapshot-official-books",
        "snapshot_official_books",
        ["ok", "partial", "no_portfolio", "disabled"],
        ["failed", "surprise_future_status"],
    ),
]


@pytest.mark.parametrize("command,worker,benign,loud", CASES)
def test_wo120_producer_status_exit_codes(command, worker, benign, loud, cfg_path, monkeypatch):
    for status in benign:
        monkeypatch.setattr(engine_cli, worker, lambda _cfg, _s=status: {"status": _s})
        assert engine_cli.main([command, "--config", str(cfg_path)]) == 0, (command, status)
    for status in loud:
        monkeypatch.setattr(engine_cli, worker, lambda _cfg, _s=status: {"status": _s})
        assert engine_cli.main([command, "--config", str(cfg_path)]) == 1, (command, status)
