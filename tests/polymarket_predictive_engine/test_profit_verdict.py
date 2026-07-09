"""Pre-registered $100/month verdict engine: the gates must be evidence-driven,
fail-closed, and impossible to flip without the registered thresholds being met."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.profit_verdict import _sign_test_p, build_profit_verdict
from polymarket_predictive_engine.utils import read_json, write_csv, write_json


def _config(tmp_path: Path):
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def _write_clv(cfg, *, finals: int, mean_final_clv, beat_close_rate) -> None:
    write_json(
        cfg.governance_root / "closing_line_value.json",
        {
            "status": "ok",
            "focus_view": {
                "diagnostic_cohort_substrings": ["updown", "up_down", "up-down"],
                "focus_final_positions": finals,
                "focus_mean_final_clv": mean_final_clv,
                "focus_beat_close_rate": beat_close_rate,
            },
        },
    )


def _write_positions(cfg, *, count: int, span_days: float) -> None:
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    rows = [
        {
            "shadow_position_id": f"pos-{i}",
            "signal_cohort": "sharp_anchor_wc",
            "opened_at": (start + timedelta(days=span_days * i / max(count - 1, 1))).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        for i in range(count)
    ]
    write_csv(
        cfg.governance_root / "closing_line_value_positions.csv",
        rows,
        fieldnames=["shadow_position_id", "signal_cohort", "opened_at"],
    )


def test_sign_test_matches_exact_binomial():
    # 10 of 12 beating the close under a fair coin: p = C(12,10)+C(12,11)+C(12,12) / 2^12
    assert abs(_sign_test_p(10, 12) - (66 + 12 + 1) / 4096) < 1e-12
    assert _sign_test_p(0, 0) is None


def test_verdict_stays_insufficient_below_sample_floor(tmp_path):
    cfg = _config(tmp_path)
    _write_clv(cfg, finals=3, mean_final_clv=0.05, beat_close_rate=1.0)

    verdict = build_profit_verdict(cfg)

    assert verdict["verdict"] == "insufficient_evidence"
    assert verdict["gates"]["A_edge_exists"]["state"] == "pending"
    # A promising early mean must NOT unlock downstream gates early.
    assert verdict["gates"]["B_edge_survives_costs"]["state"] == "pending"
    assert verdict["paper_trading_invoked"] is False
    assert (cfg.governance_root / "profit_verdict.json").exists()


def test_verdict_is_no_when_sample_floor_met_and_clv_nonpositive(tmp_path):
    cfg = _config(tmp_path)
    _write_clv(cfg, finals=14, mean_final_clv=-0.004, beat_close_rate=0.43)

    verdict = build_profit_verdict(cfg)

    assert verdict["verdict"] == "no_for_tested_edge_classes"
    assert verdict["gates"]["A_edge_exists"]["state"] == "fail"


def test_verdict_yes_requires_all_three_gates_and_stays_paper_gated(tmp_path):
    cfg = _config(tmp_path)
    # 13/14 beat the close, strong mean: sign test p ~= 0.00092.
    _write_clv(cfg, finals=14, mean_final_clv=0.03, beat_close_rate=13 / 14)
    # 60 focus entries over 10 days = 6/day; achievable = 6 x $10 x 30 = $1800/mo.
    # required = 100 / (0.03 - 0.005) = $4000/mo -> Gate C FAILS -> verdict NO.
    _write_positions(cfg, count=60, span_days=10)

    verdict = build_profit_verdict(cfg)
    assert verdict["gates"]["A_edge_exists"]["state"] == "pass"
    assert verdict["gates"]["B_edge_survives_costs"]["state"] == "pass"
    assert verdict["gates"]["C_scale_feasible"]["state"] == "fail"
    assert verdict["verdict"] == "no_for_tested_edge_classes"

    # Denser flow makes scale feasible: 240 entries over 10 days = 24/day -> $7200/mo.
    _write_positions(cfg, count=240, span_days=10)
    verdict = build_profit_verdict(cfg)
    assert verdict["gates"]["C_scale_feasible"]["state"] == "pass"
    assert verdict["verdict"] == "yes_edge_evidenced_pending_paper_confirmation"
    # Even the YES never authorises trading by itself.
    assert verdict["paper_trading_invoked"] is False
    assert verdict["live_trading_invoked"] is False

    persisted = read_json(cfg.governance_root / "profit_verdict.json")
    assert persisted["verdict"] == verdict["verdict"]


def test_frozen_cohort_entries_do_not_inflate_achievable_turnover(tmp_path):
    cfg = _config(tmp_path)
    _write_clv(cfg, finals=14, mean_final_clv=0.06, beat_close_rate=13 / 14)
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    rows = [
        {
            "shadow_position_id": f"frozen-{i}",
            "signal_cohort": "crypto_updown_fast",
            "opened_at": (start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        for i in range(500)
    ]
    write_csv(
        cfg.governance_root / "closing_line_value_positions.csv",
        rows,
        fieldnames=["shadow_position_id", "signal_cohort", "opened_at"],
    )

    verdict = build_profit_verdict(cfg)

    gate_c = verdict["gates"]["C_scale_feasible"]
    assert gate_c["observed_focus_entries"] == 0
    assert gate_c["state"] == "pending"
    assert verdict["verdict"] == "insufficient_evidence"
