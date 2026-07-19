#!/usr/bin/env python3
"""Restore a failed VPS paper deployment to its captured last-known-good state.

The caller supplies an exact rollback commit, a mode-0600 environment backup,
the previous deployed-marker backup, and a Docker image tag captured before
the candidate build.  Runtime roots are preserved across the backward source
move.  Source/config dirt or any runtime-path collision refuses the rollback
instead of deleting evidence.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
from typing import Any, Sequence


RUNTIME_ROOTS = ("data", "inputs", "outputs", "work")
DEFAULT_REQUIRED_SERVICES = (
    "polymarket-paper-live",
    "polymarket-dashboard",
    "superbru-auto-pick-watchdog",
    "vps-ops-scheduler",
)
SHA_LENGTH = 40


class RollbackError(RuntimeError):
    """Raised when automatic rollback cannot prove a safe restoration."""


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
        env=env,
    )


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run(("git", "-C", str(repo), *args), cwd=repo, check=check)


def _z_paths(repo: Path, *args: str) -> set[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    )
    return {
        item.decode("utf-8", errors="surrogateescape")
        for item in result.stdout.split(b"\0")
        if item
    }


def _runtime_path(path: str) -> bool:
    return path.replace("\\", "/").split("/", 1)[0] in RUNTIME_ROOTS


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _atomic_restore(source: Path, target: Path, *, mode: int | None = None) -> None:
    if not source.is_file():
        raise RollbackError(f"required backup is missing: {source}")
    temporary = target.with_name(f".{target.name}.rollback.tmp")
    shutil.copyfile(source, temporary)
    if mode is not None:
        temporary.chmod(mode)
    os.replace(temporary, target)


def _stash_reference(repo: Path, commit: str) -> str | None:
    result = _git(repo, "stash", "list", "--format=%H%x09%gd")
    for line in result.stdout.splitlines():
        stash_commit, separator, reference = line.partition("\t")
        if separator and stash_commit == commit:
            return reference
    return None


def _preserve_runtime_and_checkout(repo: Path, rollback_ref: str, branch: str) -> dict[str, Any]:
    candidate_head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    rollback_head = _git(repo, "rev-parse", rollback_ref).stdout.strip()
    if len(candidate_head) != SHA_LENGTH or len(rollback_head) != SHA_LENGTH:
        raise RollbackError("candidate or rollback reference is not an exact commit")
    if candidate_head == rollback_head:
        raise RollbackError("candidate and rollback revisions are identical")

    tracked = _z_paths(repo, "diff", "--name-only", "-z") | _z_paths(
        repo, "diff", "--cached", "--name-only", "-z"
    )
    untracked = _z_paths(repo, "ls-files", "--others", "--exclude-standard", "-z")
    changed = _z_paths(repo, "diff", "--name-only", "-z", f"{candidate_head}..{rollback_head}")
    source_dirty = sorted(path for path in tracked if not _runtime_path(path))
    source_untracked = sorted(path for path in untracked if not _runtime_path(path))
    collisions = sorted((tracked | untracked) & changed)
    if source_dirty or source_untracked or collisions:
        raise RollbackError(
            "rollback refused: "
            + json.dumps(
                {
                    "source_dirty_paths": source_dirty,
                    "source_untracked_paths": source_untracked,
                    "runtime_collisions": collisions,
                },
                sort_keys=True,
            )
        )

    stash_commit = ""
    if tracked:
        roots = sorted({path.replace("\\", "/").split("/", 1)[0] for path in tracked})
        before = _git(repo, "rev-parse", "--verify", "refs/stash", check=False).stdout.strip()
        message = f"vps-runtime-prerollback-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        _git(repo, "stash", "push", "-m", message, "--", *roots)
        stash_commit = _git(repo, "rev-parse", "--verify", "refs/stash").stdout.strip()
        if not stash_commit or stash_commit == before:
            raise RollbackError("runtime changes were not captured in a new rollback stash")

    try:
        _git(repo, "checkout", "-B", branch, rollback_head)
        if stash_commit:
            applied = _git(repo, "stash", "apply", stash_commit, check=False)
            conflicts = sorted(_z_paths(repo, "diff", "--name-only", "--diff-filter=U", "-z"))
            if applied.returncode != 0 or conflicts:
                raise RollbackError(
                    "runtime rollback stash did not reapply cleanly; stash retained: "
                    + json.dumps({"stash": stash_commit, "conflicts": conflicts}, sort_keys=True)
                )
            reference = _stash_reference(repo, stash_commit)
            if reference:
                _git(repo, "stash", "drop", reference)
    except Exception:
        # A failed apply retains the stash for manual evidence recovery.  Never
        # clean or reset runtime roots in an attempted automatic recovery.
        raise

    final_head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    if final_head != rollback_head:
        raise RollbackError(f"rollback checkout ended at {final_head}, expected {rollback_head}")
    return {
        "candidate_head": candidate_head,
        "rollback_head": rollback_head,
        "tracked_runtime_paths": sorted(path for path in tracked if _runtime_path(path)),
        "untracked_runtime_path_count": sum(_runtime_path(path) for path in untracked),
        "runtime_stash_used": bool(stash_commit),
    }


def _docker_command(raw: str) -> list[str]:
    command = shlex.split(raw)
    if not command or Path(command[-1]).name != "docker":
        raise RollbackError("--docker-command must resolve to docker or sudo docker")
    return command


def _compose(docker: Sequence[str], repo: Path, compose_file: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run((*docker, "compose", "-f", compose_file, *args), cwd=repo, check=check)


def rollback(args: argparse.Namespace) -> dict[str, Any]:
    repo = args.repo.resolve()
    report = args.report if args.report.is_absolute() else repo / args.report
    docker = _docker_command(args.docker_command)
    required_services = set(args.required_service or DEFAULT_REQUIRED_SERVICES)
    payload: dict[str, Any] = {
        "work_order": "deployment-controls",
        "status": "STARTED",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "failed_target_sha": args.failed_target_sha,
        "requested_rollback_ref": args.rollback_ref,
        "image_backup_tag": args.image_backup_tag,
        "required_services": sorted(required_services),
        "runtime_evidence_deleted": False,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    stack_stopped = False
    try:
        # Validate source state before stopping a functioning candidate stack.
        tracked = _z_paths(repo, "diff", "--name-only", "-z") | _z_paths(
            repo, "diff", "--cached", "--name-only", "-z"
        )
        untracked = _z_paths(repo, "ls-files", "--others", "--exclude-standard", "-z")
        source_dirty = sorted(path for path in tracked if not _runtime_path(path))
        source_untracked = sorted(path for path in untracked if not _runtime_path(path))
        if source_dirty or source_untracked:
            raise RollbackError(
                "rollback refused before stack stop: "
                + json.dumps(
                    {"source_dirty_paths": source_dirty, "source_untracked_paths": source_untracked},
                    sort_keys=True,
                )
            )

        _compose(docker, repo, args.compose_file, "stop", "--timeout", "60")
        stack_stopped = True
        checkout = _preserve_runtime_and_checkout(repo, args.rollback_ref, args.branch)
        payload.update(checkout)

        _atomic_restore(args.env_backup, repo / ".env", mode=0o600)
        marker = repo / "outputs" / "performance" / "deployed_git_rev"
        marker.parent.mkdir(parents=True, exist_ok=True)
        if args.marker_was_present:
            _atomic_restore(args.marker_backup, marker)
        else:
            marker.unlink(missing_ok=True)

        _run((*docker, "image", "tag", args.image_backup_tag, args.image_live_tag), cwd=repo)
        backup_image_id = _run(
            (*docker, "image", "inspect", "--format", "{{.Id}}", args.image_backup_tag),
            cwd=repo,
        ).stdout.strip()
        live_image_id = _run(
            (*docker, "image", "inspect", "--format", "{{.Id}}", args.image_live_tag),
            cwd=repo,
        ).stdout.strip()
        if not backup_image_id or backup_image_id != live_image_id:
            raise RollbackError("last-known-good image tag was not restored exactly")
        _compose(
            docker,
            repo,
            args.compose_file,
            "up",
            "-d",
            "--no-build",
            "--force-recreate",
        )

        running: set[str] = set()
        for _ in range(args.health_attempts):
            observed = _compose(
                docker,
                repo,
                args.compose_file,
                "ps",
                "--services",
                "--status",
                "running",
                check=False,
            )
            if observed.returncode == 0:
                running = {line.strip() for line in observed.stdout.splitlines() if line.strip()}
            if required_services <= running:
                break
            time.sleep(args.health_interval_seconds)
        missing = sorted(required_services - running)
        if missing:
            raise RollbackError(f"rollback stack is missing running services: {missing}")

        if not args.telemetry_writer.is_file():
            raise RollbackError(f"rollback telemetry writer is missing: {args.telemetry_writer}")
        telemetry_path = repo / "outputs" / "performance" / "vps_telemetry_manifest.json"
        _run(
            (
                sys.executable,
                str(args.telemetry_writer),
                "--repo-root",
                str(repo),
                "--output",
                "outputs/performance/vps_telemetry_manifest.json",
            ),
            cwd=repo,
        )
        try:
            telemetry = json.loads(telemetry_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RollbackError("rollback telemetry manifest was not readable") from exc
        if (
            str(telemetry.get("checkout_git_rev") or "") != checkout["rollback_head"]
            or str(telemetry.get("deployed_git_rev") or "") != checkout["rollback_head"]
        ):
            raise RollbackError("rollback telemetry does not identify the restored revision")

        health_script = repo / args.health_script
        if not health_script.is_file():
            raise RollbackError(f"rollback health script is missing: {args.health_script}")
        health_env = os.environ.copy()
        health_env["PM_VPS_REPO_DIR"] = str(repo)
        health = _run(("bash", str(health_script)), cwd=repo, check=False, env=health_env)
        if health.returncode != 0:
            raise RollbackError(f"rollback health check failed with exit {health.returncode}")

        payload.update(
            {
                "status": "PASS",
                "decision": "ROLLED_BACK_TO_LAST_KNOWN_GOOD",
                "restored_services": sorted(running),
                "health_check_exit_code": health.returncode,
                "restored_image_id": live_image_id,
                "telemetry_manifest_refreshed": True,
                "telemetry_checkout_git_rev": telemetry.get("checkout_git_rev"),
                "telemetry_deployed_git_rev": telemetry.get("deployed_git_rev"),
                "environment_restored": (repo / ".env").read_bytes() == args.env_backup.read_bytes(),
                "marker_restored": (
                    marker.read_bytes() == args.marker_backup.read_bytes()
                    if args.marker_was_present
                    else not marker.exists()
                ),
            }
        )
        if not payload["environment_restored"] or not payload["marker_restored"]:
            raise RollbackError("rollback byte verification failed for environment or deployed marker")
    except Exception as exc:
        payload.update(
            {
                "status": "FAIL",
                "decision": "MANUAL_INTERVENTION_REQUIRED",
                "error": str(exc),
                "stack_was_stopped": stack_stopped,
            }
        )
    payload["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    _write_report(report, payload)
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--rollback-ref", required=True)
    parser.add_argument("--branch", default="main")
    parser.add_argument("--compose-file", default="docker-compose.vps-paper.yml")
    parser.add_argument("--env-backup", type=Path, required=True)
    parser.add_argument("--marker-backup", type=Path, required=True)
    parser.add_argument("--marker-was-present", action="store_true")
    parser.add_argument("--image-backup-tag", required=True)
    parser.add_argument("--image-live-tag", default="polymarket-paper-vps:latest")
    parser.add_argument("--docker-command", default="docker")
    parser.add_argument("--telemetry-writer", type=Path, required=True)
    parser.add_argument("--failed-target-sha", required=True)
    parser.add_argument("--report", type=Path, default=Path("outputs/performance/vps_deploy_rollback.json"))
    parser.add_argument("--required-service", action="append", default=[])
    parser.add_argument("--health-script", default="scripts/check_polymarket_vps_paper.sh")
    parser.add_argument("--health-attempts", type=int, default=30)
    parser.add_argument("--health-interval-seconds", type=float, default=2.0)
    args = parser.parse_args(argv)
    result = rollback(args)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
