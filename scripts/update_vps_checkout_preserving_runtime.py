#!/usr/bin/env python3
"""Fast-forward a VPS checkout without discarding runtime evidence.

The deployed checkout intentionally bind-mounts ``data/``, ``inputs/``,
``outputs/``, and ``work/``. Long-running collectors can therefore modify
tracked fixture/report files even though source code remains untouched. A
plain ``git pull`` refuses that state, while a reset would destroy evidence.

This updater stashes tracked changes only under the registered runtime roots,
fast-forwards to an already-fetched target, reapplies those changes, and drops
only the stash it created. It fails closed for source/config changes or any
incoming-path collision. Untracked runtime corpora (including the SQLite
ledger) are never stashed, moved, cleaned, or deleted.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from typing import Sequence


RUNTIME_ROOTS = ("data", "inputs", "outputs", "work")
REFUSAL_EXIT_CODE = 4
UPDATE_FAILURE_EXIT_CODE = 5


def _git(
    repo: Path,
    *args: str,
    check: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        capture_output=True,
        text=text,
    )


def _z_paths(repo: Path, *args: str) -> set[str]:
    result = _git(repo, *args, text=False)
    assert isinstance(result.stdout, bytes)
    return {
        item.decode("utf-8", errors="surrogateescape")
        for item in result.stdout.split(b"\0")
        if item
    }


def _tracked_dirty_paths(repo: Path) -> set[str]:
    return _z_paths(repo, "diff", "--name-only", "-z") | _z_paths(
        repo,
        "diff",
        "--cached",
        "--name-only",
        "-z",
    )


def _untracked_paths(repo: Path) -> set[str]:
    return _z_paths(repo, "ls-files", "--others", "--exclude-standard", "-z")


def _incoming_paths(repo: Path, target_ref: str) -> set[str]:
    return _z_paths(repo, "diff", "--name-only", "-z", f"HEAD..{target_ref}")


def _runtime_path(path: str) -> bool:
    first = path.replace("\\", "/").split("/", 1)[0]
    return first in RUNTIME_ROOTS


def _write_report(path: Path | None, payload: dict[str, object]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _emit(payload: dict[str, object], *, report: Path | None) -> None:
    _write_report(report, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))


def _base_payload(repo: Path, target_ref: str, branch: str) -> dict[str, object]:
    return {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repo": str(repo),
        "target_ref": target_ref,
        "branch": branch,
        "runtime_roots": list(RUNTIME_ROOTS),
        "runtime_evidence_deleted": False,
        "untracked_runtime_touched": False,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }


def _stash_reference(repo: Path, commit: str) -> str | None:
    result = _git(repo, "stash", "list", "--format=%H%x09%gd")
    assert isinstance(result.stdout, str)
    for line in result.stdout.splitlines():
        stash_commit, separator, reference = line.partition("\t")
        if separator and stash_commit == commit:
            return reference
    return None


def _apply_stash(repo: Path, commit: str) -> tuple[bool, list[str]]:
    result = _git(repo, "stash", "apply", commit, check=False)
    conflicts = sorted(_z_paths(repo, "diff", "--name-only", "--diff-filter=U", "-z"))
    return result.returncode == 0 and not conflicts, conflicts


def update_checkout(
    repo: Path,
    *,
    target_ref: str,
    branch: str,
    report: Path | None = None,
) -> int:
    repo = repo.resolve()
    if report is not None and not report.is_absolute():
        report = repo / report
    payload = _base_payload(repo, target_ref, branch)

    try:
        original_head_result = _git(repo, "rev-parse", "HEAD")
        target_head_result = _git(repo, "rev-parse", target_ref)
        assert isinstance(original_head_result.stdout, str)
        assert isinstance(target_head_result.stdout, str)
        original_head = original_head_result.stdout.strip()
        target_head = target_head_result.stdout.strip()
    except subprocess.CalledProcessError as exc:
        payload.update(
            {
                "status": "refused",
                "decision": "KEEP_EXISTING_CHECKOUT",
                "reason": "repository or target reference is unavailable",
                "git_error": (exc.stderr or "").strip(),
            }
        )
        _emit(payload, report=report)
        return REFUSAL_EXIT_CODE

    tracked_dirty = _tracked_dirty_paths(repo)
    untracked = _untracked_paths(repo)
    incoming = _incoming_paths(repo, target_ref)
    source_dirty = sorted(path for path in tracked_dirty if not _runtime_path(path))
    source_untracked = sorted(path for path in untracked if not _runtime_path(path))
    collisions = sorted((tracked_dirty | untracked) & incoming)
    payload.update(
        {
            "original_head": original_head,
            "target_head": target_head,
            "tracked_runtime_paths": sorted(path for path in tracked_dirty if _runtime_path(path)),
            "tracked_runtime_path_count": sum(_runtime_path(path) for path in tracked_dirty),
            "untracked_path_count": len(untracked),
            "incoming_path_count": len(incoming),
        }
    )

    if source_dirty or source_untracked or collisions:
        payload.update(
            {
                "status": "refused",
                "decision": "KEEP_EXISTING_CHECKOUT",
                "reason": (
                    "source/config changes require operator review"
                    if source_dirty or source_untracked
                    else "incoming revision collides with preserved runtime state"
                ),
                "source_dirty_paths": source_dirty,
                "source_untracked_paths": source_untracked,
                "incoming_runtime_collisions": collisions,
            }
        )
        _emit(payload, report=report)
        return REFUSAL_EXIT_CODE

    stash_commit: str | None = None
    if tracked_dirty:
        runtime_roots = sorted({path.replace("\\", "/").split("/", 1)[0] for path in tracked_dirty})
        before_result = _git(repo, "rev-parse", "--verify", "refs/stash", check=False)
        before = before_result.stdout.strip() if isinstance(before_result.stdout, str) else ""
        message = f"vps-runtime-predeploy-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        _git(repo, "stash", "push", "-m", message, "--", *runtime_roots)
        after_result = _git(repo, "rev-parse", "--verify", "refs/stash")
        assert isinstance(after_result.stdout, str)
        stash_commit = after_result.stdout.strip()
        if not stash_commit or stash_commit == before:
            payload.update(
                {
                    "status": "failed",
                    "decision": "KEEP_EXISTING_CHECKOUT",
                    "reason": "runtime changes were not captured in a new stash",
                }
            )
            _emit(payload, report=report)
            return UPDATE_FAILURE_EXIT_CODE
        payload["preservation_stash_commit"] = stash_commit

    try:
        _git(repo, "checkout", branch)
        _git(repo, "merge", "--ff-only", target_ref)
    except subprocess.CalledProcessError as exc:
        restored = True
        conflicts: list[str] = []
        if stash_commit:
            restored, conflicts = _apply_stash(repo, stash_commit)
        payload.update(
            {
                "status": "failed",
                "decision": "KEEP_EXISTING_CHECKOUT",
                "reason": "fast-forward failed; preserved runtime state was reapplied",
                "runtime_state_reapplied": restored,
                "restore_conflicts": conflicts,
                "git_error": (exc.stderr or "").strip(),
            }
        )
        _emit(payload, report=report)
        return UPDATE_FAILURE_EXIT_CODE

    if stash_commit:
        restored, conflicts = _apply_stash(repo, stash_commit)
        if not restored:
            payload.update(
                {
                    "status": "failed",
                    "decision": "STOP_DEPLOY_PRESERVATION_STASH_RETAINED",
                    "reason": "runtime stash did not reapply cleanly",
                    "restore_conflicts": conflicts,
                    "preservation_stash_retained": True,
                }
            )
            _emit(payload, report=report)
            return UPDATE_FAILURE_EXIT_CODE
        reference = _stash_reference(repo, stash_commit)
        if reference:
            _git(repo, "stash", "drop", reference)

    final_head_result = _git(repo, "rev-parse", "HEAD")
    assert isinstance(final_head_result.stdout, str)
    payload.update(
        {
            "status": "ok",
            "decision": "CHECKOUT_UPDATED_RUNTIME_PRESERVED",
            "final_head": final_head_result.stdout.strip(),
            "runtime_state_reapplied": bool(stash_commit),
            "preservation_stash_retained": False,
        }
    )
    _emit(payload, report=report)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--target-ref", default="origin/main")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--report", type=Path, default=Path("outputs/performance/vps_checkout_update.json"))
    args = parser.parse_args(argv)
    return update_checkout(
        args.repo,
        target_ref=args.target_ref,
        branch=args.branch,
        report=args.report,
    )


if __name__ == "__main__":
    raise SystemExit(main())
