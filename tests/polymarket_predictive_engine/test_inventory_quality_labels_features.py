from pathlib import Path
import csv
import pytest

from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.data_inventory import inventory
from polymarket_predictive_engine.data_quality import data_quality
from polymarket_predictive_engine.labels import build_labels
from polymarket_predictive_engine.features import build_features


def make_cfg(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.yaml"
    text = Path("polymarket_predictive_config.example.yaml").read_text()
    text = text.replace('data_root: "."', f'data_root: "{tmp_path.as_posix()}"')
    text = text.replace('output_root: "outputs"', f'output_root: "{(tmp_path/"outputs").as_posix()}"')
    cfg.write_text(text, encoding="utf-8")
    return cfg


def write_resolution(tmp_path: Path):
    out = tmp_path / "outputs" / "polymarket_training"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "market_resolutions.csv"
    cols = ["market_slug", "condition_id", "gamma_market_id", "token_id", "outcome", "close_time", "resolution_time", "target", "resolution_quality"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        w.writerow({"market_slug":"m1", "condition_id":"m1", "gamma_market_id":"g1", "token_id":"t1", "outcome":"Yes", "close_time":"2026-01-02T00:00:00Z", "resolution_time":"2026-01-03T00:00:00Z", "target":"1", "resolution_quality":"clean_settlement"})


def write_raw(tmp_path: Path, leakage: bool = False):
    folder = tmp_path / "outputs" / "polymarket_wide" / "sports" / "ml"
    folder.mkdir(parents=True)
    cols = ["snapshot_timestamp", "market_id", "token_id", "question", "best_bid", "best_ask", "liquidity", "volume", "close_time", "resolution_time"]
    if leakage:
        cols.append("resolved")
    path = folder / "raw_market_snapshots.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        base = {"market_id":"m1", "token_id":"t1", "question":"Will A win by Friday?", "best_bid":"0.4", "best_ask":"0.42", "liquidity":"100", "volume":"10", "close_time":"2026-01-02T00:00:00Z", "resolution_time":"2026-01-03T00:00:00Z"}
        for ts, price in [("2026-01-01T00:00:00Z", "0.42"), ("2026-01-01T06:00:00Z", "0.45")]:
            row = dict(base); row["snapshot_timestamp"] = ts; row["best_ask"] = price
            if leakage: row["resolved"] = "false"
            w.writerow(row)
    return path


def test_inventory_classifies_raw(tmp_path):
    write_raw(tmp_path)
    cfg = load_config(make_cfg(tmp_path))
    rows = inventory(cfg)
    assert any(r["file_type"] == "raw_point_in_time_training_candidate" for r in rows)


def test_data_quality_missing_raw_is_blocker(tmp_path):
    cfg = load_config(make_cfg(tmp_path))
    _, summary = data_quality(cfg, allow_warnings=True)
    assert summary["blocker_count"] >= 1


def test_missing_labels_fail_closed(tmp_path):
    write_raw(tmp_path)
    cfg = load_config(make_cfg(tmp_path))
    with pytest.raises(RuntimeError):
        build_labels(cfg)


def test_label_generation_from_clean_resolution(tmp_path):
    write_raw(tmp_path)
    write_resolution(tmp_path)
    cfg = load_config(make_cfg(tmp_path))
    labels = build_labels(cfg)
    assert labels
    assert labels[0]["target"] == 1
    assert labels[0]["label_source"] == "resolution_join"


def test_feature_generation_rejects_leakage(tmp_path):
    write_raw(tmp_path, leakage=True)
    cfg = load_config(make_cfg(tmp_path))
    with pytest.raises(ValueError):
        build_features(cfg)


def test_point_in_time_features(tmp_path):
    write_raw(tmp_path, leakage=False)
    cfg = load_config(make_cfg(tmp_path))
    features = build_features(cfg)
    second = [f for f in features if f["snapshots_so_far"] == 1][0]
    assert second["price_change_6h"] != ""
