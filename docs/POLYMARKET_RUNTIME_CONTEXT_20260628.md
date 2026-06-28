# Polymarket runtime context and learnings — 2026-06-28

This note captures the current operating state, lessons learned, and safe next actions for the Polymarket paper-trading/research system. It is intended to preserve repo context so future work does not rely on chat history.

## Current objective

- Primary goal: move toward a **$100/month paper-profit target** without bypassing model, cohort, risk, or readiness gates.
- Operating mode: **paper/research only**.
- Live trading remains out of scope. Do not enable live trading, loosen risk thresholds, or force manual paper entries to chase the target.

## Current safe state

Latest observed state during the 2026-06-28 afternoon SAST session:

- Dashboard/local loop is live and updating.
- Equity remains approximately `$1000.00`.
- Actual P&L since clean baseline remains `$0.00`.
- Exposure remains `$0.00`.
- Approved signals remain `0`.
- Buy fills in the current paper cycle remain `0`.
- The main blocker is expected and healthy: model lower-bound edge remains below the configured trading threshold.
- `profit-sprint` decision remains `WAIT_ACTIVE_WINDOW`.
- The current best targets are three BTC 15-minute Up/Down rows, not immediate trades.

This means the system is doing the correct thing: collecting and scoring without opening new paper positions.

## Active-window plan

The active-window planner was added so `WAIT_ACTIVE_WINDOW` targets have explicit SAST rescore times.

Command:

```powershell
polymarket-engine active-window-plan --config polymarket_predictive_config.example.yaml
```

Current planned rescore targets:

| SAST run time | Market slug | Outcome | Why |
| --- | --- | --- | --- |
| 2026-06-29 06:31 SAST | `btc-updown-15m-1782707400` | Up | First valid model-window rescore for 12:30-12:45 AM ET BTC contract |
| 2026-06-29 06:46 SAST | `btc-updown-15m-1782708300` | Up | First valid model-window rescore for 12:45-1:00 AM ET BTC contract |
| 2026-06-29 07:01 SAST | `btc-updown-15m-1782709200` | Up | First valid model-window rescore for 1:00-1:15 AM ET BTC contract |

At each run time, use the existing safe rescore sequence:

```powershell
polymarket-engine collect-websocket --config polymarket_predictive_config.example.yaml --websocket-seconds 30
polymarket-engine paper-cycle --config polymarket_predictive_config.example.yaml --paper-source websocket
polymarket-engine profit-sprint --config polymarket_predictive_config.example.yaml
```

Only treat `PROBATIONARY_PROBE_READY` or `PAPER_TRADE_READY` as a cue to continue through the existing paper-broker path. `WAIT_ACTIVE_WINDOW`, `COLLECT_MORE_EVIDENCE`, or `approved_signals: 0` means do not trade.

## Dashboard/local loop guidance

The dashboard is useful but not required.

Start the local dashboard and websocket paper loop with:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_polymarket_local_live.ps1 -ForceRestart -WebsocketSeconds 30 -PredictionCycleSeconds 60 -DiscoveryCycleSeconds 600 -MaxAssets 24
```

Open:

```text
http://127.0.0.1:8765/
http://127.0.0.1:8765/agent_status.html
```

Healthy signs:

- `Live loop: live`
- `Resource guard: ok`
- memory below the guard threshold
- websocket messages/features increasing
- ledger snapshots inserted
- exposure remains `$0.00`
- approved signals remain `0` until gates explicitly pass

On tick 1 it is normal for `Full cycle` to show `not_started`, `Discovery` to show `skipped`, and `Last scan` to show `not_run_yet`. The slower prediction/discovery cycles run after their configured intervals.

## Discovery and config lessons

### Public-search 422 failure

A shadow-cycle error was traced to Gamma `public-search` returning HTTP 422 during liquidity discovery. The failure was not caused by trading, the broker, the websocket collector, or the sprint logic.

Immediate local fix:

```yaml
liquidity_discovery:
  public_search_enabled: false
