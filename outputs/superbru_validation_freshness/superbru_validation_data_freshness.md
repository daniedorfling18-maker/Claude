# SuperBru validation data freshness

Generated: `2026-07-06T10:58:17.448793+00:00`

Status: **not_ready**

## Sources

| Source | Rows | Status | Age hours | Coverage |
| --- | ---: | --- | ---: | ---: |
| Locked card | 6 | ok | 0.0 | - |
| Oddspedia grid | 456 | ok | 0.039 | 0/6 |
| Oddspedia comparison | 0 | missing | None | 0/6 |
| SuperBru results | 0 | missing | None | - |
| Market history | 54 | ok | 0.0 | - |
| Prediction log | 30 | ok | 0.0 | - |

## Blockers

- Oddspedia grid covers 0/6 locked-card matches.
- SuperBru results CSV is missing.
- No completed SuperBru result rows are available for realised validation.

## Recommended actions

- Run and commit the Oddspedia pipeline after every locked-card refresh.
- Run the SuperBru results backfill from an authenticated browser session, or add a headless-login results scraper.
- Use this freshness artifact before trusting CLV, ROI, or SuperBru policy validation.
