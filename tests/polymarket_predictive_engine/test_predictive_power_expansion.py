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
from polymarket_predictive_engine.utils import read_csv_rows, read_json, write_csv, write_json
import polymarket_predictive_engine.websocket_collector as websocket_collector
from polymarket_predictive_engine.websocket_collector import collect_websocket


def make_cfg(tmp_path: Path, minimum_rows: int = 2, min_category_rows: int = 3) -> Path:
    import yaml

    cfg = tmp_path / "config.yaml"
    data = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8-sig"))

    data.setdefault("paths", {})
    data["paths"]["data_root"] = tmp_path.as_posix()
    data["paths"]["output_root"] = (tmp_path / "outputs").as_posix()
    data["paths"]["database_path"] = (tmp_path / "work" / "paper.sqlite").as_posix()

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
    assert prediction["top_ask_size"] == feature["top_ask_size"]
    assert prediction["ask_depth_1pct"] == feature["ask_depth_1pct"]
    assert prediction["book_imbalance"] == feature["book_imbalance"]


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


def test_websocket_fills_unused_capacity_with_research_liquidity_targets(tmp_path):
    import yaml

    cfg_path = make_cfg(tmp_path)
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    data.setdefault("websocket_market_data", {})
    data["websocket_market_data"].update(
        {
            "use_liquidity_targets": True,
            "target_all_liquid_families": True,
            "max_liquidity_target_assets": 4,
            "max_liquidity_target_assets_per_family": 2,
            "include_research_liquidity_targets": True,
            "max_research_target_assets": 3,
            "max_research_target_assets_per_family": 2,
            "research_min_liquidity": 25,
            "research_max_spread": 0.12,
            "research_max_relative_spread": 0.60,
        }
    )
    cfg_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    cfg = load_config(cfg_path)
    write_csv(
        cfg.output_root / "polymarket_liquidity_discovery" / "liquidity_watchlist.csv",
        [
            {
                "token_id": "strict-token",
                "family": "macro_rates",
                "tradable_liquidity_candidate": "true",
                "liquidity": "1000",
                "spread": "0.01",
                "relative_spread": "0.05",
                "time_to_close_hours": "12",
            },
            {
                "token_id": "research-token-1",
                "family": "sports_other",
                "tradable_liquidity_candidate": "false",
                "liquidity": "500",
                "spread": "0.05",
                "relative_spread": "0.30",
                "time_to_close_hours": "10",
            },
            {
                "token_id": "research-token-2",
                "family": "tennis_tennis_winner",
                "tradable_liquidity_candidate": "false",
                "liquidity": "300",
                "spread": "0.08",
                "relative_spread": "0.40",
                "time_to_close_hours": "20",
            },
            {
                "token_id": "excluded-token",
                "family": "crypto_btc_updown_5m",
                "tradable_liquidity_candidate": "false",
                "liquidity": "1000",
                "spread": "0.01",
                "relative_spread": "0.02",
                "time_to_close_hours": "1",
            },
        ],
    )

    targets = websocket_collector._liquidity_target_rows(cfg, cfg.raw["websocket_market_data"])

    assert targets[0]["token_id"] == "strict-token"
    research_targets = [row for row in targets if row.get("research_liquidity_target") is True]
    assert {row["token_id"] for row in research_targets} == {"research-token-1", "research-token-2"}
    assert all(row.get("websocket_target_reason") == "broader_repricing_learning" for row in research_targets)
    assert "excluded-token" not in {row["token_id"] for row in targets}


def test_websocket_reserves_feedback_broaden_targets_when_price_action_negative(tmp_path):
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
            "feedback_broaden_target_assets": 2,
            "market_ids": [],
        }
    )
    cfg_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    cfg = load_config(cfg_path)
    write_json(
        cfg.governance_root / "price_action_feedback.json",
        {
            "status": "ok",
            "learning_state": "suppress_negative_price_action_and_broaden",
            "collection_queries": ["world cup", "tennis"],
        },
    )
    write_csv(
        cfg.output_root / "polymarket_strategy_v2" / "strategy_v2_forward_evidence.csv",
        [
            {
                "token_id": f"strategy-token-{idx}",
                "family": "crypto_btc_updown_daily",
                "signal_cohort": "strategy_v2|crypto_btc_updown_daily",
                "latest_status": "shadow_candidate",
                "mark_pnl_usdc": str(2.0 - idx * 0.1),
                "latest_risk_adjusted_anchor_edge": "0.1",
                "resolved_evidence": "False",
            }
            for idx in range(4)
        ],
    )
    write_csv(
        cfg.output_root / "polymarket_liquidity_discovery" / "liquidity_watchlist.csv",
        [
            {
                "token_id": "worldcup-token",
                "family": "sports_other",
                "tradable_liquidity_candidate": "true",
                "liquidity": "800",
                "spread": "0.02",
                "time_to_close_hours": "24",
            },
            {
                "token_id": "tennis-token",
                "family": "tennis_match_winner",
                "tradable_liquidity_candidate": "true",
                "liquidity": "700",
                "spread": "0.02",
                "time_to_close_hours": "5",
            },
            {
                "token_id": "plain-token",
                "family": "macro_rates",
                "tradable_liquidity_candidate": "true",
                "liquidity": "1200",
                "spread": "0.01",
                "time_to_close_hours": "3",
                "fast_feedback_liquidity_candidate": "true",
            },
        ],
    )

    targets = websocket_collector._liquidity_target_rows(cfg, cfg.raw["websocket_market_data"])
    token_ids = {row["token_id"] for row in targets}

    assert len(targets) == 4
    assert "worldcup-token" in token_ids
    assert "tennis-token" in token_ids
    assert sum(1 for row in targets if row.get("feedback_broaden_target") is True) == 2


