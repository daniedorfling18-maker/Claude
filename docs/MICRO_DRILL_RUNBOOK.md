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

## Drill log — 2026-07-13 (operator-executed)

Market used: "Will France win on 2026-07-14?" (WC semifinal moneyline,
$7.4M event volume, 0.25c spread). Wallet balance at start: $12.923349.

**Drill A — PASS.** Limit buy Yes 5 shares @ 36c (5.25c below the 41.25c
ask). Instant ack; rested 0/5 filled with $1.80 reserved "Until
cancelled"; visible only as aggregate depth at the 0.36 level in the
public book; cancelled cleanly; funds restored in full.

**Drill B — PASS.** Limit buy Yes @ 42c filled instantly at the book ask
0.4125 (price improvement confirmed venue-side). data-api reported
`TRADE BUY 5.14 @ 0.4125`, usdcSize $2.18253. On-chain pUSD moved
$12.923349 -> $10.740819 — exact to the cent against the venue debit.
Position held to resolution per plan; settlement leg completed
2026-07-14 (see settlement addendum below Drill D).

**Findings (all at $2 instead of $100):**
1. Near-identical market variants sit side by side (regulation win vs
   team-to-advance, ~18c apart); the order panel does not warn when a
   "limit" order crosses the spread. Ticket discipline must name the
   exact market question, not the fixture.
2. "Available to trade" does not visibly deduct reservations for open
   limit orders; displayed buying power can overstate free cash.
3. First real taker-fee print: $0.06228 on $2.12025 notional (~2.9%),
   consistent with the modeled 2.8% mean taker fee in Gate B.
4. Share sizing: UI executed 5.14 shares against a 5-share ticket.
   ROOT CAUSE identified 2026-07-13 from the operator's Trading
   settings: "Exact shares on limit buys" is ON — the UI upsizes the
   order so the NET shares after fees equal the ticket (5.14 x ~2.9%
   fee ~= 0.14). Executor note: the API path sizes orders directly, so
   WO-67 must do its own fee-aware sizing; this UI convenience will not
   exist there.
   Settlement addendum: the operator account has AUTO-REDEEM WINS
   enabled (Trading settings, confirmed 2026-07-13), so redemption is
   automatic at close; WO-67 needs no redemption logic. Signed custody
   Amendment A1 later selected this same project account for any future
   non-overlapping executor window, so no second account is created.
5. Resting orders are NOT attributable from public data (aggregate book
   depth only); fills ARE (per-wallet trade rows within seconds). This
   confirms the fill-based scoreboard design and means requote
   compliance can never be externally verified per-order.

Operator-side legs all complete as of 2026-07-14 (settlement addendum
below Drill D). Scoreboard pickup CONFIRMED (data-api activity_rows
visible to the reconciliation on 2026-07-13). Still outstanding on the
system side: the automated harvest reconciliation + settlement stamp,
gated on harvest completion (WO-85) and the WO-84 data_api fix being
deployed.

**Drill D — exit mechanics (added 2026-07-13, PASS).** Limit SELL 5
shares @ 40c filled at the 41c bid (price improvement symmetric).
data-api: `TRADE SELL 5 @ 0.41`, usdcSize $1.98953 -> sell fee $0.06047
(~2.9%, symmetric with the buy side). On-chain closed exact:
12.923349 - 2.18253 + 1.98953 = $12.730349 = UI cash to six decimals.
Partial-position sell handled cleanly; position row updated to the
0.14-share tail immediately. Measured round-trip cost at minimum size
(unplanned Drill C equivalent): ~13.5c on ~$2.10 notional (~6.4%),
dominated by the symmetric ~2.9% fees.

**Drill B settlement leg — COMPLETE 2026-07-14 (PASS).** Spain won the
semifinal, so "Will France win on 2026-07-14?" resolved NO and the
remaining 0.14-share Yes tail settled to zero — the pre-budgeted
~$0.057 loss, exactly the "either outcome completes the test" design.
Verified across all three views post-settlement: data-api positions
shows no open positions, data-api /value reports $0, and on-chain pUSD
balanceOf is $12.730349 — unchanged from the post-Drill-D cash figure,
correct because a losing position redeems nothing (auto-redeem had
nothing to claim, and no gas/fee was charged). Final drill ledger:
12.923349 − 2.18253 (buy) + 1.98953 (sell) − 0.057 (settled tail)
≈ 12.730349 cash + $0 positions. Total cost of the full drill program
A/B/D ≈ $0.193 (fees $0.123 + spread ~$0.013 + zeroed tail ~$0.057).
The complete order lifecycle is now exercised end to end at minimum
size: ack → rest → cancel → marketable fill → partial exit →
settlement-to-zero, with UI, data-api, and on-chain agreeing to six
decimals at every checkpoint. The automated settlement stamp in the
harvest remains to be observed once WO-85 restores harvest completion;
that is a system-verification item, not an operator drill leg.

**Drill E — reward-accrual pipe test (REGISTERED 2026-07-13, runs only
on explicit owner go; NOT yet run).** Purpose: the H1 revenue mechanism
(reward payments) has never been observed — `paid_rewards_usd` has only
ever read zero. Ticket: single-sided resting bid, 20 shares at one tick
below best bid on the CHEAP side of a band-eligible rewarded market
(2026-07-13 candidate: "Iran full airspace closure by August 31?",
~33c, ~$6.60 reserved), held across a UTC day boundary, then cancelled.
Before owner go, the ticket must use the venue's published reward terms
and minute-sampling formula to show expected eligible reward of at least
$1.00 for the registered hold window. If no ticket can clear that payout
floor inside the separate $7 exposure cap, Drill E is
`BLOCKED_NO_FLOOR_CLEARING_TICKET` and must not run. PASS = after a
floor-clearing hold, a nonzero reward payment appears in data-api
activity and flows to the scoreboard and cost ledger. A zero payment on
a sub-floor hold is INCONCLUSIVE, not a pipeline failure. Registered risk: headline market;
worst case the bid is filled on news and the position marks against us
(max $6.60, realistically cents of markout; exit next day in one
5-share-multiple lot). This drill's exposure is registered SEPARATELY
from the A-D $8 aggregate, with its own cap of $7.

**Additional findings:**
6. Venue MINIMUM 5 SHARES for limit orders, buys and sells alike.
   Consequences for the funded stage: kill-criteria flattening happens
   in >=5-share chunks; position tails under 5 shares cannot be
   limit-sold and must ride to resolution; plan sizes in multiples of
   5 shares to avoid stranded tails.
7. The venue clears the entire orderbook at event start ("orderbook
   will clear at 21:00 GMT+2"): resting maker quotes on sports markets
   die automatically at kickoff — no manual pull needed at event
   start, and no reward accrual after it.
