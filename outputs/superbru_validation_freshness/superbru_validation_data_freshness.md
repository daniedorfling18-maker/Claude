# SuperBru validation data freshness

Generated: `2026-07-08T08:43:22.322523+00:00`

Status: **not_ready**

## Sources

| Source | Rows | Status | Age hours | Coverage |
| --- | ---: | --- | ---: | ---: |
| Locked card | 4 | ok | 0.0 | - |
| Oddspedia grid | 456 | ok | 0.034 | 0/4 |
| Oddspedia comparison | 0 | missing | None | 0/4 |
| SuperBru results | 0 | missing | None | - |
| Market history | 74 | ok | 0.001 | - |
| Prediction log | 39 | ok | 0.0 | - |

## Blockers

- Oddspedia grid covers 0/4 locked-card matches.
- SuperBru results CSV is missing.
- No completed SuperBru result rows are available for realised validation.

## Recommended actions

- Run and commit the Oddspedia pipeline after every locked-card refresh.
- Run the SuperBru results backfill from an authenticated browser session, or add a headless-login results scraper.
- Use this freshness artifact before trusting CLV, ROI, or SuperBru policy validation.
