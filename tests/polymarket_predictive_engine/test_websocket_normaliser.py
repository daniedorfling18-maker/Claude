import json

from pytest import approx

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.websocket_normaliser import (
    FEATURE_FIELDS,
    FORBIDDEN_FEATURE_FIELDS,
    normalize_websocket_file,
    normalize_websocket_message,
)
from polymarket_predictive_engine.utils import read_csv_rows, write_csv

BOOK_EVENT = {
    "event_type": "book",
    "asset_id": "tok-1",
    "market": "0xmarket",
    "timestamp": "1718900000000",
    "bids": [
        {"price": "0.50", "size": "100"},
        {"price": "0.49", "size": "200"},
        {"price": "0.45", "size": "500"},
    ],
    "asks": [
        {"price": "0.52", "size": "150"},
        {"price": "0.53", "size": "300"},
        {"price": "0.60", "size": "400"},
    ],
}

PRICE_CHANGE_EVENT = {
    "event_type": "price_change",
    "asset_id": "tok-1",
    "market": "0xmarket",
    "timestamp": "1718900001000",
    "price_changes": [
        {"price": "0.53", "side": "SELL", "size": "250", "best_bid": "0.50", "best_ask": "0.52"},
    ],
}

NESTED_ASSET_PRICE_CHANGE_EVENT = {
    "event_type": "price_change",
    "market": "0xmarket",
    "timestamp": "1718900001000",
    "price_changes": [
        {"asset_id": "tok-yes", "price": "0.53", "side": "SELL", "size": "250", "best_bid": "0.50", "best_ask": "0.52"},
        {"asset_id": "tok-no", "price": "0.47", "side": "BUY", "size": "125", "best_bid": "0.48", "best_ask": "0.50"},
    ],
}


def test_book_event_produces_full_feature_row():
    features, quality = normalize_websocket_message(json.dumps(BOOK_EVENT), collected_at_utc="2026-06-24T00:00:00Z")
    assert len(features) == 1
    row = features[0]
    assert row["event_type"] == "book"
    assert row["market"] == "0xmarket"
    assert row["asset_id"] == "tok-1"
    assert row["source_timestamp"] == "1718900000000"
    assert row["collected_at_utc"] == "2026-06-24T00:00:00Z"
    assert row["best_bid"] == approx(0.50)
    assert row["best_ask"] == approx(0.52)
    assert row["midpoint"] == approx(0.51)
    assert row["spread"] == approx(0.02)
    assert row["top_bid_size"] == approx(100)
    assert row["top_ask_size"] == approx(150)
    assert row["bid_depth_1pct"] == approx(100)
    assert row["ask_depth_1pct"] == approx(150)
    assert row["bid_depth_5pct"] == approx(300)
    assert row["ask_depth_5pct"] == approx(450)
    assert row["book_imbalance"] == approx(-0.2)
    # a book event carries no incremental price-change fields
    assert row["price_change_side"] is None
    assert row["price_change_price"] is None
    # no quality issues for a clean two-sided book
    assert quality == []


def test_price_change_event_produces_top_of_book_row():
    features, quality = normalize_websocket_message(json.dumps(PRICE_CHANGE_EVENT), collected_at_utc="2026-06-24T00:00:01Z")
    assert len(features) == 1
    row = features[0]
    assert row["event_type"] == "price_change"
    assert row["asset_id"] == "tok-1"
    assert row["best_bid"] == approx(0.50)
    assert row["best_ask"] == approx(0.52)
    assert row["midpoint"] == approx(0.51)
    assert row["spread"] == approx(0.02)
    assert row["price_change_side"] == "SELL"
    assert row["price_change_price"] == approx(0.53)
    assert row["price_change_size"] == approx(250)
    assert quality == []


def test_nested_price_change_asset_ids_are_preserved_per_change():
    features, quality = normalize_websocket_message(json.dumps(NESTED_ASSET_PRICE_CHANGE_EVENT), collected_at_utc="2026-06-24T00:00:01Z")
    assert len(features) == 2
    assert [row["asset_id"] for row in features] == ["tok-yes", "tok-no"]
    assert [row["market"] for row in features] == ["0xmarket", "0xmarket"]
    assert [row["price_change_side"] for row in features] == ["SELL", "BUY"]
    assert quality == []


def test_array_message_with_two_book_events_yields_two_rows():
    second = dict(BOOK_EVENT, asset_id="tok-2", timestamp="1718900002000")
    features, quality = normalize_websocket_message(json.dumps([BOOK_EVENT, second]))
    assert len(features) == 2
    assert [r["asset_id"] for r in features] == ["tok-1", "tok-2"]
    assert all(r["event_type"] == "book" for r in features)
    assert quality == []


