from __future__ import annotations

import json
import os
import re
import subprocess
import textwrap
from pathlib import Path

import yaml

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from polymarket_predictive_engine.degraded_state_watchdog import (  # noqa: E402
    MARKED_JOBS,
)


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
    assert 'COMMAND_TIMEOUT="${DEPLOY_ACCEPTANCE_COMMAND_TIMEOUT_SECONDS:-900}"' in acceptance_script
    assert 'TOTAL_TIMEOUT="${DEPLOY_ACCEPTANCE_TOTAL_TIMEOUT_SECONDS:-1200}"' in acceptance_script
    assert "run_bounded" in acceptance_script
    # Every producer must record wall seconds beside its exit code: the
    # 2026-07-29 prices-history upstream slowdown took a forensic VPS session
    # to localize precisely because this artifact held exit codes only.
    assert 'run_producer maker_carry_study' in acceptance_script
    assert '"duration_seconds": int(os.environ[f"{name}_seconds"])' in acceptance_script
    assert "timeout --signal=TERM --kill-after=30s 1500" in text
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


def _workflow_text() -> str:
    return (ROOT / ".github" / "workflows" / "deploy-polymarket-vps-paper.yml").read_text(
        encoding="utf-8"
    )


def test_vps_deploy_acceptance_outer_wrappers_outlast_the_inner_budget():
    """The acceptance lane has THREE nested clocks: the per-command bound, the
    script's total budget, and the deploy paths' outer `timeout` around the
    whole compose run. The ca8c3a3 deploy (2026-07-28) failed on the innermost
    one killing a healthy 70s-baseline study at 120s under post-recreate
    contention. Whatever the numbers become, the ordering must hold:
    command <= total, and both deploy paths' outer wrappers must exceed the
    total by real margin (container start + report writing), or a slow-but-
    passing acceptance is killed from outside and reads as a deploy failure.
    """
    acceptance = (ROOT / "scripts" / "run_vps_deploy_acceptance.sh").read_text(encoding="utf-8")
    command = int(re.search(r"DEPLOY_ACCEPTANCE_COMMAND_TIMEOUT_SECONDS:-(\d+)", acceptance).group(1))
    total = int(re.search(r"DEPLOY_ACCEPTANCE_TOTAL_TIMEOUT_SECONDS:-(\d+)", acceptance).group(1))
    assert command <= total

    # Warm baselines measured on the VPS 2026-07-28 (training_harvest.json):
    # study 69.9s, fill-replay 47.6s. The per-command bound must hold at least
    # ~2x the slowest warm producer or deploy-time contention re-kills it.
    assert command >= 240

    for path in (
        ROOT / ".github" / "workflows" / "deploy-polymarket-vps-paper.yml",
        ROOT / "scripts" / "deploy_vps_paper_manual.sh",
    ):
        text = path.read_text(encoding="utf-8")
        outer_bounds = [
            int(match)
            for match in re.findall(
                r"timeout --signal=TERM --kill-after=30s (\d+)\s*\\\n\s*\$DOCKER compose[^\n]*deploy-acceptance",
                text,
            )
        ]
        assert outer_bounds, f"no outer acceptance timeout found in {path.name}"
        for bound in outer_bounds:
            assert bound >= total + 120, (
                f"{path.name}: outer acceptance wrapper {bound}s must exceed the "
                f"script's total budget {total}s with margin"
            )

    # Codex review P1 on #391: the workflow's job-level cap sits ABOVE all of
    # these clocks, and its own comment history records two mid-rollout
    # cancellations from exactly this drift. Whatever the numbers become, the
    # job cap must clear the acceptance ceiling plus the documented rest of a
    # worst-case rollout (checkout+build ~10m, 20m governance wait,
    # health/rollback headroom ~5m).
    workflow = (ROOT / ".github" / "workflows" / "deploy-polymarket-vps-paper.yml").read_text(
        encoding="utf-8"
    )
    job_cap_minutes = int(re.search(r"timeout-minutes:\s*(\d+)", workflow).group(1))
    acceptance_outer = max(
        int(match)
        for match in re.findall(
            r"timeout --signal=TERM --kill-after=30s (\d+)\s*\\\n\s*\$DOCKER compose[^\n]*deploy-acceptance",
            workflow,
        )
    )
    assert job_cap_minutes * 60 >= acceptance_outer + 35 * 60, (
        f"workflow timeout-minutes {job_cap_minutes} cannot cover the {acceptance_outer}s "
        "acceptance ceiling plus a worst-case rollout; the runner would be "
        "cancelled mid-deploy, before rollback"
    )


