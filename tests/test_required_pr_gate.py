from __future__ import annotations

import base64
import importlib.util
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ID = 123
ACCEPTED_WORKFLOW_SHA = "f" * 40
MODULE_PATH = ROOT / "scripts" / "audit_github_merge_gate.py"
SPEC = importlib.util.spec_from_file_location("audit_github_merge_gate", MODULE_PATH)
assert SPEC and SPEC.loader
merge_gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(merge_gate)

MERGE_MODULE_PATH = ROOT / "scripts" / "merge_independently_reviewed_pr.py"
MERGE_SPEC = importlib.util.spec_from_file_location("merge_independently_reviewed_pr", MERGE_MODULE_PATH)
assert MERGE_SPEC and MERGE_SPEC.loader
independent_merge = importlib.util.module_from_spec(MERGE_SPEC)
MERGE_SPEC.loader.exec_module(independent_merge)


def _workflow() -> str:
    return (ROOT / ".github" / "workflows" / "required-pr-gate.yml").read_text(encoding="utf-8")


def _independent_workflow() -> str:
    return (ROOT / ".github" / "workflows" / "independent-pr-merge.yml").read_text(encoding="utf-8")


def _contents(text: str) -> dict:
    return {
        "type": "file",
        "encoding": "base64",
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
    }


def _runners(status: str = "online") -> dict:
    return {
        "runners": [
            {
                "name": "oracle-vps-polymarket-ci",
                "status": status,
                "labels": [{"name": label} for label in ("self-hosted", "Linux", "ARM64", "polymarket-ci")],
            }
        ]
    }


def _protection() -> dict:
    return {
        "required_status_checks": {"strict": True, "contexts": [merge_gate.REQUIRED_CONTEXT]},
        "enforce_admins": {"enabled": True},
        "required_pull_request_reviews": {
            "required_approving_review_count": 1,
            "dismiss_stale_reviews": True,
            "require_last_push_approval": True,
            "bypass_pull_request_allowances": {"users": [], "teams": [], "apps": []},
        },
        "required_conversation_resolution": {"enabled": True},
    }


def _successful_run() -> dict:
    return {
        "workflow_runs": [
            {"id": 123, "event": "pull_request", "status": "completed", "conclusion": "success", "html_url": "https://example.test/run/123"}
        ]
    }


def _collaborators() -> list[dict]:
    return [
        {"login": "author", "permissions": {"push": True}},
        {"login": "reviewer", "permissions": {"push": True}},
    ]


def _required_workflow_ruleset(
    *,
    bypass: bool = False,
    repository_id: int = REPOSITORY_ID,
    sha: str = ACCEPTED_WORKFLOW_SHA,
) -> dict:
    return {
        "id": 901,
        "name": "exact required workflow",
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": ([{"actor_id": 1, "actor_type": "RepositoryRole"}] if bypass else []),
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "rules": [
            {
                "type": "workflows",
                "parameters": {
                    "workflows": [
                        {
                            "path": ".github/workflows/required-pr-gate.yml",
                            "ref": "refs/heads/main",
                            "repository_id": repository_id,
                            "sha": sha,
                        }
                    ]
                },
            }
        ],
    }


def test_registered_workflow_is_minimal_self_hosted_and_secretless() -> None:
    workflow = _workflow()
    assert merge_gate.workflow_is_configured(workflow)
    assert "pull_request:" in workflow
    assert "types: [opened, synchronize, reopened, ready_for_review]" in workflow
    assert "runs-on: [self-hosted, Linux, ARM64, polymarket-ci]" in workflow
    assert "timeout-minutes: 30" in workflow
    assert "docker run --rm --cpus 2 --memory 4g --pids-limit 512" in workflow
    assert "path: proposed-merge" in workflow
    assert "fetch-depth: 0" in workflow
    assert '${GITHUB_WORKSPACE}/proposed-merge:/src:ro' in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "secrets." not in workflow
    assert "actions/setup-python" not in workflow
    assert "shell: powershell" not in workflow
    assert "    container:" not in workflow
    assert "apt-get install -y -qq --no-install-recommends git ca-certificates" in workflow
    assert "python -m venv /tmp/ci-venv" in workflow
    assert "pip install --disable-pip-version-check -e" in workflow
    assert "pip check" in workflow
    assert "/tmp/ci-venv/bin/python -m pytest -q" in workflow
    assert workflow.count("pytest") == 1
    assert "tests/" not in workflow
    assert "--ignore" not in workflow
    assert "continue-on-error:" not in workflow
    assert "ruff check ." in workflow
    assert "config-check --config polymarket_predictive_config.example.yaml" in workflow