def test_malformed_json_message_logs_quality_row_without_crashing():
    features, quality = normalize_websocket_message("{not valid json", collected_at_utc="2026-06-24T00:00:02Z")
    assert features == []
    assert len(quality) == 1
    assert quality[0]["issue_type"] == "malformed_json"
    assert quality[0]["collected_at_utc"] == "2026-06-24T00:00:02Z"


def test_missing_ask_side_is_null_safe_with_quality_warning():
    one_sided = {
        "event_type": "book",
        "asset_id": "tok-1",
        "market": "0xmarket",
        "timestamp": "1718900003000",
        "bids": [{"price": "0.50", "size": "100"}],
        "asks": [],
    }
    features, quality = normalize_websocket_message(json.dumps(one_sided))
    assert any(q["issue_type"] == "missing_order_book_side" for q in quality)
    # null-safe: a row may still be emitted, but the absent side stays empty
    assert len(features) == 1
    row = features[0]
    assert row["best_bid"] == approx(0.50)
    assert row["best_ask"] is None
    assert row["midpoint"] is None
    assert row["spread"] is None


def test_output_files_are_feature_only_no_label_columns(tmp_path):
    cfg = EngineConfig(raw={"paths": {"output_root": str(tmp_path)}}, path=tmp_path / "cfg.yaml")
    messages = [
        {"collected_at_utc": "2026-06-24T00:00:00Z", "message": json.dumps(BOOK_EVENT)},
        {"collected_at_utc": "2026-06-24T00:00:01Z", "message": json.dumps(PRICE_CHANGE_EVENT)},
        {"collected_at_utc": "2026-06-24T00:00:02Z", "message": "{bad json"},
    ]
    input_path = tmp_path / "websocket_messages.json"
    input_path.write_text(json.dumps(messages))

    features, quality, summary = normalize_websocket_file(cfg, input_path=input_path)

    feat_csv = tmp_path / "polymarket_training" / "websocket_market_features.csv"
    qual_csv = tmp_path / "polymarket_model_governance" / "websocket_feature_quality_report.csv"
    summ_json = tmp_path / "polymarket_model_governance" / "websocket_feature_summary.json"
    assert feat_csv.exists() and qual_csv.exists() and summ_json.exists()

    header = feat_csv.read_text(encoding="utf-8").splitlines()[0].lower()
    for forbidden in FORBIDDEN_FEATURE_FIELDS:
        assert forbidden not in header
    # explicit check on the critical leakage columns
    for banned in ("target", "winner", "resolution", "resolved", "settled", "payout"):
        assert banned not in header

    assert any(r["event_type"] == "book" for r in features)
    assert any(q["issue_type"] == "malformed_json" for q in quality)
    assert summary["feature_rows"] == len(features)
    assert summary["label_columns_present"] == []
    assert set(FEATURE_FIELDS) >= {
        "collected_at_utc", "source_timestamp", "market", "asset_id", "event_type",
        "best_bid", "best_ask", "midpoint", "spread", "last_trade_price",
        "top_bid_size", "top_ask_size", "bid_depth_1pct", "ask_depth_1pct",
        "bid_depth_5pct", "ask_depth_5pct", "book_imbalance",
        "price_change_side", "price_change_price", "price_change_size",
    }


def test_normaliser_retains_existing_feature_history_for_multi_day_repricing(tmp_path):
    cfg = EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path)},
            "websocket_market_data": {
                "retain_existing_features": True,
                "feature_retention_hours": 96,
                "max_feature_rows": 10,
            },
        },
        path=tmp_path / "cfg.yaml",
    )
    existing_row = {
        "collected_at_utc": "2026-06-21T00:00:00Z",
        "source_timestamp": "1718800000000",
        "market": "old-market",
        "asset_id": "old-token",
        "event_type": "price_change",
        "best_bid": "0.41",
        "best_ask": "0.43",
        "midpoint": "0.42",
        "spread": "0.02",
        "price_change_side": "BUY",
        "price_change_price": "0.42",
        "price_change_size": "50",
        "outcome": "Yes",
    }
    features_path = tmp_path / "polymarket_training" / "websocket_market_features.csv"
    write_csv(features_path, [existing_row], fieldnames=[*FEATURE_FIELDS, "outcome"])
    input_path = tmp_path / "websocket_messages.json"
    input_path.write_text(
        json.dumps([
            {"collected_at_utc": "2026-06-24T00:00:00Z", "message": json.dumps(BOOK_EVENT)},
        ]),
        encoding="utf-8",
    )

    features, _, summary = normalize_websocket_file(cfg, input_path=input_path)
    persisted = read_csv_rows(features_path)

    assert {row["asset_id"] for row in persisted} == {"old-token", "tok-1"}
    assert {str(row["asset_id"]) for row in features} == {"old-token", "tok-1"}
    assert summary["existing_feature_rows"] == 1
    assert summary["new_feature_rows"] == 1
    assert summary["retained_feature_rows"] == 2
    assert summary["feature_retention_enabled"] is True
    assert "outcome" not in persisted[0]


