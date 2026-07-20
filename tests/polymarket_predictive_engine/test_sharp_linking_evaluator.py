"""WO-105 sharp-linking funding evaluator — recorded-fixture, fail-closed tests.

Every scenario is built from recorded artifacts on disk (decision policy,
study portfolio, sharp anchor CSV, official-book archive, Tier-0 replay). The
happy path qualifies; each registered precondition is shown to fail closed in
isolation; and the tighten-only override contract is pinned.
"""

from __future__ import annotations

import csv
import gzip
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from polymarket_predictive_engine import sharp_linking_evaluator as mod
from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.live_test_decision_policy import policy_version_sha256
from polymarket_predictive_engine.utils import write_csv, write_json

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
CAND = "0xcand"
TOKEN = "0xtok"
STUDY_STAMP = "2026-07-18T11:55:00Z"
POLICY_STAMP = "2026-07-18T11:56:00Z"


def _cfg(tmp_path: Path, extra: dict[str, Any] | None = None) -> EngineConfig:
    raw: dict[str, Any] = {"paths": {"output_root": str(tmp_path / "outputs")}}
    if extra:
        raw.update(extra)
    return EngineConfig(raw=raw, path=tmp_path / "c.yaml")


def _write_official_book(
    cfg: EngineConfig,
    condition_id: str,
    *,
    best_bid: float,
    best_ask: float,
    collected_at: str,
    asset_id: str = TOKEN,
) -> None:
    path = cfg.output_root / "maker_carry" / "official_books" / f"{condition_id}.csv.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["condition_id", "asset_id", "observation_timestamp", "best_bid", "best_ask", "midpoint", "collected_at_utc"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "condition_id": condition_id,
                "asset_id": asset_id,
                "observation_timestamp": collected_at,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "midpoint": round((best_bid + best_ask) / 2.0, 4),
                "collected_at_utc": collected_at,
            }
        )


def _setup(cfg: EngineConfig, **o: Any) -> None:
    out = cfg.output_root / "maker_carry"
    out.mkdir(parents=True, exist_ok=True)
    study_stamp = o.get("study_stamp", STUDY_STAMP)

    policy_payload = {
        "generated_at_utc": o.get("policy_stamp", POLICY_STAMP),
        "composition_stability": {"most_recurrent_market": o.get("policy_candidate", CAND)},
    }
    policy_payload["policy_version_sha256"] = (
        o["policy_version"]
        if "policy_version" in o
        else policy_version_sha256(policy_payload)
    )
    write_json(out / "decision_policy.json", policy_payload)
    portfolio_row = {
        "condition_id": CAND,
        "token_id": o.get("token", TOKEN),
        "quote_bid_price": o.get("band_lo", 0.40),
        "quote_ask_price": o.get("band_hi", 0.60),
        "question": "Calm test market?",
    }
    portfolio = o.get("portfolio", [portfolio_row] if o.get("in_portfolio", True) else [])
    write_json(out / "maker_carry_study.json", {"generated_at_utc": study_stamp, "portfolio": portfolio})

    anchor_rows = o.get(
        "anchors",
        [{"token_id": TOKEN, "probability": o.get("fair", 0.50), "anchor_timestamp_utc": o.get("anchor_stamp", STUDY_STAMP)}],
    )
    anchor_rows = [
        {
            "source": "sharp_odds",
            "anchor_source": "pinnacle",
            "bookmaker": "pinnacle",
            "join_source": "direct_token_joins",
            **row,
        }
        for row in anchor_rows
    ]
    write_csv(
        cfg.output_root / "polymarket_training" / "sharp_fundamental_probabilities.csv",
        anchor_rows,
        fieldnames=[
            "token_id",
            "probability",
            "anchor_timestamp_utc",
            "source",
            "anchor_source",
            "bookmaker",
            "join_source",
        ],
    )

    _write_official_book(
        cfg,
        CAND,
        best_bid=o.get("pm_bid", 0.49),
        best_ask=o.get("pm_ask", 0.51),
        collected_at=o.get("book_stamp", "2026-07-18T11:58:00Z"),
        asset_id=o.get("book_token", TOKEN),
    )

    market_row = {
        "condition_id": o.get("replay_condition", CAND),
        "asset_id": o.get("replay_token", TOKEN),
        "last_in_queue_evaluable_opportunities": o.get("evaluable", 35),
        "confirmed_fills": o.get("confirmed", 12),
        "coverage_ratio": o.get("coverage", 0.9),
        "simulation_to_reality_haircut": o.get("haircut", 0.8),
        "by_horizon": o.get(
            "by_horizon",
            {"5m": {"windows_covered": 12}, "15m": {"windows_covered": 11}, "60m": {"windows_covered": 10}},
        ),
    }
    write_json(
        out / "maker_fill_replay.json",
        {
            "generated_at_utc": o.get("replay_stamp", "2026-07-18T11:59:00Z"),
            "portfolio_generated_at_utc": o.get("replay_portfolio", study_stamp),
            "primary_book_source": o.get("source", "official"),
            "per_market_coverage": o.get("per_market", [market_row]),
        },
    )