def test_independent_merge_workflow_is_manual_exact_head_and_second_identity_only() -> None:
    workflow = _independent_workflow()
    assert merge_gate.independent_merge_workflow_is_configured(workflow)
    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in merge_gate._top_level_on_block(workflow)
    assert "expected_head_sha:" in workflow
    assert "path: merge-utility" in workflow
    assert "fetch-depth: 0" in workflow
    assert "MERGE_ACTOR: ${{ github.actor }}" in workflow
    assert "MERGE_TRIGGERING_ACTOR: ${{ github.triggering_actor }}" in workflow
    assert "scripts/merge_independently_reviewed_pr.py" in workflow
    assert "--merge" in workflow
    assert "set -euo pipefail" in workflow
    assert '| tee "$RUNNER_TEMP/independent-main-acceptance/merge-attestation.json"' in workflow
    assert "actions/upload-artifact@v7" in workflow
    assert "independent-main-acceptance-${{ github.run_id }}" in workflow
    assert "if-no-files-found: error" in workflow
    assert "secrets." not in workflow


def test_workflow_inventory_has_only_registered_triggers() -> None:
    workflow_dir = ROOT / ".github" / "workflows"
    expected_triggers = {
        "auto_pick.yml": {"workflow_dispatch"},
        "check_superbru_fixtures.yml": {"workflow_dispatch"},
        "ci.yml": {"workflow_dispatch"},
        "deploy-polymarket-vps-paper.yml": {"workflow_dispatch"},
        "polymarket-vps-governance-refresh.yml": {"workflow_dispatch"},
        "polymarket-vps-proof-health.yml": {"workflow_dispatch"},
        "refresh-locked-superbru-card.yml": {"workflow_dispatch"},
        "repo-audit-bundle.yml": {"workflow_dispatch"},
        "required-pr-gate.yml": {"pull_request"},
        "independent-pr-merge.yml": {"workflow_dispatch"},
        "superbru-clv-snapshot.yml": {"workflow_dispatch"},
    }
    present = {path.name for path in workflow_dir.glob("*.y*ml")}
    assert present == set(expected_triggers)
    for name, expected in expected_triggers.items():
        text = (workflow_dir / name).read_text(encoding="utf-8")
        on_block = merge_gate._top_level_on_block(text)
        actual = {
            trigger
            for trigger in ("schedule", "push", "pull_request", "workflow_dispatch")
            if re.search(rf"^  {re.escape(trigger)}\s*:", on_block, flags=re.MULTILINE)
        }
        assert actual == expected, name
        for required in ("timeout-minutes:", "concurrency:", "permissions:"):
            assert required in text, f"{name} is missing {required}"


def test_workflows_use_node24_action_majors() -> None:
    workflow_dir = ROOT / ".github" / "workflows"
    workflow_text = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(workflow_dir.glob("*.y*ml"))
    )
    expected_versions = {
        "actions/checkout": "v6",
        "actions/setup-python": "v6",
        "actions/upload-artifact": "v7",
    }

    for action, expected_version in expected_versions.items():
        versions = set(re.findall(rf"{re.escape(action)}@(v\d+)", workflow_text))
        assert versions == {expected_version}, f"{action} versions: {sorted(versions)}"


def test_legacy_context_never_claims_workflow_identity_enforcement() -> None:
    result = merge_gate.evaluate_merge_gate(
        workflow_text=_workflow(),
        runners_payload=_runners(),
        protection_payload=_protection(),
        runs_payload=_successful_run(),
        independent_merge_workflow_text=_independent_workflow(),
        collaborators_payload=_collaborators(),
    )
    assert result["status"] == "documented_independent_process_only"
    assert result["enforced"] is False
    assert result["branch_protection_enabled"] is True
    assert result["legacy_required_context_registered"] is True
    assert result["required_check_enforced"] is False
    assert result["required_workflow_identity_enforced"] is False
    assert result["independent_review_required"] is True
    assert result["direct_push_forbidden"] is True
    assert result["independent_process_operational"] is True
    assert "required_workflow_identity_enforced" in result["blockers"]
    assert "same-named GitHub Actions job" in result["legacy_status_context_risk"]


