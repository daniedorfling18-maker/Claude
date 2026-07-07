"""Cgroup-aware resource guard tests for the VPS live paper loop.

Observed 2026-07-07: the paper-live container swap-thrashed at 78% of its own
4GiB Docker mem_limit while /proc/meminfo showed the HOST at only ~69%, so the
host-only guard (threshold 99) never fired and iterations crawled for hours —
freezing the time-critical sharp-anchor fetch lane. The guard must therefore
consider the container's cgroup limit too, and report the worse of the two.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from polymarket_predictive_engine.config import EngineConfig

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "run_polymarket_live_paper_loop",
    ROOT / "scripts" / "run_polymarket_live_paper_loop.py",
)
assert SPEC and SPEC.loader
loop = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(loop)


def _write_cgroup_v2(base: Path, *, current: int, limit: str, inactive_file: int | None = None) -> None:
    base.mkdir(parents=True, exist_ok=True)
    (base / "memory.max").write_text(f"{limit}\n", encoding="utf-8")
    (base / "memory.current").write_text(f"{current}\n", encoding="utf-8")
    if inactive_file is not None:
        (base / "memory.stat").write_text(f"anon 1\ninactive_file {inactive_file}\n", encoding="utf-8")


def test_cgroup_v2_percent_subtracts_inactive_file_cache(tmp_path: Path):
    # 3.2 GiB current, 0.2 GiB reclaimable cache, 4 GiB limit -> 75%, like docker stats.
    gib = 1024**3
    _write_cgroup_v2(tmp_path, current=int(3.2 * gib), limit=str(4 * gib), inactive_file=int(0.2 * gib))
    percent = loop._cgroup_memory_percent(base=tmp_path)
    assert percent is not None
    assert abs(percent - 75.0) < 0.01


def test_cgroup_unlimited_returns_none(tmp_path: Path):
    _write_cgroup_v2(tmp_path, current=123456, limit="max")
    assert loop._cgroup_memory_percent(base=tmp_path) is None


def test_cgroup_v1_sentinel_limit_returns_none(tmp_path: Path):
    v1 = tmp_path / "memory"
    v1.mkdir(parents=True)
    (v1 / "memory.limit_in_bytes").write_text(str(1 << 62), encoding="utf-8")
    (v1 / "memory.usage_in_bytes").write_text("123456", encoding="utf-8")
    assert loop._cgroup_memory_percent(base=tmp_path) is None


def test_cgroup_v1_fallback_reads_usage_and_limit(tmp_path: Path):
    gib = 1024**3
    v1 = tmp_path / "memory"
    v1.mkdir(parents=True)
    (v1 / "memory.limit_in_bytes").write_text(str(4 * gib), encoding="utf-8")
    (v1 / "memory.usage_in_bytes").write_text(str(2 * gib), encoding="utf-8")
    (v1 / "memory.stat").write_text(f"total_inactive_file {gib}\n", encoding="utf-8")
    percent = loop._cgroup_memory_percent(base=tmp_path)
    assert percent is not None
    assert abs(percent - 25.0) < 0.01  # (2 - 1) / 4


def test_missing_cgroup_files_return_none(tmp_path: Path):
    assert loop._cgroup_memory_percent(base=tmp_path / "nope") is None


def test_resource_guard_uses_worse_of_host_and_container(tmp_path: Path, monkeypatch):
    cfg = EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "trading": {"mode": "paper"},
            "runtime_resource_guard": {"enabled": True, "max_memory_percent": 90},
        },
        path=tmp_path / "cfg.yaml",
    )
    # Host looks fine (69%) but the container is at 94% of its own cap: must skip.
    monkeypatch.setattr(loop, "_host_memory_percent", lambda: 69.0)
    monkeypatch.setattr(loop, "_cgroup_memory_percent", lambda base=None: 94.0)
    guard = loop._resource_guard(cfg)
    assert guard["skip_cycle"] is True
    assert guard["memory_percent"] == 94.0
    assert guard["host_memory_percent"] == 69.0
    assert guard["container_memory_percent"] == 94.0
    assert guard["reason"] == "memory_above_limit"

    # Both healthy: no skip, and the components stay reported for diagnosability.
    monkeypatch.setattr(loop, "_cgroup_memory_percent", lambda base=None: 40.0)
    guard = loop._resource_guard(cfg)
    assert guard["skip_cycle"] is False
    assert guard["memory_percent"] == 69.0

    # No cgroup (bare metal / Windows): host-only behaviour is preserved.
    monkeypatch.setattr(loop, "_cgroup_memory_percent", lambda base=None: None)
    guard = loop._resource_guard(cfg)
    assert guard["skip_cycle"] is False
    assert guard["memory_percent"] == 69.0
    assert guard["container_memory_percent"] is None


def test_example_config_schedules_sharp_fetch_every_full_scan():
    """The every-12th-iteration gate stretched to DAYS under VPS load and froze the
    sharp-fetch lane; rate limiting belongs to fetch_interval_minutes (>=180, quota
    guard) while the loop attempts cheaply every full scan. Do not raise these back
    without re-reading the 2026-07-07 freeze post-mortem in the config comments.
    """
    import yaml

    raw = yaml.safe_load((ROOT / "polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    schedule = raw["runtime_schedule"]
    assert int(schedule["sharp_odds_fetch_every_iterations"]) <= 2
    assert int(schedule["sharp_anchor_build_every_iterations"]) <= 2
    guard = raw["runtime_resource_guard"]
    assert float(guard["max_memory_percent"]) <= 92
