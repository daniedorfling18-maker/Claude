from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.historical_bid_ask import (
    QUOTE_FIELDS,
    QUOTE_LEDGER_RELATIVE_PATH,
    collect_historical_bid_ask,
    quote_observation_from_feature,
)
from polymarket_predictive_engine.leakage_safe_training import (
    FEATURES_RELATIVE_PATH,
    LABELS_RELATIVE_PATH,
    SPLIT_RELATIVE_PATH,
    build_leakage_safe_training,
    training_settings,
)
from polymarket_predictive_engine.maker_fill_replay import _official_row
from polymarket_predictive_engine.resolution_collector import infer_market_resolution_rows
from polymarket_predictive_engine.resolution_corpus import (
    RESOLUTION_CORPUS_RELATIVE_PATH,
    append_resolution_observations,
)
from polymarket_predictive_engine.utils import append_csv_rows, read_csv_rows, write_csv
from polymarket_predictive_engine.websocket_normaliser import WEBSOCKET_FEATURES_RELATIVE_PATH


FIXTURE_ROOT = Path("tests/fixtures/recorded")


def _cfg(tmp_path: Path, settings: dict | None = None) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {
                "data_root": str(tmp_path),
                "output_root": str(tmp_path / "outputs"),
                "database_path": str(tmp_path / "work" / "paper.sqlite"),
            },
            "leakage_safe_training": settings or {},
        },
        path=tmp_path / "config.yaml",
    )


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolution(
    market_id: str,
    token_id: str,
    target: int,
    close_at: datetime,
    *,
    category: str = "sports",
) -> dict:
    return {
        "market_slug": f"slug-{market_id}",
        "gamma_market_id": f"gamma-{market_id}",
        "condition_id": market_id,
        "question_id": f"question-{market_id}",
        "question": f"Will {market_id} happen?",
        "category": category,
        "closed": True,
        "active": False,
        "archived": False,
        "end_time": _iso(close_at),
        "close_time": _iso(close_at),
        "resolution_time": _iso(close_at + timedelta(minutes=5)),
        "winning_outcome": "Yes" if target else "No",
        "winning_token_id": token_id if target else f"other-{token_id}",
        "resolution_quality": "clean_settlement",
        "outcome_index": 0,
        "outcome": "Yes",
        "token_id": token_id,
        "outcome_price": target,
        "target": target,
    }


def _quote(
    market_id: str,
    token_id: str,
    observed_at: datetime,
    *,
    bid: float = 0.44,
    ask: float = 0.46,
    as_of: datetime | None = None,
) -> dict:
    row, reason = quote_observation_from_feature(
        {
            "market": market_id,
            "asset_id": token_id,
            "collected_at_utc": _iso(observed_at),
            "source_timestamp": int(observed_at.timestamp() * 1000),
            "event_type": "best_bid_ask",
            "category": "sports",
            "best_bid": bid,
            "best_ask": ask,
            "top_bid_size": 20,
            "top_ask_size": 15,
        },
        source_corpus="recorded_test_source",
        as_of=as_of or (observed_at + timedelta(minutes=1)),
    )
    assert reason == "ok"
    assert row is not None
    return row


