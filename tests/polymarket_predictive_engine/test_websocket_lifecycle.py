from __future__ import annotations

import json
from pathlib import Path

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.utils import read_csv_rows, read_json
import polymarket_predictive_engine.websocket_collector as websocket_collector
from polymarket_predictive_engine.websocket_normaliser import normalize_websocket_message


def _cfg(tmp_path: Path, *, custom_feature_enabled: bool) -> EngineConfig:
    subscription = {"assets_ids": ["token-1"], "type": "market"}
    if custom_feature_enabled:
        subscription["custom_feature_enabled"] = True
    return EngineConfig(
        raw={
            "paths": {
                "data_root": str(tmp_path),
                "output_root": str(tmp_path / "outputs"),
                "database_path": str(tmp_path / "work" / "paper.sqlite"),
            },
            "websocket_market_data": {
                "url": "wss://unit-test.invalid/ws",
                "market_ids": ["token-1"],
                "use_liquidity_targets": False,
                "subscription_message": subscription,
            },
        },
        path=tmp_path / "config.yaml",
    )


def _new_market(*, suffix: str = "1") -> dict[str, object]:
    return {
        "event_type": "new_market",
        "id": f"birth-event-{suffix}",
        "timestamp": 1783706400000,
        "market": f"market-{suffix}",
        "condition_id": f"condition-{suffix}",
        "question": f"Will Team {suffix} win?",
        "slug": f"team-{suffix}-win",
        "description": "Fixture market",
        "assets_ids": [f"yes-{suffix}", f"no-{suffix}"],
        "outcomes": ["Yes", "No"],
        "clob_token_ids": [f"yes-{suffix}", f"no-{suffix}"],
        "tags": ["sports", "football"],
        "active": True,
        "sports_market_type": "moneyline",
        "line": 0.5,
        "game_start_time": "2026-07-12T18:00:00Z",
        "order_price_min_tick_size": "0.01",
        "group_item_title": "Team 1 v Team 2",
        "taker_base_fee": 0,
        "fees_enabled": True,
        "fee_schedule": {
            "exponent": 2,
            "rate": 0.25,
            "taker_only": False,
            "rebate_rate": 0.2,
        },
        "event_message": {"source": "market-channel"},
    }


def _market_resolved(*, suffix: str = "1") -> dict[str, object]:
    return {
        "event_type": "market_resolved",
        "id": f"resolution-event-{suffix}",
        "timestamp": 1783792800000,
        "market": f"market-{suffix}",
        "question": f"Will Team {suffix} win?",
        "slug": f"team-{suffix}-win",
        "description": "Fixture market",
        "assets_ids": [f"yes-{suffix}", f"no-{suffix}"],
        "outcomes": ["Yes", "No"],
        "winning_asset_id": f"yes-{suffix}",
        "winning_outcome": "Yes",
        "event_message": "Market resolved",
    }


def _install_fake_socket(monkeypatch, batch: dict[str, list[dict[str, object]]], subscriptions: list[dict[str, object]]) -> None:
    real_import_module = websocket_collector.importlib.import_module

    def fake_import_module(name: str):
        if name == "websockets":
            return object()
        return real_import_module(name)

    async def fake_collect(url, seconds, subscription_message, **kwargs):
        subscriptions.append(subscription_message)
        return [
            {
                "collected_at_utc": "2026-07-11T12:00:00Z",
                "message": json.dumps(batch["events"]),
            }
        ]

    monkeypatch.setattr(websocket_collector.importlib, "import_module", fake_import_module)
    monkeypatch.setattr(websocket_collector, "_collect_messages", fake_collect)


