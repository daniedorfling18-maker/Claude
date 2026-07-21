# Mispricing bot: historical design note

This page describes a retired scanner/market-making prototype. Its former
credential, deployment and live-activation instructions have been removed.
The modules and legacy Compose files remain for repository history, diagnostics
and regression coverage only.

The current production boundary is the four-service
`docker-compose.vps-paper.yml` stack on the Oracle VPS. It runs scan/paper paths,
binds the dashboard to loopback and exposes it only through authenticated
Tailscale Serve. It is not an autonomous live trading system.

Funding is CLOSED and WO-67 remains BLOCKED. No environment flag, private key,
container image or regional location constitutes approval to trade. Read
`AGENTS.md`, `docs/OPERATING_STATE.md`,
`docs/POLYMARKET_VPS_DOCKER_RUNBOOK.md` and
`docs/REPOSITORY_LINE_AUDIT_2026-07-21.md`.