def test_websocket_reserves_paper_confirmation_targets_from_price_action_feedback(tmp_path):
    import yaml

    cfg_path = make_cfg(tmp_path)
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    data.setdefault("websocket_market_data", {})
    data["websocket_market_data"].update(
        {
            "use_liquidity_targets": True,
            "use_strategy_v2_targets": False,
            "max_liquidity_target_assets": 3,
            "feedback_broaden_target_assets": 2,
            "market_ids": [],
        }
    )
    cfg_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    cfg = load_config(cfg_path)
    write_json(
        cfg.governance_root / "price_action_feedback.json",
        {
            "status": "ok",
            "learning_state": "collect_more_positive_price_action_evidence",
            "paper_confirmation_candidates": 2,
            "collection_queries": ["bitcoin", "solana"],
        },
    )
    write_csv(
        cfg.output_root / "polymarket_liquidity_discovery" / "liquidity_watchlist.csv",
        [
            {
                "token_id": "plain-token",
                "family": "macro_rates",
                "tradable_liquidity_candidate": "true",
                "liquidity": "1200",
                "spread": "0.01",
                "time_to_close_hours": "3",
            },
            {
                "token_id": "btc-token",
                "family": "crypto_btc_updown_daily",
                "tradable_liquidity_candidate": "true",
                "liquidity": "800",
                "spread": "0.02",
                "time_to_close_hours": "8",
            },
            {
                "token_id": "sol-token",
                "family": "crypto_sol_updown_daily",
                "tradable_liquidity_candidate": "true",
                "liquidity": "700",
                "spread": "0.02",
                "time_to_close_hours": "6",
            },
        ],
    )

    targets = websocket_collector._liquidity_target_rows(cfg, cfg.raw["websocket_market_data"])
    token_ids = {row["token_id"] for row in targets}

    assert len(targets) == 3
    assert {"btc-token", "sol-token"}.issubset(token_ids)
    assert sum(1 for row in targets if row.get("feedback_broaden_target") is True) == 2


def test_websocket_prefers_updown_rows_for_updown_feedback_queries(tmp_path):
    import yaml

    cfg_path = make_cfg(tmp_path)
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    data.setdefault("websocket_market_data", {})
    data["websocket_market_data"].update(
        {
            "use_liquidity_targets": True,
            "use_strategy_v2_targets": False,
            "max_liquidity_target_assets": 3,
            "feedback_broaden_target_assets": 2,
            "feedback_broaden_target_assets_per_family": 1,
            "market_ids": [],
        }
    )
    cfg_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    cfg = load_config(cfg_path)
    write_json(
        cfg.governance_root / "price_action_feedback.json",
        {
            "status": "ok",
            "learning_state": "collect_more_positive_price_action_evidence",
            "paper_confirmation_candidates": 2,
            "collection_queries": ["bitcoin", "btc updown", "solana updown"],
        },
    )
    write_csv(
        cfg.output_root / "polymarket_liquidity_discovery" / "liquidity_watchlist.csv",
        [
            {
                "token_id": "macro-token",
                "family": "macro_rates",
                "tradable_liquidity_candidate": "true",
                "liquidity": "2000",
                "spread": "0.01",
                "time_to_close_hours": "3",
            },
            {
                "token_id": "btc-special-token",
                "family": "crypto_btc_special",
                "market_slug": "bitcoin-above-58000",
                "question": "Will Bitcoin be above 58000?",
                "tradable_liquidity_candidate": "true",
                "liquidity": "5000",
                "spread": "0.01",
                "time_to_close_hours": "4",
            },
            {
                "token_id": "btc-updown-token",
                "family": "crypto_btc_updown_daily",
                "market_slug": "btc-updown-daily",
                "question": "Bitcoin Up or Down today?",
                "tradable_liquidity_candidate": "true",
                "liquidity": "800",
                "spread": "0.02",
                "time_to_close_hours": "8",
            },
            {
                "token_id": "sol-special-token",
                "family": "crypto_sol_special",
                "market_slug": "will-solana-reach-320",
                "question": "Will Solana reach 320?",
                "tradable_liquidity_candidate": "true",
                "liquidity": "3000",
                "spread": "0.01",
                "time_to_close_hours": "4",
            },
            {
                "token_id": "sol-updown-token",
                "family": "crypto_sol_updown_daily",
                "market_slug": "solana-updown-daily",
                "question": "Solana Up or Down today?",
                "tradable_liquidity_candidate": "true",
                "liquidity": "700",
                "spread": "0.02",
                "time_to_close_hours": "6",
            },
        ],
    )

    targets = websocket_collector._liquidity_target_rows(cfg, cfg.raw["websocket_market_data"])
    feedback_targets = [row for row in targets if row.get("feedback_broaden_target") is True]
    token_ids = {row["token_id"] for row in feedback_targets}

    assert token_ids == {"btc-updown-token", "sol-updown-token"}
    assert {row.get("feedback_broaden_query") for row in feedback_targets} == {"btc updown", "solana updown"}