def test_active_no_bypass_ruleset_can_prove_required_workflow_identity() -> None:
    result = merge_gate.evaluate_merge_gate(
        workflow_text=_workflow(),
        runners_payload=_runners(),
        protection_payload=_protection(),
        runs_payload=_successful_run(),
        independent_merge_workflow_text=_independent_workflow(),
        collaborators_payload=_collaborators(),
        rulesets_payload=[_required_workflow_ruleset()],
        repository_id=REPOSITORY_ID,
        accepted_workflow_sha=ACCEPTED_WORKFLOW_SHA,
    )

    assert result["enforced"] is True
    assert result["status"] == "enforced"
    assert result["required_workflow_identity_enforced"] is True
    assert result["required_workflow_ruleset_ids"] == [901]


def test_required_workflow_ruleset_with_bypass_is_not_enforcement() -> None:
    result = merge_gate.evaluate_merge_gate(
        workflow_text=_workflow(),
        runners_payload=_runners(),
        protection_payload=_protection(),
        runs_payload=_successful_run(),
        independent_merge_workflow_text=_independent_workflow(),
        collaborators_payload=_collaborators(),
        rulesets_payload=[_required_workflow_ruleset(bypass=True)],
        repository_id=REPOSITORY_ID,
        accepted_workflow_sha=ACCEPTED_WORKFLOW_SHA,
    )

    assert result["enforced"] is False
    assert result["required_workflow_identity_enforced"] is False


@pytest.mark.parametrize(
    ("ruleset_repository_id", "ruleset_sha"),
    [
        (999, ACCEPTED_WORKFLOW_SHA),
        (REPOSITORY_ID, "e" * 40),
        (REPOSITORY_ID, ""),
    ],
)
def test_required_workflow_ruleset_must_match_repository_and_accepted_revision(
    ruleset_repository_id: int,
    ruleset_sha: str,
) -> None:
    result = merge_gate.evaluate_merge_gate(
        workflow_text=_workflow(),
        runners_payload=_runners(),
        protection_payload=_protection(),
        runs_payload=_successful_run(),
        independent_merge_workflow_text=_independent_workflow(),
        collaborators_payload=_collaborators(),
        rulesets_payload=[
            _required_workflow_ruleset(
                repository_id=ruleset_repository_id,
                sha=ruleset_sha,
            )
        ],
        repository_id=REPOSITORY_ID,
        accepted_workflow_sha=ACCEPTED_WORKFLOW_SHA,
    )

    assert result["enforced"] is False
    assert result["required_workflow_identity_enforced"] is False
    assert result["required_workflow_ruleset_ids"] == []


