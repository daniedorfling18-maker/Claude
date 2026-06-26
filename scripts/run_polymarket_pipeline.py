#!/usr/bin/env python3
"""Run the automated Polymarket dry-run pipeline once.

Pipeline mechanics:
1. Force safe dry-run mode for GitHub-hosted automation.
2. Run one bootstrap Polymarket scan to discover live World Cup Winner tokens.
3. Convert repo match-probability data into token-level model probabilities.
4. Run one final Polymarket scan using those repo-derived probabilities.
5. Generate long/short intents from the final live book snapshot.
6. Evaluate prior market-making quotes against the final live book snapshot and
   store current quotes for the next scheduled run.

This is designed for GitHub Actions scheduled dry-runs. Live trading remains out
of scope for GitHub-hosted runners and must stay behind the separate VPS/live
execution controls.
"""
from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import polymarket_mispricing_bot as bot  # noqa: E402
from polymarket_predictive_engine.config import load_config  # noqa: E402
from polymarket_predictive_engine.mispricing_alpha import train_mispricing_alpha_model  # noqa: E402
from polymarket_predictive_engine.models.optimized import train_optimized_model  # noqa: E402
from polymarket_predictive_engine.paper_cycle import run_paper_cycle  # noqa: E402
from polymarket_predictive_engine.snapshot_ingest import ingest_scanner_snapshot  # noqa: E402


def safe_dry_run_env() -> None:
    os.environ["PM_MODE"] = "dry_run"
    os.environ["POLYMARKET_EXECUTE_LIVE"] = "false"
    os.environ.setdefault("POLYMARKET_QUERY", "world cup")
    os.environ.setdefault("POLYMARKET_MODEL_PROBABILITIES_CSV", "inputs/polymarket/model_probabilities.csv")
    os.environ.setdefault("POLYMARKET_POSITIONS_CSV", "inputs/polymarket/positions.csv")
    os.environ.setdefault("POLYMARKET_OUTPUT_DIR", "outputs/polymarket")
    os.environ.setdefault("POLYMARKET_MIN_EDGE", "0.04")
    os.environ.setdefault("LONG_SHORT_MIN_EDGE", os.environ.get("POLYMARKET_MIN_EDGE", "0.04"))


def ensure_runtime_files() -> None:
    (ROOT / "inputs/polymarket").mkdir(parents=True, exist_ok=True)
    (ROOT / "outputs/polymarket").mkdir(parents=True, exist_ok=True)

    model_csv = ROOT / os.environ["POLYMARKET_MODEL_PROBABILITIES_CSV"]
    if not model_csv.exists():
        model_csv.write_text("token_id,probability\n", encoding="utf-8")

    positions_csv = ROOT / os.environ["POLYMARKET_POSITIONS_CSV"]
    if not positions_csv.exists():
        positions_csv.write_text("token_id,shares\n", encoding="utf-8")


def run_scanner_once(label: str) -> None:
    config = bot.BotConfig.from_env()
    config = dataclasses.replace(config, mode="dry_run", execute_live=False)
    print(f"pipeline: {label} scanner run", flush=True)
    tokens, opportunities = bot.run_once(config, live_executor=None)
    print(f"pipeline: {label} scanner completed tokens={tokens} opportunities={opportunities}", flush=True)


def run_converter() -> None:
    print("pipeline: building repo-derived Polymarket model probabilities", flush=True)
    subprocess.check_call([sys.executable, "scripts/build_repo_worldcup_winner_probabilities.py"], cwd=ROOT)


def run_long_short() -> None:
    print("pipeline: generating long/short intents from final market snapshot", flush=True)
    out_dir = os.environ.get("POLYMARKET_OUTPUT_DIR", "outputs/polymarket")
    min_edge = os.environ.get("LONG_SHORT_MIN_EDGE", os.environ.get("POLYMARKET_MIN_EDGE", "0.04"))
    max_live_orders = os.environ.get("LONG_SHORT_MAX_LIVE_ORDERS", "3")
    stake_usdc = os.environ.get("LONG_SHORT_STAKE_USDC", "50")

    subprocess.check_call(
        [
            sys.executable,
            "scripts/polymarket_long_short_engine.py",
            "--market-snapshot",
            f"{out_dir}/market_snapshot.csv",
            "--out-csv",
            f"{out_dir}/long_short_intents.csv",
            "--min-edge",
            min_edge,
            "--max-live-orders",
            max_live_orders,
            "--stake-usdc",
            stake_usdc,
        ],
        cwd=ROOT,
    )


def run_market_making_eval() -> None:
    print("pipeline: evaluating market-making quote mechanics", flush=True)
    out_dir = os.environ.get("POLYMARKET_OUTPUT_DIR", "outputs/polymarket")
    subprocess.check_call(
        [
            sys.executable,
            "scripts/polymarket_market_making_eval.py",
            "--snapshot",
            f"{out_dir}/market_snapshot.csv",
            "--intents",
            f"{out_dir}/long_short_intents.csv",
            "--state",
            f"{out_dir}/mm_quote_state.csv",
            "--eval-csv",
            f"{out_dir}/mm_quote_evaluations.csv",
            "--summary-csv",
            f"{out_dir}/mm_quote_summary.csv",
        ],
        cwd=ROOT,
    )


def run_forward_paper_cycle() -> None:
    """Persist the final scanner snapshot, then run the canonical paper-only path."""
    config_path = os.environ.get(
        "POLYMARKET_PREDICTIVE_CONFIG",
        "polymarket_predictive_config.example.yaml",
    )
    cfg = load_config(ROOT / config_path)
    ingest = ingest_scanner_snapshot(
        cfg,
        snapshot_path=ROOT
        / os.environ.get("POLYMARKET_OUTPUT_DIR", "outputs/polymarket")
        / "market_snapshot.csv",
    )
    optimization = (
        train_optimized_model(cfg)
        if bool(cfg.raw.get("ml_optimization", {}).get("enabled", True))
        else {"status": "skipped"}
    )
    alpha = (
        train_mispricing_alpha_model(cfg)
        if bool(cfg.raw.get("mispricing_alpha", {}).get("enabled", True))
        else {"status": "skipped"}
    )
    paper = run_paper_cycle(cfg, source="raw_snapshot")
    print(f"pipeline: raw snapshot ingest={ingest}", flush=True)
    print(
        "pipeline: optimized model="
        f"status={optimization.get('status')} mode={optimization.get('deployment_mode')}",
        flush=True,
    )
    print(
        "pipeline: mispricing alpha="
        f"status={alpha.get('status')} rows={alpha.get('training_rows')}",
        flush=True,
    )
    print(f"pipeline: forward paper cycle={paper}", flush=True)


def main() -> int:
    safe_dry_run_env()
    ensure_runtime_files()
    run_scanner_once("bootstrap")
    run_converter()
    run_scanner_once("final")
    run_long_short()
    run_market_making_eval()
    run_forward_paper_cycle()
    print("pipeline: complete", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
