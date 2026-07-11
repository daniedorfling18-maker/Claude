"""WO-52 hourly adverse-selection concentration study tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from polymarket_predictive_engine.cli import COMMANDS
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.hourly_adverse_study import run_hourly_adverse_study
from polymarket_predictive_engine.utils import read_json, write_csv


def _config(tmp_path: Path, *, min_events: int = 30):
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    raw["hourly_adverse"] = {
        "enabled": True,
        "min_events_per_bucket": min_events,
        "fdr_alpha": 0.10,
        "price_history_paths": ["outputs/polymarket_training/historical_price_snapshots.csv"],
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def _seed_candidate(cfg) -> None:
    write_csv(
        cfg.output_root / "maker_carry" / "maker_carry_candidates.csv",
        [
            {
                "question": "Will synthetic market resolve cleanly?",
                "condition_id": "0xhourly",
                "token_id": "tok-hourly",
                "quote_distance": 0.01,
                "rewards_min_size_shares": 100,
            }
        ],
        fieldnames=["question", "condition_id", "token_id", "quote_distance", "rewards_min_size_shares"],
    )


def _seed_quote_sheet(cfg) -> None:
    path = cfg.output_root / "maker_carry" / "maker_quote_sheet.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Maker quote sheet\n\nStanding rules (non-negotiable if a human ever acts on this):\n",
        encoding="utf-8",
    )


def _seed_price_history(cfg, *, toxic_hours: set[int] | None = None, minutes_per_hour: int = 60) -> None:
    toxic_hours = toxic_hours or set()
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    rows = []
    for hour in range(24):
        jump = 0.20 if hour in toxic_hours else 0.02
        for minute in range(minutes_per_hour):
            price = 0.5
            if minute == 1:
                price = 0.5 + jump
            rows.append(
                {
                    "market_id": "0xhourly",
                    "condition_id": "0xhourly",
                    "token_id": "tok-hourly",
                    "timestamp": (start + timedelta(hours=hour, minutes=minute)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "price": price,
                    "midpoint": price,
                }
            )
    write_csv(
        cfg.output_root / "polymarket_training" / "historical_price_snapshots.csv",
        rows,
        fieldnames=["market_id", "condition_id", "token_id", "timestamp", "price", "midpoint"],
    )


def _seed_trade_prints(cfg) -> None:
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    rows = []
    for hour in range(24):
        rows.append(
            {
                "trade_id": f"trade-{hour}",
                "market": "0xhourly",
                "asset_id": "tok-hourly",
                "side": "BUY",
                "price": 0.5,
                "size": 10 + hour,
                "timestamp": (start + timedelta(hours=hour, minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "collected_at_utc": (start + timedelta(hours=hour, minutes=31)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        rows,
        fieldnames=["trade_id", "market", "asset_id", "side", "price", "size", "timestamp", "collected_at_utc"],
    )


def test_uniform_synthetic_flow_flags_no_buckets_and_patches_quote_sheet(tmp_path):
    cfg = _config(tmp_path)
    _seed_candidate(cfg)
    _seed_quote_sheet(cfg)
    _seed_price_history(cfg)
    _seed_trade_prints(cfg)

    result = run_hourly_adverse_study(cfg)

    assert result["status"] == "ok"
    assert result["flagged_hours_utc"] == []
    assert result["recommended_calm_hours_label"] == "00:00-24:00 UTC"
    assert result["recommended_calm_hours_covers_50pct_reward_minutes"] is True
    assert result["paper_trading_invoked"] is False
    assert result["live_trading_invoked"] is False
    assert "hourly-adverse-study" in COMMANDS
    persisted = read_json(cfg.output_root / "maker_carry" / "hourly_adverse.json")
    assert persisted["total_band_crossings"] > 0
    sheet = (cfg.output_root / "maker_carry" / "maker_quote_sheet.md").read_text(encoding="utf-8")
    assert "Calm-hours schedule (advisory)" in sheet
    assert "Study-only: this does not modify" in sheet


def test_planted_three_hour_toxic_window_is_recovered(tmp_path):
    cfg = _config(tmp_path)
    _seed_candidate(cfg)
    _seed_price_history(cfg, toxic_hours={3, 4, 5})
    _seed_trade_prints(cfg)

    result = run_hourly_adverse_study(cfg)

    assert set(result["flagged_hours_utc"]) == {3, 4, 5}
    assert len(result["recommended_calm_hours_utc"]) == 21
    assert not ({3, 4, 5} & set(result["recommended_calm_hours_utc"]))
    toxic_rows = {row["utc_hour"]: row for row in result["buckets"] if row["utc_hour"] in {3, 4, 5}}
    assert all(row["fdr_flag"] for row in toxic_rows.values())
    assert min(row["pickoff_charge_share"] for row in toxic_rows.values()) > (1 / 24)


def test_sample_floor_suppresses_hour_flags(tmp_path):
    cfg = _config(tmp_path, min_events=30)
    _seed_candidate(cfg)
    _seed_price_history(cfg, toxic_hours={3, 4, 5}, minutes_per_hour=10)
    _seed_trade_prints(cfg)

    result = run_hourly_adverse_study(cfg)

    assert result["status"] == "insufficient_samples"
    assert result["tested_buckets"] == 0
    assert result["flagged_hours_utc"] == []
    assert all(not row["sample_floor_met"] for row in result["buckets"])
