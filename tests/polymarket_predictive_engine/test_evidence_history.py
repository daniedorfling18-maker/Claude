from __future__ import annotations

from pathlib import Path

from polymarket_predictive_engine.cli import COMMANDS
from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.evidence_history import append_evidence_history
from polymarket_predictive_engine.utils import read_csv_rows, read_json, write_json


def _cfg(tmp_path: Path) -> EngineConfig:
    return EngineConfig(
        raw={"paths": {"output_root": str(tmp_path / "outputs")}},
        path=tmp_path / "cfg.yaml",
    )


def _write_artifacts(cfg: EngineConfig, *, clv_time: str = "2026-07-03T10:00:00Z") -> None:
    write_json(
        cfg.governance_root / "closing_line_value.json",
        {
            "status": "ok",
            "generated_at_utc": clv_time,
            "positions_scored": 4,
            "final_line_positions": 3,
            "mean_final_clv": 0.0123456,
            "positive_clv_cohorts": ["macro_rates", "tennis_tennis_winner"],
            "cohorts": [
                {"signal_cohort": "macro_rates", "clv_evidence": "positive_clv_evidence"},
                {"signal_cohort": "thin", "clv_evidence": "insufficient_clv_evidence"},
            ],
        },
    )
    write_json(
        cfg.governance_root / "edge_attribution.json",
        {
            "status": "ok",
            "generated_at_utc": "2026-07-03T10:01:00Z",
            "attributed_positions": 2,
            "cohorts": [
                {
                    "signal_cohort": "macro_rates",
                    "attribution_class": "positive_edge_confirmed",
                    "total_pnl_usdc": 1.25,
                },
                {
                    "signal_cohort": "tennis_tennis_winner",
                    "attribution_class": "cost_dominated",
                    "total_pnl_usdc": -0.5,
                },
            ],
        },
    )
    write_json(
        cfg.output_root / "polymarket_algo" / "algo_sweep_summary.json",
        {
            "status": "ok",
            "generated_at_utc": "2026-07-03T10:02:00Z",
            "decision": "sweep_candidate_validated_shadow_only",
            "combos_tested": 9,
            "selected": {
                "strategy": "bid_momentum_tight_shadow",
                "params": '{"min_bid_move":0.01}',
                "validation_pnl_usdc": 0.42,
            },
        },
    )


def test_evidence_history_appends_once_per_artifact_generation(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write_artifacts(cfg)

    first = append_evidence_history(cfg)
    second = append_evidence_history(cfg)
    _write_artifacts(cfg, clv_time="2026-07-03T10:03:00Z")
    third = append_evidence_history(cfg)

    rows = read_csv_rows(cfg.governance_root / "evidence_history.csv")
    assert first["appended_sources"] == ["closing_line_value", "edge_attribution", "algo_sweep"]
    assert second["appended_rows"] == 0
    assert set(second["skipped_unchanged_sources"]) == {"closing_line_value", "edge_attribution", "algo_sweep"}
    assert third["appended_sources"] == ["closing_line_value"]
    assert [row["source"] for row in rows] == [
        "closing_line_value",
        "edge_attribution",
        "algo_sweep",
        "closing_line_value",
    ]
    clv = rows[0]
    assert clv["recorded_at_utc"] == "2026-07-03T10:00:00Z"
    assert clv["positions_scored_attributed"] == "4"
    assert clv["final_line_positions"] == "3"
    assert clv["mean_final_clv"] == "0.012346"
    assert clv["positive_cohorts"] == "macro_rates|tennis_tennis_winner"
    assert "positive_clv_evidence=1" in clv["decision_or_class_summary"]
    edge = rows[1]
    assert edge["positions_scored_attributed"] == "2"
    assert edge["positive_cohorts"] == "macro_rates"
    assert edge["total_pnl_usdc"] == "0.75"
    assert "cost_dominated=1" in edge["decision_or_class_summary"]
    algo = rows[2]
    assert algo["positions_scored_attributed"] == "9"
    assert algo["total_pnl_usdc"] == "0.42"
    assert "sweep_candidate_validated_shadow_only" in algo["decision_or_class_summary"]
    status = read_json(cfg.governance_root / "evidence_history_status.json")
    assert status["total_rows"] == 4
    assert status["paper_trading_invoked"] is False
    assert status["live_trading_invoked"] is False


def test_evidence_history_missing_artifacts_do_not_crash(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)

    summary = append_evidence_history(cfg)

    assert summary["appended_rows"] == 0
    assert set(summary["missing_sources"]) == {"closing_line_value", "edge_attribution", "algo_sweep"}
    assert read_csv_rows(cfg.governance_root / "evidence_history.csv") == []


def test_evidence_history_cli_is_registered() -> None:
    assert "evidence-history" in COMMANDS
