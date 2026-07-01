from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _vps_compose() -> dict:
    return yaml.safe_load((ROOT / "docker-compose.vps-paper.yml").read_text(encoding="utf-8"))


def test_vps_paper_compose_is_lean_and_paper_only():
    compose = _vps_compose()
    services = compose["services"]

    assert set(services) == {"polymarket-paper-live", "polymarket-dashboard"}

    paper_env = services["polymarket-paper-live"]["environment"]
    assert paper_env["POLYMARKET_EXECUTE_LIVE"] == "false"
    assert paper_env["POLYMARKET_LIVE_TRADING"] == "0"
    assert paper_env["PM_MODE"] == "scan"
    assert "--paper-source websocket" in services["polymarket-paper-live"]["command"]
    assert "--optimize-model" in services["polymarket-paper-live"]["command"]

    assert services["polymarket-paper-live"]["restart"] == "unless-stopped"
    assert services["polymarket-dashboard"]["restart"] == "unless-stopped"
    assert services["polymarket-paper-live"]["mem_limit"] == "${PM_PAPER_MEM_LIMIT:-4g}"
    assert services["polymarket-dashboard"]["mem_limit"] == "${PM_DASHBOARD_MEM_LIMIT:-256m}"


def test_vps_env_example_keeps_live_credentials_empty():
    text = (ROOT / ".env.vps-paper.example").read_text(encoding="utf-8")

    assert "POLYMARKET_EXECUTE_LIVE=false" in text
    assert "POLYMARKET_LIVE_TRADING=0" in text
    assert "POLYMARKET_PRIVATE_KEY=" in text
    assert "CLOB_API_KEY=" in text
    assert "PM_PAPER_MEM_LIMIT=4g" in text
