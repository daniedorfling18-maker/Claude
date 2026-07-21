# Repository line audit — 2026-07-21

Source basis: accepted `main` through PR #354 at
`e98d398d8b5304221b17a482996ff24f43dd15cd`.

## Scope and limits

The audit reviewed the repository's tracked source, scripts, workflows,
configuration examples, tests and documentation, and reconciled their claims
against merged PR history through #354. The review was static: no engine,
collector, test suite, Docker service, dashboard or watchdog was run locally,
because `AGENTS.md` requires all such execution in an isolated VPS environment.

This report is not build authority, a funding decision, a deployment
attestation or proof of runtime health. Generated datasets and binary artifacts
were considered only as contracts/provenance, not revalidated as live state.
Funding remains CLOSED and WO-67 remains BLOCKED.

## P1 findings — fail-closed remediation required

1. **WO-112 sizing is looser with less evidence.**
   `live_test_decision_policy.py:461-538` returns full ladder exposure for zero
   or one finite net-history row before it checks whether at least two
   capital-normalized returns exist. Seven uncapitalized rows fail closed while
   one row can receive `ladder_cap`. The surface is frozen and needs an
   owner-routed correction plus zero/one-row regression tests.
2. **Malformed or future clocks can fabricate fresh evidence.**
   `live_test_decision_policy.py:254-318,599-659` accepts date substrings as UTC
   days, orders future observations as latest and clamps negative age to zero.
   Recurrence, streak, Kelly/ladder evidence and the stale-data kill can
   therefore fail open. Reject malformed/future-skew timestamps.
3. **Non-finite typed intent values can become positive paper size.**
   `execution/intents.py:53-79,182-199`, `utils.py:308-314` and
   `risk.py:254-267` allow NaN/Inf through comparisons; an invalid cap can be
   ignored and replaced by cash/base risk. Explicit zero caps also remain
   vulnerable to truthiness handling. Central finite validation and strict JSON
   encoding are required.
4. **Malformed cohort chronology can advance promotion time.**
   `cohort_validation.py:94-105,269-281,367-393` substitutes wall-clock now for
   absent/invalid/reversed evidence timestamps. Invalid evidence must remain
   zero/`UNKNOWN`, with fixed-clock tests.
5. **Unknown observation time is synthesized as current.**
   `snapshot_ingest.py:43-70`, `anchored_edge.py:342-383,491-535` and
   `price_action_signals.py:1630-1753` replace missing timestamps with now or
   emit current-stamped signals. That fabricates point-in-time training and
   anchor evidence; missing/future timestamps must exclude the row.
6. **Paper quote “independence” is circular.**
   `paper_broker.py:296-361,364-418,584-629,1360-1443` stores the broker's own
   execution quote as `paper_execution_quote_snapshot`, rereads it as the
   independent cross-check and can fall back to a broker-created timestamp.
   `paper_round_trip.py:145-198,731-860` can label the self-copy
   `proof_verified`, while `cohort_validation.py:108-165,333-393` accepts
   consistency without requiring verified independent proof. This can feed
   profit and promotion evidence. Execution audit snapshots must not satisfy
   immutable raw-observation proof.
7. **Dashboard privacy is not fail-closed.**
   `scripts/configure_polymarket_dashboard_tailscale.sh:110-167`, deploy
   workflow lines 529-531 and rollback lines 254-257 suppress Funnel-off
   failures. An existing Funnel can survive a failed configuration/readiness
   attempt. Funnel must be disabled and proved off before service recreation.
8. **Deploy acceptance can pass stale or incomplete state.**
   `scripts/check_polymarket_vps_paper.sh` and the acceptance path do not prove
   the exact deployed SHA, all four long-running services, artifact freshness or
   advancing scheduler values. Deploy/rollback must fail on any mismatch.
9. **Required SuperBru submission can succeed with no queued fixture.**
   `scripts/auto_pick_match_scoped.py` returns success under
   `--require-submission` when `queued_count <= 0`. Production callers in the
   watchdog and `auto_pick.yml` therefore cannot distinguish no submission from
   success.
10. **Locked-card job failures are mislabeled or unbounded.**
    `scripts/run_vps_ops_scheduler.sh` defines but does not apply
    `CARD_TIMEOUT`; quota preflight conflates missing key, authentication,
    network/header errors and zero quota, then records an intentional success.
    Skips can advance last-success state and log-rotation failures are hidden.

