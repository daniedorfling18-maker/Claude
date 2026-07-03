from __future__ import annotations

import importlib.util
from pathlib import Path

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.utils import now_utc, write_json

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "run_polymarket_live_paper_loop",
    ROOT / "scripts" / "run_polymarket_live_paper_loop.py",
)
assert SPEC and SPEC.loader
loop = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(loop)


def _cfg(tmp_path: Path, dutch_arb: dict | None = None) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "trading": {"mode": "paper"},
            "dutch_arb": dutch_arb or {},
        },
        path=tmp_path / "cfg.yaml",
    )


def test_dutch_arb_loop_pass_runs_one_bounded_scan(tmp_path, monkeypatch):
    cfg = _cfg(
        tmp_path,
        {
            "enabled": True,
            "pass_interval_minutes": 15,
            "max_events_per_pass": 7,
            "max_pages_per_pass": 2,
            "alert_annualised": 0.25,
            "request_pause_seconds": 0.0,
        },
    )
    calls: list[dict] = []

    def fake_monitor(_cfg, **kwargs):
        calls.append(kwargs)
        return {
            "status": "paper_analysis",
            "generated_at_utc": "2026-07-03T08:10:00Z",
            "scan_stats_latest_poll": {"discovered": 7},
            "events_priced_complete_latest_poll": 3,
            "complete_arbs_latest_poll": 1,
            "alerts_total": 1,
            "persistent_alert_count": 0,
            "best_annualised_return_on_capital": 0.31,
            "best_opportunity": {"event_id": "e1", "ask_sum": 0.95},
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        }

    monkeypatch.setattr(loop, "run_dutch_arb_monitor", fake_monitor)

    result = loop.run_dutch_arb_loop_pass(cfg)

    assert result["status"] == "paper_analysis"
    assert result["paper_trading_invoked"] is False
    assert result["live_trading_invoked"] is False
    assert calls == [
        {
            "polls": 1,
            "poll_seconds": 0,
            "max_events": 7,
            "max_pages": 2,
            "max_outcomes": 80,
            "min_ask_sum": 0.85,
            "min_annualised": 0.0,
            "alert_annualised": 0.25,
            "pause": 0.0,
            "timeout": 20,
        }
    ]


def test_dutch_arb_loop_pass_skips_until_interval_is_due(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, {"enabled": True, "pass_interval_minutes": 15})
    write_json(
        cfg.output_root / "polymarket_arbitrage" / "dutch_arb_monitor_summary.json",
        {"generated_at_utc": now_utc(), "status": "paper_analysis"},
    )
    monkeypatch.setattr(
        loop,
        "run_dutch_arb_monitor",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("monitor should be skipped")),
    )

    result = loop.run_dutch_arb_loop_pass(cfg)

    assert result["status"] == "skipped_interval"
    assert result["paper_trading_invoked"] is False
    assert result["live_trading_invoked"] is False
    assert result["next_due_minutes"] > 0
