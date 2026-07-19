#!/usr/bin/env python3
"""Audit and, when explicitly requested, configure the WO-69 merge gate.

This operator utility uses the authenticated GitHub CLI. It never reads repo
secrets and writes a fail-closed status artifact consumed by WO-68.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from fnmatch import fnmatchcase
import json
from pathlib import Path
import subprocess
from typing import Any, Mapping, Sequence


REQUIRED_CONTEXT = "WO-69 guard and invariants"
REQUIRED_RUNNER_LABELS = {"self-hosted", "Linux", "ARM64", "polymarket-ci"}
REQUIRED_FULL_SUITE_COMMAND = "/tmp/ci-venv/bin/python -m pytest -q"
INDEPENDENT_MERGE_WORKFLOW = Path(".github/workflows/independent-pr-merge.yml")
DEFAULT_OUTPUT = Path("outputs/performance/independent_merge_gate.json")


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, Mapping):
        return bool(value.get("enabled"))
    return False


def _top_level_on_block(text: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != "on:":
            continue
        block = [line]
        for candidate in lines[index + 1 :]:
            if candidate and not candidate.startswith((" ", "\t")) and not candidate.lstrip().startswith("#"):
                break
            block.append(candidate)
        return "\n".join(block)
    return ""


def workflow_is_configured(workflow_text: str) -> bool:
    on_block = _top_level_on_block(workflow_text)
    required_fragments = {
        "pull_request:",
        "types: [opened, synchronize, reopened, ready_for_review]",
        f"name: {REQUIRED_CONTEXT}",
        "self-hosted",
        "Linux",
        "ARM64",
        "polymarket-ci",
        "name: Run complete isolated repository gate",
        "docker run --rm --cpus 2 --memory 4g --pids-limit 512",
        "path: proposed-merge",
        "fetch-depth: 0",
        '${GITHUB_WORKSPACE}/proposed-merge:/src:ro',
        "python:3.11-slim",
        "apt-get install -y -qq --no-install-recommends git ca-certificates",
        "ruff check .",
        "config-check --config polymarket_predictive_config.example.yaml",
    }
    pytest_lines = [
        line.strip()
        for line in workflow_text.splitlines()
        if "pytest" in line and not line.lstrip().startswith("#")
    ]
    checkout_step = workflow_text.find("name: Check out proposed merge")
    full_suite_step = workflow_text.find("name: Run complete isolated repository gate")
    return (
        "pull_request:" in on_block
        and all(fragment in workflow_text for fragment in required_fragments)
        and 0 <= checkout_step < full_suite_step
        and pytest_lines == [REQUIRED_FULL_SUITE_COMMAND]
        and "continue-on-error:" not in workflow_text
        and "    container:" not in workflow_text
    )


def independent_merge_workflow_is_configured(workflow_text: str) -> bool:
    """Return whether the registered second-identity merge lane is fail closed."""

    on_block = _top_level_on_block(workflow_text)
    required_fragments = {
        "workflow_dispatch:",
        "expected_head_sha:",
        "actions: read",
        "checks: read",
        "contents: write",
        "pull-requests: write",
        "runs-on: [self-hosted, Linux, ARM64, polymarket-ci]",
        "path: merge-utility",
        "fetch-depth: 0",
        "MERGE_ACTOR: ${{ github.actor }}",
        "MERGE_TRIGGERING_ACTOR: ${{ github.triggering_actor }}",
        "MERGE_EVENT: ${{ github.event_name }}",
        "MERGE_RUN_ID: ${{ github.run_id }}",
        "scripts/merge_independently_reviewed_pr.py",
        "--expected-head",
        "--merge",
    }
    return (
        "workflow_dispatch:" in on_block
        and "pull_request:" not in on_block
        and all(fragment in workflow_text for fragment in required_fragments)
        and "continue-on-error:" not in workflow_text
    )


def branch_protection_payload() -> dict[str, Any]:
    """Return the strongest available legacy branch-protection policy.

    GitHub's legacy required-status API binds a context (and optionally an app),
    not the workflow file that produced it.  This payload is useful for review,
    stale-approval, admin, and direct-push controls, but the audit must never
    classify its name-only status context as workflow-identity enforcement.
    """
    return {
        "required_status_checks": {"strict": True, "contexts": [REQUIRED_CONTEXT]},
        "enforce_admins": True,
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": True,
            "require_code_owner_reviews": False,
            "required_approving_review_count": 1,
            "require_last_push_approval": True,
            "bypass_pull_request_allowances": {"users": [], "teams": [], "apps": []},
        },
        "restrictions": None,
        "required_linear_history": True,
        "allow_force_pushes": False,
        "allow_deletions": False,
        "block_creations": False,
        "required_conversation_resolution": True,
        "lock_branch": False,
        "allow_fork_syncing": True,
    }


def _required_contexts(protection: Mapping[str, Any]) -> set[str]:
    status_checks = _mapping(protection.get("required_status_checks"))
    contexts = {str(value) for value in status_checks.get("contexts", []) if value}
    for row in status_checks.get("checks", []) or []:
        context = _mapping(row).get("context")
        if context:
            contexts.add(str(context))
    return contexts


def _bypass_count(reviews: Mapping[str, Any]) -> int:
    allowances = _mapping(reviews.get("bypass_pull_request_allowances"))
    return sum(len(allowances.get(key, []) or []) for key in ("users", "teams", "apps"))


def _ruleset_targets_main(ruleset: Mapping[str, Any]) -> bool:
    if str(ruleset.get("enforcement") or "").lower() != "active":
        return False
    if str(ruleset.get("target") or "branch").lower() != "branch":
        return False
    ref_name = _mapping(_mapping(ruleset.get("conditions")).get("ref_name"))
    includes = [str(value) for value in ref_name.get("include", []) or []]
    excludes = [str(value) for value in ref_name.get("exclude", []) or []]

    def matches(pattern: str) -> bool:
        return pattern in {"~ALL", "~DEFAULT_BRANCH"} or fnmatchcase(
            "refs/heads/main", pattern
        )

    return any(matches(pattern) for pattern in includes) and not any(
        matches(pattern) for pattern in excludes
    )


def _ruleset_requires_registered_workflow(
    ruleset: Mapping[str, Any],
    *,
    repository_id: int | None,
    accepted_workflow_sha: str,
) -> bool:
    if not _ruleset_targets_main(ruleset) or (ruleset.get("bypass_actors") or []):
        return False
    accepted_sha = str(accepted_workflow_sha or "").strip().lower()
    if repository_id is None or len(accepted_sha) != 40 or any(
        char not in "0123456789abcdef" for char in accepted_sha
    ):
        return False
    for raw_rule in ruleset.get("rules", []) or []:
        rule = _mapping(raw_rule)
        if str(rule.get("type") or "").lower() != "workflows":
            continue
        workflows = _mapping(rule.get("parameters")).get("workflows", []) or []
        for raw_workflow in workflows:
            workflow = _mapping(raw_workflow)
            path = str(workflow.get("path") or "").split("@", 1)[0]
            ref = str(workflow.get("ref") or "refs/heads/main")
            workflow_sha = str(workflow.get("sha") or "").strip().lower()
            try:
                workflow_repository_id = int(workflow.get("repository_id"))
            except (TypeError, ValueError):
                workflow_repository_id = None
            if (
                path == str(INDEPENDENT_MERGE_WORKFLOW.with_name("required-pr-gate.yml"))
                and ref in {"main", "refs/heads/main"}
                and workflow_repository_id == repository_id
                and workflow_sha == accepted_sha
            ):
                return True
    return False


def evaluate_merge_gate(
    *,
    workflow_text: str,
    runners_payload: Mapping[str, Any] | None,
    protection_payload: Mapping[str, Any] | None,
    protection_error: str = "",
    runs_payload: Mapping[str, Any] | None = None,
    independent_merge_workflow_text: str = "",
    collaborators_payload: Sequence[Mapping[str, Any]] | None = None,
    rulesets_payload: Sequence[Mapping[str, Any]] | None = None,
    rulesets_error: str = "",
    repository_id: int | None = None,
    accepted_workflow_sha: str = "",
) -> dict[str, Any]:
    workflow_configured = workflow_is_configured(workflow_text)
    runners = (_mapping(runners_payload).get("runners") or []) if runners_payload else []
    matching_runners: list[dict[str, Any]] = []
    for raw in runners:
        runner = _mapping(raw)
        labels = {str(_mapping(label).get("name")) for label in runner.get("labels", []) or []}
        if REQUIRED_RUNNER_LABELS <= labels:
            matching_runners.append(runner)
    runner_online = any(str(row.get("status") or "").lower() == "online" for row in matching_runners)

    protection = _mapping(protection_payload)
    branch_protection_enabled = bool(protection)
    status_checks = _mapping(protection.get("required_status_checks"))
    strict_status_checks = status_checks.get("strict") is True
    legacy_required_context_registered = (
        REQUIRED_CONTEXT in _required_contexts(protection) and strict_status_checks
    )
    # Legacy status checks are name/app-bound, not workflow-file-bound. Only an
    # active, no-bypass ruleset targeting main and naming the exact accepted
    # workflow can satisfy workflow identity.
    matching_required_workflow_rulesets = [
        _mapping(row)
        for row in rulesets_payload or []
        if _ruleset_requires_registered_workflow(
            _mapping(row),
            repository_id=repository_id,
            accepted_workflow_sha=accepted_workflow_sha,
        )
    ]
    required_workflow_identity_enforced = bool(matching_required_workflow_rulesets)
    reviews = _mapping(protection.get("required_pull_request_reviews"))
    required_review_count = int(reviews.get("required_approving_review_count") or 0)
    stale_reviews_dismissed = reviews.get("dismiss_stale_reviews") is True
    last_push_approval_required = reviews.get("require_last_push_approval") is True
    independent_review_required = required_review_count >= 1 and stale_reviews_dismissed and last_push_approval_required
    conversation_resolution_required = _enabled(protection.get("required_conversation_resolution"))
    admins_enforced = _enabled(protection.get("enforce_admins"))
    bypass_count = _bypass_count(reviews)
    direct_push_forbidden = (
        branch_protection_enabled
        and independent_review_required
        and conversation_resolution_required
        and admins_enforced
        and bypass_count == 0
    )

    workflow_runs = [_mapping(row) for row in (_mapping(runs_payload).get("workflow_runs") or [])] if runs_payload else []
    latest_run = workflow_runs[0] if workflow_runs else {}
    active_run_count = sum(str(row.get("status") or "").lower() in {"queued", "in_progress", "waiting", "requested"} for row in workflow_runs)
    latest_gate_success = (
        str(latest_run.get("event") or "") == "pull_request"
        and str(latest_run.get("status") or "") == "completed"
        and str(latest_run.get("conclusion") or "") == "success"
    )
    latest_successful_run = latest_run if latest_gate_success else {}

    independent_process_configured = independent_merge_workflow_is_configured(independent_merge_workflow_text)
    push_capable_collaborators = sorted(
        str(_mapping(row).get("login") or "")
        for row in collaborators_payload or []
        if _mapping(_mapping(row).get("permissions")).get("push") is True and _mapping(row).get("login")
    )
    independent_identity_available = len({login.casefold() for login in push_capable_collaborators}) >= 2
    independent_process_operational = (
        independent_process_configured
        and independent_identity_available
        and runner_online
        and latest_gate_success
    )

    combined_platform_error = "\n".join(
        part for part in (protection_error, rulesets_error) if part
    )
    plan_blocked = (
        "upgrade to github pro" in combined_platform_error.lower()
        or "make this repository public" in combined_platform_error.lower()
    )
    checks = {
        "workflow_configured": workflow_configured,
        "independent_merge_process_configured": independent_process_configured,
        "runner_registered": bool(matching_runners),
        "runner_online": runner_online,
        "latest_gate_success": latest_gate_success,
        "branch_protection_enabled": branch_protection_enabled,
        "required_workflow_identity_enforced": required_workflow_identity_enforced,
        "independent_review_required": independent_review_required,
        "stale_reviews_dismissed": stale_reviews_dismissed,
        "last_push_approval_required": last_push_approval_required,
        "conversation_resolution_required": conversation_resolution_required,
        "admin_enforcement_enabled": admins_enforced,
        "direct_push_forbidden": direct_push_forbidden,
        "review_bypass_count_zero": bypass_count == 0,
    }
    enforced = all(checks.values())
    blockers = [name for name, passed in checks.items() if not passed]
    if enforced:
        status = "enforced"
    elif independent_process_operational:
        status = "documented_independent_process_only"
    elif plan_blocked and not independent_identity_available:
        status = "blocked_github_plan_and_independent_identity"
    elif branch_protection_enabled and legacy_required_context_registered:
        status = "blocked_legacy_status_not_workflow_bound"
    else:
        status = "incomplete"
    return {
        "status": status,
        "enforced": enforced,
        "required_check_context": REQUIRED_CONTEXT,
        "branch_protection_enabled": branch_protection_enabled,
        # Backward-compatible field consumed only as a fail-closed boolean.
        "required_check_enforced": required_workflow_identity_enforced,
        "legacy_required_context_registered": legacy_required_context_registered,
        "required_workflow_identity_enforced": required_workflow_identity_enforced,
        "required_workflow_ruleset_ids": [
            row.get("id") for row in matching_required_workflow_rulesets
        ],
        "expected_required_workflow_repository_id": repository_id,
        "accepted_required_workflow_sha": accepted_workflow_sha,
        "independent_review_required": independent_review_required,
        "direct_push_forbidden": direct_push_forbidden,
        "independent_merge_process_configured": independent_process_configured,
        "independent_identity_available": independent_identity_available,
        "independent_process_operational": independent_process_operational,
        "push_capable_collaborators": push_capable_collaborators,
        "runner_online": runner_online,
        "runner_names": [str(row.get("name") or "") for row in matching_runners],
        "latest_gate_run": {
            "id": latest_run.get("id"),
            "event": latest_run.get("event"),
            "status": latest_run.get("status"),
            "conclusion": latest_run.get("conclusion"),
            "html_url": latest_run.get("html_url"),
        },
        "latest_successful_gate_run": {
            "id": latest_successful_run.get("id"),
            "event": latest_successful_run.get("event"),
            "status": latest_successful_run.get("status"),
            "conclusion": latest_successful_run.get("conclusion"),
            "html_url": latest_successful_run.get("html_url"),
        },
        "active_gate_run_count": active_run_count,
        "checks": checks,
        "blockers": blockers,
        "platform_blocker": combined_platform_error.strip() if plan_blocked else "",
        "legacy_status_context_risk": (
            "GitHub legacy required-status checks do not bind the check name to "
            ".github/workflows/required-pr-gate.yml; "
            "a same-named GitHub Actions job is not accepted as merge proof."
            if legacy_required_context_registered
            else ""
        ),
        "owner_action": (
            "Upgrade to a workflow-identity-capable required-workflow ruleset, or add "
            "a second push-capable reviewer and use only the registered independent "
            "merge workflow; never use the normal merge path from a legacy green context."
            if plan_blocked and not independent_identity_available
            else "Use only the registered independent merge workflow until a "
            "workflow-identity-capable required-workflow ruleset is verified."
            if independent_process_operational or legacy_required_context_registered
            else ("None" if enforced else "Resolve every named blocker before live capital.")
        ),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }


def _gh_api(path: str, *, method: str = "GET", payload: Mapping[str, Any] | None = None) -> tuple[Any, str]:
    command = ["gh", "api", path, "--method", method, "-H", "Accept: application/vnd.github+json", "-H", "X-GitHub-Api-Version: 2022-11-28"]
    input_text = None
    if payload is not None:
        command.extend(["--input", "-"])
        input_text = json.dumps(payload)
    completed = subprocess.run(command, input=input_text, text=True, capture_output=True, check=False)
    parsed: Any = {}
    if completed.returncode == 0 and completed.stdout.strip():
        try:
            candidate = json.loads(completed.stdout)
            parsed = candidate if isinstance(candidate, (dict, list)) else {}
        except json.JSONDecodeError:
            parsed = {}
    error = "\n".join(part for part in (completed.stdout.strip() if completed.returncode else "", completed.stderr.strip()) if part)
    return parsed, error


def audit(repo: str, workflow_path: Path, *, apply_protection: bool = False) -> dict[str, Any]:
    if apply_protection:
        _, apply_error = _gh_api(f"repos/{repo}/branches/main/protection", method="PUT", payload=branch_protection_payload())
    else:
        apply_error = ""
    runners, runners_error = _gh_api(f"repos/{repo}/actions/runners")
    protection, protection_error = _gh_api(f"repos/{repo}/branches/main/protection")
    runs, runs_error = _gh_api(f"repos/{repo}/actions/workflows/required-pr-gate.yml/runs?event=pull_request&per_page=20")
    collaborators, collaborators_error = _gh_api(f"repos/{repo}/collaborators?affiliation=all&per_page=100")
    repository, repository_error = _gh_api(f"repos/{repo}")
    workflow_revisions, workflow_revision_error = _gh_api(
        f"repos/{repo}/commits?path=.github/workflows/required-pr-gate.yml&sha=main&per_page=1"
    )
    repository_id: int | None = None
    try:
        repository_id = int(_mapping(repository).get("id"))
    except (TypeError, ValueError):
        pass
    accepted_workflow_sha = ""
    if isinstance(workflow_revisions, list) and workflow_revisions:
        accepted_workflow_sha = str(_mapping(workflow_revisions[0]).get("sha") or "")
    ruleset_summaries, rulesets_error = _gh_api(
        f"repos/{repo}/rulesets?includes_parents=true&per_page=100"
    )
    rulesets: list[dict[str, Any]] = []
    ruleset_detail_errors: list[str] = []
    if isinstance(ruleset_summaries, list):
        for raw_summary in ruleset_summaries:
            ruleset_id = _mapping(raw_summary).get("id")
            if not ruleset_id:
                ruleset_detail_errors.append("ruleset summary missing id")
                continue
            detail, detail_error = _gh_api(f"repos/{repo}/rulesets/{ruleset_id}")
            if detail_error:
                ruleset_detail_errors.append(f"ruleset {ruleset_id}: {detail_error}")
            elif isinstance(detail, Mapping):
                rulesets.append(_mapping(detail))
    independent_workflow_path = workflow_path.with_name(INDEPENDENT_MERGE_WORKFLOW.name)
    result = evaluate_merge_gate(
        workflow_text=workflow_path.read_text(encoding="utf-8"),
        runners_payload=_mapping(runners),
        protection_payload=_mapping(protection),
        protection_error="\n".join(part for part in (apply_error, protection_error) if part),
        runs_payload=_mapping(runs),
        independent_merge_workflow_text=(
            independent_workflow_path.read_text(encoding="utf-8") if independent_workflow_path.exists() else ""
        ),
        collaborators_payload=collaborators if isinstance(collaborators, list) else [],
        rulesets_payload=rulesets,
        rulesets_error="\n".join(
            part
            for part in (
                rulesets_error,
                *ruleset_detail_errors,
                repository_error,
                workflow_revision_error,
            )
            if part
        ),
        repository_id=repository_id,
        accepted_workflow_sha=accepted_workflow_sha,
    )
    result.update(
        {
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "repository": repo,
            "workflow_path": str(workflow_path),
            "query_errors": {
                "runners": runners_error,
                "workflow_runs": runs_error,
                "collaborators": collaborators_error,
                "repository_identity": repository_error,
                "accepted_workflow_revision": workflow_revision_error,
                "rulesets": "\n".join(
                    part for part in (rulesets_error, *ruleset_detail_errors) if part
                ),
            },
            "protection_apply_attempted": apply_protection,
        }
    )
    return result


def write_artifact(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="daniedorfling18-maker/Claude")
    parser.add_argument("--workflow", type=Path, default=Path(".github/workflows/required-pr-gate.yml"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--apply-protection", action="store_true", help="Attempt the registered main-branch protection policy before auditing.")
    args = parser.parse_args()
    result = audit(args.repo, args.workflow, apply_protection=args.apply_protection)
    write_artifact(args.output, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["enforced"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
