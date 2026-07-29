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
# Budget calibration (2026-07-29): the 120s per-command bound TERM-killed a
# HEALTHY maker-carry-study during the ca8c3a3 deploy. Measured baselines from
# VPS telemetry (training_harvest.json, 2026-07-28): study 69.9s and
# maker-fill-replay 47.6s on a warm, idle host - and acceptance runs cold,
# immediately after an image build and full container recreation, where CPU
# contention roughly doubles both (the replay also legitimately doubles for
# one release after WO-136's static-sheet audit pass). 300s/720s keep every
# producer bounded at ~4x its warm baseline while the deploy paths' outer
# wrappers (900s in both the workflow and the manual script) still kill a
# genuine hang. The PASS predicate is unchanged: every producer must exit 0.
COMMAND_TIMEOUT="${DEPLOY_ACCEPTANCE_COMMAND_TIMEOUT_SECONDS:-300}"
TOTAL_TIMEOUT="${DEPLOY_ACCEPTANCE_TOTAL_TIMEOUT_SECONDS:-720}"

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

set +e
run_bounded python -m polymarket_predictive_engine.cli maker-carry-study --config "$CONFIG_PATH"
maker_carry_study_code=$?
run_bounded python -m polymarket_predictive_engine.cli collect-maker-replay-data --config "$CONFIG_PATH"
collect_maker_replay_data_code=$?
run_bounded python -m polymarket_predictive_engine.cli maker-fill-replay --config "$CONFIG_PATH"
maker_fill_replay_code=$?
run_bounded python -m polymarket_predictive_engine.cli maker-live-test --config "$CONFIG_PATH"
maker_live_test_code=$?
run_bounded python -m polymarket_predictive_engine.cli decision-policy --config "$CONFIG_PATH"
decision_policy_code=$?
run_bounded python -m polymarket_predictive_engine.cli requote-alerts --config "$CONFIG_PATH"
requote_alerts_code=$?
run_bounded python -m polymarket_predictive_engine.cli reconcile-wallet --config "$CONFIG_PATH"
reconcile_wallet_code=$?
run_bounded python -m polymarket_predictive_engine.cli executor-ops-monitor --config "$CONFIG_PATH"
executor_ops_monitor_code=$?
run_bounded python -m polymarket_predictive_engine.cli operating-state --config "$CONFIG_PATH"
operating_state_code=$?
set -e

export OUT_PATH maker_carry_study_code collect_maker_replay_data_code
export maker_fill_replay_code maker_live_test_code decision_policy_code
export requote_alerts_code reconcile_wallet_code executor_ops_monitor_code
export operating_state_code
python - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

target = Path(os.environ["OUT_PATH"])
target.parent.mkdir(parents=True, exist_ok=True)
commands = {
    name: {"exit_code": int(os.environ[f"{name}_code"])}
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
