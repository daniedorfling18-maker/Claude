# Operating state control

Point-in-time operating status is generated from effective configuration and machine-readable evidence.
This tracked document intentionally contains no current status values.

Canonical outputs:

- `outputs/performance/operating_state.md` — operator-readable report.
- `outputs/performance/operating_state.json` — dashboard and automation source.
- Dashboard section **Canonical operating state** — a rendering of the same JSON.
- `outputs/performance/dashboard_private_transport.json` — fail-closed proof
  that the dashboard has a loopback-only Docker binding and an authenticated,
  tailnet-only Tailscale Serve HTTPS route with Funnel disabled.

Generate or refresh them with:

```bash
python -m polymarket_predictive_engine.cli operating-state --config polymarket_predictive_config.example.yaml
```

The daily VPS harvest and post-governance scheduler path run the same command. The generator reads
effective paper/live configuration, the configured public wallet marker, governance authorization,
paper activity, taker and maker verdicts, WO-67 precondition evidence, and the host telemetry
deployment manifest. It also reports registered reporting-only SLO measurements and separates
`origin/main`, the host checkout, and the last successfully deployed SHA with divergence age. Missing
artifacts render as `UNKNOWN`; prose is never used to fill a gap. The command is reporting-only and
cannot invoke paper or live trading.

WO-75 adds seven future-executor rows for mode, open orders, exposure versus
stage cap, last-action age, independent dead-man state, freshness SLO, and kill
criteria. They are sourced from `outputs/execution/executor_status.json` and
render `ABSENT` until `outputs/execution/execution_ledger.csv` contains a row.
The scheduler-owned monitor is documented in
`docs/EXECUTOR_LIVE_OPS_CONTROL_PLANE.md`; it does not write the future
executor heartbeat or implement the blocked STOP-binding hook.

Signed custody Amendment A1 supersedes WO-73's isolated executor sub-account.
`operator_wallet_monitoring` and `executor_wallet_monitoring` now describe
non-overlapping human/executor mode-time windows on the same single project
wallet; `maker_live_test.executor_wallet_address` remains empty. The legacy
primary-wallet row remains for compatibility and explicitly names the A1
single-account structure. The A1 sweep row is reporting-only and can only tell
the human to use the registered exit-rail runbook.

WO-81 reads P3 from exact dated markers inside the `AGENTS.md` Owner amendments
section and P5 from exact dated APPROVED lines in
`docs/KEY_CUSTODY_DESIGN_WO67_P5.md`. Missing files remain `UNKNOWN`; an
unsigned or absent marker is `not_met`. P4 names the merge-gate audit command
when its generated artifact is absent. The required governance documents are
mounted read-only in the VPS services and checked during deploy acceptance.

Scheduler reporting separates intentional quota/preflight skips from
capacity/timing overruns. Only consecutive overruns feed the target-zero SLO;
intentional skips remain visible informational telemetry.

Deployment capacity is checked against the target revision before the mounted checkout changes. A
failed `scripts/preflight_vps_capacity.py` run leaves the existing stack and deployed marker intact.

README.md and AGENTS.md may point here or to the generated files, but must not restate dynamic values.
The drift test enforces that rule.

## Document hierarchy (2026-07-12 external audit §6)

Exactly five documents are authoritative, in this order of precedence for
their own domain; every other file under `docs/` is a design note, work
order, implementation record, runbook, or generated artifact and must not
be treated as a statement of current state:

1. `README.md` — what the repository is (points here for state).
2. `AGENTS.md` — how code-changing agents must behave (invariants live here).
3. `docs/OPERATING_STATE.md` — this control document; current values live
   only in the generated `outputs/performance/operating_state.md`/`.json`.
4. `docs/EXPERIMENT_REGISTRY.md` — which hypotheses exist, their
   registered gates, stopping rules, and status. No lane may be promoted
   without a registered entry.
5. `docs/SYSTEM_MAP.md` — architecture, loops, and single points of failure.

Capital policy is defined by the generated IPS
(`outputs/performance/investment_policy_statement.md`), which derives from
the registered WO-50 decision policy and is reporting-only.