def test_resolution_corpus_is_append_only_deduped_and_uses_one_clock(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    close_at = datetime(2026, 7, 10, 12, tzinfo=timezone.utc)
    market = {
        "id": "gamma-1",
        "slug": "market-1",
        "conditionId": "condition-1",
        "questionID": "question-1",
        "question": "Recorded resolution",
        "category": "sports",
        "closed": True,
        "active": False,
        "outcomes": '["Yes", "No"]',
        "clobTokenIds": '["yes-token", "no-token"]',
        "outcomePrices": '["1", "0"]',
        "closedTime": _iso(close_at),
        "endDate": _iso(close_at),
    }
    rows, _ = infer_market_resolution_rows(
        market,
        observed_at_utc="2026-07-10T13:00:00Z",
    )
    assert {row["resolution_collected_at_utc"] for row in rows} == {"2026-07-10T13:00:00Z"}

    first = append_resolution_observations(
        cfg,
        rows,
        producer="historical_backfill",
        observed_at_utc="2026-07-10T13:00:00Z",
    )
    path = cfg.output_root / RESOLUTION_CORPUS_RELATIVE_PATH
    prefix = path.read_bytes()
    duplicate = append_resolution_observations(
        cfg,
        rows,
        producer="collect_resolutions",
        observed_at_utc="2026-07-11T13:00:00Z",
    )
    changed = dict(rows[0])
    changed["target"] = 0
    conflict = append_resolution_observations(
        cfg,
        [changed],
        producer="conflict_fixture",
        observed_at_utc="2026-07-12T13:00:00Z",
    )

    assert first["rows_appended"] == 2
    assert duplicate["rows_appended"] == 0
    assert conflict["rows_appended"] == 1
    assert path.read_bytes().startswith(prefix)
    assert len(read_csv_rows(path)) == 3


def test_recorded_public_book_becomes_exact_quote_never_midpoint_reconstruction() -> None:
    book = json.loads((FIXTURE_ROOT / "clob_book_2026-07-15.json").read_text(encoding="utf-8"))
    official = _official_row(
        book,
        condition_id="condition-recorded",
        token_id="token-recorded",
        collected_at="2026-07-15T18:30:00Z",
    )
    assert official is not None
    row, reason = quote_observation_from_feature(
        {
            **official,
            "market": "condition-recorded",
            "asset_id": "token-recorded",
            "event_type": "book",
            "category": "sports",
        },
        source_corpus="recorded_clob_book_2026-07-15",
        as_of=datetime(2026, 7, 15, 18, 31, tzinfo=timezone.utc),
    )

    assert reason == "ok"
    assert row is not None
    assert row["best_bid"] == 0.25
    assert row["best_ask"] == 0.26
    assert row["midpoint"] == 0.255
    midpoint_only, exclusion = quote_observation_from_feature(
        {
            "market": "condition-recorded",
            "asset_id": "token-recorded",
            "collected_at_utc": "2026-07-15T18:30:00Z",
            "midpoint": 0.255,
        },
        source_corpus="forbidden_midpoint_only",
        as_of=datetime(2026, 7, 15, 18, 31, tzinfo=timezone.utc),
    )
    assert midpoint_only is None
    assert exclusion == "midpoint_or_one_sided_only"


def test_historical_quote_collector_appends_once_and_rejects_future(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    source = cfg.output_root / WEBSOCKET_FEATURES_RELATIVE_PATH
    current = datetime(2026, 7, 16, 12, tzinfo=timezone.utc)
    valid = {
        "market": "m1",
        "asset_id": "t1",
        "collected_at_utc": "2026-07-16T11:59:00Z",
        "source_timestamp": "1784199540000",
        "event_type": "best_bid_ask",
        "best_bid": 0.4,
        "best_ask": 0.42,
    }
    future = {**valid, "asset_id": "future", "collected_at_utc": "2026-07-16T12:01:00Z"}
    write_csv(source, [valid, future])

    first = collect_historical_bid_ask(cfg, as_of=current)
    prefix = (cfg.output_root / QUOTE_LEDGER_RELATIVE_PATH).read_bytes()
    second = collect_historical_bid_ask(cfg, as_of=current)

    assert first["rows_appended"] == 1
    assert first["exclusions"]["future_observation"] == 1
    assert second["rows_appended"] == 0
    assert (cfg.output_root / QUOTE_LEDGER_RELATIVE_PATH).read_bytes() == prefix


def test_training_assembly_has_purged_market_split_and_separate_labels(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    start = datetime(2026, 1, 5, 12, tzinfo=timezone.utc)
    as_of = start + timedelta(days=42)
    resolution_rows = []
    quote_rows = []
    for index in range(10):
        close_at = start + timedelta(days=index * 4)
        market_id = f"market-{index}"
        token_id = f"token-{index}"
        resolution_rows.append(_resolution(market_id, token_id, index % 2, close_at))
        quote_rows.append(_quote(market_id, token_id, close_at - timedelta(hours=6)))
    append_resolution_observations(
        cfg,
        resolution_rows,
        producer="test",
        observed_at_utc=as_of,
    )
    append_csv_rows(
        cfg.output_root / QUOTE_LEDGER_RELATIVE_PATH,
        quote_rows,
        fieldnames=QUOTE_FIELDS,
    )

    result = build_leakage_safe_training(cfg, as_of=as_of)
    features = read_csv_rows(cfg.output_root / FEATURES_RELATIVE_PATH)
    labels = read_csv_rows(cfg.output_root / LABELS_RELATIVE_PATH)
    split = read_csv_rows(cfg.output_root / SPLIT_RELATIVE_PATH)

    assert result["status"] == "ready"
    assert result["split"]["train_markets"] == 7
    assert result["split"]["validation_markets"] == 3
    assert result["split"]["purged_markets"] == 0
    assert result["split"]["market_overlap_count"] == 0
    assert {row["market_id"] for row in split if row["split"] == "validation"} == {
        "market-7",
        "market-8",
        "market-9",
    }
    assert len(features) == len(labels) == 10
    assert all(row["best_bid"] and row["best_ask"] for row in features)
    assert all(row["executable_buy_price"] == row["best_ask"] for row in features)
    assert all("target" not in row and "resolution_time" not in row for row in features)
    assert {row["target"] for row in labels if row["split"] == "train"} == {"0", "1"}
    assert result["midpoint_only_rows_accepted"] == 0
    assert result["registered_h3_verdict_authority"] is False


def test_overlap_is_purged_conflicts_excluded_and_settings_cannot_widen(tmp_path: Path) -> None:
    cfg = _cfg(
        tmp_path,
        {
            "max_lookback_hours": 999,
            "embargo_hours": 1,
            "validation_fraction": 0.1,
            "thin_bucket_seconds": 60,
        },
    )
    settings = training_settings(cfg)
    assert settings == {
        "max_lookback_hours": 168.0,
        "embargo_hours": 24.0,
        "validation_fraction": 0.3,
        "thin_bucket_seconds": 3600.0,
    }

    closes = [
        datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
        datetime(2026, 1, 10, 0, tzinfo=timezone.utc),
        datetime(2026, 1, 10, 12, tzinfo=timezone.utc),
        datetime(2026, 1, 20, 12, tzinfo=timezone.utc),
    ]
    as_of = datetime(2026, 1, 25, tzinfo=timezone.utc)
    resolutions = [
        _resolution(f"m{index}", f"t{index}", index % 2, close_at)
        for index, close_at in enumerate(closes)
    ]
    # Preserve a conflicting clean target for t0; the assembler must exclude it.
    conflict = dict(resolutions[0])
    conflict["target"] = 1
    append_resolution_observations(
        cfg,
        [*resolutions, conflict],
        producer="test",
        observed_at_utc=as_of,
    )
    quotes = [
        _quote(f"m{index}", f"t{index}", close_at - timedelta(hours=6))
        for index, close_at in enumerate(closes)
    ]
    append_csv_rows(
        cfg.output_root / QUOTE_LEDGER_RELATIVE_PATH,
        quotes,
        fieldnames=QUOTE_FIELDS,
    )

    result = build_leakage_safe_training(cfg, as_of=as_of)
    split = read_csv_rows(cfg.output_root / SPLIT_RELATIVE_PATH)

    assert result["coverage"]["conflicting_clean_resolution_tokens"] == 1
    assert result["split"]["purged_markets"] == 1
    assert next(row for row in split if row["market_id"] == "m1")["split"] == "purged"
    assert not any(row["market_id"] == "m0" for row in split)
    assert result["split"]["market_overlap_count"] == 0


def test_future_quote_enters_only_after_clock_advances() -> None:
    observed = datetime(2026, 7, 16, 12, 5, tzinfo=timezone.utc)
    raw = {
        "market": "m",
        "asset_id": "t",
        "collected_at_utc": _iso(observed),
        "best_bid": 0.4,
        "best_ask": 0.42,
    }
    before, reason = quote_observation_from_feature(
        raw,
        source_corpus="clock_test",
        as_of=observed - timedelta(seconds=1),
    )
    after, accepted = quote_observation_from_feature(
        raw,
        source_corpus="clock_test",
        as_of=observed + timedelta(seconds=1),
    )
    assert before is None
    assert reason == "future_observation"
    assert after is not None
    assert accepted == "ok"
