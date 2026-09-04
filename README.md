# Claude Research Engines

This repository exists to answer one economic question under pre-registered,
fail-closed rules: can a paper-only Polymarket engine produce verified forward
evidence of a sustainable edge — concretely, the registered `$100/month`
(= `$3.33/day`) maker-carry target — before any real capital is considered?
The Polymarket predictive/paper-trading research engine is the repository's
principal system. A second, ancillary system — the World Cup/SuperBru score
engine and its VPS auto-pick watchdog — shares the infrastructure but is not
part of the economic thesis and should not be read as the repository's main
line of work.

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

## Primary economic thesis — sharp-anchor maker carry (H1) — ANSWERED, 2026-08-19

> **Read this first.** The registered evidence clock for the tested edge
> classes expired on **2026-08-19T23:59:00Z** and the verdict resolved
> **terminally**: `no_for_tested_edge_classes`. H1 is not an open
> investigation. It was measured at **$3.02/day gross** across the entire
> eligible universe against the **$3.33/day** target below, with adverse
> selection at **$63.62/day** — 21x the gross, for a net of **-$60.60/day**.
> H2 flagged **0** opportunities in 300 events with a maximum executable
> basket of **$0.00**. H3 is the one tested class still unmeasured.
>
> The full record, its provenance, and what remains the repo owner's decision
> are in
> [`docs/POLYMARKET_QUANT_MODE_CHARTER.md`](docs/POLYMARKET_QUANT_MODE_CHARTER.md#terminal-verdict--the-registered-evidence-clock-expired-2026-08-19).
> Those figures were read from VPS telemetry and are not re-derivable from
> this repository; the terminal read should be confirmed on the VPS. The
> single registered extension is spent and no agent may extend the window.
>
> The rest of this section describes what was built and tested, and is kept
> because the mechanism and its risks are still an accurate account of the
> experiment. It is **not** a statement that the lane is live.

Three research hypotheses are registered in
[`docs/EXPERIMENT_REGISTRY.md`](docs/EXPERIMENT_REGISTRY.md); the registry
permits exactly these three and no fourth. H1, sharp-anchor maker carry, was
the priority lane: it was the only one with a pre-registered profit campaign
(the frozen M-A/M-B/M-C gates in `maker_carry_study.py`), a registered
validation ladder, and a registered — and blocked — path toward any future
funding decision. That path stayed blocked and the campaign returned a NO.
H2 (persistent dutch-book consistency) and H3 (structural-bias/smart-flow
cohorts with positive executable CLV) remain registered but were always
secondary research lanes: their own registrations cap a pass at a shadow
research candidate and cannot invoke paper or live trading.

**Mechanism.** Passive maker quotes rest on rewarded Polymarket markets
around independent sharp external anchors (bookmaker-derived probabilities).
Resting liquidity earns a published reward-pot share and may capture spread;
the sharp anchor identifies quotes whose apparent carry is least likely to be
erased by adverse selection. Edge is realised reward plus spread minus
markout, fees, gas, and all investor costs — never the reward headline alone.

**What the target means.** The `$100/month` (= `$3.33/day`) figure was a
pre-registered target, never demonstrated performance — and per the terminal
verdict above it was not reached.
M-A requires trusted net carry at or above that target on the registered
number of distinct UTC days; the study's computed net carry is, by its own
registration, a simulation upper bound until the three-tier validation
ladder (fill replay, reward receipt, real-fill markout) confirms it. Current
gate progress lives only in the generated operating state and study
artifacts above — never in this file.

**Unresolved risks and measurements.** Fill quality (confirmed-fill ratio),
realised markout and adverse selection (the human real-fill stage is the only
true test of that half), reward eligibility of the quotes actually posted
(time-integrated epoch share versus snapshot extrapolation), inventory
exposure, and execution costs (fees, gas, requoting) are all still being
measured. Until they are measured on forward evidence, every carry number in
any artifact is a hypothesis, not a result.

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

Promotion-oriented research is frozen to the five hypotheses registered in
[`docs/EXPERIMENT_REGISTRY.md`](docs/EXPERIMENT_REGISTRY.md):

1. sharp-anchor maker carry;
2. persistent dutch-book consistency opportunities;
3. structural-bias/smart-flow cohorts with positive executable CLV.

Crypto up/down and unregistered lanes are diagnostic only. No gate may be
loosened to force trades or a `$100/month` headline.

## Safety status

The engine is shadow/dry-run/paper-gated and funding is closed: the registered
decision policy's binding capital is exactly zero until its pre-registered
preconditions pass on forward evidence. WO-67 is a blocked architecture
registration; there is no approved autonomous live-order path, and live
trading remains gated four independent ways plus owner authorization. Missing
or stale evidence fails closed as `UNKNOWN`.

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
