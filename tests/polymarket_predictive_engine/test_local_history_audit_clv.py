from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from polymarket_predictive_engine.utils import write_json

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_audit_module():
    spec = importlib.util.spec_from_file_location(
        "audit_polymarket_local_history",
        REPO_ROOT / "scripts" / "audit_polymarket_local_history.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_config(tmp_path: Path) -> Path:
    base = (REPO_ROOT / "polymarket_predictive_config.example.yaml").read_text(encoding="utf-8")
    out_root = (tmp_path / "outputs").as_posix()
    db_path = (tmp_path / "work" / "engine.sqlite").as_posix()
    text = base.replace("output_root: outputs", f"output_root: {out_root}").replace(
        "database_path: work/polymarket/polymarket_engine.sqlite", f"database_path: {db_path}"
    )
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(text, encoding="utf-8")
    return cfg_path


def test_audit_reports_clv_without_touching_paper_decision(tmp_path: Path, capsys):
    audit = _load_audit_module()
    cfg_path = _write_config(tmp_path)
    governance_root = tmp_path / "outputs" / "polymarket_model_governance"
    report_path = governance_root / "local_history_audit_report.md"

    without_clv = audit.run(str(cfg_path))
    capsys.readouterr()
    assert without_clv["closing_line_value"]["positions_scored"] == 0
    assert "No CLV evidence collected yet." in report_path.read_text(encoding="utf-8")
    baseline_decision = json.dumps(without_clv["paper_decision"], sort_keys=True)

    write_json(
        governance_root / "closing_line_value.json",
        {
            "positions_scored": 7,
            "final_line_positions": 6,
            "mean_final_clv": 0.031,
            "positive_clv_cohorts": ["sports_other|worldcup"],
            "cohorts": [
                {
                    "signal_cohort": "sports_other|worldcup",
                    "positions": 7,
                    "final_positions": 6,
                    "mean_final_clv": 0.031,
                    "final_clv_ci_low": 0.01,
                    "final_clv_ci_high": 0.05,
                    "clv_evidence": "positive_clv_evidence",
                },
                {
                    "signal_cohort": "crypto|btc",
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

    with_clv = audit.run(str(cfg_path))
    capsys.readouterr()

    block = with_clv["closing_line_value"]
    assert block["positions_scored"] == 7
    assert block["positive_clv_cohorts"] == ["sports_other|worldcup"]
    # Cohorts without final evidence are filtered out of the audit block.
    assert [row["signal_cohort"] for row in block["cohorts"]] == ["sports_other|worldcup"]

    report = report_path.read_text(encoding="utf-8")
    assert "## Closing-line value (CLV)" in report
    assert "sports_other|worldcup — n_final=6" in report
    assert "evidence=positive_clv_evidence" in report
    assert "not a promotion trigger" in report

    # CLV must be report-only: the paper decision is identical with and without it.
    assert json.dumps(with_clv["paper_decision"], sort_keys=True) == baseline_decision
