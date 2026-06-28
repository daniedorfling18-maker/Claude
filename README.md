# Claude Research Engines

> **Current active work:** Polymarket predictive engine in automated **shadow-research mode**.
> The system is **not approved for paper trading** and must not be used for live trading.
> Start here: [`docs/POLYMARKET_CURRENT_STATE.md`](docs/POLYMARKET_CURRENT_STATE.md) and [`AGENTS.md`](AGENTS.md).

This repository contains two related local-first research systems:

1. **Polymarket predictive engine** — current focus. It discovers liquid Polymarket opportunities, builds point-in-time features, scores mispricing candidates, captures forward shadow evidence, and audits whether paper trading is allowed.
2. **World Cup / SuperBru score engine** — legacy-but-still-useful score-prediction engine that converts bookmaker odds into calibrated scoreline distributions and maximises expected SuperBru points.

The repo is local-first. Docker is for deployment scenarios only, not normal local development.

---

## Current Polymarket state

Last project state update: **2026-06-28**.

The Polymarket system is now in a much better place than when debugging began:

```text
websocket collection: working
metadata enrichment: working
broad liquidity discovery: working
family-balanced websocket targets: working
prediction pipeline: working
mispricing-alpha scoring: working
shadow evidence capture: working
scheduled local automation: working
local-history audit: working
paper trading: blocked by governance
live trading: not invoked
```

The important conclusion is:

```text
The broker is not broken.
The websocket route is not the current blocker.
The current blocker is insufficient positive forward evidence.
```

Current expected audit result:

```text
paper_allowed = false
paper_reason = no approved trade_signals rows; sports_other shadow evidence is not positive; sports_other has no closed/settled positions yet
paper_trading_invoked = false
live_trading_invoked = false
```

That is the correct fail-closed state.

---

## What we learned

### 1. No-trade was caused upstream, not at the broker

The paper broker had cash/equity, but no approved signals reached it. Rejections were caused by alpha, validation, cohort-promotion, liquidity, spread, model-window, and same-category evidence gates.

### 2. Bad 5-minute crypto families remain excluded

The following fast 5-minute crypto families showed poor evidence and should not be treated as actionable just because they are liquid:

```text
crypto_btc_updown_5m
crypto_sol_updown_5m
crypto_xrp_updown_5m
crypto_updown_5m
```

They may still appear in diagnostics, but they should not crowd out research slots or get promoted without new evidence.

### 3. `unknown` is research-only

Some liquid markets are currently classified as `unknown`, including esports, tennis outrights, legal/policy, and other event types. These are useful for classifier expansion, but not actionable until they have a real family classification and family-specific validation path.

### 4. `sports_other` is the first real alpha-shadow candidate

The strongest accepted alpha-shadow candidates are currently World Cup round-of-16 style `sports_other` markets. They are real candidates, but the cohort is still open, negative mark-to-market, and not settled. This means it cannot yet support paper promotion.

### 5. BTC 15-minute Up/Down is a timing diagnostic, not the strategy

`crypto_btc_updown_15m` is currently the main fast-feedback family because it is liquid and closes soon. It is useful for testing whether short-window data, model timing, and scoring work. It is not the system's target strategy by itself.

### 6. Discovery is now broad by default

The system should look for **all plausible liquid Polymarket opportunities**, not only BTC Up/Down. Liquidity discovery now scans broad areas including sports, soccer, football, basketball, baseball, tennis, golf, UFC, esports, World Cup, politics, elections, Trump, Fed, inflation, economy, stocks, crypto, BTC/ETH/SOL/XRP, AI, OpenAI, SpaceX, weather, and culture.

Websocket targets are now selected across all liquid non-excluded families with a per-family cap, so one family cannot consume every slot.

---

## Current safe workflow

Install or refresh the scheduled local shadow-research task:

```powershell
.\scripts\install_polymarket_shadow_research_task.ps1 `
  -IntervalMinutes 15 `
  -WebsocketSeconds 30
```

Check latest status:

```powershell
Get-Content .\work\shadow_research_cycle_latest_status.json -Raw
```

Run one manual diagnostic cycle:

```powershell
.\scripts\run_polymarket_shadow_research_cycle.ps1 `
  -ConfigPath polymarket_predictive_config.example.yaml `
  -WebsocketSeconds 30
```

Read the audit:

```powershell
Get-Content .\outputs\polymarket_model_governance\local_history_audit_report.md -Raw
```

Check discovery and target balance:

```powershell
$liq = Get-Content .\outputs\polymarket_model_governance\liquidity_discovery_summary.json -Raw | ConvertFrom-Json
$liq.model_target_queue |
  Select-Object family, status, tradable_tokens, fast_feedback_tradable_tokens, shortest_time_to_close_hours, recommendation |
  Format-Table -AutoSize

Import-Csv .\outputs\polymarket_model_governance\websocket_liquidity_targets.csv |
  Group-Object family |
  Sort-Object Count -Descending |
  Select-Object Count, Name |
  Format-Table -AutoSize
```

