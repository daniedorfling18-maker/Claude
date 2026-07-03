from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

from polymarket_predictive_engine.utils import read_json, write_json


AUDIT_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "audit_polymarket_local_history.py"
SPEC = importlib.util.spec_from_file_location("audit_polymarket_local_history", AUDIT_SCRIPT)
assert SPEC is not None and SPEC.loader is not None
audit_polymarket_local_history = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_polymarket_local_history)


def _config_path(tmp_path: Path) -> Path:
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def test_local_history_audit_reports_clv_without_changing_paper_decision(tmp_path: Path):
    config_path = _config_path(tmp_path)

    without_clv = audit_polymarket_local_history.run(str(config_path))
    decision_without_clv = without_clv["paper_decision"]
    empty_report = Path(tmp_path / "outputs" / "polymarket_model_governance" / "local_history_audit_report.md")
    assert "Closing-line value (CLV)" in empty_report.read_text(encoding="utf-8")
    assert "No CLV evidence collected yet." in empty_report.read_text(encoding="utf-8")

    governance_root = tmp_path / "outputs" / "polymarket_model_governance"
    write_json(
        governance_root / "closing_line_value.json",
        {
            "positions_scored": 5,
            "final_line_positions": 3,
            "mean_final_clv": 0.0142,
            "positive_clv_cohorts": ["macro_rates"],
            "cohorts": [
                {
                    "signal_cohort": "macro_rates",
                    "positions": 4,
                    "final_positions": 3,
                    "mean_final_clv": 0.0142,
                    "final_clv_ci_low": 0.003,
                    "final_clv_ci_high": 0.025,
                    "clv_evidence": "positive_clv_evidence",
                },
                {
                    "signal_cohort": "provisional_only",
                    "positions": 1,
                    "final_positions": 0,
                    "mean_final_clv": None,
                    "final_clv_ci_low": None,
                    "final_clv_ci_high": None,
                    "clv_evidence": "insufficient_clv_evidence",
                },
            ],
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )

    write_json(
        governance_root / "edge_attribution.json",
        {
            "status": "ok",
            "generated_at_utc": "2026-07-02T12:10:00Z",
            "closed_positions_seen": 3,
            "attributed_positions": 2,
            "skipped_unattributable_closed": 1,
            "cohorts": [
                {
                    "signal_cohort": "macro_rates",
                    "attributed_positions": 2,
                    "total_pnl_usdc": -2.2,
                    "execution_cost_usdc": 0.4,
                    "line_movement_usdc": -1.0,
                    "settlement_surprise_usdc": -0.8,
                    "attribution_class": "cost_dominated",
                    "recommended_action": "tighten_execution_filters",
                }
            ],
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )
    write_json(
        tmp_path / "outputs" / "polymarket_algo" / "algo_sweep_summary.json",
        {
            "status": "ok",
            "decision": "sweep_candidate_failed_out_of_sample_validation",
            "strategy": "tight_spread_join_bid_shadow",
            "events_total": 90,
            "train_events": 60,
            "validation_events": 30,
            "combos_tested": 2,
            "train_candidates": 1,
            "selected": {
                "tight_spread_maximum": 0.02,
                "minimum_book_imbalance": 0.55,
                "train_pnl_usdc": 0.2,
                "validation_pnl_usdc": -0.1,
            },
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )

    with_clv = audit_polymarket_local_history.run(str(config_path))
    summary = read_json(governance_root / "local_history_audit_summary.json")
    report = (governance_root / "local_history_audit_report.md").read_text(encoding="utf-8")

    assert with_clv["paper_decision"] == decision_without_clv
    assert summary["paper_decision"] == decision_without_clv
    assert summary["closing_line_value"]["positions_scored"] == 5
    assert summary["closing_line_value"]["final_line_positions"] == 3
    assert summary["closing_line_value"]["mean_final_clv"] == 0.0142
    assert summary["closing_line_value"]["positive_clv_cohorts"] == ["macro_rates"]
    assert [row["signal_cohort"] for row in summary["closing_line_value"]["cohorts"]] == ["macro_rates"]
    assert summary["edge_attribution"]["attributed_positions"] == 2
    assert summary["edge_attribution"]["cohort_classes"] == {"macro_rates": "cost_dominated"}
    assert summary["algo_sweep"]["decision"] == "sweep_candidate_failed_out_of_sample_validation"
    assert "Closing-line value (CLV)" in report
    assert "macro_rates — n_final=3, mean_final_clv=0.0142" in report
    assert "evidence=positive_clv_evidence" in report
    assert "Edge attribution" in report
    assert "macro_rates — positions=2" in report
    assert "class=cost_dominated" in report
    assert "Algo sweep lab" in report
    assert "decision=sweep_candidate_failed_out_of_sample_validation" in report
