"""End-to-end paper-trading session for the predictive engine.

Trains the calibration model, runs the *out-of-sample* paper simulation, and gates the
whole thing behind ``paper_trade_readiness``. It never touches a live order path: it only
calls training and the settled paper simulator, and refuses to run under live env flags or
a non-paper trading mode. The deliverable is one report with the honest forward-style P&L.
"""
from __future__ import annotations

from typing import Any

from .config import EngineConfig, load_config
from .models.calibration_v2 import train_calibration_model
from .paper_edge_simulator import simulate_paper_edge
from .readiness import paper_trade_readiness
from .utils import now_utc, write_json


def run_paper_session(cfg: EngineConfig) -> dict[str, Any]:
    # 1. Train/refresh the calibration model so the readiness gate can see it.
    try:
        calibration = train_calibration_model(cfg)
    except Exception as exc:  # insufficient labels etc. - surfaced, not fatal
        calibration = {"status": "refused", "reason": str(exc)}

    # 2. Mechanical go/no-go for paper (risk-free; does not require proven edge).
    gate = paper_trade_readiness(cfg)
    report: dict[str, Any] = {
        "generated_at_utc": now_utc(),
        "live_trading": False,
        "trading_mode": cfg.trading_mode,
        "calibration": calibration,
        "readiness": gate,
    }
    if not gate["approved_for_paper_trading"]:
        report["status"] = "blocked"
        write_json(cfg.governance_root / "paper_session_report.json", report)
        return report

    # 3. Out-of-sample, settled paper simulation.
    paper = simulate_paper_edge(cfg)
    curve = {c.get("edge_threshold"): c for c in paper.get("paper_pnl_by_edge_threshold", [])}
    at_min = curve.get(paper.get("minimum_edge", 0.03)) or {}
    report["status"] = "ran"
    report["paper"] = paper
    report["headline"] = {
        "evaluation": paper.get("evaluation"),
        "oos_test_markets": paper.get("test_markets"),
        "oos_settled_rows": paper.get("settled_rows"),
        "oos_roi_at_minimum_edge": at_min.get("roi_on_staked"),
        "oos_pnl_at_minimum_edge": at_min.get("total_realised_pnl"),
        "expected_profitable_oos": gate["expected_profitable_oos"],
        "verdict": (
            "paper engine ready and the model is profitable out of sample"
            if gate["expected_profitable_oos"]
            else "paper engine ready, but the model is NOT profitable out of sample - do not expect gains"
        ),
    }
    write_json(cfg.governance_root / "paper_session_report.json", report)
    return report


def main(config_path: str) -> dict[str, Any]:
    return run_paper_session(load_config(config_path))
