# Polymarket VPS production runbook

Last reconciled: 2026-07-21, against merged source through PR #354.

This is the operating runbook for the single canonical
`docker-compose.vps-paper.yml` project on the Oracle VPS. It does not
authorize live trading. Funding is CLOSED, WO-67 remains BLOCKED and the stack
is hard-set to scan/paper modes.

## Non-negotiable boundary

- Runtime, collectors, dashboards, watchdogs, Docker and test execution are
  VPS-only.
- Repository path: `/home/opc/Claude`.
- Exactly one production Compose project may write runtime data.
- Routine deployments use `Deploy Polymarket VPS Paper` from reviewed,
  accepted `main`; do not deploy by ad-hoc `git pull`, source copying or a
  manual rebuild.
- Secrets live only in the VPS `.env` or GitHub repository secrets and must
  never appear in source, telemetry or logs.

Read `AGENTS.md` before operating the stack.

## Long-running services

| Service | Purpose | Default memory cap |
|---|---|---:|
| `polymarket-paper-live` | Continuous market collection and paper/evidence loop | `PM_PAPER_MEM_LIMIT` (4 GiB example) |
| `polymarket-dashboard` | Oversight backend, bound to VPS loopback | `PM_DASHBOARD_MEM_LIMIT` (256 MiB) |
| `vps-ops-scheduler` | Governance, proof, collection and maintenance jobs | `VPS_OPS_MEM_LIMIT` (2 GiB) |
| `superbru-auto-pick-watchdog` | SuperBru pre-kickoff watchdog | `SUPERBRU_AUTO_PICK_MEM_LIMIT` (1 GiB) |

`vps-deploy-acceptance` is a deployment-only profile. The deploy workflow
stops and proves the scheduler absent before running this one-shot service, so
both writers cannot operate concurrently.

## Safety configuration

The Compose definition and example environment keep the Polymarket path
non-live:

```text
PM_MODE=scan
POLYMARKET_MODE=scan
POLYMARKET_EXECUTE_LIVE=false
POLYMARKET_LIVE_TRADING=0
```

Do not change those values through this runbook.

## Initial provisioning

Initial host provisioning is a distinct, reviewed operation. Because the
repository is private, do not pipe a raw GitHub URL into a shell or depend on an
unauthenticated clone. Establish authenticated Git access, inspect the accepted
revision and provision `/home/opc/Claude` without starting production services.

Copy `.env.vps-paper.example` to `.env` only on the VPS and populate secrets
there or through the deploy workflow. Do not use the legacy bootstrap script to
start production: it bypasses the accepted-revision and rollback sequence below.
The first production start must use the same guarded deployment path as every
later update.

## Guarded deployment

Workflow:

```text
.github/workflows/deploy-polymarket-vps-paper.yml
```

Required repository secrets:

```text
PM_VPS_HOST
PM_VPS_USER
PM_VPS_SSH_PRIVATE_KEY
```

Optional/conditional secrets:

```text
PM_VPS_PORT
PM_VPS_REPO_DIR
THE_ODDS_API_KEY
SUPERBRU_EMAIL or SUPERBRU_USERNAME
SUPERBRU_PASSWORD
SUPERBRU_LOGIN_URL
SUPERBRU_POOL_URL
SUPERBRU_PLAYER_NAME
SUPERBRU_POOL_KEYWORDS
```

The workflow input `acceptance_run_id` identifies the successful
`Independently Reviewed PR Merge` run whose attestation binds the exact target
`main` SHA. Missing, malformed or mismatched evidence stops before cutover.

The workflow:

1. validates secrets, target capacity, checkout ancestry, acceptance evidence,
   Tailscale enrollment and rollback prerequisites;
2. preserves runtime roots and the current environment;
3. quiesces bind-mounted writers;
4. updates to the exact accepted `main` revision and stamps
   `PM_VPS_DEPLOYED_SHA`;
5. rebuilds the canonical image and four-service Compose project;
6. runs the isolated `vps-deploy-acceptance` profile;
7. refreshes governance and private-dashboard evidence; and
8. restores the last-known-good revision if post-cutover acceptance fails.

Do not replace this sequence with manual `down`, `git pull` and `up`.

## Private dashboard

The container backend is bound only to:

```text
127.0.0.1:8765
```

Tailscale Serve provides authenticated, tailnet-only HTTPS. Tailscale Funnel is
forbidden. On the enrolled VPS:

```bash
cd /home/opc/Claude
bash scripts/configure_polymarket_dashboard_tailscale.sh
```

The script writes the verified URL to:

```text
PM_DASHBOARD_PUBLIC_URL=https://<node>.<tailnet>.ts.net/
```

Despite the variable's historical name, it is not public: it must never contain
a public IP or plain HTTP URL. Read it without printing the rest of `.env`:

```bash
grep '^PM_DASHBOARD_PUBLIC_URL=' /home/opc/Claude/.env
```

The public cloud firewall should have no ingress rule for TCP `8765`.

## Seasonal SuperBru controls

The World Cup locked-card refresh is seasonal. PR #354 added:

```text
OPS_CARD_REFRESH_ENABLED
OPS_LOG_MAX_BYTES
```

- `OPS_CARD_REFRESH_ENABLED=0` makes the scheduler record an intentional skip
  before the Odds API preflight. Use it when the tracked tournament is over.
- `OPS_LOG_MAX_BYTES=52428800` retains the default 50 MiB single-generation
  scheduler-log rotation; `0` disables that protection and is not recommended.

Changing `.env` requires a guarded service recreation to take effect. The
current implementation evaluates the card-refresh disable on the job's normal
cadence, so the old job status can remain visible until that cadence is reached.
PR #354's dashboard readiness loop and scheduler rotation also have open review
follow-ups recorded in the work-order register; do not overstate their timing
bounds.

## Open deployment audit gaps

The 2026-07-21 static audit found controls that must be fixed before the deploy
path can be described as fail-closed end to end:

- Tailscale configuration and workflow rollback suppress Funnel-off failures;
  an existing Funnel route can therefore survive a failed readiness attempt.
- deploy acceptance does not yet prove the exact deployed SHA, all four
  services, or fresh advancing scheduler/health evidence;
- SSH host enrollment uses `ssh-keyscan` trust-on-first-use instead of a pinned
  host-key secret; and
- the bootstrap path can still start production outside the guarded workflow.

Until owner-routed fixes and VPS verification land, treat deployment/transport
acceptance as incomplete and independently check the generated evidence. The
full findings and exact code locations are in
`docs/REPOSITORY_LINE_AUDIT_2026-07-21.md`.

## Read-only operating checks

After SSHing to the VPS:

```bash
cd /home/opc/Claude
docker compose -f docker-compose.vps-paper.yml ps
docker stats --no-stream
bash scripts/check_polymarket_vps_paper.sh
```

Inspect the generated state and deploy evidence:

```text
outputs/performance/operating_state.md
outputs/performance/operating_state.json
outputs/ops_scheduler/deploy_acceptance.json
outputs/ops_scheduler/deploy_acceptance_cycle.json
outputs/performance/vps_deploy_rollback.json
outputs/performance/dashboard_private_transport.json
```

Missing or stale evidence is `UNKNOWN`. Do not infer health from container
presence alone.

## Recovery

Use the guarded deploy rollback and `docs/RESTORE.md`. Never destroy or reset
runtime roots to fix a source checkout. If rollback evidence cannot prove the
last-known-good restoration, the deployment remains failed and requires owner
review.

Legacy Compose files and local launchers remain for repository history and
regression coverage only. They are not alternate production stacks.
