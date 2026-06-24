#!/bin/sh
set -eu

OUT_DIR="${POLYMARKET_OUTPUT_DIR:-outputs/polymarket}"
SNAPSHOT="${OUT_DIR}/market_snapshot.csv"
POLL="${PROBABILITY_CONVERTER_POLL_SECONDS:-5}"

printf '%s\n' "probability converter loop: watching ${SNAPSHOT} (poll ${POLL}s)"

last=""
while true; do
  if [ -f "${SNAPSHOT}" ]; then
    cur="$(stat -c %Y "${SNAPSHOT}" 2>/dev/null || date +%s)"
    if [ "${cur}" != "${last}" ]; then
      python scripts/build_repo_worldcup_winner_probabilities.py || echo "probability converter error continuing"
      last="${cur}"
    fi
  fi
  sleep "${POLL}"
done
