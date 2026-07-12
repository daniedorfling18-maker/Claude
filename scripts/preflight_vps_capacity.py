#!/usr/bin/env python3
"""Fail closed before a VPS deploy can replace a healthy running stack.

The preflight is host-only and reporting-only. It reads declared compose
memory ceilings plus registered CPU/memory/disk floors, writes a JSON artifact,
and exits nonzero when the host cannot safely absorb the requested stack.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any, Mapping


GIB = 1024**3
MIB = 1024**2
REGISTERED_MIN_VCPUS = 2
REGISTERED_MIN_FREE_DISK_BYTES = 6 * GIB
REGISTERED_MIN_AVAILABLE_MEMORY_BYTES = 512 * MIB
MEMORY_LIMIT_PATTERN = re.compile(r"^\s*mem_limit\s*:\s*(?P<value>[^#]+?)\s*$")
INTERPOLATION_PATTERN = re.compile(r"^\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::?-)(?P<default>[^}]+)\}$")


def parse_size(value: Any) -> int:
    text = str(value or "").strip().strip('"\'').lower().replace(" ", "")
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([kmgt]?i?b?)?", text)
    if not match:
        raise ValueError(f"invalid memory/disk size: {value!r}")
    number = float(match.group(1))
    suffix = match.group(2) or "b"
    multipliers = {
        "b": 1,
        "k": 1024,
        "kb": 1024,
        "kib": 1024,
        "m": MIB,
        "mb": MIB,
        "mib": MIB,
        "g": GIB,
        "gb": GIB,
        "gib": GIB,
        "t": 1024 * GIB,
        "tb": 1024 * GIB,
        "tib": 1024 * GIB,
    }
    if suffix not in multipliers:
        raise ValueError(f"unsupported size suffix: {suffix!r}")
    return int(number * multipliers[suffix])


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def compose_memory_limits(compose_path: Path, env_path: Path) -> list[dict[str, Any]]:
    env_file = read_env_file(env_path)
    limits: list[dict[str, Any]] = []
    current_service = "unknown"
    for line in compose_path.read_text(encoding="utf-8").splitlines():
        service_match = re.match(r"^  ([A-Za-z0-9_.-]+):\s*$", line)
        if service_match:
            current_service = service_match.group(1)
        match = MEMORY_LIMIT_PATTERN.match(line)
        if not match:
            continue
        expression = match.group("value").strip().strip('"\'')
        interpolation = INTERPOLATION_PATTERN.match(expression)
        source = "literal"
        if interpolation:
            name = interpolation.group("name")
            default = interpolation.group("default")
            expression = os.getenv(name) or env_file.get(name) or default
            source = name
        limits.append(
            {
                "service": current_service,
                "configured": expression,
                "bytes": parse_size(expression),
                "source": source,
            }
        )
    if not limits:
        raise ValueError(f"no mem_limit declarations found in {compose_path}")
    return limits


def _meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        amount = raw.strip().split()[0]
        if amount.isdigit():
            values[key] = int(amount) * 1024
    return values


def collect_host_metrics(root: Path) -> dict[str, int]:
    memory = _meminfo()
    disk = shutil.disk_usage(root)
    return {
        "vcpu_count": int(os.cpu_count() or 0),
        "memory_total_bytes": int(memory.get("MemTotal", 0)),
        "memory_available_bytes": int(memory.get("MemAvailable", 0)),
        "swap_total_bytes": int(memory.get("SwapTotal", 0)),
        "disk_total_bytes": int(disk.total),
        "disk_free_bytes": int(disk.free),
    }


def _tightened_int(env: Mapping[str, str], key: str, registered: int) -> int:
    raw = str(env.get(key) or "").strip()
    if not raw:
        return registered
    return max(registered, int(raw))


def _tightened_size(env: Mapping[str, str], key: str, registered: int) -> int:
    raw = str(env.get(key) or "").strip()
    if not raw:
        return registered
    return max(registered, parse_size(raw))


def evaluate_capacity(
    metrics: Mapping[str, int],
    memory_limits: list[Mapping[str, Any]],
    *,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    environment = environment or {}
    requested_memory = sum(int(row["bytes"]) for row in memory_limits)
    requirements = {
        "minimum_vcpus": _tightened_int(environment, "PM_VPS_MIN_VCPUS", REGISTERED_MIN_VCPUS),
        "minimum_total_memory_bytes": requested_memory,
        "minimum_available_memory_bytes": _tightened_size(
            environment,
            "PM_VPS_MIN_AVAILABLE_MEMORY",
            REGISTERED_MIN_AVAILABLE_MEMORY_BYTES,
        ),
        "minimum_free_disk_bytes": _tightened_size(
            environment,
            "PM_VPS_MIN_FREE_DISK",
            REGISTERED_MIN_FREE_DISK_BYTES,
        ),
    }
    checks = [
        {
            "id": "vcpu_capacity",
            "measured": int(metrics.get("vcpu_count", 0)),
            "required": requirements["minimum_vcpus"],
            "passed": int(metrics.get("vcpu_count", 0)) >= requirements["minimum_vcpus"],
            "unit": "count",
        },
        {
            "id": "compose_memory_commit",
            "measured": int(metrics.get("memory_total_bytes", 0)),
            "required": requirements["minimum_total_memory_bytes"],
            "passed": int(metrics.get("memory_total_bytes", 0)) >= requirements["minimum_total_memory_bytes"],
            "unit": "bytes",
        },
        {
            "id": "available_memory_headroom",
            "measured": int(metrics.get("memory_available_bytes", 0)),
            "required": requirements["minimum_available_memory_bytes"],
            "passed": int(metrics.get("memory_available_bytes", 0)) >= requirements["minimum_available_memory_bytes"],
            "unit": "bytes",
        },
        {
            "id": "deployment_disk_headroom",
            "measured": int(metrics.get("disk_free_bytes", 0)),
            "required": requirements["minimum_free_disk_bytes"],
            "passed": int(metrics.get("disk_free_bytes", 0)) >= requirements["minimum_free_disk_bytes"],
            "unit": "bytes",
        },
    ]
    blockers = [row["id"] for row in checks if not row["passed"]]
    return {
        "status": "pass" if not blockers else "fail",
        "decision": "DEPLOY_ALLOWED" if not blockers else "REFUSE_DEPLOY_KEEP_EXISTING_STACK",
        "checks": checks,
        "blockers": blockers,
        "host_metrics": dict(metrics),
        "requirements": requirements,
        "compose_memory_limits": [dict(row) for row in memory_limits],
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose", type=Path, default=Path("docker-compose.vps-paper.yml"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output", type=Path, default=Path("outputs/performance/vps_capacity_preflight.json"))
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()

    env_file = read_env_file(args.env_file)
    limits = compose_memory_limits(args.compose, args.env_file)
    result = evaluate_capacity(collect_host_metrics(args.root), limits, environment={**env_file, **os.environ})
    result.update(
        {
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "compose_path": str(args.compose),
            "env_path": str(args.env_file),
            "reporting_only": True,
        }
    )
    write_json(args.output, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "pass" else 3


if __name__ == "__main__":
    raise SystemExit(main())
