from __future__ import annotations

import json
import os
import subprocess
import textwrap
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
        # Explicit-profile one-shot service; ordinary compose up omits it.
        "vps-deploy-acceptance",
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
    assert paper_env["POLYMARKET_GOVERNANCE_REFRESH_SECONDS"] == "0"
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
    assert services["polymarket-dashboard"]["ports"] == [
        "127.0.0.1:${POLYMARKET_DASHBOARD_PORT:-8765}:8765"
    ]
    superbru = services["superbru-auto-pick-watchdog"]
    assert "run_superbru_auto_pick_watchdog.sh" in superbru["command"]
    assert superbru["environment"]["SUPERBRU_AUTO_PICK_ENABLED"] == "${SUPERBRU_AUTO_PICK_ENABLED:-true}"
    assert "POLYMARKET_LIVE_TRADING" not in superbru["environment"]
    acceptance = services["vps-deploy-acceptance"]
    assert acceptance["profiles"] == ["deploy-acceptance"]
    assert acceptance["x-capacity-replaces"] == "vps-ops-scheduler"
    assert acceptance["restart"] == "no"
    assert acceptance["environment"]["POLYMARKET_EXECUTE_LIVE"] == "false"
    assert acceptance["environment"]["POLYMARKET_LIVE_TRADING"] == "0"
    assert "env_file" not in acceptance
    assert acceptance["command"] == "sh scripts/run_vps_deploy_acceptance.sh"


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
    assert "POLYMARKET_DASHBOARD_HOST=127.0.0.1" in text
    assert "PM_DASHBOARD_PUBLIC_URL=" in text


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
    assert "api.ipify.org" not in text
    assert "configure_polymarket_dashboard_tailscale.sh" in text
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

    builder_prune = text.index("$DOCKER builder prune --force")
    preflight = text.index('python3 "$PREFLIGHT_DIR/preflight_vps_capacity.py"')
    quiesce = text.index('compose -f "$COMPOSE_FILE" stop --timeout 60')
    checkout = text.index('python3 "$PREFLIGHT_DIR/update_vps_checkout_preserving_runtime.py"')
    compose_up = text.index('$DOCKER compose -f "$COMPOSE_FILE" up -d --build')
    assert builder_prune < preflight < quiesce < checkout < compose_up
    deployed_marker = text.index('mv "$marker_tmp" outputs/performance/deployed_git_rev')
    manifest_refresh = text.index("python3 scripts/write_vps_telemetry_manifest.py")
    governance_wait = text.index("waiting for scheduler-owned post-deploy governance refresh")
    assert compose_up < deployed_marker < manifest_refresh < governance_wait
    assert "docker system prune" not in text.lower()
    assert "outputs/performance/deployed_git_rev" in text
    assert "REFUSE_DEPLOY_KEEP_EXISTING_STACK" in (ROOT / "scripts" / "preflight_vps_capacity.py").read_text(encoding="utf-8")
    assert "git pull --ff-only" not in text
    assert "runs-on: [self-hosted, Linux, ARM64, polymarket-ci]" in text
    assert "runs-on: ubuntu-latest" not in text
    assert "source unchanged after refusal; restoring previous paper stack" in text
    assert 'printf \'0\\n\' | $SUDO tee "$governance_stamp"' in text
    assert 'compose -f "$COMPOSE_FILE" up -d --no-build' in text
    assert "rollback_vps_paper_deploy.py" in text
    assert "rollback-last-known-good" in text
    assert "trap 'deploy_exit $?' EXIT" in text
    assert "without invoking rollback" not in text
    assert 'if [ "$failed_checkout" = "$original_head" ]; then' not in text
    assert "restoring and revalidating the secure last-known-good stack" in text
    assert "--telemetry-writer" in text
    assert "ROLLED_BACK_TO_LAST_KNOWN_GOOD" in (
        ROOT / "scripts" / "rollback_vps_paper_deploy.py"
    ).read_text(encoding="utf-8")


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
    assert 'case "$REPO_DIR" in' in text
    assert '"~/"*) REPO_DIR="$HOME${REPO_DIR#?}" ;;' in text
    assert "eval " not in text
    assert "validate_dashboard_private_transport.py" in text
    assert "tailscale serve status" in text
    assert "tailscale funnel status" in text
    assert "--configured-url \"$private_dashboard_url\"" in text
    assert "private_https_reachable=false" in text
    assert 'curl -fsS --max-time 10 "$probe_url"' in text
    assert "printf '{}\\n' > \"$transport_tmp/tailscale-status.json\"" in text
    assert text.count("validate_dashboard_private_transport.py") == 1


