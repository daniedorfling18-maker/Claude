# Required GitHub Actions Secrets

Configure these secrets in **Settings → Secrets and variables → Actions** on the repository.

## `daily-superbru-robust.yml`

| Secret | Required | Description |
|--------|----------|-------------|
| `THE_ODDS_API_KEY` | **Yes** | API key from [the-odds-api.com](https://the-odds-api.com). Used to fetch live World Cup H2H and totals odds. Without this the daily pipeline fails immediately. |

## `data-inventory.yml`

| Secret | Required | Description |
|--------|----------|-------------|
| `SUPERBRU_LOGIN_URL` | No | Superbru login endpoint. If unset, the Superbru round inventory audit runs without login (public pool data only). |
| `SUPERBRU_USERNAME` | No | Superbru account username / email. |
| `SUPERBRU_PASSWORD` | No | Superbru account password. |

> **Note:** `SUPERBRU_LOGIN_URL`, `SUPERBRU_USERNAME`, and `SUPERBRU_PASSWORD` are optional — the `data-inventory` workflow uses `continue-on-error: true` for the Superbru audit step, so it will not fail if these are absent.

## No secrets required

`ci.yml` runs purely offline using committed example fixtures and odds snapshots and does not require any secrets.
