#!/bin/sh
set -eu

OUT_DIR="${POLYMARKET_OUTPUT_DIR:-outputs/polymarket}"
SNAPSHOT="${OUT_DIR}/market_snapshot.csv"
POLL="${LONG_SHORT_POLL_SECONDS:-3}"
MAX_ORDERS="${LONG_SHORT_MAX_LIVE_ORDERS:-3}"
MIN_EDGE="${LONG_SHORT_MIN_EDGE:-0.04}"

printf '%s\n' "long/short engine loop: watching ${SNAPSHOT} (poll ${POLL}s, min_edge=${MIN_EDGE})"

last=""
while true; do
  if [ -f "${SNAPSHOT}" ]; then
    cur="$(stat -c %Y "${SNAPSHOT}" 2>/dev/null || date +%s)"
    if [ "${cur}" != "${last}" ]; then
      python scripts/polymarket_long_short_engine.py \
        --market-snapshot "${SNAPSHOT}" \
        --out-csv "${OUT_DIR}/long_short_intents.csv" \
        --min-edge "${MIN_EDGE}" \
        --max-live-orders "${MAX_ORDERS}" \
        || echo "long/short engine error continuing"
      last="${cur}"
    fi
  fi
  sleep "${POLL}"
done
