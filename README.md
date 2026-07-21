# Claude Research Engines

This repository contains two related systems:

1. the Polymarket predictive/paper-trading research engine; and
2. the World Cup/SuperBru score engine and VPS auto-pick watchdog.

## Start with generated state

Point-in-time operating state is generated from runtime evidence, not maintained
in README prose. Read the VPS dashboard or these files in `/home/opc/Claude`:

```text
PM_DASHBOARD_PUBLIC_URL from /home/opc/Claude/.env
outputs/performance/operating_state.md
outputs/performance/operating_state.json
```

The dashboard URL must be the private Tailscale Serve address
`https://<node>.<tailnet>.ts.net/`. Port `8765` is bound to VPS loopback only;
there is no supported public-IP dashboard route and Tailscale Funnel must remain
disabled.

[`docs/OPERATING_STATE.md`](docs/OPERATING_STATE.md) defines the control, and
[`AGENTS.md`](AGENTS.md) defines how agents operate the repository.

## Runtime model

Production and verification are VPS-only. Do not run Python engines, tests,
Docker, dashboards, scheduled tasks, collectors, model training, brokers, or
watchdogs on the local workstation. Local work is limited to code inspection and
editing, Git/GitHub operations, and SSH control.

The production stack is deployed from reviewed `main` through
`Deploy Polymarket VPS Paper` using `docker-compose.vps-paper.yml`. The guarded
workflow preserves ledgers, records the deployed SHA, verifies current data
contracts, and retains rollback state.

The long-running stack contains four services:

1. `polymarket-paper-live` — collection and paper/evidence loop;
2. `polymarket-dashboard` — loopback-only reporting backend;
3. `vps-ops-scheduler` — governance, proof, collection and maintenance jobs; and
4. `superbru-auto-pick-watchdog` — the VPS SuperBru submission watchdog.

## Quant/research contract

The system seeks executable mispricing—not mere probability movement. Entry and
exit evidence uses actual bid/ask prices, spread, depth, fees, adverse selection,
and cost attribution. Historical/model/reconstructed/shadow/paper/live evidence
classes remain separate, and promotion requires prospective out-of-sample proof.

Promotion-oriented research is frozen to the three hypotheses registered in
[`docs/EXPERIMENT_REGISTRY.md`](docs/EXPERIMENT_REGISTRY.md):

1. sharp-anchor maker carry;
2. persistent dutch-book consistency opportunities;
3. structural-bias/smart-flow cohorts with positive executable CLV.

Crypto up/down and unregistered lanes are diagnostic only. No gate may be
loosened to force trades or a `$100/month` headline.

## Safety status

The engine is shadow/dry-run/paper-gated. WO-67 is a blocked architecture
registration; there is no approved autonomous live-order path. Missing or stale
evidence fails closed as `UNKNOWN`.

## Work and verification

- Read [`docs/POLYMARKET_CODEX_WORK_ORDERS.md`](docs/POLYMARKET_CODEX_WORK_ORDERS.md).
- Use one work order per branch/PR.
- Run target/full tests in an isolated ARM64/Python 3.11 VPS checkout.
- Require the WO-69 PR gate to pass before merge.
- Deploy merged `main` with the guarded VPS workflow and inspect post-deploy
  acceptance plus the generated operating state.

## Key references

| Topic | File |
|---|---|
| Agent operating rules | `AGENTS.md` |
| Generated operating-state contract | `docs/OPERATING_STATE.md` |
| Work orders | `docs/POLYMARKET_CODEX_WORK_ORDERS.md` |
| Experiment registry | `docs/EXPERIMENT_REGISTRY.md` |
| Quant-mode charter | `docs/POLYMARKET_QUANT_MODE_CHARTER.md` |
| Edge reset | `docs/POLYMARKET_EDGE_STRATEGY_RESET.md` |
| VPS setup | `docs/ORACLE_VPS_SETUP.md` |
| Docker safety | `docs/POLYMARKET_DOCKER_SAFETY_AUDIT.md` |
| 2026-07-21 whole-repository audit | `docs/REPOSITORY_LINE_AUDIT_2026-07-21.md` |
| Polymarket CLI | `src/polymarket_predictive_engine/cli.py` |
| SuperBru package | `src/superbru_score_engine` |

Legacy local scripts remain for history and regression coverage only. Their
presence is not an active run instruction.

## Documentation status

The documentation set was reconciled against merged history through PR #354 on
2026-07-21. That date describes source documentation only; it is not evidence
that the VPS is deployed at the same revision or currently healthy. Use the
generated operating state and deployed-SHA evidence for those questions. The
dated line audit records unresolved code/deployment findings; documentation
reconciliation does not imply that those implementation gaps are fixed.
