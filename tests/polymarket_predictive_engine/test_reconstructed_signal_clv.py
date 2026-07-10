from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from polymarket_predictive_engine.cli import COMMANDS
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.reconstructed_signal_clv import run_reconstructed_clv_study
from polymarket_predictive_engine.utils import read_csv_rows, read_json, write_csv


def _config(tmp_path: Path, *, snapshot_path: Path, history_path: Path, minimum_units: int = 8):
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    raw["reconstructed_clv"] = {
        "enabled": True,
        "snapshot_paths": [str(snapshot_path)],
        "token_map_paths": [],
        "price_history_paths": [str(history_path)],
        "max_price_history_requests": 0,
        "max_gamma_requests": 0,
        "minimum_units_per_cohort": minimum_units,
        "fdr_alpha": 0.10,
        "fundamental_probability_haircut": 0.50,
        "minimum_fundamental_edge_after_haircut": 0.03,
        "minimum_entry_price": 0.05,
        "maximum_entry_price": 0.90,
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def _write_planted_files(tmp_path: Path, *, close_delta: float, markets: int = 10) -> tuple[Path, Path]:
    snapshot_path = tmp_path / "sharp_snapshots.csv"
    history_path = tmp_path / "historical_price_snapshots.csv"
    start = datetime(2026, 7, 10, 12, tzinfo=timezone.utc)
    close = start + timedelta(days=2)
    snapshots = []
    history = []
    for idx in range(markets):
        token = f"token-{idx}"
        market = f"market-{idx}"
        snapshots.append(
            {
                "market_slug": market,
                "market_id": market,
                "outcome": "Yes",
                "token_id": token,
                "probability": 0.57,
                "anchor_timestamp_utc": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "close_time": close.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "sport": "soccer",
                "question": f"Will Team {idx} win?",
            }
        )
        history.extend(
            [
                {
                    "market_id": market,
                    "market_slug": market,
                    "token_id": token,
                    "timestamp": (start - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "price": 0.50,
                    "close_time": close.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                {
                    "market_id": market,
                    "market_slug": market,
                    "token_id": token,
                    "timestamp": (close - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "price": round(0.50 + close_delta, 6),
                    "close_time": close.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            ]
        )
    write_csv(snapshot_path, snapshots)
    write_csv(history_path, history)
    return snapshot_path, history_path


def test_reconstructed_clv_recovers_planted_positive_edge(tmp_path: Path) -> None:
    snapshot_path, history_path = _write_planted_files(tmp_path, close_delta=0.03)
    cfg = _config(tmp_path, snapshot_path=snapshot_path, history_path=history_path)

    summary = run_reconstructed_clv_study(cfg)

    assert summary["status"] == "ok"
    assert summary["evidence_class"] == "reconstructed_research"
    assert summary["reconstructed_entries"] == 10
    assert summary["independent_units"] == 10
    assert abs(summary["unit_mean_clv"] - 0.03) < 1e-9
    assert summary["research_flagged_cohorts"] == ["reconstructed_sharp_anchor|soccer"]
    assert summary["paper_trading_invoked"] is False
    assert summary["live_trading_invoked"] is False

    persisted = read_json(cfg.governance_root / "reconstructed_signal_clv.json")
    assert persisted["non_verdict_warning"].startswith("Reconstructed entries are retrospective")
    rows = read_csv_rows(cfg.governance_root / "reconstructed_signal_clv_positions.csv")
    assert {row["evidence_class"] for row in rows} == {"reconstructed_research"}


def test_reconstructed_clv_martingale_null_flags_nothing(tmp_path: Path) -> None:
    snapshot_path, history_path = _write_planted_files(tmp_path, close_delta=0.0)
    cfg = _config(tmp_path, snapshot_path=snapshot_path, history_path=history_path)

    summary = run_reconstructed_clv_study(cfg)

    assert summary["status"] == "ok"
    assert summary["unit_mean_clv"] == 0.0
    assert summary["unit_beat_close_rate"] == 0.0
    assert summary["research_flagged_cohorts"] == []
    assert summary["cohorts"][0]["research_flag"] is False


def test_reconstructed_clv_collapses_same_fixture_markets(tmp_path: Path) -> None:
    snapshot_path = tmp_path / "sharp_snapshots.csv"
    history_path = tmp_path / "historical_price_snapshots.csv"
    start = datetime(2026, 7, 10, 12, tzinfo=timezone.utc)
    close = start + timedelta(days=2)
    rows = []
    history = []
    for idx, question in enumerate(
        [
            "Will France win on 2026-07-12?",
            "Will France advance against Morocco?",
        ]
    ):
        token = f"token-{idx}"
        rows.append(
            {
                "market_slug": f"fixture-market-{idx}",
                "market_id": f"fixture-market-{idx}",
                "outcome": "Yes",
                "token_id": token,
                "probability": 0.57,
                "anchor_timestamp_utc": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "close_time": close.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "sport": "soccer",
                "question": question,
            }
        )
        history.extend(
            [
                {"market_id": f"fixture-market-{idx}", "token_id": token, "timestamp": (start - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"), "price": 0.50},
                {"market_id": f"fixture-market-{idx}", "token_id": token, "timestamp": (close - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"), "price": 0.53},
            ]
        )
    write_csv(snapshot_path, rows)
    write_csv(history_path, history)
    cfg = _config(tmp_path, snapshot_path=snapshot_path, history_path=history_path, minimum_units=1)

    summary = run_reconstructed_clv_study(cfg)

    assert summary["reconstructed_entries"] == 2
    assert summary["independent_units"] == 1
    rows_out = read_csv_rows(cfg.governance_root / "reconstructed_signal_clv_positions.csv")
    assert len({row["cluster_unit"] for row in rows_out}) == 1


def test_reconstructed_clv_disabled_writes_safe_summary(tmp_path: Path) -> None:
    snapshot_path, history_path = _write_planted_files(tmp_path, close_delta=0.03)
    cfg = _config(tmp_path, snapshot_path=snapshot_path, history_path=history_path)
    cfg.raw["reconstructed_clv"]["enabled"] = False

    summary = run_reconstructed_clv_study(cfg)

    assert summary["status"] == "disabled"
    assert summary["paper_trading_invoked"] is False
    assert summary["live_trading_invoked"] is False


def test_reconstructed_clv_cli_registered() -> None:
    assert "reconstructed-clv-study" in COMMANDS
