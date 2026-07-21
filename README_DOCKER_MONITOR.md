# Retired Docker monitor stack

This page is retained only to explain the legacy `docker-compose.monitor.yml`
surface. It is not an operating runbook and its former activation examples have
been removed.

The monitor stack predated the canonical Oracle VPS deployment. It coupled a
market scanner to a long/short intent generator and wrote shared files below
`outputs/polymarket/`. It is not a supported production stack, is not an
alternate deployment path and must not be started locally.

Current rules:

- all runtime and verification are VPS-only;
- the single production project is `docker-compose.vps-paper.yml` with four
  long-running services;
- the dashboard is loopback-bound and available only through authenticated
  Tailscale Serve;
- funding is CLOSED, WO-67 is BLOCKED, and no autonomous live-order path is
  approved;
- legacy Compose files remain solely for history and regression coverage.

Read `AGENTS.md`, `docs/OPERATING_STATE.md` and
`docs/POLYMARKET_VPS_DOCKER_RUNBOOK.md` for the current contract. The dated
static audit and remaining implementation gaps are in
`docs/REPOSITORY_LINE_AUDIT_2026-07-21.md`.