def test_websocket_reserves_paper_proof_blocker_updown_target_from_research_focus(tmp_path):
    import yaml

    cfg_path = make_cfg(tmp_path)
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    data.setdefault("websocket_market_data", {})
    data["websocket_market_data"].update(
        {
            "use_liquidity_targets": True,
            "use_strategy_v2_targets": False,
            "max_liquidity_target_assets": 3,
            "feedback_broaden_target_assets": 1,
            "feedback_broaden_target_assets_per_family": 1,
            "market_ids": [],
        }
    )
    cfg_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    cfg = load_config(cfg_path)
    write_json(
        cfg.governance_root / "price_action_feedback.json",
        {
            "status": "ok",
            "learning_state": "collect_more_positive_price_action_evidence",
            "paper_confirmation_candidates": 1,
            "collection_queries": ["btc updown"],
        },
    )
    write_json(
        cfg.governance_root / "research_focus.json",
        {
            "status": "ok",
            "collection_queries": ["solana updown", "btc updown", "ethereum"],
            "proof_priority_queries": ["solana updown", "btc updown"],
            "price_action_model": {
                "paper_confirmation_blocker_queries": ["solana updown"],
            },
        },
    )
    write_csv(
        cfg.output_root / "polymarket_liquidity_discovery" / "liquidity_watchlist.csv",
        [
            {
                "token_id": "btc-updown-token",
                "family": "crypto_btc_updown_daily",
                "market_slug": "btc-updown-daily",
                "question": "Bitcoin Up or Down today?",
                "tradable_liquidity_candidate": "true",
                "liquidity": "2000",
                "spread": "0.01",
                "time_to_close_hours": "8",
            },
            {
                "token_id": "sol-updown-token",
                "family": "crypto_sol_updown_daily",
                "market_slug": "solana-updown-daily",
                "question": "Solana Up or Down today?",
                "tradable_liquidity_candidate": "true",
                "liquidity": "700",
                "spread": "0.02",
                "time_to_close_hours": "6",
            },
            {
                "token_id": "sol-special-token",
                "family": "crypto_sol_special",
                "market_slug": "will-solana-reach-320",
                "question": "Will Solana reach 320?",
                "tradable_liquidity_candidate": "true",
                "liquidity": "5000",
                "spread": "0.01",
                "time_to_close_hours": "4",
            },
            {
                "token_id": "macro-token",
                "family": "macro_rates",
                "tradable_liquidity_candidate": "true",
                "liquidity": "3000",
                "spread": "0.01",
                "time_to_close_hours": "3",
            },
        ],
    )

    targets = websocket_collector._liquidity_target_rows(cfg, cfg.raw["websocket_market_data"])
    feedback_targets = [row for row in targets if row.get("feedback_broaden_target") is True]

    assert [row["token_id"] for row in feedback_targets] == ["sol-updown-token"]
    assert feedback_targets[0]["feedback_broaden_query"] == "solana updown"
    assert "sol-updown-token" in {row["token_id"] for row in targets}


