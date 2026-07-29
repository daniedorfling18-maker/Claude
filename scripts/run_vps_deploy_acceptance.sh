#!/usr/bin/env sh
# Dedicated one-shot deployment acceptance lane.
#
# This script is intentionally not sourced by or executed inside the recurring
# VPS scheduler.  The deploy workflow stops that scheduler first, then starts
# the profiled vps-deploy-acceptance service for exactly one bounded cycle.
# Every command below is reporting/read-only; there is no broker, signer,
# cancellation, paper-order, or live-order command in this lane.

set -u

CONFIG_PATH="${POLYMARKET_CONFIG_PATH:-/app/polymarket_predictive_config.example.yaml}"
OUT_PATH="${DEPLOY_ACCEPTANCE_CYCLE_PATH:-/app/outputs/ops_scheduler/deploy_acceptance_cycle.json}"
# Budget calibration, round 2 (2026-07-29 after the 08187b6 acceptance
# failure). The study's wall time is dominated by ~80 uncached calls to
# clob.polymarket.com/prices-history (two windows x 40 candidates), and that
# UPSTREAM endpoint degraded from ~0.3s to ~5-10s per origin fetch sometime
# after 2026-07-28 13:37Z - measured on 2026-07-29 with an instrumented run
# (p50 5.1s, max 10.3s, all HTTP 200, cf-cache-status EXPIRED, no rate-limit
# headers; the same requests answered in 0.3s the day before). A full study
# completed in 627s under that weather, on BOTH the old and new code. The
# per-command bound therefore holds ~1.4x the observed worst case; a genuine
# hang is still killed, just later, and the deploy paths' outer wrappers
# (1500s) bound the whole stage. The PASS predicate is unchanged: every
# producer must exit 0. If upstream recovers, runs simply finish early -
# these are ceilings, not sleeps.
COMMAND_TIMEOUT="${DEPLOY_ACCEPTANCE_COMMAND_TIMEOUT_SECONDS:-900}"
TOTAL_TIMEOUT="${DEPLOY_ACCEPTANCE_TOTAL_TIMEOUT_SECONDS:-1200}"

case "$COMMAND_TIMEOUT:$TOTAL_TIMEOUT" in
  *[!0-9:]*|:*|*:) printf '%s\n' "deploy acceptance timeouts must be positive integers" >&2; exit 64 ;;
esac
if [ "$COMMAND_TIMEOUT" -lt 1 ] || [ "$TOTAL_TIMEOUT" -lt 1 ]; then
  printf '%s\n' "deploy acceptance timeouts must be positive integers" >&2
  exit 64
fi
acceptance_deadline=$(( $(date -u +%s) + TOTAL_TIMEOUT ))

run_bounded() {
  remaining=$(( acceptance_deadline - $(date -u +%s) ))
  if [ "$remaining" -le 0 ]; then
    return 124
  fi
  limit="$COMMAND_TIMEOUT"
  if [ "$remaining" -lt "$limit" ]; then
    limit="$remaining"
  fi
  timeout --signal=TERM --kill-after=10s "$limit" "$@"
}

case "${POLYMARKET_EXECUTE_LIVE:-false}" in
  1|true|TRUE|yes|YES)
    printf '%s\n' "deploy acceptance refuses POLYMARKET_EXECUTE_LIVE=${POLYMARKET_EXECUTE_LIVE}" >&2
    exit 64
    ;;
esac
case "${POLYMARKET_LIVE_TRADING:-0}" in
  1|true|TRUE|yes|YES)
    printf '%s\n' "deploy acceptance refuses POLYMARKET_LIVE_TRADING=${POLYMARKET_LIVE_TRADING}" >&2
    exit 64
    ;;
esac

# Each producer records its wall seconds next to its exit code, so a failed
# or slow acceptance names where the time went from the artifact alone. The
# 2026-07-29 diagnosis of the prices-history upstream slowdown required an
# instrumented forensic session on the VPS precisely because this file only
# recorded exit codes.
run_producer() {
  producer_name="$1"; shift
  producer_started=$(date -u +%s)
  run_bounded "$@"
  producer_code=$?
  producer_elapsed=$(( $(date -u +%s) - producer_started ))
  eval "${producer_name}_code=\$producer_code"
  eval "${producer_name}_seconds=\$producer_elapsed"
  return 0
}

