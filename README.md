# Claude Research Engines

## What this repository is for

This repository is a research engine for one question: can a pre-registered,
fail-closed, paper-only process tell a profitable, executable strategy apart
from a historical premium, an accounting error, an overfit result, or
insufficient evidence? A defensible negative result is a successful outcome.

The Polymarket predictive/paper-trading research engine is the principal system
and the only one the question above is about. A second, ancillary system — the
World Cup/SuperBru score engine and its VPS auto-pick watchdog — shares the
infrastructure but is not part of the economic thesis and should not be read as
the repository's main line of work.

## Generated state

Point-in-time operating state is generated from runtime evidence, not maintained
in README prose. This page carries no current value. Read the generated files on
the VPS at `/home/opc/Claude`:

```text
outputs/performance/operating_state.md
outputs/performance/operating_state.json
```

The dashboard is reached at the URL in `PM_DASHBOARD_PUBLIC_URL`, which is a
Tailscale address; this page prints no host address of its own.
[`docs/OPERATING_STATE.md`](docs/OPERATING_STATE.md) defines the contract those
files satisfy, and [`AGENTS.md`](AGENTS.md) defines how agents operate the
repository.

## State of the evidence

Every reading in this repository carries an evidence class, and the classes do
not mix: historical, modeled, reconstructed, shadow, paper and
live-real-money are separate, and promotion between them requires prospective
out-of-sample proof rather than a better backtest. Nothing has been promoted past paper, and the binding capital
is zero. What each registered lane currently rests on, what it was read from,
and where that reading is recorded are in
[`docs/EVIDENCE_STATE_2026-09-13.md`](docs/EVIDENCE_STATE_2026-09-13.md), which
is a dated record of the last snapshot the VPS produced and not a statement of
current state — for that, read the generated files named above.

## Retracted figures

Until 2026-09-13 this page printed adverse selection at $63.62/day, a net of
−$60.60/day, and a gross of $3.02/day. The charter's correction of 2026-08-23
withdrew the section that asserted them; none of the three appears in any
artifact on the telemetry mirror. The recorded readings and their evidence
classes are in `docs/EVIDENCE_STATE_2026-09-13.md`, linked under "State of the
evidence" above.

## Supported workflows

Production and verification are VPS-only. Do not run Python engines, tests,
Docker, dashboards, scheduled tasks, collectors, model training, brokers, or
watchdogs on the local workstation. Local work is limited to code inspection and
editing, Git/GitHub operations, and SSH control.

Four workflows are supported, and nothing else is:

1. **VPS production.** Deployment runs through Path A (the
   `Deploy Polymarket VPS Paper` workflow) or, where Path A is unavailable,
   Path B, both defined in [`AGENTS.md`](AGENTS.md). Their guard order, refusal
   conditions and attestation rules live there and are not restated here.
2. **The offline `pytest` suite in an ephemeral agent sandbox**, under the
   2026-07-27 amendment in [`AGENTS.md`](AGENTS.md). A sandbox run is not
   verification of record; the required pull-request gate is.
3. **Offline historical research from the two named public sources**, writing
   only under `research/`, under the 2026-09-12 amendment in
   [`AGENTS.md`](AGENTS.md). It permits nothing prospective and contacts no
   venue, wallet or paid API. Committed results are reproduced with
   `python -m premium_research.cli verify-manifest` and
   `python -m premium_research.cli verify-results`, and the refined and
   corrected passes with `verify-results --work-order WO-167` and
   `verify-results --work-order WO-170`. These two selectors landed on `main`
   with #455, and their results are not verification of record until that
   pull request's required gate runs.
4. **Reading the telemetry mirror** `origin/vps-telemetry`, whose JSON files are
   complete while its CSVs hold only the last 200 rows. A per-file truncation
   manifest is WO-172, merged at #455, and not verified until that pull
   request's required gate runs.

## Known limitations

