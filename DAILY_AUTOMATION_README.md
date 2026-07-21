# SuperBru automation operations

Last reconciled: 2026-07-21.

The prior local Windows/Oddspedia “daily production flow” is retired. All
runtime and verification are VPS-only under `AGENTS.md`. The tracked World
Cup tournament has ended, so the locked-card refresh should normally be
quiesced until another explicitly configured competition is introduced.

## Current production components

Two components in `docker-compose.vps-paper.yml` own recurring SuperBru work:

| Component | Default cadence | Responsibility |
|---|---:|---|
| `superbru-auto-pick-watchdog` | 900 seconds | Checks SuperBru for matches in the configured pre-kickoff window and writes submission evidence |
| `vps-ops-scheduler` locked-card job | 43,200 seconds | Runs the seasonal card-refresh chain when enabled |

The GitHub Actions workflows `auto_pick.yml`,
`refresh-locked-superbru-card.yml` and `superbru-clv-snapshot.yml` are
manual-dispatch fallbacks/diagnostics. They no longer carry schedules.
`check_superbru_fixtures.yml` is intentionally disabled and fails closed.

## Seasonal posture

For the completed tournament:

```text
OPS_CARD_REFRESH_ENABLED=0
SUPERBRU_AUTO_PICK_ENABLED=false
```

This records an intentional skip before the odds preflight and avoids repeated
zero-event failures or quota use. The scheduler evaluates the switch on the
job's normal cadence, so its previously stale status may remain visible until
that cadence.

The watchdog has a separate switch:

```text
SUPERBRU_AUTO_PICK_ENABLED
```

Disable it when there is no active competition or valid pick window. Changing
competition, pool, schedule or credentials is an operating decision; do not
silently repurpose the World Cup defaults.

## Source of truth and artifacts

The historical locked card remains:

```text
outputs/final_locked_picks/superbru_final_card.csv
```

Operational evidence is written under:

```text
outputs/pregame_checks/auto_pick_vps/
outputs/ops_scheduler/status.json
outputs/ops_scheduler/status.csv
```

The generated operating state, not this README, determines whether the jobs are
fresh, intentionally skipped, failed or unknown.

## Credentials

SuperBru credentials belong only in GitHub repository secrets or the VPS
`.env`:

```text
SUPERBRU_EMAIL or SUPERBRU_USERNAME
SUPERBRU_PASSWORD
SUPERBRU_LOGIN_URL
SUPERBRU_POOL_URL
SUPERBRU_PLAYER_NAME
SUPERBRU_POOL_KEYWORDS
```

`THE_ODDS_API_KEY` is required only for paths that fetch odds. Never place
credential values in source, logs, artifacts or chat.

## Deployment and checks

Use `Deploy Polymarket VPS Paper` to propagate reviewed source and sealed
configuration. Do not run the automation locally or deploy through ad-hoc
`git pull`.

Read-only VPS checks:

```bash
cd /home/opc/Claude
docker compose -f docker-compose.vps-paper.yml ps
docker compose -f docker-compose.vps-paper.yml logs --tail=100 superbru-auto-pick-watchdog
docker compose -f docker-compose.vps-paper.yml logs --tail=100 vps-ops-scheduler
```

Then inspect:

```text
outputs/performance/operating_state.md
outputs/pregame_checks/auto_pick_vps/latest_watchdog_status.json
outputs/ops_scheduler/status.json
```

Missing or stale evidence is `UNKNOWN`; container presence is not proof of a
successful submission.

The 2026-07-21 audit found that `--require-submission` can still return success
when zero fixtures are queued, and that scheduler quota/auth/network failures
can be mislabeled as intentional skips. Until those paths are fixed, corroborate
submission and job outcome from fresh, match-specific evidence; see
`docs/REPOSITORY_LINE_AUDIT_2026-07-21.md`.

## Historical research pipeline

The Oddspedia, pool-intelligence, calibration and World Cup backtesting scripts
remain available for dated research and regression coverage. They are not the
production scheduler. Any approved rerun must occur in an isolated VPS
environment and must preserve evidence-class and point-in-time rules.
