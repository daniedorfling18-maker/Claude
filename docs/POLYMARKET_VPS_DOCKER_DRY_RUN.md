# Legacy VPS Docker dry-run guide

This page is retained as a historical pointer only. The earlier two-service
dry-run instructions are superseded and must not be used to create a second
writer or alternate production stack.

The only supported production project is `docker-compose.vps-paper.yml` in
`/home/opc/Claude`, with four long-running services and guarded deployment from
reviewed `main`. Runtime and verification are VPS-only. The dashboard must be
loopback-bound and reachable only through authenticated Tailscale Serve.

Follow `AGENTS.md`, `docs/OPERATING_STATE.md` and
`docs/POLYMARKET_VPS_DOCKER_RUNBOOK.md`. Funding is CLOSED; WO-67 is BLOCKED.
