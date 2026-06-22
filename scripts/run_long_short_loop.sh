#!/bin/sh
# Run the long/short engine right after each new bot snapshot.
#
# Watches the bot's market_snapshot.csv and, whenever it changes, runs the long/short
# engine against the fresh book. Pairs with docker-compose.monitor.yml so the engine is
# a post-scan step beside polymarket_mispricing_bot.py. Dry-run unless PM_MODE=live and
# POLYMARKET_EXECUTE_LIVE=true are set (the engine enforces that itself).
set -eu

OUT_DIR="${POLYMARKET_OUTPUT_DIR:-outputs/polymarket}"
SNAPSHOT="${OUT_DIR}/market_snapshot.csv"
POLL="${LONG_SHORT_POLL_SECONDS:-3}"

MODEL_PROBS_OUT="${MODEL_PROBS_OUT:-${OUT_DIR}/model_probabilities.csv}"
echo "long/short engine loop: watching ${SNAPSHOT} (poll ${POLL}s, max_live_orders=${LONG_SHORT_MAX_LIVE_ORDERS:-3})"

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
        --max-live-orders "${LONG_SHORT_MAX_LIVE_ORDERS:-3}" \
        || echo "long/short engine error (continuing)"
      last="${cur}"
    fi
  fi
  sleep "${POLL}"
done