def test_vps_deploy_workflow_requires_current_dashboard_schema():
    text = (ROOT / ".github" / "workflows" / "deploy-polymarket-vps-paper.yml").read_text(encoding="utf-8")

    assert "scheduler-owned post-deploy governance refresh" in text
    assert "outputs/ops_scheduler/status.json" in text
    assert 'job.get("last_exit_code")' in text
    assert "late-stage failure must make the deploy red" in text
    assert "governance_refresh_status.json" in text
    assert "price_action_model_summary.json" in text
    assert "did not publish a fresh price-action model" in text
    acceptance_script = (ROOT / "scripts" / "run_vps_deploy_acceptance.sh").read_text(
        encoding="utf-8"
    )
    assert "polymarket_predictive_engine.cli maker-live-test" in acceptance_script
    assert "maker_live_test_code" in acceptance_script
    assert '"fills_last_24h_raw"' in text
    assert '"maker_test_fills_last_24h"' in text
    assert "exec -T polymarket-paper-live" not in text
    assert "exec -T vps-ops-scheduler" not in text
    assert "--profile deploy-acceptance run" in text
    assert "deployment_health" in text
    assert "mispricing_alpha_bridge" in text
    assert "coverage_by_sport_market" in text
    assert "alpha_validated_anchor_rows" in text
    assert "sharp_sports_funnel" in text
    assert "Executor live-ops control plane" in text
    assert '"executor_status"' in text
    assert "SUPERBRU_PASSWORD" in text
    assert "VPS auto-pick watchdog" in text
    assert "json.dumps(updates" in text
    assert 'updates["POLYMARKET_GOVERNANCE_REFRESH_SECONDS"] = "0"' in text


def test_vps_deploy_runs_real_data_acceptance_after_restart_and_before_success():
    text = (ROOT / ".github" / "workflows" / "deploy-polymarket-vps-paper.yml").read_text(encoding="utf-8")

    baseline = text.index("deploy_acceptance_baseline.json")
    quiesce = text.index('compose -f "$COMPOSE_FILE" stop --timeout 60')
    compose_up = text.index('$DOCKER compose -f "$COMPOSE_FILE" up -d --build')
    producer_cycle = text.index("one-shot real-data acceptance")
    acceptance = text.index("--profile deploy-acceptance run")
    render = text.index("python scripts/render_polymarket_dashboard.py", acceptance)
    success = text.index("VPS deploy verified")

    assert baseline < quiesce < compose_up < producer_cycle < acceptance < render < success
    acceptance_script = (ROOT / "scripts" / "run_vps_deploy_acceptance.sh").read_text(
        encoding="utf-8"
    )
    assert "deploy_acceptance_cycle.json" in acceptance_script
    assert "maker-carry-study" in acceptance_script
    assert "collect-maker-replay-data" in acceptance_script
    assert "maker-fill-replay" in acceptance_script
    assert "requote-alerts" in acceptance_script
    assert "reconcile-wallet" in acceptance_script
    assert "executor-ops-monitor" in acceptance_script
    assert "operating-state" in acceptance_script
    assert 'TOTAL_TIMEOUT="${DEPLOY_ACCEPTANCE_TOTAL_TIMEOUT_SECONDS:-420}"' in acceptance_script
    assert "run_bounded" in acceptance_script
    assert "timeout --signal=TERM --kill-after=30s 600" in text
    assert 'acceptance_status" != "PASS"' in text
    assert "automatic rollback is armed for $original_head" in text
    assert 'install -d -m 0775 -o "$(id -u)" -g "$(id -g)"' in text
    assert "outputs/performance outputs/ops_scheduler" in text
    assert 'PM_VPS_REPO_DIR="$REPO_DIR" bash scripts/check_polymarket_vps_paper.sh' in text