def test_websocket_reserves_validation_gap_targets_from_research_focus(tmp_path):
    import yaml

    cfg_path = make_cfg(tmp_path)
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    data.setdefault("websocket_market_data", {})
    data["websocket_market_data"].update(
        {
            "use_liquidity_targets": True,
            "use_strategy_v2_targets": False,
            "max_liquidity_target_assets": 3,
            "feedback_broaden_target_assets": 2,
            "market_ids": [],
        }
    )
    cfg_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    cfg = load_config(cfg_path)
    write_json(
        cfg.governance_root / "price_action_feedback.json",
        {
            "status": "ok",
            "learning_state": "collect_model_validation_gap_price_action_evidence",
            "model_validation_gap_active": True,
            "collection_queries": ["fed", "esports"],
            "model_validation_gap_queries": ["fed", "esports"],
        },
    )
    write_json(
        cfg.governance_root / "research_focus.json",
        {
            "status": "ok",
            "collection_queries": ["fed", "esports"],
            "price_action_model": {
                "validation_gap_needs_collection": True,
                "validation_gap_queries": ["fed", "esports"],
            },
        },
    )
    write_csv(
        cfg.output_root / "polymarket_liquidity_discovery" / "liquidity_watchlist.csv",
        [
            {
                "token_id": "plain-token",
                "family": "crypto_btc_special",
                "tradable_liquidity_candidate": "true",
                "liquidity": "5000",
                "spread": "0.01",
                "time_to_close_hours": "2",
                "fast_feedback_liquidity_candidate": "true",
            },
            {
                "token_id": "fed-token",
                "family": "macro_rates",
                "tradable_liquidity_candidate": "true",
                "liquidity": "800",
                "spread": "0.02",
                "time_to_close_hours": "8",
            },
            {
                "token_id": "esports-token",
                "family": "esports_match",
                "tradable_liquidity_candidate": "true",
                "liquidity": "700",
                "spread": "0.02",
                "time_to_close_hours": "6",
            },
        ],
    )

    targets = websocket_collector._liquidity_target_rows(cfg, cfg.raw["websocket_market_data"])
    token_ids = {row["token_id"] for row in targets}
    feedback_targets = [row for row in targets if row.get("feedback_broaden_target") is True]

    assert len(targets) == 3
    assert {"fed-token", "esports-token"}.issubset(token_ids)
    assert {row["token_id"] for row in feedback_targets} == {"fed-token", "esports-token"}
    assert all(
        "macro_rates" in row["feedback_broaden_family_prefixes"]
        or "esports" in row["feedback_broaden_family_prefixes"]
        for row in feedback_targets
    )


def test_websocket_maps_sharp_sports_queries_to_specific_families(tmp_path):
    import yaml

    cfg_path = make_cfg(tmp_path)
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    data.setdefault("websocket_market_data", {})
    data["websocket_market_data"].update(
        {
            "use_liquidity_targets": True,
            "use_strategy_v2_targets": False,
            "max_liquidity_target_assets": 4,
            "feedback_broaden_target_assets": 3,
            "feedback_broaden_target_assets_per_family": 1,
            "market_ids": [],
        }
    )
    cfg_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    cfg = load_config(cfg_path)
    write_json(
        cfg.governance_root / "price_action_feedback.json",
        {
            "status": "ok",
            "learning_state": "collect_more_positive_price_action_evidence",
            "positive_collect_candidates": 3,
            "collection_queries": ["nba", "mlb", "mma"],
        },
    )
    write_csv(
        cfg.output_root / "polymarket_liquidity_discovery" / "liquidity_watchlist.csv",
        [
            {
                "token_id": "plain-token",
                "family": "sports_other",
                "tradable_liquidity_candidate": "true",
                "liquidity": "2000",
                "spread": "0.01",
                "time_to_close_hours": "3",
            },
            {
                "token_id": "nba-token",
                "family": "basketball_nba_match",
                "market_slug": "nba-celtics-knicks",
                "question": "NBA: Will the Celtics beat the Knicks?",
                "tradable_liquidity_candidate": "true",
                "liquidity": "900",
                "spread": "0.02",
                "time_to_close_hours": "8",
            },
            {
                "token_id": "mlb-token",
                "family": "baseball_mlb_match",
                "market_slug": "mlb-yankees-red-sox",
                "question": "MLB: Will the Yankees beat the Red Sox?",
                "tradable_liquidity_candidate": "true",
                "liquidity": "850",
                "spread": "0.02",
                "time_to_close_hours": "7",
            },
            {
                "token_id": "mma-token",
                "family": "mma_match",
                "market_slug": "ufc-fighter-a-vs-fighter-b",
                "question": "UFC: Will Fighter A beat Fighter B?",
                "tradable_liquidity_candidate": "true",
                "liquidity": "800",
                "spread": "0.02",
                "time_to_close_hours": "6",
            },
        ],
    )

    targets = websocket_collector._liquidity_target_rows(cfg, cfg.raw["websocket_market_data"])
    feedback_targets = [row for row in targets if row.get("feedback_broaden_target") is True]

    assert {row["token_id"] for row in feedback_targets} == {"nba-token", "mlb-token", "mma-token"}
    prefixes_by_query = {row["feedback_broaden_query"]: row["feedback_broaden_family_prefixes"] for row in feedback_targets}
    assert "basketball_nba" in prefixes_by_query["nba"]
    assert "baseball_mlb" in prefixes_by_query["mlb"]
    assert "mma" in prefixes_by_query["mma"]


