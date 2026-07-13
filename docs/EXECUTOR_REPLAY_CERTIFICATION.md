# Executor replay certification (WO-74)

WO-74 is an executor-independent acceptance harness. It certifies recorded
decisions; it cannot place, amend, cancel, sign, or authenticate an order.

## Inputs and contract

The generated scenario corpus combines eligible WO-44 official-book windows
with synthetic event-start clears, the 5-share minimum, sub-5-share tails,
crossed spreads, news gaps, stale websocket input, heartbeat gaps, and a
kill-criteria day. Certification requires exactly one replay decision row for
every corpus cycle and exactly one action-ledger row for every logged action.

The unchanged contract checks that a candidate:

1. places quotes only at the exact registered scenario ticket;
2. remains inside the current decision-policy capital cap;
3. uses positive sizes in multiples of 5 shares;
4. cancels all open orders within one cycle of `pull_quotes_now` or `STOP`;
5. ends flat on missing or stale inputs;
6. triggers and logs a flat state after a heartbeat gap; and
7. appends every action exactly once to the candidate execution ledger.

The dated result is `outputs/execution/replay_certification.json`. A FAIL is a
registered canary blocker. A PASS is only one prerequisite and never grants
canary or live authority.

## Pre-amendment reference run

On the VPS, after WO-44 official-book files exist:

```bash
python -m polymarket_predictive_engine.cli executor-replay-certification \
  --config polymarket_predictive_config.example.yaml \
  --generate-replay-stub \
  --candidate-build-id wo74-reference-stub
```

The generated log is explicitly labelled `reference_stub`; it proves the
harness only. It uses no real executor and loads no credential. After the
owner amendment, a candidate executor must emit the same JSON/CSV contract and
pass this harness unchanged using `--executor-decision-log`,
`--executor-ledger`, and `--candidate-build-id`.

