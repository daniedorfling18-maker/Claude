# AGENTS.md — how to run and work in this repo

Canonical instructions for Codex and any coding agent. **Read this before suggesting how to run
anything.** The short version: this repo is **local-first**; Docker is for deployment only.

## Run model — local-first for development

Run plain Python for development, validation, and watching the bots. **Do not spin up Docker for
local work.** The paper bot is Docker-free by design.

```bash
pip install -e ".[dev]"                                  # one-time setup

# The Polymarket paper bot (the main thing to run locally on Windows):
powershell -ExecutionPolicy Bypass -File scripts/start_polymarket_local_live.ps1

# One-click Windows helper from repo root:
start_polymarket_bot.cmd

# Optional: install reboot/logon auto-start task:
powershell -ExecutionPolicy Bypass -File scripts/install_polymarket_local_live_task.ps1 -RunNow

# Raw equivalent if you are not on Windows:
python scripts/run_polymarket_local_live_loop.py \
    --config polymarket_predictive_config.example.yaml --max-assets 20

# One-off engine commands:
polymarket-engine <command> --config polymarket_predictive_config.example.yaml
#   e.g. paper-cycle, build-crypto-fundamental, refresh-sharp-anchor,
#        train-mispricing-alpha, validate, dutch-arb-monitor

pytest                                                   # tests (or: python -m pytest -q)
```

Keep `--max-assets` small (20–30) to bound the websocket/feature set. One Python process is a few
hundred MB; it still honours the kill switch, readiness, and P&L-pause controls.

The dashboard is at `http://127.0.0.1:8765/`. The agent-lane status page is at
`http://127.0.0.1:8765/agent_status.html` after the first broker/paper-cycle tick.

## When to use Docker — and when NOT to

**Do NOT use Docker for local development.** It is only worth it for:

1. **Unattended 24/7 on a remote VPS** (`restart: unless-stopped` survives crashes/reboots).
2. **Reproducible deploy** to a fresh host that lacks the right Python/deps.
3. **Live trading from a non-geoblocked cloud region** (the US is geoblocked).

None of those is local dev. If you *are* deploying with Docker:

- Run **one** compose stack at a time (duplicate-writer rule — see `docs/POLYMARKET_DOCKER_SAFETY_AUDIT.md`).
- **Never** run `docker-compose.polymarket-wide-raw.yml` whole (20 services ≈ 10 GB).
- Every service caps at `mem_limit: ${PM_MEM_LIMIT:-512m}`; budget = (containers) × cap.
- Full guidance: **`docs/RUNNING_LEAN.md`**.

## Memory discipline (the machine is RAM-constrained)

- Prefer the local process over containers; don't run multiple stacks.
- `runtime_resource_guard` in the config backs the bot off at high memory — lower
  `max_memory_percent` (e.g. 85) if you keep hitting the ceiling. It throttles the bot only; it
  cannot shrink Docker or Codex.
- Keep one Codex session; stop stray background agents.

## Safety / governance — do not weaken these

- Everything is **paper / dry-run by default**. The engine has **no live order path**
  (`execution/live.py` is a skeleton that raises).
- Live trading is gated four independent ways and must stay so: kill switch off
  (`POLYMARKET_KILL_SWITCH` ≠ 1), `trading.mode: live`, `POLYMARKET_LIVE_TRADING=1`, and a human
  approval file. The bot's `LiveExecutor` additionally needs `PM_MODE=live` +
  `POLYMARKET_EXECUTE_LIVE=true` + a non-geoblocked IP + key + SDK. Keep dry-run.
- No label leakage (point-in-time features only); validation is **OOS by market** with bootstrap
  CIs; trade/rule promotion requires **forward shadow evidence**, not in-sample backtest ROI.
- Do not paste `scripts/start_polymarket_local_live.ps1` into a shell. Always run it with `-File`;
  the script now refuses pasted execution because `$PSScriptRoot` is required for safe paths.

## Where things are

| Topic | File |
|---|---|
| Running lean / memory | `docs/RUNNING_LEAN.md` |
| Alpha approach + audit | `docs/ACTUARIAL_AUDIT_PREDICTIVE_VALUE.md` |
| Independent signals (sharp odds, Deribit) | `docs/POLYMARKET_SHARP_ANCHOR.md` |
| Governance + live approval | `docs/POLYMARKET_ACTUARIAL_MODEL_GOVERNANCE.md`, `docs/POLYMARKET_LIVE_TRADING_APPROVAL_CHECKLIST.md` |
| Docker (deploy only) | `docs/LIVE_DUTCH_ARB_DOCKER.md`, `docs/ORACLE_VPS_SETUP.md` |
| Engine commands | `src/polymarket_predictive_engine/cli.py` (`COMMANDS`) |
| Local dashboard launcher | `scripts/start_polymarket_local_live.ps1`, `start_polymarket_bot.cmd` |
| Agent lane / why-no-trade trace | `src/polymarket_predictive_engine/decision_trace.py` |

The repo has two parts: the **SuperBru score engine** (`src/superbru_score_engine`, see `README.md`)
and the **Polymarket predictive engine** (`src/polymarket_predictive_engine`, current focus). Both
run locally; neither requires Docker for development.

## Before pushing

- Run `pytest` (CI also validates workflow-trigger rules, runs the suite, and online smoke tests).
- Keep changes leakage-safe and dry-run; do not add a live order path or relax the gates above.