def test_websocket_reserves_current_positive_analogue_tokens(tmp_path):
    import yaml

    cfg_path = make_cfg(tmp_path)
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    data.setdefault("websocket_market_data", {})
    data["websocket_market_data"].update(
        {
            "use_liquidity_targets": True,
            "use_strategy_v2_targets": False,
            "feedback_broaden_target_enabled": False,
            "include_research_liquidity_targets": False,
            "max_liquidity_target_assets": 2,
            "max_current_positive_analogue_target_assets": 2,
            "market_ids": [],
        }
    )
    cfg_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    cfg = load_config(cfg_path)
    write_json(
        cfg.governance_root / "research_focus.json",
        {
            "status": "ok",
            "price_action_current_positive_analogues": {
                "state": "learning_targets_available",
                "targets": [
                    {
                        "token_id": "fed-analogue-token",
                        "family": "macro_rates",
                        "market_slug": "will-the-fed-increase-rates",
                        "question": "Will the Fed increase rates?",
                        "outcome": "Yes",
                        "latest_bid": 0.49,
                        "latest_ask": 0.50,
                        "latest_spread": 0.01,
                        "validation_roi": 0.004,
                        "robust_validation_roi_gap": 0.026,
                    }
                ],
            },
        },
    )
    write_csv(
        cfg.output_root / "polymarket_liquidity_discovery" / "liquidity_watchlist.csv",
        [
            {
                "token_id": "btc-token",
                "family": "crypto_btc_special",
                "tradable_liquidity_candidate": "true",
                "liquidity": "5000",
                "spread": "0.01",
                "time_to_close_hours": "2",
            },
            {
                "token_id": "eth-token",
                "family": "crypto_eth_special",
                "tradable_liquidity_candidate": "true",
                "liquidity": "4000",
                "spread": "0.01",
                "time_to_close_hours": "2",
            },
        ],
    )

    targets = websocket_collector._liquidity_target_rows(cfg, cfg.raw["websocket_market_data"])
    token_ids = [row["token_id"] for row in targets]
    analogue_targets = [row for row in targets if row.get("current_positive_analogue_target") is True]

    assert token_ids[0] == "fed-analogue-token"
    assert len(targets) == 2
    assert len(analogue_targets) == 1
    assert analogue_targets[0]["websocket_target_reason"] == "reserve_current_positive_analogue_for_forward_bid_tracking"


