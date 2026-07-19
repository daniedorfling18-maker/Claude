#!/usr/bin/env python3
"""Fail-closed validation for the VPS dashboard's private HTTPS transport."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def evaluate_private_transport(
    *,
    tailscale_status: Mapping[str, Any],
    serve_status: str,
    docker_bindings: str,
    expected_port: int,
    configured_url: str,
) -> dict[str, Any]:
    blockers: list[str] = []
    backend_state = str(tailscale_status.get("BackendState") or "")
    self_node = _mapping(tailscale_status.get("Self"))
    dns_name = str(self_node.get("DNSName") or "").strip().rstrip(".").lower()
    tailscale_ips = [str(value) for value in tailscale_status.get("TailscaleIPs", []) if value]
    private_url = f"https://{dns_name}/" if dns_name else ""
    backend_url = f"http://127.0.0.1:{expected_port}"

    if backend_state != "Running":
        blockers.append("tailscale_backend_not_running")
    if not dns_name or not dns_name.endswith(".ts.net"):
        blockers.append("tailscale_https_dns_name_missing_or_invalid")
    if not tailscale_ips:
        blockers.append("tailscale_ip_missing")
    if not configured_url or configured_url.rstrip("/") != private_url.rstrip("/"):
        blockers.append("dashboard_env_private_url_missing_or_mismatch")

    bindings = [line.strip() for line in docker_bindings.splitlines() if line.strip()]
    if not bindings:
        blockers.append("dashboard_docker_binding_missing")
    expected_suffix = f":{expected_port}"
    public_bindings = [
        value
        for value in bindings
        if not (
            value == f"127.0.0.1{expected_suffix}"
            or value == f"[::1]{expected_suffix}"
        )
    ]
    if public_bindings:
        blockers.append("dashboard_has_non_loopback_binding")

    if private_url and private_url.rstrip("/") not in serve_status:
        blockers.append("tailscale_serve_https_url_missing")
    if backend_url not in serve_status:
        blockers.append("tailscale_serve_backend_target_mismatch")
    serve_status_lower = serve_status.lower()
    if (
        "funnel on" in serve_status_lower
        or "funnel=true" in serve_status_lower
        or "available on the internet" in serve_status_lower
    ):
        blockers.append("public_tailscale_funnel_enabled")

    parsed = urlparse(private_url)
    if parsed.scheme != "https" or parsed.port not in {None, 443}:
        blockers.append("dashboard_private_url_not_https_443")

    return {
        "work_order": "dashboard-transport",
        "status": "PASS" if not blockers else "FAIL",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "transport": "tailscale_serve_https",
        "tailnet_authenticated": backend_state == "Running",
        "private_url": private_url,
        "backend_url": backend_url,
        "docker_bindings": bindings,
        "public_listener_present": bool(public_bindings),
        "serve_target_verified": private_url.rstrip("/") in serve_status and backend_url in serve_status,
        "blockers": blockers,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tailscale-status", type=Path, required=True)
    parser.add_argument("--serve-status", type=Path, required=True)
    parser.add_argument("--docker-bindings", type=Path, required=True)
    parser.add_argument("--expected-port", type=int, default=8765)
    parser.add_argument("--configured-url", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        status = json.loads(args.tailscale_status.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        status = {}
        parse_error = type(exc).__name__
    else:
        parse_error = ""
    payload = evaluate_private_transport(
        tailscale_status=_mapping(status),
        serve_status=args.serve_status.read_text(encoding="utf-8") if args.serve_status.exists() else "",
        docker_bindings=(
            args.docker_bindings.read_text(encoding="utf-8")
            if args.docker_bindings.exists()
            else ""
        ),
        expected_port=args.expected_port,
        configured_url=args.configured_url,
    )
    if parse_error:
        payload["blockers"] = sorted(set([*payload["blockers"], "tailscale_status_malformed"]))
        payload["status"] = "FAIL"
    if args.output is not None:
        _write_json(args.output, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
