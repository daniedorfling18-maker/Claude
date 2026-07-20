#!/usr/bin/env python3
"""Verify that a deploy target came from the accepted independent merge lane."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


EXPECTED_WORKFLOW = ".github/workflows/independent-pr-merge.yml"
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
REQUIRED_CHECKS = {
    "exact_expected_head",
    "current_main_is_ancestor",
    "latest_exact_head_check_passed",
    "latest_exact_head_workflow_passed",
    "independent_current_head_approval",
    "merge_actor_is_repository_owner",
    "merge_triggering_actor_is_repository_owner",
    "independent_reviewer_is_distinct_from_owner",
    "merge_workflow_is_accepted_current_main",
    "trusted_merge_control_unchanged",
    "all_review_threads_resolved",
}


class AcceptanceError(RuntimeError):
    """Raised when deployment acceptance evidence is not exact and complete."""


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _login(value: Any) -> str:
    return str(value or "").strip()


def _workflow_path(value: Any) -> str:
    return str(value or "").split("@", 1)[0]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AcceptanceError(message)


def verify_acceptance(
    *,
    run: Mapping[str, Any],
    attestation: Mapping[str, Any],
    run_id: str,
    target_sha: str,
) -> dict[str, Any]:
    """Validate immutable run and attestation evidence without network I/O."""

    run_id = str(run_id or "").strip()
    target = str(target_sha or "").strip().lower()
    _require(bool(re.fullmatch(r"[1-9][0-9]*", run_id)), "run ID is not positive")
    _require(bool(SHA_PATTERN.fullmatch(target)), "target is not an exact commit SHA")

    run_payload = _mapping(run)
    evidence = _mapping(attestation)
    _require(str(run_payload.get("id") or "") == run_id, "workflow run ID mismatch")
    _require(
        _workflow_path(run_payload.get("path")) == EXPECTED_WORKFLOW,
        "workflow path is not the independent merge lane",
    )
    _require(run_payload.get("event") == "issue_comment", "workflow event is not issue_comment")
    _require(run_payload.get("head_branch") == "main", "workflow did not run from main")
    _require(
        run_payload.get("status") == "completed" and run_payload.get("conclusion") == "success",
        "workflow run is not a completed success",
    )

    run_head = str(run_payload.get("head_sha") or "").lower()
    _require(bool(SHA_PATTERN.fullmatch(run_head)), "workflow run head is not an exact commit")
    _require(evidence.get("status") == "merged" and evidence.get("merged") is True, "merge incomplete")
    _require(
        str(evidence.get("merge_commit_sha") or "").lower() == target,
        "merge commit does not match deploy target",
    )
    _require(
        str(evidence.get("merge_workflow_run_id") or "") == run_id,
        "attested workflow run ID mismatch",
    )
    for field in ("merge_workflow_sha", "current_main_sha", "verified_main_parent_sha"):
        _require(
            str(evidence.get(field) or "").lower() == run_head,
            f"{field} is not the accepted workflow parent",
        )

    _require(not evidence.get("blockers"), "merge attestation contains blockers")
    checks = _mapping(evidence.get("checks"))
    _require(REQUIRED_CHECKS <= set(checks), "merge attestation is missing required controls")
    _require(all(value is True for value in checks.values()), "merge attestation has a failed control")

    owner = _login(evidence.get("repository_owner"))
    author = _login(evidence.get("pull_request_author"))
    actor = _login(_mapping(run_payload.get("actor")).get("login"))
    triggering_actor = _login(_mapping(run_payload.get("triggering_actor")).get("login"))
    attested_actor = _login(evidence.get("merge_actor"))
    attested_triggering_actor = _login(evidence.get("merge_triggering_actor"))
    _require(bool(owner and author and actor and triggering_actor), "merge identities are incomplete")
    _require(
        actor.casefold() == owner.casefold()
        and triggering_actor.casefold() == owner.casefold(),
        "workflow actor and rerun initiator are not the repository owner",
    )
    _require(
        attested_actor.casefold() == actor.casefold()
        and attested_triggering_actor.casefold() == triggering_actor.casefold(),
        "attested actors do not match the workflow run",
    )
    raw_reviewers = evidence.get("eligible_reviewers", [])
    # Fail closed on malformed reviewer evidence: a bare string or mapping would
    # otherwise iterate character/key-wise into a nonempty reviewer set that could
    # pass the owner/author-exclusion checks without a real reviewer identity list.
    _require(isinstance(raw_reviewers, list), "eligible_reviewers attestation is not a list")
    _require(
        all(isinstance(value, str) and value.strip() for value in raw_reviewers),
        "eligible_reviewers attestation is not a list of non-empty login strings",
    )
    reviewers = {_login(value).casefold() for value in raw_reviewers}
    _require(bool(reviewers), "no independent current-head reviewer is attested")
    _require(owner.casefold() not in reviewers, "repository owner is listed as reviewer")
    _require(author.casefold() not in reviewers, "pull-request author is listed as reviewer")

    _require(evidence.get("funding_opened") is False, "funding state is not CLOSED")
    _require(evidence.get("wo67_status") == "BLOCKED", "WO-67 is not BLOCKED")
    _require(evidence.get("paper_trading_invoked") is False, "paper trading was invoked")
    _require(evidence.get("live_trading_invoked") is False, "live trading was invoked")
    return {
        "status": "PASS",
        "acceptance_run_id": run_id,
        "target_main_sha": target,
        "accepted_parent_sha": run_head,
        "repository_owner": owner,
        "independent_reviewers": sorted(reviewers),
        "funding_opened": False,
        "wo67_status": "BLOCKED",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }


def _fetch_run(repo: str, run_id: str, token: str) -> dict[str, Any]:
    _require(bool(REPOSITORY_PATTERN.fullmatch(repo)), "repository name is invalid")
    _require(bool(re.fullmatch(r"[1-9][0-9]*", run_id)), "run ID is not positive")
    _require(bool(token), "GITHUB_TOKEN is missing")
    request = Request(
        f"https://api.github.com/repos/{repo}/actions/runs/{run_id}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "polymarket-vps-deploy-acceptance",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed GitHub API origin
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError("GitHub workflow-run lookup failed") from exc
    _require(isinstance(payload, Mapping), "GitHub workflow-run response is not an object")
    return _mapping(payload)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--target-sha", required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        run = _fetch_run(args.repo, args.run_id, os.environ.get("GITHUB_TOKEN", ""))
        raw_attestation = json.loads(args.artifact.read_text(encoding="utf-8"))
        _require(isinstance(raw_attestation, Mapping), "merge attestation is not an object")
        result = verify_acceptance(
            run=run,
            attestation=_mapping(raw_attestation),
            run_id=args.run_id,
            target_sha=args.target_sha,
        )
    except (AcceptanceError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