def test_websocket_reserves_in_band_paper_confirmation_blocker_tokens(tmp_path):
    import yaml

    cfg_path = make_cfg(tmp_path)
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    data.setdefault("websocket_market_data", {})
    data["websocket_market_data"].update(
        {
            "use_liquidity_targets": True,
            "use_strategy_v2_targets": False,
            "feedback_broaden_target_enabled": False,
            "include_research_liquidity_targets": False,
            "max_liquidity_target_assets": 2,
            "max_paper_confirmation_blocker_target_assets": 2,
            "market_ids": [],
        }
    )
    cfg_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    cfg = load_config(cfg_path)
    write_json(
        cfg.governance_root / "research_focus.json",
        {
            "status": "ok",
            "price_action_paper_confirmation_blockers": {
                "state": "in_band_historical_analogue_gaps",
                "targets": [
                    {
                        "family": "crypto_btc_updown_event",
                        "market_slug": "bitcoin-up-or-down-july-4-2026",
                        "question": "Bitcoin Up or Down - July 4?",
                        "outcome": "Up",
                        "latest_bid": 0.49,
                        "latest_ask": 0.50,
                        "latest_spread": 0.01,
                        "recommended_collection_query": "btc updown",
                        "historical_analogue_gate": "no_positive_historical_analogue_examples",
                        "historical_analogue_key": "crypto_btc_updown_event|ask=40-60c|spread=<=1c|side=",
                        "historical_analogue_validation_rows": 4,
                        "historical_analogue_positive_rows": 0,
                        "historical_analogue_validation_roi": -0.02,
                        "decision_use": "in_band_historical_analogue_gap_collection_target",
                        "entry_band_wait": False,
                    },
                    {
                        "token_id": "proof-entry-band-wait-token",
                        "family": "crypto_btc_updown_event",
                        "market_slug": "bitcoin-high-priced-favourite",
                        "question": "Bitcoin Up or Down - July 4?",
                        "outcome": "Up",
                        "latest_bid": 0.99,
                        "latest_ask": 0.995,
                        "latest_spread": 0.005,
                        "recommended_collection_query": "btc updown",
                        "historical_analogue_gate": "positive_historical_analogue",
                        "decision_use": "entry_price_band_wait_do_not_chase_until_quote_enters_risk_band",
                        "entry_band_wait": True,
                    },
                ],
            },
        },
    )
    write_csv(
        cfg.output_root / "polymarket_liquidity_discovery" / "liquidity_watchlist.csv",
        [
            {
                "token_id": "proof-in-band-token",
                "family": "crypto_btc_updown_event",
                "market_slug": "bitcoin-up-or-down-july-4-2026",
                "question": "Bitcoin Up or Down - July 4?",
                "outcome": "Up",
                "best_bid": "0.49",
                "best_ask": "0.50",
                "tradable_liquidity_candidate": "true",
                "liquidity": "2500",
                "spread": "0.01",
                "time_to_close_hours": "2",
            },
            {
                "token_id": "fallback-token",
                "family": "crypto_btc_special",
                "tradable_liquidity_candidate": "true",
                "liquidity": "5000",
                "spread": "0.01",
                "time_to_close_hours": "2",
            },
        ],
    )

    targets = websocket_collector._liquidity_target_rows(cfg, cfg.raw["websocket_market_data"])
    token_ids = [row["token_id"] for row in targets]
    proof_targets = [row for row in targets if row.get("paper_confirmation_blocker_target") is True]

    assert token_ids[0] == "proof-in-band-token"
    assert "proof-entry-band-wait-token" not in token_ids
    assert len(proof_targets) == 1
    assert proof_targets[0]["paper_confirmation_blocker_gate"] == "no_positive_historical_analogue_examples"
    assert proof_targets[0]["paper_confirmation_blocker_query"] == "btc updown"
    assert proof_targets[0]["websocket_target_match_source"] == "slug_outcome_artifact"
    assert proof_targets[0]["websocket_target_reason"] == "reserve_paper_confirmation_blocker_for_forward_bid_tracking"


def test_websocket_resolves_paper_confirmation_blocker_tokens_from_predictions(tmp_path):
    import yaml

    cfg_path = make_cfg(tmp_path)
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    data.setdefault("websocket_market_data", {})
    data["websocket_market_data"].update(
        {
            "use_liquidity_targets": True,
            "use_strategy_v2_targets": False,
            "feedback_broaden_target_enabled": False,
            "include_research_liquidity_targets": False,
            "max_liquidity_target_assets": 2,
            "max_paper_confirmation_blocker_target_assets": 2,
            "market_ids": [],
        }
    )
    cfg_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    cfg = load_config(cfg_path)
    write_json(
        cfg.governance_root / "research_focus.json",
        {
            "status": "ok",
            "price_action_paper_confirmation_blockers": {
                "state": "in_band_historical_analogue_gaps",
                "targets": [
                    {
                        "family": "worldcup",
                        "market_slug": "will-north-america-win-the-world-cup",
                        "question": "Will North America win the World Cup?",
                        "outcome": "Yes",
                        "latest_bid": 0.052,
                        "latest_ask": 0.053,
                        "latest_spread": 0.001,
                        "recommended_collection_query": "world cup",
                        "historical_analogue_gate": "no_positive_historical_analogue_examples",
                        "historical_analogue_key": "worldcup|ask=5-10c|spread=<=0.1c|side=SELL",
                        "decision_use": "in_band_historical_analogue_gap_collection_target",
                        "entry_band_wait": False,
                    },
                ],
            },
        },
    )
    write_csv(
        cfg.output_root / "polymarket_liquidity_discovery" / "liquidity_watchlist.csv",
        [
            {
                "token_id": "fallback-token",
                "family": "crypto_btc_special",
                "tradable_liquidity_candidate": "true",
                "liquidity": "5000",
                "spread": "0.01",
                "time_to_close_hours": "2",
            },
        ],
    )
    write_csv(
        cfg.output_root / "polymarket_predictions" / "predictions.csv",
        [
            {
                "token_id": "prediction-proof-token",
                "family": "worldcup",
                "category": "worldcup",
                "market_slug": "will-north-america-win-the-world-cup",
                "question": "Will North America win the World Cup?",
                "outcome": "Yes",
                "best_bid": "0.052",
                "best_ask": "0.053",
                "spread": "0.001",
                "liquidity": "320",
            },
        ],
    )

    targets = websocket_collector._liquidity_target_rows(cfg, cfg.raw["websocket_market_data"])
    proof_targets = [row for row in targets if row.get("paper_confirmation_blocker_target") is True]

    assert [row["token_id"] for row in proof_targets] == ["prediction-proof-token"]
    assert proof_targets[0]["paper_confirmation_blocker_query"] == "world cup"
    assert proof_targets[0]["websocket_target_match_source"] == "slug_outcome_artifact"


