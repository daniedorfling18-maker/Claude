# Oddspedia pipeline status

Generated: `2026-07-07T09:59:18.633028+00:00`

Status: **source_unavailable**

Failed command: `/opt/hostedtoolcache/Python/3.11.15/x64/bin/python scripts/discover_oddspedia_match_urls_curl.py --fixtures-csv outputs/final_locked_picks/superbru_final_card.csv --impersonate chrome124`
Return code: `1`

Oddspedia validation was unavailable from this runner, so no fresh validation should be trusted. The SuperBru locked card may still be refreshed from live SuperBru state and fresh bookmaker odds; the validation freshness audit must surface Oddspedia as missing/stale.