def test_audit_fetches_repository_identity_and_latest_accepted_workflow_revision(
    monkeypatch,
    tmp_path: Path,
) -> None:
    ruleset = _required_workflow_ruleset()

    def fake_gh_api(path, *, method="GET", payload=None):
        assert method in {"GET", "PUT"}
        assert payload is None
        responses = {
            "repos/owner/repo/actions/runners": _runners(),
            "repos/owner/repo/branches/main/protection": _protection(),
            "repos/owner/repo/actions/workflows/required-pr-gate.yml/runs?event=pull_request&per_page=20": _successful_run(),
            "repos/owner/repo/collaborators?affiliation=all&per_page=100": _collaborators(),
            "repos/owner/repo": {"id": REPOSITORY_ID, "default_branch": "main"},
            "repos/owner/repo/commits?path=.github/workflows/required-pr-gate.yml&sha=main&per_page=1": [
                {"sha": ACCEPTED_WORKFLOW_SHA}
            ],
            f"repos/owner/repo/contents/.github/workflows/required-pr-gate.yml?ref={ACCEPTED_WORKFLOW_SHA}": _contents(
                _workflow()
            ),
            f"repos/owner/repo/contents/.github/workflows/independent-pr-merge.yml?ref={ACCEPTED_WORKFLOW_SHA}": _contents(
                _independent_workflow()
            ),
            "repos/owner/repo/rulesets?includes_parents=true&per_page=100": [
                {"id": ruleset["id"]}
            ],
            f"repos/owner/repo/rulesets/{ruleset['id']}": ruleset,
        }
        return responses[path], ""

    monkeypatch.setattr(merge_gate, "_gh_api", fake_gh_api)

    stale_workflow = tmp_path / ".github" / "workflows" / "required-pr-gate.yml"
    stale_workflow.parent.mkdir(parents=True)
    stale_workflow.write_text("name: stale local workflow\n", encoding="utf-8")
    stale_workflow.with_name("independent-pr-merge.yml").write_text(
        "name: stale local sibling\n",
        encoding="utf-8",
    )

    result = merge_gate.audit("owner/repo", stale_workflow)

    assert result["enforced"] is True
    assert result["expected_required_workflow_repository_id"] == REPOSITORY_ID
    assert result["accepted_required_workflow_sha"] == ACCEPTED_WORKFLOW_SHA
    assert result["independent_merge_process_configured"] is True
    assert result["query_errors"]["accepted_required_workflow_contents"] == ""


def test_private_free_plan_blocker_stays_explicit_and_fail_closed() -> None:
    result = merge_gate.evaluate_merge_gate(
        workflow_text=_workflow(),
        runners_payload=_runners(),
        protection_payload={},
        protection_error="Upgrade to GitHub Pro or make this repository public to enable this feature.",
        runs_payload=_successful_run(),
        independent_merge_workflow_text=_independent_workflow(),
        collaborators_payload=[{"login": "author", "permissions": {"push": True}}],
    )
    assert result["status"] == "blocked_github_plan_and_independent_identity"
    assert result["enforced"] is False
    assert result["branch_protection_enabled"] is False
    assert result["independent_review_required"] is False
    assert result["independent_merge_process_configured"] is True
    assert result["independent_identity_available"] is False
    assert "branch_protection_enabled" in result["blockers"]
    assert "required_workflow_identity_enforced" in result["blockers"]
    assert "workflow-identity-capable" in result["owner_action"]
    assert result["paper_trading_invoked"] is False
    assert result["live_trading_invoked"] is False


def test_offline_runner_cannot_be_reported_as_enforced() -> None:
    result = merge_gate.evaluate_merge_gate(
        workflow_text=_workflow(),
        runners_payload=_runners("offline"),
        protection_payload=_protection(),
        runs_payload=_successful_run(),
        independent_merge_workflow_text=_independent_workflow(),
        collaborators_payload=_collaborators(),
    )
    assert result["status"] == "blocked_legacy_status_not_workflow_bound"
    assert result["enforced"] is False
    assert result["checks"]["runner_registered"] is True
    assert result["checks"]["runner_online"] is False


def test_latest_active_or_failed_gate_cannot_reuse_prior_success() -> None:
    runs = {
        "workflow_runs": [
            {"id": 125, "event": "pull_request", "status": "in_progress", "conclusion": None},
            {"id": 124, "event": "pull_request", "status": "completed", "conclusion": "failure"},
            {"id": 123, "event": "pull_request", "status": "completed", "conclusion": "success"},
        ]
    }

    result = merge_gate.evaluate_merge_gate(
        workflow_text=_workflow(),
        runners_payload=_runners(),
        protection_payload=_protection(),
        runs_payload=runs,
        independent_merge_workflow_text=_independent_workflow(),
        collaborators_payload=_collaborators(),
    )

    assert result["enforced"] is False
    assert result["checks"]["latest_gate_success"] is False
    assert result["latest_gate_run"]["id"] == 125
    assert result["latest_successful_gate_run"]["id"] is None
    assert result["active_gate_run_count"] == 1


