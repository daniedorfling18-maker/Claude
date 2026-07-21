# Paper-trading loop: current boundary

The old local Docker paper-bot procedure is retired. Its Compose commands,
direct optimizer call and local volume assumptions have been removed.

The active paper/evidence loop is `polymarket-paper-live` inside the single
four-service `docker-compose.vps-paper.yml` project on the Oracle VPS. It is
deployed only through the guarded workflow from reviewed `main`; a local stack
or manual Compose start is not an alternate operating path.

Paper execution is not live execution and cannot authorize funding. The
2026-07-21 audit found a P1 circular quote-proof path in the paper broker and
cohort evidence; do not treat current paper proof as independently verified
until an owner-routed fix and VPS verification land. See
`docs/REPOSITORY_LINE_AUDIT_2026-07-21.md`.

Use `AGENTS.md`, `docs/OPERATING_STATE.md` and
`docs/POLYMARKET_VPS_DOCKER_RUNBOOK.md`. Funding is CLOSED and WO-67 is BLOCKED.
