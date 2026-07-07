# SuperBru validation data freshness

Generated: `2026-07-07T09:59:18.690904+00:00`

Status: **not_ready**

## Sources

| Source | Rows | Status | Age hours | Coverage |
| --- | ---: | --- | ---: | ---: |
| Locked card | 5 | ok | 0.0 | - |
| Oddspedia grid | 456 | ok | 0.037 | 0/5 |
| Oddspedia comparison | 0 | missing | None | 0/5 |
| SuperBru results | 0 | missing | None | - |
| Market history | 65 | ok | 0.001 | - |
| Prediction log | 35 | ok | 0.0 | - |

## Blockers

- Oddspedia grid covers 0/5 locked-card matches.
- SuperBru results CSV is missing.
- No completed SuperBru result rows are available for realised validation.

## Recommended actions

- Run and commit the Oddspedia pipeline after every locked-card refresh.
- Run the SuperBru results backfill from an authenticated browser session, or add a headless-login results scraper.
- Use this freshness artifact before trusting CLV, ROI, or SuperBru policy validation.
