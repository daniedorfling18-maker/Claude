from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from polymarket_predictive_engine.cli import COMMANDS
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.drift_scan_study import run_drift_scan
from polymarket_predictive_engine.utils import read_csv_rows, read_json, write_csv


def _config(tmp_path: Path, history_path: Path, *, min_markets: int = 20):
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    raw["drift_scan"] = {
        "enabled": True,
        "price_history_paths": [str(history_path)],
        "horizons_hours": [24],
        "price_bins": [0.80, 0.85, 0.90, 0.95],
        "time_to_close_bins_hours": [24, 72, 168],
        "min_markets_per_bin": min_markets,
        "fdr_alpha": 0.10,
        "bootstrap_iterations": 200,
        "bootstrap_seed": 20260710,
        "taker_fee_rate": 0.05,
        "exit_cost_per_dollar": 0.005,
        "adverse_cost_per_dollar": 0.005,
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def _write_history(tmp_path: Path, *, drift: float, markets: int = 30) -> Path:
    path = tmp_path / "historical_price_snapshots.csv"
    close = datetime(2026, 7, 13, 12, tzinfo=timezone.utc)
    start = close - timedelta(hours=72)
    future = start + timedelta(hours=24)
    rows = []
    for idx in range(markets):
        market = f"market-{idx}"
        token = f"token-{idx}"
        rows.extend(
            [
                {
                    "market_id": market,
                    "market_slug": market,
                    "token_id": token,
                    "category": "sports",
                    "timestamp": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "price": 0.88,
                    "midpoint": 0.88,
                    "close_time": close.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                {
                    "market_id": market,
                    "market_slug": market,
                    "token_id": token,
                    "category": "sports",
                    "timestamp": future.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "price": round(0.88 + drift, 6),
                    "midpoint": round(0.88 + drift, 6),
                    "close_time": close.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            ]
        )
    write_csv(path, rows)
    return path


def test_drift_scan_recovers_planted_two_cent_drift(tmp_path: Path) -> None:
    history_path = _write_history(tmp_path, drift=0.02, markets=30)
    cfg = _config(tmp_path, history_path)

    summary = run_drift_scan(cfg)

    assert summary["status"] == "ok"
    assert summary["tested_bins"] == 1
    assert summary["flagged_bins"] == 1
    flagged = summary["flagged"][0]
    assert flagged["category"] == "sports"
    assert flagged["time_to_close_bin"] == "24-72h"
    assert flagged["price_bin"] == "0.85-0.90"
    assert abs(flagged["mean_drift"] - 0.02) < 1e-9
    assert flagged["cluster_bootstrap_ci_low"] > 0
    assert flagged["bh_fdr_pass"] is True
    assert flagged["abs_drift_exceeds_cost"] is True
    assert flagged["paper_trading_invoked"] is False
    assert flagged["live_trading_invoked"] is False

    persisted = read_json(cfg.governance_root / "drift_scan.json")
    assert persisted["paper_trading_invoked"] is False
    assert persisted["live_trading_invoked"] is False
    rows = read_csv_rows(cfg.governance_root / "drift_scan.csv")
    assert rows[0]["research_flag"] == "True"


def test_drift_scan_martingale_null_flags_nothing(tmp_path: Path) -> None:
    history_path = _write_history(tmp_path, drift=0.0, markets=30)
    cfg = _config(tmp_path, history_path)

    summary = run_drift_scan(cfg)

    assert summary["status"] == "ok"
    assert summary["tested_bins"] == 1
    assert summary["flagged_bins"] == 0
    row = read_csv_rows(cfg.governance_root / "drift_scan.csv")[0]
    assert float(row["mean_drift"]) == 0.0
    assert row["research_flag"] == "False"


def test_drift_scan_disabled_writes_safe_summary(tmp_path: Path) -> None:
    history_path = _write_history(tmp_path, drift=0.02, markets=30)
    cfg = _config(tmp_path, history_path)
    cfg.raw["drift_scan"]["enabled"] = False

    summary = run_drift_scan(cfg)

    assert summary["status"] == "disabled"
    assert summary["paper_trading_invoked"] is False
    assert summary["live_trading_invoked"] is False


def test_drift_scan_cli_registered() -> None:
    assert "drift-scan" in COMMANDS
