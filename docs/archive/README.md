# Archived documents

Moved here by `git mv`; every file is recoverable from Git history.

Nothing in this directory is a statement of current state, and nothing here is a
run instruction. These files are kept because they record how the system was
designed and what was tried, not because any of it is in force. The canonical
documents are listed in the repository `README.md`; where one of them replaces a
file here, the row names it.

`reason` is one of three:

- **dated snapshot** — a note written about a particular day, correct as of that
  day and never updated since.
- **superseded by `<path>`** — a canonical file now covers the same ground; the
  `replacement` cell repeats that path.
- **legacy local design; VPS-only rule** — a design or runbook for running the
  stack on a local workstation, which the VPS-only rule in `AGENTS.md` forbids.

Where no canonical file replaces the archived one, `replacement` is `none`.

| file | reason | replacement |
|---|---|---|
| `POLYMARKET_ACTUARIAL_GRADE_GAP_ASSESSMENT_20260628.md` | dated snapshot | none |
| `POLYMARKET_ENGINE_APPLY_NOTES.md` | legacy local design; VPS-only rule | none |
| `POLYMARKET_LIVE_LEARNING_SYSTEM_DESIGN.md` | legacy local design; VPS-only rule | none |
| `POLYMARKET_MISPRICING_BOT.md` | legacy local design; VPS-only rule | none |
| `POLYMARKET_PAPER_PROFIT_AUDIT.md` | superseded by `docs/POLYMARKET_QUANT_MODE_CHARTER.md` | `docs/POLYMARKET_QUANT_MODE_CHARTER.md` |
| `POLYMARKET_PREDICTIVE_POWER_ROADMAP.md` | superseded by `docs/POLYMARKET_EDGE_STRATEGY_RESET.md` | `docs/POLYMARKET_EDGE_STRATEGY_RESET.md` |
| `POLYMARKET_STRATEGY_V2.md` | superseded by `docs/POLYMARKET_EDGE_STRATEGY_RESET.md` | `docs/POLYMARKET_EDGE_STRATEGY_RESET.md` |
| `POLYMARKET_STRATEGY_V2_QUICKSTART.md` | superseded by `docs/POLYMARKET_EDGE_STRATEGY_RESET.md` | `docs/POLYMARKET_EDGE_STRATEGY_RESET.md` |
| `POLYMARKET_VPS_DOCKER_DRY_RUN.md` | superseded by `docs/POLYMARKET_VPS_DOCKER_RUNBOOK.md` | `docs/POLYMARKET_VPS_DOCKER_RUNBOOK.md` |
| `VPS_DOCKER_DRY_RUN_MONITOR.md` | superseded by `docs/POLYMARKET_VPS_DOCKER_RUNBOOK.md` | `docs/POLYMARKET_VPS_DOCKER_RUNBOOK.md` |
| `VPS_RESTART_FORENSICS_2026-07-12.md` | dated snapshot | none |
| `VENTURE_THESIS.md` | dated snapshot | none |
| `LIVE_DUTCH_ARB_DOCKER.md` | legacy local design; VPS-only rule | none |
| `POLYMARKET_RESOLUTION_COLLECTOR.md` | superseded by `src/polymarket_predictive_engine/resolution_collector.py` | `src/polymarket_predictive_engine/resolution_collector.py` |
| `polymarket_overnight_governance_20260625.md` | dated snapshot | none |
