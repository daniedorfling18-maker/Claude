#!/bin/sh
# Periodic DRY-RUN refresh of the sharp-odds anchor: fetch sharp odds (Pinnacle/Betfair via The
# Odds API) -> de-vig -> write the mispricing-alpha fundamental. Pure data transform + a read-only
# odds GET; it never places an order.
#
# The Odds API charges usage credits per request, so the default interval is hourly - pre-match
# odds barely move intraday. Without THE_ODDS_API_KEY the fetch reports missing_api_key and the
# loop idles harmlessly (the rest of the stack keeps running).
#
# Env:
#   PM_CONFIG               engine config inside the container
#   SHARP_REFRESH_SECONDS   seconds between refreshes (default 3600)
#   THE_ODDS_API_KEY        odds API key (passed through from .env; never committed)
set -u

CONFIG="${PM_CONFIG:-/app/polymarket_predictive_config.example.yaml}"
SLEEP="${SHARP_REFRESH_SECONDS:-3600}"

echo "[sharp-anchor] config=${CONFIG} refresh=${SLEEP}s (DRY-RUN; needs THE_ODDS_API_KEY to fetch)"

while true; do
  polymarket-engine refresh-sharp-anchor --config "${CONFIG}" \
    || echo "[sharp-anchor] cycle failed; retrying after ${SLEEP}s"
  sleep "${SLEEP}"
done
