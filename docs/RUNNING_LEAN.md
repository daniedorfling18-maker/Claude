# Resource controls: current boundary

The former local-capacity guide is retired. Its instructions for starting
alternate Compose stacks, local PowerShell launchers and direct Python loops
have been removed.

All runtime and verification run on the Oracle VPS. The single supported
production project is `docker-compose.vps-paper.yml`, whose four long-running
services have explicit memory limits in `.env.vps-paper.example`. Do not start
legacy Compose files to work around capacity and do not create a duplicate
writer.

Capacity is checked by the guarded deployment before cutover. Point-in-time
resource and service evidence belongs in the generated operating state; missing
or stale evidence is `UNKNOWN`. Use read-only operator checks from
`docs/POLYMARKET_VPS_DOCKER_RUNBOOK.md` after SSHing to the VPS.

Funding is CLOSED and WO-67 is BLOCKED. Resource availability never authorizes
live execution.
