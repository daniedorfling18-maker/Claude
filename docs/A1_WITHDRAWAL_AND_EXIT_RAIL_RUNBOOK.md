# Amendment A1 withdrawal and exit-rail runbook

Status: registered human runbook under WO-81. The first completed withdrawal
using this procedure is the exit-rail test required by custody Amendment A1.
The route is Polymarket → Solana → VALR → ZAR. The system only calculates and
records advice; it cannot initiate, sign, approve, or submit any leg.

## Hard stops before starting

1. Use only the single project account named by Amendment A1.
2. Confirm `outputs/execution/a1_sweep_advisory.json` says
   `status=sweep_advised` and note its generated time and suggested amount.
3. Confirm WO-62 reconciliation is fresh and `clean` or `explained`. Stop on
   `partial`, `DISCREPANCY`, stale evidence, or three NAV views that disagree.
4. Confirm no open order reserves the amount to be withdrawn. During any future
   executor era, first halt the executor and verify zero open orders; human and
   executor windows may never overlap.
5. In the current Polymarket withdrawal UI/bridge, verify Solana USDC is still
   a supported destination and inspect the live quote, minimum, fee, and amount
   received. Never rely on a remembered contract or bridge address.
6. In the signed-in VALR deposit screen, create or re-check the USDC deposit
   address and confirm the network is exactly Solana. Address and network must
   agree in both interfaces. Stop if VALR shows another network, a memo/tag you
   cannot supply, maintenance, or a changed minimum.

## Human withdrawal procedure

1. Record UTC start time, pre-withdrawal Polymarket NAV/cash, quoted amount,
   quoted destination amount, and every displayed fee. Screenshot the quote.
2. For the first exit-rail test, send the smallest amount that satisfies both
   current venue minima. Never use the full balance as the test transaction.
3. Paste the current VALR Solana USDC deposit address into Polymarket's
   withdrawal flow. Compare the first and last six characters with the VALR
   screen, confirm **Solana** again, then submit manually.
4. Record the Polymarket withdrawal/bridge reference and Solana transaction
   signature. Wait for Polymarket/bridge completion and network confirmation;
   do not repeat a pending transfer.
5. Confirm the USDC credit in VALR. Record UTC credit time and exact credited
   quantity. A missing or short credit is a stop-and-reconcile event, not a
   reason to send another transfer.
6. In VALR, convert only the credited project USDC to ZAR using the operator's
   chosen order type. Record execution price, USDC sold, ZAR proceeds, explicit
   fee, and UTC fill time. Do not mix unrelated account balances into the
   measurement.
7. Re-run WO-62 reconciliation. The withdrawal must appear as an explained
   external flow and the remaining project-account NAV must be at or below the
   active stage cap, subject only to normal mark movement.

## Door-to-door cost calculation and WO-63 entries

Calculate every leg from observed values, not advertised fee schedules:

- bridge/network cost = Polymarket value removed minus USDC credited at VALR,
  valued in USD at the recorded transfer-time rate;
- VALR trading fee = explicit exchange fee;
- FX/slippage cost = difference between the recorded independent USD/ZAR
  reference value and actual ZAR proceeds, if separately measurable;
- any investor-paid Polygon or Solana gas = actual transaction cost. Never book
  relayer-paid gas to the investor.

Append each positive cost separately with an idempotent reference. Example:

```bash
python -m polymarket_predictive_engine.cli add-cost \
  --config polymarket_predictive_config.example.yaml \
  --cost-date YYYY-MM-DD \
  --cost-category rail \
  --cost-usd 1.23 \
  --cost-ref exit-rail:YYYY-MM-DD:bridge-or-transaction-reference \
  --cost-note "A1 exit-rail test: Polymarket -> Solana -> VALR -> ZAR; measured leg"
```

Replace `1.23` with the measured positive USD cost. Use one unique `cost_ref`
per leg; the WO-63 ledger rejects duplicates. If a leg has exactly zero cost,
record that fact in the stage operator log rather than inventing a positive
ledger row. Run `sync-cost-ledger` afterward and verify the entries appear in
`outputs/performance/cost_ledger.csv` and its summary before the next anchor.

## Exit-rail test pass/fail record

The first run passes only when all of the following are present:

- Polymarket/bridge reference and Solana transaction signature;
- exact sent and VALR-credited USDC, with UTC times;
- exact ZAR proceeds and VALR fee;
- door-to-door elapsed time and all measured costs in WO-63;
- post-withdrawal WO-62 reconciliation clean/explained;
- WO-61 ledger-chain verification remains valid after anchoring.

Any wrong-network warning, unexplained amount difference, missing credit,
reconciliation discrepancy, duplicate submission, or unbooked cost is a failed
test. Stop, preserve evidence, and do not retry until the cause is understood.
