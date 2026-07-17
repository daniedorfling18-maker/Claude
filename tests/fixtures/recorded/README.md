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
- `gamma_public_search_2026-07-16.json`: Gamma `GET /public-search` response captured on
  2026-07-16 for `world cup winner`; the event/pagination envelope, JSON-encoded market arrays,
  booleans, prices, and team labels are retained while public IDs and token IDs are inert.
- `the_odds_api_sports_2026-07-16.json`: The Odds API `GET /v4/sports` catalogue captured on
  2026-07-16 through the zero-credit endpoint. The API key and response headers are omitted.
- `the_odds_api_events_2026-07-13.json`: one event from the VPS's recorded The Odds API World Cup
  response, retaining the Pinnacle h2h/totals nesting, decimal odds, and provider timestamps;
  only the public event ID was replaced.
- `coingecko_simple_price_2026-07-16.json`: CoinGecko `GET /simple/price` response for POL/USD,
  preserving the numeric quote and seconds timestamp.
- `deribit_book_summary_2026-07-16.json`: Deribit public option book-summary envelope, trimmed to
  three same-expiry BTC calls while preserving instrument syntax, quote units, microsecond RPC
  timing fields, and scalar types.
- `binance_klines_2026-07-16.json`: Binance public `GET /api/v3/klines` row with millisecond open
  and close times and string-valued OHLCV fields.
- `coinbase_candles_2026-07-16.json`: Coinbase Exchange public candles row with seconds timestamp
  and its recorded `[time, low, high, open, close, volume]` numeric ordering.
- `data_api_leaderboard_2026-07-16.json`: Data API `GET /v1/leaderboard` rows, preserving string
  ranks, public-wallet field name, numeric P&L/volume, and badge/image fields; identities are inert.
- `data_api_value_2026-07-16.json`: Data API `GET /value` list preserving the `user` plus numeric
  `value` shape; the public wallet is inert.
- `official_contracts_excerpt_2026-07-16.html`: trimmed HTML recorded from Polymarket's official
  contracts page; contract labels and table structure are retained and addresses are inert.
- `polygon_rpc_chain_id_2026-07-16.json`: public Polygon JSON-RPC `eth_chainId` response recorded
  from an unauthenticated endpoint; request ID and hex result types are retained.
- `clob_websocket_book_2026-07-16.json`: one live CLOB market-channel capture retaining the outer
  collector envelope and JSON-string message, millisecond timestamp string, and string book levels;
  market/token/hash identifiers are inert.
- `vps_discovery_starvation_2026-07-16.json`: sanitized governance telemetry from the paper VPS
  showing the former frozen up/down rotation and the adaptive-query ordering that starved the
  registered H1/H2/H3 research lanes. No credentials, account identifiers, or market token IDs
  are present.
