#!/usr/bin/env python3
"""Audit and, when explicitly requested, configure the WO-69 merge gate.

This operator utility uses the authenticated GitHub CLI. It never reads repo
secrets and writes a fail-closed status artifact consumed by WO-68.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from typing import Any, Mapping


REQUIRED_CONTEXT = "WO-69 guard and invariants"
REQUIRED_RUNNER_LABELS = {"self-hosted", "Windows", "X64", "polymarket-ci"}
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
        f"name: {REQUIRED_CONTEXT}",
        "self-hosted",
        "Windows",
        "X64",
        "polymarket-ci",
        "test_operating_state.py",
        "test_execution_governance_storage.py",
        "test_safety_invariants.py",
        "test_polymarket_vps_docker.py",
        "ruff check .",
        "config-check --config polymarket_predictive_config.example.yaml",
    }
    return "pull_request:" in on_block and all(fragment in workflow_text for fragment in required_fragments)


def branch_protection_payload() -> dict[str, Any]:
    """Return the exact fail-closed protection policy registered by WO-69."""
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


def evaluate_merge_gate(
    *,
    workflow_text: str,
    runners_payload: Mapping[str, Any] | None,
    protection_payload: Mapping[str, Any] | None,
    protection_error: str = "",
    runs_payload: Mapping[str, Any] | None = None,
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
    required_check_enforced = REQUIRED_CONTEXT in _required_contexts(protection)
    reviews = _mapping(protection.get("required_pull_request_reviews"))
    required_review_count = int(reviews.get("required_approving_review_count") or 0)
    independent_review_required = required_review_count >= 1
    admins_enforced = _enabled(protection.get("enforce_admins"))
    bypass_count = _bypass_count(reviews)
    direct_push_forbidden = branch_protection_enabled and independent_review_required and admins_enforced and bypass_count == 0

    workflow_runs = (_mapping(runs_payload).get("workflow_runs") or []) if runs_payload else []
    latest_run = _mapping(workflow_runs[0]) if workflow_runs else {}
    latest_gate_success = (
        str(latest_run.get("event") or "") == "pull_request"
        and str(latest_run.get("conclusion") or "") == "success"
    )

    plan_blocked = "upgrade to github pro" in protection_error.lower() or "make this repository public" in protection_error.lower()
    checks = {
        "workflow_configured": workflow_configured,
        "runner_registered": bool(matching_runners),
        "runner_online": runner_online,
        "latest_gate_success": latest_gate_success,
        "branch_protection_enabled": branch_protection_enabled,
        "required_check_enforced": required_check_enforced,
        "independent_review_required": independent_review_required,
        "admin_enforcement_enabled": admins_enforced,
        "direct_push_forbidden": direct_push_forbidden,
        "review_bypass_count_zero": bypass_count == 0,
    }
    enforced = all(checks.values())
    blockers = [name for name, passed in checks.items() if not passed]
    status = "enforced" if enforced else ("blocked_github_plan" if plan_blocked else "incomplete")
    return {
        "status": status,
        "enforced": enforced,
        "required_check_context": REQUIRED_CONTEXT,
        "branch_protection_enabled": branch_protection_enabled,
        "required_check_enforced": required_check_enforced,
        "independent_review_required": independent_review_required,
        "direct_push_forbidden": direct_push_forbidden,
        "runner_online": runner_online,
        "runner_names": [str(row.get("name") or "") for row in matching_runners],
        "latest_gate_run": {
            "id": latest_run.get("id"),
            "event": latest_run.get("event"),
            "status": latest_run.get("status"),
            "conclusion": latest_run.get("conclusion"),
            "html_url": latest_run.get("html_url"),
        },
        "checks": checks,
        "blockers": blockers,
        "platform_blocker": protection_error.strip() if plan_blocked else "",
        "owner_action": (
            "Upgrade the private repository to GitHub Pro/Team, then rerun this utility with --apply-protection."
            if plan_blocked
            else ("None" if enforced else "Resolve every named blocker before live capital.")
        ),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }


def _gh_api(path: str, *, method: str = "GET", payload: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], str]:
    command = ["gh", "api", path, "--method", method, "-H", "Accept: application/vnd.github+json", "-H", "X-GitHub-Api-Version: 2022-11-28"]
    input_text = None
    if payload is not None:
        command.extend(["--input", "-"])
        input_text = json.dumps(payload)
    completed = subprocess.run(command, input=input_text, text=True, capture_output=True, check=False)
    parsed: dict[str, Any] = {}
    if completed.returncode == 0 and completed.stdout.strip():
        try:
            parsed = _mapping(json.loads(completed.stdout))
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
    runs, runs_error = _gh_api(f"repos/{repo}/actions/workflows/required-pr-gate.yml/runs?event=pull_request&per_page=1")
    result = evaluate_merge_gate(
        workflow_text=workflow_path.read_text(encoding="utf-8"),
        runners_payload=runners,
        protection_payload=protection,
        protection_error="\n".join(part for part in (apply_error, protection_error) if part),
        runs_payload=runs,
    )
    result.update(
        {
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "repository": repo,
            "workflow_path": str(workflow_path),
            "query_errors": {"runners": runners_error, "workflow_runs": runs_error},
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
