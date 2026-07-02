from __future__ import annotations

from pathlib import Path

import yaml

from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.paper_cycle import run_paper_cycle
from polymarket_predictive_engine.runtime_lock import runtime_lock, runtime_lock_path
from polymarket_predictive_engine.utils import read_json, write_json


def _config(tmp_path: Path):
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def test_runtime_lock_blocks_second_prediction_lane(tmp_path):
    cfg = _config(tmp_path)
    with runtime_lock(cfg, "prediction_cycle") as first:
        assert first.acquired is True
        with runtime_lock(cfg, "prediction_cycle") as second:
            assert second.acquired is False
            assert second.existing_payload["pid"] == first.payload["pid"]

    assert not runtime_lock_path(cfg, "prediction_cycle").exists()


def test_runtime_lock_replaces_stale_prediction_lane(tmp_path):
    cfg = _config(tmp_path)
    path = runtime_lock_path(cfg, "prediction_cycle")
    write_json(path, {"name": "prediction_cycle", "pid": 999999, "acquired_at_utc": "2026-01-01T00:00:00Z"})

    with runtime_lock(cfg, "prediction_cycle", stale_after_seconds=1) as lock:
        assert lock.acquired is True
        assert lock.stale_lock_replaced is True
        assert lock.existing_payload["pid"] == 999999

    assert not path.exists()


def test_paper_cycle_fails_closed_when_prediction_lane_is_already_running(tmp_path):
    cfg = _config(tmp_path)

    with runtime_lock(cfg, "prediction_cycle") as lock:
        assert lock.acquired is True
        result = run_paper_cycle(cfg, source="websocket")

    assert result["status"] == "skipped_existing_prediction_cycle"
    assert result["paper_trading_invoked"] is False
    assert result["live_trading_invoked"] is False
    assert result["runtime_lock"]["existing_payload"]["pid"] == lock.payload["pid"]
    saved = read_json(cfg.governance_root / "forward_paper_cycle.json")
    assert saved["status"] == "skipped_existing_prediction_cycle"
