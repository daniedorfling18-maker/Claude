# Required GitHub Actions Secrets

Configure these secrets in **Settings → Secrets and variables → Actions** on the repository.

The repository runs eight workflows. The sections below list secrets required for required checks and for authenticated workflow paths.

## `ci.yml` (push / pull request)

| Secret | Required | Description |
|--------|----------|-------------|
| `THE_ODDS_API_KEY` | **Yes** | Used by the online smoke prediction step. Without it, CI intentionally fails before the online smoke can run. |

> The offline tests and offline smoke prediction do not need secrets, but the current CI workflow includes the online smoke as a required check.

## `refresh-locked-superbru-card.yml` (scheduled, twice daily)

| Secret | Required | Description |
|--------|----------|-------------|
| `THE_ODDS_API_KEY` | **Yes** | API key from [the-odds-api.com](https://the-odds-api.com). The scheduled refresh fetches and validates fresh market odds before rebuilding the locked SuperBru card. |

> Commits are pushed with the automatic `GITHUB_TOKEN` (the workflow grants `contents: write`); no personal token is needed for the commit step.

## `auto_pick.yml` (scheduled, ~25 min before each kickoff)

| Secret | Required | Description |
|--------|----------|-------------|
| `SUPERBRU_USERNAME` | **Yes** | Superbru account username / email (exposed to the script as `SUPERBRU_EMAIL`). |
| `SUPERBRU_PASSWORD` | **Yes** | Superbru account password. |
| `THE_ODDS_API_KEY` | **Yes** | Used for the live one-match odds recompute before submission. Without it the run falls back to the committed locked-card pick. |
| `SUPERBRU_LOGIN_URL` | No | Login endpoint. Defaults to `https://www.superbru.com/login`. |
| `SUPERBRU_POOL_URL` | No | Pool view URL. A World Cup 2026 pool default is hard-coded if unset. |
| `SUPERBRU_PLAYER_NAME` | No | Leader/chaser player name for pool-position logic. Defaults to `Danie`. |
| `SUPERBRU_POOL_KEYWORDS` | No | Comma-separated pool-name keywords for leaderboard matching. Has a built-in default. |

## `check_superbru_fixtures.yml` (scheduled, daily)

| Secret | Required | Description |
|--------|----------|-------------|
| `SUPERBRU_USERNAME` | **Yes** | Superbru account username / email. |
| `SUPERBRU_PASSWORD` | **Yes** | Superbru account password. |
| `WORKFLOW_PAT` | No | Personal access token with `repo` + `workflow` scope. Required only to open the auto-PR that adds missing `auto_pick.yml` cron entries (pushing a workflow file needs `workflow` scope). Without it the job still uploads the fixture report and suggested cron lines but cannot open the PR. |

## `data-inventory.yml` (scheduled, daily)

| Secret | Required | Description |
|--------|----------|-------------|
| `SUPERBRU_LOGIN_URL` | No | Superbru login endpoint. If unset, the Superbru inventory audit runs without login (public pool data only). |
| `SUPERBRU_USERNAME` | No | Superbru account username / email. |
| `SUPERBRU_PASSWORD` | No | Superbru account password. |
| `SUPERBRU_POOL_URL` | No | Pool view URL. A default is used if unset. |

> The Superbru audit step uses `continue-on-error: true`, so the workflow does not fail when these are absent.

## `deploy-polymarket-vps-paper.yml` (manual VPS deployment)

| Secret | Required | Description |
|--------|----------|-------------|
| `PM_VPS_HOST` | **Yes** | Public IPv4/DNS of the Oracle VPS, for example `129.151.178.42`. |
| `PM_VPS_USER` | **Yes** | SSH user for the Ubuntu VPS, usually `ubuntu`. |
| `PM_VPS_SSH_PRIVATE_KEY` | **Yes** | Private key matching the public key installed on the VPS. Store the full OpenSSH private key text. |
| `PM_VPS_PORT` | No | SSH port. Defaults to `22`. |
| `PM_VPS_REPO_DIR` | No | Repo directory on the VPS. Defaults to `~/Claude`. |
| `THE_ODDS_API_KEY` | Recommended | Injected into the VPS `.env` so sharp-anchor odds fetching can run. Without it, deployment still works but the highest-priority independent anchor remains disabled. |

The workflow pulls `main`, rebuilds `docker-compose.vps-paper.yml`, forces a dashboard render, and verifies that the deployed dashboard contains the current proof-gate/evidence-funnel sections.

## No secrets required

- `repo-audit-bundle.yml` only archives and audits the tracked source tree.
- `polymarket-mispricing-bot.yml` is manual dry-run only under the committed workflow defaults.
