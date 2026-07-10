"""WO-42 calibration-bias study tests."""
from __future__ import annotations

from pathlib import Path

import yaml

from polymarket_predictive_engine.cli import COMMANDS
from polymarket_predictive_engine.calibration_bias_study import build_calibration_bias_study
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.utils import read_csv_rows, write_csv


def _config(tmp_path: Path):
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    raw["calibration_bias"] = {
        "enabled": True,
        "price_horizons_hours": [24],
        "min_markets_per_bin": 20,
        "price_bins": [0.05, 0.10, 0.15, 0.50, 0.55, 0.90, 0.95],
        "fdr_alpha": 0.10,
        "bootstrap_iterations": 200,
        "bootstrap_seed": 123,
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def _write_corpus(cfg, rows: list[tuple[str, str, float, int]]) -> None:
    resolutions = []
    prices = []
    for market, token, price, target in rows:
        resolutions.append(
            {
                "condition_id": market,
                "market_slug": market,
                "token_id": token,
                "category": "sports",
                "target": target,
                "close_time": "2026-01-10T00:00:00Z",
                "resolution_quality": "clean_settlement",
            }
        )
        prices.append(
            {
                "market_id": market,
                "condition_id": market,
                "token_id": token,
                "category": "sports",
                "timestamp": "2026-01-09T00:00:00Z",
                "midpoint": price,
                "price": price,
                "close_time": "2026-01-10T00:00:00Z",
            }
        )
    write_csv(cfg.output_root / "polymarket_training" / "market_resolutions.csv", resolutions)
    write_csv(cfg.output_root / "polymarket_training" / "historical_price_snapshots.csv", prices)


def test_planted_favourite_longshot_bias_flags_after_fdr(tmp_path):
    cfg = _config(tmp_path)
    rows = []
    for idx in range(30):
        rows.append((f"fav-{idx}", f"fav-token-{idx}", 0.90, 1))
        rows.append((f"dog-{idx}", f"dog-token-{idx}", 0.10, 0))
    _write_corpus(cfg, rows)

    summary = build_calibration_bias_study(cfg)

    assert summary["status"] == "ok"
    assert summary["flagged_bins"] == 2
    curve = read_csv_rows(cfg.output_root / "calibration_bias" / "calibration_curve.csv")
    assert {row["price_bin"] for row in curve if row["flagged_edge"] == "True"} == {"0.10-0.15", "0.90-0.95"}
    assert summary["paper_trading_invoked"] is False
    assert summary["live_trading_invoked"] is False


def test_unbiased_synthetic_corpus_flags_nothing(tmp_path):
    cfg = _config(tmp_path)
    rows = []
    for idx in range(40):
        rows.append((f"fair-{idx}", f"fair-token-{idx}", 0.50, 1 if idx % 2 == 0 else 0))
    _write_corpus(cfg, rows)

    summary = build_calibration_bias_study(cfg)

    assert summary["status"] == "ok"
    assert summary["flagged_bins"] == 0
    curve = read_csv_rows(cfg.output_root / "calibration_bias" / "calibration_curve.csv")
    assert all(row["flagged_edge"] == "False" for row in curve)


def test_disabled_mode_writes_summary(tmp_path):
    cfg = _config(tmp_path)
    raw = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    raw["calibration_bias"]["enabled"] = False
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    cfg = load_config(tmp_path / "config.yaml")

    summary = build_calibration_bias_study(cfg)

    assert summary["status"] == "disabled"
    assert summary["paper_trading_invoked"] is False
    assert summary["live_trading_invoked"] is False


def test_cli_exposes_calibration_bias_study():
    assert "calibration-bias-study" in COMMANDS
