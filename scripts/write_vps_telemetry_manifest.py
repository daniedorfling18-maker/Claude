#!/usr/bin/env python3
"""Write the host-observed VPS source/checkout/deploy manifest atomically.

WO-68/68b operating state consumes this artifact. Keeping the host probe in
one helper prevents the telemetry cron and guarded deploy path from publishing
different definitions of "source", "checkout", or "deployed".
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


UNKNOWN = "unknown"
DEFAULT_OUTPUT = Path("outputs/performance/vps_telemetry_manifest.json")


def _git(repo_root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return UNKNOWN
    if completed.returncode != 0:
        return UNKNOWN
    value = completed.stdout.strip().splitlines()
    return value[0].strip() if value and value[0].strip() else UNKNOWN


def _deployed_sha(repo_root: Path) -> str:
    marker = repo_root / "outputs" / "performance" / "deployed_git_rev"
    try:
        value = marker.read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    if value:
        return value

    env_path = repo_root / ".env"
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return UNKNOWN
    for raw_line in reversed(lines):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw_value = stripped.split("=", 1)
        if key.strip() != "PM_VPS_DEPLOYED_SHA":
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1].strip()
        return value or UNKNOWN
    return UNKNOWN


def _source_sha(repo_root: Path) -> str:
    line = _git(repo_root, "ls-remote", "origin", "refs/heads/main")
    return line.split()[0] if line != UNKNOWN and line.split() else UNKNOWN


def _disk_used_percent(path: Path) -> str:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return ""
    if usage.total <= 0:
        return ""
    return f"{round((usage.used / usage.total) * 100)}%"


def _previous_payload(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def build_manifest(repo_root: Path, output_path: Path, *, as_of: str | None = None) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    stamp = as_of or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    deployed = _deployed_sha(repo_root)
    checkout = _git(repo_root, "rev-parse", "HEAD")
    source = _source_sha(repo_root)
    previous = _previous_payload(output_path)

    known = source != UNKNOWN and deployed != UNKNOWN
    if known and source == deployed:
        divergence_started = ""
    elif known:
        prior_source = str(previous.get("source_git_rev") or "")
        prior_deployed = str(previous.get("deployed_git_rev") or "")
        divergence_started = (
            str(previous.get("divergence_started_at_utc") or "")
            if prior_source and prior_deployed and prior_source != prior_deployed
            else stamp
        )
    else:
        divergence_started = str(previous.get("divergence_started_at_utc") or "")

    return {
        "pushed_at_utc": stamp,
        "source_observed_at_utc": stamp if source != UNKNOWN else UNKNOWN,
        "source_git_rev": source,
        "deployed_git_rev": deployed,
        "checkout_git_rev": checkout,
        "divergence_started_at_utc": divergence_started,
        "host": socket.gethostname() or UNKNOWN,
        "disk_used_percent": _disk_used_percent(repo_root),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }


def write_manifest(repo_root: Path, output_path: Path, *, as_of: str | None = None) -> dict[str, Any]:
    target = output_path if output_path.is_absolute() else repo_root / output_path
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = build_manifest(repo_root, target, as_of=as_of)
    temporary = target.with_name(f"{target.name}.tmp.{os.getpid()}")
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--as-of", help="Fixed UTC timestamp for deterministic tests.")
    args = parser.parse_args(argv)
    write_manifest(args.repo_root, args.output, as_of=args.as_of)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
