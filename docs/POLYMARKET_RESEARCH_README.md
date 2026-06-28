# Polymarket Research README

Last updated: 2026-06-28

This is the dedicated README for the Polymarket predictive engine. It documents where the system is now, what we learned, what is automated, and what must happen before any paper-risk step is considered.

## Executive summary

The Polymarket engine is currently in **automated shadow-research mode**.

```text
paper_allowed = false
paper_trading_invoked = false
live_trading_invoked = false
```

This is not a failure. It is the intended fail-closed state while the system gathers forward evidence.

The infrastructure now works:

```text
websocket collection: working
metadata enrichment: working
broad liquidity discovery: working
family-balanced websocket target selection: working
feature build: working
prediction: working
mispricing-alpha scoring: working
shadow evidence capture: working
scheduled local automation: working
local-history audit: working
```

The current blocker is **not** the broker and **not** the websocket. The blocker is that no family has enough positive closed/settled forward evidence to justify paper trading.

## Current audit state

The latest expected audit verdict is:

```text
Paper decision: BLOCK
```

Current blocking reasons:

```text
no approved trade_signals rows
sports_other shadow evidence is not positive
sports_other has no closed/settled positions yet
```

This is the correct state. `trade_signals.csv` being empty is expected until governance allows paper.

## System goal

The goal is **not** to force trades. The goal is to find repeatable, measurable edge across all plausible Polymarket opportunities.

The target remains:

```text
$100/month profit target
```

But the route to that target is evidence-first:

```text
1. Discover broad liquid Polymarket opportunities.
2. Classify markets into meaningful families.
3. Score candidates using point-in-time features and independent anchors where available.
4. Capture forward shadow evidence.
5. Promote only families with positive closed/settled evidence.
6. Use tiny probationary paper only after audit approval.
7. Scale stake only after evidence remains positive.
```

At the configured probationary stake of `$2`, a 3% ROI produces only `$0.06` per trade. That would require about 1,667 trades/month to reach `$100/month`, which is unrealistic. Therefore, the practical path is finding a family that can support both positive ROI and higher justified stake after evidence.

## What we learned

### The broker was not broken

The paper account/broker was not the reason no trades happened. The broker had cash/equity. The issue was that no approved signal reached it. The actual blocks were upstream: alpha thresholds, same-category validation, cohort promotion, liquidity, spread, model-window, and fundamental cross-check gates.

### Websocket routing is fixed

The websocket collector now uses liquidity-selected token IDs and retries supported subscription envelopes. The working observed Polymarket websocket envelope is `assets_ids`. Websocket normalisation now enriches rows with market metadata, so useful rows no longer collapse into `unknown`.

### Bad 5-minute crypto is excluded from action

The following fast 5-minute crypto families showed poor local/shadow evidence and must not be treated as actionable just because they are liquid:

```text
crypto_updown_5m
crypto_btc_updown_5m
crypto_sol_updown_5m
crypto_xrp_updown_5m
```

They can remain visible in diagnostics, but they should not be promoted or allowed to dominate websocket slots.

### Unknown markets are research-only

`unknown` markets may include esports, tennis outrights, legal/policy questions, culture, or other useful Polymarket areas. They are not automatically bad, but they are not actionable until classified into a real family with a validation path.

### Sports is the first accepted alpha-shadow family

The first accepted alpha-shadow candidates are currently `sports_other`, especially World Cup round-of-16 style markets. They are real candidates, but the cohort is currently open, negative mark-to-market, and not settled. It therefore cannot justify paper risk yet.

### BTC 15m is only a fast-feedback diagnostic

`crypto_btc_updown_15m` currently appears as the main fast-feedback family because it is liquid and closes soon. It is useful for testing short-window collection and scoring, but it is not the strategy by itself. It should not crowd out broader Polymarket discovery.

### Discovery is now broad by default

The scanner now searches broadly across Polymarket opportunity areas, including:

```text
sports, soccer, football, basketball, baseball, tennis, golf, UFC,
esports, World Cup, politics, elections, Trump, Fed, inflation,
economy, stocks, crypto, bitcoin, ethereum, solana, XRP,
AI, OpenAI, SpaceX, weather, culture
```

Websocket target selection now uses liquid opportunities across families with a per-family cap. The intent is to avoid a BTC-only system and route all plausible Polymarket money-making events into research, shadow, or blocked status.

## Current automation

The main local automation is the Windows scheduled task:

```text
Polymarket Shadow Research Cycle
```

Install or refresh it from the repo root:

```powershell
.\scripts\install_polymarket_shadow_research_task.ps1 `
  -IntervalMinutes 15 `
  -WebsocketSeconds 30
```

The cycle performs:

```text
liquidity discovery
websocket collection
websocket normalisation
feature build
prediction
mispricing-alpha scoring
dry/governance signal generation
alpha-candidate shadow evidence
local-history audit
```

The latest status is written to:

```text
work/shadow_research_cycle_latest_status.json
```

A healthy run has:

