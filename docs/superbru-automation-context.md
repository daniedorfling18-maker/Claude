# SuperBru automation context

Last reconciled: 2026-07-21.

This document describes the stable production split after recurring GitHub
Actions schedules moved to the VPS. It does not state that an active tournament
exists or that any current submission is due. The World Cup card-refresh job is
seasonal and should be quiesced when the competition is over.

The committed `.env.vps-paper.example` disables both seasonal automation
switches. Actual VPS values are dynamic and must be read through generated
operating evidence without exposing the environment.

## Production ownership

### VPS auto-pick watchdog

```text
service: superbru-auto-pick-watchdog
entrypoint: scripts/run_superbru_auto_pick_watchdog.sh
default interval: 900 seconds
output root: outputs/pregame_checks/auto_pick_vps
```

The watchdog checks the configured SuperBru page for matches inside the
pre-kickoff/revision window. Its active runner is
`scripts/auto_pick_match_scoped_smart_odds.py`, which delegates to the
match-scoped runner while applying alias-aware team matching and submission.

The watchdog writes `latest_watchdog_status.json` and `watchdog.log`.
`dry_run=false` in its status describes the SuperBru competition action; it
does not enable or invoke Polymarket trading.

### VPS locked-card refresh

```text
service: vps-ops-scheduler
job: locked_card_refresh
default interval: 43,200 seconds
switch: OPS_CARD_REFRESH_ENABLED
```

The refresh chain prepares the historical committed card and associated
analysis outputs. It does not own the pre-kickoff polling loop.

PR #354 added an intentional seasonal skip. Set
`OPS_CARD_REFRESH_ENABLED=0` in the VPS `.env` when the tracked competition
has ended. The switch is evaluated when the normal job cadence is reached, so
an older status can remain visible until then.

## GitHub workflow posture

The following workflows are manual-dispatch only:

- `.github/workflows/auto_pick.yml`;
- `.github/workflows/refresh-locked-superbru-card.yml`; and
- `.github/workflows/superbru-clv-snapshot.yml`.

`.github/workflows/check_superbru_fixtures.yml` is intentionally disabled and
fails closed pending a future, separately authorized competition/schedule
design. None of these workflows is the recurring production scheduler.

## Pick and evidence sources

The historical committed fallback card is:

```text
outputs/final_locked_picks/superbru_final_card.csv
```

Submission evidence and diagnostics are under:

```text
outputs/pregame_checks/auto_pick_vps/
outputs/pregame_checks/auto_pick/
```

Scheduler status is under:

```text
outputs/ops_scheduler/status.json
outputs/ops_scheduler/status.csv
```

The generated operating state determines freshness and status. A committed card
or a running container is not evidence that a pick was submitted.

## Match-scoped odds and fallback behavior

When the configured odds credential and a matching imminent fixture are
available, the match-scoped runner can obtain a narrow odds snapshot and
recompute the pick. Its historical fallback is the committed card row.

This behavior is competition-specific. Before any new competition is enabled,
review:

- the pool and login URLs;
- team aliases and fixture identity;
- page timezone and kickoff timestamps;
- pre-kickoff and revision windows;
- odds region/market scope and quota;
- fallback-card validity; and
- locked/absent input fail-closed behavior.

Do not carry the World Cup defaults into another competition without that
review.

## Team aliases

`scripts/team_name_aliases.py` supplies canonical team keys used across card,
odds-event and browser matching. The alias-aware entrypoint and submission
adapter prevent display labels such as `USA` from silently failing to match a
canonical card name such as `United States`.

Alias coverage is an identity control, not permission to submit an ambiguous
fixture. A missing or conflicting match remains blocked.

## Secrets

Secrets are supplied by the guarded deploy workflow and stored only in the VPS
`.env`, or are provided to a manual GitHub workflow:

```text
SUPERBRU_EMAIL or SUPERBRU_USERNAME
SUPERBRU_PASSWORD
SUPERBRU_LOGIN_URL
SUPERBRU_POOL_URL
SUPERBRU_PLAYER_NAME
SUPERBRU_POOL_KEYWORDS
THE_ODDS_API_KEY
```

Do not commit, log, display or copy their values into artifacts.

## Status interpretation

The match-scoped runner can report states including:

```text
submitted
dry_run
no_pick_available
pick_card_missing
locked_skipped
no_inputs_skipped
not_in_window
submit_failed
```

Read the exact artifact and timestamp rather than inferring success from the
latest log line. Missing or stale evidence is `UNKNOWN`.

The current match-scoped runner can return success under
`--require-submission` with zero queued fixtures, and scheduler preflight can
misclassify auth/network failures as quota skips. Treat submission/job success
as unproved unless fresh match-specific artifacts corroborate it. These and
other ops findings are recorded in
`docs/REPOSITORY_LINE_AUDIT_2026-07-21.md`.

## Operations

Deployment follows `docs/POLYMARKET_VPS_DOCKER_RUNBOOK.md`. Read-only checks
run on the VPS:

```bash
cd /home/opc/Claude
docker compose -f docker-compose.vps-paper.yml ps
docker compose -f docker-compose.vps-paper.yml logs --tail=100 superbru-auto-pick-watchdog
docker compose -f docker-compose.vps-paper.yml logs --tail=100 vps-ops-scheduler
```

Do not run the watchdog, refresh pipeline, browser submission or tests on the
local workstation.
