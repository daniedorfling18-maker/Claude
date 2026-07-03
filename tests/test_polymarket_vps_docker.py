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
    assert "--governance-refresh-seconds $${POLYMARKET_GOVERNANCE_REFRESH_SECONDS}" in services["polymarket-paper-live"]["command"]
    assert paper_env["POLYMARKET_GOVERNANCE_REFRESH_SECONDS"] == "${POLYMARKET_GOVERNANCE_REFRESH_SECONDS:-120}"

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
    assert "POLYMARKET_GOVERNANCE_REFRESH_SECONDS=120" in text


def test_vps_bootstrap_script_starts_only_lean_paper_stack():
    text = (ROOT / "scripts" / "bootstrap_polymarket_vps_paper.sh").read_text(encoding="utf-8")

    assert "docker-compose.vps-paper.yml" in text
    assert "apt-get" in text
    assert "dnf" in text
    assert "yum" in text
    assert "https://download.docker.com/linux/rhel/docker-ce.repo" in text
    assert "docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin" in text
    assert "systemctl enable --now docker" in text
    assert "docker-compose.polymarket-wide-raw.yml" not in text
    assert "docker-compose.monitor.yml" not in text
    assert "POLYMARKET_LIVE_TRADING=1" not in text
    assert "POLYMARKET_EXECUTE_LIVE=true" not in text
    assert "PM_PAPER_MEM_LIMIT 2g" in text
    assert "POLYMARKET_WEBSOCKET_MAX_ASSETS 80" in text


def test_vps_health_script_checks_dashboard_and_heartbeat_files():
    text = (ROOT / "scripts" / "check_polymarket_vps_paper.sh").read_text(encoding="utf-8")

    assert "docker-compose.vps-paper.yml" in text
    assert "outputs/polymarket_model_governance/local_live_loop_heartbeat.json" in text
    assert "outputs/polymarket_model_governance/forward_paper_cycle.json" in text
    assert "outputs/polymarket_dashboard/dashboard_data.json" in text
    assert "curl -fsS --max-time 5" in text
    assert "mispricing_alpha_bridge" in text
    assert "coverage_by_sport_market" in text
    assert "alpha_validated_anchor_rows" in text


def test_vps_deploy_workflow_requires_current_dashboard_schema():
    text = (ROOT / ".github" / "workflows" / "deploy-polymarket-vps-paper.yml").read_text(encoding="utf-8")

    assert "deployment_health" in text
    assert "mispricing_alpha_bridge" in text
    assert "coverage_by_sport_market" in text
    assert "alpha_validated_anchor_rows" in text
