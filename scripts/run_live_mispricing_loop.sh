#!/bin/sh
# Continuous DRY-RUN live mispricing scan driven by the Polymarket WebSocket book.
#
# Each cycle captures the live WebSocket order book for WS_SECONDS, normalises it, then scans for
# BUY_YES / SELL_YES / MAKE signals against our fair values (inputs/polymarket/model_probabilities.csv
# by default - mount your own). It NEVER places an order; results land in outputs/.
#
# Tunables come from the environment (see .env.example):
#   PM_CONFIG                 path to the engine config inside the container
#   WS_SECONDS                WebSocket capture window per cycle (default 60)
#   MISPRICING_SLEEP_SECONDS  pause between cycles (default 5)
set -u

CONFIG="${PM_CONFIG:-/app/polymarket_predictive_config.example.yaml}"
WS_SECONDS="${WS_SECONDS:-60}"
SLEEP_SECONDS="${MISPRICING_SLEEP_SECONDS:-5}"

echo "[live-mispricing] config=${CONFIG} ws_seconds=${WS_SECONDS} sleep=${SLEEP_SECONDS} (DRY-RUN, never trades)"

# Loop, not exec: a single failed cycle (e.g. a transient WebSocket drop) must not kill the service.
while true; do
  polymarket-engine run-live-mispricing --config "${CONFIG}" --websocket-seconds "${WS_SECONDS}" \
    || echo "[live-mispricing] cycle failed; retrying after ${SLEEP_SECONDS}s"
  sleep "${SLEEP_SECONDS}"
done