def test_websocket_merges_position_and_paper_confirmation_blocker_metadata(tmp_path):
    import yaml

    cfg_path = make_cfg(tmp_path)
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    data.setdefault("websocket_market_data", {})
    data["websocket_market_data"].update(
        {
            "use_liquidity_targets": True,
            "use_strategy_v2_targets": False,
            "feedback_broaden_target_enabled": False,
            "include_research_liquidity_targets": False,
            "max_liquidity_target_assets": 3,
            "max_paper_confirmation_blocker_target_assets": 2,
            "position_token_slots": 3,
            "market_ids": [],
        }
    )
    cfg_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    cfg = load_config(cfg_path)
    write_csv(
        cfg.output_root / "polymarket_shadow" / "shadow_positions.csv",
        [
            {
                "shadow_position_id": "shadow-proof",
                "status": "open",
                "market_id": "worldcup-market",
                "token_id": "shared-proof-token",
                "market_slug": "will-north-america-win-the-world-cup",
                "question": "Will North America win the World Cup?",
                "outcome": "Yes",
                "category": "worldcup",
                "close_time": "2099-01-01T00:00:00Z",
                "quantity": "10",
            }
        ],
    )
    write_json(
        cfg.governance_root / "research_focus.json",
        {
            "status": "ok",
            "price_action_paper_confirmation_blockers": {
                "state": "in_band_historical_analogue_gaps",
                "targets": [
                    {
                        "token_id": "shared-proof-token",
                        "family": "worldcup",
                        "market_slug": "will-north-america-win-the-world-cup",
                        "question": "Will North America win the World Cup?",
                        "outcome": "Yes",
                        "latest_bid": 0.052,
                        "latest_ask": 0.053,
                        "latest_spread": 0.001,
                        "recommended_collection_query": "world cup",
                        "historical_analogue_gate": "no_positive_historical_analogue_examples",
                        "historical_analogue_key": "worldcup|ask=5-10c|spread=<=0.1c|side=SELL",
                        "decision_use": "in_band_historical_analogue_gap_collection_target",
                        "entry_band_wait": False,
                    },
                ],
            },
        },
    )
    write_csv(
        cfg.output_root / "polymarket_liquidity_discovery" / "liquidity_watchlist.csv",
        [
            {
                "token_id": "fallback-token",
                "family": "crypto_btc_special",
                "tradable_liquidity_candidate": "true",
                "liquidity": "5000",
                "spread": "0.01",
                "time_to_close_hours": "2",
            },
        ],
    )

    targets = websocket_collector._liquidity_target_rows(cfg, cfg.raw["websocket_market_data"])
    shared = next(row for row in targets if row["token_id"] == "shared-proof-token")

    assert shared["open_position_target"] is True
    assert shared["paper_confirmation_blocker_target"] is True
    assert shared["position_source"] == "shadow_position"
    assert shared["paper_confirmation_blocker_query"] == "world cup"
    assert shared["websocket_target_reason"] == (
        "open_position+reserve_paper_confirmation_blocker_for_forward_bid_tracking"
    )


