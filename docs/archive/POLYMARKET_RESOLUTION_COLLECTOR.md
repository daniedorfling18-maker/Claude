# Polymarket Resolution Collector

`polymarket-engine collect-resolutions` reads unique market slugs from raw point-in-time snapshots and enriches them with Gamma market metadata. It writes token-level rows to `outputs/polymarket_training/market_resolutions.csv` and a quality file to `outputs/polymarket_model_governance/resolution_quality_report.csv`.

The collector is intentionally conservative. It only assigns labels when `closed=true`, outcomes and token IDs map one-to-one, and the settlement vector has exactly one near-one outcome and all other outcomes near zero. Active markets, all-zero legacy vectors, ambiguous vectors, and non-binary markets are written as metadata but remain unlabelled.

Final settlement fields are never written back into raw snapshot files. The label builder joins raw snapshots to this separate resolution file when creating supervised labels.
