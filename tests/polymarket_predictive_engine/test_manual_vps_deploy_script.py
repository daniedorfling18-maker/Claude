"""WO-133 — the guarded manual deploy path (Path B).

These tests exist because the failure modes of a deploy script are ORDERING
failures, and ordering is exactly what a reader skims past. Each test names the
consequence of getting the order wrong, so a later edit that reorders the guards
fails here rather than on the VPS.
"""
from __future__ import annotations

import json
import re
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
    # WO-145: authorised_by was a hardcoded owner-authorization CLAIM stamped on
    # every deploy. It is replaced by trigger_mechanism, which records the
    # MECHANISM rather than a claimed identity; a bare invocation with no
    # PM_DEPLOY_TRIGGER_MECHANISM set defaults to "vps_shell" and carries no
    # actor fields at all.
    assert "authorised_by" not in record
    assert record["trigger_mechanism"] == "vps_shell"
    assert "trigger_actor" not in record
    assert "approval_actor" not in record
    assert "no eligible independent reviewer can exist" in record["attestation_unverifiable_reason"]
    assert record["paper_trading_invoked"] is False
    assert record["live_trading_invoked"] is False
    # The recorded order is the order main() actually runs.
    assert record["guard_order"] == [name for name in _guard_order() if name != "write_deploy_record"]


def test_wo145_deploy_record_carries_trigger_mechanism_and_actors_on_workflow_paths(tmp_path: Path) -> None:
    # WO-145 / §145.1(b): trigger_mechanism gains push (and already carried
    # workflow_dispatch); on both workflow-triggered mechanisms the record also
    # carries the real GitHub actor and the environment-approval actor - facts
    # about who did what, never the literal owner-authorization claim this WO
    # exists to remove.
    script = f"""
    set -e
    PM_MANUAL_DEPLOY_LIBRARY_ONLY=1 . {SCRIPT}
    REPO_DIR={tmp_path}
    PM_OUTPUT_ROOT={tmp_path}/outputs
    PM_DEPLOY_TRIGGER_MECHANISM=push
    PM_DEPLOY_TRIGGER_ACTOR=some-actor
    PM_DEPLOY_APPROVAL_ACTOR=some-approver
    write_deploy_record deadbeef
    """
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

    record = json.loads(
        (tmp_path / "outputs" / "performance" / "vps_manual_deploy.json").read_text(encoding="utf-8")
    )
    assert record["trigger_mechanism"] == "push"
    assert record["trigger_actor"] == "some-actor"
    assert record["approval_actor"] == "some-approver"
    assert "authorised_by" not in record


def test_wo145_deploy_record_rejects_an_invalid_trigger_mechanism(tmp_path: Path) -> None:
    script = f"""
    set -e
    PM_MANUAL_DEPLOY_LIBRARY_ONLY=1 . {SCRIPT}
    REPO_DIR={tmp_path}
    PM_OUTPUT_ROOT={tmp_path}/outputs
    PM_DEPLOY_TRIGGER_MECHANISM=laptop_shell
    write_deploy_record deadbeef
    """
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode != 0
    assert "must be one of workflow_dispatch, push, vps_shell" in result.stderr


def test_wo145_target_sha_is_rejected_when_unset_or_empty(tmp_path: Path) -> None:
    # §145.1(a1b): the :99 tip-defaulting fallback is deleted. An unset or empty
    # PM_DEPLOY_TARGET_SHA must be REJECTED, not silently retargeted to whatever
    # origin/main happens to be at that moment - that comparison could never
    # fail, which is byte-for-byte the (5c) defect this deletion exists to close.
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    base = f"""
    set -e
    PM_MANUAL_DEPLOY_LIBRARY_ONLY=1 . {SCRIPT}
    REPO_DIR={repo}
    git() {{ case "$*" in *"rev-parse origin/main"*) echo aaaaaaa;; *fetch*) return 0;; esac; }}
    """
    for unset_form in ("", "PM_DEPLOY_TARGET_SHA=", "PM_DEPLOY_TARGET_SHA='' "):
        script = base + f"\n{unset_form}\nassert_target_is_origin_main\n"
        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        assert result.returncode != 0, unset_form
        assert "PM_DEPLOY_TARGET_SHA is not set" in result.stderr, unset_form

    # A genuinely set value against the same stub still behaves as before.
    script = base + "\nPM_DEPLOY_TARGET_SHA=aaaaaaa assert_target_is_origin_main\n"
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


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


def _declared_options(helper: str) -> tuple[set[str], set[str]]:
    """Every --flag the helper's argparse declares, and the subset it requires."""
    source = (ROOT / "scripts" / helper).read_text(encoding="utf-8")
    declared: set[str] = set()
    required: set[str] = set()
    for call in re.findall(r"add_argument\((.*?)\)\n", source, flags=re.DOTALL):
        flags = re.findall(r"[\"'](--[a-z0-9-]+)[\"']", call)
        if not flags:
            continue
        declared.update(flags)
        if re.search(r"required\s*=\s*True", call):
            required.update(flags)
    return declared, required


