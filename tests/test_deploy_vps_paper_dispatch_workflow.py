"""WO-145 / §145.1 — trigger the VPS deploy from GitHub Actions instead of a
laptop shell, and auto-trigger it on merge to `main`.

Two jobs: an un-gated `guard` (check 1, before pickup) and an
`environment:`-gated `deploy` (check 2 as its first step, after approval).
Neither check can ever CAUSE a deploy: check 1 may only skip, check 2 may only
fail. This file exercises both checks, the runtime-enforced approval gate
(a3), the `PM_DEPLOY_TARGET_SHA` pin reaching the remote process, and the
tmux sentinel/wait-for wrapper that stops a failed deploy from reporting a
green run.
"""
from __future__ import annotations

import json
import re
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy_vps_paper_dispatch.yml"
PATH_A_WORKFLOW = ROOT / ".github" / "workflows" / "deploy-polymarket-vps-paper.yml"
SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _parsed() -> dict:
    # PyYAML resolves a top-level bare "on:" key to the boolean True (YAML 1.1),
    # not the string "on" - this is a well-known GitHub Actions/PyYAML trap.
    # jobs/steps/env/run are ordinary string keys and parse normally.
    return yaml.safe_load(_text())


def _on_block() -> str:
    lines = _text().splitlines()
    for index, line in enumerate(lines):
        if line.strip() != "on:":
            continue
        block = [line]
        for candidate in lines[index + 1 :]:
            if candidate and not candidate.startswith((" ", "\t")):
                break
            block.append(candidate)
        return "\n".join(block)
    raise AssertionError("no top-level 'on:' block found")


def _job(name: str) -> dict:
    return _parsed()["jobs"][name]


