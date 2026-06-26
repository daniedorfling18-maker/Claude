# Running Polymarket locally on Docker (dry-run, continuous)

Everything here is **dry-run**. The bot stack can graduate to live only behind explicit gates
(see [Governance and safety](#governance-and-safety)); my two engine services have no order path
at all.

## TL;DR — the bot + websockets + dutch-arb, constantly, in one command

`docker-compose.polymarket-fixed.yml` (the REST bot pipeline) and `docker-compose.live.yml`
(websocket feed + dutch-arb monitor) write **disjoint** `outputs/` paths, so Docker can merge them
into one project:

```bash
cp .env.example .env                                   # first time only (defaults are dry-run)
docker compose \
  -f docker-compose.polymarket-fixed.yml \
  -f docker-compose.live.yml \
  up -d --build
docker compose -f docker-compose.polymarket-fixed.yml -f docker-compose.live.yml ps
```

That runs, continuously, with `restart: unless-stopped`:

| Service | Stack | Feed | Writes |
|---|---|---|---|
| `pm-fixed-monitor` (mispricing bot) | fixed | REST | `outputs/polymarket/market_snapshot.csv`, `opportunities.csv`, `execution_log.csv` |
| `pm-fixed-converter` | fixed | — | `inputs/polymarket/model_probabilities.csv` (World-Cup winner probs) |
| `pm-fixed-long-short` | fixed | REST | `outputs/polymarket/long_short_intents.csv` |
| `pm-fixed-mm-eval` | fixed | REST | `outputs/polymarket/mm_quote_*.csv` |
| `pm-fixed-ml` | fixed | REST | `outputs/polymarket/*` (training collector) |
| `live-mispricing` | live | **WebSocket** | `outputs/polymarket_training/websocket_market_features.csv`, `outputs/polymarket_mispricing/` |
| `dutch-arb-monitor` | live | REST | `outputs/polymarket_arbitrage/` |

No two services write the same file, so this respects the duplicate-writer rule
(`docs/POLYMARKET_DOCKER_SAFETY_AUDIT.md`). If you only want the dutch-arb monitor + websocket feed,
run just `-f docker-compose.live.yml`.

## The compose stacks, and which can run together

| Compose file | Services | Writes | Feed |
|---|---|---|---|
| `docker-compose.live.yml` | dutch-arb-monitor, live-mispricing | `outputs/polymarket_arbitrage/`, `…/websocket_market_features.csv`, `…/polymarket_mispricing/` | REST + **WS** |
| `docker-compose.polymarket-fixed.yml` | bot → converter → long-short → mm-eval → ml | `outputs/polymarket/*` | REST |
| `docker-compose.polymarket-wide-raw.yml` | per-category scan + ml collectors | `outputs/polymarket_wide/<category>/*` | REST |
| `docker-compose.yml` | polymarket-agent, websocket-live-features | `outputs/polymarket/*`, **`…/websocket_market_features.csv`** | REST + WS |
| `docker-compose.monitor.yml` | polymarket-monitor (bot), polymarket-long-short | `outputs/polymarket/*` | REST |
| `docker-compose.polymarket-collector.yml` | pm-data-collector | `outputs/polymarket/*` | REST |

**Safe together:** `polymarket-fixed` + `live` (disjoint paths, shown above); `polymarket-wide-raw`
is category-isolated so it never collides. **Do not also start** `docker-compose.yml`,
`docker-compose.monitor.yml`, or `docker-compose.polymarket-collector.yml` alongside them — those
re-write `outputs/polymarket/*` and/or `websocket_market_features.csv`, which is the duplicate-writer
hazard the safety audit prohibits.

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
# live-only stack
docker compose -f docker-compose.live.yml down
# or, if you started the combined bot + websockets + dutch-arb stack
docker compose -f docker-compose.polymarket-fixed.yml -f docker-compose.live.yml down
```

## Governance and safety

There are **two independent live-trade gates** in this repo. Both default to off; the commands
above set neither.

**1. Engine gate** (`config.py :: live_trading_allowed`) — governs `polymarket-engine` commands
(`dutch-arb-monitor`, `live-mispricing`, paper engine). Fail-closed in four places, and on top of
that the engine's live executor (`execution/live.py`) is a **skeleton that raises** — there is no
order path at all:

1. Kill switch off — `POLYMARKET_KILL_SWITCH` ≠ `1`.
2. `trading.mode: live` in the engine config (default `paper`).
3. `POLYMARKET_LIVE_TRADING=1` in the environment.
4. Human approval file `config/polymarket_live_approval.yaml`
   (template: `config/polymarket_live_approval.example.yaml`).

**2. Bot gate** (`scripts/polymarket_mispricing_bot.py :: LiveExecutor`) — governs the bot /
long-short / mm stack in `docker-compose.polymarket-fixed.yml`. This one **can** place real orders
(passive maker bids, order type forced to a resting limit), but only when **all** of these hold:

1. `PM_MODE=live` (the fixed stack hard-codes `dry_run`).
2. `POLYMARKET_EXECUTE_LIVE=true` (compose hard-codes `false`).
3. `check_geoblock` passes — Polymarket's geoblock API must not block the host IP (rules out the US).
4. `POLYMARKET_PRIVATE_KEY` (+ optional CLOB creds) present.
5. The `py-clob-client-v2` SDK installed (`INSTALL_POLYMARKET_SDK=true` at build).

Miss any one and the bot logs `dry_run` / `live_error` and places nothing. Per
`docs/POLYMARKET_MISPRICING_BOT.md`, keep it in `dry_run` until `long_short_intents.csv` has looked
sane for several sessions and you have reconciled fills against your wallet before raising
`POLYMARKET_MAX_ORDER_USD` (default `5`).

Read before going anywhere near live:

- `docs/POLYMARKET_ACTUARIAL_MODEL_GOVERNANCE.md` — intended use, model risk, approval requirements.
- `docs/POLYMARKET_RISK_CONTROL_STANDARD.md` — bankroll, Kelly cap, exposure, spread/liquidity, kill switch.
- `docs/POLYMARKET_LIVE_TRADING_APPROVAL_CHECKLIST.md` — the full pre-live checklist.
- `docs/POLYMARKET_DOCKER_SAFETY_AUDIT.md` — the duplicate-writer rule enforced above.

The honest expectation from `docs/ACTUARIAL_AUDIT_PREDICTIVE_VALUE.md`: liquid Polymarket markets
are efficient on all three axes (direction, spread, basket), so this is for **monitoring and
validation**, not an expectation of profit.