def test_vps_deploy_acceptance_never_passes_no_build_to_compose_run():
    """Path A parity with WO-133's fix. Observed on the VPS 2026-07-28.

    Path B carried a verbatim copy of this workflow's acceptance line and died:

        unknown flag: --no-build
        ERROR: deploy acceptance failed

    `--no-build` is an `up` flag; `docker compose run` has no such option, so
    acceptance aborts before it executes. This workflow had the identical defect,
    unnoticed because Path A has been blocked on its acceptance_run_id gate.

    Removing it changes nothing about what runs - `run` does not build by default
    and `up -d --build` has already built the image - and it is asserted here
    because the two sibling callers that were always correct
    (restore_from_archive.sh, push_vps_archive.sh) prove the correct form.
    """
    text = _workflow_text()
    acceptance = text[text.index("--profile deploy-acceptance run") :]
    acceptance = acceptance[: acceptance.index("</dev/null")]
    assert "--no-build" not in acceptance
    assert "--rm --no-deps vps-deploy-acceptance" in acceptance

    # `up` does accept it, and the scheduler restart must keep it so restarting
    # the scheduler never rebuilds the image that was just deployed and verified.
    assert "up -d --no-build vps-ops-scheduler" in text

    for sibling in ("restore_from_archive.sh", "push_vps_archive.sh"):
        source = (ROOT / "scripts" / sibling).read_text(encoding="utf-8")
        run_lines = [line for line in source.splitlines() if "compose" in line and " run " in line]
        assert run_lines, sibling
        for line in run_lines:
            assert "--no-build" not in line, f"{sibling}: {line}"


def test_vps_deploy_rollback_probe_outlasts_a_cold_dashboard_start():
    """Path A parity with WO-133's second fix.

    The rollback recreates the dashboard container and then re-probes it. With no
    overrides it inherits the helper's 5 x 2s default - a ~8s span - so a healthy
    restore gets reported as MANUAL_INTERVENTION_REQUIRED purely because the
    dashboard had not finished starting. Observed on Path B 2026-07-28: the
    restore was correct in every respect and the same URL returned 200 shortly
    after.

    The window is bought with the INTERVAL because the helper clamps attempts at
    10, so a larger attempt count is silently truncated.
    """
    text = _workflow_text()
    helper = (ROOT / "scripts" / "rollback_vps_paper_deploy.py").read_text(encoding="utf-8")

    attempts_cap = int(
        re.search(r"range\(max\(1,\s*min\((\d+),\s*probe_attempts\)\)\)", helper).group(1)
    )
    interval_cap = float(re.search(r"min\(([\d.]+),\s*probe_interval_seconds\)", helper).group(1))

    asked_attempts = int(re.search(r"--https-probe-attempts (\d+)", text).group(1))
    asked_interval = float(re.search(r"--https-probe-interval-seconds ([\d.]+)", text).group(1))

    # Requesting beyond a cap reads as a long window and behaves as a short one.
    assert asked_attempts <= attempts_cap
    assert asked_interval <= interval_cap
    # Effective span: the last sleep buys no further retry.
    assert (min(attempts_cap, asked_attempts) - 1) * min(interval_cap, asked_interval) >= 60.0


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
    # §151.2 sixth-file scope (2026-08-04): the stale "11-13h offset guard"
    # detail-string tail was corrected together with the decoupling that
    # removed the guard it named as a precondition - this pinned literal and
    # the scheduler string change together, in the same build, or neither.
    assert "decoupled standalone-interval cadence, no harvest-window precondition" in script
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
    # WO-120 keeps an UNSET key an intentional skip (a deliberate config
    # state); only a present-but-failing preflight goes loud.
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


def test_wo117_maker_study_overrun_classification_is_standalone_interval_now():
    # WO-117 originally required lateness to be judged against interval + one
    # harvest-window width, because maker_study_intraday could only fire
    # inside the registered 11-13h harvest-age window and its daily
    # recurrence drifted by more than one tick. §151.2's sixth-file scope
    # (2026-08-04, reconciling #433 CODEX ROUND-1 sched:878): that window
    # guard is now REMOVED as a precondition (WO-151 §151.1), so there is no
    # window width left to derive a tolerance from, and keeping the old
    # 7200s add-on would silently widen this job's overrun tolerance beyond
    # every other standalone-interval job's - masking real starvation.
    # maker_study_intraday now takes the bare-interval form, exactly like
    # trade_prints and the other standalone-interval jobs, and relies solely
    # on schedule_skip_kind's own built-in TICK_SECONDS grace.
    script = (ROOT / "scripts" / "run_vps_ops_scheduler.sh").read_text(encoding="utf-8")
    # The harvest-window tolerance variable is gone from this call site -
    # removed, not repurposed under the same name (the old finding's exact
    # arithmetic - OFFSET_MAX - OFFSET_MIN feeding a bare $((...)) - is what
    # made a nonnumeric OPS_MAKER_STUDY_INTRADAY_OFFSET_*_SECONDS able to kill
    # the whole scheduler; #433 CODEX ROUND-1 sched:877).
    assert "MAKER_STUDY_WINDOW_TOLERANCE" not in script
    assert (
        "MAKER_STUDY_WINDOW_TOLERANCE=$((MAKER_STUDY_INTRADAY_OFFSET_MAX - MAKER_STUDY_INTRADAY_OFFSET_MIN))"
        not in script
    )
    assert (
        'schedule_skip_kind maker_study_intraday $((MAKER_STUDY_INTRADAY_INTERVAL + MAKER_STUDY_WINDOW_TOLERANCE))'
        not in script
    )
    # The standalone-interval form now matches every other bare-interval job.
    assert 'schedule_skip_kind maker_study_intraday "$MAKER_STUDY_INTRADAY_INTERVAL"' in script
    assert 'schedule_skip_kind trade_prints "$PRINTS_INTERVAL"' in script
    # OFFSET_MIN/MAX are still read and numerically clamped (they still feed
    # maker_carry_study.py's own independent harvest-age reporting fields,
    # and remain a documented env-var surface), but no longer arithmetic'd
    # against each other at this call site (#433 CODEX ROUND-1 sched:877).
    assert 'case "$MAKER_STUDY_INTRADAY_OFFSET_MIN" in \'\'|*[!0-9]*) MAKER_STUDY_INTRADAY_OFFSET_MIN=39600 ;; esac' in script
    assert 'case "$MAKER_STUDY_INTRADAY_OFFSET_MAX" in \'\'|*[!0-9]*) MAKER_STUDY_INTRADAY_OFFSET_MAX=46800 ;; esac' in script


