from __future__ import annotations

from pathlib import Path
from typing import Any

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.promotion_review import build_promotion_review
from polymarket_predictive_engine.utils import read_json, write_json


ADVISORY = "negative closing-line value evidence (advisory)"
DECISION_FIELDS = (
    "status",
    "metadata_valid",
    "metadata_blocker",
    "promoted",
    "probationary",
    "full_gates_passed",
    "full_gates_total",
    "probation_gates_passed",
    "probation_gates_total",
    "missing_full_gates",
    "missing_probation_gates",
    "gaps_to_full",
    "next_action",
)


def _cfg(tmp_path: Path) -> EngineConfig:
    return EngineConfig(
        raw={"paths": {"output_root": str(tmp_path / "outputs")}},
        path=tmp_path / "cfg.yaml",
    )


def _cohort(name: str, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "signal_cohort": name,
        "metadata_valid": True,
        "buy_fills": 5,
        "settled_fills": 3,
        "total_pnl_usdc": 1.0,
        "roi": 0.04,
        "monthly_run_rate_usdc": 30.0,
        "evidence_elapsed_hours": 2.0,
        "total_buy_cost_usdc": 25.0,
        "promoted": False,
        "probationary": False,
    }
    row.update(overrides)
    return row


def _write_signal_cohorts(cfg: EngineConfig, rows: list[dict[str, Any]]) -> None:
    write_json(cfg.governance_root / "signal_cohort_pnl.json", {"status": "ok", "cohorts": rows})


def _write_clv(cfg: EngineConfig, rows: list[dict[str, Any]]) -> None:
    write_json(
        cfg.governance_root / "closing_line_value.json",
        {
            "status": "ok",
            "cohorts": rows,
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )


def _by_cohort(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["cohort"]): row for row in result["review"]}


def _decision_subset(row: dict[str, Any]) -> dict[str, Any]:
    return {field: row.get(field) for field in DECISION_FIELDS}


def test_positive_clv_is_last_tiebreaker_without_changing_decision_fields(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _write_signal_cohorts(cfg, [_cohort("plain"), _cohort("positive_clv")])
    _write_clv(
        cfg,
        [
            {
                "signal_cohort": "plain",
                "final_positions": 3,
                "mean_final_clv": 0.0,
                "final_clv_ci_low": -0.01,
                "final_clv_ci_high": 0.01,
                "clv_evidence": "insufficient_clv_evidence",
            },
            {
                "signal_cohort": "positive_clv",
                "final_positions": 3,
                "mean_final_clv": 0.02,
                "final_clv_ci_low": 0.01,
                "final_clv_ci_high": 0.03,
                "clv_evidence": "positive_clv_evidence",
            },
        ],
    )

    result = build_promotion_review(cfg)
    rows = _by_cohort(result)

    assert [row["cohort"] for row in result["review"][:2]] == ["positive_clv", "plain"]
    assert _decision_subset(rows["positive_clv"]) == _decision_subset(rows["plain"])
    assert rows["positive_clv"]["clv_evidence"] == "positive_clv_evidence"
    assert result["clv_source"] == "closing_line_value.json"
    assert result["clv_is_advisory_only"] is True


def test_positive_clv_alone_cannot_change_promotion_decision(tmp_path: Path):
    cfg = _cfg(tmp_path)
    thin_row = _cohort(
        "thin_positive_clv",
        buy_fills=0,
        settled_fills=0,
        total_pnl_usdc=0.0,
        roi=0.0,
        monthly_run_rate_usdc=0.0,
        evidence_elapsed_hours=0.0,
        total_buy_cost_usdc=0.0,
    )
    _write_signal_cohorts(cfg, [thin_row])
    without_clv = _by_cohort(build_promotion_review(cfg))["thin_positive_clv"]

    _write_clv(
        cfg,
        [
            {
                "signal_cohort": "thin_positive_clv",
                "final_positions": 3,
                "mean_final_clv": 0.04,
                "final_clv_ci_low": 0.02,
                "final_clv_ci_high": 0.06,
                "clv_evidence": "positive_clv_evidence",
            }
        ],
    )
    with_clv = _by_cohort(build_promotion_review(cfg))["thin_positive_clv"]

    assert _decision_subset(with_clv) == _decision_subset(without_clv)
    assert with_clv["status"] == "collecting_evidence"
    assert with_clv["promoted"] is False
    assert with_clv["probationary"] is False
    assert with_clv["clv_evidence"] == "positive_clv_evidence"


def test_negative_clv_adds_advisory_note_without_changing_decision_fields(tmp_path: Path):
    cfg = _cfg(tmp_path)
    row = _cohort("negative_clv")
    _write_signal_cohorts(cfg, [row])
    without_clv = _by_cohort(build_promotion_review(cfg))["negative_clv"]

    _write_clv(
        cfg,
        [
            {
                "signal_cohort": "negative_clv",
                "final_positions": 3,
                "mean_final_clv": -0.04,
                "final_clv_ci_low": -0.06,
                "final_clv_ci_high": -0.02,
                "clv_evidence": "negative_clv_evidence",
            }
        ],
    )
    with_clv = _by_cohort(build_promotion_review(cfg))["negative_clv"]

    assert _decision_subset(with_clv) == _decision_subset(without_clv)
    assert with_clv["advisory_notes"] == [ADVISORY]
    assert with_clv["clv_evidence"] == "negative_clv_evidence"


def test_missing_clv_artifact_adds_only_default_annotations(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _write_signal_cohorts(cfg, [_cohort("no_clv")])

    result = build_promotion_review(cfg)
    written = read_json(cfg.governance_root / "promotion_review.json")
    row = result["review"][0]

    assert written["clv_source"] == "closing_line_value.json"
    assert written["clv_is_advisory_only"] is True
    assert row["clv_evidence"] == "insufficient_clv_evidence"
    assert row["clv_mean_final"] is None
    assert row["clv_ci_low"] is None
    assert row["clv_ci_high"] is None
    assert row["clv_final_positions"] == 0
    assert "advisory_notes" not in row