def test_websocket_reserves_open_position_tokens_before_discovery(tmp_path, monkeypatch):
    import yaml

    cfg_path = make_cfg(tmp_path)
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    data.setdefault("websocket_market_data", {})
    data["websocket_market_data"].update(
        {
            "use_liquidity_targets": True,
            "use_strategy_v2_targets": False,
            "feedback_broaden_target_enabled": False,
            "include_research_liquidity_targets": False,
            "max_liquidity_target_assets": 4,
            "position_token_slots": 10,
            "position_grace_hours": 6,
            "market_ids": [],
        }
    )
    cfg_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    cfg = load_config(cfg_path)
    write_csv(
        cfg.output_root / "polymarket_shadow" / "shadow_positions.csv",
        [
            {
                "shadow_position_id": "shadow-1",
                "status": "open",
                "market_id": "shadow-market",
                "token_id": "shadow-position-token",
                "market_slug": "shadow-position-market",
                "outcome": "Yes",
                "category": "macro_rates",
                "close_time": "2099-01-01T00:00:00Z",
                "quantity": "10",
            }
        ],
    )
    write_csv(
        cfg.output_root / "polymarket_portfolio" / "positions.csv",
        [
            {
                "position_id": "paper-1",
                "status": "open",
                "market_id": "paper-market",
                "token_id": "paper-position-token",
                "side": "BUY_YES",
                "quantity": "5",
                "category": "",
            }
        ],
    )
    write_csv(
        cfg.output_root / "polymarket_portfolio" / "paper_orders.csv",
        [
            {
                "order_id": "paper-order-1",
                "created_at": "2026-07-03T00:00:00Z",
                "mode": "paper",
                "market_id": "paper-market",
                "token_id": "paper-position-token",
                "source_signal_json": json.dumps(
                    {
                        "market_id": "paper-market",
                        "token_id": "paper-position-token",
                        "market_slug": "paper-position-market",
                        "outcome": "Yes",
                        "category": "crypto_eth_updown_daily",
                        "close_time": "2099-01-02T00:00:00Z",
                    },
                    sort_keys=True,
                ),
            }
        ],
    )
    write_csv(
        cfg.output_root / "polymarket_liquidity_discovery" / "liquidity_watchlist.csv",
        [
            {
                "token_id": f"discovery-token-{idx}",
                "family": "crypto_btc_special",
                "tradable_liquidity_candidate": "true",
                "liquidity": str(5000 - idx),
                "spread": "0.01",
                "time_to_close_hours": "2",
            }
            for idx in range(4)
        ],
    )

    subscriptions = []
    real_import_module = websocket_collector.importlib.import_module

    def fake_import_module(name):
        if name == "websockets":
            return object()
        return real_import_module(name)

    async def fake_collect(url, seconds, subscription_message, **kwargs):
        subscriptions.append(subscription_message)
        return [{"collected_at_utc": "2026-07-03T00:00:00Z", "message": "{\"asset_id\":\"ok\"}"}]

    monkeypatch.setattr(websocket_collector.importlib, "import_module", fake_import_module)
    monkeypatch.setattr(websocket_collector, "_collect_messages", fake_collect)

    result = collect_websocket(cfg, websocket_seconds=1)
    targets = read_csv_rows(cfg.governance_root / "websocket_liquidity_targets.csv")
    token_ids = [row["token_id"] for row in targets]

    assert result["status"] == "collected"
    assert result["target_source"].startswith("open_positions+")
    assert result["target_position_counts"] == {"paper_position": 1, "shadow_position": 1}
    assert token_ids[:2] == ["shadow-position-token", "paper-position-token"]
    assert len(token_ids) == 4
    assert all(row["selection_reason"] == "open_position" for row in targets[:2])
    assert all(row["websocket_target_reason"] == "open_position" for row in targets[:2])
    assert targets[1]["close_time"] == "2099-01-02T00:00:00Z"
    assert subscriptions[0]["assets_ids"][:2] == ["shadow-position-token", "paper-position-token"]


def test_position_tokens_drop_positions_after_grace_window(tmp_path):
    import yaml

    cfg_path = make_cfg(tmp_path)
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    data.setdefault("websocket_market_data", {})
    data["websocket_market_data"].update(
        {
            "position_grace_hours": 6,
            "include_position_tokens_without_close_time": False,
        }
    )
    cfg_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    cfg = load_config(cfg_path)
    write_csv(
        cfg.output_root / "polymarket_shadow" / "shadow_positions.csv",
        [
            {
                "shadow_position_id": "future",
                "status": "open",
                "market_id": "future-market",
                "token_id": "future-token",
                "close_time": "2026-07-03T14:00:00Z",
                "quantity": "1",
            },
            {
                "shadow_position_id": "within",
                "status": "open",
                "market_id": "within-market",
                "token_id": "within-grace-token",
                "close_time": "2026-07-03T08:00:00Z",
                "quantity": "1",
            },
            {
                "shadow_position_id": "expired",
                "status": "open",
                "market_id": "expired-market",
                "token_id": "expired-token",
                "close_time": "2026-07-03T05:00:00Z",
                "quantity": "1",
            },
            {
                "shadow_position_id": "missing",
                "status": "open",
                "market_id": "missing-market",
                "token_id": "missing-close-token",
                "close_time": "",
                "quantity": "1",
            },
        ],
    )

    targets = websocket_collector.position_tokens(cfg, as_of="2026-07-03T12:00:00Z")
    token_ids = {row["token_id"] for row in targets}

    assert token_ids == {"within-grace-token", "future-token"}
    assert {row["position_close_state"] for row in targets} == {"within_grace", "future_close"}


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