def test_happy_path_qualifies(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _setup(cfg)
    qualified, checks, summary = mod.evaluate_sharp_linking(cfg, as_of=NOW)
    assert qualified is True, [c for c in checks if not c["passed"]]
    assert all(c["passed"] for c in checks)
    assert summary["candidate_condition_id"] == CAND
    assert summary["candidate_token_id"] == TOKEN
    assert summary["consensus_sharp_fair"] == 0.5
    assert summary["qualifying_sharp_sources"] == ["pinnacle"]
    assert summary["consumed_policy_generated_at"] == POLICY_STAMP
    assert summary["consumed_policy_version_sha256"]


@pytest.mark.parametrize(
    "anchor",
    [
        {"anchor_source": "", "bookmaker": ""},
        {"anchor_source": "sharp_odds", "bookmaker": "sharp_odds"},
        {"anchor_source": "manual_pinnacle_snapshot", "bookmaker": "manual_pinnacle_snapshot"},
        {"anchor_source": "betfair", "bookmaker": "betfair"},
        {"anchor_source": "pinnacle", "bookmaker": "betfair_ex_eu"},
        {"anchor_source": "soft_book", "bookmaker": "soft_book"},
    ],
)
def test_unverified_or_conflicting_sharp_source_fails_closed(tmp_path: Path, anchor: dict[str, str]) -> None:
    cfg = _cfg(tmp_path)
    _setup(
        cfg,
        anchors=[
            {
                "token_id": TOKEN,
                "probability": 0.5,
                "anchor_timestamp_utc": STUDY_STAMP,
                **anchor,
            }
        ],
    )

    qualified, checks, _ = mod.evaluate_sharp_linking(cfg, as_of=NOW)

    assert qualified is False
    provenance = next(row for row in checks if row["check"] == "exact_token_anchor_provenance")
    assert provenance["passed"] is False
    assert "rejected_provenance=1" in provenance["detail"]


def test_all_registered_sharp_books_are_accepted(tmp_path: Path) -> None:
    assert mod.QUALIFYING_SHARP_SOURCES == frozenset(
        {"pinnacle", "betfair_ex_eu", "betfair_ex_uk"}
    )
    for source in sorted(mod.QUALIFYING_SHARP_SOURCES):
        cfg = _cfg(tmp_path / source)
        _setup(
            cfg,
            anchors=[
                {
                    "token_id": TOKEN,
                    "probability": 0.5,
                    "anchor_timestamp_utc": STUDY_STAMP,
                    "anchor_source": source,
                    "bookmaker": source,
                }
            ],
        )
        qualified, checks, summary = mod.evaluate_sharp_linking(cfg, as_of=NOW)
        assert qualified is True, [row for row in checks if not row["passed"]]
        assert summary["qualifying_sharp_sources"] == [source]


def test_one_bad_fresh_source_cannot_hide_behind_one_valid_source(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _setup(
        cfg,
        anchors=[
            {
                "token_id": TOKEN,
                "probability": 0.5,
                "anchor_timestamp_utc": STUDY_STAMP,
                "anchor_source": "pinnacle",
                "bookmaker": "pinnacle",
            },
            {
                "token_id": TOKEN,
                "probability": 0.5,
                "anchor_timestamp_utc": STUDY_STAMP,
                "anchor_source": "unknown",
                "bookmaker": "unknown",
            },
        ],
    )

    qualified, checks, summary = mod.evaluate_sharp_linking(cfg, as_of=NOW)

    assert qualified is False
    assert summary["qualifying_sharp_sources"] == ["pinnacle"]
    assert next(row for row in checks if row["check"] == "exact_token_anchor_provenance")["passed"] is False


def test_candidate_identity_requires_one_portfolio_row_and_no_csv_fallback(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _setup(
        cfg,
        portfolio=[
            {"condition_id": CAND, "token_id": TOKEN, "quote_bid_price": 0.4, "quote_ask_price": 0.6},
            {"condition_id": CAND, "token_id": "0xother", "quote_bid_price": 0.4, "quote_ask_price": 0.6},
        ],
    )
    qualified, checks, _ = mod.evaluate_sharp_linking(cfg, as_of=NOW)
    assert qualified is False
    identity = next(row for row in checks if row["check"] == "candidate_market_identified")
    assert identity["passed"] is False
    assert "portfolio_matches=2" in identity["detail"]

    _setup(cfg, token="")
    write_csv(
        cfg.output_root / "maker_carry" / "maker_carry_candidates.csv",
        [{"condition_id": CAND, "token_id": TOKEN}],
    )
    qualified, checks, _ = mod.evaluate_sharp_linking(cfg, as_of=NOW)
    assert qualified is False
    assert next(row for row in checks if row["check"] == "candidate_market_identified")["passed"] is False


@pytest.mark.parametrize(
    "overrides,failed_check",
    [
        ({"book_token": "0xother"}, "fresh_executable_pm_book"),
        ({"replay_token": "0xother"}, "tier0_market_row_present"),
        ({"replay_condition": "0xother"}, "tier0_market_row_present"),
    ],
)
def test_every_downstream_row_requires_the_exact_condition_token_pair(
    tmp_path: Path,
    overrides: dict[str, Any],
    failed_check: str,
) -> None:
    cfg = _cfg(tmp_path)
    _setup(cfg, **overrides)
    qualified, checks, _ = mod.evaluate_sharp_linking(cfg, as_of=NOW)
    assert qualified is False
    assert next(row for row in checks if row["check"] == failed_check)["passed"] is False


def test_duplicate_exact_replay_rows_are_ambiguous_and_fail_closed(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    market_row = {
        "condition_id": CAND,
        "asset_id": TOKEN,
        "last_in_queue_evaluable_opportunities": 35,
        "confirmed_fills": 12,
        "coverage_ratio": 0.9,
        "simulation_to_reality_haircut": 0.8,
        "by_horizon": {
            "5m": {"windows_covered": 12},
            "15m": {"windows_covered": 11},
            "60m": {"windows_covered": 10},
        },
    }
    _setup(cfg, per_market=[market_row, dict(market_row)])
    qualified, checks, _ = mod.evaluate_sharp_linking(cfg, as_of=NOW)
    assert qualified is False
    assert next(row for row in checks if row["check"] == "tier0_market_row_present")["passed"] is False


def test_missing_all_artifacts_fails_closed(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    qualified, checks, _ = mod.evaluate_sharp_linking(cfg, as_of=NOW)
    assert qualified is False
    assert checks[0]["check"] == "candidate_market_identified" and checks[0]["passed"] is False


FAIL_CASES = [
    ({"in_portfolio": False}, "candidate_market_identified"),
    ({"policy_candidate": "0xother"}, "candidate_market_identified"),
    ({"policy_stamp": ""}, "candidate_policy_version_identified"),
    ({"policy_stamp": "not-a-timestamp"}, "candidate_policy_version_identified"),
    ({"policy_version": ""}, "candidate_policy_version_identified"),
    ({"policy_version": "0" * 64}, "candidate_policy_version_identified"),
    ({"anchor_stamp": "2026-07-18T00:00:00Z"}, "exact_token_fresh_anchor"),
    (
        {
            "anchors": [
                {"token_id": TOKEN, "probability": 0.50, "anchor_timestamp_utc": STUDY_STAMP},
                {"token_id": TOKEN, "probability": 0.58, "anchor_timestamp_utc": STUDY_STAMP},
            ]
        },
        "fresh_anchors_agree",
    ),
    ({"fair": 0.70}, "consensus_fair_inside_quote_band"),
    ({"book_stamp": "2026-07-18T11:00:00Z"}, "fresh_executable_pm_book"),
    ({"replay_stamp": "2026-07-18T11:00:00Z"}, "tier0_replay_fresh"),
    ({"replay_portfolio": "2026-07-18T10:00:00Z"}, "tier0_matches_current_portfolio_version"),
    ({"source": "archive"}, "tier0_primary_source_official"),
    ({"per_market": []}, "tier0_market_row_present"),
    ({"evaluable": 20}, "tier0_min_evaluable_opportunities"),
    ({"confirmed": 5}, "tier0_min_confirmed_fills"),
    ({"coverage": 0.5}, "tier0_min_coverage_ratio"),
    (
        {"by_horizon": {"5m": {"windows_covered": 5}, "15m": {"windows_covered": 11}, "60m": {"windows_covered": 10}}},
        "tier0_markout_windows_each_horizon",
    ),
    ({"haircut": 1.5}, "tier0_adverse_haircut_within_ceiling"),
    ({"coverage": "nan"}, "tier0_min_coverage_ratio"),  # #262: NaN must fail closed
    ({"haircut": "nan"}, "tier0_adverse_haircut_within_ceiling"),  # #262: NaN must fail closed
]


@pytest.mark.parametrize("overrides,expected_first_failing", FAIL_CASES)
def test_each_precondition_fails_closed(tmp_path: Path, overrides: dict[str, Any], expected_first_failing: str) -> None:
    cfg = _cfg(tmp_path)
    _setup(cfg, **overrides)
    qualified, checks, _ = mod.evaluate_sharp_linking(cfg, as_of=NOW)
    assert qualified is False, overrides
    first_failing = next(c["check"] for c in checks if not c["passed"])
    assert first_failing == expected_first_failing, (overrides, [c for c in checks if not c["passed"]])


def test_single_anchor_agrees_trivially(tmp_path: Path) -> None:
    # One fresh anchor is internally consistent: the agreement check must pass
    # (disagreement = 0), not fail on an empty comparison.
    cfg = _cfg(tmp_path)
    _setup(cfg)
    _, checks, _ = mod.evaluate_sharp_linking(cfg, as_of=NOW)
    agree = next(c for c in checks if c["check"] == "fresh_anchors_agree")
    assert agree["passed"] is True


def test_override_cannot_loosen_registered_maximum(tmp_path: Path) -> None:
    # A config that tries to WIDEN the replay-age maximum is ignored: a 60-min
    # replay still fails against the registered 30-min ceiling.
    cfg = _cfg(tmp_path, {"sharp_linking_evaluator": {"max_replay_age_seconds": 999999}})
    _setup(cfg, replay_stamp="2026-07-18T11:00:00Z")
    qualified, checks, _ = mod.evaluate_sharp_linking(cfg, as_of=NOW)
    assert qualified is False
    fresh = next(c for c in checks if c["check"] == "tier0_replay_fresh")
    assert fresh["passed"] is False


def test_override_can_only_tighten_minimum(tmp_path: Path) -> None:
    # A config that raises the confirmed-fill minimum is applied: a 12-fill
    # market that passes the registered floor of 10 now fails at 100.
    cfg = _cfg(tmp_path, {"sharp_linking_evaluator": {"min_confirmed_fills": 100}})
    _setup(cfg)
    qualified, checks, _ = mod.evaluate_sharp_linking(cfg, as_of=NOW)
    assert qualified is False
    confirmed = next(c for c in checks if c["check"] == "tier0_min_confirmed_fills")
    assert confirmed["passed"] is False


def test_zero_valued_max_override_is_honored(tmp_path: Path) -> None:
    # #262: a stricter `max_adverse_haircut: 0` must bind, not be treated as an
    # invalid override that restores the registered 1.0 ceiling.
    cfg = _cfg(tmp_path, {"sharp_linking_evaluator": {"max_adverse_haircut": 0}})
    _setup(cfg, haircut=0.8)
    qualified, checks, _ = mod.evaluate_sharp_linking(cfg, as_of=NOW)
    assert qualified is False
    haircut_check = next(c for c in checks if c["check"] == "tier0_adverse_haircut_within_ceiling")
    assert haircut_check["passed"] is False


def test_run_writes_atomic_fail_closed_artifact(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _setup(cfg)
    payload = mod.run_sharp_linking_evaluator(cfg, as_of=NOW)
    assert payload["qualified"] is True
    assert payload["work_order"] == "WO-105"
    assert payload["paper_trading_invoked"] is False
    assert payload["live_trading_invoked"] is False
    assert (cfg.output_root / mod.OUTPUT_RELATIVE).exists()
    assert payload["registered_constants"]["max_anchor_age_seconds"] == 6 * 3600
    # #260: the qualification records the exact study/replay versions it read,
    # so the WO-99 gate can bind to the current artifacts.
    assert payload["consumed_study_generated_at"] == STUDY_STAMP
    assert payload["consumed_replay_generated_at"] == "2026-07-18T11:59:00Z"
    assert payload["consumed_policy_generated_at"] == POLICY_STAMP
    assert payload["consumed_policy_version_sha256"]
