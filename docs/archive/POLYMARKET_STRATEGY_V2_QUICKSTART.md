# Polymarket Strategy V2 Quickstart

Strategy V2 is a read-only anchored-edge scanner. It does not generate trade signals and does not place paper or live orders.

## Run

From the repo root:

```powershell
python .\scripts\run_polymarket_strategy_v2_anchored_edge.py polymarket_predictive_config.example.yaml
```

or, after CLI wiring is available locally:

```powershell
python -m polymarket_predictive_engine.cli anchored-edge --config polymarket_predictive_config.example.yaml
```

## Outputs

```text
outputs/polymarket_strategy_v2/anchored_edge_candidates.csv
outputs/polymarket_strategy_v2/anchored_edge_report.json
outputs/polymarket_strategy_v2/anchored_edge_report.md
```

## Add manual anchors

Create this optional CSV:

```text
inputs/polymarket/strategy_v2_manual_anchors.csv
```

Supported columns:

```text
token_id,market_slug,outcome,fair_probability,anchor_source,anchor_timestamp_utc,methodology_note
```

Rules:

- Use `token_id` whenever possible.
- If `token_id` is unavailable, use `market_slug` + `outcome`.
- `fair_probability` must be between 0 and 1.
- `anchor_source` and `methodology_note` are required for manual anchors to be reviewable.

## Decision rule

Keep Strategy V2 shadow-only until the report shows a clean, anchored family with enough settled evidence.

Do not copy Strategy V2 candidates into `trade_signals.csv` manually.
