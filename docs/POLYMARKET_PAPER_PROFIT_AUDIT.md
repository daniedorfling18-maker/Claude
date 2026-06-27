# Polymarket paper-profit audit

This note records the current paper/shadow system design and the changes made to aim at the `$100/month` paper-profit target without weakening trading gates.

## Ledger snapshot refresh behavior

The local live loop deliberately refreshes two different classes of state:

1. **Append/idempotent SQLite market snapshots.**
   `scripts/run_polymarket_local_live_loop.py` normalises the latest websocket top-of-book features and inserts them into the SQLite `market_snapshots` table. The insert uses an idempotency key based on source, timestamp, market id, token id, and event type. These rows are not paper fills and are not cash ledger entries; they are current price evidence for marking positions and generating fresh features.

2. **Current-state JSON/dashboard files.**
   Files such as `local_live_loop_heartbeat.json`, `live_paper_loop_heartbeat.json`, `forward_paper_cycle.json`, `paper_trade_refresh.json`, `dashboard_data.json`, and `agent_status.json` are intentionally overwritten. They are dashboards/heartbeats, not immutable ledgers. The immutable paper state is in SQLite and exported CSVs under `outputs/polymarket_portfolio/`.

So a changing `Ledger snapshots` number on the dashboard means the live loop is ingesting fresh top-of-book marks into `market_snapshots` for the current tick. It does not mean historical paper fills are being erased.

## Current no-trade interpretation

When the dashboard shows `Approved signals = 0`, the paper broker has nothing to fill. Current blockers are usually one or more of:

- lower-bound edge below the configured threshold;
- liquidity below the alpha or shadow gate;
- spread or relative-spread above limit;
- model timing window not met (`before_model_window`, `too_close_to_resolution`, or `outside_time_to_close_window`);
- negative/quarantined cohort evidence.

The correct response is to improve evidence collection and market targeting, not to lower gates.

## Implemented improvements

### 1. Liquidity-aware local scan start

`start_polymarket_local_live.ps1` now defaults local starts to:

- `POLYMARKET_SCAN_QUERY_MODE=rotate`
- `POLYMARKET_MAX_SCAN_QUERIES=1`

If `liquidity_discovery_summary.json` exists and `POLYMARKET_QUERIES` is not already set, the launcher builds a liquidity-aware query order so zero-tradable families are demoted before the bot starts.

The one-click `start_polymarket_bot.cmd` also defaults to rotate mode and one query per cycle.

### 2. Crypto Up/Down proxy label builder

`src/polymarket_predictive_engine/crypto_updown_labels.py` adds `build_crypto_updown_proxy_labels()`. It uses the existing public-price crypto Up/Down settlement logic to generate clean resolution rows for resolved 5m/15m BTC, ETH, SOL, and XRP Up/Down markets.

CLI:

```powershell
python -m polymarket_predictive_engine.cli build-crypto-updown-labels --config polymarket_predictive_config.example.yaml
python -m polymarket_predictive_engine.cli build-labels --config polymarket_predictive_config.example.yaml
```

The builder merges resolved rows into `outputs/polymarket_training/websocket_resolutions.csv`, enabling `build-labels` to join WebSocket/snapshot features by token id.

### 3. Expanded research focus

`research_focus.py` no longer tracks only three hard-coded cohorts. It now includes:

- core BTC/XRP/SOL watchlist cohorts;
- all probationary cohorts;
- all promoted cohorts;
- near-miss learning cohorts;
- positive ROI/P&L cohorts with enough evidence to be useful.

It also embeds promotion-review and profit-goal summaries into `research_focus.json`.

### 4. Promotion review report

`promotion_review.py` writes `outputs/polymarket_model_governance/promotion_review.json` with exact remaining gates per cohort: fills, settled fills, P&L, ROI-equivalent P&L, tracking hours, and monthly run-rate.

CLI:

```powershell
python -m polymarket_predictive_engine.cli promotion-review --config polymarket_predictive_config.example.yaml
```

### 5. $100/month goal planner

`goal_planner.py` writes `outputs/polymarket_model_governance/paper_profit_goal_plan.json` with:

- target daily/weekly/monthly P&L;
- actual P&L since clean baseline;
- prorated pace;
- required daily P&L from here;
- approved/rejected signal counts;
- promoted/probationary cohorts;
- main gap and recommended action.

CLI:

```powershell
python -m polymarket_predictive_engine.cli goal-plan --config polymarket_predictive_config.example.yaml
```

## Operating principle

The $100/month target should be pursued through better selection, labeling, and evidence collection, not by lowering minimum edge, liquidity, spread, timing, cohort, or risk gates.
