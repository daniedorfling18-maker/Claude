# Executor live-ops control plane

WO-75 items 1, 3, and 4 provide the read-only operating surface around the
future executor. They do not implement an executor, load credentials, or
place, amend, cancel, or sign orders. WO-75 item 2 (binding STOP propagation)
remains post-amendment and is explicitly absent.

## Runtime ownership

Two independent processes have different responsibilities:

1. The future executor will be the sole writer of
   `outputs/execution/execution_ledger.csv` and
   `outputs/execution/executor_heartbeat.json`.
2. The existing VPS ops scheduler runs `executor-ops-monitor` every five
   minutes. It only reads those files and writes status/notification evidence.

No execution ledger means the executor is `ABSENT`, not healthy and not
failed. Once a ledger row exists, a missing heartbeat fails closed as both a
freshness breach and a dead-man trigger.

## Producer contract for the future executor

Every action appends exactly one execution-ledger row with at least:

- `recorded_at_utc`
- `mode` (`replay`, `canary`, or `portfolio`)
- `action_type`
- `open_orders_after`
- `exposure_usd_after`

Every executor cycle atomically replaces the heartbeat JSON with at least:

- `heartbeat_at_utc`
- `mode`
- `cycle_id`
- `executor_build_id`

The heartbeat freshness SLO is 600 seconds. The independent dead-man alarm is
1,800 seconds. Effective configuration may tighten those maxima but cannot
widen them. The monitor never writes either producer artifact.

## Monitor outputs

- `outputs/execution/executor_status.json` — mode, open orders, exposure versus
  the tightest observed stage cap, action age, freshness, dead-man countdown,
  kill scoreboard, executor-wallet reconciliation, and active owner alerts.
- `outputs/execution/executor_ops_notification.md` — body for the existing
  owner email-wrapper contract.
- `outputs/execution/executor_ops_notification_state.json` — alert digest used
  to suppress duplicate messages while an incident remains unchanged.

The same status appears in generated operating-state rows and the dashboard's
**Executor live-ops control plane** panel. Alerts cover a registered kill
criterion, heartbeat dead-man trigger, executor-wallet discrepancy above $1,
freshness beyond 600 seconds, and (as an additional tightening control)
exposure above the stage cap or an unregistered runtime mode.

## Deliberately blocked boundary

`requote_alerts` and decision-policy STOP states remain advisory to the human
lane. They are not binding executor inputs in this implementation. Wiring that
cancel-all behavior is WO-75 item 2 and may only occur with the owner amendment
that also unblocks WO-67. The monitor records
`stop_propagation_binding_implemented=false` so this boundary is machine
auditable.