def test_normaliser_can_return_latest_rows_while_retaining_history(tmp_path):
    cfg = EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path)},
            "websocket_market_data": {
                "retain_existing_features": True,
                "feature_retention_hours": 96,
                "max_feature_rows": 10,
                "return_latest_features_only": True,
            },
        },
        path=tmp_path / "cfg.yaml",
    )
    existing_row = {
        "collected_at_utc": "2026-06-23T00:00:00Z",
        "source_timestamp": "1718800000000",
        "market": "old-market",
        "asset_id": "old-token",
        "event_type": "price_change",
        "best_bid": "0.41",
        "best_ask": "0.43",
        "midpoint": "0.42",
        "spread": "0.02",
        "price_change_side": "BUY",
        "price_change_price": "0.42",
        "price_change_size": "50",
    }
    features_path = tmp_path / "polymarket_training" / "websocket_market_features.csv"
    write_csv(features_path, [existing_row], fieldnames=FEATURE_FIELDS)
    input_path = tmp_path / "websocket_messages_latest.json"
    input_path.write_text(
        json.dumps([
            {"collected_at_utc": "2026-06-24T00:00:00Z", "message": json.dumps(BOOK_EVENT)},
        ]),
        encoding="utf-8",
    )

    features, _, summary = normalize_websocket_file(cfg, input_path=input_path)
    persisted = read_csv_rows(features_path)

    assert {row["asset_id"] for row in persisted} == {"old-token", "tok-1"}
    assert {str(row["asset_id"]) for row in features} == {"tok-1"}
    assert summary["feature_rows"] == 2
    assert summary["returned_feature_rows"] == 1
    assert summary["return_latest_features_only"] is True


def test_normaliser_can_skip_retained_history_rewrite_between_live_ticks(tmp_path):
    cfg = EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path)},
            "websocket_market_data": {
                "retain_existing_features": True,
                "feature_retention_hours": 96,
                "max_feature_rows": 10,
                "return_latest_features_only": True,
                "feature_retention_write_interval_seconds": 999999,
            },
        },
        path=tmp_path / "cfg.yaml",
    )
    existing_row = {
        "collected_at_utc": "2026-06-23T00:00:00Z",
        "source_timestamp": "1718800000000",
        "market": "old-market",
        "asset_id": "old-token",
        "event_type": "price_change",
        "best_bid": "0.41",
        "best_ask": "0.43",
        "midpoint": "0.42",
        "spread": "0.02",
        "price_change_side": "BUY",
        "price_change_price": "0.42",
        "price_change_size": "50",
    }
    features_path = tmp_path / "polymarket_training" / "websocket_market_features.csv"
    summary_path = tmp_path / "polymarket_model_governance" / "websocket_feature_summary.json"
    write_csv(features_path, [existing_row], fieldnames=FEATURE_FIELDS)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(
            {
                "collected_at_utc": "2999-01-01T00:00:00Z",
                "feature_retention_last_write_utc": "2999-01-01T00:00:00Z",
                "feature_rows": 1,
                "retained_feature_rows": 1,
                "event_type_counts": {"price_change": 1},
                "category_counts": {"unknown": 1},
            }
        ),
        encoding="utf-8",
    )
    input_path = tmp_path / "websocket_messages_latest.json"
    input_path.write_text(
        json.dumps([
            {"collected_at_utc": "2026-06-24T00:00:00Z", "message": json.dumps(BOOK_EVENT)},
        ]),
        encoding="utf-8",
    )

    features, _, summary = normalize_websocket_file(cfg, input_path=input_path)
    persisted = read_csv_rows(features_path)

    assert {str(row["asset_id"]) for row in features} == {"tok-1"}
    assert {str(row["asset_id"]) for row in persisted} == {"old-token"}
    assert summary["feature_retention_write_skipped"] is True
    assert summary["feature_rows"] == 1
    assert summary["returned_feature_rows"] == 1


