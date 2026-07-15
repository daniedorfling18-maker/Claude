# Sanitized recorded API fixtures

These payloads were captured from Polymarket's unauthenticated public endpoints on 2026-07-15.
They preserve the real response nesting, field names, timestamp units, and scalar types for parser
regression tests. Sanitization replaces public wallet, token, condition, transaction, profile, and
URL identifiers with deterministic inert values; it does not reshape payloads or change types. No
credentials, private account data, or request headers are stored.

- `data_api_activity_2026-07-15.json`: Data API `GET /activity` list, including the recorded
  seconds timestamp and activity-type shape.
- `data_api_positions_2026-07-15.json`: Data API `GET /positions` list.
- `data_api_trades_2026-07-15.json`: Data API `GET /trades` list with a seconds timestamp.
- `clob_book_2026-07-15.json`: CLOB `GET /book` object with a millisecond timestamp string.
- `clob_books_2026-07-15.json`: CLOB `POST /books` list envelope.
- `clob_prices_history_2026-07-15.json`: CLOB `GET /prices-history` object; five ordered history
  observations are retained and the public token/query parameters are omitted.
- `gamma_markets_2026-07-15.json`: Gamma `GET /markets` list, including JSON-encoded outcome and
  token arrays, ISO timestamps, nested event metadata, and the live reward-object shape.