def test_registered_protection_payload_has_no_owner_or_review_bypass() -> None:
    payload = merge_gate.branch_protection_payload()
    assert payload["required_status_checks"]["contexts"] == [merge_gate.REQUIRED_CONTEXT]
    assert payload["enforce_admins"] is True
    assert payload["required_pull_request_reviews"]["required_approving_review_count"] == 1
    assert payload["required_pull_request_reviews"]["require_last_push_approval"] is True
    assert payload["required_pull_request_reviews"]["bypass_pull_request_allowances"] == {"users": [], "teams": [], "apps": []}
    assert payload["allow_force_pushes"] is False
    assert payload["allow_deletions"] is False


def test_same_named_github_actions_context_cannot_certify_branch_protection() -> None:
    protection = _protection()
    protection["required_status_checks"] = {
        "strict": True,
        "checks": [
            {
                "context": merge_gate.REQUIRED_CONTEXT,
                "app_id": 15368,
            }
        ],
    }

    result = merge_gate.evaluate_merge_gate(
        workflow_text=_workflow(),
        runners_payload=_runners(),
        protection_payload=protection,
        runs_payload=_successful_run(),
        independent_merge_workflow_text=_independent_workflow(),
        collaborators_payload=[{"login": "author", "permissions": {"push": True}}],
    )

    assert result["legacy_required_context_registered"] is True
    assert result["required_workflow_identity_enforced"] is False
    assert result["enforced"] is False
    assert result["status"] == "blocked_legacy_status_not_workflow_bound"


def test_protection_without_latest_push_semantics_is_not_enforced() -> None:
    protection = _protection()
    protection["required_pull_request_reviews"]["require_last_push_approval"] = False

    result = merge_gate.evaluate_merge_gate(
        workflow_text=_workflow(),
        runners_payload=_runners(),
        protection_payload=protection,
        runs_payload=_successful_run(),
        independent_merge_workflow_text=_independent_workflow(),
        collaborators_payload=_collaborators(),
    )

    assert result["enforced"] is False
    assert result["checks"]["last_push_approval_required"] is False
    assert result["checks"]["independent_review_required"] is False


def test_failed_gh_api_response_cannot_masquerade_as_control_state(monkeypatch) -> None:
    class Failed:
        returncode = 1
        stdout = '{"message":"Upgrade to GitHub Pro","status":"403"}'
        stderr = "gh: Upgrade to GitHub Pro"

    monkeypatch.setattr(merge_gate.subprocess, "run", lambda *args, **kwargs: Failed())

    payload, error = merge_gate._gh_api("repos/example/private/branches/main/protection")

    assert payload == {}
    assert "Upgrade to GitHub Pro" in error


def _candidate_evidence() -> dict:
    head = "a" * 40
    main = "b" * 40
    return {
        "pull_request": {
            "number": 321,
            "title": "Verified change",
            "state": "open",
            "draft": False,
            "mergeable": True,
            "user": {"login": "author"},
            "head": {"sha": head},
            "base": {"ref": "main", "sha": main},
        },
        "expected_head": head,
        "actor": "reviewer",
        "triggering_actor": "reviewer",
        "main_ref": {"object": {"sha": main}},
        "comparison": {"status": "ahead", "behind_by": 0},
        "check_runs": [
            {
                "id": 10,
                "name": independent_merge.REQUIRED_CONTEXT,
                "head_sha": head,
                "status": "completed",
                "conclusion": "success",
                "app": {"slug": "github-actions"},
            }
        ],
        "workflow_runs": [
            {
                "id": 20,
                "event": "pull_request",
                "head_sha": head,
                "path": independent_merge.REQUIRED_WORKFLOW,
                "status": "completed",
                "conclusion": "success",
            }
        ],
        "reviews": [
            {
                "id": 30,
                "state": "APPROVED",
                "commit_id": head,
                "submitted_at": "2026-07-19T12:00:00Z",
                "author_association": "COLLABORATOR",
                "user": {"login": "reviewer"},
            }
        ],
        "review_threads": [{"id": "thread-1", "isResolved": True, "isOutdated": False}],
        "changed_files": [{"filename": "src/safe_change.py"}],
    }


def test_independent_merge_candidate_requires_exact_current_head_evidence() -> None:
    result = independent_merge.evaluate_merge_candidate(**_candidate_evidence())

    assert result["eligible"] is True
    assert result["status"] == "eligible"
    assert result["eligible_reviewers"] == ["reviewer"]
    assert result["blockers"] == []
    assert result["funding_opened"] is False
    assert result["wo67_status"] == "BLOCKED"


