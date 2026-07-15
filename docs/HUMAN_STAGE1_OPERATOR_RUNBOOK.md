# Human Stage-1 operator runbook (WO-82)

This runbook records a human-run maker experiment. It is not trading
authorisation. The generated page and CLI are read-only with respect to the
venue: they cannot place, amend, cancel, sign, or authenticate an order.

## Start of each UTC stage day

1. Generate the current page:

   ```bash
   python -m polymarket_predictive_engine.cli stage-day \
     --config polymarket_predictive_config.example.yaml
   ```

2. Open `outputs/execution/stage_days/YYYY-MM-DD.md` and stop if any of these
   are not usable evidence:
   - ticket state is not `EXACT_HUMAN_TICKETS`;
   - requote state is `pull_quotes_now`, `STOP`, or `UNKNOWN`;
   - a kill criterion is triggered or unknown;
   - yesterday's reconciliation is a discrepancy or unknown.
3. Confirm Amendment A1 sequencing: this is the human window, no executor
   window may overlap it, and only the single project account is used.
4. In Polymarket, open the named market from the page and compare the URL,
   outcome, bid, ask, and size to the exact ticket before entering anything.
5. Place any authorised human quote in the venue UI. The repository does not
   submit it. Immediately record each side that was actually entered:

   ```bash
   python -m polymarket_predictive_engine.cli stage-day \
     --config polymarket_predictive_config.example.yaml \
     --record-action quote_placed \
     --action-market-id CONDITION_ID \
     --action-side bid \
     --action-price 0.48 \
     --action-size-shares 100 \
     --action-note "venue order reference recorded offline"
   ```

Never put an API key, private key, secret, passphrase, session token, or login
detail in an operator note.

## Owner drill and maintenance attribution (WO-88)

Normal Stage-1 quote actions remain maker-test evidence. Use `drill_trade` or
`maintenance_trade` only for an owner action that must not be attributed to the
maker experiment, and record its actual UTC time, market, one side, price, and
size exactly:

```bash
python -m polymarket_predictive_engine.cli stage-day \
  --config polymarket_predictive_config.example.yaml \
  --record-action drill_trade \
  --action-at-utc 2026-07-15T06:01:00Z \
  --action-market-id CONDITION_ID \
  --action-side bid \
  --action-price 0.48 \
  --action-size-shares 5 \
  --action-note "owner micro-drill; no stage quotes live"
```

The exclusion is fail-safe and retrospective. The activity-feed fill must
match that row's market, BUY/bid or SELL/ask side, price, cumulative size, and
fixed five-minute time window. The row must also be covered by the latest
verified WO-61 byte-prefix anchor. Until then—or on any mismatch—the fill
continues to count against the maker-test fills alarm. Never label a normal
Stage-1 quote as drill or maintenance activity.

## During the day

- Re-open the generated page before every placement or replacement.
- Record `quote_repriced`, `size_changed`, and `quote_pulled` events as soon as
  the human completes them in the venue UI.
- A CLI log entry is evidence of a reported human action, not proof that the
  venue accepted it. Venue state, the public activity feed, reconciliation,
  and the anchored log are checked together.
- Keep the single project account at or near the active stage cap. Any future
  sweep advisory is executed manually through
  `docs/A1_WITHDRAWAL_AND_EXIT_RAIL_RUNBOOK.md`.

## Kill-criteria response — execute in this order

1. Stop entering or replacing quotes. Leave the generated stage page open as
   the evidence reference.
2. In Polymarket, open **Portfolio**, then **Open Orders**; choose **Cancel all**
   and confirm the venue prompt.
3. Refresh **Open Orders** and verify the count is zero. If an order remains,
   do not replace it; retry the venue cancellation and record the unresolved
   order ID in a credential-free note.
4. Open **Positions** and record every remaining inventory position. Do not
   improvise a taker exit outside the registered policy.
5. Record `kill_acknowledged` and each `quote_pulled` action, including UTC
   time and market ID:

   ```bash
   python -m polymarket_predictive_engine.cli stage-day \
     --config polymarket_predictive_config.example.yaml \
     --record-action kill_acknowledged \
     --action-note "trigger name and venue open-order count after cancel-all"
   ```

6. Re-run the page and capture the resulting requote, reconciliation, and cost
   evidence. Resume only after a documented human review clears the registered
   trigger; elapsed time by itself never clears it.

## End of day

1. Record a final `inventory_observed` or `no_action` row so silence cannot be
   mistaken for a missing log.
2. Re-run `stage-day`; verify the action table contains every action taken.
3. Let the daily harvest run `anchor-ledgers` after the page. The append-only
   `outputs/execution/stage_operator_log.csv` is enrolled in the WO-61 prefix
   hash chain and is the P2 evidence baseline for any later canary comparison.
4. Review the next day's reconciliation before starting another quote window.

## Amendment A1 controls

- Human Stage 1 completes before any future executor canary begins.
- Human and executor trading never overlap on the single project account.
- Excess balance is swept manually to VALR under the registered $5 threshold
  and `docs/A1_WITHDRAWAL_AND_EXIT_RAIL_RUNBOOK.md`; no repository component
  moves funds.
- A credential compromise means revoke, withdraw the remainder, and abandon
  the account as specified in `docs/KEY_CUSTODY_DESIGN_WO67_P5.md`.
