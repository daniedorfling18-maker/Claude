# Polymarket Mispricing Bot

This workflow adds a guarded Polymarket scanner/trader to the repo without changing the existing Superbru automation.

## What it does

The bot runs from `.github/workflows/polymarket-mispricing-bot.yml`.

It:

1. Discovers active Polymarket events through the Gamma API.
2. Extracts active order-book-enabled markets and outcome token IDs.
3. Pulls CLOB order books for each token.
4. Loads your model probabilities from CSV or JSON.
5. Flags buy opportunities when model probability is above the executable ask.
6. Flags sell opportunities when the executable bid is above model probability and a known positive position exists.
7. Writes artifacts:
   - `outputs/polymarket/market_snapshot.csv`
   - `outputs/polymarket/opportunities.csv`
   - `outputs/polymarket/execution_log.csv`

The workflow is **manual-only** (`workflow_dispatch`) — a manual dry run — and the script loops for
about 270 seconds. GitHub Actions cannot run a true permanent daemon; move the same script to a VPS
or self-hosted runner (see the Docker stacks) if you need always-on execution. CI validates the
allowed trigger set, so keep this workflow's trigger as `workflow_dispatch` (the `ci.yml`
automation-rules check expects exactly that).

## Modes

`PM_MODE` supports:

- `scan`: market/orderbook snapshot only.
- `dry_run`: calculate opportunities and paper-log them, but do not submit orders.
- `live`: submit orders only when `POLYMARKET_EXECUTE_LIVE=true` is also set.

Scheduled runs default to `dry_run` unless the repository variable `POLYMARKET_MODE` is set.

## Required model probabilities

The bot cannot know whether a market is mispriced unless you provide an independent fair probability.

Default file path:

```text
inputs/polymarket/model_probabilities.csv
```

Supported columns:

```csv
token_id,probability
123456789,0.62
```

or:

```csv
market_slug,outcome,probability
will-team-a-beat-team-b,Yes,0.62
```

or:

```csv
event_slug,market_slug,outcome,probability
world-cup-match-a-b,will-team-a-beat-team-b,Yes,0.62
```

You can also provide a JSON mapping as `POLYMARKET_MODEL_PROBABILITIES_JSON`.

## Selling

Selling is gated more strictly than buying. The bot will only sell if it knows you hold the token.

Use either:

```text
inputs/polymarket/positions.csv
```

with:

```csv
token_id,shares
123456789,10
```

or set `POLYMARKET_WALLET_ADDRESS` so the script can query current positions from the public Data API.

To override this protection, set:

```text
POLYMARKET_ALLOW_SELL_WITHOUT_POSITION=true
```

That is not recommended.

## Live trading setup

Secrets required for live trading:

```text
POLYMARKET_PRIVATE_KEY
POLYMARKET_WALLET_ADDRESS
CLOB_API_KEY
CLOB_SECRET
CLOB_PASS_PHRASE
```

If the CLOB API credentials are not supplied, the script attempts to derive them with the private key.

Live mode also requires repository variables:

```text
POLYMARKET_MODE=live
POLYMARKET_EXECUTE_LIVE=true
```

The geoblock check runs before live trading and blocks execution when the runner IP is not eligible.

## Important controls

Default controls:

```text
POLYMARKET_MIN_EDGE=0.04
POLYMARKET_MAX_SPREAD=0.04
POLYMARKET_MAX_ORDER_USD=5
POLYMARKET_MIN_ASK_SIZE=5
POLYMARKET_MIN_BID_SIZE=5
POLYMARKET_ORDER_TYPE=FOK
```

Do not increase order size until paper logs are stable and you have reconciled fills against your wallet.

## Recommended rollout

1. Set `POLYMARKET_MODE=scan`, wait for the next scheduled run, and review artifacts.
2. Add a small model probability CSV and set `POLYMARKET_MODE=dry_run`.
3. Review artifacts over multiple days.
4. Only then enable `POLYMARKET_MODE=live` and `POLYMARKET_EXECUTE_LIVE=true` with very small order size.
5. Move to a VPS or self-hosted runner for production continuity.

## Long/short trading engine

`scripts/polymarket_long_short_engine.py` turns the bot's output into a long/short trading
layer. It consumes the bot's live `outputs/polymarket/market_snapshot.csv` (per-token
`best_bid` / `best_ask` / `fair_probability`) and, for every outcome token, evaluates:

- **Directional (long/short)** — long (buy YES) when the model fair beats the ask, short
  (buy the complementary NO) when it is below the bid. Paper only.
- **Market-making** — a passive maker bid placed strictly inside the spread (never crosses,
  so it is always a maker). This is the mode that can graduate to live.

Run it after a bot scan so the book is fresh:

```bash
python scripts/polymarket_long_short_engine.py \
  --market-snapshot outputs/polymarket/market_snapshot.csv
```

Output: `outputs/polymarket/long_short_intents.csv` (every intent is `DRY_RUN` by default).

### Graduating market-making to live

Live market-making reuses the bot's `BotConfig`, `check_geoblock`, and `LiveExecutor`, so
**every existing guard applies** and order type is forced to `GTC` (resting maker limit,
never a taker). It places passive maker **BUY** limits only, capped at
`POLYMARKET_MAX_ORDER_USD` and `--max-live-orders` (default 3). It stays dry-run unless:

```text
PM_MODE=live
POLYMARKET_EXECUTE_LIVE=true
# plus POLYMARKET_PRIVATE_KEY (+ CLOB creds) and a non-geoblocked IP
```

Without all of these the engine reports `dry_run` / `live_error` and places nothing.

Honest expectation: pre-match WC odds barely move, so the **directional** edge is ~nil;
market-making the spread (+ Polymarket maker rebates) is the market-neutral capital engine.
Graduate it only after the dry-run `long_short_intents.csv` looks sane for several sessions,
and reconcile any live fills against your wallet before increasing size.