def test_retained_history_rewrite_updates_last_write_timestamp(tmp_path):
    cfg = EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path)},
            "websocket_market_data": {
                "retain_existing_features": True,
                "max_feature_rows": 10,
                "return_latest_features_only": True,
                "feature_retention_write_interval_seconds": 1,
            },
        },
        path=tmp_path / "cfg.yaml",
    )
    features_path = tmp_path / "polymarket_training" / "websocket_market_features.csv"
    summary_path = tmp_path / "polymarket_model_governance" / "websocket_feature_summary.json"
    write_csv(
        features_path,
        [
            {
                "collected_at_utc": "2026-06-23T00:00:00Z",
                "source_timestamp": "1718800000000",
                "market": "old-market",
                "asset_id": "old-token",
                "event_type": "price_change",
                "best_bid": "0.41",
                "best_ask": "0.43",
                "midpoint": "0.42",
                "spread": "0.02",
            }
        ],
        fieldnames=FEATURE_FIELDS,
    )
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(
            {
                "collected_at_utc": "2000-01-01T00:00:00Z",
                "feature_retention_last_write_utc": "2000-01-01T00:00:00Z",
                "feature_rows": 1,
            }
        ),
        encoding="utf-8",
    )
    input_path = tmp_path / "websocket_messages_latest.json"
    input_path.write_text(
        json.dumps([
            {"collected_at_utc": "2026-06-24T00:00:00Z", "message": json.dumps(BOOK_EVENT)},
        ]),
        encoding="utf-8",
    )

    _, _, summary = normalize_websocket_file(cfg, input_path=input_path)

    assert summary["feature_retention_write_skipped"] is False
    assert summary["feature_retention_last_write_utc"] != "2000-01-01T00:00:00Z"


def test_minimum_required_columns_present():
    required = {
        "collected_at_utc", "source_timestamp", "market", "asset_id", "event_type",
        "best_bid", "best_ask", "midpoint", "spread", "last_trade_price",
        "top_bid_size", "top_ask_size", "bid_depth_1pct", "ask_depth_1pct",
        "bid_depth_5pct", "ask_depth_5pct", "book_imbalance",
        "price_change_side", "price_change_price", "price_change_size",
    }
    assert required <= set(FEATURE_FIELDS)
    assert len(FEATURE_FIELDS) >= 20


def test_expiring_feature_rows_are_archived_not_destroyed(tmp_path):
    """2026-07-09: rows leaving the live retention window were silently deleted -
    training substrate destroyed daily. They must land in the compressed archive
    (deduplicated, disk-capped) before being dropped from the live table."""
    import gzip

    from polymarket_predictive_engine.config import EngineConfig
    from polymarket_predictive_engine.websocket_normaliser import _retained_feature_rows

    cfg = EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "websocket_market_data": {
                "retain_existing_features": True,
                "feature_retention_hours": 24,
                "max_feature_rows": 0,
                "training_archive_enabled": True,
                "training_archive_max_mb": 100,
            },
        },
        path=tmp_path / "cfg.yaml",
    )
    features_path = tmp_path / "features.csv"
    old_row = {"collected_at_utc": "2026-07-01T00:00:00Z", "source_timestamp": "1", "market": "m", "asset_id": "a", "best_bid": "0.4"}
    fresh_row = {"collected_at_utc": "2026-07-09T00:00:00Z", "source_timestamp": "2", "market": "m", "asset_id": "a", "best_bid": "0.5"}

    retained, diagnostics = _retained_feature_rows(cfg, features_path=features_path, new_features=[old_row, fresh_row])

    assert [row["collected_at_utc"] for row in retained] == ["2026-07-09T00:00:00Z"]
    archive = diagnostics["training_archive"]
    assert archive["archived_rows"] == 1
    archive_file = archive["archive_file"]
    with gzip.open(archive_file, "rt", encoding="utf-8") as handle:
        content = handle.read()
    assert "2026-07-01T00:00:00Z" in content

    # Disabled flag must skip archiving without touching retention behaviour.
    cfg.raw["websocket_market_data"]["training_archive_enabled"] = False
    retained2, diagnostics2 = _retained_feature_rows(cfg, features_path=features_path, new_features=[old_row, fresh_row])
    assert diagnostics2["training_archive"]["archived_rows"] == 0
    assert [row["collected_at_utc"] for row in retained2] == ["2026-07-09T00:00:00Z"]


def test_training_archive_size_cap_deletes_oldest(tmp_path):
    from polymarket_predictive_engine.config import EngineConfig
    from polymarket_predictive_engine.websocket_normaliser import _archive_expiring_features

    cfg = EngineConfig(
        raw={"paths": {"output_root": str(tmp_path / "outputs")}},
        path=tmp_path / "cfg.yaml",
    )
    settings = {"training_archive_enabled": True, "training_archive_max_mb": 0.000001}
    rows = [{"collected_at_utc": f"2026-07-0{i}T00:00:00Z", "source_timestamp": str(i), "market": "m", "asset_id": "a"} for i in range(1, 6)]

    first = _archive_expiring_features(cfg, settings, rows[:2])
    second = _archive_expiring_features(cfg, settings, rows[2:])

    assert first["archived_rows"] == 2
    assert second["archived_rows"] == 3
    # The tiny cap forces the oldest archive out once the second lands.
    assert second["oldest_archives_deleted_for_cap"] >= 1
