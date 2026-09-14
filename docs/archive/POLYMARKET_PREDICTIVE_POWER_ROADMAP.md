# Polymarket Predictive Power Roadmap

This roadmap extends the base Polymarket predictive engine with historical labels, point-in-time price histories, calibration, category fallback models, and paper-only edge simulation.

## Safety principles

- Active market outcome prices are never used as targets.
- Historical price snapshots do not include target, winner, winning, resolved, settlement, payout, or final-result fields.
- Labels are created only by joining snapshot rows to clean closed-market resolution rows.
- Live trading gates remain unchanged.
- The edge simulator is paper-only and does not call live execution.

## Required command order

```powershell
python -m pytest -q
polymarket-engine backfill-resolved-markets --config polymarket_predictive_config.example.yaml --historical-limit 250
polymarket-engine collect-price-history --config polymarket_predictive_config.example.yaml --historical-limit 500
polymarket-engine build-labels --config polymarket_predictive_config.example.yaml
polymarket-engine build-features-v2 --config polymarket_predictive_config.example.yaml
polymarket-engine train-calibration --config polymarket_predictive_config.example.yaml
polymarket-engine calibrate-categories --config polymarket_predictive_config.example.yaml
polymarket-engine simulate-paper-edge --config polymarket_predictive_config.example.yaml
polymarket-engine collect-websocket --config polymarket_predictive_config.example.yaml --websocket-seconds 60
polymarket-engine collect-external-feeds --config polymarket_predictive_config.example.yaml
```

## Outputs

- `outputs/polymarket_training/historical_resolutions.csv`
- `outputs/polymarket_training/historical_price_snapshots.csv`
- `outputs/polymarket_training/labels.csv`
- `outputs/polymarket_training/features_v2.csv`
- `outputs/polymarket_models/calibration_v2.json`
- `outputs/polymarket_models/category_calibration_v2.json`
- `outputs/polymarket_paper_edge/paper_edge_orders.csv`
- `outputs/polymarket_paper_edge/paper_edge_rejections.csv`

## Model gate logic

Calibration refuses when there are insufficient joined labels. Category calibration falls back to the global calibration model when a category has too few rows. Paper edge simulation refuses until a calibration model exists.
