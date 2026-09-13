# Sanitized recorded API fixtures

These payloads were captured from Polymarket's unauthenticated public endpoints on 2026-07-15 and 2026-07-16.
They preserve the real response nesting, field names, timestamp units, and scalar types for parser
regression tests. Sanitization replaces public wallet, token, condition, transaction, profile, and
URL identifiers with deterministic inert values; it does not reshape payloads or change types. No
credentials, private account data, or request headers are stored.

- `data_api_activity_2026-07-15.json`: Data API `GET /activity` list, including the recorded
  seconds timestamp and activity-type shape.
- `data_api_positions_2026-07-15.json`: Data API `GET /positions` list.
- `data_api_trades_2026-07-15.json`: Data API `GET /trades` list with a seconds timestamp.
- `data_api_holders_2026-07-16.json`: Data API `GET /holders` list of token groups, preserving
  the nested `holders`, `proxyWallet`, `asset`, `amount`, and outcome-index shape.
- `data_api_oi_2026-07-16.json`: Data API `GET /oi` list, preserving the recorded `market` and
  numeric `value` fields; only the public condition identifier was replaced.
- `clob_book_2026-07-15.json`: CLOB `GET /book` object with a millisecond timestamp string.
- `clob_books_2026-07-15.json`: CLOB `POST /books` list envelope.
- `clob_prices_history_2026-07-15.json`: CLOB `GET /prices-history` object; five ordered history
  observations are retained and the public token/query parameters are omitted.
- `gamma_markets_2026-07-15.json`: Gamma `GET /markets` list, including JSON-encoded outcome and
  token arrays, ISO timestamps, nested event metadata, and the live reward-object shape.
- `gamma_negrisk_event_2026-07-16.json`: Gamma `GET /events` object recorded from a five-leg
  negRisk event, preserving the event envelope, JSON-encoded token pairs, fee-enabled booleans,
  null category values, and grouped outcome titles; public token identifiers were replaced.
- `vps_discovery_starvation_2026-07-16.json`: sanitized governance telemetry from the paper VPS
  showing the former frozen up/down rotation and the adaptive-query ordering that starved the
  registered H1/H2/H3 research lanes. No credentials, account identifiers, or market token IDs
  are present.

## Public archive and Deribit history fixtures (WO-166, captured 2026-09-12)

These are verbatim, keyless, public-history payloads used by `premium_research`'s parser tests. They
contain no identifiers to sanitise: prices, funding rates and timestamps only. **Capture disclosure:**
they were fetched from `data.binance.vision` and `www.deribit.com` on 2026-09-12 from an agent sandbox
before any `AGENTS.md` amendment permitted that contact; WO-166 records the capture and enumerates
these four files as the only fetched payloads written outside `research/`.

- `binance_vision_fundingRate_BTCUSDT_2024-01.csv`: the CSV member of
  `data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2024-01.zip` from
  `data.binance.vision`, verbatim (header + 93 rows; some `calc_time` values carry a 1 ms jitter).
- `binance_vision_klines_BTCUSDT_1h_2024-01_head.csv`: the header and first 48 rows of the CSV member
  of `data/futures/um/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2024-01.zip`. Older months of the same
  series ship without a header, which the loader also accepts.
- `deribit_funding_history_BTC_2026-09-12.json`: one full page (744 hourly rows) of
  `public/get_funding_rate_history` for `BTC-PERPETUAL`, span 2024-01-01T00:00Z to 2024-02-01T00:00Z,
  preserving the `interest_8h`, `interest_1h`, `index_price` and `prev_index_price` fields.
- `deribit_dvol_BTC_2026-09-12.json`: one page (92 daily candles) of
  `public/get_volatility_index_data` for BTC at `resolution=86400`, span 2024-01-01 to 2024-04-01,
  preserving the `[timestamp, open, high, low, close]` row shape and the `continuation` field.

## Governance ledger snapshot (WO-169, committed 2026-09-13)

- `closing_line_final_history_2026-08-21.csv`: the append-only closing-line ledger as it stood at
  the last telemetry push before the VPS outage, taken verbatim from
  `origin/vps-telemetry:telemetry/outputs/polymarket_model_governance/closing_line_final_history.csv`
  at commit `fcebaa2` (snapshot `2026-08-21T02:00:09Z`), 90 rows,
  sha256 `4b66d07f1050125dbe01d39220fafc1261929291090b9e565abba4d6b4b17b33`.
  **Identifier-retention departure.** Unlike every fixture above, its `market_id` values (0x-prefixed
  64-hex condition ids) and `token_id` values (public on-chain token ids) are retained verbatim rather
  than replaced with inert values. Byte identity with `fcebaa2` is the provenance proof for the
  WO-169 correction, and the market and fixture clustering must reproduce the recorded 55 independent
  units; substituted identifiers would change the clustering and break both. Every retained value is
  public on-chain or public-API data. No credentials, wallet keys, account identifiers, request
  headers or `.env` values are present, and `credential_guard._scan_csv` (which exempts
  public-identifier keys) returns no finding — asserted by
  `tests/polymarket_predictive_engine/test_verdict_reconcile.py`.