```json
{
  "status": "ok",
  "paper_allowed": false,
  "paper_trading_invoked": false,
  "live_trading_invoked": false
}
```

## Manual diagnostic commands

Run one shadow-only cycle:

```powershell
.\scripts\run_polymarket_shadow_research_cycle.ps1 `
  -ConfigPath polymarket_predictive_config.example.yaml `
  -WebsocketSeconds 30
```

Check status:

```powershell
Get-Content .\work\shadow_research_cycle_latest_status.json -Raw
```

Check audit:

```powershell
Get-Content .\outputs\polymarket_model_governance\local_history_audit_report.md -Raw
```

Check broad opportunity queue:

```powershell
$liq = Get-Content .\outputs\polymarket_model_governance\liquidity_discovery_summary.json -Raw | ConvertFrom-Json
$liq.model_target_queue |
  Select-Object family, status, tradable_tokens, fast_feedback_tradable_tokens, shortest_time_to_close_hours, recommendation |
  Format-Table -AutoSize
```

Check websocket target family balance:

```powershell
Import-Csv .\outputs\polymarket_model_governance\websocket_liquidity_targets.csv |
  Group-Object family |
  Sort-Object Count -Descending |
  Select-Object Count, Name |
  Format-Table -AutoSize
```

Check active sports evidence:

```powershell
Import-Csv .\outputs\polymarket_shadow\shadow_positions.csv |
  Where-Object { $_.status -eq "open" -and $_.signal_cohort -eq "sports_other" } |
  Select-Object opened_at, shadow_source, category, market_slug, outcome, entry_price, latest_mark_price, unrealised_pnl_usdc, return_pct, close_time |
  Format-List
```

## Important files

### Scripts

```text
scripts/run_polymarket_shadow_research_cycle.ps1
scripts/install_polymarket_shadow_research_task.ps1
scripts/run_polymarket_liquidity_discovery.py
scripts/audit_polymarket_local_history.py
scripts/run_alpha_candidate_shadow_evidence.py
```

### Engine files

```text
src/polymarket_predictive_engine/websocket_collector.py
src/polymarket_predictive_engine/websocket_normaliser.py
src/polymarket_predictive_engine/mispricing_alpha.py
src/polymarket_predictive_engine/strategy.py
src/polymarket_predictive_engine/shadow_cohort.py
src/polymarket_predictive_engine/strategy_search.py
src/polymarket_predictive_engine/cli.py
```

### Output files

```text
work/shadow_research_cycle_latest_status.json
outputs/polymarket_model_governance/local_history_audit_report.md
outputs/polymarket_model_governance/local_history_audit_summary.json
outputs/polymarket_model_governance/liquidity_discovery_summary.json
outputs/polymarket_model_governance/websocket_liquidity_targets.csv
outputs/polymarket_model_governance/alpha_candidate_shadow_evidence_inputs.csv
outputs/polymarket_shadow/shadow_positions.csv
outputs/polymarket_shadow/shadow_fills.csv
outputs/polymarket_predictions/mispricing_alpha_scores.csv
outputs/polymarket_predictions/rejected_signals.csv
outputs/polymarket_predictions/trade_signals.csv
```

## Promotion requirements before paper

Paper trading can only be reviewed when:

```text
paper_allowed = true
approved_signals > 0
family-specific shadow evidence is positive
closed/settled fills exist
ROI clears the configured threshold
monthly run-rate is plausible
cohort is probationary/promoted by governance
```

If any of those are missing, remain in shadow research.

## Strategic next steps

1. **Keep broad discovery active.** The scanner should search all plausible Polymarket opportunity areas, not only crypto.
2. **Improve family classification.** Convert useful `unknown` markets into real families such as esports, tennis outrights, legal/policy, culture, weather, macro, tech, or company-event families.
3. **Add independent anchors.** For sports, use sharp bookmaker/no-vig odds where available. For crypto event markets, use independent fundamental sources such as Deribit or other fair-value anchors where applicable.
4. **Use BTC 15m only as timing diagnostics.** It is useful for fast feedback but should not become the whole strategy unless it proves positive in shadow.
5. **Wait for sports evidence to settle.** `sports_other` is the first accepted alpha-shadow family, but it is not yet positive or settled.
6. **Do not scale stake before evidence.** Scaling early would scale variance and losses, not expected value.

## Safety rules

- Do not weaken gates to force trades.
- Do not remove same-category validation.
- Do not bypass cohort promotion.
- Do not treat `unknown` as actionable.
- Do not trust bad 5-minute crypto families because they are liquid.
- Do not raise stake before positive closed evidence exists.
- Do not add or enable a live order path.

## Relationship to other docs

Read these in order:

```text
1. docs/POLYMARKET_RESEARCH_README.md
2. docs/POLYMARKET_CURRENT_STATE.md
3. docs/POLYMARKET_SHADOW_RESEARCH_RUNBOOK.md
4. AGENTS.md
5. docs/ACTUARIAL_AUDIT_PREDICTIVE_VALUE.md
```
