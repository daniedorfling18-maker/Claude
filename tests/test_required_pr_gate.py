from __future__ import annotations

import base64
import importlib.util
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ID = 123
ACCEPTED_WORKFLOW_SHA = "f" * 40
ACCEPTED_INDEPENDENT_WORKFLOW_SHA = "e" * 40
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


def test_independent_merge_workflow_is_default_branch_owner_comment_and_second_identity_only() -> None:
    workflow = _independent_workflow()
    assert merge_gate.independent_merge_workflow_is_configured(workflow)
    on_block = merge_gate._top_level_on_block(workflow)
    assert "issue_comment:" in on_block
    assert "workflow_dispatch:" not in on_block
    assert "pull_request:" not in on_block
    assert "github.actor == github.repository_owner" in workflow
    assert "github.triggering_actor == github.repository_owner" in workflow
    assert "/independent-merge" in workflow
    assert "--merge-comment \"$MERGE_COMMAND\"" in workflow
    assert "environment:" not in workflow
    assert "ref: main" in workflow
    assert workflow.count("${{ github.event.comment.body }}") == 1
    assert "path: merge-utility" in workflow
    assert "fetch-depth: 0" in workflow
    assert "id-token: write" in workflow
    assert "ACTIONS_ID_TOKEN_REQUEST_URL" in workflow
    assert "ACTIONS_ID_TOKEN_REQUEST_TOKEN" in workflow
    assert "scripts/merge_independently_reviewed_pr.py" in workflow
    assert "--merge" in workflow
    assert "set -euo pipefail" in workflow
    assert '| tee "$RUNNER_TEMP/independent-main-acceptance/merge-attestation.json"' in workflow
    assert "actions/upload-artifact@v7" in workflow
    assert "independent-main-acceptance-${{ github.run_id }}" in workflow
    assert "if-no-files-found: error" in workflow
    assert "secrets." not in workflow


def test_canonical_standards_describe_the_default_branch_owner_comment_lane() -> None:
    standards = (ROOT / "docs" / "ENGINEERING_STANDARDS.md").read_text(
        encoding="utf-8"
    )
    assert "`/independent-merge <40-character-lowercase-head-sha>`" in standards
    assert "GitHub loads this `issue_comment` workflow from the default" in standards
    assert "original workflow actor and actual `github.triggering_actor`" in standards
    assert "dispatch `.github/workflows/independent-pr-merge.yml`" not in standards


@pytest.mark.parametrize(
    "comment",
    [
        " /independent-merge " + "a" * 40,
        "/independent-merge  " + "a" * 40,
        "/independent-merge " + "A" * 40,
        "/independent-merge " + "a" * 39,
        "/independent-merge " + "a" * 40 + " --force",
        "/independent-merge " + "a" * 40 + "\nanything",
    ],
)
def test_merge_comment_parser_rejects_every_nonexact_command(comment: str) -> None:
    with pytest.raises(independent_merge.MergeGateError, match="merge comment must be exactly"):
        independent_merge.expected_head_from_merge_comment(comment)


def test_merge_comment_parser_returns_only_the_exact_lowercase_head() -> None:
    head = "a" * 40
    assert independent_merge.expected_head_from_merge_comment(
        f"/independent-merge {head}"
    ) == head


def test_oidc_identity_requires_the_accepted_main_workflow() -> None:
    now = 1_800_000_000
    audience = "https://github.com/owner/repo/independent-merge"
    claims = {
        "iss": independent_merge.OIDC_ISSUER,
        "aud": audience,
        "repository": "owner/repo",
        "event_name": "issue_comment",
        "ref": "refs/heads/main",
        "workflow_ref": (
            "owner/repo/.github/workflows/independent-pr-merge.yml@refs/heads/main"
        ),
        "workflow_sha": "b" * 40,
        "run_id": "9001",
        "actor": "owner",
        "iat": now - 10,
        "nbf": now - 10,
        "exp": now + 300,
    }

    accepted = independent_merge._validate_oidc_claims(
        claims,
        repo="owner/repo",
        audience=audience,
        now_epoch=now,
    )
    assert accepted["run_id"] == "9001"

    claims["workflow_ref"] = (
        "owner/repo/.github/workflows/independent-pr-merge.yml@refs/heads/attacker"
    )
    with pytest.raises(independent_merge.MergeGateError, match="workflow_ref"):
        independent_merge._validate_oidc_claims(
            claims,
            repo="owner/repo",
            audience=audience,
            now_epoch=now,
        )

    claims["workflow_ref"] = (
        "owner/repo/.github/workflows/independent-pr-merge.yml@refs/heads/main"
    )
    claims["actor"] = "reviewer"
    with pytest.raises(independent_merge.MergeGateError, match="actor"):
        independent_merge._validate_oidc_claims(
            claims,
            repo="owner/repo",
            audience=audience,
            now_epoch=now,
        )


