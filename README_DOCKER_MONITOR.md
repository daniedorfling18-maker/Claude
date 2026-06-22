# Docker monitor quick start

The monitor compose runs two services that share the `outputs/` volume:

- `polymarket-monitor` — the mispricing bot. Scans Polymarket continuously and writes
  `outputs/polymarket/market_snapshot.csv` every few seconds.
- `polymarket-long-short` — the long/short engine. Watches that snapshot and re-runs
  **right after every bot scan**, writing `outputs/polymarket/long_short_intents.csv`.

```bash
cp .env.example .env
docker compose -f docker-compose.monitor.yml up -d --build
docker compose -f docker-compose.monitor.yml logs -f          # both services
docker compose -f docker-compose.monitor.yml logs -f polymarket-long-short
```

The monitor compose forces dry-run on both services:

```env
PM_MODE=dry_run
POLYMARKET_EXECUTE_LIVE=false
```

For directional long/short signals the bot needs your fair probabilities in
`inputs/polymarket/model_probabilities.csv` (see `model_probabilities.example.csv`). With
no model file the engine still emits market-making quotes around the mid. Review both
`market_snapshot.csv` and `long_short_intents.csv` under `outputs/polymarket/`.

Tuning (optional `.env` values):

```env
POLYMARKET_SCAN_INTERVAL_SECONDS=5     # how often the bot rescans / rewrites the snapshot
LONG_SHORT_POLL_SECONDS=3              # how often the engine checks for a new snapshot
LONG_SHORT_MAX_LIVE_ORDERS=3          # safety cap on live maker orders per engine run
```

## Going live with market-making

The dry-run monitor never trades. To let the long/short engine place **passive maker bids**
(market-making only; directional stays paper), the host must be in a non-geoblocked region
(this rules out the US) and you must:

1. Build with the Polymarket SDK and run the live agent image:
   `INSTALL_POLYMARKET_SDK=true docker compose up -d --build` (uses `docker-compose.yml`).
2. Set `PM_MODE=live`, `POLYMARKET_EXECUTE_LIVE=true`, and provide `POLYMARKET_PRIVATE_KEY`
   (+ CLOB creds) in `.env`.
3. Keep `POLYMARKET_MAX_ORDER_USD` small (default 5) and review the first fills against your
   wallet before increasing size.

If any guard fails (wrong region, missing key, geoblock), the engine reports `live_error`
and places nothing. See `docs/POLYMARKET_MISPRICING_BOT.md` for the full long/short section.