## P2 findings — correctness and reliability

- `backtest.py:10-35` joins current signals to resolved labels without an as-of
  relation, treats USDC stake as share quantity, omits fee/depth and labels any
  trades execution-aware/approved. It is diagnostic only.
- `maker_carry_study.py:1481-1612` counts replayed timestamps toward the
  100-snapshot gate, can raise on non-finite numeric input, accepts malformed day
  substrings and makes the exact switch-margin boundary input-order-dependent.
- Shared dashboard/state writers in `dashboard_proof_questions.py`,
  `operating_state.py`, `dashboard.py`, `decision_trace.py` and
  `pipeline_inventory.py` do not all meet the atomic-write contract.
- `decision_trace.py:44-55,227-257` reports an untimestamped payload as OK;
  `pipeline_health.py` hard-codes execution flags and `pipeline_inventory.py`
  infers capability with loose filename substrings. Those views are diagnostic,
  not safety state.
- Paper/backtest artifacts do not consistently emit the standard
  `paper_trading_invoked`/`live_trading_invoked` fields.
- SuperBru result ingestion treats missing match status as completed.
- SuperBru backtests can select odds observed after kickoff and reuse the same
  data for selection and reporting; their significance is exploratory.
- Explicitly missing SuperBru config paths silently fall back to defaults;
  boolean parsing treats nonempty strings such as `"false"` as true, validation
  lacks finite/range/enum checks, unknown strategy names fall through and the
  knockout field is unused.
- Watchdog state/log writes are not consistently atomic or bounded; missing
  credentials can make configured fallbacks unreachable, and a rematch can be
  suppressed when identity uses only the team pair.
- Dashboard healthchecks prove file existence/staleness more often than service
  liveness; three long-running services lack equivalent healthchecks, services
  inherit the full `.env`, and containers run as root.
- Deploy and manual VPS workflows use `ssh-keyscan` trust-on-first-use. The
  private repository bootstrap path is unauthenticated and can start production
  outside the guarded deploy.
- Seasonal examples formerly enabled the completed tournament. The committed
  example is now disabled, but runtime `.env` state must be checked separately.
- `credential_guard.py` checks primitive numeric values before secret-key names
  and broadly allowlists fields containing `token`, allowing some secret-shaped
  values to bypass intent.
- Provider fallbacks, telemetry locks/status, non-atomic writes and finite input
  validation need direct adversarial coverage across the ops/SuperBru surface.

## Coverage gaps

No dedicated direct test module was found for several active modules, including
`cohort_validation`, `decision_trace`, `pipeline_health`,
`pipeline_inventory`, `snapshot_ingest`, `collection_coverage`,
`external_feed_collector`, `family_calibration`, `overnight_collection`,
`owner_activity_attribution`, `promotion_review`, `snapshot_label_collector`
and `strategy_search`. Indirect coverage may exist; this inventory is a
prioritization signal, not proof that code is wholly untested.

PR #354 also retains three review follow-ups: the dashboard readiness sequence
can take roughly 210 seconds despite a 60-second description, scheduler log
rotation runs only at the top of its loop, and a disabled card-refresh status
can remain stale until the 12-hour job cadence.

## Documentation reconciliation performed

- Updated root/current-state/system-map references through PR #354 and the four
  canonical services.
- Replaced public dashboard instructions with private authenticated Tailscale
  Serve, while recording the unresolved Funnel fail-closed gap.
- Rewrote the VPS, automation, secrets and seasonal configuration guidance;
  disabled ended-tournament examples.
- Retired copyable live-order, alternate-Compose, local Windows and raw
  bootstrap instructions.
- Marked backtests, pipeline maps, calibration statistics and historical
  research evidence as diagnostic/non-authoritative where appropriate.
- Corrected work-order and governance provenance and recorded WO-111 through
  WO-114 without claiming unobserved day-after success.

## Required next action

Open separate, registered work orders for the findings above. Frozen/control
surfaces require the owner authorization and merge path in `AGENTS.md`. Each fix
needs focused adversarial tests plus the complete unfiltered suite in the
isolated ARM64/Python 3.11 VPS gate. Runtime health, exact deployment and
day-after evidence must then be verified from generated artifacts; this static
audit cannot supply them.
