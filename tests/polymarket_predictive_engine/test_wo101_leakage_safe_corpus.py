from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import polymarket_predictive_engine.historical_backfill as historical_backfill_module
import polymarket_predictive_engine.resolution_collector as resolution_collector
from polymarket_predictive_engine import websocket_resolution_collector
from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.historical_bid_ask import (
    QUOTE_FIELDS,
    QUOTE_LEDGER_RELATIVE_PATH,
    QUOTE_STATE_RELATIVE_PATH,
    collect_historical_bid_ask,
    quote_observation_from_feature,
)
from polymarket_predictive_engine.leakage_safe_training import (
    FEATURES_RELATIVE_PATH,
    LABELS_RELATIVE_PATH,
    REGISTERED_MIN_VALIDATION_MARKETS,
    SPLIT_RELATIVE_PATH,
    _resolution_index,
    build_leakage_safe_training,
    training_settings,
)
from polymarket_predictive_engine.maker_fill_replay import _official_row
from polymarket_predictive_engine.resolution_collector import infer_market_resolution_rows
from polymarket_predictive_engine.resolution_corpus import (
    RESOLUTION_CORPUS_RELATIVE_PATH,
    append_resolution_observations,
)
from polymarket_predictive_engine.utils import append_csv_rows, read_csv_rows, read_json, write_csv
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


def test_gamma_end_time_is_the_pre_close_boundary_not_later_settlement() -> None:
    market = {
        "id": "gamma-boundary",
        "slug": "market-boundary",
        "conditionId": "condition-boundary",
        "question": "Boundary fixture",
        "closed": True,
        "active": False,
        "outcomes": '["Yes", "No"]',
        "clobTokenIds": '["yes-boundary", "no-boundary"]',
        "outcomePrices": '["1", "0"]',
        "endDate": "2026-07-16T10:00:00Z",
        "closedTime": "2026-07-16T10:15:00Z",
    }

    rows, _ = infer_market_resolution_rows(
        market,
        observed_at_utc="2026-07-16T10:20:00Z",
    )

    assert {row["close_time"] for row in rows} == {"2026-07-16T10:00:00Z"}
    assert {row["resolution_time"] for row in rows} == {"2026-07-16T10:15:00Z"}