def _invocation_flags(helper: str) -> set[str]:
    """The --flags the manual script actually passes to that helper."""
    body = _script()
    marker = f'"$STAGE_DIR/{helper}"'
    assert marker in body, helper
    # The invocation runs from the marker to the first line that is not a
    # continuation - i.e. the first line not ending in a backslash.
    tail = body.split(marker, 1)[1]
    lines: list[str] = []
    for line in tail.splitlines():
        # The `|| fail "..."` clause is not part of the invocation. Its message
        # may legitimately quote other commands' flags (the capacity refusal names
        # `docker builder prune --force`), which must not read as arguments passed.
        if line.lstrip().startswith("||"):
            break
        lines.append(line)
        if not line.rstrip().endswith("\\"):
            break
    return set(re.findall(r"(?<![\w-])(--[a-z0-9-]+)", "\n".join(lines)))


@pytest.mark.parametrize(
    "helper",
    [
        "preflight_vps_capacity.py",
        "validate_dashboard_private_transport.py",
        "update_vps_checkout_preserving_runtime.py",
        "rollback_vps_paper_deploy.py",
    ],
)
def test_wo133_every_helper_invocation_matches_that_helper_s_real_parser(helper: str) -> None:
    """The test that would have caught the first VPS run's real failure.

    Path B was written against INVENTED signatures: `--repo-dir` for a preflight
    whose flag is `--root`, `--target` for an updater whose flag is `--target-ref`,
    `--image-tag` for a rollback whose flag is `--image-backup-tag`, and a
    transport validator called with none of its four required inputs. Four of the
    five staged helpers would have failed, INCLUDING the rollback utility - so a
    failure past the arming boundary would have found its recovery path broken too.

    Every earlier test asserted the ORDER of the guards, which was right, and none
    asserted that a guard could run at all. This one reads each helper's own
    argparse and refuses any flag it does not declare or requires and never gets.
    """
    declared, required = _declared_options(helper)
    assert declared, f"could not read any options from {helper}"
    passed = _invocation_flags(helper)

    unknown = passed - declared
    assert not unknown, f"{helper} does not accept {sorted(unknown)}"
    missing = required - passed
    assert not missing, f"{helper} requires {sorted(missing)} and the invocation omits them"


def test_wo133_the_health_gate_is_driven_by_its_documented_env_contract() -> None:
    # check_polymarket_vps_paper.sh takes no flags; it reads PM_VPS_REPO_DIR.
    health = (ROOT / "scripts" / "check_polymarket_vps_paper.sh").read_text(encoding="utf-8")
    assert 'REPO_DIR="${PM_VPS_REPO_DIR:-' in health
    assert 'PM_VPS_REPO_DIR="$REPO_DIR" bash "$STAGE_DIR/check_polymarket_vps_paper.sh"' in _script()


def test_wo133_rollback_is_given_every_helper_it_needs_from_the_target_revision() -> None:
    # rollback_vps_paper_deploy.py shells out to a telemetry writer and a transport
    # validator by path. Handing it paths inside the CHECKOUT would point it at the
    # half-updated tree it is trying to undo, so both must come from the staging
    # directory - and both must therefore be staged.
    body = _script()
    for helper in ("write_vps_telemetry_manifest.py", "validate_dashboard_private_transport.py"):
        assert f"    {helper} \\" in body or f"    {helper}\n" in body, f"{helper} is not staged"
    assert '--telemetry-writer "$STAGE_DIR/write_vps_telemetry_manifest.py"' in body
    assert '--transport-validator "$STAGE_DIR/validate_dashboard_private_transport.py"' in body


def test_wo133_capacity_is_measured_against_the_target_compose_not_the_running_one() -> None:
    # A deploy that adds a service or raises a memory reservation has to be refused
    # BEFORE it is built. Measuring the running stack's compose file cannot do that.
    body = _script()
    assert 'show "$target_sha:$COMPOSE_FILE" > "$STAGE_DIR/compose.yml"' in body
    assert '--compose "$STAGE_DIR/compose.yml"' in body


def test_wo133_transport_guard_writes_no_artifact_into_the_repository(tmp_path: Path) -> None:
    # Guard 4 runs before the arming boundary, so its report goes to the staging
    # directory. Writing outputs/performance/dashboard_private_transport.json here
    # would mutate the host during a guard that is allowed to refuse for free.
    body = _script()
    transport = body.split("assert_private_transport() {", 1)[1].split("\n}", 1)[0]
    assert '--output "$transport_tmp/dashboard_private_transport.json"' in transport
    assert "outputs/performance" not in transport


def _compose_invocation(anchor: str) -> str:
    """The `docker compose ...` call inside a named function, continuations joined."""
    body = _script().split(f"{anchor}() {{", 1)[1].split("\n}", 1)[0]
    joined: list[str] = []
    buffer = ""
    for line in body.splitlines():
        buffer += " " + line.strip().rstrip("\\")
        if not line.rstrip().endswith("\\"):
            joined.append(buffer.strip())
            buffer = ""
    return "\n".join(joined)


