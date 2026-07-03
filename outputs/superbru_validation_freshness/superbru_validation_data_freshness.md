# SuperBru validation data freshness

Generated: `2026-07-03T06:01:58.230884+00:00`

Status: **not_ready**

## Sources

| Source | Rows | Status | Age hours | Coverage |
| --- | ---: | --- | ---: | ---: |
| Locked card | 11 | ok | 13.784 | - |
| Oddspedia grid | 456 | stale | 188.741 | 0/11 |
| Oddspedia comparison | 0 | missing | None | 0/11 |
| SuperBru results | 0 | missing | None | - |
| Market history | 13 | ok | 67.559 | - |
| Prediction log | 0 | missing | None | - |

## Blockers

- Oddspedia grid is stale.
- Oddspedia grid covers 0/11 locked-card matches.
- SuperBru results CSV is missing.
- No completed SuperBru result rows are available for realised validation.

## Recommended actions

- Run and commit the Oddspedia pipeline after every locked-card refresh.
- Run the SuperBru results backfill from an authenticated browser session, or add a headless-login results scraper.
- Use this freshness artifact before trusting CLV, ROI, or SuperBru policy validation.
