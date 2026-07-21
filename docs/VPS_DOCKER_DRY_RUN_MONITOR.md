# Legacy VPS monitor guide

The old monitor-stack procedure is retired. It is neither the current health
check nor an alternate production deployment, and its executable steps have
been removed.

Use the single guarded `docker-compose.vps-paper.yml` project documented in
`docs/POLYMARKET_VPS_DOCKER_RUNBOOK.md`. Point-in-time health comes from
`outputs/performance/operating_state.{md,json}` on the VPS under the contract in
`docs/OPERATING_STATE.md`; container presence alone is insufficient.

All runtime and verification are VPS-only. Funding is CLOSED, WO-67 is BLOCKED
and no autonomous live-order path is approved.
