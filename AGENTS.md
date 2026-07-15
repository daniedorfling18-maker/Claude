# AGENTS.md — how to run and change this repository

This is the canonical instruction file for every coding agent.

## Non-negotiable operating rule: VPS only

All Polymarket and SuperBru runtime work runs on the Oracle VPS. The local
workstation is a control surface for reading/editing code, Git operations, GitHub
review, and SSH administration only.

Do not start any of the following on the local workstation:

- Python engines, collectors, model training, test suites, brokers, or watchdogs;
- Docker or Compose stacks;
- dashboard servers;
- Windows scheduled tasks or any legacy local launcher.

Run verification in an isolated VPS container or through the self-hosted ARM64
required PR gate. Run production only through the guarded VPS deployment.

## Authoritative operating state

Never infer current state from prose or a Git checkout. WO-68 generates the
point-in-time report from runtime evidence:

```text
/home/opc/Claude/outputs/performance/operating_state.md
/home/opc/Claude/outputs/performance/operating_state.json
```

The dashboard serves the same evidence at:

```text
http://129.151.178.42:8765/
```

Missing or stale evidence is `UNKNOWN` and fails closed. The control contract is
documented in `docs/OPERATING_STATE.md`.

## VPS production contract

- Repository: `/home/opc/Claude`
- Compose file: `docker-compose.vps-paper.yml`
- Long-running services: `polymarket-paper-live`, `polymarket-dashboard`,
  `vps-ops-scheduler`, and `superbru-auto-pick-watchdog`
- One production Compose stack only; never start a duplicate writer.
- Secrets live only in the VPS `.env` or GitHub repository secrets. Never print,
  copy into source, commit, or place them in telemetry.
- Deploy from a reviewed, merged `main` with the
  `Deploy Polymarket VPS Paper` workflow. That path preserves runtime ledgers,
  stamps `PM_VPS_DEPLOYED_SHA`, runs post-deploy acceptance, and retains a
  rollback revision. Do not replace it with an ad-hoc pull/rebuild.

Read-only operator checks after SSHing to the VPS:

```bash
cd /home/opc/Claude
docker compose -f docker-compose.vps-paper.yml ps
docker stats --no-stream
```

## Safety and governance

- The system is shadow/dry-run/paper-gated by default. There is no approved
  autonomous live order path.
- WO-67 is architecture registration only and remains blocked until every P1-P5
  precondition passes. Do not add even dormant executor, signer, cancellation,
  credential-loading, or live-order code before that registered authorization.
- Do not loosen alpha, liquidity, validation, cohort, maker, risk, stake, or
  promotion controls to manufacture activity.
- New features must be point-in-time. Labels may only become available at their
  real observation time; validation is chronological and out-of-sample by market.
- Forward evidence classes stay distinct: historical, modeled, reconstructed,
  shadow, paper, and live-real-money evidence may not be relabelled upward.
- Every new artifact is fail-closed and states
  `paper_trading_invoked=false` and `live_trading_invoked=false` unless an
  already-authorized paper path genuinely produced it.
- Never expose private keys, API keys, passwords, passphrases, cookies, or `.env`
  values in chat, logs, artifacts, tests, Git, or dashboard data.

## Research focus

The promotion-oriented research surface is
frozen to exactly the three primary hypotheses
in `docs/EXPERIMENT_REGISTRY.md`:

1. sharp-anchor maker carry;
2. persistent dutch-book consistency opportunities;
3. structural-bias/smart-flow cohorts with positive executable CLV.

Crypto up/down is a timing/infrastructure diagnostic with negative forward
evidence. Do not spend modelling or collection-priority work trying to revive it.
Unregistered lanes are diagnostic/parked and cannot consume promotion-oriented
work or capital.

## Work-order and Git discipline

- Read `docs/POLYMARKET_CODEX_WORK_ORDERS.md` and the relevant registered design
  before changing code.
- Every work order and review complies with `docs/ENGINEERING_STANDARDS.md`
  (clock/unit rules, atomic writes, data-dependency contracts,
  recorded-reality fixtures, fail-safe statements, day-after checks).
- One work order per branch and PR. Do not combine WOs or add drive-by refactors.
- Preserve unrelated/user changes in every checkout.
- Use the required PR gate; do not merge red checks.
- Run target tests and the full suite in an isolated ARM64/Python 3.11 VPS
  checkout when the change warrants it. Record exact results in the PR/WO status.
- Never use destructive Git commands to deal with runtime data.

## Stable references

| Topic | File |
|---|---|
| Generated-state contract | `docs/OPERATING_STATE.md` |
| Work-order queue and constraints | `docs/POLYMARKET_CODEX_WORK_ORDERS.md` |
| Binding engineering standards | `docs/ENGINEERING_STANDARDS.md` |
| Experiment freeze | `docs/EXPERIMENT_REGISTRY.md` |
| Quant contract | `docs/POLYMARKET_QUANT_TRADING_CONTRACT.md` |
| Quant-mode charter | `docs/POLYMARKET_QUANT_MODE_CHARTER.md` |
| Edge reset and exclusions | `docs/POLYMARKET_EDGE_STRATEGY_RESET.md` |
| VPS setup/deployment | `docs/ORACLE_VPS_SETUP.md` |
| Docker safety | `docs/POLYMARKET_DOCKER_SAFETY_AUDIT.md` |
| Engine commands | `src/polymarket_predictive_engine/cli.py` |

Legacy local launchers and runbooks remain only for repository history and
regression coverage. Their presence is not permission to run them locally.
