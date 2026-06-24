#!/bin/sh
set -eu

OUT_DIR="${POLYMARKET_OUTPUT_DIR:-outputs/polymarket}"
SNAPSHOT="${OUT_DIR}/market_snapshot.csv"
POLL="${LONG_SHORT_POLL_SECONDS:-3}"
MAX_ORDERS="${LONG_SHORT_MAX_LIVE_ORDERS:-3}"
MIN_EDGE="${LONG_SHORT_MIN_EDGE:-0.04}"

MODEL_PROBS_OUT="${MODEL_PROBS_OUT:-${OUT_DIR}/model_probabilities.csv}"
printf '%s\n' "long/short engine loop: watching ${SNAPSHOT} (poll ${POLL}s, min_edge=${MIN_EDGE})"

last=""
while true; do
  if [ -f "${SNAPSHOT}" ]; then
    cur="$(stat -c %Y "${SNAPSHOT}" 2>/dev/null || date +%s)"
    if [ "${cur}" != "${last}" ]; then
      # Refresh live model fairs from the prediction log + learned calibration so the bot's
      # next scan fills fair_probability and the directional engine has something to trade against.
      python scripts/build_polymarket_model_probabilities.py \
        --market-snapshot "${SNAPSHOT}" \
        --out "${MODEL_PROBS_OUT}" \
        || echo "model-probabilities converter error (continuing)"
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
