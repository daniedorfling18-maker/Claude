# Polymarket predictive paper-trading loop

> **Retired local runbook.** Do not run this stack locally. The active paper
> system uses `docker-compose.vps-paper.yml` on the VPS and the guarded deploy
> workflow described in `AGENTS.md`. The commands below remain for history only.

This workflow is strictly paper trading and is intended to run locally through
Docker. It scans live Polymarket order books, stores the snapshot, predicts from
the deployed model, gates signals through the risk controls, and records fills in
the local SQLite paper ledger. It does not instantiate the live executor and it
forces live-execution environment flags off.

GitHub Actions are not the operating path for this bot; the scheduled Action is
disabled/manual-only because the bot depends on local Docker state and mounted
local volumes.

## Local Docker run

Prepare the local env file once:

```bash
cp .env.example .env
```

Run continuously:

```bash
docker compose up -d --build polymarket-paper-bot
```

Run one local smoke cycle:

```bash
PAPER_LOOP_ITERATIONS=1 docker compose up --build polymarket-paper-bot
```

View logs:

```bash
docker compose logs -f polymarket-paper-bot
```

Stop:

```bash
docker compose down
```

The Docker service writes:

- `outputs/polymarket/market_snapshot.csv` from the scanner
- `outputs/polymarket_fixed/worldcup/ml/raw_market_snapshots.csv` as the canonical point-in-time stream
- `outputs/polymarket_models/optimized_model_v1.json`
- `outputs/polymarket_predictions/predictions.csv`
- `outputs/polymarket_portfolio/*.csv`
- `outputs/polymarket_model_governance/live_paper_loop_heartbeat.json`
- `outputs/polymarket_model_governance/forward_paper_cycle.json`

## ML optimizer contract

Run directly with:

```bash
python -m polymarket_predictive_engine.cli optimize-model
```

The Docker loop runs this step automatically with `--optimize-model`.

The optimizer is market-anchored and leakage-screened:

- the market midpoint/implied probability is used once as a `market_logit` anchor;
- executable/current price aliases such as `best_ask`, `best_bid`, and
  `executable_buy_price` are excluded from model features;
- regularization is tuned using market-disjoint walk-forward folds;
- the latest markets are kept as an untouched final holdout;
- the artifact is deployed as `champion` only if it improves holdout Brier score
  versus the market and meets the configured holdout size gates;
- otherwise it remains `shadow`, so paper runs can still log forward evidence
  without trusting it for sizing.

Paper prediction uses an optimized `champion` automatically. If the optimized
model is `shadow`, the existing calibration model remains the champion and the
optimized probability is emitted as `shadow_probability`.

## Live promotion remains blocked

Forward paper execution is not live trading approval. Live promotion still
requires the separate governance gate, clean label history, market-relative
validation, forward paper evidence, and the human approval file described in the
live trading checklist.
