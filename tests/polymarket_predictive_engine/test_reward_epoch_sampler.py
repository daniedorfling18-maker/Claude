from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from polymarket_predictive_engine import reward_epoch_sampler
from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.reward_epoch_sampler import (
    CANDIDATE_COPY_FIELDS,
    SAMPLE_FIELDS,
    run_reward_epoch_sample,
)
from polymarket_predictive_engine.utils import read_csv_rows, read_json, write_csv, write_json


SAMPLED_AT = "2026-07-19T10:00:00Z"
STUDY_AT = "2026-07-19T09:59:00Z"


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reward_epoch_sampler, "now_utc", lambda: SAMPLED_AT)


def _cfg(tmp_path: Path) -> EngineConfig:
    return EngineConfig(
        raw={"paths": {"output_root": str(tmp_path / "outputs")}},
        path=tmp_path / "config.yaml",
    )


def _candidate(condition_id: str, token_id: str, question: str) -> dict[str, str]:
    return {
        "condition_id": condition_id,
        "token_id": token_id,
        "question": question,
        "band_eligible": "true",
        "pot_usd_per_day": "25.50",
        "estimated_reward_share": "0.125",
        "gross_reward_usd_per_day": "3.1875",
        "competitor_score_bid": "101.25",
        "competitor_score_ask": "99.75",
        "mid_price": "0.42",
    }


def _write_candidates(cfg: EngineConfig, rows: list[dict[str, str]]) -> None:
    write_csv(
        cfg.output_root / "maker_carry" / "maker_carry_candidates.csv",
        rows,
        fieldnames=CANDIDATE_COPY_FIELDS,
    )


def _write_study(cfg: EngineConfig, stamp: str = STUDY_AT) -> None:
    write_json(
        cfg.output_root / "maker_carry" / "maker_carry_study.json",
        {"status": "ok", "generated_at_utc": stamp},
    )


def _sample_rows(cfg: EngineConfig) -> list[dict[str, str]]:
    return read_csv_rows(cfg.output_root / "maker_carry" / "reward_epoch_samples.csv")


