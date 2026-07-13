from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _vps_compose() -> dict:
    return yaml.safe_load((ROOT / "docker-compose.vps-paper.yml").read_text(encoding="utf-8"))


def test_vps_paper_compose_is_lean_and_paper_only():
    compose = _vps_compose()
    services = compose["services"]

    assert set(services) == {
        "polymarket-paper-live",
        "polymarket-dashboard",
        "superbru-auto-pick-watchdog",
        # 2026-07-09: runs the ex-GitHub recurring jobs locally after the
        # Actions minutes quota was exhausted; paper-only like everything else.
        "vps-ops-scheduler",
    }

    paper_env = services["polymarket-paper-live"]["environment"]
    assert paper_env["POLYMARKET_EXECUTE_LIVE"] == "false"
    assert paper_env["POLYMARKET_LIVE_TRADING"] == "0"
    assert paper_env["PM_MODE"] == "scan"
    assert "--paper-source websocket" in services["polymarket-paper-live"]["command"]
    assert "--optimize-model" in services["polymarket-paper-live"]["command"]
    assert "--governance-refresh-seconds $${POLYMARKET_GOVERNANCE_REFRESH_SECONDS}" in services["polymarket-paper-live"]["command"]
    assert "--discovery-max-runtime-seconds $${POLYMARKET_DISCOVERY_MAX_RUNTIME_SECONDS}" in services["polymarket-paper-live"]["command"]
    assert "--prediction-max-runtime-seconds $${POLYMARKET_PREDICTION_MAX_RUNTIME_SECONDS}" in services["polymarket-paper-live"]["command"]
    assert "--governance-max-runtime-seconds $${POLYMARKET_GOVERNANCE_MAX_RUNTIME_SECONDS}" in services["polymarket-paper-live"]["command"]
    assert paper_env["POLYMARKET_GOVERNANCE_REFRESH_SECONDS"] == "${POLYMARKET_GOVERNANCE_REFRESH_SECONDS:-0}"
    assert paper_env["POLYMARKET_DISCOVERY_MAX_RUNTIME_SECONDS"] == "${POLYMARKET_DISCOVERY_MAX_RUNTIME_SECONDS:-900}"
    assert paper_env["POLYMARKET_PREDICTION_MAX_RUNTIME_SECONDS"] == "${POLYMARKET_PREDICTION_MAX_RUNTIME_SECONDS:-600}"
    assert paper_env["POLYMARKET_GOVERNANCE_MAX_RUNTIME_SECONDS"] == "${POLYMARKET_GOVERNANCE_MAX_RUNTIME_SECONDS:-600}"

    assert services["polymarket-paper-live"]["restart"] == "unless-stopped"
    assert services["polymarket-dashboard"]["restart"] == "unless-stopped"
    assert services["superbru-auto-pick-watchdog"]["restart"] == "unless-stopped"
    assert services["polymarket-paper-live"]["mem_limit"] == "${PM_PAPER_MEM_LIMIT:-4g}"
    assert services["polymarket-dashboard"]["mem_limit"] == "${PM_DASHBOARD_MEM_LIMIT:-256m}"
    assert services["superbru-auto-pick-watchdog"]["mem_limit"] == "${SUPERBRU_AUTO_PICK_MEM_LIMIT:-1g}"
    assert services["polymarket-paper-live"]["build"]["args"]["INSTALL_SCRAPER"] == "true"
    dashboard_command = services["polymarket-dashboard"]["command"]
    assert "render_polymarket_dashboard.py" in dashboard_command
    assert "if [ ! -f /app/outputs/polymarket_dashboard/index.html ]" not in dashboard_command
    superbru = services["superbru-auto-pick-watchdog"]
    assert "run_superbru_auto_pick_watchdog.sh" in superbru["command"]
    assert superbru["environment"]["SUPERBRU_AUTO_PICK_ENABLED"] == "${SUPERBRU_AUTO_PICK_ENABLED:-true}"
    assert "POLYMARKET_LIVE_TRADING" not in superbru["environment"]