def test_vps_deploy_acceptance_is_scheduler_isolated_and_stdin_closed():
    text = (ROOT / ".github" / "workflows" / "deploy-polymarket-vps-paper.yml").read_text(encoding="utf-8")

    scheduler_stop = text.index("stop --timeout 60 vps-ops-scheduler")
    scheduler_absent = text.index("Recurring scheduler remained active", scheduler_stop)
    acceptance_exec = text.index("--profile deploy-acceptance run", scheduler_absent)
    acceptance_stdin_closed = text.index("vps-deploy-acceptance </dev/null", acceptance_exec)
    scheduler_restart = text.index("up -d --no-build vps-ops-scheduler", acceptance_stdin_closed)
    dashboard_exec = text.index("exec -T polymarket-dashboard", acceptance_stdin_closed)
    dashboard_stdin_closed = text.index(
        "--config /app/polymarket_predictive_config.example.yaml </dev/null",
        dashboard_exec,
    )
    status_gate = text.index('acceptance_status="', dashboard_stdin_closed)
    success = text.index("VPS deploy verified", status_gate)

    assert (
        scheduler_stop
        < scheduler_absent
        < acceptance_exec
        < acceptance_stdin_closed
        < scheduler_restart
        < dashboard_exec
        < dashboard_stdin_closed
        < status_gate
        < success
    )
    assert "exec -T vps-ops-scheduler" not in text


def test_vps_deploy_requires_independent_main_attestation():
    text = (ROOT / ".github" / "workflows" / "deploy-polymarket-vps-paper.yml").read_text(encoding="utf-8")
    verifier = (ROOT / "scripts" / "verify_independent_main_acceptance.py").read_text(
        encoding="utf-8"
    )

    assert "acceptance_run_id" in text
    assert "independent-main-acceptance-${{ inputs.acceptance_run_id }}" in text
    assert "name: Check out accepted deployment verifier" in text
    assert "ref: main" in text
    assert "persist-credentials: false" in text
    assert "verify_independent_main_acceptance.py" in text
    assert 'EXPECTED_WORKFLOW = ".github/workflows/independent-pr-merge.yml"' in verifier
    assert 'run_payload.get("event") == "issue_comment"' in verifier
    assert 'run_payload.get("head_branch") == "main"' in verifier
    assert 'str(evidence.get("merge_workflow_run_id") or "") == run_id' in verifier
    assert '"verified_main_parent_sha"' in verifier
    assert 'evidence.get("merge_commit_sha")' in verifier
    assert "workflow actor and rerun initiator are not the repository owner" in verifier
    assert "no independent current-head reviewer is attested" in verifier
    assert "funding state is not CLOSED" in verifier
    assert 'accepted_main_sha="$(git rev-parse origin/main)"' in text


def test_vps_deploy_dashboard_probes_cannot_sigpipe_under_pipefail():
    text = (ROOT / ".github" / "workflows" / "deploy-polymarket-vps-paper.yml").read_text(encoding="utf-8")

    assert 'printf \'%s\' "$html" | grep -q' not in text
    assert 'printf \'%s\' "$data" | grep -q' not in text
    assert 'grep -Fq -- "Proof status" <<<"$html"' in text
    assert 'grep -Fq -- "profit_target_proof_status" <<<"$data"' in text
    assert "grep -Fq -- '\"executor_status\"' <<<\"$data\"" in text


