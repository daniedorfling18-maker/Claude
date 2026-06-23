from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.features_v2 import build_features_v2
from polymarket_predictive_engine.labels import build_labels
from polymarket_predictive_engine.models.calibration_v2 import fit_bucket_calibrator, train_calibration_model
from polymarket_predictive_engine.models.category_calibration import train_category_calibration
from polymarket_predictive_engine.paper_edge_simulator import simulate_paper_edge
from polymarket_predictive_engine.price_history_collector import normalize_price_history_payload
from polymarket_predictive_engine.resolution_collector import infer_market_resolution_rows
from polymarket_predictive_engine.websocket_collector import collect_websocket


def make_cfg(tmp_path: Path, minimum_rows: int = 2, min_category_rows: int = 3) -> Path:
    cfg = tmp_path / "config.yaml"
    text = Path("polymarket_predictive_config.example.yaml").read_text()
    text = text.replace('data_root: "."', f'data_root: "{tmp_path.as_posix()}"')
    text = text.replace('output_root: "outputs"', f'output_root: "{(tmp_path / "outputs").as_posix()}"')
    text = text.replace("minimum_training_rows: 100", f"minimum_training_rows: {minimum_rows}")
    text = text.replace("min_rows_per_category: 50", f"min_rows_per_category: {min_category_rows}")
    cfg.write_text(text, encoding="utf-8")
    return cfg


def write_resolution(tmp_path: Path) -> None:
    out = tmp_path / "outputs" / "polymarket_training"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "historical_resolutions.csv"
    cols = ["market_slug", "condition_id", "gamma_market_id", "question_id", "token_id", "outcome", "category", "close_time", "resolution_time", "target", "resolution_quality"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for token, outcome, target in [("t1", "Yes", "1"), ("t0", "No", "0")]:
            w.writerow({"market_slug": "m1", "condition_id": "m1", "gamma_market_id": "g1", "question_id": "q1", "token_id": token, "outcome": outcome, "category": "sports", "close_time": "2026-01-02T00:00:00Z", "resolution_time": "2026-01-03T00:00:00Z", "target": target, "resolution_quality": "clean_settlement"})


def write_history(tmp_path: Path, leakage: bool = False) -> None:
    out = tmp_path / "outputs" / "polymarket_training"
    out.mkdir(parents=True, exist_ok=True)
    cols = ["market_id", "market_slug", "token_id", "outcome", "category", "timestamp", "midpoint", "price", "close_time", "source"]
    if leakage:
        cols.append("target")
    with (out / "historical_price_snapshots.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for token, outcome, base in [("t1", "Yes", 0.40), ("t0", "No", 0.60)]:
            for ts, bump in [("2026-01-01T00:00:00Z", 0.0), ("2026-01-01T06:00:00Z", 0.05)]:
                row = {"market_id": "m1", "market_slug": "m1", "token_id": token, "outcome": outcome, "category": "sports", "timestamp": ts, "midpoint": base + bump, "price": base + bump, "close_time": "2026-01-02T00:00:00Z", "source": "test"}
                if leakage:
                    row["target"] = "1"
                w.writerow(row)


def test_historical_closed_market_classification():
    rows, quality = infer_market_resolution_rows(
        {
            "slug": "m1",
            "id": "g1",
            "conditionId": "m1",
            "questionID": "q1",
            "question": "Will A win?",
            "category": "sports",
            "closed": True,
            "active": False,
            "outcomes": json.dumps(["Yes", "No"]),
            "clobTokenIds": json.dumps(["t1", "t0"]),
            "outcomePrices": json.dumps(["1", "0"]),
            "closedTime": "2026-01-03T00:00:00Z",
            "endDate": "2026-01-02T00:00:00Z",
        }
    )
    assert quality[0]["resolution_quality"] == "clean_settlement"
    assert {r["target"] for r in rows} == {0, 1}


def test_price_history_payload_normalisation():
    rows = normalize_price_history_payload({"history": [{"t": 1767225600, "p": "0.55"}]})
    assert rows == [{"timestamp": "2026-01-01T00:00:00Z", "price": 0.55}]


def test_labels_join_clean_resolution_and_features_v2_point_in_time(tmp_path):
    write_resolution(tmp_path)
    write_history(tmp_path)
    cfg = load_config(make_cfg(tmp_path))
    labels = build_labels(cfg)
    features = build_features_v2(cfg)
    assert labels
    assert features
    second = [f for f in features if f["token_id"] == "t1" and f["snapshots_so_far"] == 1][0]
    assert second["price_change_6h"] != ""


def test_features_v2_rejects_leakage(tmp_path):
    write_resolution(tmp_path)
    write_history(tmp_path, leakage=True)
    cfg = load_config(make_cfg(tmp_path))
    with pytest.raises(ValueError):
        build_features_v2(cfg)


def test_bucket_calibration():
    rows = [{"predicted_probability": 0.2, "target": 0}, {"predicted_probability": 0.8, "target": 1}]
    reliability, model = fit_bucket_calibrator(rows, bucket_count=2)
    assert len(reliability) == 2
    assert model["bucket_count"] == 2


def test_category_insufficient_rows_fallback(tmp_path):
    write_resolution(tmp_path)
    write_history(tmp_path)
    cfg = load_config(make_cfg(tmp_path, minimum_rows=2, min_category_rows=99))
    build_labels(cfg)
    build_features_v2(cfg)
    train_calibration_model(cfg)
    result = train_category_calibration(cfg)
    assert result["fallback_categories"] >= 1


def test_paper_simulator_refuses_without_calibration(tmp_path):
    write_resolution(tmp_path)
    write_history(tmp_path)
    cfg = load_config(make_cfg(tmp_path))
    build_labels(cfg)
    build_features_v2(cfg)
    with pytest.raises(RuntimeError):
        simulate_paper_edge(cfg)


def test_websocket_skipped_if_dependency_missing(tmp_path, monkeypatch):
    cfg = load_config(make_cfg(tmp_path))
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "websockets":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("importlib.import_module", lambda name: (_ for _ in ()).throw(ImportError("missing")) if name == "websockets" else __import__(name))
    result = collect_websocket(cfg, websocket_seconds=1)
    assert result["status"] == "skipped"
