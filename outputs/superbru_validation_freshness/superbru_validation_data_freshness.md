# SuperBru validation data freshness

Generated: `2026-07-03T10:06:13.501070+00:00`

Status: **not_ready**

## Sources

| Source | Rows | Status | Age hours | Coverage |
| --- | ---: | --- | ---: | ---: |
| Locked card | 9 | ok | 0.0 | - |
| Oddspedia grid | 456 | ok | 0.043 | 0/9 |
| Oddspedia comparison | 0 | missing | None | 0/9 |
| SuperBru results | 0 | missing | None | - |
| Market history | 9 | ok | 0.001 | - |
| Prediction log | 9 | ok | 0.0 | - |

## Blockers

- Oddspedia grid covers 0/9 locked-card matches.
- SuperBru results CSV is missing.
- No completed SuperBru result rows are available for realised validation.

## Recommended actions

- Run and commit the Oddspedia pipeline after every locked-card refresh.
- Run the SuperBru results backfill from an authenticated browser session, or add a headless-login results scraper.
- Use this freshness artifact before trusting CLV, ROI, or SuperBru policy validation.