def test_two_candidates_append_exact_fields(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    candidates = [
        _candidate("condition-1", "token-1", "Question one?"),
        _candidate("condition-2", "token-2", "Question two?"),
    ]
    _write_candidates(cfg, candidates)
    _write_study(cfg)

    summary = run_reward_epoch_sample(cfg)

    assert SAMPLE_FIELDS == [
        "sampled_at_utc",
        "study_generated_at_utc",
        "condition_id",
        "token_id",
        "question",
        "band_eligible",
        "pot_usd_per_day",
        "estimated_reward_share",
        "gross_reward_usd_per_day",
        "competitor_score_bid",
        "competitor_score_ask",
        "mid_price",
    ]
    assert summary["status"] == "ok"
    assert summary["rows_sampled"] == 2
    assert summary["total_rows"] == 2
    rows = _sample_rows(cfg)
    assert len(rows) == 2
    for row, candidate in zip(rows, candidates):
        assert row["sampled_at_utc"] == SAMPLED_AT
        assert row["study_generated_at_utc"] == STUDY_AT
        for field in CANDIDATE_COPY_FIELDS:
            assert row[field] == candidate[field]


def test_same_study_stamp_is_idempotent(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write_candidates(cfg, [_candidate("condition-1", "token-1", "Question?")])
    _write_study(cfg)

    first = run_reward_epoch_sample(cfg)
    second = run_reward_epoch_sample(cfg)

    assert first["rows_sampled"] == 1
    assert second["rows_sampled"] == 0
    assert second["total_rows"] == 1
    assert len(_sample_rows(cfg)) == 1


def test_overlapping_invocations_append_each_key_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(tmp_path)
    _write_candidates(cfg, [_candidate("condition-1", "token-1", "Question?")])
    _write_study(cfg)
    append_entered = Event()
    release_append = Event()
    original_append = reward_epoch_sampler.append_csv_rows

    def blocking_append(*args, **kwargs):
        append_entered.set()
        assert release_append.wait(timeout=5)
        return original_append(*args, **kwargs)

    monkeypatch.setattr(reward_epoch_sampler, "append_csv_rows", blocking_append)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(run_reward_epoch_sample, cfg)
        assert append_entered.wait(timeout=5)
        second_future = pool.submit(run_reward_epoch_sample, cfg)
        try:
            second = second_future.result(timeout=5)
        finally:
            release_append.set()
        first = first_future.result(timeout=5)

    assert first["status"] == "ok"
    assert first["runtime_lock"]["acquired"] is True
    assert second["status"] == "skipped_lock_held"
    assert second["runtime_lock"]["acquired"] is False
    assert len(_sample_rows(cfg)) == 1
    assert read_json(cfg.output_root / "maker_carry" / "reward_epoch_sampler.json") == first


def test_new_study_stamp_appends_fresh_sample(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write_candidates(cfg, [_candidate("condition-1", "token-1", "Question?")])
    _write_study(cfg)
    run_reward_epoch_sample(cfg)
    _write_study(cfg, "2026-07-19T10:30:00Z")

    summary = run_reward_epoch_sample(cfg)

    assert summary["rows_sampled"] == 1
    assert summary["total_rows"] == 2
    assert [row["study_generated_at_utc"] for row in _sample_rows(cfg)] == [
        STUDY_AT,
        "2026-07-19T10:30:00Z",
    ]


def test_missing_candidates_reports_no_candidates(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write_study(cfg)

    summary = run_reward_epoch_sample(cfg)

    assert summary["status"] == "no_candidates"
    assert summary["rows_sampled"] == 0
    assert summary["total_rows"] == 0
    assert read_json(cfg.output_root / "maker_carry" / "reward_epoch_sampler.json") == summary


def test_missing_study_reports_no_study(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write_candidates(cfg, [_candidate("condition-1", "token-1", "Question?")])

    summary = run_reward_epoch_sample(cfg)

    assert summary["status"] == "no_study"
    assert summary["rows_sampled"] == 0
    assert summary["total_rows"] == 0
    assert _sample_rows(cfg) == []


@pytest.mark.parametrize("study_status", ["disabled", "failed", "no_candidates", ""])
def test_inactive_study_does_not_append_stale_candidates(
    tmp_path: Path,
    study_status: str,
) -> None:
    cfg = _cfg(tmp_path)
    _write_candidates(cfg, [_candidate("condition-1", "token-1", "Question?")])
    write_json(
        cfg.output_root / "maker_carry" / "maker_carry_study.json",
        {"status": study_status, "generated_at_utc": STUDY_AT},
    )

    summary = run_reward_epoch_sample(cfg)

    assert summary["status"] == "no_study"
    assert summary["study_status"] == (study_status or None)
    assert summary["rows_sampled"] == 0
    assert summary["total_rows"] == 0
    assert _sample_rows(cfg) == []


def test_duplicate_condition_keeps_first_candidate(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    first = _candidate("condition-1", "token-first", "First question?")
    second = _candidate("condition-1", "token-second", "Second question?")
    _write_candidates(cfg, [first, second])
    _write_study(cfg)

    summary = run_reward_epoch_sample(cfg)

    assert summary["rows_sampled"] == 1
    assert summary["total_rows"] == 1
    rows = _sample_rows(cfg)
    assert rows[0]["token_id"] == "token-first"
    assert rows[0]["question"] == "First question?"


def test_summary_carries_required_no_trading_flags(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)

    run_reward_epoch_sample(cfg)
    persisted = read_json(cfg.output_root / "maker_carry" / "reward_epoch_sampler.json")

    assert persisted["paper_trading_invoked"] is False
    assert persisted["live_trading_invoked"] is False