def test_wo133_acceptance_never_passes_no_build_to_compose_run() -> None:
    """Regression for the 2026-07-28 deploy failure, observed on the VPS.

        unknown flag: --no-build
        ERROR: deploy acceptance failed

    `docker compose run` has no `--no-build` - it is an `up` flag - so acceptance
    aborted PAST the arming boundary, taking a healthy stack down and forcing a
    rollback. This is pinned to the observed error rather than to a belief about
    Docker's CLI, because a belief about Docker's CLI is what caused it.

    Dropping the flag is behaviour-preserving: `run` does not build by default,
    and recreate_stack builds the image two steps earlier.
    """
    acceptance = _compose_invocation("run_deploy_acceptance")
    assert "--profile deploy-acceptance run" in acceptance
    assert "--no-build" not in acceptance

    # `up` DOES accept it, and the scheduler restart must keep it - restarting the
    # scheduler must never trigger a rebuild of the image just deployed.
    restart = _compose_invocation("restart_scheduler")
    assert "up -d --no-build vps-ops-scheduler" in restart


def test_wo133_acceptance_is_time_bounded_past_the_arming_boundary() -> None:
    # Acceptance runs with the scheduler stopped, after arming. A hang there holds
    # the deploy open indefinitely on a quiesced stack, so it is bounded like
    # Path A's (workflow :706).
    acceptance = _compose_invocation("run_deploy_acceptance")
    assert "timeout --signal=TERM --kill-after=30s 1500" in acceptance


def _effective_probe_span_seconds() -> float:
    """The retry span the rollback ACTUALLY gets, after its own clamps.

    Requesting a probe window is not the same as receiving one:
    `_verify_private_dashboard_transport` clamps BOTH inputs. So this reads the
    clamps out of the helper and applies them to what the script asks for. The
    span is (attempts - 1) intervals, since the last sleep buys no further retry.
    """
    helper = (ROOT / "scripts" / "rollback_vps_paper_deploy.py").read_text(encoding="utf-8")
    attempts_cap = int(
        re.search(r"range\(max\(1,\s*min\((\d+),\s*probe_attempts\)\)\)", helper).group(1)
    )
    interval_cap = float(
        re.search(r"min\(([\d.]+),\s*probe_interval_seconds\)", helper).group(1)
    )

    body = _script()
    asked_attempts = int(re.search(r"--https-probe-attempts (\d+)", body).group(1))
    asked_interval = float(re.search(r"--https-probe-interval-seconds ([\d.]+)", body).group(1))

    return (min(attempts_cap, asked_attempts) - 1) * min(interval_cap, asked_interval)


def test_wo133_rollback_probe_window_is_at_least_sixty_effective_seconds() -> None:
    """Regression for the 2026-07-28 rollback false alarm, and for my fix to it.

    The rollback restored everything correctly and still reported FAIL /
    MANUAL_INTERVENTION_REQUIRED because a just-recreated dashboard behind
    Tailscale Serve had not answered inside the default window. A cold start is
    not a failed rollback.

    My first fix asked for 30 attempts x 2s and claimed 60 seconds. It delivered
    about 20: the helper clamps attempts at 10 and the interval at 10.0s, so the
    request was silently truncated. Codex caught that on #377.

    This test therefore measures the EFFECTIVE span through the helper's own
    clamps instead of asserting the flag values back at me - which is precisely
    what the version it replaces did wrong, and the same shape as the marker-path
    tests that could never fail.
    """
    assert _effective_probe_span_seconds() >= 60.0

    declared, _ = _declared_options("rollback_vps_paper_deploy.py")
    for flag in ("--https-probe-attempts", "--https-probe-interval-seconds"):
        assert flag in declared, flag


def test_wo133_probe_request_stays_inside_the_helpers_own_bounds() -> None:
    # The span must be bought within the shared helper's contract. Exceeding a cap
    # would mean the script asks for something it cannot get - which reads as a
    # 60s window in review while behaving as a 20s one in production, and would
    # otherwise require changing a file Path A also depends on.
    helper = (ROOT / "scripts" / "rollback_vps_paper_deploy.py").read_text(encoding="utf-8")
    attempts_cap = int(
        re.search(r"range\(max\(1,\s*min\((\d+),\s*probe_attempts\)\)\)", helper).group(1)
    )
    interval_cap = float(
        re.search(r"min\(([\d.]+),\s*probe_interval_seconds\)", helper).group(1)
    )
    body = _script()
    assert int(re.search(r"--https-probe-attempts (\d+)", body).group(1)) <= attempts_cap
    assert float(re.search(r"--https-probe-interval-seconds ([\d.]+)", body).group(1)) <= interval_cap


def test_wo133_never_writes_an_attestation_or_claims_independent_review() -> None:
    body = _script()
    assert "merge-attestation" not in body
    assert '"attestation_verified": True' not in body
    # Path A must stay named as the required route so this never reads as a peer.
    assert "Path A" in body and "REQUIRED" in body