def test_custom_lifecycle_frames_append_and_deduplicate(tmp_path: Path, monkeypatch) -> None:
    cfg = _cfg(tmp_path, custom_feature_enabled=True)
    batch = {"events": [_new_market(), _market_resolved()]}
    subscriptions: list[dict[str, object]] = []
    _install_fake_socket(monkeypatch, batch, subscriptions)

    first = websocket_collector.collect_websocket(cfg, websocket_seconds=1)
    resolution_path = cfg.output_root / "polymarket_websocket" / "resolution_events.csv"
    market_birth_path = cfg.output_root / "polymarket_websocket" / "market_births.csv"
    resolution_rows = read_csv_rows(resolution_path)
    market_birth_rows = read_csv_rows(market_birth_path)

    assert first["status"] == "collected"
    assert subscriptions[0]["custom_feature_enabled"] is True
    assert first["lifecycle_capture"]["downstream_wired"] is False
    assert first["lifecycle_capture"]["resolution_events_appended"] == 1
    assert first["lifecycle_capture"]["market_births_appended"] == 1
    assert len(resolution_rows) == 1
    assert resolution_rows[0]["market"] == "market-1"
    assert resolution_rows[0]["winning_asset_id"] == "yes-1"
    assert resolution_rows[0]["winning_outcome"] == "Yes"
    assert len(market_birth_rows) == 1
    assert market_birth_rows[0]["question"] == "Will Team 1 win?"
    assert market_birth_rows[0]["condition_id"] == "condition-1"
    assert market_birth_rows[0]["sports_market_type"] == "moneyline"
    assert market_birth_rows[0]["game_start_time"] == "2026-07-12T18:00:00Z"
    assert market_birth_rows[0]["order_price_min_tick_size"] == "0.01"
    assert market_birth_rows[0]["fee_schedule_exponent"] == "2"
    assert market_birth_rows[0]["fee_schedule_rate"] == "0.25"
    assert market_birth_rows[0]["fee_schedule_taker_only"] == "False"
    assert market_birth_rows[0]["fee_schedule_rebate_rate"] == "0.2"

    resolution_prefix = resolution_path.read_bytes()
    market_birth_prefix = market_birth_path.read_bytes()
    duplicate = websocket_collector.collect_websocket(cfg, websocket_seconds=1)

    assert duplicate["lifecycle_capture"]["resolution_events_appended"] == 0
    assert duplicate["lifecycle_capture"]["resolution_events_duplicates"] == 1
    assert duplicate["lifecycle_capture"]["market_births_appended"] == 0
    assert duplicate["lifecycle_capture"]["market_births_duplicates"] == 1
    assert resolution_path.read_bytes() == resolution_prefix
    assert market_birth_path.read_bytes() == market_birth_prefix

    batch["events"] = [_new_market(suffix="2"), _market_resolved(suffix="2")]
    third = websocket_collector.collect_websocket(cfg, websocket_seconds=1)

    assert third["lifecycle_capture"]["resolution_events_appended"] == 1
    assert third["lifecycle_capture"]["market_births_appended"] == 1
    assert resolution_path.read_bytes().startswith(resolution_prefix)
    assert market_birth_path.read_bytes().startswith(market_birth_prefix)
    assert len(read_csv_rows(resolution_path)) == 2
    assert len(read_csv_rows(market_birth_path)) == 2


def test_custom_flag_off_preserves_existing_collector_behaviour(tmp_path: Path, monkeypatch) -> None:
    cfg = _cfg(tmp_path, custom_feature_enabled=False)
    batch = {"events": [_new_market(), _market_resolved()]}
    subscriptions: list[dict[str, object]] = []
    _install_fake_socket(monkeypatch, batch, subscriptions)

    result = websocket_collector.collect_websocket(cfg, websocket_seconds=1)
    summary = read_json(cfg.output_root / "polymarket_websocket" / "websocket_summary.json")

    assert result["status"] == "collected"
    assert result["new_messages"] == 1
    assert subscriptions == [{"assets_ids": ["token-1"], "type": "market"}]
    assert "lifecycle_capture" not in result
    assert "lifecycle_capture" not in summary
    assert not (cfg.output_root / "polymarket_websocket" / "resolution_events.csv").exists()
    assert not (cfg.output_root / "polymarket_websocket" / "market_births.csv").exists()


def test_dynamic_subscriptions_enable_custom_features_only_when_requested() -> None:
    enabled = websocket_collector._subscription_variants(
        ["token-1"],
        {"subscription_message": {"type": "market", "custom_feature_enabled": True}},
        dynamic_ids=True,
    )
    disabled = websocket_collector._subscription_variants(["token-1"], {}, dynamic_ids=True)

    assert all(variant["custom_feature_enabled"] is True for variant in enabled)
    assert disabled == [
        {"assets_ids": ["token-1"], "type": "market"},
        {"asset_ids": ["token-1"], "type": "market"},
        {"markets": ["token-1"], "type": "market"},
    ]


def test_resolution_event_remains_out_of_training_features() -> None:
    features, quality = normalize_websocket_message(
        json.dumps(_market_resolved()),
        collected_at_utc="2026-07-11T12:00:00Z",
    )

    assert features == []
    assert len(quality) == 1
    assert quality[0]["event_type"] == "market_resolved"
    assert quality[0]["issue_type"] == "non_feature_event"