def test_dashboard_renderer_prefers_mounted_src_on_vps():
    text = (ROOT / "scripts" / "render_polymarket_dashboard.py").read_text(encoding="utf-8")

    assert 'SRC = ROOT / "src"' in text
    assert "sys.path.insert(0, str(SRC))" in text
    assert text.index("sys.path.insert(0, str(SRC))") < text.index("from polymarket_predictive_engine.dashboard import render_dashboard")


def test_vps_env_example_keeps_live_credentials_empty():
    text = (ROOT / ".env.vps-paper.example").read_text(encoding="utf-8")

    assert "POLYMARKET_EXECUTE_LIVE=false" in text
    assert "POLYMARKET_LIVE_TRADING=0" in text
    assert "POLYMARKET_PRIVATE_KEY=" in text
    assert "CLOB_API_KEY=" in text
    assert "PM_PAPER_MEM_LIMIT=4g" in text
    assert "SUPERBRU_AUTO_PICK_MEM_LIMIT=1g" in text
    assert "POLYMARKET_GOVERNANCE_REFRESH_SECONDS=0" in text
    assert "PM_VPS_MIN_VCPUS=2" in text
    assert "PM_VPS_MIN_FREE_DISK=6g" in text
    assert "POLYMARKET_DISCOVERY_MAX_RUNTIME_SECONDS=900" in text
    assert "POLYMARKET_PREDICTION_MAX_RUNTIME_SECONDS=600" in text
    assert "POLYMARKET_GOVERNANCE_MAX_RUNTIME_SECONDS=600" in text
    assert "SUPERBRU_AUTO_PICK_ENABLED=true" in text
    assert "SUPERBRU_AUTO_PICK_WINDOW_MINUTES=5000" in text
    assert "SUPERBRU_AUTO_PICK_REVISION_WINDOW_MINUTES=260" in text
    assert "SUPERBRU_EMAIL=" in text
    assert "SUPERBRU_PASSWORD=" in text
    assert "SUPERBRU_POOL_URL=" in text


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
    preflight = text.index("preflight_vps_capacity.py")
    checkout = text.index("update_vps_checkout_preserving_runtime.py")
    compose_up = text.index('compose -f "$COMPOSE_FILE" up -d --build')
    assert preflight < checkout < compose_up
    assert "outputs/performance/deployed_git_rev" in text
    assert "git pull --ff-only" not in text


def test_vps_deploy_preflight_runs_before_compose_replacement():
    text = (ROOT / ".github" / "workflows" / "deploy-polymarket-vps-paper.yml").read_text(encoding="utf-8")

    preflight = text.index('python3 "$PREFLIGHT_DIR/preflight_vps_capacity.py"')
    quiesce = text.index('compose -f "$COMPOSE_FILE" stop --timeout 60')
    checkout = text.index('python3 "$PREFLIGHT_DIR/update_vps_checkout_preserving_runtime.py"')
    compose_up = text.index('$DOCKER compose -f "$COMPOSE_FILE" up -d --build')
    assert preflight < quiesce < checkout < compose_up
    assert "outputs/performance/deployed_git_rev" in text
    assert "REFUSE_DEPLOY_KEEP_EXISTING_STACK" in (ROOT / "scripts" / "preflight_vps_capacity.py").read_text(encoding="utf-8")
    assert "git pull --ff-only" not in text
    assert "runs-on: [self-hosted, Linux, ARM64, polymarket-ci]" in text
    assert "runs-on: ubuntu-latest" not in text
    assert "source unchanged after refusal; restoring previous paper stack" in text
    assert 'printf \'0\\n\' | $SUDO tee "$governance_stamp"' in text
    assert 'compose -f "$COMPOSE_FILE" up -d --no-build' in text


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
    assert "sharp_sports_funnel" in text
    assert "Repair: docker compose -f $COMPOSE_FILE exec -T polymarket-paper-live python scripts/render_polymarket_dashboard.py" in text


