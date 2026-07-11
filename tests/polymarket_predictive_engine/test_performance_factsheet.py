"""WO-60 evidence-classed performance factsheet tests."""
from __future__ import annotations

import math
from pathlib import Path
import statistics

import pytest
import yaml

from polymarket_predictive_engine.cli import COMMANDS
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.dashboard import render_dashboard
from polymarket_predictive_engine.performance_factsheet import (
    SIMULATED_BANNER,
    _performance_stats,
    build_performance_factsheet,
)
from polymarket_predictive_engine.utils import read_json, write_csv


def _stats_settings(*, minimum: int = 20, iterations: int = 500) -> dict:
    return {
        "risk_free_rate_annual": 0.0,
        "min_daily_observations": minimum,
        "bootstrap_iterations": iterations,
        "bootstrap_seed": 20260711,
        "periods_per_year": 365,
    }


def _config(tmp_path: Path):
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    raw["performance_factsheet"] = {
        "enabled": True,
        "risk_free_rate_annual": 0.0,
        "min_daily_observations": 2,
        "bootstrap_iterations": 100,
        "bootstrap_seed": 7,
        "periods_per_year": 365,
    }
    raw["maker_live_test"]["wallet_address"] = "0xabc"
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def test_known_sharpe_and_sortino_are_recovered():
    returns = [0.02, 0.01, -0.01] * 10
    stats = _performance_stats(
        returns,
        dates=[f"2026-01-{index + 1:02d}" for index in range(len(returns))],
        settings=_stats_settings(),
        series_id="planted_known_effect",
    )
    mean = statistics.mean(returns)
    expected_sharpe = mean / statistics.stdev(returns) * math.sqrt(365)
    expected_downside = math.sqrt(sum(min(value, 0.0) ** 2 for value in returns) / len(returns))
    expected_sortino = mean / expected_downside * math.sqrt(365)

    assert stats["annualised_sharpe"] == pytest.approx(expected_sharpe, abs=1e-8)
    assert stats["annualised_sortino"] == pytest.approx(expected_sortino, abs=1e-8)
    assert stats["bootstrap_sharpe_90_ci"]["low"] is not None
    assert stats["bootstrap_sharpe_90_ci"]["high"] is not None


def test_zero_edge_series_has_sharpe_ci_covering_zero():
    returns = [-0.01, 0.01] * 20
    stats = _performance_stats(
        returns,
        dates=[f"day-{index:03d}" for index in range(len(returns))],
        settings=_stats_settings(iterations=1000),
        series_id="martingale_null",
    )

    ci = stats["bootstrap_sharpe_90_ci"]
    assert stats["annualised_sharpe"] == pytest.approx(0.0, abs=1e-12)
    assert ci["low"] < 0 < ci["high"]


def test_drawdown_depth_and_duration_are_exact():
    stats = _performance_stats(
        [0.10, -0.10, -0.10, 0.30],
        dates=["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"],
        settings=_stats_settings(minimum=2, iterations=50),
        series_id="known_drawdown",
    )

    assert stats["max_drawdown_depth"] == 0.19
    assert stats["max_drawdown_duration_days"] == 2


def test_sample_floor_suppresses_every_annualised_field_and_ci():
    stats = _performance_stats(
        [0.01, -0.005] * 5,
        dates=[f"day-{index}" for index in range(10)],
        settings=_stats_settings(minimum=20),
        series_id="short_sample",
    )

    assert stats["annualised_return"] is None
    assert stats["annualised_sharpe"] is None
    assert stats["annualised_sortino"] is None
    assert stats["annualised_calmar"] is None
    assert stats["bootstrap_sharpe_90_ci"]["low"] is None
    assert stats["bootstrap_sharpe_90_ci"]["high"] is None
    assert stats["annualised_metrics_reason"] == "requires at least 20 daily observations; found 10"


