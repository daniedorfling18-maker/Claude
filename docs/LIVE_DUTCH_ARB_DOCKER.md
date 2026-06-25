# Running the live stack locally on Docker (dry-run, continuous)

This runs two long-lived, **dry-run** services from `docker-compose.live.yml`. Neither ever places
an order — they read public order books, compute signals, and write results under `./outputs`.

| Service | What it does | Feed |
|---|---|---|
| `dutch-arb-monitor` | Polls Gamma/CLOB for complete-set (negRisk) dutch-book arbs, ranks any locks by **annualised return on capital**, diffs state across polls (appeared / persisting / cleared), and alerts when one clears a threshold. | REST polling |
| `live-mispricing` | Captures the live order book over the **WebSocket**, then scans for `BUY_YES` / `SELL_YES` / `MAKE` signals against your fair values. | WebSocket |

## 1. Prerequisites

- Docker with the Compose plugin (`docker compose version`).
- A clone of this repo. From the repo root:

```bash
cp .env.example .env      # first time only; safe defaults are dry-run
```

The defaults in `.env` keep everything in dry-run (`PM_MODE=dry_run`, `POLYMARKET_EXECUTE_LIVE=false`)
and require no wallet or API keys.

## 2. Build and start (constantly running)

```bash
docker compose -f docker-compose.live.yml up -d --build
```

Both containers have `restart: unless-stopped`, so they come back after a crash or a host reboot
and run until you stop them.

## 3. Watch it

```bash
# arb monitor (ranked opportunities + why events were skipped)
docker compose -f docker-compose.live.yml logs -f dutch-arb-monitor

# websocket mispricing scan
docker compose -f docker-compose.live.yml logs -f live-mispricing
```

## 4. Where results land (`./outputs`, mounted to the host)

```text
outputs/polymarket_arbitrage/dutch_arb_monitor_summary.json   # rewritten every poll: best arb,
                                                              # ranked top-5, alerts, scan_stats,
                                                              # transitions, dry-run execution plan
outputs/polymarket_arbitrage/dutch_arb_monitor_opportunities.csv
outputs/polymarket_arbitrage/dutch_arb_monitor_alerts.csv     # appears once an arb clears the bar
outputs/polymarket_training/websocket_market_features.csv     # latest WebSocket book snapshots
outputs/polymarket_mispricing/*                               # live mispricing signals
```

`dutch_arb_monitor_summary.json` is refreshed on every poll, so you can tail it from a dashboard:

```bash
watch -n 5 'jq "{best:.best_annualised_return_on_capital, arbs:.complete_arbs_latest_poll, \
  scanned:.scan_stats_latest_poll, alerts:.alerts_total}" outputs/polymarket_arbitrage/dutch_arb_monitor_summary.json'
```

If you see `complete_arbs_latest_poll: 0`, read `scan_stats_latest_poll` — it explains why
(`skipped_cardinality` = baskets bigger than the leg cap, `skipped_unpriceable_leg` = a dead/closed
outcome you cannot buy, so the basket is not lockable). That is the expected result on efficient
liquid markets, not an error.

## 5. Tuning (edit `.env`, then re-run `up -d`)

| Variable | Default | Meaning |
|---|---|---|
| `ARB_POLL_SECONDS` | `30` | Sleep between arb polls. |
| `ARB_MAX_EVENTS` | `20` | Events priced per poll. Lower = faster cycles. |
| `ARB_ALERT_ANNUALISED` | `0.10` | Alert when an arb's annualised return on capital clears this. |
| `ARB_MIN_ANNUALISED` | `0.0` | Drop opportunities below this annualised return. |
| `WS_SECONDS` | `60` | WebSocket capture window per mispricing cycle. |
| `MISPRICING_SLEEP_SECONDS` | `5` | Pause between mispricing cycles. |

Fair values for the mispricing scan are read from `inputs/polymarket/model_probabilities.csv`
(mounted read-only). Drop your own there; without it the scan still surfaces spread/`MAKE` signals.
The WebSocket subscribes to the asset ids in `polymarket_predictive_config.example.yaml`
(`websocket_market_data.subscription_message`) — point it at the games you care about.

## 6. Stop

```bash
docker compose -f docker-compose.live.yml down
```

## Safety

These services are analysis-only. The arb monitor's "execution plan" is a list of orders tagged
`dry_run: true` that is **never submitted**, and every summary reports `live_trading: false`
regardless of `trading.mode`. There is no order-placement code path in this stack. Going live would
be a separate, deliberate step behind the kill switch, an explicit `trading.mode: live`, the
`POLYMARKET_LIVE_TRADING=1` opt-in, and a human approval file — none of which this stack sets.