def test_vps_deploy_workflow_requires_current_dashboard_schema():
    text = (ROOT / ".github" / "workflows" / "deploy-polymarket-vps-paper.yml").read_text(encoding="utf-8")

    assert "scheduler-owned post-deploy model refresh" in text
    assert "governance_refresh_status.json" in text
    assert "price_action_model_summary.json" in text
    assert "did not publish a post-deploy price-action model" in text
    assert "exec -T polymarket-paper-live" not in text
    assert "deployment_health" in text
    assert "mispricing_alpha_bridge" in text
    assert "coverage_by_sport_market" in text
    assert "alpha_validated_anchor_rows" in text
    assert "sharp_sports_funnel" in text
    assert "SUPERBRU_PASSWORD" in text
    assert "VPS auto-pick watchdog" in text
    assert "json.dumps(updates" in text


def test_vps_deploy_workflow_writes_public_dashboard_url():
    text = (ROOT / ".github" / "workflows" / "deploy-polymarket-vps-paper.yml").read_text(encoding="utf-8")

    assert "PM_VPS_HOST='$PM_VPS_HOST'" in text
    assert "PM_DASHBOARD_PUBLIC_URL=" in text
    assert "public_url = f\"http://{host}:{port}/\"" in text


def test_vps_deploy_workflow_validates_private_key_secret():
    text = (ROOT / ".github" / "workflows" / "deploy-polymarket-vps-paper.yml").read_text(encoding="utf-8")

    assert 'raw_key.replace("\\\\n", "\\n")' in text
    assert "ssh-keygen -y -f" in text
    assert "not the .pub public key or a PuTTY .ppk file" in text


def test_vps_deploy_workflow_explains_unauthorized_public_key():
    text = (ROOT / ".github" / "workflows" / "deploy-polymarket-vps-paper.yml").read_text(encoding="utf-8")

    assert "Validate VPS SSH authorization" in text
    assert "VPS_SSH_OK" in text
    assert "/home/${PM_VPS_USER}/.ssh/authorized_keys" in text
    assert "The deploy private key is valid, but the VPS rejected it" in text


def test_vps_governance_refresh_workflow_survives_slow_refresh():
    """The 2026-07-07 19:57 UTC scheduled run was cancelled at a 20-minute job cap while the
    VPS was busy, silently doubling the model re-score gap to ~12h. The remote command must
    time out (exit 124, logs preserved) before the job cap cancels the whole run."""
    text = (ROOT / ".github" / "workflows" / "polymarket-vps-governance-refresh.yml").read_text(encoding="utf-8")

    assert "timeout-minutes: 30" in text
    assert "sudo timeout 1500 docker compose" in text
    assert "refresh-governance failed or exceeded 25 minutes" in text