def test_registry_banners_outputs_cli_and_dashboard_are_honest(tmp_path):
    cfg = _config(tmp_path)
    write_csv(
        cfg.output_root / "polymarket_portfolio" / "portfolio_snapshots.csv",
        [
            {"created_at": "2026-07-01T08:00:00Z", "equity_usdc": 100.0},
            {"created_at": "2026-07-01T20:00:00Z", "equity_usdc": 101.0},
            {"created_at": "2026-07-02T20:00:00Z", "equity_usdc": 99.0},
            {"created_at": "2026-07-03T20:00:00Z", "equity_usdc": 102.0},
        ],
    )
    shadow_rows = []
    for cohort, rois in {"family_a": [0.01, -0.01, 0.03], "family_b": [0.0, 0.02, 0.01]}.items():
        for day, roi in enumerate(rois, start=1):
            shadow_rows.append(
                {
                    "generated_at_utc": f"2026-07-{day:02d}T12:00:00Z",
                    "signal_cohort": cohort,
                    "shadow_total_pnl_usdc": roi * 100.0,
                    "shadow_total_buy_cost_usdc": 100.0,
                    "shadow_roi": roi,
                }
            )
    write_csv(cfg.governance_root / "shadow_signal_cohort_pnl.csv", shadow_rows)
    write_csv(
        cfg.output_root / "maker_carry" / "maker_carry_history.csv",
        [
            {
                "generated_at_utc": f"2026-07-{day:02d}T08:00:00Z",
                "portfolio_net_carry_usd_per_day": net,
                "portfolio_capital_usd": 100.0,
            }
            for day, net in enumerate([1.0, -0.5, 1.5], start=1)
        ],
    )
    write_csv(
        cfg.output_root / "maker_carry" / "maker_live_test_history.csv",
        [
            {"generated_at_utc": "2026-07-01T08:00:00Z", "net_score_usd": 1.0},
            {"generated_at_utc": "2026-07-02T08:00:00Z", "net_score_usd": 0.5},
            {"generated_at_utc": "2026-07-03T08:00:00Z", "net_score_usd": 2.0},
        ],
    )

    result = build_performance_factsheet(cfg)

    systems = {row["system_id"] for row in result["series"]}
    assert systems == {"paper_portfolio", "shadow_signal_cohorts", "maker_carry_model", "maker_live_test"}
    assert any(row["series_id"] == "shadow_signal_cohorts:aggregate" for row in result["series"])
    assert all(
        row["banner"] == SIMULATED_BANNER and row["external_presentation_eligible"] is False
        for row in result["series"]
        if row["evidence_class"] != "live_real_money"
    )
    live = next(row for row in result["series"] if row["series_id"] == "maker_live_test")
    assert live["external_presentation_eligible"] is True
    assert live["external_presentation_ready"] is True
    assert result["paper_trading_invoked"] is False
    assert result["live_trading_invoked"] is False
    assert result["policy_or_gate_inputs"] is False
    assert "performance-factsheet" in COMMANDS

    persisted = read_json(cfg.output_root / "performance" / "performance_factsheet.json")
    markdown = (cfg.output_root / "performance" / "performance_factsheet.md").read_text(encoding="utf-8")
    assert persisted["series_count"] == len(result["series"])
    assert SIMULATED_BANNER in markdown
    assert "bootstrap 90% CI" in markdown
    assert "REPORTING ONLY" in markdown

    rendered = render_dashboard(cfg)
    dashboard_data = read_json(cfg.output_root / "polymarket_dashboard" / "dashboard_data.json")
    dashboard_html = Path(rendered["dashboard_file"]).read_text(encoding="utf-8")
    assert dashboard_data["performance_factsheet"]["series_count"] == len(result["series"])
    assert 'id="performanceFactsheet"' in dashboard_html
    assert "Sharpe (90% CI)" in dashboard_html


def test_disabled_mode_writes_inert_artifacts(tmp_path):
    cfg = _config(tmp_path)
    raw = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    raw["performance_factsheet"]["enabled"] = False
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")

    result = build_performance_factsheet(load_config(tmp_path / "config.yaml"))

    assert result["status"] == "disabled"
    assert result["paper_trading_invoked"] is False
    assert result["live_trading_invoked"] is False
    assert (cfg.output_root / "performance" / "performance_factsheet.json").exists()
    assert (cfg.output_root / "performance" / "performance_factsheet.md").exists()
