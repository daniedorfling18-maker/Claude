#!/bin/sh
# Continuous DRY-RUN dutch-book arbitrage execution monitor.
#
# Polls the live Gamma/CLOB order books for complete multi-outcome (negRisk) baskets, ranks any
# locks by annualised return on capital, diffs state across polls (appeared/persisting/cleared),
# and writes outputs/polymarket_arbitrage/* every poll. It NEVER places an order - every plan is
# dry-run and the summary always reports live_trading: false.
#
# Tunables come from the environment (see .env.example):
#   PM_CONFIG             path to the engine config inside the container
#   ARB_POLL_SECONDS      seconds to sleep between polls (default 30)
#   ARB_MAX_EVENTS        max events to price per poll (default 20; lower = faster cycles)
#   ARB_MIN_ANNUALISED    minimum annualised return to keep an opportunity (default 0.0)
#   ARB_ALERT_ANNUALISED  alert when an arb clears this annualised return (default 0.10)
set -eu

CONFIG="${PM_CONFIG:-/app/polymarket_predictive_config.example.yaml}"
POLL_SECONDS="${ARB_POLL_SECONDS:-30}"
MAX_EVENTS="${ARB_MAX_EVENTS:-20}"
MIN_ANNUALISED="${ARB_MIN_ANNUALISED:-0.0}"
ALERT_ANNUALISED="${ARB_ALERT_ANNUALISED:-0.10}"

echo "[dutch-arb-monitor] config=${CONFIG} poll_seconds=${POLL_SECONDS} max_events=${MAX_EVENTS} alert>=${ALERT_ANNUALISED} (DRY-RUN, never trades)"

# --polls 0 runs continuously in-process so cross-poll state diffing is preserved.
# exec so the container's stop signal reaches the Python process directly.
exec polymarket-engine dutch-arb-monitor \
  --config "${CONFIG}" \
  --polls 0 \
  --poll-seconds "${POLL_SECONDS}" \
  --max-events "${MAX_EVENTS}" \
  --min-annualised "${MIN_ANNUALISED}" \
  --alert-annualised "${ALERT_ANNUALISED}"