def test_vps_ops_scheduler_replaces_github_side_jobs():
    """2026-07-09: the GitHub Actions minutes quota was exhausted and every
    scheduled workflow was refused at start. The VPS must be able to run the
    recurring jobs itself: governance refresh, CLV snapshot, and the
    locked-card refresh chain, with odds-quota preflights and per-job
    timeouts inside its own memory cgroup."""
    import yaml as _yaml

    compose = _yaml.safe_load((ROOT / "docker-compose.vps-paper.yml").read_text(encoding="utf-8"))
    service = compose["services"]["vps-ops-scheduler"]
    assert "run_vps_ops_scheduler.sh" in service["command"]
    assert service["mem_limit"] == "${VPS_OPS_MEM_LIMIT:-2g}"
    mounts = " ".join(service["volumes"])
    for needed in ("./outputs:/app/outputs", "./work:/app/work", "./data:/app/data", "./inputs:/app/inputs"):
        assert needed in mounts, f"ops scheduler missing mount {needed}"

    script = (ROOT / "scripts" / "run_vps_ops_scheduler.sh").read_text(encoding="utf-8")
    assert "refresh-governance" in script
    assert "superbru_clv_experiment.py snapshot" in script
    assert "run_daily_robust_pipeline.py" in script
    assert "x-requests-remaining" in script  # free-endpoint quota preflight
    assert 'timeout "$GOVERNANCE_TIMEOUT"' in script
    assert "status.json" in script
    assert "consecutive_skipped_cycles" in script
    assert "duration_seconds" in script
    assert "polymarket_predictive_engine.cli operating-state" in script
    # 2026-07-09: daily resolved-market harvest (Gamma backfill + CLOB price
    # histories) - free, key-less, outcome-labelled training corpus.
    assert "backfill-resolved-markets" in script
    assert "collect-price-history" in script
    assert "run_training_harvest" in script
    assert "collect-trade-prints" in script  # signed-flow substrate, 15-min cadence
    # 2026-07-09: the two registered research lanes ride existing jobs - the
    # WO-36 maker-carry study daily with the harvest, the WO-34 event-group
    # consistency scan at print cadence (persistence needs frequency).
    assert "maker-carry-study" in script
    assert "decision-policy" in script
    assert "maker_study_intraday" in script
    assert "OPS_MAKER_STUDY_INTRADAY_INTERVAL_SECONDS" in script
    assert "OPS_MAKER_STUDY_INTRADAY_OFFSET_MIN_SECONDS" in script
    assert "OPS_MAKER_STUDY_INTRADAY_OFFSET_MAX_SECONDS" in script
    assert "11-13h offset guard" in script
    assert "backfill-trade-prints" in script
    assert "scan-event-groups" in script
    assert "maker-live-test" in script  # WO-36 step 4 scoreboard, inert without a wallet


def test_vps_telemetry_push_script_is_single_commit_and_actions_free():
    # The telemetry bridge exists because the sandbox cannot reach the VPS and
    # GitHub Actions minutes are exhausted: it must never grow branch history,
    # never trigger workflows, and never ship the heavy collection corpora.
    script = Path("scripts/push_vps_telemetry.sh").read_text(encoding="utf-8")
    # parentless commit force-pushed => branch always holds exactly one commit
    assert "commit-tree" in script
    assert '"+$COMMIT:refs/heads/$BRANCH"' in script
    # belt-and-braces: the one automatic workflow is PR-only, never telemetry.
    assert "[skip ci]" in script
    # decision summaries in, collection corpora out
    for included in (
        "outputs/polymarket_model_governance",
        "outputs/maker_carry",
        "outputs/ops_scheduler",
    ):
        assert included in script
    for excluded in (
        "polymarket_training",
        "polymarket_websocket",
        "polymarket_trade_prints",
    ):
        assert excluded not in script.replace(
            "outputs/polymarket_training archive", ""
        ), f"heavy dir {excluded} must not be whitelisted"
    # official book snapshots live under maker_carry but stay on the VPS
    assert "official_books" in script
    assert "source_git_rev" in script
    assert "divergence_started_at_utc" in script
    assert "ls-remote origin refs/heads/main" in script
    assert "rev-parse HEAD" in script
    assert "outputs/performance/deployed_git_rev" in script


def test_vps_diagnostic_script_rides_telemetry_and_stays_capped():
    # The diagnostic must land where the telemetry whitelist ships it, stay
    # under the bridge's per-file cap, and never hard-fail on a missing tool.
    script = Path("scripts/vps_diagnostic.sh").read_text(encoding="utf-8")
    assert "outputs/ops_scheduler/vps_diagnostic.log" in script
    assert "280" in script  # trim guard under the 300KB telemetry cap
    for fail_soft in ("docker unavailable", "systemctl unavailable", "no status.json"):
        assert fail_soft in script
    # report covers host, docker, container, and app layers
    for section in ("HOST:", "DOCKER:", "CONTAINER", "APP:"):
        assert section in script
