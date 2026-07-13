# WO-73 append-only ledger schema incident

Date detected: 2026-07-13 UTC  
State: code corrected; production recovery must pass chain and archive verification

## Detection and impact

The first forced WO-65 archive after the public maker-test wallet activated the
24-hour pre-live RPO failed closed. WO-61 verification identified the first
broken anchor date as 2026-07-12 for exactly two files:

- `maker_carry/maker_live_test_history.csv`
- `performance/wallet_reconciliation_history.csv`

No order, cancellation, amendment, credential, gate, stake, or promotion path
was involved. The external `vps-anchor` manifests remained intact. The failure
prevented a new investor archive from being published, as designed.

## Root cause

WO-73 introduced separate operator/executor reporting and inserted
`wallet_role` (and, for the maker scoreboard, `wallet_address`) into the two
existing CSV schemas. Both paths were already registered as WO-61
`append_only` ledgers. Their writers used read-all plus atomic rewrite, so the
new header and blank values on historical rows changed previously anchored
bytes. This was a schema-migration defect, not an operator edit.

## Recovery provenance

VPS Git object
`5926e91f28a32561d92b2a92687c832996794a44` is the retained 2026-07-13
17:30 UTC telemetry snapshot immediately before the WO-73 deployment. Its two
files contain the complete pre-migration histories and independently match
both published anchors:

- maker history: 2026-07-12 prefix SHA-256
  `7aac8ba89ae0e3927b053cc796d324861fb5cc25c80686cc7212479ae8106fbf`
  at 571 bytes, and 2026-07-13 prefix SHA-256
  `ec760b36edc56faa86014ffd4192a0e455c650c3e56c15765b52214e21d9ce6d`
  at 6,238 bytes;
- reconciliation history: 2026-07-12 prefix SHA-256
  `371423ca4d45246651a260fd642b13fe353a60659b111e771fa4b35770d5dbdb`
  at 363 bytes, and 2026-07-13 prefix SHA-256
  `bc826b4471ed38c09db380a777cf0fe353770e11f2e7e3b5e42c6048ff6678c1`
  at 527 bytes.

Recovery must extract those exact blobs, append any later rows using only each
legacy schema, and atomically replace the two damaged runtime copies while
their writer services are stopped. Role-aware rows are retained in the new
versioned ledgers described below. Success requires `verify-ledger-chain` and
a forced `snapshot-ledger-archive` to pass before services are considered
fully recovered.

## Permanent correction

- `append_csv_rows` is a strict write primitive that never rewrites existing
  bytes and refuses a header mismatch.
- The two original paths retain their pre-WO-73 schemas and primary-wallet
  history for compatibility and anchor continuity.
- Role-aware rows write to new fixed-schema append-only paths:
  `maker_live_test_wallet_history.csv` and
  `wallet_reconciliation_wallet_history.csv`.
- Both new paths are enrolled in the WO-61 registry. Any future schema change
  must create another versioned path; an enrolled append-only header is
  immutable.

