"""WO-133 — the guarded manual deploy path (Path B).

These tests exist because the failure modes of a deploy script are ORDERING
failures, and ordering is exactly what a reader skims past. Each test names the
consequence of getting the order wrong, so a later edit that reorders the guards
fails here rather than on the VPS.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "deploy_vps_paper_manual.sh"


def _script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _guard_order() -> list[str]:
    """The call order inside main(), which is the contract these tests defend."""
    body = _script().split("\nmain() {", 1)[1].split("\n}\n", 1)[0]
    calls: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        for name in (
            "assert_target_is_origin_main",
            "stage_target_scripts",
            "assert_checkout_matches_marker",
            "run_capacity_preflight",
            "assert_private_transport",
            "arm_rollback",
            "update_checkout",
            "write_deploy_markers",
            "recreate_stack",
            "run_deploy_acceptance",
            "run_health_gate",
            "restart_scheduler",
            "write_deploy_record",
        ):
            if stripped.startswith(name) or stripped.startswith(f'target_sha="$({name}'):
                calls.append(name)
    return calls


def test_wo133_script_is_syntactically_valid() -> None:
    assert subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True).returncode == 0


def test_wo133_transport_proof_runs_before_the_stack_is_quiesced() -> None:
    # Proving private transport AFTER quiescing would mean tearing down a healthy
    # stack to discover the new one must not be started. Public exposure is the
    # one thing this gate exists to prevent.
    order = _guard_order()
    assert order.index("assert_private_transport") < order.index("arm_rollback")
    assert order.index("assert_private_transport") < order.index("recreate_stack")


def test_wo133_markers_are_written_before_containers_are_recreated() -> None:
    # The ordering defect caught in review of the earlier draft: recreate first
    # and a failure in between leaves the stack running new code while every
    # marker still claims the old SHA - the deployed-state report is then a lie
    # exactly when an operator most needs it.
    order = _guard_order()
    assert order.index("write_deploy_markers") < order.index("recreate_stack")


def test_wo133_nothing_mutates_the_host_before_the_arming_boundary() -> None:
    # Every refusal-capable guard must run while the host is still untouched, so
    # a refused deploy costs nothing and needs no rollback.
    order = _guard_order()
    arming = order.index("arm_rollback")
    for guard in (
        "assert_target_is_origin_main",
        "assert_checkout_matches_marker",
        "run_capacity_preflight",
        "assert_private_transport",
    ):
        assert order.index(guard) < arming, guard
    for mutation in ("update_checkout", "write_deploy_markers", "recreate_stack"):
        assert order.index(mutation) > arming, mutation


def test_wo133_acceptance_runs_with_the_scheduler_stopped_then_restarts_it() -> None:
    # A running scheduler writes the very artifacts acceptance is judging.
    body = _script()
    acceptance = body.split("run_deploy_acceptance() {", 1)[1].split("\n}", 1)[0]
    assert "stop vps-ops-scheduler" in acceptance
    assert acceptance.index("stop vps-ops-scheduler") < acceptance.index("deploy-acceptance")
    order = _guard_order()
    assert order.index("run_deploy_acceptance") < order.index("restart_scheduler")
    assert order.index("run_health_gate") < order.index("restart_scheduler")


def test_wo133_refuses_a_target_that_is_not_the_origin_main_tip(tmp_path: Path) -> None:
    # This refusal is what stops Path B becoming a way to ship unreviewed code.
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    script = f"""
    set -e
    PM_MANUAL_DEPLOY_LIBRARY_ONLY=1 . {SCRIPT}
    REPO_DIR={repo}
    git() {{ case "$*" in *"rev-parse origin/main"*) echo aaaaaaa;; *fetch*) return 0;; esac; }}
    PM_DEPLOY_TARGET_SHA=bbbbbbb assert_target_is_origin_main
    """
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode != 0
    assert "is not the current origin/main tip" in result.stderr


MARKER_RELATIVE = "outputs/performance/deployed_git_rev"


def test_wo133_refuses_when_the_checkout_and_marker_disagree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "outputs" / "performance").mkdir(parents=True)
    (repo / MARKER_RELATIVE).write_text("aaaaaaa\n", encoding="utf-8")
    script = f"""
    set -e
    PM_MANUAL_DEPLOY_LIBRARY_ONLY=1 . {SCRIPT}
    REPO_DIR={repo}
    git() {{ echo bbbbbbb; }}
    assert_checkout_matches_marker
    """
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode != 0
    assert "does not match deployed marker" in result.stderr


def test_wo133_marker_path_matches_every_other_consumer() -> None:
    # This test exists because the first VPS run of Path B refused at guard 2 with
    # "deployed marker /home/opc/Claude/deployed_git_rev is missing". The marker was
    # not missing - the script was looking in the wrong place, and the tests around
    # it built their fixtures at the same wrong path, so they could never catch it.
    # The path is a shared contract, so it is asserted against its OTHER holders
    # rather than restated here as another independent guess.
    for path, needle in (
        (".github/workflows/deploy-polymarket-vps-paper.yml", f"mv \"$marker_tmp\" {MARKER_RELATIVE}"),
        ("scripts/bootstrap_polymarket_vps_paper.sh", f'mv "$marker_tmp" {MARKER_RELATIVE}'),
        ("scripts/rollback_vps_paper_deploy.py", '"outputs" / "performance" / "deployed_git_rev"'),
        ("scripts/write_vps_telemetry_manifest.py", '"outputs" / "performance" / "deployed_git_rev"'),
    ):
        assert needle in (ROOT / path).read_text(encoding="utf-8"), path

    body = _script()
    assert f'DEPLOYED_MARKER_RELATIVE="{MARKER_RELATIVE}"' in body
    # And no reader may reconstruct the path by hand alongside the shared helper.
    assert '"$REPO_DIR/deployed_git_rev"' not in body


def test_wo133_a_missing_marker_refuses_before_anything_is_touched(tmp_path: Path) -> None:
    # Refusing is correct when the marker is genuinely absent - but it must refuse
    # naming the path the rest of the system uses, or the operator debugs the wrong
    # thing (which is exactly what happened).
    repo = tmp_path / "repo"
    repo.mkdir()
    script = f"""
    set -e
    PM_MANUAL_DEPLOY_LIBRARY_ONLY=1 . {SCRIPT}
    REPO_DIR={repo}
    assert_checkout_matches_marker
    """
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode != 0
    assert f"{repo}/{MARKER_RELATIVE} is missing" in result.stderr


def _repo_at_guard_two(tmp_path: Path, sha: str = "aaaaaaa") -> Path:
    repo = tmp_path / "repo"
    (repo / "outputs" / "performance").mkdir(parents=True)
    (repo / MARKER_RELATIVE).write_text(f"{sha}\n", encoding="utf-8")
    (repo / ".env").write_text("PM_VPS_DEPLOYED_SHA=old\n", encoding="utf-8")
    return repo


def _run_guard_two(repo: Path, sha: str = "aaaaaaa") -> subprocess.CompletedProcess[str]:
    script = f"""
    set -e
    PM_MANUAL_DEPLOY_LIBRARY_ONLY=1 . {SCRIPT}
    REPO_DIR={repo}
    git() {{ echo {sha}; }}
    assert_checkout_matches_marker
    """
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def test_wo133_guard_two_passes_on_an_agreeing_writable_checkout(tmp_path: Path) -> None:
    result = _run_guard_two(_repo_at_guard_two(tmp_path))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("break_it", "expected"),
    [
        (lambda repo: (repo / MARKER_RELATIVE).chmod(0o444), "marker"),
        (lambda repo: (repo / "outputs" / "performance").chmod(0o555), "performance"),
        (lambda repo: (repo / ".env").chmod(0o444), ".env"),
    ],
    ids=["readonly_marker", "readonly_output_dir", "readonly_env"],
)
def test_wo133_unwritable_deploy_targets_refuse_before_arming(
    tmp_path: Path, break_it, expected: str
) -> None:
    # Both post-arming writes are proven at guard 2. Without this the deploy arms,
    # quiesces a healthy stack, fails on a permission error and rolls back - paying
    # a full outage for something knowable while the host was still untouched. The
    # containers write under outputs/ as root, so this is reachable in production.
    if __import__("os").geteuid() == 0:
        pytest.skip("root ignores the write bit, so the refusal cannot be observed")
    repo = _repo_at_guard_two(tmp_path)
    break_it(repo)
    try:
        result = _run_guard_two(repo)
        assert result.returncode != 0
        assert "is not writable by" in result.stderr
        assert expected in result.stderr
        assert "after the arming boundary" in result.stderr
    finally:
        (repo / "outputs" / "performance").chmod(0o755)


def test_wo133_marker_write_lands_atomically_at_the_shared_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "outputs" / "performance").mkdir(parents=True)
    (repo / ".env").write_text("PM_VPS_DEPLOYED_SHA=old\nOTHER=keep\n", encoding="utf-8")
    script = f"""
    set -e
    PM_MANUAL_DEPLOY_LIBRARY_ONLY=1 . {SCRIPT}
    REPO_DIR={repo}
    write_deploy_markers deadbeef
    """
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (repo / MARKER_RELATIVE).read_text(encoding="utf-8") == "deadbeef\n"
    # No temp file survives, in either the marker or the .env write.
    assert not list((repo / "outputs" / "performance").glob("*.tmp*"))
    assert not list(repo.glob(".env.tmp*"))


def test_wo133_env_write_owns_only_the_deployed_sha_key(tmp_path: Path) -> None:
    # PM_IMAGE_BUILD_SHA is DERIVED by compose as ${PM_VPS_DEPLOYED_SHA:-unknown}
    # for every service. Writing it into .env plants a value no container reads,
    # and the next host-side reader that trusts .env - the pattern
    # check_polymarket_vps_paper.sh already uses for PM_VPS_DEPLOYED_SHA - would
    # then compare the deployed SHA against itself, which is the vacuous
    # deployment-alignment check (TS-2) reintroduced by the back door.
    compose = (ROOT / "docker-compose.vps-paper.yml").read_text(encoding="utf-8")
    assert "PM_IMAGE_BUILD_SHA: ${PM_VPS_DEPLOYED_SHA:-unknown}" in compose
    # The key may be DISCUSSED in a comment - it must never appear in executable code.
    for line in _script().splitlines():
        if "PM_IMAGE_BUILD_SHA" in line:
            assert line.lstrip().startswith("#"), line

    repo = tmp_path / "repo"
    (repo / "outputs" / "performance").mkdir(parents=True)
    (repo / ".env").write_text(
        "OTHER=keep\nPM_VPS_DEPLOYED_SHA=old\nTHE_ODDS_API_KEY=x\n", encoding="utf-8"
    )
    script = f"""
    set -e
    PM_MANUAL_DEPLOY_LIBRARY_ONLY=1 . {SCRIPT}
    REPO_DIR={repo}
    write_deploy_markers deadbeef
    """
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

    env_text = (repo / ".env").read_text(encoding="utf-8")
    assert "PM_VPS_DEPLOYED_SHA=deadbeef" in env_text
    assert "PM_IMAGE_BUILD_SHA" not in env_text
    # Unrelated keys - secrets included - survive the rewrite untouched.
    assert "OTHER=keep" in env_text
    assert "THE_ODDS_API_KEY=x" in env_text
    assert (repo / ".env").stat().st_mode & 0o777 == 0o600


def test_wo133_a_failure_past_the_arming_boundary_invokes_rollback(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    stage = tmp_path / "stage"
    stage.mkdir()
    # Rollback must also come from the TARGET revision: rolling back with the old
    # checkout's rollback utility is the one moment you cannot afford a stale tool.
    (stage / "rollback_vps_paper_deploy.py").write_text(
        "import sys; print('ROLLBACK-INVOKED')\n", encoding="utf-8"
    )
    script = f"""
    PM_MANUAL_DEPLOY_LIBRARY_ONLY=1 . {SCRIPT}
    REPO_DIR={repo}
    STAGE_DIR={stage}
    ROLLBACK_ARMED=true
    ORIGINAL_HEAD=abc123
    ROLLBACK_DIR={tmp_path}
    rollback_if_armed 1
    """
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode == 1  # a rolled-back deploy is still a failed deploy
    assert "ROLLBACK-INVOKED" in result.stdout


def test_wo133_a_successful_run_never_invokes_rollback(tmp_path: Path) -> None:
    script = f"""
    PM_MANUAL_DEPLOY_LIBRARY_ONLY=1 . {SCRIPT}
    ROLLBACK_ARMED=true
    rollback_if_armed 0
    """
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0
    assert "restoring" not in result.stdout


def test_wo133_deploy_record_reports_the_unprovable_step_as_unproven(tmp_path: Path) -> None:
    # The whole point of Path B's honesty contract: it cannot bind the SHA to an
    # independent review, so it says so in the artifact rather than implying it did.
    script = f"""
    set -e
    PM_MANUAL_DEPLOY_LIBRARY_ONLY=1 . {SCRIPT}
    REPO_DIR={tmp_path}
    PM_OUTPUT_ROOT={tmp_path}/outputs
    write_deploy_record deadbeef
    """
    assert subprocess.run(["bash", "-c", script], capture_output=True, text=True).returncode == 0

    record = json.loads(
        (tmp_path / "outputs" / "performance" / "vps_manual_deploy.json").read_text(encoding="utf-8")
    )
    assert record["attestation_verified"] is False
    assert record["deployed_sha"] == "deadbeef"
    assert record["authorised_by"] == "owner"
    assert "GitHub API credentials" in record["attestation_unverifiable_reason"]
    assert record["paper_trading_invoked"] is False
    assert record["live_trading_invoked"] is False
    # The recorded order is the order main() actually runs.
    assert record["guard_order"] == [name for name in _guard_order() if name != "write_deploy_record"]


def test_wo133_guards_are_read_from_the_target_revision_not_the_old_checkout() -> None:
    # The checkout being deployed FROM is by definition out of date - that is why
    # a deploy is running. Reading the guards out of it would run the PREVIOUS
    # revision's preflight, transport proof, health gate and rollback against the
    # new code, silently skipping any hardening the target added.
    body = _script()
    order = _guard_order()
    assert order.index("stage_target_scripts") < order.index("run_capacity_preflight")
    assert order.index("stage_target_scripts") < order.index("assert_private_transport")
    for helper in (
        "preflight_vps_capacity.py",
        "validate_dashboard_private_transport.py",
        "update_vps_checkout_preserving_runtime.py",
        "rollback_vps_paper_deploy.py",
        "check_polymarket_vps_paper.sh",
    ):
        assert f'"$STAGE_DIR/{helper}"' in body, helper
        assert f'"$REPO_DIR/scripts/{helper}"' not in body, helper


def test_wo133_never_writes_an_attestation_or_claims_independent_review() -> None:
    body = _script()
    assert "merge-attestation" not in body
    assert '"attestation_verified": True' not in body
    # Path A must stay named as the required route so this never reads as a peer.
    assert "Path A" in body and "REQUIRED" in body