def test_historical_backfill_observation_clock_is_after_gamma_response(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cfg = _cfg(tmp_path)
    sequence: list[str] = []

    def fake_candidates(**_kwargs):
        sequence.append("gamma_response")
        return [], {"strategy": "recorded_test", "scans": []}

    def fake_clock(value=None):
        assert value is None
        sequence.append("observation_clock")
        return "2026-07-16T12:00:01Z"

    monkeypatch.setattr(historical_backfill_module, "_candidate_markets", fake_candidates)
    monkeypatch.setattr(historical_backfill_module, "canonical_utc", fake_clock)

    _, _, summary = historical_backfill_module.historical_backfill(
        cfg,
        historical_limit=1,
        allow_old_history=True,
    )

    assert sequence == ["gamma_response", "observation_clock"]
    assert summary["collected_at_utc"] == "2026-07-16T12:00:01Z"


def test_resolution_collector_clock_is_after_all_gamma_responses(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cfg = _cfg(tmp_path)
    cfg.raw["resolution"] = {"request_pause_seconds": 0}
    sequence: list[str] = []

    def fake_fetch(slug, **_kwargs):
        sequence.append(f"gamma_response:{slug}")
        return {
            "id": f"gamma-{slug}",
            "slug": slug,
            "conditionId": f"condition-{slug}",
            "questionID": f"question-{slug}",
            "question": slug,
            "category": "sports",
            "closed": True,
            "active": False,
            "outcomes": '["Yes", "No"]',
            "clobTokenIds": f'["yes-{slug}", "no-{slug}"]',
            "outcomePrices": '["1", "0"]',
            "closedTime": "2026-07-16T10:00:00Z",
            "endDate": "2026-07-16T10:00:00Z",
        }

    real_clock = resolution_collector.canonical_utc

    def fake_clock(value=None):
        if value is None:
            sequence.append("observation_clock")
            return "2026-07-16T12:00:01Z"
        return real_clock(value)

    monkeypatch.setattr(
        resolution_collector,
        "_unique_raw_slugs",
        lambda _cfg: {"first": "sports", "second": "sports"},
    )
    monkeypatch.setattr(resolution_collector, "fetch_gamma_market", fake_fetch)
    monkeypatch.setattr(resolution_collector, "canonical_utc", fake_clock)

    _, _, summary = resolution_collector.collect_resolutions(cfg)
    corpus = read_csv_rows(cfg.output_root / RESOLUTION_CORPUS_RELATIVE_PATH)

    assert sequence[:3] == [
        "gamma_response:first",
        "gamma_response:second",
        "observation_clock",
    ]
    assert summary["generated_at_utc"] == "2026-07-16T12:00:01Z"
    assert {row["resolution_observed_at_utc"] for row in corpus} == {
        "2026-07-16T12:00:01Z"
    }


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


def test_websocket_resolution_producer_appends_to_canonical_corpus(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cfg = _cfg(tmp_path)
    token = "websocket-token"
    write_csv(
        cfg.output_root / WEBSOCKET_FEATURES_RELATIVE_PATH,
        [{"market": "condition-ws", "asset_id": token}],
    )
    market = {
        "id": "gamma-ws",
        "slug": "market-ws",
        "conditionId": "condition-ws",
        "questionID": "question-ws",
        "question": "Websocket tracked market",
        "category": "sports",
        "closed": True,
        "active": False,
        "outcomes": '["Yes", "No"]',
        "clobTokenIds": f'["{token}", "other-token"]',
        "outcomePrices": '["1", "0"]',
        "closedTime": "2026-07-16T10:00:00Z",
        "endDate": "2026-07-16T10:00:00Z",
    }

    sequence: list[str] = []

    def fake_scan(**kwargs):
        sequence.append("gamma_response")
        requested = kwargs["token_ids"]
        return ({requested_token: market for requested_token in requested}, {"matched_tokens": len(requested)})

    def fake_clock():
        sequence.append("observation_clock")
        return "2026-07-16T12:00:01Z"

    monkeypatch.setattr(websocket_resolution_collector, "_scan_gamma_for_tokens", fake_scan)
    monkeypatch.setattr(websocket_resolution_collector, "now_utc", fake_clock)
    result = websocket_resolution_collector.collect_websocket_resolutions(cfg)

    corpus = read_csv_rows(cfg.output_root / RESOLUTION_CORPUS_RELATIVE_PATH)
    assert result["append_only_resolution_corpus"]["rows_appended"] == 2
    assert {row["token_id"] for row in corpus} == {token, "other-token"}
    assert {row["producer"] for row in corpus} == {"websocket_resolution_collector"}
    assert {row["resolution_observed_at_utc"] for row in corpus} == {
        "2026-07-16T12:00:01Z"
    }
    assert sequence == ["gamma_response", "observation_clock"]
    assert result["paper_trading_invoked"] is False
    assert result["live_trading_invoked"] is False


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
    state_after_first = read_json(cfg.output_root / QUOTE_STATE_RELATIVE_PATH)
    second = collect_historical_bid_ask(cfg, as_of=current)
    third = collect_historical_bid_ask(cfg, as_of=current + timedelta(minutes=2))

    assert first["rows_appended"] == 1
    assert first["exclusions"]["future_observation"] == 1
    assert first["source_files_deferred_for_future_observations"] == 1
    assert state_after_first["sources"] == {}
    assert second["rows_appended"] == 0
    assert second["source_files_changed"] == 1
    assert second["exclusions"]["future_observation"] == 1
    assert third["rows_appended"] == 1
    assert third["source_files_deferred_for_future_observations"] == 0
    assert (cfg.output_root / QUOTE_LEDGER_RELATIVE_PATH).read_bytes().startswith(prefix)
    assert len(read_csv_rows(cfg.output_root / QUOTE_LEDGER_RELATIVE_PATH)) == 2
    assert read_json(cfg.output_root / QUOTE_STATE_RELATIVE_PATH)["sources"]


def test_training_assembly_has_purged_market_split_and_separate_labels(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    start = datetime(2026, 1, 5, 12, tzinfo=timezone.utc)
    as_of = start + timedelta(days=82)
    quote_rows = []
    for index in range(20):
        close_at = start + timedelta(days=index * 4)
        market_id = f"market-{index}"
        token_id = f"token-{index}"
        append_resolution_observations(
            cfg,
            [_resolution(market_id, token_id, index % 2, close_at)],
            producer="test",
            observed_at_utc=close_at + timedelta(minutes=5),
        )
        quote_rows.append(_quote(market_id, token_id, close_at - timedelta(hours=6)))
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
    assert result["split"]["train_markets"] == 10
    assert result["split"]["validation_markets"] == 10
    assert result["split"]["minimum_validation_markets_required"] == 10
    assert result["split"]["purged_markets"] == 0
    assert result["split"]["market_overlap_count"] == 0
    assert {
        row["market_id"] for row in split if row["split"] == "validation"
    } == {f"market-{index}" for index in range(10, 20)}
    assert len(features) == len(labels) == 20
    assert all(row["best_bid"] and row["best_ask"] for row in features)
    assert all(row["executable_buy_price"] == row["best_ask"] for row in features)
    assert all("target" not in row and "resolution_time" not in row for row in features)
    assert {row["target"] for row in labels if row["split"] == "train"} == {"0", "1"}
    for snapshot in (features, labels, split):
        assert snapshot
        assert {row["paper_trading_invoked"] for row in snapshot} == {"false"}
        assert {row["live_trading_invoked"] for row in snapshot} == {"false"}
    assert result["midpoint_only_rows_accepted"] == 0
    assert result["registered_h3_verdict_authority"] is False


def test_label_is_available_only_when_resolution_was_first_observed(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    close_at = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    observed_at = close_at + timedelta(days=3)
    append_resolution_observations(
        cfg,
        [_resolution("observed-market", "observed-token", 1, close_at)],
        producer="delayed_observer",
        observed_at_utc=observed_at,
    )
    path = cfg.output_root / RESOLUTION_CORPUS_RELATIVE_PATH

    before, before_counts = _resolution_index(
        path,
        as_of=observed_at - timedelta(seconds=1),
    )
    after, _ = _resolution_index(path, as_of=observed_at + timedelta(seconds=1))

    assert before == {}
    assert before_counts["resolution_future_observations"] == 1
    assert after["observed-token"]["label_available_at"] == observed_at


def test_resolution_timestamp_must_also_be_available_by_assembly_clock(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    close_at = datetime(2026, 2, 3, 12, tzinfo=timezone.utc)
    producer_observed_at = datetime(2026, 2, 1, 12, tzinfo=timezone.utc)
    append_resolution_observations(
        cfg,
        [_resolution("future-label-market", "future-label-token", 1, close_at)],
        producer="bad_early_observer_fixture",
        observed_at_utc=producer_observed_at,
    )
    path = cfg.output_root / RESOLUTION_CORPUS_RELATIVE_PATH

    before, before_counts = _resolution_index(
        path,
        as_of=datetime(2026, 2, 2, 12, tzinfo=timezone.utc),
    )
    after, _ = _resolution_index(
        path,
        as_of=datetime(2026, 2, 4, 12, tzinfo=timezone.utc),
    )

    assert before == {}
    assert before_counts["resolution_label_not_available_by_assembly_clock"] == 1
    assert "future-label-token" in after


def test_divergent_clean_close_times_quarantine_the_entire_token(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    corrected_close = datetime(2026, 2, 3, 12, tzinfo=timezone.utc)
    original_close = corrected_close + timedelta(hours=3)
    original = _resolution("revised-close-market", "revised-close-token", 1, original_close)
    corrected = dict(original)
    corrected["end_time"] = _iso(corrected_close)
    corrected["close_time"] = _iso(corrected_close)
    append_resolution_observations(
        cfg,
        [original],
        producer="original_close_fixture",
        observed_at_utc=original_close + timedelta(minutes=10),
    )
    append_resolution_observations(
        cfg,
        [corrected],
        producer="corrected_close_fixture",
        observed_at_utc=original_close + timedelta(minutes=20),
    )

    index, counts = _resolution_index(
        cfg.output_root / RESOLUTION_CORPUS_RELATIVE_PATH,
        as_of=original_close + timedelta(minutes=30),
    )

    assert "revised-close-token" not in index
    assert counts["conflicting_clean_close_time_tokens"] == 1
    assert counts["clean_resolution_tokens"] == 0


def test_split_collects_until_ten_independent_validation_markets_exist(
    tmp_path: Path,
) -> None:
    cfg = _cfg(tmp_path)
    start = datetime(2026, 2, 1, 12, tzinfo=timezone.utc)
    quotes = []
    for index in range(10):
        close_at = start + timedelta(days=index * 3)
        append_resolution_observations(
            cfg,
            [_resolution(f"minimum-{index}", f"minimum-token-{index}", index % 2, close_at)],
            producer="minimum_validation_test",
            observed_at_utc=close_at + timedelta(minutes=5),
        )
        quotes.append(
            _quote(
                f"minimum-{index}",
                f"minimum-token-{index}",
                close_at - timedelta(hours=6),
            )
        )
    append_csv_rows(
        cfg.output_root / QUOTE_LEDGER_RELATIVE_PATH,
        quotes,
        fieldnames=QUOTE_FIELDS,
    )

    result = build_leakage_safe_training(
        cfg,
        as_of=start + timedelta(days=31),
    )

    assert result["status"] == "collecting"
    assert result["split"]["markets_total"] == 10
    assert result["split"]["validation_markets"] == 0
    assert result["split"]["purged_markets"] == 10
    assert result["split"]["minimum_validation_markets_required"] == 10
    assert result["split"]["independent_market_unit"] == "canonical_market_id_whole_market"


def test_overlap_is_purged_conflicts_excluded_and_settings_cannot_widen(tmp_path: Path) -> None:
    cfg = _cfg(
        tmp_path,
        {
            "max_lookback_hours": 999,
            "embargo_hours": 1,
            "validation_fraction": 0.1,
            "minimum_validation_markets": 1,
            "thin_bucket_seconds": 60,
        },
    )
    settings = training_settings(cfg)
    assert settings == {
        "max_lookback_hours": 168.0,
        "embargo_hours": 24.0,
        "validation_fraction": 0.3,
        "minimum_validation_markets": 10,
        "thin_bucket_seconds": 3600.0,
    }
    stricter_fraction = training_settings(
        _cfg(tmp_path / "stricter", {"validation_fraction": 0.80})
    )
    assert stricter_fraction["validation_fraction"] == 0.80
    capped_fraction = training_settings(
        _cfg(tmp_path / "capped", {"validation_fraction": 1.50})
    )
    assert capped_fraction["validation_fraction"] == 1.0

    closes = [
        datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
        datetime(2026, 1, 2, 12, tzinfo=timezone.utc),
        datetime(2026, 1, 3, 12, tzinfo=timezone.utc),
        datetime(2026, 1, 9, 12, tzinfo=timezone.utc),
        *(datetime(2026, 1, 10 + index, 12, tzinfo=timezone.utc) for index in range(10)),
    ]
    as_of = datetime(2026, 1, 25, tzinfo=timezone.utc)
    resolutions = [
        _resolution(f"m{index}", f"t{index}", index % 2, close_at)
        for index, close_at in enumerate(closes)
    ]
    # Preserve a conflicting clean target for t0; the assembler must exclude it.
    conflict = dict(resolutions[0])
    conflict["target"] = 1
    for close_at, resolution in zip(closes, resolutions):
        append_resolution_observations(
            cfg,
            [resolution],
            producer="test",
            observed_at_utc=close_at + timedelta(minutes=5),
        )
    append_resolution_observations(
        cfg,
        [conflict],
        producer="conflict_test",
        observed_at_utc=closes[0] + timedelta(minutes=5),
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
    assert result["split"]["validation_markets"] == REGISTERED_MIN_VALIDATION_MARKETS
    assert next(row for row in split if row["market_id"] == "m3")["split"] == "purged"
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


# ---------------------------------------------------------------------------
# Corpus stratification.
#
# Measured 2026-08-15 against origin/vps-telemetry @ 0ae83704: the resolved
# corpus held 1000 markets whose top resolution sources were ligue2 (131),
# MLB (118), flashscore (78), ATP (69), PGA (69) and setkacup (66), plus
# sub-hourly "Up or Down" crypto candles — and it shared ZERO condition ids
# with the 40 markets the study was quoting. Because the fetch takes the most
# recently closed markets, and fixtures/candles resolve continuously while a
# macro question resolves once, that sampling rule can never reach the traded
# population. Every directional statistic computed on it was measuring a
# different universe from the one being traded.
# ---------------------------------------------------------------------------

_STRATA_BASE = datetime(2026, 8, 14, tzinfo=timezone.utc)


def _strata_market(index: int, *, category: str, source: str, duration_hours: float) -> dict:
    close = _STRATA_BASE - timedelta(minutes=index)
    return {
        "id": f"m{index}",
        "conditionId": f"0x{index:04x}",
        "closed": True,
        "question": f"question {index}",
        "category": category,
        "resolutionSource": source,
        "startDate": (close - timedelta(hours=duration_hours)).isoformat().replace("+00:00", "Z"),
        "closedTime": close.isoformat().replace("+00:00", "Z"),
    }


def _measured_corpus_shape() -> list[dict]:
    """A corpus mirroring the real composition measured on 2026-08-15."""
    markets: list[dict] = []
    index = 0
    for source, count in (
        ("https://www.ligue2.fr/", 131),
        ("https://www.mlb.com/scores", 118),
        ("https://www.flashscore.com/", 78),
        ("https://www.atptour.com/en/scores/current", 69),
        ("https://www.pgatour.com/", 69),
        ("https://setkacup.com", 66),
    ):
        for _ in range(count):
            markets.append(_strata_market(index, category="sports", source=source, duration_hours=3.0))
            index += 1
    for _ in range(150):
        markets.append(_strata_market(index, category="crypto", source="https://data.chain.link/", duration_hours=5 / 60))
        index += 1
    for category, count in (("politics", 40), ("geopolitics", 35), ("macro", 30), ("culture", 20)):
        for _ in range(count):
            markets.append(_strata_market(index, category=category, source="https://www.reuters.com/", duration_hours=720.0))
            index += 1
    return markets


def test_per_fixture_sport_and_subhourly_candles_are_excluded() -> None:
    excluded = historical_backfill_module.DEFAULT_EXCLUDED_RESOLUTION_DOMAINS
    kwargs = {"min_duration_hours": 6.0, "excluded_domains": excluded}

    fixture = _strata_market(1, category="sports", source="https://setkacup.com", duration_hours=3.0)
    assert historical_backfill_module._excluded_population(fixture, **kwargs).startswith("per_fixture_sport")

    # www. prefixes and sub-paths must not defeat the domain match.
    ligue2 = _strata_market(2, category="sports", source="https://www.ligue2.fr/calendrier", duration_hours=3.0)
    assert historical_backfill_module._excluded_population(ligue2, **kwargs).startswith("per_fixture_sport")

    candle = _strata_market(3, category="crypto", source="https://data.chain.link/", duration_hours=5 / 60)
    assert historical_backfill_module._excluded_population(candle, **kwargs) == "sub_duration_floor"

    tradeable = _strata_market(4, category="geopolitics", source="https://www.reuters.com/", duration_hours=720.0)
    assert historical_backfill_module._excluded_population(tradeable, **kwargs) == ""


def test_stratified_take_reaches_the_traded_population() -> None:
    """The regression this exists to prevent: a corpus that is 100% sport."""
    corpus = _measured_corpus_shape()

    recency_only = sorted(corpus, key=historical_backfill_module._market_close_dt, reverse=True)[:200]
    recency_families = {historical_backfill_module._market_family(m) for m in recency_only}
    assert recency_families == {"sports"}, "precondition: recency-only sampling returns sport alone"

    eligible = [
        market
        for market in corpus
        if not historical_backfill_module._excluded_population(
            market,
            min_duration_hours=6.0,
            excluded_domains=historical_backfill_module.DEFAULT_EXCLUDED_RESOLUTION_DOMAINS,
        )
    ]
    selected, meta = historical_backfill_module._stratified_take(
        eligible, requested=200, max_share_per_family=0.15
    )

    families = {historical_backfill_module._market_family(m) for m in selected}
    assert "sports" not in families
    assert "crypto" not in families
    assert families == {"politics", "geopolitics", "macro", "culture"}
    assert meta["composition"]["politics"] == 30
    # Every family is capped, so no single population can crowd out the rest.
    assert max(meta["composition"].values()) <= meta["per_family_cap"]


def test_stratified_take_reports_an_unfilled_quota_rather_than_hiding_it() -> None:
    """A short corpus is a finding about the venue, not a silent shortfall."""
    thin = [
        _strata_market(i, category="politics", source="https://www.reuters.com/", duration_hours=720.0)
        for i in range(5)
    ]
    selected, meta = historical_backfill_module._stratified_take(thin, requested=200, max_share_per_family=1.0)

    assert len(selected) == 5
    assert meta["selected_markets"] == 5
    assert meta["requested_markets"] == 200
    assert meta["quota_met"] is False


def test_stratification_can_be_disabled_without_changing_legacy_selection() -> None:
    corpus = _measured_corpus_shape()
    selected, meta = historical_backfill_module._stratified_take(corpus, requested=10, max_share_per_family=1.0)
    assert meta["quota_met"] is True
    assert len(selected) == 10


def test_candle_series_are_caught_by_name_not_by_duration() -> None:
    """The duration floor cannot identify candles, measured against the real feed.

    "XRP Up or Down - August 15, 6:00AM-6:15AM ET" is a FIFTEEN MINUTE market
    whose startDate is the listing time a day earlier, so close-minus-start
    reads 24.1 hours and clears any sane duration floor. startDate is when the
    market was listed, not when its event window opens. The venue names its own
    candle series with the window length, and that is the reliable signal.
    """
    candle = {
        "id": "c1",
        "closed": True,
        "question": "XRP Up or Down - August 15, 6:00AM-6:15AM ET",
        "startDate": "2026-08-14T10:08:59Z",
        "closedTime": "2026-08-15T10:14:00Z",
        "events": [{"series": [{"slug": "xrp-up-or-down-15m"}]}],
    }

    # The duration heuristic alone is fooled, exactly as it was in production.
    assert historical_backfill_module._market_duration_hours(candle) > 24.0

    reason = historical_backfill_module._excluded_population(
        candle,
        min_duration_hours=6.0,
        excluded_domains=historical_backfill_module.DEFAULT_EXCLUDED_RESOLUTION_DOMAINS,
    )
    assert reason == "sub_hourly_candle_series"


def test_series_slug_is_the_family_key() -> None:
    """Measured on the live feed: 100/100 markets carry a series slug and 0/100
    carry a category, so keying on category alone never bound anything."""
    fixture = {"events": [{"series": [{"slug": "dota-2"}]}], "category": ""}
    assert historical_backfill_module._market_family(fixture) == "series:dota-2"

    # A one-off question with no series - the shape the study actually trades.
    one_off = {"slug": "will-enzo-fernandez-stay-at-chelsea", "events": []}
    assert historical_backfill_module._market_family(one_off).startswith("slug:")
