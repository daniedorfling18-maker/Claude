#!/usr/bin/env python3
"""Fail-closed merge lane for an independently reviewed exact PR head.

The only mutating mode is intended for ``independent-pr-merge.yml``. GitHub
must have loaded that ``issue_comment`` workflow from the accepted default
branch, the repository owner must have posted and initiated the exact-head
command, and a distinct trusted identity must have approved that current head.
It never reads repository secrets other than the short-lived workflow token
supplied through ``GITHUB_TOKEN``.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import sys
import time
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


API_ROOT = "https://api.github.com"
GRAPHQL_URL = "https://api.github.com/graphql"
REQUIRED_CONTEXT = "WO-69 guard and invariants"
REQUIRED_WORKFLOW = ".github/workflows/required-pr-gate.yml"
INDEPENDENT_WORKFLOW = ".github/workflows/independent-pr-merge.yml"
OIDC_ISSUER = "https://token.actions.githubusercontent.com"
PROTECTED_CONTROL_PATHS = {
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
    # WO-153 scope (a) — the deploy-control surface. `_protected_control_path`
    # predates any deploy-control surface; these sixteen entries are the
    # dispatch workflow/script/test-file triad, the six scripts and the
    # compose file `stage_target_scripts` stages as gates, the three-file
    # acceptance-verdict chain reached through that compose file's own gate
    # command, and Path A's own binding step plus its tailnet-transport
    # enforcement step.
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
}
# The trusted workflow invokes these tools through ``python -m`` while its
# current directory is the candidate checkout.  A top-level module/package
# with the same name wins module resolution and can return success without
# running the accepted tool.  Protect both ``name.py`` and every path below a
# top-level ``name/`` package.
PROTECTED_PYTHON_ENTRYPOINTS = {"pip", "pytest", "ruff", "setuptools", "wheel"}
TRUSTED_REVIEW_ASSOCIATIONS = {"COLLABORATOR", "MEMBER", "OWNER"}
CONTROL_REVIEW_STATES = {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
MERGE_COMMENT_PATTERN = re.compile(r"^/independent-merge ([0-9a-f]{40})$")


class MergeGateError(RuntimeError):
    """Raised when evidence cannot be fetched or the merge changes underneath us."""


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _login(value: Any) -> str:
    return str(_mapping(value).get("login") or "").strip()


def _ordered(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        (_mapping(row) for row in rows),
        key=lambda row: (str(row.get("submitted_at") or row.get("started_at") or ""), int(row.get("id") or 0)),
    )


def _workflow_path(value: Any) -> str:
    """Normalize Actions' ``path@ref`` response to the registered file path."""

    return str(value or "").split("@", 1)[0]


def expected_head_from_merge_comment(value: str) -> str:
    """Parse the exact owner command without accepting extra text or options."""

    match = MERGE_COMMENT_PATTERN.fullmatch(value)
    if match is None:
        raise MergeGateError(
            "merge comment must be exactly /independent-merge followed by one lowercase 40-character SHA"
        )
    return match.group(1)


def _protected_control_path(path: str) -> bool:
    normalized = path.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.lstrip("/")
    parts = normalized.split("/")
    import_root = parts[1] if len(parts) > 1 and parts[0] == "src" else parts[0]
    shadows_python_entrypoint = any(
        import_root in {entrypoint, f"{entrypoint}.py"}
        for entrypoint in PROTECTED_PYTHON_ENTRYPOINTS
    )
    basename = parts[-1]
    # A test-package initializer (``tests/__init__.py`` or any ``__init__.py``
    # under a ``tests`` directory) is imported by pytest during collection, so
    # a candidate can use it to mutate pytest state or skip collection and let
    # the trusted ``python -m pytest`` pass without running the repository
    # suite.  Treat it as merge-control input alongside ``conftest.py``.
    is_test_package_init = basename == "__init__.py" and "tests" in parts[:-1]
    return (
        normalized in PROTECTED_CONTROL_PATHS
        or basename == "conftest.py"
        or is_test_package_init
        or shadows_python_entrypoint
    )