def test_actions_workflow_path_ref_suffix_is_normalized() -> None:
    evidence = _candidate_evidence()
    evidence["workflow_runs"][0]["path"] = (
        f"{independent_merge.REQUIRED_WORKFLOW}@refs/pull/321/merge"
    )

    result = independent_merge.evaluate_merge_candidate(**evidence)

    assert result["eligible"] is True
    assert result["checks"]["latest_exact_head_workflow_passed"] is True


def test_stale_approval_and_author_dispatch_are_both_rejected() -> None:
    evidence = _candidate_evidence()
    evidence["actor"] = "author"
    evidence["reviews"][0]["commit_id"] = "c" * 40

    result = independent_merge.evaluate_merge_candidate(**evidence)

    assert result["eligible"] is False
    assert "merge_actor_is_pull_request_author" in result["blockers"]
    assert "no_independent_approval_on_current_head" in result["blockers"]
    assert "merge_actor_did_not_approve_current_head" in result["blockers"]


def test_latest_failed_exact_head_check_cannot_reuse_older_success() -> None:
    evidence = _candidate_evidence()
    evidence["check_runs"].append(
        {
            "id": 11,
            "name": independent_merge.REQUIRED_CONTEXT,
            "head_sha": evidence["expected_head"],
            "status": "completed",
            "conclusion": "failure",
            "app": {"slug": "github-actions"},
        }
    )

    result = independent_merge.evaluate_merge_candidate(**evidence)

    assert result["eligible"] is False
    assert result["latest_required_check_id"] == 11
    assert "latest_exact_head_required_check_not_successful" in result["blockers"]


def test_unresolved_thread_or_behind_head_blocks_merge() -> None:
    evidence = _candidate_evidence()
    evidence["comparison"] = {"status": "diverged", "behind_by": 1}
    evidence["review_threads"][0]["isResolved"] = False

    result = independent_merge.evaluate_merge_candidate(**evidence)

    assert result["eligible"] is False
    assert "pull_request_head_does_not_contain_current_main" in result["blockers"]
    assert "unresolved_review_threads" in result["blockers"]


def test_rerun_initiator_must_be_an_independent_current_head_approver() -> None:
    evidence = _candidate_evidence()
    evidence["triggering_actor"] = "author"

    result = independent_merge.evaluate_merge_candidate(**evidence)

    assert result["eligible"] is False
    assert "merge_triggering_actor_is_pull_request_author" in result["blockers"]
    assert "merge_triggering_actor_did_not_approve_current_head" in result["blockers"]


def test_candidate_cannot_replace_the_trusted_merge_control() -> None:
    evidence = _candidate_evidence()
    evidence["changed_files"] = [
        {
            "filename": ".github/workflows/renamed-required-pr-gate.yml",
            "previous_filename": ".github/workflows/required-pr-gate.yml",
        },
        {"filename": "src/safe_change.py"},
    ]

    result = independent_merge.evaluate_merge_candidate(**evidence)

    assert result["eligible"] is False
    assert result["protected_control_changes"] == [
        ".github/workflows/required-pr-gate.yml"
    ]
    assert "pull_request_changes_trusted_merge_control" in result["blockers"]


@pytest.mark.parametrize(
    "path",
    [
        "conftest.py",
        "tests/integration/conftest.py",
        "pip.py",
        "pip/__main__.py",
        "pytest.py",
        "pytest/__main__.py",
        "pytest.ini",
        "pyproject.toml",
        "ruff.py",
        "ruff/__main__.py",
        "sitecustomize.py",
    ],
)
def test_candidate_cannot_change_pytest_execution_controls(path: str) -> None:
    evidence = _candidate_evidence()
    evidence["changed_files"] = [{"filename": path}]

    result = independent_merge.evaluate_merge_candidate(**evidence)

    assert result["eligible"] is False
    assert result["protected_control_changes"] == [path]
    assert "pull_request_changes_trusted_merge_control" in result["blockers"]