def test_wo117_offset_env_nonnumeric_value_does_not_kill_the_scheduler(tmp_path):
    # #433 CODEX ROUND-1 P1 sched:877: OPS_MAKER_STUDY_INTRADAY_OFFSET_MIN/MAX
    # _SECONDS used to feed an unconditional $((MAX - MIN)) with no numeric
    # guard - a nonnumeric value there raised a shell "illegal number" error
    # that killed the whole scheduler loop, not just this one job. Mirrors
    # test_book_pulse_interval_and_timeout_env_clamps's source-the-script
    # pattern: a garbage env value must clamp to the registered default and
    # the script must still source cleanly (exit 0).
    script = ROOT / "scripts" / "run_vps_ops_scheduler.sh"
    out_dir = tmp_path / "ops"
    out_dir.mkdir()

    def _read(**env_overrides):
        env = {
            **os.environ,
            "OPS_SCHEDULER_LIBRARY_ONLY": "1",
            "OPS_SCHEDULER_OUT_DIR": str(out_dir),
            **env_overrides,
        }
        result = subprocess.run(
            [
                "sh",
                "-c",
                '. "$1"; printf "%s %s\\n" '
                '"$MAKER_STUDY_INTRADAY_OFFSET_MIN" "$MAKER_STUDY_INTRADAY_OFFSET_MAX"',
                "sh",
                str(script),
            ],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        offset_min, offset_max = result.stdout.strip().split()
        return int(offset_min), int(offset_max)

    assert _read(OPS_MAKER_STUDY_INTRADAY_OFFSET_MIN_SECONDS="abc") == (39600, 46800)
    assert _read(OPS_MAKER_STUDY_INTRADAY_OFFSET_MAX_SECONDS="not-a-number") == (39600, 46800)
    assert _read(OPS_MAKER_STUDY_INTRADAY_OFFSET_MIN_SECONDS="") == (39600, 46800)
    # A numeric override still passes through unclamped (no range clamp is
    # registered for these, only the numeric-format guard).
    assert _read(OPS_MAKER_STUDY_INTRADAY_OFFSET_MIN_SECONDS="41000") == (41000, 46800)
    # Defaults, sanity-checked alongside the clamps.
    assert _read() == (39600, 46800)


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


def test_wo120_scheduler_fail_loud_plumbing_is_wired():
    script = (ROOT / "scripts" / "run_vps_ops_scheduler.sh").read_text(encoding="utf-8")
    # status.json is written atomically (it was the one non-atomic stamp file;
    # a torn write wiped all job history and disarmed both scheduler
    # watchdog registrations).
    assert "os.replace(tmp_path, path)" in script
    # Odds preflight taxonomy: 0 quota-ok / 1 quota-exhausted / 2 preflight
    # error - and both odds callers stamp a LOUD failure on 2 instead of an
    # intentional skip that refreshes the success stamp.
    preflight = script.split("odds_quota_available() {", 1)[1].split("\n}", 1)[0]
    assert "return 2" in preflight
    assert script.count('stamp_status clv_snapshot 1 "odds preflight failed') == 1
    assert script.count('stamp_status locked_card_refresh 1 "odds preflight failed') == 1
    assert 'stamp_status clv_snapshot 0 "skipped: odds quota exhausted" "" intentional' in script
    # Corrupt-stamp guard in the one reader that lacked it.
    reader = script.split("seconds_since_stamp() {", 1)[1].split("\n}", 1)[0]
    assert "*[!0-9]*" in reader
    # Rotation failure is loud, not silent.
    assert "log rotation FAILED" in script
    # Job-local start stamps: the shared STARTED_AT global was clobbered by
    # concurrent safety pulses (ledger_anchor's duration was the pulse's).
    assert 'LEDGER_ANCHOR_STARTED_AT=$(date' in script
    assert '"$LEDGER_ANCHOR_STARTED_AT"' in script
    assert 'MAKER_SAFETY_STARTED_AT=$(date' in script
    assert 'PULSE_STARTED_AT=$(date' in script


def test_wo120_corrupt_stamp_keeps_job_schedulable(tmp_path):
    out_dir = tmp_path / "ops"
    out_dir.mkdir()
    script = ROOT / "scripts" / "run_vps_ops_scheduler.sh"
    env = {
        **os.environ,
        "OPS_SCHEDULER_LIBRARY_ONLY": "1",
        "OPS_SCHEDULER_OUT_DIR": str(out_dir),
    }
    (out_dir / "last_governance_refresh").write_text("garbage\n", encoding="utf-8")

    result = subprocess.run(
        ["sh", "-c", '. "$1"; seconds_since_stamp governance_refresh', "sh", str(script)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    # A corrupt stamp used to produce an arithmetic error (empty output) that
    # broke the caller's [ -ge ] test - the job was never scheduled again.
    assert result.returncode == 0, result.stderr
    value = result.stdout.strip()
    assert value.isdigit()
    assert int(value) > 0  # treated as epoch 0: immediately due


def test_wo120_stamp_status_leaves_no_temp_file_and_valid_json(tmp_path):
    out_dir = tmp_path / "ops"
    out_dir.mkdir()
    script = ROOT / "scripts" / "run_vps_ops_scheduler.sh"
    env = {
        **os.environ,
        "OPS_SCHEDULER_LIBRARY_ONLY": "1",
        "OPS_SCHEDULER_OUT_DIR": str(out_dir),
    }

    result = subprocess.run(
        ["sh", "-c", '. "$1"; stamp_status trade_prints 0 "detail" ""', "sh", str(script)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads((out_dir / "status.json").read_text(encoding="utf-8"))
    assert payload["jobs"]["trade_prints"]["last_exit_code"] == 0
    assert not [p for p in out_dir.iterdir() if p.name.endswith(".tmp")]


def test_wo143_paper_cycle_interval_and_timeout_clamp_bands(tmp_path):
    out_dir = tmp_path / "ops"
    out_dir.mkdir()
    script = ROOT / "scripts" / "run_vps_ops_scheduler.sh"

    def interval_for(value):
        env = {
            **os.environ,
            "OPS_SCHEDULER_LIBRARY_ONLY": "1",
            "OPS_SCHEDULER_OUT_DIR": str(out_dir),
            "OPS_PAPER_CYCLE_INTERVAL_SECONDS": value,
        }
        result = subprocess.run(
            ["sh", "-c", '. "$1"; echo "$PAPER_CYCLE_INTERVAL"', "sh", str(script)],
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        return int(result.stdout.strip())

    def timeout_for(value):
        env = {
            **os.environ,
            "OPS_SCHEDULER_LIBRARY_ONLY": "1",
            "OPS_SCHEDULER_OUT_DIR": str(out_dir),
            "OPS_PAPER_CYCLE_TIMEOUT_SECONDS": value,
        }
        result = subprocess.run(
            ["sh", "-c", '. "$1"; echo "$PAPER_CYCLE_TIMEOUT"', "sh", str(script)],
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        return int(result.stdout.strip())

    # Two-sided: config may run more often (floor 3600) or less often
    # (ceiling 14400) and cannot leave that band, mirroring the
    # HARVEST_RETRY_INTERVAL precedent for bounding a heavy job.
    assert interval_for("999999") == 14400
    assert interval_for("60") == 3600
    assert interval_for("abc") == 14400
    assert timeout_for("99999") == 1800
    # WO-143.7(d) registered test (6): "0" is all digits, so it passes the
    # case-statement check unchanged and previously reached GNU `timeout`,
    # where "a duration of 0 disables the associated timeout" -- an unbounded
    # cycle holding the prediction_cycle lock. It must resolve to the
    # registered positive default instead.
    assert timeout_for("0") == 1800
    assert timeout_for("900") == 900


def test_wo143_paper_cycle_job_wiring_and_loop_position():
    script = (ROOT / "scripts" / "run_vps_ops_scheduler.sh").read_text(encoding="utf-8")
    assert (
        'timeout "$PAPER_CYCLE_TIMEOUT" python -m polymarket_predictive_engine.cli scheduled-paper-cycle '
        '--config "$CONFIG_PATH" --paper-source websocket'
        in script
    )
    assert 'wait_with_safety_pulses "$JOB_PID" paper_cycle' in script
    assert 'PAPER_CYCLE_STARTED_AT=$(date' in script
    assert '"$PAPER_CYCLE_STARTED_AT"' in script
    # The SIGTERM handler installed by scheduled_paper_cycle.py must be
    # allowed to unwind the prediction_cycle lock cleanly -- `timeout -k`
    # would defeat that with a follow-up SIGKILL. No job in this script uses it.
    assert "timeout -k" not in script
    loop_body = script.split("while :; do", 1)[1]
    assert loop_body.index("run_trade_prints") < loop_body.index("run_paper_cycle_job") < loop_body.index("run_ledger_anchor")


def test_wo143_paper_cycle_failure_accounting_sequence(tmp_path):
    out_dir = tmp_path / "ops"
    out_dir.mkdir()
    script = ROOT / "scripts" / "run_vps_ops_scheduler.sh"
    env = {
        **os.environ,
        "OPS_SCHEDULER_LIBRARY_ONLY": "1",
        "OPS_SCHEDULER_OUT_DIR": str(out_dir),
    }

    subprocess.run(
        ["sh", "-c", '. "$1"; stamp_status paper_cycle 75 "lock contention" "2026-07-15T00:00:00Z"', "sh", str(script)],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    job = json.loads((out_dir / "status.json").read_text(encoding="utf-8"))["jobs"]["paper_cycle"]
    assert job["last_success_utc"] == ""
    assert isinstance(job["duration_seconds"], (int, float))

    subprocess.run(
        ["sh", "-c", '. "$1"; stamp_status paper_cycle 0 "ran" "2026-07-15T00:05:00Z"', "sh", str(script)],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    job = json.loads((out_dir / "status.json").read_text(encoding="utf-8"))["jobs"]["paper_cycle"]
    assert job["last_success_utc"]

    subprocess.run(
        ["sh", "-c", '. "$1"; stamp_status paper_cycle 124 "overrun" "2026-07-15T00:10:00Z"', "sh", str(script)],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    job = json.loads((out_dir / "status.json").read_text(encoding="utf-8"))["jobs"]["paper_cycle"]
    assert job["skip_kind"] == "overrun"


def test_wo143_paper_cycle_disabled_records_intentional_skip(tmp_path):
    out_dir = tmp_path / "ops"
    out_dir.mkdir()
    script = ROOT / "scripts" / "run_vps_ops_scheduler.sh"
    env = {
        **os.environ,
        "OPS_SCHEDULER_LIBRARY_ONLY": "1",
        "OPS_SCHEDULER_OUT_DIR": str(out_dir),
        "OPS_PAPER_CYCLE_ENABLED": "0",
    }

    result = subprocess.run(
        ["sh", "-c", '. "$1"; run_paper_cycle_job', "sh", str(script)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    job = json.loads((out_dir / "status.json").read_text(encoding="utf-8"))["jobs"]["paper_cycle"]
    assert job["last_exit_code"] == 0
    assert job["skip_kind"] == "intentional"
    assert job["skipped_intentional"] is True
    assert "disabled" in job["detail"]
    # An intentional skip refreshes the success stamp so the quiesced job
    # never trips the scheduler_completion_freshness SLO.
    assert job["last_success_utc"]


def test_wo118_disabled_superbru_watchdog_cannot_reach_the_submitting_loop():
    # WO-118: this is the only script that takes an external action (submitting
    # picks). The disabled branch must terminate explicitly - if `tail` ever
    # dies, control must not fall through into the `while true` submit loop.
    script = (ROOT / "scripts" / "run_superbru_auto_pick_watchdog.sh").read_text(encoding="utf-8")
    disabled_branch = script.split('SUPERBRU_AUTO_PICK_ENABLED:-true}" != "true"', 1)[1].split("fi", 1)[0]
    assert "tail -f /dev/null" in disabled_branch
    assert "exit 0" in disabled_branch.split("tail -f /dev/null", 1)[1]


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


# --- WO-149: book_pulse scheduler job ---


def test_book_pulse_interval_and_timeout_env_clamps(tmp_path):
    # Test (17): clamps 999999->900, 60->300, abc->300, timeout 99999->240.
    script = ROOT / "scripts" / "run_vps_ops_scheduler.sh"
    out_dir = tmp_path / "ops"
    out_dir.mkdir()

    def _read(**env_overrides):
        env = {
            **os.environ,
            "OPS_SCHEDULER_LIBRARY_ONLY": "1",
            "OPS_SCHEDULER_OUT_DIR": str(out_dir),
            **env_overrides,
        }
        result = subprocess.run(
            ["sh", "-c", '. "$1"; printf "%s %s\\n" "$BOOK_PULSE_INTERVAL" "$BOOK_PULSE_TIMEOUT"', "sh", str(script)],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        interval, timeout = result.stdout.strip().split()
        return int(interval), int(timeout)

    assert _read(OPS_BOOK_PULSE_INTERVAL_SECONDS="999999")[0] == 900
    assert _read(OPS_BOOK_PULSE_INTERVAL_SECONDS="60")[0] == 300
    assert _read(OPS_BOOK_PULSE_INTERVAL_SECONDS="abc")[0] == 300
    assert _read(OPS_BOOK_PULSE_TIMEOUT_SECONDS="99999")[1] == 240
    # Defaults, sanity-checked alongside the clamps.
    assert _read() == (300, 240)


def test_book_pulse_job_is_wired_like_trade_prints_and_ordered_before_ledger_anchor():
    # Test (18): static wiring - the exact command line,
    # wait_with_safety_pulses "$JOB_PID" book_pulse, stamp_status with
    # started-at, loop block after trade_prints and before ledger_anchor.
    script = (ROOT / "scripts" / "run_vps_ops_scheduler.sh").read_text(encoding="utf-8")

    assert "run_book_pulse() {" in script
    assert (
        'python -m polymarket_predictive_engine.cli snapshot-official-books-pulse --config "$CONFIG_PATH"'
        in script
    )
    assert 'wait_with_safety_pulses "$JOB_PID" book_pulse' in script
    body = script.split("run_book_pulse() {", 1)[1].split("\nrun_maker_safety_refresh() {", 1)[0]
    assert "BOOK_PULSE_STARTED_AT=$(date" in body
    assert 'stamp_status book_pulse "$CODE"' in body
    assert '"$BOOK_PULSE_STARTED_AT"' in body

    loop = script.split("while :; do", 1)[1]
    trade_call_idx = loop.index("run_trade_prints\n")
    pulse_call_idx = loop.index("run_book_pulse\n")
    ledger_call_idx = loop.index("run_ledger_anchor\n")
    assert trade_call_idx < pulse_call_idx < ledger_call_idx


def test_book_pulse_overrun_leaves_last_success_empty_with_numeric_duration_and_recovers(tmp_path):
    # Test (19): exit 124 records skip_kind: "overrun" and leaves
    # last_success_utc empty with a numeric duration; a following exit 0
    # refreshes it.
    out_dir = tmp_path / "ops"
    out_dir.mkdir()
    script = ROOT / "scripts" / "run_vps_ops_scheduler.sh"
    env = {**os.environ, "OPS_SCHEDULER_LIBRARY_ONLY": "1", "OPS_SCHEDULER_OUT_DIR": str(out_dir)}

    overrun = subprocess.run(
        ["sh", "-c", '. "$1"; stamp_status book_pulse 124 "overrun" "2026-08-01T00:00:00Z"', "sh", str(script)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert overrun.returncode == 0, overrun.stderr
    job = json.loads((out_dir / "status.json").read_text(encoding="utf-8"))["jobs"]["book_pulse"]
    assert job["skip_kind"] == "overrun"
    assert job["last_success_utc"] == ""
    assert isinstance(job["duration_seconds"], (int, float))

    recovered = subprocess.run(
        ["sh", "-c", '. "$1"; stamp_status book_pulse 0 "ok" "2026-08-01T00:05:00Z"', "sh", str(script)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert recovered.returncode == 0, recovered.stderr
    job_recovered = json.loads((out_dir / "status.json").read_text(encoding="utf-8"))["jobs"]["book_pulse"]
    assert job_recovered["last_success_utc"] != ""
    assert job_recovered["skip_kind"] == "none"


def test_book_pulse_disabled_stamps_intentional_skip_at_exit_zero(tmp_path):
    # Test (20): OPS_BOOK_PULSE_ENABLED=0 stamps an intentional skip at exit 0.
    out_dir = tmp_path / "ops"
    out_dir.mkdir()
    script = ROOT / "scripts" / "run_vps_ops_scheduler.sh"
    env = {
        **os.environ,
        "OPS_SCHEDULER_LIBRARY_ONLY": "1",
        "OPS_SCHEDULER_OUT_DIR": str(out_dir),
        "OPS_BOOK_PULSE_ENABLED": "0",
    }

    result = subprocess.run(
        ["sh", "-c", '. "$1"; run_book_pulse', "sh", str(script)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    job = json.loads((out_dir / "status.json").read_text(encoding="utf-8"))["jobs"]["book_pulse"]
    assert job["last_exit_code"] == 0
    assert job["skip_kind"] == "intentional"


# ---------------------------------------------------------------------------
# WO-152 — mark_in_flight, the scheduler side of the starvation attribution
# ledger. Write-only telemetry: no job's execution, scheduling decision, exit
# code, skip classification, or timeout depends on the marker, and a failing
# mark_in_flight logs and proceeds.
# ---------------------------------------------------------------------------

_WO152_SCHEDULER = ROOT / "scripts" / "run_vps_ops_scheduler.sh"
# The watchdog owns the canonical membership; the scheduler's call sites are
# asserted against it rather than against a hand-copied list.
#
# It is now actually IMPORTED. This was a duplicate frozenset directly under a
# comment claiming it was not one, and it drifted the moment MARKED_JOBS gained
# an entry -- so the test failed for the copy being stale rather than for the
# scheduler being wrong, which is the opposite of what it exists to detect.
_WO152_MARKED_JOBS = MARKED_JOBS


def _wo152_env(out_dir: Path) -> dict:
    return {
        **os.environ,
        "OPS_SCHEDULER_LIBRARY_ONLY": "1",
        "OPS_SCHEDULER_OUT_DIR": str(out_dir),
    }


def _wo152_call(out_dir: Path, snippet: str, shell: str = "sh") -> subprocess.CompletedProcess:
    return subprocess.run(
        [shell, "-c", f'set -u; . "$1"; {snippet}', "sh", str(_WO152_SCHEDULER)],
        env=_wo152_env(out_dir),
        text=True,
        capture_output=True,
        check=False,
    )


def test_wo152_mark_in_flight_sets_only_the_marker_and_preserves_every_other_record(tmp_path):
    """WO-152 Test 1."""
    out_dir = tmp_path / "ops"
    out_dir.mkdir()
    seed = {
        "mode": "vps_ops_scheduler",
        "jobs": {
            "training_harvest": {"last_exit_code": 0, "runs_total": 34, "failed_cycles_total": 14},
            "trade_prints": {"last_exit_code": 0, "runs_total": 2604},
        },
    }
    (out_dir / "status.json").write_text(json.dumps(seed), encoding="utf-8")

    result = _wo152_call(out_dir, 'mark_in_flight training_harvest "2026-08-04T22:54:01Z"')

    assert result.returncode == 0, result.stderr
    payload = json.loads((out_dir / "status.json").read_text(encoding="utf-8"))
    harvest = payload["jobs"]["training_harvest"]
    assert harvest["in_flight_since_utc"] == "2026-08-04T22:54:01Z"
    # Every other key on that job's record, and every other job's record.
    assert harvest["runs_total"] == 34
    assert harvest["failed_cycles_total"] == 14
    assert payload["jobs"]["trade_prints"] == seed["jobs"]["trade_prints"]
    assert payload["mode"] == "vps_ops_scheduler"
    assert not [p for p in out_dir.iterdir() if p.name.endswith(".tmp")]


def test_wo152_stamp_status_clears_the_marker_on_success_and_on_failure(tmp_path):
    """WO-152 Test 2. stamp_status rebuilds jobs[job] wholesale, so the marker's
    absence after completion is permanent by construction - no new code."""
    out_dir = tmp_path / "ops"
    out_dir.mkdir()

    for exit_code in (0, 1):
        marked = _wo152_call(out_dir, 'mark_in_flight trade_prints "2026-08-04T22:54:01Z"')
        assert marked.returncode == 0, marked.stderr
        assert "in_flight_since_utc" in json.loads((out_dir / "status.json").read_text(encoding="utf-8"))["jobs"]["trade_prints"]

        stamped = _wo152_call(out_dir, f'stamp_status trade_prints {exit_code} "detail" ""')
        assert stamped.returncode == 0, stamped.stderr
        record = json.loads((out_dir / "status.json").read_text(encoding="utf-8"))["jobs"]["trade_prints"]
        assert "in_flight_since_utc" not in record
        assert record["last_exit_code"] == exit_code


def test_wo152_missing_and_empty_status_json_both_write_a_valid_marker(tmp_path):
    """WO-152 Test 3, the two cases with nothing in the existing file to lose."""
    for name, seed in (("missing", None), ("empty", "")):
        out_dir = tmp_path / name
        out_dir.mkdir()
        if seed is not None:
            (out_dir / "status.json").write_text(seed, encoding="utf-8")

        result = _wo152_call(out_dir, 'mark_in_flight book_pulse "2026-08-04T22:54:01Z"')

        assert result.returncode == 0, result.stderr
        payload = json.loads((out_dir / "status.json").read_text(encoding="utf-8"))
        assert payload["jobs"]["book_pulse"]["in_flight_since_utc"] == "2026-08-04T22:54:01Z"
        assert not [p for p in out_dir.iterdir() if p.name.endswith(".tmp")]


def test_wo152_corrupt_status_json_is_left_byte_for_byte_untouched(tmp_path):
    """WO-152 Test 4. Replacing a corrupt file wholesale would destroy every
    OTHER job's stamps; skipping loses only this one job's attribution."""
    out_dir = tmp_path / "ops"
    out_dir.mkdir()
    corrupt = '{"jobs": {"training_harvest": {"runs_total": 34'
    status = out_dir / "status.json"
    status.write_bytes(corrupt.encode("utf-8"))

    result = _wo152_call(out_dir, 'mark_in_flight training_harvest "2026-08-04T22:54:01Z"; echo STILL_ALIVE')

    assert result.returncode == 0, result.stderr
    assert "STILL_ALIVE" in result.stdout
    assert "mark_in_flight" in (result.stdout + result.stderr)
    assert status.read_bytes() == corrupt.encode("utf-8")
    assert not [p for p in out_dir.iterdir() if p.name.endswith(".tmp")]


def test_wo152_every_marked_job_is_marked_and_the_skip_path_is_not(tmp_path):
    """WO-152 Test 5. Asserted against the constant, not a hand-copied list.

    This requires the new MAKER_STUDY_STARTED_AT line to exist, so the
    assertion fails honestly rather than passing vacuously.
    """
    text = _WO152_SCHEDULER.read_text(encoding="utf-8")
    called = set(re.findall(r"^\s*mark_in_flight ([a-z_]+) ", text, flags=re.MULTILINE))

    assert called == _WO152_MARKED_JOBS

    # Each marked job is marked at the point its own *_STARTED_AT stamp is taken.
    for job, variable in (
        ("governance_refresh", "GOVERNANCE_STARTED_AT"),
        ("clv_snapshot", "CLV_STARTED_AT"),
        ("locked_card_refresh", "CARD_STARTED_AT"),
        ("training_harvest", "HARVEST_STARTED_AT"),
        ("maker_study_intraday", "MAKER_STUDY_STARTED_AT"),
        ("trade_prints", "PRINTS_STARTED_AT"),
        ("book_pulse", "BOOK_PULSE_STARTED_AT"),
        ("ledger_anchor", "LEDGER_ANCHOR_STARTED_AT"),
    ):
        assert f'{variable}=$(date -u +%Y-%m-%dT%H:%M:%SZ)\n  mark_in_flight {job} "${variable}"' in text

    # run_book_pulse's disabled branch stamps and returns before the marker: a
    # job that returns early on a skip path never held the loop.
    disabled_branch = text.split('if [ "$BOOK_PULSE_ENABLED" = "0" ]; then', 1)[1].split("fi", 1)[0]
    assert "stamp_status book_pulse 0" in disabled_branch
    assert "mark_in_flight" not in disabled_branch


def test_wo152_mark_in_flight_never_kills_the_scheduler(tmp_path):
    """WO-152 Tests 6-7. Three failure directions, under the production shell family.

    The scheduler runs `set -u` and every `set -e` in it is inside a job
    subshell, never in the main loop - so a bare "$2" on a one-argument call
    would not fail the function, it would TERMINATE THE SCHEDULER.
    """
    out_dir = tmp_path / "ops"
    out_dir.mkdir()
    # A python child that cannot read or write status.json. The WO's registered
    # form of this case is an unwritable OUT_DIR, which is not a failure when
    # the suite runs as root (as it does on the ARM64 gate); a status.json that
    # is a DIRECTORY raises in the same place, uncaught, for every uid.
    unreadable = tmp_path / "unreadable"
    (unreadable / "status.json").mkdir(parents=True)

    cases = {
        "one_argument": (out_dir, "mark_in_flight training_harvest; echo STILL_ALIVE"),
        "empty_second": (out_dir, 'mark_in_flight training_harvest ""; echo STILL_ALIVE'),
        "no_arguments": (out_dir, "mark_in_flight; echo STILL_ALIVE"),
        "failing_python_child": (unreadable, 'mark_in_flight training_harvest "2026-08-04T22:54:01Z"; echo STILL_ALIVE'),
    }

    for shell in ("sh", "bash", "dash"):
        if subprocess.run(["which", shell], capture_output=True, check=False).returncode != 0:
            continue
        for name, (directory, snippet) in cases.items():
            result = _wo152_call(directory, snippet, shell=shell)
            context = f"{shell}/{name}: rc={result.returncode} out={result.stdout!r} err={result.stderr!r}"
            # The function returns 0, the calling shell is STILL RUNNING, and a
            # log line naming mark_in_flight was emitted.
            assert result.returncode == 0, context
            assert "STILL_ALIVE" in result.stdout, context
            assert "mark_in_flight" in (result.stdout + result.stderr), context


def test_wo152_library_only_precedent_is_intact():
    """WO-152 Test 8, in the form its own registered text defines.

    NOTE (build discrepancy, reported for a ruling): the registered assertion is
    a raw `grep -c` of 14 in this file, but the WO's own scheduler-side tests
    above extend the same library-only idiom, so the literal count necessarily
    moves. The registered text defines the 14 as "the count of tests in
    test_polymarket_vps_docker.py that follow the established library-only
    source-and-call pattern" and enumerates them by name, so the enumeration is
    asserted here instead - it is the stable form of the same claim.
    """
    text = Path(__file__).read_text(encoding="utf-8")
    registered = [
        "test_long_scheduler_job_wait_keeps_safety_pulse_live",
        "test_failed_training_harvest_rearms_after_bounded_retry_backoff",
        "test_deploy_forced_governance_refresh_is_not_counted_as_scheduler_overrun",
        "test_ops_scheduler_disabled_seasonal_card_records_intentional_skip",
        "test_ops_scheduler_card_refresh_enabled_by_default_preflights_odds",
        "test_ops_scheduler_rotates_oversized_log",
        "test_ops_scheduler_log_rotation_is_noop_when_small_or_disabled",
        "test_wo117_offset_env_nonnumeric_value_does_not_kill_the_scheduler",
        "test_wo117_window_tolerance_boundary_semantics",
        "test_wo120_corrupt_stamp_keeps_job_schedulable",
        "test_wo120_stamp_status_leaves_no_temp_file_and_valid_json",
        "test_book_pulse_interval_and_timeout_env_clamps",
        "test_book_pulse_overrun_leaves_last_success_empty_with_numeric_duration_and_recovers",
        "test_book_pulse_disabled_stamps_intentional_skip_at_exit_zero",
    ]

    assert len(registered) == 14
    for name in registered:
        assert f"def {name}(" in text, name

    # The 15th tree-wide occurrence is a different test file using the same idiom.
    other = ROOT / "tests" / "polymarket_predictive_engine" / "test_training_harvest.py"
    assert "OPS_SCHEDULER_LIBRARY_ONLY" in other.read_text(encoding="utf-8")