```

Important: appending a second top-level `liquidity_discovery:` block works temporarily in PyYAML because the last duplicate key wins, but it also discards earlier settings such as `event_limit` and `token_limit`. Use the config normaliser to clean this up.

Normalise local config after emergency overrides:

```powershell
python .\scripts\normalise_polymarket_config.py --dry-run
python .\scripts\normalise_polymarket_config.py
```

Expected useful output:

```text
removed_duplicate_liquidity_blocks: 1
replaced_public_search_enabled: true
```

### Liquidity discovery health target

After disabling public search and normalising config, liquidity discovery should compute successfully:

```powershell
python .\scripts\run_polymarket_liquidity_discovery.py --config polymarket_predictive_config.example.yaml
```

Healthy result shape:

- `status: computed`
- `errors: 0`
- non-zero `tokens_scanned`
- non-zero `tradable_tokens`
- family summary populated

## Market-family classification learnings

The dashboard initially showed too many liquid markets as `unknown`. This reduces research value because the system cannot attach future models or cohort evidence to specific families.

Classification improvements added:

- `macro_rates` for Fed / interest-rate / bps markets
- `macro_economy` for inflation, CPI, recession, broad economy rows
- `equities_macro` for stock-index/macroequity rows
- `esports_match` for CS2, Counter-Strike, Rainbow Six, esports/e-sports rows
- `ai_model_leader` for OpenAI, Anthropic, Google, Meta, xAI, and best-AI-model markets
- `culture_science_special` for aliens/UFO/UAP rows
- better tennis routing where event context is available
- continued crypto special/updown routing
- continued sports/world-cup routing

Liquidity discovery now passes `event_slug` and `event_title` into the classifier so rows like tennis winners can use event-level context instead of staying `unknown`.

These classification changes do **not** approve trades. They only improve research grouping and future evidence collection.

## Cohort and evidence context

Current recurring evidence signals:

- `near_miss_learning|unknown` is the strongest probationary watch item, with positive P&L/ROI but still requiring more fills for full promotion.
- `exploratory_inverse_historical_rule|crypto_btc_updown_5m|outcome=up` is near promotion but has not cleared ROI requirements.
- `exploratory_historical_rule|crypto_xrp_updown_5m|outcome=down` has high ROI but too few fills/settled observations.
- Several 5-minute crypto cohorts have negative closed evidence and should remain quarantined or evidence-only until they recover under the existing gates.
- Sports/world-cup candidates are useful for shadow/label evidence but currently fail same-category label gates and should not be forced into paper trading.

## What not to do

Do not:

- manually run `paper-trade` while `approved_signals` is `0`
- lower `minimum_edge`, `minimum_confidence`, or risk thresholds to create activity
- bypass `WAIT_ACTIVE_WINDOW`
- enable live trading
- remove public-safety/live-trading environment guards
- treat shadow entries as proof of tradable edge until promotion gates pass
- overreact to `not_on_pace` while the system has no approved paper signal

## Recommended near-term workflow

1. Leave dashboard/local loop running only if laptop can stay plugged in and awake.
2. Before the BTC windows, do not force paper cycles repeatedly.
3. Around 06:31, 06:46, and 07:01 SAST on 2026-06-29, run `active-window-plan` and the safe rescore sequence.
4. If `profit-sprint` remains `WAIT_ACTIVE_WINDOW`, it is still too early or the plan has not moved to the active row.
5. If it changes to `COLLECT_MORE_EVIDENCE`, the active window opened but the edge did not survive scoring.
6. Only if it changes to `PROBATIONARY_PROBE_READY` or `PAPER_TRADE_READY`, use the existing gated paper-broker path.

## Useful diagnostic commands

```powershell
polymarket-engine profit-sprint --config polymarket_predictive_config.example.yaml
polymarket-engine active-window-plan --config polymarket_predictive_config.example.yaml
python .\scripts\run_polymarket_liquidity_discovery.py --config polymarket_predictive_config.example.yaml
Get-Content .\work\local_live_loop.out.log -Tail 80
Get-Content .\work\local_live_loop.err.log -Tail 80
Get-Content .\outputs\polymarket_model_governance\local_live_loop_discovery_heartbeat.json -Raw
Get-Content .\outputs\polymarket_model_governance\active_window_plan.json -Raw
```

## Open improvements to consider later

- Make Gamma public-search failures non-fatal per query instead of requiring `public_search_enabled: false`.
- Add an automation or local scheduler that triggers active-window rescoring at planned SAST times without loosening gates.
- Add dashboard display of active-window plan status (`WAIT`, `RUN_NOW`, `EXPIRED`).
- Add tests for market-family routing using representative Fed, esports, AI, tennis, crypto, and sports rows.
- Add tests for config normalisation to ensure duplicate top-level blocks are removed safely.
- Add clearer dashboard language distinguishing shadow positions from paper-broker positions.

## Current principle

The system should become better at **waiting, observing, classifying, and explaining** before it becomes more active. The goal is not more trades; the goal is more justified trades under existing governance.