Do **not** start the old local live loop as the default workflow. The current workflow is shadow research plus audit.

---

## What must happen before paper trading can be considered

Do not paper trade until the audit allows it and a human review confirms the relevant family. The minimum evidence shape is:

```text
paper_allowed = true
approved_signals > 0
family-specific shadow evidence is positive
closed/settled fills exist
ROI clears the configured threshold
monthly run-rate is plausible
cohort is probationary/promoted by governance
```

Even then, the next step is tiny probationary paper, not live trading.

---

## Why the $100/month target is not solved yet

At the configured probationary stake of `$2`, a 3% ROI produces only `$0.06` per trade. Hitting `$100/month` at that stake would require roughly 1,667 trades per month, which is unrealistic.

The path is therefore:

```text
1. Discover broad liquid opportunities.
2. Classify them into reliable families.
3. Prove one family in shadow with positive closed evidence.
4. Move only that family to tiny probationary paper.
5. Scale stake only after evidence remains positive.
```

Stake scaling before evidence would scale losses, not expected value.

---

## Important Polymarket files

| Purpose | File |
|---|---|
| Current project state | `docs/POLYMARKET_CURRENT_STATE.md` |
| Operating runbook | `docs/POLYMARKET_SHADOW_RESEARCH_RUNBOOK.md` |
| Agent instructions | `AGENTS.md` |
| Scheduled shadow runner | `scripts/run_polymarket_shadow_research_cycle.ps1` |
| Scheduled task installer | `scripts/install_polymarket_shadow_research_task.ps1` |
| Liquidity discovery | `scripts/run_polymarket_liquidity_discovery.py` |
| Local history audit | `scripts/audit_polymarket_local_history.py` |
| Alpha candidate shadow bridge | `scripts/run_alpha_candidate_shadow_evidence.py` |
| Engine CLI | `src/polymarket_predictive_engine/cli.py` |
| Websocket collector | `src/polymarket_predictive_engine/websocket_collector.py` |
| Mispricing alpha | `src/polymarket_predictive_engine/mispricing_alpha.py` |
| Shadow cohort ledger | `src/polymarket_predictive_engine/shadow_cohort.py` |

Important outputs:

```text
work/shadow_research_cycle_latest_status.json
outputs/polymarket_model_governance/local_history_audit_report.md
outputs/polymarket_model_governance/liquidity_discovery_summary.json
outputs/polymarket_model_governance/websocket_liquidity_targets.csv
outputs/polymarket_model_governance/alpha_candidate_shadow_evidence_inputs.csv
outputs/polymarket_shadow/shadow_positions.csv
outputs/polymarket_shadow/shadow_fills.csv
outputs/polymarket_predictions/mispricing_alpha_scores.csv
outputs/polymarket_predictions/rejected_signals.csv
outputs/polymarket_predictions/trade_signals.csv
```

`trade_signals.csv` being empty is currently expected.

---

## Safety rules

- Do not weaken gates to force trades.
- Do not remove same-category validation.
- Do not bypass cohort promotion.
- Do not treat `unknown` as actionable.
- Do not trust bad 5-minute crypto families because they are liquid.
- Do not raise stake before positive closed evidence exists.
- Do not add or enable a live order path.

---

## SuperBru score engine quick start

The legacy SuperBru engine remains available.

Linux / macOS:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp config.example.yaml config.yaml
python -m superbru_score_engine config-check --config config.yaml --profiles calibration_profiles.yaml
python -m superbru_score_engine predict --config config.yaml --fixtures examples/fixtures.csv --odds-json examples/odds_snapshot.json --out-dir outputs
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
copy config.example.yaml config.yaml
python -m superbru_score_engine config-check --config config.yaml --profiles calibration_profiles.yaml
python -m superbru_score_engine predict --config config.yaml --fixtures examples/fixtures.csv --odds-json examples/odds_snapshot.json --out-dir outputs
```

The SuperBru engine converts bookmaker odds into calibrated scoreline distributions and selects the score prediction that maximises expected SuperBru points. It is not a most-likely-score picker.

Key SuperBru commands:

| Command | Purpose |
|---|---|
| `config-check` | Validate config against calibration profile |
| `fetch-odds` | Fetch live odds, write fixtures/odds JSON |
| `predict` | Run decision engine, output predictions |
| `results` | Fetch completed scores, update ratings store |
| `backtest` | Score predictions against historical results |
| `tune` | Hyperparameter calibration sweep |
| `football-data-backtest` | World Cup proxy calibration via Football-Data |
| `football-data-league-backtest` | Big Five league calibration sweep |
| `the-odds-api-historical-backtest` | Historical odds snapshot comparison |
| `public-results-backtest` | Ratings-only backtest from public results CSV |

---

## Local-first development

```bash
pip install -e ".[dev]"
pytest
```

Do not use Docker for local development. Use Docker only for deployment-like scenarios and only with the existing safety guidance.