def _decode_oidc_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise MergeGateError("GitHub OIDC provider returned a malformed token")
    try:
        header = json.loads(base64.urlsafe_b64decode(parts[0] + "=" * (-len(parts[0]) % 4)))
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except (binascii.Error, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MergeGateError("GitHub OIDC token payload could not be decoded") from exc
    if _mapping(header).get("alg") != "RS256" or not _mapping(header).get("kid"):
        raise MergeGateError("GitHub OIDC token header is not an identified RS256 key")
    if not isinstance(claims, Mapping):
        raise MergeGateError("GitHub OIDC token claims are not an object")
    return _mapping(claims)


def _validate_oidc_claims(
    claims: Mapping[str, Any],
    *,
    repo: str,
    audience: str,
    now_epoch: int | None = None,
) -> dict[str, Any]:
    """Validate current-job claims returned over the authenticated OIDC endpoint."""

    payload = _mapping(claims)
    now = int(time.time()) if now_epoch is None else int(now_epoch)
    expected_workflow_ref = f"{repo}/{INDEPENDENT_WORKFLOW}@refs/heads/main"
    try:
        expires = int(payload.get("exp"))
        issued = int(payload.get("iat"))
        not_before = int(payload.get("nbf"))
    except (TypeError, ValueError) as exc:
        raise MergeGateError("GitHub OIDC token time claims are missing or invalid") from exc
    required = {
        "iss": OIDC_ISSUER,
        "aud": audience,
        "repository": repo,
        "event_name": "issue_comment",
        "ref": "refs/heads/main",
        "workflow_ref": expected_workflow_ref,
    }
    mismatches = [key for key, value in required.items() if str(payload.get(key) or "") != value]
    if str(payload.get("actor") or "").casefold() != repo.split("/", 1)[0].casefold():
        mismatches.append("actor")
    if mismatches:
        raise MergeGateError(
            "GitHub OIDC identity does not match the accepted main workflow: "
            + ", ".join(sorted(mismatches))
        )
    if expires <= now or issued > now + 30 or not_before > now + 30:
        raise MergeGateError("GitHub OIDC token is expired or not currently valid")
    if not str(payload.get("run_id") or "").isdigit():
        raise MergeGateError("GitHub OIDC token run_id is missing")
    if not SHA_PATTERN.fullmatch(str(payload.get("workflow_sha") or "").lower()):
        raise MergeGateError("GitHub OIDC token workflow_sha is invalid")
    if not _login({"login": payload.get("actor")}):
        raise MergeGateError("GitHub OIDC token actor is missing")
    return payload


def request_current_workflow_identity(repo: str) -> dict[str, Any]:
    """Request a current-job OIDC token directly from GitHub over fixed HTTPS."""

    raw_url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", "")
    request_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")
    parsed = urlsplit(raw_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(".actions.githubusercontent.com")
        or not request_token
    ):
        raise MergeGateError("GitHub OIDC request endpoint or bearer token is unavailable")
    audience = f"https://github.com/{repo}/independent-merge"
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["audience"] = audience
    request_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))
    request = Request(
        request_url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {request_token}",
            "User-Agent": "polymarket-independent-merge-identity",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - constrained GitHub host
            response_payload = _mapping(json.loads(response.read().decode("utf-8")))
    except (HTTPError, URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MergeGateError("GitHub OIDC identity request failed") from exc
    claims = _decode_oidc_payload(str(response_payload.get("value") or ""))
    return _validate_oidc_claims(claims, repo=repo, audience=audience)


def evaluate_merge_candidate(
    *,
    repository: str,
    pull_request: Mapping[str, Any],
    expected_head: str,
    workflow_identity: Mapping[str, Any],
    merge_workflow_run: Mapping[str, Any],
    main_ref: Mapping[str, Any],
    comparison: Mapping[str, Any],
    check_runs: Sequence[Mapping[str, Any]],
    workflow_runs: Sequence[Mapping[str, Any]],
    reviews: Sequence[Mapping[str, Any]],
    review_threads: Sequence[Mapping[str, Any]],
    changed_files: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Evaluate immutable merge evidence without performing network I/O."""

    pr = _mapping(pull_request)
    head_sha = str(_mapping(pr.get("head")).get("sha") or "").lower()
    base = _mapping(pr.get("base"))
    base_sha = str(base.get("sha") or "").lower()
    current_main_sha = str(_mapping(main_ref.get("object")).get("sha") or "").lower()
    author = _login(pr.get("user"))
    repository_owner = repository.split("/", 1)[0].strip()
    identity = _mapping(workflow_identity)
    workflow_run = _mapping(merge_workflow_run)
    normalized_actor = _login(workflow_run.get("actor"))
    normalized_triggering_actor = _login(workflow_run.get("triggering_actor"))
    expected = expected_head.strip().lower()
    blockers: list[str] = []

    workflow_run_id = str(workflow_run.get("id") or "")
    identity_run_id = str(identity.get("run_id") or "")
    identity_workflow_sha = str(identity.get("workflow_sha") or "").lower()
    run_head_sha = str(workflow_run.get("head_sha") or "").lower()
    accepted_workflow_run = (
        str(identity.get("repository") or "") == repository
        and str(identity.get("event_name") or "") == "issue_comment"
        and str(identity.get("ref") or "") == "refs/heads/main"
        and str(identity.get("workflow_ref") or "")
        == f"{repository}/{INDEPENDENT_WORKFLOW}@refs/heads/main"
        and identity_run_id == workflow_run_id
        and str(workflow_run.get("event") or "") == "issue_comment"
        and _workflow_path(workflow_run.get("path")) == INDEPENDENT_WORKFLOW
        and str(workflow_run.get("head_branch") or "") == "main"
        and identity_workflow_sha == current_main_sha
        and run_head_sha == current_main_sha
        and str(identity.get("actor") or "").casefold() == normalized_actor.casefold()
        and normalized_actor.casefold() == repository_owner.casefold()
        and normalized_triggering_actor.casefold() == repository_owner.casefold()
    )
    if not accepted_workflow_run:
        blockers.append("merge_workflow_identity_is_not_accepted_current_main")

    if not SHA_PATTERN.fullmatch(expected):
        blockers.append("expected_head_must_be_a_40_character_lowercase_sha")
    if str(pr.get("state") or "").lower() != "open":
        blockers.append("pull_request_not_open")
    if bool(pr.get("draft")):
        blockers.append("pull_request_is_draft")
    if str(base.get("ref") or "") != "main":
        blockers.append("pull_request_does_not_target_main")
    if head_sha != expected:
        blockers.append("pull_request_head_changed")
    if not normalized_actor:
        blockers.append("merge_actor_missing")
    elif normalized_actor.casefold() != repository_owner.casefold():
        blockers.append("merge_actor_is_not_repository_owner")
    if not normalized_triggering_actor:
        blockers.append("merge_triggering_actor_missing")
    elif normalized_triggering_actor.casefold() != repository_owner.casefold():
        blockers.append("merge_triggering_actor_is_not_repository_owner")
    if pr.get("mergeable") is not True:
        blockers.append("pull_request_not_currently_mergeable")
    if not current_main_sha or base_sha != current_main_sha:
        blockers.append("pull_request_base_is_not_current_main")
    if (
        str(comparison.get("status") or "").lower() not in {"ahead", "identical"}
        or int(comparison.get("behind_by") or 0) != 0
    ):
        blockers.append("pull_request_head_does_not_contain_current_main")

    matching_checks = [
        row
        for row in _ordered(check_runs)
        if str(row.get("name") or "") == REQUIRED_CONTEXT
        and str(row.get("head_sha") or "").lower() == expected
        and str(_mapping(row.get("app")).get("slug") or "") == "github-actions"
    ]
    latest_check = matching_checks[-1] if matching_checks else {}
    exact_head_check_passed = (
        str(latest_check.get("status") or "").lower() == "completed"
        and str(latest_check.get("conclusion") or "").lower() == "success"
    )
    if not exact_head_check_passed:
        blockers.append("latest_exact_head_required_check_not_successful")

    matching_workflow_runs = [
        row
        for row in _ordered(workflow_runs)
        if str(row.get("event") or "") == "pull_request"
        and str(row.get("head_sha") or "").lower() == expected
        and _workflow_path(row.get("path")) == REQUIRED_WORKFLOW
    ]
    latest_workflow_run = matching_workflow_runs[-1] if matching_workflow_runs else {}
    exact_head_workflow_passed = (
        str(latest_workflow_run.get("status") or "").lower() == "completed"
        and str(latest_workflow_run.get("conclusion") or "").lower() == "success"
    )
    if not exact_head_workflow_passed:
        blockers.append("latest_exact_head_required_workflow_not_successful")

    latest_by_reviewer: dict[str, dict[str, Any]] = {}
    for review in _ordered(reviews):
        state = str(review.get("state") or "").upper()
        reviewer = _login(review.get("user"))
        if state in CONTROL_REVIEW_STATES and reviewer:
            latest_by_reviewer[reviewer.casefold()] = review
    unresolved_change_requests = sorted(
        _login(review.get("user"))
        for review in latest_by_reviewer.values()
        if str(review.get("state") or "").upper() == "CHANGES_REQUESTED"
    )
    eligible_reviewers = sorted(
        _login(review.get("user"))
        for review in latest_by_reviewer.values()
        if str(review.get("state") or "").upper() == "APPROVED"
        and str(review.get("commit_id") or "").lower() == expected
        and str(review.get("author_association") or "").upper() in TRUSTED_REVIEW_ASSOCIATIONS
        and _login(review.get("user")).casefold() != author.casefold()
        and _login(review.get("user")).casefold() != repository_owner.casefold()
    )
    if unresolved_change_requests:
        blockers.append("unresolved_changes_requested_review")
    if not eligible_reviewers:
        blockers.append("no_independent_approval_on_current_head")

    changed_paths = {
        str(row.get(field) or "").strip()
        for row in changed_files
        for field in ("filename", "previous_filename")
        if str(row.get(field) or "").strip()
    }
    protected_control_changes = sorted(path for path in changed_paths if _protected_control_path(path))
    if protected_control_changes:
        blockers.append("pull_request_changes_trusted_merge_control")

    unresolved_threads = [
        str(thread.get("id") or "unknown") for thread in review_threads if not bool(thread.get("isResolved"))
    ]
    if unresolved_threads:
        blockers.append("unresolved_review_threads")

    checks = {
        "exact_expected_head": bool(SHA_PATTERN.fullmatch(expected)) and head_sha == expected,
        "current_main_is_ancestor": "pull_request_head_does_not_contain_current_main" not in blockers,
        "latest_exact_head_check_passed": exact_head_check_passed,
        "latest_exact_head_workflow_passed": exact_head_workflow_passed,
        "independent_current_head_approval": bool(eligible_reviewers),
        "merge_actor_is_repository_owner": normalized_actor.casefold()
        == repository_owner.casefold(),
        "merge_triggering_actor_is_repository_owner": normalized_triggering_actor.casefold()
        == repository_owner.casefold(),
        "independent_reviewer_is_distinct_from_owner": bool(eligible_reviewers)
        and all(
            reviewer.casefold() != repository_owner.casefold()
            for reviewer in eligible_reviewers
        ),
        "merge_workflow_is_accepted_current_main": accepted_workflow_run,
        "trusted_merge_control_unchanged": not protected_control_changes,
        "all_review_threads_resolved": not unresolved_threads,
    }
    return {
        "status": "eligible" if not blockers else "blocked",
        "eligible": not blockers,
        "pull_request_number": pr.get("number"),
        "expected_head_sha": expected,
        "current_main_sha": current_main_sha,
        "pull_request_author": author,
        "repository_owner": repository_owner,
        "merge_actor": normalized_actor,
        "merge_triggering_actor": normalized_triggering_actor,
        "merge_workflow_run_id": workflow_run_id,
        "merge_workflow_sha": identity_workflow_sha,
        "eligible_reviewers": eligible_reviewers,
        "unresolved_change_request_reviewers": unresolved_change_requests,
        "unresolved_review_thread_ids": unresolved_threads,
        "protected_control_changes": protected_control_changes,
        "latest_required_check_id": latest_check.get("id"),
        "latest_required_workflow_run_id": latest_workflow_run.get("id"),
        "checks": checks,
        "blockers": blockers,
        "funding_opened": False,
        "wo67_status": "BLOCKED",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }


class GitHubClient:
    """Small stdlib-only GitHub API client; errors never include the token."""

    def __init__(self, token: str) -> None:
        if not token:
            raise MergeGateError("GITHUB_TOKEN is required")
        self._token = token

    def request(self, path: str, *, method: str = "GET", payload: Mapping[str, Any] | None = None) -> Any:
        url = path if path.startswith("https://") else f"{API_ROOT}/{path.lstrip('/')}"
        body = json.dumps(dict(payload)).encode("utf-8") if payload is not None else None
        request = Request(
            url,
            data=body,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "User-Agent": "polymarket-independent-merge-gate",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed GitHub API origin
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            message = f"GitHub API returned HTTP {exc.code}"
            try:
                detail = _mapping(json.loads(exc.read().decode("utf-8"))).get("message")
                if detail:
                    message = f"{message}: {detail}"
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
            raise MergeGateError(message) from exc
        except (URLError, TimeoutError) as exc:
            raise MergeGateError(f"GitHub API request failed: {exc.reason if isinstance(exc, URLError) else exc}") from exc

    def paginated_list(self, path: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        separator = "&" if "?" in path else "?"
        for page in range(1, 21):
            payload = self.request(f"{path}{separator}per_page=100&page={page}")
            if not isinstance(payload, list):
                raise MergeGateError("GitHub API pagination returned a non-list payload")
            rows.extend(_mapping(row) for row in payload)
            if len(payload) < 100:
                return rows
        raise MergeGateError("GitHub API pagination exceeded 2,000 rows")

    def review_threads(self, owner: str, name: str, number: int) -> list[dict[str, Any]]:
        query = """
        query($owner: String!, $name: String!, $number: Int!, $after: String) {
          repository(owner: $owner, name: $name) {
            pullRequest(number: $number) {
              reviewThreads(first: 100, after: $after) {
                nodes { id isResolved isOutdated }
                pageInfo { hasNextPage endCursor }
              }
            }
          }
        }
        """
        rows: list[dict[str, Any]] = []
        after: str | None = None
        for _ in range(20):
            payload = self.request(
                GRAPHQL_URL,
                method="POST",
                payload={"query": query, "variables": {"owner": owner, "name": name, "number": number, "after": after}},
            )
            if _mapping(payload).get("errors"):
                raise MergeGateError("GitHub GraphQL returned review-thread errors")
            threads = _mapping(
                _mapping(_mapping(_mapping(payload).get("data")).get("repository")).get("pullRequest")
            ).get("reviewThreads")
            page = _mapping(threads)
            rows.extend(_mapping(row) for row in page.get("nodes", []) or [])
            page_info = _mapping(page.get("pageInfo"))
            if not bool(page_info.get("hasNextPage")):
                return rows
            after = str(page_info.get("endCursor") or "") or None
            if after is None:
                raise MergeGateError("GitHub GraphQL review-thread cursor is missing")
        raise MergeGateError("GitHub GraphQL review threads exceeded 2,000 rows")


def _split_repo(repo: str) -> tuple[str, str]:
    parts = repo.strip().split("/")
    if len(parts) != 2 or not all(parts):
        raise MergeGateError("--repo must be OWNER/NAME")
    return parts[0], parts[1]


def collect_evidence(
    client: GitHubClient,
    repo: str,
    number: int,
    expected_head: str,
    *,
    merge_run_id: str,
) -> dict[str, Any]:
    owner, name = _split_repo(repo)
    escaped_head = quote(expected_head, safe="")
    run_query = urlencode({"event": "pull_request", "head_sha": expected_head})
    pull_request = _mapping(client.request(f"repos/{repo}/pulls/{number}"))
    main_ref = _mapping(client.request(f"repos/{repo}/git/ref/heads/main"))
    current_main = str(_mapping(main_ref.get("object")).get("sha") or "")
    comparison = _mapping(client.request(f"repos/{repo}/compare/{quote(current_main, safe='')}...{escaped_head}"))
    check_payload = _mapping(client.request(f"repos/{repo}/commits/{escaped_head}/check-runs?per_page=100"))
    check_runs = [_mapping(row) for row in check_payload.get("check_runs", []) or []]
    if int(check_payload.get("total_count") or 0) > len(check_runs):
        raise MergeGateError("More than 100 exact-head check runs exist; evidence is incomplete")
    workflow_payload = _mapping(
        client.request(f"repos/{repo}/actions/workflows/required-pr-gate.yml/runs?{run_query}&per_page=100")
    )
    workflow_runs = [_mapping(row) for row in workflow_payload.get("workflow_runs", []) or []]
    if int(workflow_payload.get("total_count") or 0) > len(workflow_runs):
        raise MergeGateError("More than 100 exact-head workflow runs exist; evidence is incomplete")
    return {
        "repository": repo,
        "pull_request": pull_request,
        "main_ref": main_ref,
        "comparison": comparison,
        "check_runs": check_runs,
        "workflow_runs": workflow_runs,
        "merge_workflow_run": _mapping(
            client.request(f"repos/{repo}/actions/runs/{quote(merge_run_id, safe='')}")
        ),
        "reviews": client.paginated_list(f"repos/{repo}/pulls/{number}/reviews"),
        "review_threads": client.review_threads(owner, name, number),
        "changed_files": client.paginated_list(f"repos/{repo}/pulls/{number}/files"),
    }


def atomic_squash_merge(
    client: GitHubClient,
    *,
    repo: str,
    pull_request: Mapping[str, Any],
    expected_head: str,
    expected_main: str,
) -> dict[str, Any]:
    """Create a squash-equivalent commit and fast-forward main atomically.

    The ordinary pull-request merge endpoint can bind the head SHA but not the
    base SHA. Here the new commit has exactly the expected main as its parent,
    and the non-force ref update succeeds only while current main is still an
    ancestor. If another merge advances main first, GitHub rejects the update
    instead of silently merging onto an untested base.
    """

    expected = expected_head.strip().lower()
    parent = expected_main.strip().lower()
    if not SHA_PATTERN.fullmatch(expected) or not SHA_PATTERN.fullmatch(parent):
        raise MergeGateError("atomic merge requires exact head and main SHAs")
    head_commit = _mapping(client.request(f"repos/{repo}/git/commits/{expected}"))
    tree_sha = str(_mapping(head_commit.get("tree")).get("sha") or "").lower()
    if not SHA_PATTERN.fullmatch(tree_sha):
        raise MergeGateError("exact PR head tree could not be verified")

    pr = _mapping(pull_request)
    number = int(pr.get("number") or 0)
    title = str(pr.get("title") or "Independently reviewed change").strip()
    created = _mapping(
        client.request(
            f"repos/{repo}/git/commits",
            method="POST",
            payload={
                "message": f"{title} (#{number})\n\nVerified head: {expected}",
                "tree": tree_sha,
                "parents": [parent],
            },
        )
    )
    merge_sha = str(created.get("sha") or "").lower()
    created_tree = str(_mapping(created.get("tree")).get("sha") or "").lower()
    created_parents = [
        str(_mapping(row).get("sha") or "").lower()
        for row in created.get("parents", []) or []
    ]
    if (
        not SHA_PATTERN.fullmatch(merge_sha)
        or created_tree != tree_sha
        or created_parents != [parent]
    ):
        raise MergeGateError("GitHub returned an unverifiable atomic squash commit")

    try:
        updated = _mapping(
            client.request(
                f"repos/{repo}/git/refs/heads/main",
                method="PATCH",
                payload={"sha": merge_sha, "force": False},
            )
        )
    except MergeGateError as exc:
        raise MergeGateError(
            "atomic main update rejected; main may have advanced after evidence refresh"
        ) from exc
    if str(_mapping(updated.get("object")).get("sha") or "").lower() != merge_sha:
        raise MergeGateError("GitHub did not confirm the atomic main ref update")

    close_warning = ""
    close_skipped_reason = ""
    try:
        latest_pr = _mapping(client.request(f"repos/{repo}/pulls/{number}"))
        latest_head = str(_mapping(latest_pr.get("head")).get("sha") or "").lower()
        if latest_head != expected:
            close_skipped_reason = "pull_request_head_changed_after_atomic_merge"
        else:
            client.request(
                f"repos/{repo}/pulls/{number}",
                method="PATCH",
                payload={"state": "closed"},
            )
    except MergeGateError as exc:
        # The immutable code update already succeeded. Preserve that fact and
        # surface the administrative follow-up without attempting a rollback.
        close_warning = str(exc)
    return {
        "merged": True,
        "merge_commit_sha": merge_sha,
        "merge_method": "atomic_fast_forward_squash",
        "verified_head_tree_sha": tree_sha,
        "verified_main_parent_sha": parent,
        "pull_request_close_warning": close_warning,
        "pull_request_close_skipped_reason": close_skipped_reason,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--merge-comment", required=True)
    parser.add_argument("--merge", action="store_true")
    args = parser.parse_args()
    expected_head = expected_head_from_merge_comment(args.merge_comment)

    workflow_identity = request_current_workflow_identity(args.repo)
    merge_run_id = str(workflow_identity.get("run_id") or "")
    client = GitHubClient(os.environ.get("GITHUB_TOKEN", ""))
    evidence = collect_evidence(
        client,
        args.repo,
        args.pr,
        expected_head,
        merge_run_id=merge_run_id,
    )
    result = evaluate_merge_candidate(
        expected_head=expected_head,
        workflow_identity=workflow_identity,
        **evidence,
    )
    if not result["eligible"]:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 2
    if args.merge:
        refreshed = collect_evidence(
            client,
            args.repo,
            args.pr,
            expected_head,
            merge_run_id=merge_run_id,
        )
        result = evaluate_merge_candidate(
            expected_head=expected_head,
            workflow_identity=workflow_identity,
            **refreshed,
        )
        if not result["eligible"]:
            print(json.dumps(result, indent=2, sort_keys=True))
            return 2
        merge_result = atomic_squash_merge(
            client,
            repo=args.repo,
            pull_request=_mapping(refreshed.get("pull_request")),
            expected_head=expected_head,
            expected_main=str(result.get("current_main_sha") or ""),
        )
        result.update({"status": "merged", **merge_result})
    else:
        result["merged"] = False
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MergeGateError as exc:
        print(json.dumps({"status": "error", "error": str(exc), "funding_opened": False, "wo67_status": "BLOCKED"}))
        raise SystemExit(2) from exc