set +e
run_producer maker_carry_study python -m polymarket_predictive_engine.cli maker-carry-study --config "$CONFIG_PATH"
run_producer collect_maker_replay_data python -m polymarket_predictive_engine.cli collect-maker-replay-data --config "$CONFIG_PATH"
run_producer maker_fill_replay python -m polymarket_predictive_engine.cli maker-fill-replay --config "$CONFIG_PATH"
run_producer maker_live_test python -m polymarket_predictive_engine.cli maker-live-test --config "$CONFIG_PATH"
run_producer decision_policy python -m polymarket_predictive_engine.cli decision-policy --config "$CONFIG_PATH"
run_producer requote_alerts python -m polymarket_predictive_engine.cli requote-alerts --config "$CONFIG_PATH"
run_producer reconcile_wallet python -m polymarket_predictive_engine.cli reconcile-wallet --config "$CONFIG_PATH"
run_producer executor_ops_monitor python -m polymarket_predictive_engine.cli executor-ops-monitor --config "$CONFIG_PATH"
run_producer operating_state python -m polymarket_predictive_engine.cli operating-state --config "$CONFIG_PATH"
set -e

export OUT_PATH maker_carry_study_code collect_maker_replay_data_code
export maker_fill_replay_code maker_live_test_code decision_policy_code
export requote_alerts_code reconcile_wallet_code executor_ops_monitor_code
export operating_state_code
export maker_carry_study_seconds collect_maker_replay_data_seconds
export maker_fill_replay_seconds maker_live_test_seconds decision_policy_seconds
export requote_alerts_seconds reconcile_wallet_seconds executor_ops_monitor_seconds
export operating_state_seconds
python - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

target = Path(os.environ["OUT_PATH"])
target.parent.mkdir(parents=True, exist_ok=True)
commands = {
    name: {
        "exit_code": int(os.environ[f"{name}_code"]),
        "duration_seconds": int(os.environ[f"{name}_seconds"]),
    }
    for name in (
        "maker_carry_study",
        "collect_maker_replay_data",
        "maker_fill_replay",
        "maker_live_test",
        "decision_policy",
        "requote_alerts",
        "reconcile_wallet",
        "executor_ops_monitor",
        "operating_state",
    )
}
payload = {
    "work_order": "WO-79-deployment-controls",
    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    "execution_lane": "dedicated_one_shot_container",
    "scheduler_isolated": True,
    "commands": commands,
    "paper_trading_invoked": False,
    "live_trading_invoked": False,
}
temporary = target.with_name(target.name + ".tmp")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
temporary.replace(target)
PY

run_bounded python -m polymarket_predictive_engine.cli artifact-contracts --config "$CONFIG_PATH"
run_bounded python -m polymarket_predictive_engine.cli deploy-acceptance --config "$CONFIG_PATH"
run_bounded python -m polymarket_predictive_engine.cli operating-state --config "$CONFIG_PATH"

run_bounded python - "$OUT_PATH" <<'PY'
import json
import sys
from pathlib import Path

cycle = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
acceptance_path = Path(sys.argv[1]).with_name("deploy_acceptance.json")
acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
nonzero = {
    name: row.get("exit_code")
    for name, row in cycle.get("commands", {}).items()
    if row.get("exit_code") != 0
}
if nonzero or acceptance.get("status") != "PASS":
    print(
        json.dumps(
            {
                "status": "FAIL",
                "nonzero_producers": nonzero,
                "acceptance_status": acceptance.get("status"),
                "paper_trading_invoked": False,
                "live_trading_invoked": False,
            },
            sort_keys=True,
        ),
        file=sys.stderr,
    )
    raise SystemExit(1)
print(json.dumps({"status": "PASS", "scheduler_isolated": True}, sort_keys=True))
PY
