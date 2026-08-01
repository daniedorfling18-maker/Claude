"""WO-141 -- A failed refresh must not erase collected price history.

Tests pin the per-token merge rule in
``price_history_collector.collect_price_history``: a token's prior corpus
rows survive any fetch outcome that is not a clean, recognized venue answer.
See the "## WO-141" section of ``docs/POLYMARKET_CODEX_WORK_ORDERS.md`` for
the registered spec; the twelve tests below are numbered to match its
enumerated test list.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from polymarket_predictive_engine import price_history_collector
from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.price_history_collector import (
    SNAPSHOT_FIELDS,
    collect_price_history,
)
from polymarket_predictive_engine.utils import read_csv_rows, read_json, write_csv

CLOSE_TIME = "2026-07-10T12:00:00Z"
CLOSE_DT = datetime(2026, 7, 10, 12, tzinfo=timezone.utc)


def _cfg(tmp_path: Path, *, max_tokens: int = 500) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "historical_price_history": {
                "max_tokens": max_tokens,
                "request_pause_seconds": 0,
                "progress_every_tokens": 0,
                "request_timeout_seconds": 1,
                "lookback_days_before_close": 30,
                "lookahead_days_after_close": 3,
                "min_close_time": "2024-01-01T00:00:00Z",
            },
        },
        path=tmp_path / "config.yaml",
    )


def _resolution(token_id: str, slug: str, close_time: str = CLOSE_TIME, *, category: str = "sports") -> dict[str, Any]:
    return {
        "market_slug": slug,
        "condition_id": f"condition-{token_id}",
        "gamma_market_id": f"gamma-{token_id}",
        "token_id": token_id,
        "outcome": "Yes",
        "category": category,
        "close_time": close_time,
        "resolution_time": close_time,
        "resolution_quality": "clean_settlement",
    }


def _write_resolutions(cfg: EngineConfig, rows: list[dict[str, Any]]) -> None:
    write_csv(cfg.output_root / "polymarket_training" / "historical_resolutions.csv", rows)


def _snapshot_path(cfg: EngineConfig) -> Path:
    return cfg.output_root / "polymarket_training" / "historical_price_snapshots.csv"


def _quality_path(cfg: EngineConfig) -> Path:
    return cfg.governance_root / "historical_price_history_quality.csv"


def _summary_path(cfg: EngineConfig) -> Path:
    return cfg.governance_root / "historical_price_history_summary.json"


def _raw_row(
    token_id: str,
    timestamp: str,
    price: Any,
    midpoint: Any,
    *,
    slug: str = "world-cup-general",
    close_time: str = CLOSE_TIME,
    source: str = "polymarket_clob_prices_history:bounded_close_window",
) -> dict[str, Any]:
    return {
        "market_id": f"condition-{token_id}",
        "market_slug": slug,
        "condition_id": f"condition-{token_id}",
        "gamma_market_id": f"gamma-{token_id}",
        "token_id": token_id,
        "outcome": "Yes",
        "category": "sports",
        "timestamp": timestamp,
        "midpoint": midpoint,
        "price": price,
        "close_time": close_time,
        "source": source,
    }


def _snapshot_row(token_id: str, timestamp: str, price: float, **overrides: Any) -> dict[str, Any]:
    return _raw_row(token_id, timestamp, price, price, **overrides)


def _write_prior_corpus(
    cfg: EngineConfig,
    rows: list[dict[str, Any]],
    *,
    fieldnames: list[str] | None = None,
) -> None:
    write_csv(_snapshot_path(cfg), rows, fieldnames=fieldnames or SNAPSHOT_FIELDS)


def _by_key(rows: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    return sorted((row.get("token_id"), row.get("timestamp"), str(row.get("price"))) for row in rows)


def _sequenced_response(sequence_by_token: dict[str, list[Any]]):
    """Fake ``_request_history`` that pops responses in order, per token.

    Each entry in a token's sequence is either an ``Exception`` instance
    (raised) or a payload (returned). Once a token's sequence is exhausted
    the last entry repeats, so a single-item list means "always this".
    """

    counters: dict[str, int] = {}

    def _fake(_base_url: str, params: dict[str, Any], _timeout: int) -> Any:
        token_id = str(params.get("market"))
        idx = counters.get(token_id, 0)
        counters[token_id] = idx + 1
        sequence = sequence_by_token[token_id]
        item = sequence[min(idx, len(sequence) - 1)]
        if isinstance(item, BaseException):
            raise item
        return item

    return _fake


def _always_raises(_base_url: str, _params: dict[str, Any], _timeout: int) -> Any:
    raise RuntimeError("recorded test outage")


# ---------------------------------------------------------------------------
# Test 1: a run whose every request errors leaves every requested token's
# prior series byte-identical in the corpus, and the summary records the
# preservation counts alongside error_count == requested_tokens.
# ---------------------------------------------------------------------------


def test_01_total_outage_preserves_every_prior_series_byte_identical(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path)
    _write_resolutions(
        cfg,
        [_resolution("token-a", "world-cup-a"), _resolution("token-b", "world-cup-b")],
    )
    prior_rows = [
        _snapshot_row("token-a", "2026-07-09T12:00:00Z", 0.40),
        _snapshot_row("token-a", "2026-07-09T18:00:00Z", 0.45),
        _snapshot_row("token-b", "2026-07-09T12:00:00Z", 0.55),
    ]
    _write_prior_corpus(cfg, prior_rows)
    before = read_csv_rows(_snapshot_path(cfg))

    monkeypatch.setattr(price_history_collector, "_request_history", _always_raises)

    snapshots, quality, summary = collect_price_history(cfg)

    after = read_csv_rows(_snapshot_path(cfg))
    assert _by_key(after) == _by_key(before)
    assert _by_key(snapshots) == _by_key(before)
    assert summary["error_count"] == summary["requested_tokens"] == 2
    assert summary["preserved_token_count"] == 2
    assert summary["preserved_row_count"] == 3
    assert all(row["status"] == "fetch_error" for row in quality)
    assert all(row["attempt_errors"] > 0 for row in quality)


# ---------------------------------------------------------------------------
# Test 2: a 1-of-N-success partial run writes fresh rows for the success and
# preserves each failed token's prior rows.
# ---------------------------------------------------------------------------


def test_02_partial_success_writes_fresh_and_preserves_failed_tokens(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path)
    _write_resolutions(
        cfg,
        [
            _resolution("token-ok", "world-cup-ok"),
            _resolution("token-fail-a", "world-cup-fail-a"),
            _resolution("token-fail-b", "world-cup-fail-b"),
        ],
    )
    stale_ok_row = _snapshot_row("token-ok", "2026-06-01T00:00:00Z", 0.10)
    prior_fail_a = [_snapshot_row("token-fail-a", "2026-07-09T12:00:00Z", 0.30)]
    _write_prior_corpus(cfg, [stale_ok_row, *prior_fail_a])
    # token-fail-b has no prior rows at all.

    fresh_point_epoch = int((CLOSE_DT - timedelta(hours=6)).timestamp())
    sequence = {
        "token-ok": [{"history": [{"t": fresh_point_epoch, "p": 0.77}]}],
        "token-fail-a": [RuntimeError("outage-a")],
        "token-fail-b": [RuntimeError("outage-b")],
    }
    monkeypatch.setattr(price_history_collector, "_request_history", _sequenced_response(sequence))

    snapshots, quality, summary = collect_price_history(cfg)

    by_token: dict[str, list[dict[str, Any]]] = {}
    for row in snapshots:
        by_token.setdefault(row["token_id"], []).append(row)

    # token-ok: clean success replaces wholesale -- the stale prior row is gone.
    assert len(by_token.get("token-ok", [])) == 1
    assert by_token["token-ok"][0]["price"] == 0.77

    # token-fail-a: prior rows preserved unchanged.
    assert _by_key(by_token.get("token-fail-a", [])) == _by_key(prior_fail_a)

    # token-fail-b: no prior rows, preservation contributes nothing.
    assert "token-fail-b" not in by_token

    assert summary["preserved_token_count"] == 1
    assert summary["preserved_row_count"] == 1
    assert summary["error_count"] == 2


# ---------------------------------------------------------------------------
# Test 3: a token rotated out of the selection is dropped even when
# preservation is active (pins the rolling design).
# ---------------------------------------------------------------------------


def test_03_token_rotated_out_of_selection_still_drops(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path)
    # Only token-current is requested this run; token-rotated-out is not.
    _write_resolutions(cfg, [_resolution("token-current", "world-cup-current")])
    _write_prior_corpus(
        cfg,
        [
            _snapshot_row("token-current", "2026-07-09T12:00:00Z", 0.5),
            _snapshot_row("token-rotated-out", "2026-07-01T12:00:00Z", 0.6),
        ],
    )
    monkeypatch.setattr(price_history_collector, "_request_history", _always_raises)

    snapshots, quality, summary = collect_price_history(cfg)

    tokens_written = {row["token_id"] for row in snapshots}
    assert "token-rotated-out" not in tokens_written
    assert {row["token_id"] for row in quality} == {"token-current"}
    assert summary["requested_tokens"] == 1
    # The preserved corpus for the still-requested token survives.
    assert tokens_written == {"token-current"}


# ---------------------------------------------------------------------------
# Test 4: a failed token with no prior rows adds nothing.
# ---------------------------------------------------------------------------


def test_04_failed_token_without_prior_rows_adds_nothing(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path)
    _write_resolutions(cfg, [_resolution("token-lonely", "world-cup-lonely")])
    # No prior corpus file at all.
    monkeypatch.setattr(price_history_collector, "_request_history", _always_raises)

    snapshots, quality, summary = collect_price_history(cfg)

    assert snapshots == []
    assert summary["preserved_token_count"] == 0
    assert summary["preserved_row_count"] == 0
    assert summary["error_count"] == 1
    assert not Path(_snapshot_path(cfg)).exists() or read_csv_rows(_snapshot_path(cfg)) == []


# ---------------------------------------------------------------------------
# Test 5: a full-success run is byte-identical to today's output with both
# preservation counts at 0 (regression -- the mechanism is inert on the
# healthy path).
# ---------------------------------------------------------------------------


def test_05_full_success_is_byte_identical_and_preservation_inert(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path)
    _write_resolutions(cfg, [_resolution("token-a", "world-cup-a")])
    # Stale prior data that a healthy run must fully replace, not blend with.
    _write_prior_corpus(cfg, [_snapshot_row("token-a", "2020-01-01T00:00:00Z", 0.01)])

    p1 = int((CLOSE_DT - timedelta(hours=7)).timestamp())
    p2 = int((CLOSE_DT - timedelta(hours=5)).timestamp())
    payload = {"history": [{"t": p1, "p": 0.55}, {"t": p2, "p": 0.60}]}
    monkeypatch.setattr(price_history_collector, "_request_history", lambda *_a, **_k: payload)

    snapshots, quality, summary = collect_price_history(cfg)

    assert len(snapshots) == 2
    assert {row["price"] for row in snapshots} == {0.55, 0.60}
    assert all(row["timestamp"] != "2020-01-01T00:00:00Z" for row in snapshots)
    assert summary["preserved_token_count"] == 0
    assert summary["preserved_row_count"] == 0
    assert summary["preserved_rows_dropped_invalid"] == 0
    assert summary["prior_corpus_untrusted"] is False
    assert quality[0]["attempt_errors"] == 0
    assert quality[0]["status"] == "ok"


# ---------------------------------------------------------------------------
# Test 6: the quality CSV still carries fetch_error rows for preserved
# tokens (honesty).
# ---------------------------------------------------------------------------


def test_06_quality_csv_still_records_fetch_error_for_preserved_tokens(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path)
    _write_resolutions(cfg, [_resolution("token-a", "world-cup-a")])
    _write_prior_corpus(cfg, [_snapshot_row("token-a", "2026-07-09T12:00:00Z", 0.5)])
    monkeypatch.setattr(price_history_collector, "_request_history", _always_raises)

    snapshots, quality, summary = collect_price_history(cfg)

    persisted_quality = read_csv_rows(_quality_path(cfg))
    assert len(snapshots) == 1  # preserved, not erased
    assert persisted_quality[0]["status"] == "fetch_error"
    assert int(persisted_quality[0]["attempt_errors"]) > 0
    assert persisted_quality[0]["error"] == "recorded test outage"
    assert summary["error_count"] == 1


# ---------------------------------------------------------------------------
# Test 7: a prior corpus with a non-SNAPSHOT_FIELDS header is wholly
# untrusted -- the run falls back to write-what-was-fetched, stamps
# prior_corpus_untrusted, and does not raise.
# ---------------------------------------------------------------------------


def test_07_malformed_header_makes_prior_corpus_wholly_untrusted(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path)
    _write_resolutions(
        cfg,
        [_resolution("token-fail", "world-cup-fail"), _resolution("token-ok", "world-cup-ok")],
    )
    # Foreign/reordered-with-missing header: not SNAPSHOT_FIELDS.
    _write_prior_corpus(
        cfg,
        [{"token_id": "token-fail", "timestamp": "2026-07-09T12:00:00Z", "price": 0.5, "midpoint": 0.5, "source": "x"}],
        fieldnames=["token_id", "timestamp", "price", "midpoint", "source"],
    )

    fresh_point_epoch = int((CLOSE_DT - timedelta(hours=6)).timestamp())
    sequence = {
        "token-fail": [RuntimeError("outage")],
        "token-ok": [{"history": [{"t": fresh_point_epoch, "p": 0.9}]}],
    }
    monkeypatch.setattr(price_history_collector, "_request_history", _sequenced_response(sequence))

    snapshots, quality, summary = collect_price_history(cfg)

    assert summary["prior_corpus_untrusted"] is True
    tokens_written = {row["token_id"] for row in snapshots}
    # token-fail has nothing trustworthy to preserve -> write-what-was-fetched (nothing).
    assert "token-fail" not in tokens_written
    # token-ok is unaffected by the untrusted corpus -- still writes its fresh fetch.
    assert tokens_written == {"token-ok"}
    assert summary["preserved_token_count"] == 0
    assert summary["preserved_row_count"] == 0


# ---------------------------------------------------------------------------
# Test 8: within a trusted corpus, a row with a missing timestamp, a
# non-finite price, or a missing/non-finite/disagreeing midpoint is dropped
# alone and counted in preserved_rows_dropped_invalid while the token's
# valid rows survive.
# ---------------------------------------------------------------------------


def test_08_invalid_rows_dropped_alone_and_counted(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path)
    _write_resolutions(cfg, [_resolution("token-a", "world-cup-a")])

    valid_1 = _snapshot_row("token-a", "2026-07-08T00:00:00Z", 0.20)
    valid_2 = _snapshot_row("token-a", "2026-07-08T06:00:00Z", 0.25)
    missing_timestamp = _raw_row("token-a", "", 0.30, 0.30)
    nonfinite_price = _raw_row("token-a", "2026-07-08T12:00:00Z", "nan", "nan")
    disagreeing_midpoint = _raw_row("token-a", "2026-07-08T18:00:00Z", 0.5, 0.6)
    _write_prior_corpus(
        cfg,
        [valid_1, missing_timestamp, nonfinite_price, disagreeing_midpoint, valid_2],
    )

    monkeypatch.setattr(price_history_collector, "_request_history", _always_raises)

    snapshots, quality, summary = collect_price_history(cfg)

    assert _by_key(snapshots) == _by_key([valid_1, valid_2])
    assert summary["preserved_rows_dropped_invalid"] == 3
    assert summary["preserved_row_count"] == 2
    assert summary["preserved_token_count"] == 1
    assert summary["prior_corpus_untrusted"] is False


# ---------------------------------------------------------------------------
# Test 9: a token whose fetch chain mixed errors with a final empty payload
# (attempt_errors > 0, zero points) keeps its prior rows and its quality row
# records the attempt errors.
# ---------------------------------------------------------------------------


def test_09_mixed_error_then_empty_chain_preserves_prior_rows(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path)
    _write_resolutions(cfg, [_resolution("token-a", "world-cup-a")])
    prior_rows = [_snapshot_row("token-a", "2026-07-09T12:00:00Z", 0.5)]
    _write_prior_corpus(cfg, prior_rows)

    # 5 attempts total for a token with a close_time (bounded, short,
    # interval_1m, interval_1w, configured_interval): 4 raise, the last
    # returns a recognized-but-empty answer (no exception at the end).
    sequence = {
        "token-a": [
            RuntimeError("e1"),
            RuntimeError("e2"),
            RuntimeError("e3"),
            RuntimeError("e4"),
            {"history": []},
        ]
    }
    monkeypatch.setattr(price_history_collector, "_request_history", _sequenced_response(sequence))

    snapshots, quality, summary = collect_price_history(cfg)

    assert _by_key(snapshots) == _by_key(prior_rows)
    assert quality[0]["attempt_errors"] == 4
    assert quality[0]["history_points"] == 0
    assert summary["preserved_token_count"] == 1
    assert summary["preserved_row_count"] == 1


# ---------------------------------------------------------------------------
# Test 10: a clean empty (attempt_errors == 0, recognized schema) still
# drops prior rows (venue-authoritative regression).
# ---------------------------------------------------------------------------


def test_10_clean_empty_still_drops_prior_rows(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path)
    _write_resolutions(cfg, [_resolution("token-a", "world-cup-a")])
    _write_prior_corpus(cfg, [_snapshot_row("token-a", "2026-07-09T12:00:00Z", 0.5)])

    monkeypatch.setattr(price_history_collector, "_request_history", lambda *_a, **_k: {"history": []})

    snapshots, quality, summary = collect_price_history(cfg)

    assert snapshots == []
    assert quality[0]["attempt_errors"] == 0
    assert quality[0]["status"] == "empty_history"
    assert summary["preserved_token_count"] == 0
    assert summary["preserved_row_count"] == 0


# ---------------------------------------------------------------------------
# Test 11: a chain whose every attempt returns HTTP 200 with an
# error-shaped/unrecognized body counts those attempts in attempt_errors and
# preserves prior rows -- it must NOT read as a clean empty.
# ---------------------------------------------------------------------------


def test_11_unrecognized_payload_chain_is_not_a_clean_empty(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path)
    _write_resolutions(cfg, [_resolution("token-a", "world-cup-a")])
    prior_rows = [_snapshot_row("token-a", "2026-07-09T12:00:00Z", 0.5)]
    _write_prior_corpus(cfg, prior_rows)

    # Error-shaped body: no history/prices/data key, not a bare list.
    unrecognized_payload = {"error": "not found", "code": 404}
    monkeypatch.setattr(price_history_collector, "_request_history", lambda *_a, **_k: unrecognized_payload)

    snapshots, quality, summary = collect_price_history(cfg)

    assert quality[0]["attempt_errors"] == 5  # every attempt in the chain was unrecognized
    assert quality[0]["history_points"] == 0
    # Must land in the preserve bucket, not the clean-empty (drop) bucket.
    assert _by_key(snapshots) == _by_key(prior_rows)
    assert summary["preserved_token_count"] == 1
    assert summary["preserved_row_count"] == 1


# ---------------------------------------------------------------------------
# Test 12: a fallback success (primary attempt raises, a shorter window
# answers) unions the fetched points with the token's prior rows,
# deduplicated on (token_id, timestamp) with the fetched row winning ties,
# so prior coverage outside the fallback window survives.
# ---------------------------------------------------------------------------


def test_12_fallback_success_unions_with_prior_rows_fresh_wins_ties(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path)
    _write_resolutions(cfg, [_resolution("token-a", "world-cup-a")])

    tie_ts = (CLOSE_DT - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    outside_ts = (CLOSE_DT - timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    prior_tie_row = _snapshot_row("token-a", tie_ts, 0.20)  # stale value at the tie key
    prior_outside_row = _snapshot_row("token-a", outside_ts, 0.45)  # only the (failed) 30-day window covered this
    _write_prior_corpus(cfg, [prior_tie_row, prior_outside_row])

    tie_epoch = int((CLOSE_DT - timedelta(days=1)).timestamp())
    fresh_payload = {"history": [{"t": tie_epoch, "p": 0.66}]}
    # bounded_close_window (the 30-day primary attempt) raises; short_close_window
    # (7-day fallback) answers with the fresh point above.
    sequence = {"token-a": [RuntimeError("bounded window unavailable"), fresh_payload]}
    monkeypatch.setattr(price_history_collector, "_request_history", _sequenced_response(sequence))

    snapshots, quality, summary = collect_price_history(cfg)

    assert quality[0]["attempt_errors"] == 1
    assert quality[0]["history_points"] == 1

    by_ts = {row["timestamp"]: float(row["price"]) for row in snapshots}
    assert by_ts[tie_ts] == 0.66  # freshly fetched row wins the tie
    assert by_ts[outside_ts] == 0.45  # prior coverage outside the fallback window survives
    assert len(snapshots) == 2
    # Not classified as pure preservation -- this run did fetch a point.
    assert summary["preserved_token_count"] == 0
    assert summary["preserved_row_count"] == 0


# ---------------------------------------------------------------------------
# Sanity: summary always carries the WO-141 fields, and the whole run never
# raises even in these degraded scenarios.
# ---------------------------------------------------------------------------


def test_summary_json_persists_wo141_fields(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path)
    _write_resolutions(cfg, [_resolution("token-a", "world-cup-a")])
    _write_prior_corpus(cfg, [_snapshot_row("token-a", "2026-07-09T12:00:00Z", 0.5)])
    monkeypatch.setattr(price_history_collector, "_request_history", _always_raises)

    collect_price_history(cfg)

    persisted_summary = read_json(_summary_path(cfg))
    for key in (
        "preserved_token_count",
        "preserved_row_count",
        "preserved_rows_dropped_invalid",
        "prior_corpus_untrusted",
    ):
        assert key in persisted_summary