def test_workflow_inventory_has_only_registered_triggers() -> None:
    workflow_dir = ROOT / ".github" / "workflows"
    expected_triggers = {
        "auto_pick.yml": {"workflow_dispatch"},
        "check_superbru_fixtures.yml": {"workflow_dispatch"},
        "ci.yml": {"workflow_dispatch"},
        "deploy-polymarket-vps-paper.yml": {"workflow_dispatch"},
        "deploy_vps_paper_dispatch.yml": {"push", "workflow_dispatch"},
        "polymarket-vps-governance-refresh.yml": {"workflow_dispatch"},
        "polymarket-vps-proof-health.yml": {"workflow_dispatch"},
        "refresh-locked-superbru-card.yml": {"workflow_dispatch"},
        "repo-audit-bundle.yml": {"workflow_dispatch"},
        "required-pr-gate.yml": {"pull_request"},
        "independent-pr-merge.yml": {"issue_comment"},
        "superbru-clv-snapshot.yml": {"workflow_dispatch"},
    }
    present = {path.name for path in workflow_dir.glob("*.y*ml")}
    assert present == set(expected_triggers)
    for name, expected in expected_triggers.items():
        text = (workflow_dir / name).read_text(encoding="utf-8")
        on_block = merge_gate._top_level_on_block(text)
        actual = {
            trigger
            for trigger in (
                "schedule",
                "push",
                "pull_request",
                "workflow_dispatch",
                "issue_comment",
            )
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
        "actions/download-artifact": "v8",
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
            "repos/owner/repo/commits?path=.github/workflows/independent-pr-merge.yml&sha=main&per_page=1": [
                {"sha": ACCEPTED_INDEPENDENT_WORKFLOW_SHA}
            ],
            f"repos/owner/repo/contents/.github/workflows/required-pr-gate.yml?ref={ACCEPTED_WORKFLOW_SHA}": _contents(
                _workflow()
            ),
            f"repos/owner/repo/contents/.github/workflows/independent-pr-merge.yml?ref={ACCEPTED_INDEPENDENT_WORKFLOW_SHA}": _contents(
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
    assert (
        result["accepted_independent_merge_workflow_sha"]
        == ACCEPTED_INDEPENDENT_WORKFLOW_SHA
    )
    assert result["independent_merge_process_configured"] is True
    assert result["query_errors"]["accepted_required_workflow_contents"] == ""
    assert result["query_errors"]["accepted_independent_workflow_contents"] == ""


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


@pytest.mark.parametrize(
    ("field", "check"),
    [
        ("allow_force_pushes", "force_pushes_forbidden"),
        ("allow_deletions", "deletions_forbidden"),
    ],
)
def test_rewrite_or_deletion_enabled_protection_is_not_enforced(
    field: str,
    check: str,
) -> None:
    protection = _protection()
    protection[field] = {"enabled": True}

    result = merge_gate.evaluate_merge_gate(
        workflow_text=_workflow(),
        runners_payload=_runners(),
        protection_payload=protection,
        runs_payload=_successful_run(),
        independent_merge_workflow_text=_independent_workflow(),
        collaborators_payload=_collaborators(),
    )

    assert result["enforced"] is False
    assert result["checks"][check] is False
    assert result["checks"]["direct_push_forbidden"] is False
    assert check in result["blockers"]


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
        "repository": "owner/repo",
        "pull_request": {
            "number": 321,
            "title": "Verified change",
            "state": "open",
            "draft": False,
            "mergeable": True,
            "user": {"login": "owner"},
            "head": {"sha": head},
            "base": {"ref": "main", "sha": main},
        },
        "expected_head": head,
        "workflow_identity": {
            "repository": "owner/repo",
            "event_name": "issue_comment",
            "ref": "refs/heads/main",
            "workflow_ref": (
                "owner/repo/.github/workflows/independent-pr-merge.yml@refs/heads/main"
            ),
            "workflow_sha": main,
            "run_id": "9001",
            "actor": "owner",
        },
        "merge_workflow_run": {
            "id": 9001,
            "event": "issue_comment",
            "path": independent_merge.INDEPENDENT_WORKFLOW,
            "head_branch": "main",
            "head_sha": main,
            "actor": {"login": "owner"},
            "triggering_actor": {"login": "owner"},
        },
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


def test_owner_cannot_supply_the_independent_review_for_another_author() -> None:
    evidence = _candidate_evidence()
    evidence["pull_request"]["user"] = {"login": "contributor"}
    evidence["reviews"][0]["user"] = {"login": "owner"}

    result = independent_merge.evaluate_merge_candidate(**evidence)

    assert result["eligible"] is False
    assert result["eligible_reviewers"] == []
    assert "no_independent_approval_on_current_head" in result["blockers"]


def test_actions_workflow_path_ref_suffix_is_normalized() -> None:
    evidence = _candidate_evidence()
    evidence["workflow_runs"][0]["path"] = (
        f"{independent_merge.REQUIRED_WORKFLOW}@refs/pull/321/merge"
    )

    result = independent_merge.evaluate_merge_candidate(**evidence)

    assert result["eligible"] is True
    assert result["checks"]["latest_exact_head_workflow_passed"] is True


def test_stale_approval_and_nonowner_merge_attempt_are_both_rejected() -> None:
    evidence = _candidate_evidence()
    evidence["workflow_identity"]["actor"] = "reviewer"
    evidence["merge_workflow_run"]["actor"] = {"login": "reviewer"}
    evidence["reviews"][0]["commit_id"] = "c" * 40

    result = independent_merge.evaluate_merge_candidate(**evidence)

    assert result["eligible"] is False
    assert "merge_actor_is_not_repository_owner" in result["blockers"]
    assert "no_independent_approval_on_current_head" in result["blockers"]


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


def test_rerun_initiator_must_be_the_repository_owner() -> None:
    evidence = _candidate_evidence()
    evidence["merge_workflow_run"]["triggering_actor"] = {"login": "reviewer"}

    result = independent_merge.evaluate_merge_candidate(**evidence)

    assert result["eligible"] is False
    assert "merge_triggering_actor_is_not_repository_owner" in result["blockers"]


def test_candidate_branch_cannot_supply_the_merge_workflow_identity() -> None:
    evidence = _candidate_evidence()
    evidence["workflow_identity"].update(
        {
            "ref": "refs/heads/attacker-branch",
            "workflow_ref": (
                "owner/repo/.github/workflows/independent-pr-merge.yml"
                "@refs/heads/attacker-branch"
            ),
            "workflow_sha": "c" * 40,
            "actor": "attacker",
        }
    )
    evidence["merge_workflow_run"].update(
        {
            "head_branch": "attacker-branch",
            "head_sha": "c" * 40,
            "actor": {"login": "attacker"},
            # A branch copy may hard-code owner strings elsewhere, but the
            # server-reported triggering actor remains the attacker.
            "triggering_actor": {"login": "attacker"},
        }
    )

    result = independent_merge.evaluate_merge_candidate(**evidence)

    assert result["eligible"] is False
    assert "merge_workflow_identity_is_not_accepted_current_main" in result["blockers"]
    assert "merge_actor_is_not_repository_owner" in result["blockers"]


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
        "setup.py",
        "setuptools/__init__.py",
        "src/pip.py",
        "pytest.py",
        "pytest/__main__.py",
        "src/pytest.py",
        "pytest.ini",
        "pytest.toml",
        "tests/__init__.py",
        "tests/polymarket_predictive_engine/__init__.py",
        "pyproject.toml",
        "ruff.py",
        "ruff/__main__.py",
        "src/ruff.py",
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


def test_src_package_initializer_is_not_treated_as_merge_control() -> None:
    # The test-package __init__ protection must not over-reach into ordinary
    # source packages, or the lane could never merge a normal src change.
    evidence = _candidate_evidence()
    evidence["changed_files"] = [
        {"filename": "src/polymarket_predictive_engine/__init__.py"}
    ]

    result = independent_merge.evaluate_merge_candidate(**evidence)

    assert result["protected_control_changes"] == []
    assert "pull_request_changes_trusted_merge_control" not in result["blockers"]


def test_merge_workflow_identity_accepts_ref_suffixed_path() -> None:
    # Actions can report the workflow path with an @ref suffix; the
    # merge-identity check must normalize it, or a genuine default-branch run is
    # rejected as not-accepted-current-main and the lane can never merge.
    evidence = _candidate_evidence()
    evidence["merge_workflow_run"]["path"] = (
        f"{independent_merge.INDEPENDENT_WORKFLOW}@refs/heads/main"
    )

    result = independent_merge.evaluate_merge_candidate(**evidence)

    assert result["eligible"] is True
    assert (
        "merge_workflow_identity_is_not_accepted_current_main"
        not in result["blockers"]
    )


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


# --- WO-153 scope (a) — the deploy-control surface added to
# PROTECTED_CONTROL_PATHS. Twelve pre-existing entries plus sixteen new ones
# totals twenty-eight; both counts are hardcoded here (not read off the live
# module) so these tests fail if the registration is incomplete rather than
# trivially agreeing with whatever the module currently contains.

WO_153_PRE_EXISTING_PATHS = (
    ".github/workflows/required-pr-gate.yml",
    ".github/workflows/independent-pr-merge.yml",
    "scripts/audit_github_merge_gate.py",
    "scripts/merge_independently_reviewed_pr.py",
    "pyproject.toml",
    "pytest.ini",
    "pytest.toml",
    "setup.py",
    "setup.cfg",
    "sitecustomize.py",
    "tox.ini",
    "usercustomize.py",
)

WO_153_NEW_PATHS = (
    ".github/workflows/deploy_vps_paper_dispatch.yml",
    "scripts/deploy_vps_paper_manual.sh",
    "tests/test_deploy_vps_paper_dispatch_workflow.py",
    "scripts/preflight_vps_capacity.py",
    "scripts/validate_dashboard_private_transport.py",
    "scripts/update_vps_checkout_preserving_runtime.py",
    "scripts/rollback_vps_paper_deploy.py",
    "scripts/write_vps_telemetry_manifest.py",
    "scripts/check_polymarket_vps_paper.sh",
    "docker-compose.vps-paper.yml",
    "scripts/run_vps_deploy_acceptance.sh",
    "src/polymarket_predictive_engine/deploy_acceptance.py",
    "src/polymarket_predictive_engine/artifact_contracts.py",
    ".github/workflows/deploy-polymarket-vps-paper.yml",
    "scripts/verify_independent_main_acceptance.py",
    "scripts/configure_polymarket_dashboard_tailscale.sh",
)


def test_wo153_pre_existing_and_new_paths_do_not_overlap() -> None:
    assert set(WO_153_PRE_EXISTING_PATHS) & set(WO_153_NEW_PATHS) == set()
    assert len(WO_153_PRE_EXISTING_PATHS) == 12
    assert len(WO_153_NEW_PATHS) == 16


@pytest.mark.parametrize("path", WO_153_NEW_PATHS)
def test_wo153_new_deploy_control_paths_are_protected(path: str) -> None:
    # (1) `_protected_control_path` returns True for each of the sixteen new
    # registered paths.
    assert independent_merge._protected_control_path(path) is True


@pytest.mark.parametrize("path", WO_153_NEW_PATHS)
def test_wo153_new_deploy_control_paths_survive_normalisation(path: str) -> None:
    # (2) it returns True for each of the same sixteen paths in the forms
    # `./<path>` and `/<path>` and with backslash separators, exercising the
    # existing normaliser.
    assert independent_merge._protected_control_path(f"./{path}") is True
    assert independent_merge._protected_control_path(f"/{path}") is True
    assert independent_merge._protected_control_path(path.replace("/", "\\")) is True


@pytest.mark.parametrize("path", WO_153_NEW_PATHS)
def test_wo153_new_deploy_control_path_alone_reports_protected_change(path: str) -> None:
    # (3) + (4): a simulated PR touching only one of the sixteen new paths —
    # the dispatch workflow, the test file, the deploy script, the acceptance
    # script, the acceptance module, the contract-registry module, the Path A
    # workflow, the Path A acceptance-verifier script, and the Path A
    # tailnet-transport script, each in turn via this parametrisation —
    # reports `trusted_merge_control_unchanged: false` and lists that path in
    # `protected_control_changes`.
    evidence = _candidate_evidence()
    evidence["changed_files"] = [{"filename": path}]

    result = independent_merge.evaluate_merge_candidate(**evidence)

    assert result["eligible"] is False
    assert result["checks"]["trusted_merge_control_unchanged"] is False
    assert result["protected_control_changes"] == [path]
    assert "pull_request_changes_trusted_merge_control" in result["blockers"]


@pytest.mark.parametrize("path", WO_153_PRE_EXISTING_PATHS)
def test_wo153_pre_existing_protected_paths_still_protected(path: str) -> None:
    # (5) every path in PROTECTED_CONTROL_PATHS before this change still
    # returns True — the tightening-only claim, asserted rather than argued.
    assert independent_merge._protected_control_path(path) is True


def test_wo153_path_outside_the_set_is_still_unprotected() -> None:
    # (6) a path outside the set still returns False, so the widening did
    # not become a prefix match.
    assert (
        independent_merge._protected_control_path(
            "src/polymarket_predictive_engine/cli.py"
        )
        is False
    )


def test_wo153_widening_is_strictly_more_protective_never_less() -> None:
    # Scope (a) is strictly tightening: the predicate flags strictly more
    # paths as protected after this change, and every path it flagged before
    # this change it still flags — never fewer.
    pre = set(WO_153_PRE_EXISTING_PATHS)
    new = set(WO_153_NEW_PATHS)
    assert independent_merge.PROTECTED_CONTROL_PATHS == pre | new
    assert len(independent_merge.PROTECTED_CONTROL_PATHS) == 28
    for path in pre:
        assert independent_merge._protected_control_path(path) is True
    for path in new:
        assert independent_merge._protected_control_path(path) is True
    # A path this WO does not register, and did not register before it,
    # stays unprotected both before and after — the widening adds, it does
    # not also silently narrow the unprotected set into a false match.
    assert (
        independent_merge._protected_control_path(
            "src/polymarket_predictive_engine/cli.py"
        )
        is False
    )


def _wo153_stage_target_scripts_helpers(script_text: str) -> list[str]:
    match = re.search(r"for helper in(?P<body>.*?)\n\s*do\b", script_text, re.DOTALL)
    assert match, "stage_target_scripts helper list not found in deploy script"
    names = [token.rstrip("\\") for token in match.group("body").split()]
    names = [token for token in names if token]
    assert names, "stage_target_scripts helper list parsed to zero entries"
    return [f"scripts/{name}" for name in names]


def _wo153_compose_file_default(script_text: str) -> str:
    match = re.search(r'COMPOSE_FILE="\$\{PM_COMPOSE_FILE:-([^}]+)\}"', script_text)
    assert match, "COMPOSE_FILE default not found in deploy script"
    return match.group(1)


def _wo153_compose_service_block(compose_text: str, service_name: str) -> str:
    lines = compose_text.splitlines()
    start = next(
        i
        for i, line in enumerate(lines)
        if line.strip() == f"{service_name}:" and re.match(r"^ {2}\S", line)
    )
    body: list[str] = []
    for line in lines[start + 1 :]:
        if re.match(r"^ {2}\S", line):
            break
        body.append(line)
    return "\n".join(body)


def test_wo153_deploy_script_closure_chain_is_fully_registered() -> None:
    # (7) a closure test that does not merely re-enumerate the six staged
    # scripts by hand but parses `scripts/deploy_vps_paper_manual.sh`'s own
    # text — the `for helper in ...` list inside `stage_target_scripts` and
    # the `COMPOSE_FILE` default — and parses the resulting compose file's
    # `vps-deploy-acceptance` service `command:` line to find
    # `scripts/run_vps_deploy_acceptance.sh`, and that script's own
    # `deploy-acceptance` CLI invocation to resolve
    # `src/polymarket_predictive_engine/deploy_acceptance.py`, and that
    # module's own `from .artifact_contracts import ...` line to resolve
    # `src/polymarket_predictive_engine/artifact_contracts.py` — asserting
    # every path the parse produces is a member of PROTECTED_CONTROL_PATHS.
    deploy_script = (ROOT / "scripts" / "deploy_vps_paper_manual.sh").read_text(
        encoding="utf-8"
    )
    helpers = _wo153_stage_target_scripts_helpers(deploy_script)
    compose_file = _wo153_compose_file_default(deploy_script)

    compose_text = (ROOT / compose_file).read_text(encoding="utf-8")
    service_block = _wo153_compose_service_block(compose_text, "vps-deploy-acceptance")
    command_match = re.search(r"^\s*command:\s*(.+)$", service_block, re.MULTILINE)
    assert command_match, "vps-deploy-acceptance command: line not found"
    acceptance_script_match = re.search(r"scripts/\S+\.sh", command_match.group(1))
    assert acceptance_script_match, "acceptance script invocation not found"
    acceptance_script = acceptance_script_match.group(0)

    acceptance_script_text = (ROOT / acceptance_script).read_text(encoding="utf-8")
    assert "cli deploy-acceptance" in acceptance_script_text, (
        "acceptance script no longer invokes the deploy-acceptance CLI command"
    )
    deploy_acceptance_module = "src/polymarket_predictive_engine/deploy_acceptance.py"

    deploy_acceptance_text = (ROOT / deploy_acceptance_module).read_text(encoding="utf-8")
    assert (
        "from .artifact_contracts import build_contract_registry, validate_contract_fixture"
        in deploy_acceptance_text
    ), "deploy_acceptance.py no longer imports the contract-registry module"
    artifact_contracts_module = "src/polymarket_predictive_engine/artifact_contracts.py"

    resolved = set(helpers) | {
        compose_file,
        acceptance_script,
        deploy_acceptance_module,
        artifact_contracts_module,
    }
    assert len(resolved) >= 10, resolved  # non-zero, exhaustive visit count (A3)
    for path in sorted(resolved):
        assert independent_merge._protected_control_path(path) is True, path


def test_wo153_cli_deploy_acceptance_dispatch_is_not_retargeted() -> None:
    # (8) closes a visibility gap in test (7)'s own resolution step: test (7)
    # resolves the `deploy-acceptance` CLI invocation to
    # `src/polymarket_predictive_engine/deploy_acceptance.py` by name, not by
    # reading `cli.py`'s own dispatch table. `cli.py` is not, and is not
    # made, a member of PROTECTED_CONTROL_PATHS (registering it would flag
    # every unrelated command's routine maintenance as a protected-control
    # change). This test instead parses `cli.py`'s own source text for the
    # exact `elif args.command == "deploy-acceptance":` branch and asserts it
    # still calls `build_deploy_acceptance`, imported from `.deploy_acceptance`
    # — so a retargeted branch trips this test directly without registering
    # `cli.py` itself.
    cli_text = (ROOT / "src" / "polymarket_predictive_engine" / "cli.py").read_text(
        encoding="utf-8"
    )
    assert "from .deploy_acceptance import build_deploy_acceptance" in cli_text, (
        "cli.py no longer imports build_deploy_acceptance from .deploy_acceptance"
    )
    branch_match = re.search(
        r'elif args\.command == "deploy-acceptance":\s*\n\s*_print\(build_deploy_acceptance\(cfg\)\)',
        cli_text,
    )
    assert branch_match, (
        "the deploy-acceptance dispatch branch no longer calls build_deploy_acceptance directly"
    )


def test_wo153_path_a_one_hop_helpers_are_registered() -> None:
    # (9) closes the same drift gap in Path A that test (7) closes in Path B:
    # hand-enumeration in (1)-(4) proves the predicate accepts the two Path A
    # one-hop helper paths once given their names; it does not prove the
    # workflow still calls those two names. This test parses
    # `.github/workflows/deploy-polymarket-vps-paper.yml`'s own text for its
    # two one-hop helper invocations — the
    # `verify_independent_main_acceptance.py` line in the "Bind deployment to
    # independently accepted main" step, and the
    # `configure_polymarket_dashboard_tailscale.sh` line in the tailnet
    # enforcement step — and asserts the exact two-element set it resolves to
    # is a subset of PROTECTED_CONTROL_PATHS.
    workflow_text = (
        ROOT / ".github" / "workflows" / "deploy-polymarket-vps-paper.yml"
    ).read_text(encoding="utf-8")

    verify_match = re.search(
        r"deploy-control/(scripts/verify_independent_main_acceptance\.py)",
        workflow_text,
    )
    tailscale_match = re.search(
        r"bash (scripts/configure_polymarket_dashboard_tailscale\.sh)",
        workflow_text,
    )
    assert verify_match, "verify_independent_main_acceptance.py invocation not found"
    assert tailscale_match, "configure_polymarket_dashboard_tailscale.sh invocation not found"

    resolved = {verify_match.group(1), tailscale_match.group(1)}
    assert resolved == {
        "scripts/verify_independent_main_acceptance.py",
        "scripts/configure_polymarket_dashboard_tailscale.sh",
    }
    assert resolved <= independent_merge.PROTECTED_CONTROL_PATHS