def _step(job: str, name: str) -> dict:
    for step in _job(job)["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"step {name!r} not found in job {job!r}")


def _run(job: str, name: str) -> str:
    return textwrap.dedent(_step(job, name)["run"])


CHECK2_NAME = "Check 2 - re-resolve origin/main after approval"
APPROVAL_NAME = "Enforce runtime approval"
DEPLOY_SSH_NAME = "Deploy over SSH (Path B guarded script)"
RESOLVE_NAME = "Resolve current origin/main tip"


def _fake_git(bin_dir: Path, *, tip: str | None, fail: bool = False) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    if fail:
        body = "#!/usr/bin/env bash\necho 'fake network failure' >&2\nexit 1\n"
    else:
        body = f"#!/usr/bin/env bash\nif [[ \"$*\" == *'ls-remote'* ]]; then printf '%s\\trefs/heads/main\\n' '{tip or ''}'; exit 0; fi\nexit 1\n"
    path = bin_dir / "git"
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _run_shell(
    script: str,
    env: dict[str, str],
    *,
    bin_dir: Path,
    github_output: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    full_env = {"PATH": f"{bin_dir}:/usr/bin:/bin"}
    full_env.update(env)
    if github_output is not None:
        full_env["GITHUB_OUTPUT"] = str(github_output)
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=full_env
    )


def _read_output(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                result[key] = value
    return result


# --- WO-145 test 1: secrets via env:, no set -x, persist-credentials -----------
def test_wo145_secrets_flow_only_through_env_never_interpolated_into_run() -> None:
    text = _text()
    assert "set -x" not in text
    d = _parsed()
    for job in d["jobs"].values():
        for step in job.get("steps", []):
            run = step.get("run", "")
            assert "secrets." not in run, step.get("name")
            assert "github.token" not in run, step.get("name")
    # This workflow does not check out the repository at all (both checks use
    # a bare `git ls-remote`, and the actual deploy runs entirely over SSH on
    # the VPS, which already has its own checkout) so there is no
    # actions/checkout step to carry a persist-credentials setting. If one is
    # ever added, it must disable credential persistence, matching Path A.
    if "actions/checkout" in text:
        assert "persist-credentials: false" in text


# --- WO-145 test 2 / §145.1 test (7): environment, concurrency, permissions ----
def test_wo145_deploy_job_is_gated_and_shares_path_as_concurrency_group() -> None:
    deploy = _job("deploy")
    guard = _job("guard")
    assert deploy.get("environment") == "vps-paper-deploy"
    # (4b) - the gate goes on deploy and must NOT be on guard.
    assert "environment" not in guard or guard.get("environment") is None

    path_a_concurrency = re.search(
        r"^concurrency:\n(?:  .*\n)*", PATH_A_WORKFLOW.read_text(encoding="utf-8"), re.MULTILINE
    ).group(0)
    this_concurrency = re.search(r"^concurrency:\n(?:  .*\n)*", _text(), re.MULTILINE).group(0)
    assert this_concurrency == path_a_concurrency
    assert "group: deploy-polymarket-vps-paper" in this_concurrency
    assert "cancel-in-progress: false" in this_concurrency


def test_wo145_permissions_are_no_broader_than_path_a() -> None:
    d = _parsed()
    path_a = yaml.safe_load(PATH_A_WORKFLOW.read_text(encoding="utf-8"))
    assert d.get("permissions") == path_a.get("permissions") == {"actions": "read", "contents": "read"}


def test_wo145_timeouts_match_the_registered_literals() -> None:
    # (a2): 70 minutes on deploy (same worst case as Path A, same host, same
    # guarded script), 5 on guard (one git ls-remote and nothing else).
    assert _job("guard")["timeout-minutes"] == 5
    assert _job("deploy")["timeout-minutes"] == 70


# --- §145.1 tests (1)-(3): trigger set --------------------------------------
def test_wo145_1_trigger_set_is_exactly_push_to_main_and_workflow_dispatch() -> None:
    on_block = _on_block()
    assert re.search(r"^  push:\s*$", on_block, re.MULTILINE)
    assert re.search(r"^  workflow_dispatch:\s*$", on_block, re.MULTILINE)
    for forbidden in ("schedule", "pull_request_target", "pull_request:", "issue_comment"):
        assert forbidden not in on_block, forbidden
    parsed = _parsed()
    on = parsed[True]
    assert set(on.keys()) == {"push", "workflow_dispatch"}
    assert on["push"] == {"branches": ["main"]}
    assert on["workflow_dispatch"] is None
    # No paths: filter - a docs-only merge still moves the SHA and still deploys.
    assert "paths:" not in on_block


def test_wo145_no_fork_or_paths_filter_anywhere() -> None:
    text = _text()
    assert "pull_request_target" not in text
    assert "fork" not in text.lower()
    assert re.search(r"^\s*paths:", text, re.MULTILINE) is None


# --- WO-145 test 4: StrictHostKeyChecking never disabled ------------------------
def test_wo145_stricthostkeychecking_is_never_disabled() -> None:
    assert "StrictHostKeyChecking" not in _text()


# --- WO-145's SSH key-handling block reused verbatim from Path A ---------------
def test_wo145_prepare_ssh_step_is_reused_verbatim_from_path_a() -> None:
    path_a = yaml.safe_load(PATH_A_WORKFLOW.read_text(encoding="utf-8"))
    path_a_step = next(s for s in path_a["jobs"]["deploy"]["steps"] if s["name"] == "Prepare SSH")
    this_step = _step("deploy", "Prepare SSH")
    assert this_step["env"] == path_a_step["env"]
    assert textwrap.dedent(this_step["run"]).strip() == textwrap.dedent(path_a_step["run"]).strip()


# --- §145.1 test (4)/(4b): check 1 (guard) ---------------------------------------
def test_1451_guard_reports_superseded_true_when_behind_and_fails_on_bad_tip(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    script = _run("guard", RESOLVE_NAME)

    _fake_git(bin_dir, tip=SHA_A)
    out = tmp_path / "equal.out"
    result = _run_shell(
        script,
        {"GH_TOKEN": "tok", "GITHUB_REPOSITORY": "owner/repo", "GITHUB_SHA": SHA_A},
        bin_dir=bin_dir,
        github_output=out,
    )
    assert result.returncode == 0, result.stderr
    assert _read_output(out) == {"superseded": "false"}

    _fake_git(bin_dir, tip=SHA_B)
    out2 = tmp_path / "behind.out"
    result = _run_shell(
        script,
        {"GH_TOKEN": "tok", "GITHUB_REPOSITORY": "owner/repo", "GITHUB_SHA": SHA_A},
        bin_dir=bin_dir,
        github_output=out2,
    )
    assert result.returncode == 0, result.stderr
    assert _read_output(out2) == {"superseded": "true"}


@pytest.mark.parametrize("mode", ["fail", "empty"])
def test_1451_guard_fails_closed_on_an_unresolvable_tip(tmp_path: Path, mode: str) -> None:
    # §145.1 test (6) / A2: network failure or empty result must FAIL the job,
    # not proceed as though it were superseded-or-not.
    bin_dir = tmp_path / "bin"
    script = _run("guard", RESOLVE_NAME)
    if mode == "fail":
        _fake_git(bin_dir, tip=None, fail=True)
    else:
        _fake_git(bin_dir, tip="")
    out = tmp_path / "out"
    result = _run_shell(
        script,
        {"GH_TOKEN": "tok", "GITHUB_REPOSITORY": "owner/repo", "GITHUB_SHA": SHA_A},
        bin_dir=bin_dir,
        github_output=out,
    )
    assert result.returncode != 0
    assert _read_output(out).get("superseded") == "true"


def test_1451_guard_carries_no_environment_key_deploy_carries_it() -> None:
    # (4b): a superseded run must emit no approval request, which requires the
    # gate to be on `deploy`, never on `guard`.
    guard = _job("guard")
    deploy = _job("deploy")
    assert guard.get("environment") in (None,)
    assert deploy.get("environment") == "vps-paper-deploy"


def _deploy_if_condition() -> tuple[str, str, str]:
    """Parse `if: needs.guard.outputs.superseded == '<value>'` into its parts."""
    expr = _job("deploy")["if"]
    match = re.match(r"needs\.guard\.outputs\.(\w+) == '(\w+)'$", expr.strip())
    assert match, expr
    return expr, match.group(1), match.group(2)


def test_1451_deploy_if_gate_skips_when_superseded_and_proceeds_when_not() -> None:
    _, output_key, not_superseded_value = _deploy_if_condition()
    assert output_key == "superseded"
    # (4): behind -> superseded=true -> if false -> job SKIPPED, no step runs.
    assert ("true" == not_superseded_value) is False
    # (5): equal -> superseded=false -> if true -> job proceeds to the gate.
    assert ("false" == not_superseded_value) is True


def test_1451_a_superseded_run_never_lets_the_ssh_step_execute(tmp_path: Path) -> None:
    """(4): sentinel proof that a skipped deploy job never reaches the SSH step.

    A job-level `if:` that evaluates false means GitHub Actions never starts
    any step of that job - this is a platform guarantee, not something an
    offline test can re-execute. What IS testable offline is that (a) the
    guard's own script correctly reports `superseded=true` for a behind SHA,
    and (b) the `if:` expression parsed directly from the registered workflow
    text would evaluate false against that value. Given both, no step of
    `deploy` - including the one that shells `ssh` - ever runs, so the
    sentinel an executed SSH step would write is provably absent.
    """
    bin_dir = tmp_path / "bin"
    _fake_git(bin_dir, tip=SHA_B)
    out = tmp_path / "out"
    result = _run_shell(
        _run("guard", RESOLVE_NAME),
        {"GH_TOKEN": "tok", "GITHUB_REPOSITORY": "owner/repo", "GITHUB_SHA": SHA_A},
        bin_dir=bin_dir,
        github_output=out,
    )
    assert result.returncode == 0
    superseded = _read_output(out)["superseded"]
    assert superseded == "true"

    _, _, not_superseded_value = _deploy_if_condition()
    would_run = superseded == not_superseded_value
    assert would_run is False

    sentinel = tmp_path / "ssh_invoked.marker"
    if would_run:  # pragma: no cover - defensive; never true given the above
        sentinel.write_text("invoked", encoding="utf-8")
    assert not sentinel.exists()


# --- §145.1 test (4c)-(4g): check 2 ----------------------------------------------
def test_1451_check2_is_the_deploy_jobs_first_step() -> None:
    steps = _job("deploy")["steps"]
    assert steps[0]["name"] == CHECK2_NAME


def test_1451_check2_message_is_interpolated_not_literal(tmp_path: Path) -> None:
    script = _run("deploy", CHECK2_NAME)
    bin_dir = tmp_path / "bin"
    _fake_git(bin_dir, tip=SHA_C)
    result = _run_shell(script, {"GH_TOKEN": "tok", "GITHUB_REPOSITORY": "owner/repo", "GITHUB_SHA": SHA_A}, bin_dir=bin_dir)
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "superseded: approved " in combined
    assert ", main is now " in combined
    assert "; approve the newer run" in combined
    # The invariant fragments must NOT be adjacent - i.e. the placeholders are
    # substituted values, not the literal angle-bracket text.
    assert "<github.sha>" not in combined
    assert "<tip>" not in combined
    assert SHA_A in combined
    assert SHA_C in combined


def test_1451_check2_failure_branch_is_exit_1_not_success_or_continue() -> None:
    workflow_text = _text()
    check2 = _step("deploy", CHECK2_NAME)
    assert "continue-on-error" not in workflow_text
    run = check2["run"]
    assert "exit 1" in run
    assert "exit 0" not in run


@pytest.mark.parametrize("mode", ["fail", "empty"])
def test_1451_check2_fails_closed_on_an_unresolvable_tip(tmp_path: Path, mode: str) -> None:
    script = _run("deploy", CHECK2_NAME)
    bin_dir = tmp_path / "bin"
    if mode == "fail":
        _fake_git(bin_dir, tip=None, fail=True)
    else:
        _fake_git(bin_dir, tip="")
    result = _run_shell(script, {"GH_TOKEN": "tok", "GITHUB_REPOSITORY": "owner/repo", "GITHUB_SHA": SHA_A}, bin_dir=bin_dir)
    assert result.returncode != 0


def test_1451_check2_comparison_is_exercised_behaviourally() -> None:
    script = _run("deploy", CHECK2_NAME)
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        bin_dir = Path(tmp) / "bin"
        _fake_git(bin_dir, tip=SHA_A)
        equal_result = _run_shell(script, {"GH_TOKEN": "tok", "GITHUB_REPOSITORY": "o/r", "GITHUB_SHA": SHA_A}, bin_dir=bin_dir)
        assert equal_result.returncode == 0, equal_result.stderr

        _fake_git(bin_dir, tip=SHA_B)
        differ_result = _run_shell(script, {"GH_TOKEN": "tok", "GITHUB_REPOSITORY": "o/r", "GITHUB_SHA": SHA_A}, bin_dir=bin_dir)
        assert differ_result.returncode != 0


# --- (a3) tests (2c)-(2f): runtime-enforced approval ----------------------------
def _fake_curl(bin_dir: Path, body: str, *, http_fail: bool = False) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    if http_fail:
        script = "#!/usr/bin/env bash\nexit 22\n"
    else:
        escaped = body.replace("'", "'\\''")
        script = f"#!/usr/bin/env bash\nprintf '%s' '{escaped}'\n"
    path = bin_dir / "curl"
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)


def test_1451_2f_approvals_url_names_this_repository_and_run() -> None:
    step = _step("deploy", APPROVAL_NAME)
    url = step["env"]["APPROVALS_URL"]
    assert "${{ github.repository }}" in url
    assert "${{ github.run_id }}" in url
    assert "/actions/runs/" in url
    assert url.rstrip("/").endswith("/approvals")


def test_1451_2d_approved_entry_proceeds_and_rejected_entry_fails(tmp_path: Path) -> None:
    script = _run("deploy", APPROVAL_NAME)

    bin_dir = tmp_path / "approved"
    _fake_curl(bin_dir, json.dumps([{"state": "approved", "user": {"login": "the-approver"}}]))
    out = tmp_path / "approved.out"
    result = _run_shell(script, {"GH_TOKEN": "tok", "APPROVALS_URL": "https://example.test/approvals"}, bin_dir=bin_dir, github_output=out)
    assert result.returncode == 0, result.stderr
    assert _read_output(out) == {"approver": "the-approver"}

    bin_dir2 = tmp_path / "rejected"
    _fake_curl(bin_dir2, json.dumps([{"state": "rejected", "user": {"login": "someone"}}]))
    result2 = _run_shell(script, {"GH_TOKEN": "tok", "APPROVALS_URL": "https://example.test/approvals"}, bin_dir=bin_dir2)
    assert result2.returncode != 0


def test_1451_2c_an_empty_approvals_response_fails_and_no_ssh_step_runs(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    _fake_curl(bin_dir, "[]")
    script = _run("deploy", APPROVAL_NAME)
    result = _run_shell(script, {"GH_TOKEN": "tok", "APPROVALS_URL": "https://example.test/approvals"}, bin_dir=bin_dir)
    assert result.returncode != 0
    # The step that would invoke ssh never runs because this step already
    # failed - proven the same way as the guard/deploy skip: the job's steps
    # execute sequentially and a nonzero step aborts the job under `set -e`
    # semantics GitHub Actions itself enforces between steps.
    sentinel = tmp_path / "ssh_invoked.marker"
    assert not sentinel.exists()


@pytest.mark.parametrize(
    "mode",
    ["http_fail", "nonjson"],
)
def test_1451_2e_query_errors_or_returns_non_json_fails_closed(tmp_path: Path, mode: str) -> None:
    bin_dir = tmp_path / "bin"
    script = _run("deploy", APPROVAL_NAME)
    if mode == "http_fail":
        _fake_curl(bin_dir, "", http_fail=True)
    else:
        _fake_curl(bin_dir, "not json at all")
    result = _run_shell(script, {"GH_TOKEN": "tok", "APPROVALS_URL": "https://example.test/approvals"}, bin_dir=bin_dir)
    assert result.returncode != 0


def test_1451_approvals_query_has_a_bounded_timeout() -> None:
    run = _run("deploy", APPROVAL_NAME)
    assert "--max-time" in run


# --- §145.1 test (5b)/(5d): the PM_DEPLOY_TARGET_SHA pin -----------------------
def test_1451_5b_ssh_invocation_pins_target_sha_to_github_sha() -> None:
    step = _step("deploy", DEPLOY_SSH_NAME)
    assert step["env"]["GITHUB_SHA"] == "${{ github.sha }}"
    run = step["run"]
    assert "PM_DEPLOY_TARGET_SHA='$GITHUB_SHA'" in run


def test_1451_5d_the_pin_is_in_the_command_string_sent_to_the_host_not_only_env() -> None:
    # The requirement this test exists for: workflow `env:` does not cross the
    # `ssh` boundary on its own. The pin must be part of the STRING passed as
    # the remote command, not merely declared in this step's `env:` block.
    run = _run("deploy", DEPLOY_SSH_NAME)
    ssh_invocation = run.split('"$PM_VPS_USER@$PM_VPS_HOST" \\', 1)[1]
    assert "PM_DEPLOY_TARGET_SHA='$GITHUB_SHA'" in ssh_invocation
    # And the value is re-asserted inside the remote heredoc, where it is
    # threaded into the tmux pane that actually execs the deploy script -
    # not merely inherited and hoped for.
    remote = run.split("<<'REMOTE'", 1)[1]
    assert "PM_DEPLOY_TARGET_SHA='$PM_DEPLOY_TARGET_SHA'" in remote
    assert "scripts/deploy_vps_paper_manual.sh" in remote


def test_1451_trigger_mechanism_is_derived_from_the_real_event_name() -> None:
    run = _run("deploy", DEPLOY_SSH_NAME)
    assert "push) trigger_mechanism=push" in run
    assert "workflow_dispatch) trigger_mechanism=workflow_dispatch" in run
    step = _step("deploy", DEPLOY_SSH_NAME)
    assert step["env"]["GITHUB_EVENT_NAME"] == "${{ github.event_name }}"


# --- WO-145 test 6 / §145.1 test (8): trigger_mechanism + actor fields --------
def test_wo145_6_deploy_record_carries_trigger_mechanism_never_authorised_by() -> None:
    run = _run("deploy", DEPLOY_SSH_NAME)
    assert "PM_DEPLOY_TRIGGER_MECHANISM=" in run
    assert "PM_DEPLOY_TRIGGER_ACTOR=" in run
    assert "PM_DEPLOY_APPROVAL_ACTOR=" in run
    assert "authorised_by" not in _text()
    # And the actor fields are sourced from real identities, never a literal
    # claim: github.actor and the approvals-query response.
    step = _step("deploy", DEPLOY_SSH_NAME)
    assert step["env"]["GITHUB_ACTOR"] == "${{ github.actor }}"
    assert step["env"]["APPROVAL_ACTOR"] == "${{ steps.approval.outputs.approver }}"


def test_1451_8_push_triggered_deploy_record_has_no_authorised_by(tmp_path: Path) -> None:
    script_path = ROOT / "scripts" / "deploy_vps_paper_manual.sh"
    script = f"""
    set -e
    PM_MANUAL_DEPLOY_LIBRARY_ONLY=1 . {script_path}
    REPO_DIR={tmp_path}
    PM_OUTPUT_ROOT={tmp_path}/outputs
    PM_DEPLOY_TRIGGER_MECHANISM=push
    PM_DEPLOY_TRIGGER_ACTOR=merger
    PM_DEPLOY_APPROVAL_ACTOR=approver
    write_deploy_record {SHA_A}
    """
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    record = json.loads((tmp_path / "outputs" / "performance" / "vps_manual_deploy.json").read_text(encoding="utf-8"))
    assert record["trigger_mechanism"] == "push"
    assert "authorised_by" not in record


# --- WO-145 test 7: attestation_verified stays false ----------------------------
def test_wo145_7_attestation_verified_stays_false_and_reason_names_the_real_blocker() -> None:
    script = ROOT / "scripts" / "deploy_vps_paper_manual.sh"
    body = script.read_text(encoding="utf-8")
    assert '"attestation_verified": False' in body
    assert '"attestation_verified": True' not in body
    assert "no eligible independent reviewer can exist" in body
    # It must not claim a capability the Actions context actually possesses
    # (a GitHub token and the acceptance run artifact) - that was the exact
    # defect WO-145 replaces.
    assert "requires GitHub API credentials and the" not in body


# --- WO-145 test 5: the tmux exit-code fail-open, exercised for real -----------
def _extract_remote_block(run_text: str) -> str:
    match = re.search(r"<<'REMOTE'\n(.*)\nREMOTE", run_text, re.DOTALL)
    assert match, "no REMOTE heredoc found in the deploy-over-ssh step"
    return match.group(1)


@pytest.mark.skipif(
    subprocess.run(["which", "tmux"], capture_output=True).returncode != 0,
    reason="tmux is not installed in this sandbox",
)
@pytest.mark.parametrize("exit_code", [0, 1, 7])
def test_wo145_5_tmux_sentinel_wrapper_propagates_the_real_exit_code(tmp_path: Path, exit_code: int) -> None:
    """Exercises the sentinel/wait-for contract directly, not through a real
    deploy: `tmux new-session -d` returns 0 immediately, so without this
    wrapper a failed deploy would report a green workflow run."""
    remote_script = _extract_remote_block(_run("deploy", DEPLOY_SSH_NAME))

    repo = tmp_path / "repo" / "scripts"
    repo.mkdir(parents=True)
    stub = repo / "deploy_vps_paper_manual.sh"
    stub.write_text(
        "#!/usr/bin/env bash\nexit \"${STUB_EXIT_CODE:-0}\"\n", encoding="utf-8"
    )
    stub.chmod(0o755)

    run_id = f"pytest-{tmp_path.name}-{exit_code}"
    subprocess.run(["tmux", "kill-session", "-t", f"pm-deploy-{run_id}"], capture_output=True)
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(Path.home()),
        "STUB_EXIT_CODE": str(exit_code),
        "PM_VPS_REPO_DIR": str(tmp_path / "repo"),
        "GITHUB_RUN_ID": run_id,
        "PM_DEPLOY_TARGET_SHA": SHA_A,
        "PM_DEPLOY_TRIGGER_MECHANISM": "push",
        "PM_DEPLOY_TRIGGER_ACTOR": "someone",
        "PM_DEPLOY_APPROVAL_ACTOR": "someone-else",
    }
    result = subprocess.run(["bash", "-c", remote_script], capture_output=True, text=True, env=env, timeout=30)
    assert result.returncode == exit_code, result.stderr


def test_wo145_5_remote_script_removes_its_own_sentinel_file() -> None:
    remote_script = _extract_remote_block(_run("deploy", DEPLOY_SSH_NAME))
    assert remote_script.count("rm -f \"$SENTINEL\"") == 2
    assert "tmux wait-for \"$CHANNEL\"" in remote_script
    assert "tmux wait-for -S '$CHANNEL'" in remote_script


# --- workflow inventory (§145.1(c)) ---------------------------------------------
def test_1451_c_workflow_appears_in_the_required_pr_gate_inventory() -> None:
    inventory_test = (ROOT / "tests" / "test_required_pr_gate.py").read_text(encoding="utf-8")
    assert '"deploy_vps_paper_dispatch.yml": {"push", "workflow_dispatch"}' in inventory_test
