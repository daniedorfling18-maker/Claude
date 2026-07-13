# Operator micro-drill runbook (live wallet, ~$5 total)

Status: operator runbook (human-executed). Registered 2026-07-13 after the
first funded deposit ($12.92 pUSD). The system stays read-only throughout:
it cannot place, amend, cancel, or sign anything. Every order below is
placed and cancelled BY THE OPERATOR in the Polymarket UI.

## Purpose — and what this is NOT

Operational identification only: venue ack, fill capture, cancel behavior,
monitoring pickup, reconciliation closure, true round-trip cost, settlement
and redemption mechanics. Evidence class: live operational. The P&L of
these drills is irrelevant by design and MUST NOT be cited as edge
evidence for any hypothesis in docs/EXPERIMENT_REGISTRY.md. Amounts are
below reward-program minimum quote sizes, so no reward-share claims arise.

## Preconditions

- Wallet reconciliation shows the expected pre-drill NAV.
- Pick a liquid, tight-spread, near-dated market (2026-07-13 ticket: the
  World Cup semifinal "Team to Advance" market — resolves next day, so the
  drill also exercises settlement).
- Note UTC time before every action; screenshot every state change.

## Drill A — rest and cancel (~$2, no fill expected)

1. Limit BUY, ~$2 notional, priced 5–10 cents BELOW the current best bid
   (deliberately unmarketable). Record: submit time, ack time, order id.
2. Leave it resting 3–5 minutes. Screenshot the open order.
3. CANCEL. Record cancel time and confirmation.
- Validates: order entry, ack latency, resting-order visibility, cancel
  path, and that NO fill occurs at an unmarketable price.

## Drill B — fill, hold to resolution (~$2–3)

1. Limit BUY at (or one tick through) the best ask so it fills immediately.
   Record: submit time, fill time, fill price, size, fee shown.
2. Hold through market resolution (next day for the ticketed market).
3. After resolution: record settlement outcome and redeem; record redeemed
   amount and any gas/fee.
- Validates: marketable fill capture, data-api activity row, scoreboard
  pickup, NAV movement, settlement stamp, redemption mechanics.

## Drill C (optional) — round-trip cost (~$2)

1. Marketable BUY as in B; then immediately marketable SELL to close.
2. Record both prints; the loss IS the measurement (spread + fees).
- Validates: exit mechanics; feeds the true-cost ledger with a measured
  round-trip cost at minimum size.

## Verification checklist (system side, after each drill)

| Evidence | Artifact | When |
|---|---|---|
| Fill appears in venue activity | data-api `/activity` for the wallet | minutes |
| Scoreboard row | `outputs/maker_carry/maker_live_test.json` + history CSV | next trade_prints cycle (~30 min) |
| NAV moves by fill amount ± fee | WO-62 `wallet_reconciliation.json` three-way | next harvest (daily) |
| Cost entry | WO-63 cost ledger | next harvest |
| Ledger anchoring | WO-61 chain verification stays `ok` | next anchor push |
| Settlement stamp | resolution captured for the held position | after market resolves |

Discrepancies are human-review alerts, not gate inputs. Record every
mismatch verbatim; a reconciliation that closes to the cent is the pass.

## Hard limits

- Total drill exposure across A+B+C: ≤ $8 of the $12.92 balance.
- No orders on markets with < $100k daily volume or spread > 2 cents.
- No repetition to "get a better result" — one pass per drill, results
  logged as they land. A failed drill is a finding, not a retry loop.
- This runbook never authorizes automation. Any automated order path
  remains blocked behind WO-67 P1–P5, including the dated owner amendment.
