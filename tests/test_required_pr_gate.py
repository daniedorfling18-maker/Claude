from __future__ import annotations

import importlib.util
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "audit_github_merge_gate.py"
SPEC = importlib.util.spec_from_file_location("audit_github_merge_gate", MODULE_PATH)
assert SPEC and SPEC.loader
merge_gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(merge_gate)


def _workflow() -> str:
    return (ROOT / ".github" / "workflows" / "required-pr-gate.yml").read_text(encoding="utf-8")


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
        "required_status_checks": {"contexts": [merge_gate.REQUIRED_CONTEXT]},
        "enforce_admins": {"enabled": True},
        "required_pull_request_reviews": {
            "required_approving_review_count": 1,
            "bypass_pull_request_allowances": {"users": [], "teams": [], "apps": []},
        },
    }


def _successful_run() -> dict:
    return {
        "workflow_runs": [
            {"id": 123, "event": "pull_request", "status": "completed", "conclusion": "success", "html_url": "https://example.test/run/123"}
        ]
    }


def test_registered_workflow_is_minimal_self_hosted_and_secretless() -> None:
    workflow = _workflow()
    assert merge_gate.workflow_is_configured(workflow)
    assert "pull_request:" in workflow
    assert "types: [opened, synchronize, reopened]" in workflow
    assert "ready_for_review" not in workflow
    assert "runs-on: [self-hosted, Linux, ARM64, polymarket-ci]" in workflow
    assert "timeout-minutes: 15" in workflow
    assert "image: python:3.11-slim" in workflow
    assert "options: --cpus 2 --memory 4g" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "secrets." not in workflow
    assert "actions/setup-python" not in workflow
    assert "shell: powershell" not in workflow
    assert workflow.count("shell: bash") == 6
    assert "Provision Git for deploy preservation tests" in workflow
    assert "apt-get install -y --no-install-recommends git" in workflow
    assert "python -m venv .ci-venv" in workflow
    assert "pip install --disable-pip-version-check -e" in workflow
    assert "pip check" in workflow
    assert "pytest -q" in workflow
    assert "test_wo73_controls.py" in workflow
    assert "test_executor_replay_certification.py" in workflow
    assert "test_executor_ops_monitor.py" in workflow
    assert "test_deploy_acceptance.py" in workflow
    assert "test_collection_hygiene.py" in workflow
    assert "test_degraded_state_watchdog.py" in workflow
    assert "test_operating_state.py" in workflow
    assert "test_safety_invariants.py" in workflow
    assert "test_polymarket_vps_docker.py" in workflow
    assert "test_vps_checkout_update.py" in workflow
    assert "ruff check ." in workflow
    assert "config-check --config polymarket_predictive_config.example.yaml" in workflow


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


def test_merge_gate_is_enforced_only_when_every_control_is_proven() -> None:
    result = merge_gate.evaluate_merge_gate(
        workflow_text=_workflow(),
        runners_payload=_runners(),
        protection_payload=_protection(),
        runs_payload=_successful_run(),
    )
    assert result["status"] == "enforced"
    assert result["enforced"] is True
    assert result["branch_protection_enabled"] is True
    assert result["required_check_enforced"] is True
    assert result["independent_review_required"] is True
    assert result["direct_push_forbidden"] is True
    assert result["blockers"] == []


def test_private_free_plan_blocker_stays_explicit_and_fail_closed() -> None:
    result = merge_gate.evaluate_merge_gate(
        workflow_text=_workflow(),
        runners_payload=_runners(),
        protection_payload={},
        protection_error="Upgrade to GitHub Pro or make this repository public to enable this feature.",
        runs_payload=_successful_run(),
    )
    assert result["status"] == "blocked_github_plan"
    assert result["enforced"] is False
    assert result["branch_protection_enabled"] is False
    assert result["independent_review_required"] is False
    assert "branch_protection_enabled" in result["blockers"]
    assert "Upgrade the private repository" in result["owner_action"]
    assert result["paper_trading_invoked"] is False
    assert result["live_trading_invoked"] is False


def test_offline_runner_cannot_be_reported_as_enforced() -> None:
    result = merge_gate.evaluate_merge_gate(
        workflow_text=_workflow(),
        runners_payload=_runners("offline"),
        protection_payload=_protection(),
        runs_payload=_successful_run(),
    )
    assert result["status"] == "incomplete"
    assert result["enforced"] is False
    assert result["checks"]["runner_registered"] is True
    assert result["checks"]["runner_online"] is False


def test_active_or_failed_pr_does_not_erase_prior_successful_gate_proof() -> None:
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
    )

    assert result["enforced"] is True
    assert result["checks"]["latest_gate_success"] is True
    assert result["latest_gate_run"]["id"] == 125
    assert result["latest_successful_gate_run"]["id"] == 123
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


def test_failed_gh_api_response_cannot_masquerade_as_control_state(monkeypatch) -> None:
    class Failed:
        returncode = 1
        stdout = '{"message":"Upgrade to GitHub Pro","status":"403"}'
        stderr = "gh: Upgrade to GitHub Pro"

    monkeypatch.setattr(merge_gate.subprocess, "run", lambda *args, **kwargs: Failed())

    payload, error = merge_gate._gh_api("repos/example/private/branches/main/protection")

    assert payload == {}
    assert "Upgrade to GitHub Pro" in error
