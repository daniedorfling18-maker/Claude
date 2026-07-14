# Claude Research Engines

This repository contains two related systems:

1. the Polymarket predictive/paper-trading research engine; and
2. the World Cup/SuperBru score engine and VPS auto-pick watchdog.

## Start with generated state

Point-in-time operating state is generated from runtime evidence, not maintained
in README prose. Read the VPS dashboard or these files in `/home/opc/Claude`:

```text
http://129.151.178.42:8765/
outputs/performance/operating_state.md
outputs/performance/operating_state.json
```

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
| Polymarket CLI | `src/polymarket_predictive_engine/cli.py` |
| SuperBru package | `src/superbru_score_engine` |

Legacy local scripts remain for history and regression coverage only. Their
presence is not an active run instruction.
