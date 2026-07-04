# SuperBru validation data freshness

Generated: `2026-07-04T08:50:58.020733+00:00`

Status: **not_ready**

## Sources

| Source | Rows | Status | Age hours | Coverage |
| --- | ---: | --- | ---: | ---: |
| Locked card | 8 | ok | 0.0 | - |
| Oddspedia grid | 456 | ok | 0.044 | 0/8 |
| Oddspedia comparison | 0 | missing | None | 0/8 |
| SuperBru results | 0 | missing | None | - |
| Market history | 26 | ok | 0.001 | - |
| Prediction log | 17 | ok | 0.0 | - |

## Blockers

- Oddspedia grid covers 0/8 locked-card matches.
- SuperBru results CSV is missing.
- No completed SuperBru result rows are available for realised validation.

## Recommended actions

- Run and commit the Oddspedia pipeline after every locked-card refresh.
- Run the SuperBru results backfill from an authenticated browser session, or add a headless-login results scraper.
- Use this freshness artifact before trusting CLV, ROI, or SuperBru policy validation.
