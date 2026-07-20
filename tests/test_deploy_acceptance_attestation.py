from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "verify_independent_main_acceptance.py"
SPEC = importlib.util.spec_from_file_location("verify_independent_main_acceptance", MODULE_PATH)
assert SPEC and SPEC.loader
acceptance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acceptance)


RUN_ID = "9001"
PARENT = "b" * 40
TARGET = "c" * 40


def _run() -> dict:
    return {
        "id": int(RUN_ID),
        "path": acceptance.EXPECTED_WORKFLOW + "@refs/heads/main",
        "event": "issue_comment",
        "head_branch": "main",
        "head_sha": PARENT,
        "status": "completed",
        "conclusion": "success",
        "actor": {"login": "owner"},
        "triggering_actor": {"login": "owner"},
    }


def _attestation() -> dict:
    return {
        "status": "merged",
        "merged": True,
        "merge_commit_sha": TARGET,
        "merge_workflow_run_id": RUN_ID,
        "merge_workflow_sha": PARENT,
        "current_main_sha": PARENT,
        "verified_main_parent_sha": PARENT,
        "repository_owner": "owner",
        "pull_request_author": "owner",
        "merge_actor": "owner",
        "merge_triggering_actor": "owner",
        "eligible_reviewers": ["reviewer"],
        "checks": {name: True for name in acceptance.REQUIRED_CHECKS},
        "blockers": [],
        "funding_opened": False,
        "wo67_status": "BLOCKED",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }


def test_issue_comment_owner_and_distinct_reviewer_accept_exact_main() -> None:
    result = acceptance.verify_acceptance(
        run=_run(),
        attestation=_attestation(),
        run_id=RUN_ID,
        target_sha=TARGET,
    )

    assert result["status"] == "PASS"
    assert result["accepted_parent_sha"] == PARENT
    assert result["target_main_sha"] == TARGET
    assert result["funding_opened"] is False
    assert result["wo67_status"] == "BLOCKED"


@pytest.mark.parametrize("event", ["workflow_dispatch", "pull_request", "push"])
def test_non_default_branch_comment_event_is_rejected(event: str) -> None:
    run = _run()
    run["event"] = event

    with pytest.raises(acceptance.AcceptanceError, match="issue_comment"):
        acceptance.verify_acceptance(
            run=run,
            attestation=_attestation(),
            run_id=RUN_ID,
            target_sha=TARGET,
        )


def test_owner_cannot_masquerade_as_independent_reviewer() -> None:
    attestation = _attestation()
    attestation["eligible_reviewers"] = ["owner"]

    with pytest.raises(acceptance.AcceptanceError, match="owner is listed as reviewer"):
        acceptance.verify_acceptance(
            run=_run(),
            attestation=attestation,
            run_id=RUN_ID,
            target_sha=TARGET,
        )


@pytest.mark.parametrize(
    "malformed",
    ["reviewer", {"reviewer": True}, ["", "  "], [123], None],
)
def test_malformed_eligible_reviewers_fail_closed(malformed) -> None:
    # #298 Codex P2: a non-list (bare string / mapping / null) or a list containing a
    # non-string / empty login must fail closed. Before the guard a bare string
    # iterated character-wise into a nonempty reviewer set that excluded owner/author
    # and returned PASS with no real reviewer identity.
    attestation = _attestation()
    attestation["eligible_reviewers"] = malformed

    with pytest.raises(acceptance.AcceptanceError, match="eligible_reviewers"):
        acceptance.verify_acceptance(
            run=_run(),
            attestation=attestation,
            run_id=RUN_ID,
            target_sha=TARGET,
        )


def test_old_dispatch_actor_model_is_rejected() -> None:
    run = _run()
    run["actor"] = {"login": "reviewer"}
    run["triggering_actor"] = {"login": "reviewer"}

    with pytest.raises(acceptance.AcceptanceError, match="repository owner"):
        acceptance.verify_acceptance(
            run=run,
            attestation=_attestation(),
            run_id=RUN_ID,
            target_sha=TARGET,
        )


def test_stale_parent_or_wrong_workflow_run_is_rejected() -> None:
    attestation = _attestation()
    attestation["verified_main_parent_sha"] = "d" * 40

    with pytest.raises(acceptance.AcceptanceError, match="verified_main_parent_sha"):
        acceptance.verify_acceptance(
            run=_run(),
            attestation=attestation,
            run_id=RUN_ID,
            target_sha=TARGET,
        )

    attestation = _attestation()
    attestation["merge_workflow_run_id"] = "9002"
    with pytest.raises(acceptance.AcceptanceError, match="run ID mismatch"):
        acceptance.verify_acceptance(
            run=_run(),
            attestation=attestation,
            run_id=RUN_ID,
            target_sha=TARGET,
        )


def test_missing_control_or_open_funding_fails_closed() -> None:
    attestation = _attestation()
    attestation["checks"].pop("all_review_threads_resolved")

    with pytest.raises(acceptance.AcceptanceError, match="missing required controls"):
        acceptance.verify_acceptance(
            run=_run(),
            attestation=attestation,
            run_id=RUN_ID,
            target_sha=TARGET,
        )

    attestation = _attestation()
    attestation["funding_opened"] = True
    with pytest.raises(acceptance.AcceptanceError, match="funding state"):
        acceptance.verify_acceptance(
            run=_run(),
            attestation=attestation,
            run_id=RUN_ID,
            target_sha=TARGET,
        )
