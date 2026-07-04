from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_goal_status_module():
    path = ROOT / "scripts" / "polymarket_goal_status.py"
    spec = importlib.util.spec_from_file_location("polymarket_goal_status", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_goal_status_prefers_explicit_public_dashboard_url(monkeypatch):
    module = _load_goal_status_module()

    monkeypatch.setenv("PM_DASHBOARD_PUBLIC_URL", "http://129.151.178.42:8765")

    assert module.dashboard_url_hint() == "http://129.151.178.42:8765/"


def test_goal_status_builds_public_url_from_vps_host(monkeypatch):
    module = _load_goal_status_module()

    monkeypatch.delenv("PM_DASHBOARD_PUBLIC_URL", raising=False)
    monkeypatch.setenv("PM_VPS_HOST", "129.151.178.42")
    monkeypatch.setenv("POLYMARKET_DASHBOARD_PORT", "8765")

    assert module.dashboard_url_hint() == "http://129.151.178.42:8765/"
