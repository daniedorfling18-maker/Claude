# GitHub Actions secrets

Last reconciled: 2026-07-21.

Configure secrets in **Settings → Secrets and variables → Actions**. Never
place values in source, workflow logs, artifacts, telemetry or documentation.

The repository currently contains eleven workflows:

| Workflow | Trigger posture | Secrets |
|---|---|---|
| `required-pr-gate.yml` | Pull request | None |
| `independent-pr-merge.yml` | Owner-authored issue comment plus independent review evidence | None beyond scoped `GITHUB_TOKEN`/OIDC |
| `ci.yml` | Manual dispatch | None |
| `repo-audit-bundle.yml` | Manual dispatch | None |
| `deploy-polymarket-vps-paper.yml` | Manual dispatch | VPS, odds and optional SuperBru secrets below |
| `polymarket-vps-governance-refresh.yml` | Manual dispatch fallback | VPS SSH secrets |
| `polymarket-vps-proof-health.yml` | Manual dispatch fallback | VPS SSH secrets |
| `auto_pick.yml` | Manual dispatch fallback | SuperBru plus odds secrets |
| `refresh-locked-superbru-card.yml` | Manual dispatch fallback | Odds plus optional SuperBru context |
| `superbru-clv-snapshot.yml` | Manual dispatch diagnostic | Odds API key |
| `check_superbru_fixtures.yml` | Manual dispatch; intentionally disabled/fails closed | None while disabled |

Recurring production work runs inside the VPS
`vps-ops-scheduler`/`superbru-auto-pick-watchdog`, not on workflow
schedules.

## VPS connection

Used by deployment and the manual VPS governance/proof workflows:

| Secret | Required | Purpose |
|---|---:|---|
| `PM_VPS_HOST` | Yes | VPS SSH host or DNS name |
| `PM_VPS_USER` | Yes | VPS SSH user; production convention is `opc` |
| `PM_VPS_SSH_PRIVATE_KEY` | Yes | Private key matching the VPS authorized public key |
| `PM_VPS_PORT` | No | SSH port; defaults to `22` |
| `PM_VPS_REPO_DIR` | No | Repository path; production convention is `/home/opc/Claude` |

`deploy-polymarket-vps-paper.yml` also requires the
`acceptance_run_id` workflow input. It must identify the successful
independent-merge artifact for the exact `main` SHA; it is not a secret.

## Odds provider

| Secret | Required | Purpose |
|---|---:|---|
| `THE_ODDS_API_KEY` | Conditional | Sharp-anchor and SuperBru odds fetches |

The deploy workflow can inject this value into the VPS `.env`. A missing key
must remain visible as missing/disabled evidence; it must never be replaced by
a placeholder value.

## SuperBru

Used by the deploy workflow to seed the VPS environment and by manual
SuperBru workflow fallbacks:

| Secret | Required | Purpose |
|---|---:|---|
| `SUPERBRU_EMAIL` or `SUPERBRU_USERNAME` | Yes for authenticated paths | Login identity |
| `SUPERBRU_PASSWORD` | Yes for authenticated paths | Login password |
| `SUPERBRU_LOGIN_URL` | No | Login endpoint |
| `SUPERBRU_POOL_URL` | Yes for authenticated production | Explicit allowlisted pool/matches URL; do not rely on a source default |
| `SUPERBRU_PLAYER_NAME` | No | Player identity used by pool-position logic |
| `SUPERBRU_POOL_KEYWORDS` | No | Comma-separated pool identity guard |
| `SUPERBRU_PAGE_TIMEZONE` | No | Page/kickoff timezone for the manual auto-pick workflow |

Competition-specific defaults in source are not authority to enable a new
tournament. Review fixture identity, pool, aliases, timezone, odds scope and
submission windows before enabling any successor competition.

The current deploy workflow learns the SSH host key with `ssh-keyscan`. That is
trust-on-first-use, not pinned identity. A future owner-routed remediation should
store and verify an exact host-key fingerprint/known-hosts value; do not describe
the present connection as cryptographically pinned.

## Workflow-specific notes

### Required PR gate

The required gate uses the self-hosted ARM64/Python 3.11 runner with read-only
repository permissions and no persisted checkout credential. It requires no
project secret.

### Independent merge

This workflow is driven by an exact owner-authored
`/independent-merge <40-character-head-sha>` PR comment and validates a
distinct current-head reviewer. Its scoped token/OIDC permissions are defined
in the workflow; no PAT is documented or required.

### Deployment

The deployment workflow runs on the repository-scoped self-hosted ARM64 runner.
It validates the exact acceptance artifact, preserves runtime data and
environment state, updates to accepted `main`, rebuilds the canonical Compose
project, runs isolated acceptance and retains rollback evidence.

### Manual SuperBru workflows

`auto_pick.yml`, `refresh-locked-superbru-card.yml` and
`superbru-clv-snapshot.yml` are manual fallback/diagnostic workflows. Their
historical names and comments may mention schedules, but their current triggers
do not contain a schedule. `check_superbru_fixtures.yml` exits non-zero by
design while disabled.
