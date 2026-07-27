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


def test_wo133_refuses_when_the_checkout_and_marker_disagree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "deployed_git_rev").write_text("aaaaaaa\n", encoding="utf-8")
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


def test_wo133_a_failure_past_the_arming_boundary_invokes_rollback(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "rollback_vps_paper_deploy.py").write_text(
        "import sys; print('ROLLBACK-INVOKED')\n", encoding="utf-8"
    )
    script = f"""
    PM_MANUAL_DEPLOY_LIBRARY_ONLY=1 . {SCRIPT}
    REPO_DIR={repo}
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


def test_wo133_never_writes_an_attestation_or_claims_independent_review() -> None:
    body = _script()
    assert "merge-attestation" not in body
    assert '"attestation_verified": True' not in body
    # Path A must stay named as the required route so this never reads as a peer.
    assert "Path A" in body and "REQUIRED" in body
