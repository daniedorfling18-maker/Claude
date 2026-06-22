# Required GitHub Actions Secrets

Configure these secrets in **Settings → Secrets and variables → Actions** on the repository.

The repository runs six workflows. Only three of them need secrets.

## `refresh-locked-superbru-card.yml` (scheduled, twice daily)

| Secret | Required | Description |
|--------|----------|-------------|
| `THE_ODDS_API_KEY` | No | API key from [the-odds-api.com](https://the-odds-api.com). The scheduled refresh runs with `--skip-market-odds-fetch` and reuses committed cached odds, so the key is only consumed if you remove that flag to refresh market odds during a rebuild. |

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

## No secrets required

- `ci.yml` runs entirely offline using committed example fixtures and odds snapshots.
- `repo-audit-bundle.yml` only archives and audits the tracked source tree.
