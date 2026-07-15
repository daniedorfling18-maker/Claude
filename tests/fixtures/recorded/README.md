# Sanitized recorded API fixtures

These payloads preserve real public endpoint field names and scalar types for parser regression
tests. They contain no credentials, wallet addresses, account identifiers, or private data.

- `clob_prices_history_2026-07-15.json`: captured 2026-07-15 from the public Polymarket CLOB
  `GET /prices-history` endpoint. Sanitization retained five ordered observations from the returned
  history and omitted the public token/query parameters; the response objects themselves are
  otherwise unchanged.

