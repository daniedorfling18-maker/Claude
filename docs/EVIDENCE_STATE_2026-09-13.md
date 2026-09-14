# State of the evidence — recorded 2026-09-13

These are readings of the 2026-08-21 snapshot, not current state. The VPS has
been offline since 2026-08-21, so every artifact below is the last one the host
wrote, read from the telemetry mirror `origin/vps-telemetry` at `fcebaa2` or
from a file committed to this repository. Current state is not recorded here and
never will be: it lives only in the generated files
`outputs/performance/operating_state.md` and
`outputs/performance/operating_state.json`, which are produced on the VPS from
runtime evidence.

This is a dated record, not a front door. It is not a statement that any lane is
open, and it authorises nothing.

Two of the six class words name registered things: `modeled` is a registered
evidence class, and `historical-class diagnostic` is a registered class
qualifier. The other four — `unread`, `untested`, `terminal` and
`existence observed` — are reading states, not registered classes. None of them
promotes a lane.

Three rows are written under a close-out guard. Their work orders' results were
merged to `main` by #455 and are recorded in the charter and the register, but
#455's required check has not executed, so no figure from them is printed here.
The same caveat reaches WO-166, whose merge #454 had its required check
cancelled after a day in the queue: it too was never verified, and the rows that
name it say so rather than resting on its longer tenure on `main`.

| line | evidence class | reading | what it rests on | where recorded |
|---|---|---|---|---|
| `H1 sharp-anchor maker carry` | `modeled` | `insufficient_evidence`, with the three gate states as the artifact writes them: M-A `pending`, M-B `pending`, M-C `pass_by_construction`; modelled net carry +$1.68/day against the $3.33/day target | A simulation, not a measurement: its adverse-selection charge rests on 3 replay-confirmed hypothetical fills, with 77.5% of opportunities lacking contemporaneous book state; realized wallet rewards are $0; capacity is bounded by the sizing model, not measured (WO-173, merged at #455, and not verified until that pull request's required gate runs) | `maker_carry_study.json` (`maker_gates.maker_verdict`, `portfolio_net_carry_usd_per_day`, `target_net_usd_per_day`), `maker_fill_replay.json` (`confirmed_fills`, `no_contemporaneous_state_rate`), `maker_live_test.json` (`rewards_usd_total`) |
| `H2 dutch-book` | `unread` | No deviation was found in the 2026-08-21 scan, which is **not** the verdict: `events_scanned = 300` with `flagged_deviations = 0`, and `groups_with_complete_ask_side = 67` with `flagged_deviations = 0` and `max_executable_basket_usd = 0.0` | `docs/VPS_OUTAGE_2026-08-21.md` records that `outputs/h2_dutch/h2_evaluation.json` is the only artifact permitted to state H2's verdict, and that a zero-flag scan reading is an inference from the scan rather than the verdict. That file has not been read since the host went offline, which is why the class is a reading state and not a result | `implication_scan.json`, `event_group_scan.json`, and `docs/VPS_OUTAGE_2026-08-21.md` |
| `H3 smart-flow` | `untested` | `fills_seen = 0`, last generated 2026-07-17. An ingestion failure, not a negative result: the lane was never given the input it grades | In the charter's recorded words, "its input was never collected and its job stopped running" | `smart_flow_clv.json` and `docs/POLYMARKET_QUANT_MODE_CHARTER.md` |
| `The legacy $100/month verdict engine` | `terminal` | Terminal `no_for_tested_edge_classes` on the registered clock, which expired 2026-08-19 | Its binding Gate A metric is a per-share price difference labelled per dollar (−0.013943 on 55 units, `unit_mean_net_settlement_return_per_dollar`). The corrected per-dollar reading is WO-169: recorded in the charter and the register by #455, and not verification of record until that pull request's required gate runs, and no figure from it is printed on this row | `profit_verdict.json` and `docs/POLYMARKET_QUANT_MODE_CHARTER.md` |
| `Perpetual funding carry` | `historical-class diagnostic` | Crypto, and not verification of record. WO-166 returned a NO-GO on its completeness rule — the reading of the three that has been on `main` longest, and the only one merged before #455, by #454, whose required check was cancelled without executing, so it is no more verification of record than the other two | WO-167's refined completeness rule: recorded in the charter and the register by #455, and not verification of record until that pull request's required gate runs. WO-170's corrected pass: recorded in the charter and the register by #455, and not verification of record until that pull request's required gate runs. Neither WO-167 nor WO-170 prints a figure on this row | `docs/POLYMARKET_QUANT_MODE_CHARTER.md` and `docs/POLYMARKET_CODEX_WORK_ORDERS.md` |
| `Variance risk premium` | `existence observed` | Observed in the merged, charter-recorded WO-166 artifact, whose merge #454 had its required check cancelled without executing; parked by decision, with no tradeable claim attached to it | WO-170's realignment of that lane: recorded in the charter and the register by #455, and not verification of record until that pull request's required gate runs, and no figure from it is printed on this row | `research/premium_poc/results/vrp.json` and `docs/POLYMARKET_QUANT_MODE_CHARTER.md` |

Nothing here authorises paper trading, live trading, or capital.
`paper_trading_invoked=false`, `live_trading_invoked=false`.
