# AGENTS.md — how to run and work in this repo

Canonical instructions for Codex and any coding agent. **Read this before suggesting how to run
anything.** The short version: this repo is **local-first**; Docker is for deployment only.

## Current focus and operating state

The current active project is the **Polymarket predictive engine** in automated **shadow-research
mode**. Do not treat the bot as ready for paper trading or live trading. The current audit state is:

```text
paper_allowed = false
paper_trading_invoked = false
live_trading_invoked = false
```

The reason is not a broken broker. The infrastructure now works. The blocker is insufficient positive
forward evidence: `sports_other` has accepted shadow candidates, but the cohort is still negative and
has no closed/settled positions. Start with:

- `docs/POLYMARKET_RESEARCH_README.md`
- `docs/POLYMARKET_CURRENT_STATE.md`
- `docs/POLYMARKET_SHADOW_RESEARCH_RUNBOOK.md`

## Run model — local-first for development

Run plain Python for development, validation, and watching the research cycle. **Do not spin up Docker
for local work.** Docker is only for deployment scenarios.

```bash
pip install -e ".[dev]"                                  # one-time setup
pytest                                                   # tests (or: python -m pytest -q)
```

### Current Polymarket workflow: shadow research cycle

Use this scheduled-task workflow instead of starting the old local live loop:

```powershell
# Install or refresh the Windows scheduled task.
.\scripts\install_polymarket_shadow_research_task.ps1 `
  -IntervalMinutes 15 `
  -WebsocketSeconds 30

# Check latest status.
Get-Content .\work\shadow_research_cycle_latest_status.json -Raw

# Check detailed audit.
Get-Content .\outputs\polymarket_model_governance\local_history_audit_report.md -Raw
```

Manual one-cycle run for diagnostics only:

```powershell
.\scripts\run_polymarket_shadow_research_cycle.ps1 `
  -ConfigPath polymarket_predictive_config.example.yaml `
  -WebsocketSeconds 30
```

This cycle performs broad liquidity discovery, websocket collection, normalisation, feature build,
prediction, mispricing-alpha scoring, dry/governance signal generation, alpha-candidate shadow evidence,
and the local-history audit. It explicitly writes `paper_trading_invoked=false` and
`live_trading_invoked=false`.

## Do not use the old Polymarket live-loop entry points as the default

These entry points exist, but they are **not** the current safe default:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_polymarket_local_live.ps1
start_polymarket_bot.cmd
python scripts/run_polymarket_local_live_loop.py --config polymarket_predictive_config.example.yaml --max-assets 20
```

Only use them after a human review confirms that the audit permits paper trading and the relevant family
has positive closed/settled shadow evidence. Do not use them to bypass the shadow-research gate.

The dashboard is at `http://127.0.0.1:8765/`. For visibility only, prefer the lightweight dashboard
server task instead of the old local live loop:

```powershell
.\scripts\install_polymarket_dashboard_task.ps1 -StartNow
```

The dashboard runner serves existing dashboard artifacts only. It does not invoke paper trading, live
trading, Docker, or the old local live loop, and it refuses to start when memory is at or above its
guardrail. The current recommended research workflow remains the scheduled shadow/Strategy V2 cycle
and file-based audit status.

For lightweight local paper-broker/dashboard maintenance without heavy discovery/model scanning, use:

```powershell
.\scripts\install_polymarket_paper_maintenance_task.ps1 -IntervalMinutes 1
```

This task only runs the paper broker when fresh paper signals are pending or an open paper-confirmation
probe has reached its fixed exit horizon; otherwise it only refreshes the static dashboard artifact.
It is still paper-only and guarded by local memory pressure.

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

## Memory discipline

- Prefer the local process over containers; don't run multiple stacks.
- `runtime_resource_guard` in the config backs the bot off at high memory — lower
  `max_memory_percent` (e.g. 85) if you keep hitting the ceiling. It throttles the bot only; it
  cannot shrink Docker or Codex.
- Keep one Codex session; stop stray background agents.

## Safety / governance — do not weaken these

- Everything is **shadow / dry-run / paper-gated by default**. The engine has **no approved live order path**.
- Live trading remains gated four independent ways and must stay so: kill switch off
  (`POLYMARKET_KILL_SWITCH` ≠ 1), `trading.mode: live`, `POLYMARKET_LIVE_TRADING=1`, and a human
  approval file. The bot's `LiveExecutor` additionally needs `PM_MODE=live` +
  `POLYMARKET_EXECUTE_LIVE=true` + a non-geoblocked IP + key + SDK. Keep dry-run.
- No label leakage: point-in-time features only; validation is out-of-sample by market with bootstrap
  CIs; trade/rule promotion requires **forward shadow evidence**, not in-sample backtest ROI.
- Do not paste PowerShell launcher scripts into a shell. Run them with `-File` or by script path.
- Do not loosen alpha thresholds, same-category gates, cohort-promotion gates, or family exclusions to
  force activity.
- Do not treat `unknown` or bad 5-minute crypto families as actionable just because they are liquid.

## Where things are

| Topic | File |
|---|---|
| Dedicated Polymarket README | `docs/POLYMARKET_RESEARCH_README.md` |
| Current Polymarket state | `docs/POLYMARKET_CURRENT_STATE.md` |
| Quant trading contract | `docs/POLYMARKET_QUANT_TRADING_CONTRACT.md` |
| Shadow research runbook | `docs/POLYMARKET_SHADOW_RESEARCH_RUNBOOK.md` |
| Running lean / memory | `docs/RUNNING_LEAN.md` |
| Alpha approach + audit | `docs/ACTUARIAL_AUDIT_PREDICTIVE_VALUE.md` |
| Independent signals (sharp odds, Deribit) | `docs/POLYMARKET_SHARP_ANCHOR.md` |
| Governance + live approval | `docs/POLYMARKET_ACTUARIAL_MODEL_GOVERNANCE.md`, `docs/POLYMARKET_LIVE_TRADING_APPROVAL_CHECKLIST.md` |
| Docker (deploy only) | `docs/LIVE_DUTCH_ARB_DOCKER.md`, `docs/ORACLE_VPS_SETUP.md` |
| Engine commands | `src/polymarket_predictive_engine/cli.py` (`COMMANDS`) |
| Shadow research cycle | `scripts/run_polymarket_shadow_research_cycle.ps1`, `scripts/install_polymarket_shadow_research_task.ps1` |
| Agent lane / why-no-trade trace | `src/polymarket_predictive_engine/decision_trace.py` |

The repo has two parts: the **SuperBru score engine** (`src/superbru_score_engine`, see `README.md`)
and the **Polymarket predictive engine** (`src/polymarket_predictive_engine`, current focus). Both run
locally; neither requires Docker for development.

## Before pushing

- Run `pytest` where practical.
- Keep changes leakage-safe and dry-run/shadow-safe.
- Do not add a live order path or relax the gates above.
- When changing Polymarket discovery, prefer broad opportunity discovery plus family-balanced target selection over single-family concentration.
