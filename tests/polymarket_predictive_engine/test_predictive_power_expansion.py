from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.features_v2 import build_features_v2
from polymarket_predictive_engine.labels import build_labels
from polymarket_predictive_engine.models.calibration_v2 import fit_bucket_calibrator, numeric_model_feature_columns, train_calibration_model
from polymarket_predictive_engine.models.calibrated import write_predictions
from polymarket_predictive_engine.models.category_calibration import train_category_calibration
from polymarket_predictive_engine.paper_edge_simulator import simulate_paper_edge
from polymarket_predictive_engine.price_history_collector import normalize_price_history_payload
from polymarket_predictive_engine.resolution_collector import infer_market_resolution_rows
from polymarket_predictive_engine.utils import read_csv_rows, read_json, write_csv
import polymarket_predictive_engine.websocket_collector as websocket_collector
from polymarket_predictive_engine.websocket_collector import collect_websocket


def make_cfg(tmp_path: Path, minimum_rows: int = 2, min_category_rows: int = 3) -> Path:
    import yaml

    cfg = tmp_path / "config.yaml"
    data = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8-sig"))

    data.setdefault("paths", {})
    data["paths"]["data_root"] = tmp_path.as_posix()
    data["paths"]["output_root"] = (tmp_path / "outputs").as_posix()

    data.setdefault("calibration_v2", {})
    data["calibration_v2"]["minimum_training_rows"] = minimum_rows

    data.setdefault("governance_thresholds", {})
    data["governance_thresholds"]["min_training_rows"] = minimum_rows

    data.setdefault("calibration", {})
    data["calibration"]["min_rows_per_category"] = min_category_rows

    data.setdefault("category_calibration", {})
    data["category_calibration"]["min_rows_per_category"] = min_category_rows

    cfg.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
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
    cols = ["market_id", "market_slug", "question", "token_id", "outcome", "category", "timestamp", "midpoint", "price", "close_time", "source"]
    if leakage:
        cols.append("target")
    with (out / "historical_price_snapshots.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for token, outcome, base in [("t1", "Yes", 0.40), ("t0", "No", 0.60)]:
            for ts, bump in [("2026-01-01T00:00:00Z", 0.0), ("2026-01-01T06:00:00Z", 0.05)]:
                row = {"market_id": "m1", "market_slug": "m1", "question": "Unit test Up or Down?", "token_id": token, "outcome": outcome, "category": "sports", "timestamp": ts, "midpoint": base + bump, "price": base + bump, "close_time": "2026-01-02T00:00:00Z", "source": "test"}
                if leakage:
                    row["target"] = "1"
                w.writerow(row)


def write_websocket_features(tmp_path: Path, leakage: bool = False) -> None:
    out = tmp_path / "outputs" / "polymarket_training"
    out.mkdir(parents=True, exist_ok=True)
    cols = [
        "collected_at_utc",
        "source_timestamp",
        "market",
        "asset_id",
        "event_type",
        "best_bid",
        "best_ask",
        "midpoint",
        "spread",
        "last_trade_price",
        "top_bid_size",
        "top_ask_size",
        "bid_depth_1pct",
        "ask_depth_1pct",
        "bid_depth_5pct",
        "ask_depth_5pct",
        "book_imbalance",
        "price_change_side",
        "price_change_price",
        "price_change_size",
    ]
    if leakage:
        cols.append("target")
    with (out / "websocket_market_features.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        row = {
            "collected_at_utc": "2026-01-01T00:00:00Z",
            "source_timestamp": "1782269898496",
            "market": "mws",
            "asset_id": "aws",
            "event_type": "price_change",
            "best_bid": "0.51",
            "best_ask": "0.53",
            "midpoint": "0.52",
            "spread": "0.02",
            "last_trade_price": "",
            "top_bid_size": "100",
            "top_ask_size": "150",
            "bid_depth_1pct": "100",
            "ask_depth_1pct": "150",
            "bid_depth_5pct": "300",
            "ask_depth_5pct": "450",
            "book_imbalance": "-0.2",
            "price_change_side": "BUY",
            "price_change_price": "0.51",
            "price_change_size": "25",
        }
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


def test_features_v2_websocket_only_no_labels(tmp_path):
    write_websocket_features(tmp_path)
    cfg = load_config(make_cfg(tmp_path))
    features = build_features_v2(cfg)
    assert len(features) == 1
    row = features[0]
    assert row["feature_source"] == "websocket"
    assert row["market_id"] == "mws"
    assert row["token_id"] == "aws"
    assert row["implied_probability"] == 0.52
    assert row["event_type"] == "price_change"
    assert row["source_file"].endswith("websocket_market_features.csv")
    assert row["liquidity"] == 250.0
    assert row["top_bid_size"] == 100.0
    assert row["book_imbalance"] == -0.2


def test_features_v2_websocket_uses_price_change_size_as_liquidity_fallback(tmp_path):
    write_websocket_features(tmp_path)
    path = tmp_path / "outputs" / "polymarket_training" / "websocket_market_features.csv"
    rows = read_csv_rows(path)
    fieldnames = list(rows[0].keys())
    for row in rows:
        for key in ("top_bid_size", "top_ask_size", "bid_depth_1pct", "ask_depth_1pct", "bid_depth_5pct", "ask_depth_5pct"):
            row[key] = ""
        row["price_change_size"] = "25"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    cfg = load_config(make_cfg(tmp_path))
    features = build_features_v2(cfg)

    assert features[0]["liquidity"] == 25.0


def test_features_v2_combines_historical_and_websocket_sources(tmp_path):
    write_history(tmp_path)
    write_websocket_features(tmp_path)
    cfg = load_config(make_cfg(tmp_path))
    features = build_features_v2(cfg)
    sources = {row["feature_source"] for row in features}
    assert {"historical", "websocket"}.issubset(sources)
    summary = read_json(tmp_path / "outputs" / "polymarket_model_governance" / "features_v2_summary.json")
    assert summary["historical_feature_rows"] == 4
    assert summary["websocket_feature_rows"] == 1
    assert summary["total_feature_rows"] == 5


def test_features_v2_rejects_websocket_leakage(tmp_path):
    write_websocket_features(tmp_path, leakage=True)
    cfg = load_config(make_cfg(tmp_path))
    with pytest.raises(ValueError):
        build_features_v2(cfg)


def test_features_v2_final_schema_has_no_leakage_columns(tmp_path):
    write_history(tmp_path)
    write_websocket_features(tmp_path)
    cfg = load_config(make_cfg(tmp_path))
    build_features_v2(cfg)
    rows = read_csv_rows(tmp_path / "outputs" / "polymarket_training" / "features_v2.csv")
    columns = set(rows[0].keys())
    forbidden = ["target", "winner", "winning", "resolved", "resolution", "settled", "settlement", "payout", "outcome", "label"]
    assert not [column for column in columns if any(token in column.lower() for token in forbidden)]


def test_live_side_metadata_survives_feature_to_prediction_handoff(tmp_path):
    write_history(tmp_path)
    cfg = load_config(make_cfg(tmp_path))
    features = build_features_v2(cfg)
    feature = next(row for row in features if row["token_id"] == "t1")
    assert feature["selection_name"] == "Yes"
    assert feature["question"] == "Unit test Up or Down?"
    assert feature["close_time"] == "2026-01-02T00:00:00Z"
    assert "outcome" not in feature

    predictions = write_predictions(features, str(tmp_path / "predictions.csv"))
    prediction = next(row for row in predictions if row["token_id"] == "t1")
    assert prediction["outcome"] == "Yes"
    assert prediction["question"] == "Unit test Up or Down?"
    assert prediction["close_time"] == "2026-01-02T00:00:00Z"


def test_model_feature_selection_ignores_provenance_and_leakage_fields():
    rows = [
        {
            "market_id": "m1",
            "token_id": "t1",
            "prediction_timestamp": "2026-01-01T00:00:00Z",
            "feature_source": "websocket",
            "source_file": "websocket_market_features.csv",
            "event_type": "price_change",
            "price_change_side": "BUY",
            "midpoint": "0.52",
            "spread": "0.02",
            "book_imbalance": "-0.2",
            "target": "1",
            "label": "1",
            "outcome": "Yes",
        }
    ]
    cols = numeric_model_feature_columns(rows)
    assert "midpoint" in cols
    assert "spread" in cols
    assert "book_imbalance" in cols
    assert "feature_source" not in cols
    assert "source_file" not in cols
    assert "event_type" not in cols
    assert "price_change_side" not in cols
    assert "target" not in cols
    assert "label" not in cols
    assert "outcome" not in cols


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


def test_websocket_prioritises_strategy_v2_forward_evidence_targets(tmp_path, monkeypatch):
    import yaml

    cfg_path = make_cfg(tmp_path)
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    data.setdefault("websocket_market_data", {})
    data["websocket_market_data"].update(
        {
            "use_liquidity_targets": True,
            "use_strategy_v2_targets": True,
            "max_strategy_v2_target_assets": 4,
            "max_liquidity_target_assets": 4,
            "market_ids": [],
            "url": "wss://unit-test.invalid/ws",
        }
    )
    cfg_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    cfg = load_config(cfg_path)
    write_csv(
        cfg.output_root / "polymarket_strategy_v2" / "strategy_v2_forward_evidence.csv",
        [
            {
                "token_id": "strategy-token",
                "family": "macro_rates",
                "signal_cohort": "strategy_v2|macro_rates",
                "latest_status": "shadow_candidate",
                "mark_pnl_usdc": "0.5",
                "latest_risk_adjusted_anchor_edge": "0.1",
                "resolved_evidence": "False",
            },
            {
                "token_id": "resolved-token",
                "family": "macro_rates",
                "signal_cohort": "strategy_v2|macro_rates",
                "latest_status": "shadow_candidate",
                "mark_pnl_usdc": "9.0",
                "latest_risk_adjusted_anchor_edge": "0.2",
                "resolved_evidence": "true",
            },
        ],
    )
    write_csv(
        cfg.output_root / "polymarket_liquidity_discovery" / "liquidity_watchlist.csv",
        [
            {
                "token_id": "liquid-token",
                "family": "sports_other",
                "tradable_liquidity_candidate": "true",
                "liquidity": "1000",
                "spread": "0.02",
                "time_to_close_hours": "12",
            }
        ],
    )

    real_import_module = websocket_collector.importlib.import_module
    subscriptions = []

    def fake_import_module(name):
        if name == "websockets":
            return object()
        return real_import_module(name)

    async def fake_collect(url, seconds, subscription_message, **kwargs):
        subscriptions.append(subscription_message)
        return [{"collected_at_utc": "2026-06-30T10:00:00Z", "message": "{\"asset_id\":\"strategy-token\"}"}]

    monkeypatch.setattr(websocket_collector.importlib, "import_module", fake_import_module)
    monkeypatch.setattr(websocket_collector, "_collect_messages", fake_collect)

    result = collect_websocket(cfg, websocket_seconds=1)
    targets = read_csv_rows(cfg.governance_root / "websocket_liquidity_targets.csv")

    assert result["status"] == "collected"
    assert result["target_source"] == "strategy_v2_forward_evidence+liquidity_watchlist"
    assert result["target_strategy_v2_counts"] == {"strategy_v2|macro_rates": 1}
    assert targets[0]["token_id"] == "strategy-token"
    assert "strategy-token" in subscriptions[0]["assets_ids"]
    assert "resolved-token" not in subscriptions[0]["assets_ids"]


def test_websocket_collector_fails_closed_on_socket_error(tmp_path, monkeypatch):
    cfg = load_config(make_cfg(tmp_path))

    async def fake_collect(*args, **kwargs):
        raise TimeoutError("socket open timed out")

    monkeypatch.setattr(websocket_collector, "_collect_messages", fake_collect)
    result = collect_websocket(cfg, websocket_seconds=1)

    assert result["status"] == "error"
    assert result["new_messages"] == 0
    assert "TimeoutError" in result["reason"]
    summary = read_json(cfg.output_root / "polymarket_websocket" / "websocket_summary.json")
    assert summary["status"] == "error"
