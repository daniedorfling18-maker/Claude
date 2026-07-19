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


def test_goal_status_prefers_explicit_private_dashboard_url(monkeypatch):
    module = _load_goal_status_module()

    monkeypatch.setenv(
        "PM_DASHBOARD_PUBLIC_URL",
        "https://polymarket-trader.example-tailnet.ts.net",
    )

    assert (
        module.dashboard_url_hint()
        == "https://polymarket-trader.example-tailnet.ts.net/"
    )


def test_goal_status_never_builds_public_url_from_vps_host(monkeypatch):
    module = _load_goal_status_module()

    monkeypatch.delenv("PM_DASHBOARD_PUBLIC_URL", raising=False)
    monkeypatch.setenv("PM_VPS_HOST", "129.151.178.42")
    monkeypatch.setenv("POLYMARKET_DASHBOARD_PORT", "8765")

    assert module.dashboard_url_hint() == "http://127.0.0.1:8765/"


def test_goal_status_rejects_legacy_or_ambiguous_remote_urls(monkeypatch):
    module = _load_goal_status_module()
    monkeypatch.setenv("POLYMARKET_DASHBOARD_PORT", "8765")

    rejected = [
        "http://129.151.178.42:8765/",
        "https://dashboard.example.com/",
        "https://polymarket.example.ts.net.attacker.invalid/",
        "https://polymarket.example.ts.net:8443/",
        "https://polymarket.example.ts.net/path",
    ]
    for value in rejected:
        monkeypatch.setenv("PM_DASHBOARD_PUBLIC_URL", value)
        assert module.dashboard_url_hint() == "http://127.0.0.1:8765/"