The self-hosted runner has accepted no job since 2026-08-23T12:36Z and the VPS
has been offline since 2026-08-21
([`docs/VPS_OUTAGE_2026-08-21.md`](docs/VPS_OUTAGE_2026-08-21.md)). Three merges
have landed since — #452 on 2026-08-22, #454 on 2026-09-12 and #455 on
2026-09-13 — and neither merge has had its required gate execute: #454's run was
cancelled after a day in the queue and #455's is still queued;
whether #452 was gated before the runner stopped is not established by any
artifact in this repository, which itself records that its evidence cannot
distinguish a host down from 2026-08-21T02:00 from one degraded on 08-21 and
unresponsive by 08-23. The telemetry CSVs are truncated. The legacy verdict
engine's Gate A metric is a per-share price difference labelled per dollar
(WO-169, merged at #455, and not verified until that pull request's required
gate runs). The strategy search selected on its holdout (WO-171, merged at #455,
recorded in the register but not in the charter, and not verified until that
pull request's required gate runs). Maker capacity is bounded by a sizing model rather than measured. The
funding-carry haircut of 2.0 pp is an assumption, not a measurement.

## Governance in one paragraph

One work order per branch and per pull request; every draft passes the S8
admission checklist in
[`docs/ENGINEERING_STANDARDS.md`](docs/ENGINEERING_STANDARDS.md) before it is
registered; the GLOBAL RULE at the top of
[`docs/POLYMARKET_CODEX_WORK_ORDERS.md`](docs/POLYMARKET_CODEX_WORK_ORDERS.md)
requires the `origin/main` tip at dispatch to be an ancestor of the build
branch, with both SHAs recorded on the work order; frozen and registered
surfaces are merged by the repository owner and never by an agent; no
autonomous live-order path exists and none may be added; and every reading
carries its evidence class, with missing or stale evidence failing closed as
`UNKNOWN` rather than passing quietly.

## Documents

Every file under `docs/` belongs to exactly one class, and the classification is
the literal dictionary in `tests/test_repository_hygiene.py`, which asserts it is
an exhaustive partition. The canonical documents are listed in full below; the
remaining classes are given by name and count only, so this page carries no list
that can go stale without a test failing.

| Topic | File |
|---|---|
| What this repository is | [`README.md`](README.md) |
| State of the evidence, 2026-09-13 | [`docs/EVIDENCE_STATE_2026-09-13.md`](docs/EVIDENCE_STATE_2026-09-13.md) |
| Agent operating rules | [`AGENTS.md`](AGENTS.md) |
| Binding engineering standards | [`docs/ENGINEERING_STANDARDS.md`](docs/ENGINEERING_STANDARDS.md) |
| Experiment registry and freeze | [`docs/EXPERIMENT_REGISTRY.md`](docs/EXPERIMENT_REGISTRY.md) |
| Work-order queue and constraints | [`docs/POLYMARKET_CODEX_WORK_ORDERS.md`](docs/POLYMARKET_CODEX_WORK_ORDERS.md) |
| Quant-mode charter | [`docs/POLYMARKET_QUANT_MODE_CHARTER.md`](docs/POLYMARKET_QUANT_MODE_CHARTER.md) |
| Quant trading contract | [`docs/POLYMARKET_QUANT_TRADING_CONTRACT.md`](docs/POLYMARKET_QUANT_TRADING_CONTRACT.md) |
| Generated operating-state contract | [`docs/OPERATING_STATE.md`](docs/OPERATING_STATE.md) |
| VPS setup and deployment | [`docs/ORACLE_VPS_SETUP.md`](docs/ORACLE_VPS_SETUP.md) |
| Docker safety | [`docs/POLYMARKET_DOCKER_SAFETY_AUDIT.md`](docs/POLYMARKET_DOCKER_SAFETY_AUDIT.md) |
| Edge reset and exclusions | [`docs/POLYMARKET_EDGE_STRATEGY_RESET.md`](docs/POLYMARKET_EDGE_STRATEGY_RESET.md) |
| VPS outage record | [`docs/VPS_OUTAGE_2026-08-21.md`](docs/VPS_OUTAGE_2026-08-21.md) |
| Sharp-odds anchor sourcing | [`docs/POLYMARKET_SHARP_ANCHOR.md`](docs/POLYMARKET_SHARP_ANCHOR.md) |
| Architecture and single points of failure | [`docs/SYSTEM_MAP.md`](docs/SYSTEM_MAP.md) |
| Engine commands | [`src/polymarket_predictive_engine/cli.py`](src/polymarket_predictive_engine/cli.py) |
| SuperBru package | [`src/superbru_score_engine`](src/superbru_score_engine) |

The nine classes, counted over members under `docs/`:

- **canonical** — 13 under `docs/`, the rows above less `README.md`, `AGENTS.md`
  and the two source paths.
- **owner-surface** — 5 under `docs/`. Amendments, decisions and checks that sit
  at the owner's end of the governance chain. The class name records where they
  sit, not who typed them.
- **draft template, unsigned, not in force** — 1 under `docs/`. Its own header
  says it authorizes nothing.
- **referenced by code or tests** — 12 under `docs/`. Runbooks and standards a
  module or a test names by path, kept where they are for that reason.
- **retired in place with a loud notice** — 6 under `docs/`. Superseded, but
  pinned by tests that assert the notice is still on them.
- **kept by cross-reference** — 10 under `docs/`. Each has exactly one named
  referrer, and a test asserts the referrer still contains the name.
- **SuperBru ancillary** — 9 under `docs/`. The score engine, not the economic
  thesis.
- **incident records** — 1 under `docs/`.
- **archived** — 15 under `docs/`, in `docs/archive/`, each with a row in
  [`docs/archive/README.md`](docs/archive/README.md) giving the reason it was
  moved and the canonical file that replaces it, or `none`. Every one is
  recoverable from Git history.

Legacy local scripts and runbooks remain for history and regression coverage
only. Their presence is not an instruction to run them, and the VPS-only rule
above governs regardless.
