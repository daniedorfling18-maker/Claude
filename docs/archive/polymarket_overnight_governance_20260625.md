# Polymarket Overnight Governance Handoff - 2026-06-25

## Context

An overnight local Polymarket data collection run was executed. The collection itself worked, but the loop was unsafe because it continued into label-building and feature-building even though there were no clean resolved labels.

The bad/premature generated artefacts were quarantined locally and must not be used for training.

## Useful outputs from overnight

These outputs are useful for inspection only:

- outputs/polymarket_websocket/websocket_messages.json
- outputs/polymarket_training/websocket_market_features.csv
- outputs/polymarket_training/websocket_resolutions.csv
- outputs/polymarket_training/pseudo_label_candidates.csv
- outputs/polymarket_training/clean_resolved_snapshot_labels.csv
- outputs/polymarket_model_governance/snapshot_market_current_status.csv
- outputs/polymarket_model_governance/websocket_feature_quality_report.csv
- outputs/polymarket_model_governance/websocket_resolution_quality_report.csv

Observed overnight state:

- websocket messages: 26,882
- websocket feature rows: 45,020
- snapshot markets: 487
- Gamma found: 468
- Gamma not found: 19
- pseudo-near-terminal markets: 302
- pseudo label rows: 604
- clean resolved label markets: 0
- clean resolved label rows: 0

## Bad/premature artefacts quarantined locally

These were moved out of the active training folder and must not be used as model inputs:

- historical_resolutions.csv
- market_resolutions.csv
- labels.csv
- label_quality_report.csv
- features_v2.csv
- feature_dictionary_v2.csv
- features.csv
- feature_dictionary.csv
- historical_price_snapshots.csv
- pseudo_validation_features.csv

At least one quarantine folder exists locally under:

- outputs/polymarket_training/quarantine_bad_overnight_20260625_050850

## What went wrong

The overnight loop should have been collection-only. Instead, it attempted:

- build-labels
- build-features-v2

even though clean_resolved_snapshot_labels.csv had zero usable clean labels.

build-labels correctly failed with:

ERROR: No resolved outcome labels found.

But build-features-v2 still ran and produced a huge premature feature file before failing/interruption.

Backfill-resolved-markets also scanned very old closed markets, including 2022 markets. That is wrong for this live/current overnight workflow unless explicitly requested with a bounded historical research mode.

## Required repo fixes

Implement these before any further overnight model work:

1. Add a safe collection-only overnight command or script.
   - It should run:
     - collect-websocket
     - normalize-websocket
     - resolve-websocket-markets
     - collect-snapshot-labels
   - It must not run build-labels, build-features-v2, train-skill-model, paper-trade, or live-trade.

2. Add a hard guard to build-features-v2.
   - It must fail fast if clean resolved labels are missing or empty.
   - It must not produce features_v2.csv from unlabeled or pseudo-labeled data unless an explicit research-only flag is passed.

3. Add a hard guard to build-labels.
   - If no clean labels exist, it should exit cleanly with a clear message and no giant quality report.

4. Add safety to backfill-resolved-markets.
   - It should require a recent cutoff or max-age parameter by default.
   - Historical backfill older than the cutoff should require an explicit flag such as --allow-old-history or --research-historical.

5. Add readiness/promotion blockers.
   - Block if labels.csv is empty.
   - Block if clean_resolved_snapshot_labels.csv has zero rows.
   - Block if features_v2.csv exists but labels are empty.
   - Block if training artefacts were generated from pseudo labels.
   - Block paper trading unless clean label and market-relative validation gates pass.

6. Add tests.
   - Test that collection-only loop does not invoke training commands.
   - Test that build-features-v2 refuses to run with zero clean labels.
   - Test that old historical backfill requires explicit opt-in.
   - Test that readiness remains NOT_APPROVED when clean labels are missing.

## Correct future operating model

Overnight local run:

collect live data: yes
monitor current markets: yes
check current snapshot labels: yes
historical old-market backfill: no
build labels/features: only after clean labels exist
train model: only after clean labels exist
paper trade: only after readiness and promotion gate explicitly approve
live trade: disabled

## Claude Code instruction

Please inspect the current repo and implement the safety fixes above. Do not rely on local outputs as model inputs. Treat the overnight outputs as evidence only. The priority is to make the workflow safe, repeatable, and incapable of producing training artefacts without clean labels.