def test_atomic_squash_uses_verified_tree_parent_and_non_force_ref_update() -> None:
    head = "a" * 40
    main = "b" * 40
    tree = "c" * 40
    merged = "d" * 40

    class FakeClient:
        def __init__(self) -> None:
            self.calls = []

        def request(self, path, *, method="GET", payload=None):
            self.calls.append((path, method, payload))
            if path.endswith(f"/git/commits/{head}") and method == "GET":
                return {"sha": head, "tree": {"sha": tree}}
            if path.endswith("/git/commits") and method == "POST":
                assert payload["tree"] == tree
                assert payload["parents"] == [main]
                return {
                    "sha": merged,
                    "tree": {"sha": tree},
                    "parents": [{"sha": main}],
                }
            if path.endswith("/git/refs/heads/main") and method == "PATCH":
                assert payload == {"sha": merged, "force": False}
                return {"object": {"sha": merged}}
            if path.endswith("/pulls/321") and method == "GET":
                return {"state": "open", "head": {"sha": head}}
            if path.endswith("/pulls/321") and method == "PATCH":
                assert payload == {"state": "closed"}
                return {"state": "closed"}
            raise AssertionError((path, method, payload))

    client = FakeClient()
    result = independent_merge.atomic_squash_merge(
        client,
        repo="owner/repo",
        pull_request={"number": 321, "title": "Verified change"},
        expected_head=head,
        expected_main=main,
    )

    assert result["merged"] is True
    assert result["merge_commit_sha"] == merged
    assert result["verified_main_parent_sha"] == main
    assert result["merge_method"] == "atomic_fast_forward_squash"
    assert result["pull_request_close_skipped_reason"] == ""


def test_atomic_squash_leaves_pr_open_if_head_changes_after_main_update() -> None:
    head = "a" * 40
    changed_head = "e" * 40
    main = "b" * 40
    tree = "c" * 40
    merged = "d" * 40

    class HeadRaceClient:
        def __init__(self) -> None:
            self.close_called = False

        def request(self, path, *, method="GET", payload=None):
            if path.endswith(f"/git/commits/{head}") and method == "GET":
                return {"sha": head, "tree": {"sha": tree}}
            if path.endswith("/git/commits") and method == "POST":
                return {
                    "sha": merged,
                    "tree": {"sha": tree},
                    "parents": [{"sha": main}],
                }
            if path.endswith("/git/refs/heads/main") and method == "PATCH":
                return {"object": {"sha": merged}}
            if path.endswith("/pulls/321") and method == "GET":
                return {"state": "open", "head": {"sha": changed_head}}
            if path.endswith("/pulls/321") and method == "PATCH":
                self.close_called = True
            raise AssertionError((path, method, payload))

    client = HeadRaceClient()
    result = independent_merge.atomic_squash_merge(
        client,
        repo="owner/repo",
        pull_request={"number": 321, "title": "Verified change"},
        expected_head=head,
        expected_main=main,
    )

    assert result["merged"] is True
    assert client.close_called is False
    assert (
        result["pull_request_close_skipped_reason"]
        == "pull_request_head_changed_after_atomic_merge"
    )


def test_atomic_squash_fails_closed_if_main_advances_before_ref_update() -> None:
    head = "a" * 40
    main = "b" * 40
    tree = "c" * 40
    merged = "d" * 40

    class RacingClient:
        def request(self, path, *, method="GET", payload=None):
            if path.endswith(f"/git/commits/{head}") and method == "GET":
                return {"sha": head, "tree": {"sha": tree}}
            if path.endswith("/git/commits") and method == "POST":
                return {
                    "sha": merged,
                    "tree": {"sha": tree},
                    "parents": [{"sha": main}],
                }
            if path.endswith("/git/refs/heads/main") and method == "PATCH":
                raise independent_merge.MergeGateError("422 not a fast-forward")
            raise AssertionError((path, method, payload))

    with pytest.raises(
        independent_merge.MergeGateError,
        match="atomic main update rejected",
    ):
        independent_merge.atomic_squash_merge(
            RacingClient(),
            repo="owner/repo",
            pull_request={"number": 321, "title": "Verified change"},
            expected_head=head,
            expected_main=main,
        )
