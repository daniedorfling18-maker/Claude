# Operating state control

Point-in-time operating status is generated from effective configuration and machine-readable evidence.
This tracked document intentionally contains no current status values.

Canonical outputs:

- `outputs/performance/operating_state.md` — operator-readable report.
- `outputs/performance/operating_state.json` — dashboard and automation source.
- Dashboard section **Canonical operating state** — a rendering of the same JSON.

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
