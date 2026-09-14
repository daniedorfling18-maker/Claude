# Polymarket Predictive Engine Patch

Copy this file tree into the repository root.

Then update `pyproject.toml` by adding:

```toml
polymarket-engine = "polymarket_predictive_engine.cli:main"
```

under `[project.scripts]`.

Also append the contents of `GITIGNORE_POLYMARKET_SNIPPET.txt` to `.gitignore`.

The live approval file must never be committed. Only `config/polymarket_live_approval.example.yaml` is included.

Recommended commands:

```powershell
pip install -e .
pytest
polymarket-engine config-check --config polymarket_predictive_config.example.yaml
polymarket-engine pipeline-inventory --config polymarket_predictive_config.example.yaml
polymarket-engine pipeline-health --config polymarket_predictive_config.example.yaml
polymarket-engine inventory --config polymarket_predictive_config.example.yaml
polymarket-engine data-quality --config polymarket_predictive_config.example.yaml --allow-data-quality-warnings
polymarket-engine readiness --config polymarket_predictive_config.example.yaml
polymarket-engine build-labels --config polymarket_predictive_config.example.yaml
polymarket-engine build-features --config polymarket_predictive_config.example.yaml
polymarket-engine predict --config polymarket_predictive_config.example.yaml
polymarket-engine validate --config polymarket_predictive_config.example.yaml
polymarket-engine generate-signals --config polymarket_predictive_config.example.yaml
polymarket-engine backtest --config polymarket_predictive_config.example.yaml
polymarket-engine paper-trade --config polymarket_predictive_config.example.yaml
polymarket-engine live-trade --config polymarket_predictive_config.example.yaml
```

Expected first-run behaviour: if resolved labels are not present in `raw_market_snapshots.csv`, `build-labels`, `train`, `validate` and live trading fail closed. That is intentional.