def test_vps_deploy_remote_script_is_valid_bash():
    text = (ROOT / ".github" / "workflows" / "deploy-polymarket-vps-paper.yml").read_text(encoding="utf-8")
    lines = text.splitlines()
    start = next(index for index, line in enumerate(lines) if "<<'REMOTE'" in line) + 1
    end = next(index for index in range(start, len(lines)) if lines[index].strip() == "REMOTE")
    remote_script = textwrap.dedent("\n".join(lines[start:end])) + "\n"

    result = subprocess.run(
        ["bash", "-n"],
        input=remote_script,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_vps_deploy_workflow_enforces_private_dashboard_url():
    text = (ROOT / ".github" / "workflows" / "deploy-polymarket-vps-paper.yml").read_text(encoding="utf-8")

    assert 'updates["POLYMARKET_DASHBOARD_HOST"] = "127.0.0.1"' in text
    assert 'updates["PM_DASHBOARD_PUBLIC_URL"] = os.environ["DASHBOARD_PRIVATE_URL"]' in text
    assert 'dashboard_private_url="https://${dashboard_dns}/"' in text
    assert "configure_polymarket_dashboard_tailscale.sh" in text
    assert "public_url =" not in text
    assert "http://${PM_VPS_HOST}" not in text
    assert "tailnet-authenticated HTTPS" in text

    rollback_armed = text.index("ROLLBACK_ARMED=true")
    funnel_mutation = text.index("tailscale funnel --https=443 off")
    serve_mutation = text.index("tailscale serve --bg --yes --https=443")
    live_env_write = text.index('env_path.write_text("\\n".join(rendered) + "\\n"')
    assert rollback_armed < funnel_mutation < serve_mutation < live_env_write
    assert 'ENV_PATCH_PATH="$ENV_PATCH"' in text
    assert ".env.private-transport.tmp" not in text
    assert "value[0] == value[-1]" in text
    assert 'port="$dashboard_port"' in text
    assert "grep -E '^POLYMARKET_DASHBOARD_PORT='" not in text


def test_private_dashboard_setup_restores_env_until_transport_is_proven():
    text = (ROOT / "scripts" / "configure_polymarket_dashboard_tailscale.sh").read_text(
        encoding="utf-8"
    )

    backup = text.index('install -m 0600 .env "$env_backup"')
    failure_trap = text.index("trap 'restore_env_on_failure' EXIT")
    env_write = text.index('ENV_PATH="$REPO_DIR/.env" PRIVATE_URL="$private_url"')
    compose_recreate = text.index("--force-recreate polymarket-dashboard")
    failing_evidence = text.index(
        "# Until a live HTTPS request succeeds, publish explicit failing evidence."
    )
    private_probe = text.index('curl -fsS --max-time 10 "$private_url"')
    reachable_proof = text.index("--private-https-reachable")
    commit = text.index("env_committed=true")
    assert (
        backup
        < failure_trap
        < env_write
        < compose_recreate
        < failing_evidence
        < private_probe
        < reachable_proof
        < commit
    )
    assert 'install -m 0600 "$env_backup" .env' in text
    assert "value[0] == value[-1]" in text
    assert text.count("validate_dashboard_private_transport.py") == 2


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
    for service_name in ("polymarket-paper-live", "vps-ops-scheduler"):
        governance_mounts = " ".join(compose["services"][service_name]["volumes"])
        for needed in (
            "./AGENTS.md:/app/AGENTS.md:ro",
            "./CLAUDE.md:/app/CLAUDE.md:ro",
            "./docs:/app/docs:ro",
        ):
            assert needed in governance_mounts, f"{service_name} missing read-only governance mount {needed}"

    script = (ROOT / "scripts" / "run_vps_ops_scheduler.sh").read_text(encoding="utf-8")
    assert "refresh-governance" in script
    assert "superbru_clv_experiment.py snapshot" in script
    assert "run_daily_robust_pipeline.py" in script
    assert "x-requests-remaining" in script  # free-endpoint quota preflight
    assert 'timeout "$GOVERNANCE_TIMEOUT"' in script
    assert "status.json" in script
    assert "consecutive_skipped_cycles" in script
    assert 'skip_kind == "intentional"' in script
    assert 'skip_kind == "overrun"' in script
    assert "consecutive_skipped_intentional" in script
    assert "consecutive_skipped_overrun" in script
    assert 'stamp_status clv_snapshot 0 "skipped: odds quota exhausted" "" intentional' in script
    assert "audit_polymarket_local_history.py" in script
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
    # WO-85 completion correction: long jobs stay serialized, but a bounded
    # child wait keeps the safety/dashboard pulse live. Maker attribution is
    # no longer hidden at the tail of the heavy trade-print pipeline.
    assert "wait_with_safety_pulses" in script
    assert 'SAFETY_PULSE_INTERVAL="${OPS_SAFETY_PULSE_INTERVAL_SECONDS:-300}"' in script
    assert 'MAKER_SAFETY_INTERVAL="${OPS_MAKER_SAFETY_INTERVAL_SECONDS:-900}"' in script
    assert "run_maker_safety_refresh" in script
    trade_prints_body = script.split("run_trade_prints() {", 1)[1].split("run_maker_safety_refresh() {", 1)[0]
    maker_safety_body = script.split("run_maker_safety_refresh() {", 1)[1].split(
        "run_degraded_state_watchdog() {", 1
    )[0]
    assert "maker-live-test" not in trade_prints_body
    assert "decision-policy" not in trade_prints_body
    assert "requote-alerts" not in trade_prints_body
    for command in ("maker-live-test", "decision-policy", "requote-alerts"):
        assert command in maker_safety_body


def test_long_scheduler_job_wait_keeps_safety_pulse_live(tmp_path):
    trace = tmp_path / "safety-pulse.log"
    out_dir = tmp_path / "ops"
    out_dir.mkdir()
    script = ROOT / "scripts" / "run_vps_ops_scheduler.sh"
    env = {
        **os.environ,
        "OPS_SCHEDULER_LIBRARY_ONLY": "1",
        "OPS_SCHEDULER_OUT_DIR": str(out_dir),
        "OPS_SAFETY_PULSE_INTERVAL_SECONDS": "1",
        "OPS_SAFETY_POLL_SECONDS": "1",
        "TRACE": str(trace),
    }

    result = subprocess.run(
        [
            "sh",
            "-c",
            '. "$1"; '
            'run_maker_safety_refresh() { printf "maker\\n" >> "$TRACE"; }; '
            'run_degraded_state_watchdog() { printf "watchdog\\n" >> "$TRACE"; }; '
            '(sleep 2) & child=$!; wait_with_safety_pulses "$child" synthetic_long_job',
            "sh",
            str(script),
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    pulses = trace.read_text(encoding="utf-8").splitlines()
    assert "maker" in pulses
    assert "watchdog" in pulses


def test_failed_training_harvest_rearms_after_bounded_retry_backoff(tmp_path):
    out_dir = tmp_path / "ops"
    out_dir.mkdir()
    attempt_stamp = out_dir / "last_attempt_training_harvest"
    script = ROOT / "scripts" / "run_vps_ops_scheduler.sh"
    env = {
        **os.environ,
        "OPS_SCHEDULER_LIBRARY_ONLY": "1",
        "OPS_SCHEDULER_OUT_DIR": str(out_dir),
        "OPS_TRAINING_HARVEST_RETRY_SECONDS": "900",
    }

    # A future stamp is defensively clamped to age zero, so it remains inside
    # the retry window even under the suite's fixed wall clock.
    attempt_stamp.write_text("9999999999\n", encoding="utf-8")
    blocked = subprocess.run(
        ["sh", "-c", '. "$1"; training_harvest_retry_ready', "sh", str(script)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert blocked.returncode == 1

    attempt_stamp.write_text("1\n", encoding="utf-8")
    ready = subprocess.run(
        ["sh", "-c", '. "$1"; training_harvest_retry_ready', "sh", str(script)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert ready.returncode == 0, ready.stderr


def test_deploy_forced_governance_refresh_is_not_counted_as_scheduler_overrun(tmp_path):
    out_dir = tmp_path / "ops"
    out_dir.mkdir()
    (out_dir / "last_governance_refresh").write_text("0\n", encoding="utf-8")
    (out_dir / "status.json").write_text(
        json.dumps(
            {
                "jobs": {
                    "governance_refresh": {
                        "consecutive_skipped_cycles": 2,
                        "consecutive_skipped_overrun": 2,
                        "skipped_cycles_total": 3,
                        "skipped_overrun_total": 3,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    script = ROOT / "scripts" / "run_vps_ops_scheduler.sh"
    env = {
        **os.environ,
        "OPS_SCHEDULER_LIBRARY_ONLY": "1",
        "OPS_SCHEDULER_OUT_DIR": str(out_dir),
    }

    result = subprocess.run(
        [
            "sh",
            "-c",
            '. "$1"; JOB_SCHEDULE_SKIP_KIND="$(schedule_skip_kind governance_refresh 21600)"; '
            'stamp_status governance_refresh 0 "forced deployment refresh"',
            "sh",
            str(script),
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    job = json.loads((out_dir / "status.json").read_text(encoding="utf-8"))["jobs"]["governance_refresh"]
    assert job["skip_kind"] == "none"
    assert job["skipped_overrun"] is False
    assert job["consecutive_skipped_overrun"] == 0
    assert job["consecutive_skipped_cycles"] == 0
    assert job["skipped_overrun_total"] == 3


def test_vps_telemetry_push_script_is_single_commit_and_actions_free():
    # The telemetry bridge exists because the sandbox cannot reach the VPS and
    # GitHub Actions minutes are exhausted: it must never grow branch history,
    # never trigger workflows, and never ship the heavy collection corpora.
    script = Path("scripts/push_vps_telemetry.sh").read_text(encoding="utf-8")
    writer = Path("scripts/write_vps_telemetry_manifest.py").read_text(encoding="utf-8")
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
    assert "write_vps_telemetry_manifest.py" in script
    assert "source_git_rev" in writer
    assert "divergence_started_at_utc" in writer
    assert '"ls-remote", "origin", "refs/heads/main"' in writer
    assert '"rev-parse", "HEAD"' in writer
    assert "deployed_git_rev" in writer


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


def test_ops_scheduler_seasonal_disable_and_log_rotation_are_wired():
    # WO-114: the seasonal locked-card job gains a clean disable switch, and the
    # unbounded ops log gains rotation. Both are opt-out defaults that leave the
    # live behaviour unchanged until explicitly configured.
    script = (ROOT / "scripts" / "run_vps_ops_scheduler.sh").read_text(encoding="utf-8")
    assert 'CARD_REFRESH_ENABLED="${OPS_CARD_REFRESH_ENABLED:-1}"' in script
    assert 'if [ "$CARD_REFRESH_ENABLED" = "0" ]; then' in script
    assert "OPS_CARD_REFRESH_ENABLED=0" in script  # self-documenting skip detail
    # The disable guard must sit ahead of the odds preflight so a quiesced job
    # never even touches the odds endpoint.
    body = script.split("run_locked_card_refresh() {", 1)[1].split("run_training_harvest() {", 1)[0]
    assert body.index("CARD_REFRESH_ENABLED") < body.index("odds_quota_available")
    # Bounded rotation, defaulting to 50 MiB, wired into the top of the loop
    # where no job subshell holds the log open.
    assert 'LOG_MAX_BYTES="${OPS_LOG_MAX_BYTES:-52428800}"' in script
    assert "rotate_log_if_needed() {" in script
    loop_head = script.split("while :; do", 1)[1].split("governance_refresh", 1)[0]
    assert "rotate_log_if_needed" in loop_head


def test_ops_scheduler_disabled_seasonal_card_records_intentional_skip(tmp_path):
    out_dir = tmp_path / "ops"
    out_dir.mkdir()
    script = ROOT / "scripts" / "run_vps_ops_scheduler.sh"
    env = {
        **os.environ,
        "OPS_SCHEDULER_LIBRARY_ONLY": "1",
        "OPS_SCHEDULER_OUT_DIR": str(out_dir),
        "OPS_CARD_REFRESH_ENABLED": "0",
    }
    env.pop("THE_ODDS_API_KEY", None)

    result = subprocess.run(
        ["sh", "-c", '. "$1"; run_locked_card_refresh', "sh", str(script)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    job = json.loads((out_dir / "status.json").read_text(encoding="utf-8"))["jobs"]["locked_card_refresh"]
    assert job["last_exit_code"] == 0
    assert job["skip_kind"] == "intentional"
    assert job["skipped_intentional"] is True
    assert job["skipped_overrun"] is False
    assert "disabled" in job["detail"]
    # An intentional skip is a successful "nothing to do" cycle, so the success
    # stamp refreshes and the quiesced job never trips the staleness SLO.
    assert job["last_success_utc"]


def test_ops_scheduler_card_refresh_enabled_by_default_preflights_odds(tmp_path):
    out_dir = tmp_path / "ops"
    out_dir.mkdir()
    script = ROOT / "scripts" / "run_vps_ops_scheduler.sh"
    env = {
        **os.environ,
        "OPS_SCHEDULER_LIBRARY_ONLY": "1",
        "OPS_SCHEDULER_OUT_DIR": str(out_dir),
    }
    env.pop("OPS_CARD_REFRESH_ENABLED", None)  # default is enabled
    env.pop("THE_ODDS_API_KEY", None)  # make the free-quota preflight decline

    result = subprocess.run(
        ["sh", "-c", '. "$1"; run_locked_card_refresh', "sh", str(script)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    job = json.loads((out_dir / "status.json").read_text(encoding="utf-8"))["jobs"]["locked_card_refresh"]
    assert job["skip_kind"] == "intentional"
    # The default (enabled) path falls through the disable guard to the odds
    # preflight, so the decline reason is quota exhaustion, not the switch.
    assert "odds quota exhausted" in job["detail"]
    assert "disabled" not in job["detail"]


def test_ops_scheduler_rotates_oversized_log(tmp_path):
    out_dir = tmp_path / "ops"
    out_dir.mkdir()
    log_file = out_dir / "ops_scheduler.log"
    log_file.write_text("x" * 5000 + "\n", encoding="utf-8")
    script = ROOT / "scripts" / "run_vps_ops_scheduler.sh"
    env = {
        **os.environ,
        "OPS_SCHEDULER_LIBRARY_ONLY": "1",
        "OPS_SCHEDULER_OUT_DIR": str(out_dir),
        "OPS_LOG_MAX_BYTES": "1000",
    }

    result = subprocess.run(
        ["sh", "-c", '. "$1"; rotate_log_if_needed', "sh", str(script)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    rotated = out_dir / "ops_scheduler.log.1"
    assert rotated.exists()
    assert "x" * 5000 in rotated.read_text(encoding="utf-8")
    live = log_file.read_text(encoding="utf-8")
    assert "log rotated" in live  # fresh log carries only the rotation notice
    assert "xxxx" not in live


def test_ops_scheduler_log_rotation_is_noop_when_small_or_disabled(tmp_path):
    out_dir = tmp_path / "ops"
    out_dir.mkdir()
    log_file = out_dir / "ops_scheduler.log"
    script = ROOT / "scripts" / "run_vps_ops_scheduler.sh"

    # (a) Below the cap: nothing rotates.
    log_file.write_text("small\n", encoding="utf-8")
    env = {
        **os.environ,
        "OPS_SCHEDULER_LIBRARY_ONLY": "1",
        "OPS_SCHEDULER_OUT_DIR": str(out_dir),
        "OPS_LOG_MAX_BYTES": "1000000",
    }
    subprocess.run(
        ["sh", "-c", '. "$1"; rotate_log_if_needed', "sh", str(script)],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert not (out_dir / "ops_scheduler.log.1").exists()
    assert log_file.read_text(encoding="utf-8") == "small\n"

    # (b) Rotation disabled: never rotates, even far over any threshold.
    log_file.write_text("y" * 5000, encoding="utf-8")
    env["OPS_LOG_MAX_BYTES"] = "0"
    subprocess.run(
        ["sh", "-c", '. "$1"; rotate_log_if_needed', "sh", str(script)],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert not (out_dir / "ops_scheduler.log.1").exists()
    assert log_file.read_text(encoding="utf-8") == "y" * 5000


def test_wo117_maker_study_overrun_classification_is_window_aware():
    # WO-117: maker_study_intraday may only fire inside the registered 11-13h
    # harvest-age window, whose daily recurrence drifts by more than one tick,
    # so lateness must be judged against interval + one window width - not the
    # bare interval, which stamped every legitimate on-window run "overrun".
    script = (ROOT / "scripts" / "run_vps_ops_scheduler.sh").read_text(encoding="utf-8")
    assert (
        "MAKER_STUDY_WINDOW_TOLERANCE=$((MAKER_STUDY_INTRADAY_OFFSET_MAX - MAKER_STUDY_INTRADAY_OFFSET_MIN))"
        in script
    )
    assert (
        'schedule_skip_kind maker_study_intraday $((MAKER_STUDY_INTRADAY_INTERVAL + MAKER_STUDY_WINDOW_TOLERANCE))'
        in script
    )
    # The bare-interval form must be gone for this job only; other jobs keep it.
    assert 'schedule_skip_kind maker_study_intraday "$MAKER_STUDY_INTRADAY_INTERVAL"' not in script
    assert 'schedule_skip_kind trade_prints "$PRINTS_INTERVAL"' in script


def test_wo117_window_tolerance_boundary_semantics(tmp_path):
    # A run 25h after its stamp (inside interval + 2h window width) is
    # on-schedule for the window-gated job; the same age judged against the
    # bare interval would have been "overrun". Beyond interval + tolerance +
    # tick, overrun still stamps - real starvation stays visible.
    out_dir = tmp_path / "ops"
    out_dir.mkdir()
    script = ROOT / "scripts" / "run_vps_ops_scheduler.sh"
    env = {
        **os.environ,
        "OPS_SCHEDULER_LIBRARY_ONLY": "1",
        "OPS_SCHEDULER_OUT_DIR": str(out_dir),
    }
    stamp = out_dir / "last_maker_study_intraday"
    now = int(__import__("time").time())

    def kind(age_seconds: int, effective_interval: int) -> str:
        stamp.write_text(f"{now - age_seconds}\n", encoding="utf-8")
        result = subprocess.run(
            ["sh", "-c", f'. "$1"; schedule_skip_kind maker_study_intraday {effective_interval}', "sh", str(script)],
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        return result.stdout.strip()

    interval, tolerance, tick = 86400, 7200, 300
    age_25h = 90000
    assert kind(age_25h, interval) == "overrun"  # bare interval: mislabeled
    assert kind(age_25h, interval + tolerance) == ""  # window-aware: on-schedule
    assert kind(interval + tolerance + tick + 60, interval + tolerance) == "overrun"


def test_private_dashboard_setup_waits_for_recreated_backend():
    # WO-114: the loopback readiness check must be a bounded retry loop, not a
    # single probe, so it does not race the just-force-recreated reporting
    # container (which reset connections mid-startup in practice).
    text = (ROOT / "scripts" / "configure_polymarket_dashboard_tailscale.sh").read_text(
        encoding="utf-8"
    )
    assert "backend_ready=false" in text
    assert 'while [ "$probe" -lt 30 ]; do' in text
    assert "within 60s" in text
    recreate = text.index("--force-recreate polymarket-dashboard")
    retry_loop = text.index("backend_ready=false")
    serve_enable = text.index("tailscale serve --bg --yes --https=443")
    assert recreate < retry_loop < serve_enable
