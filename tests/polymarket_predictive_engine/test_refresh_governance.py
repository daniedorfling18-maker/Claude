from __future__ import annotations

from pathlib import Path

from polymarket_predictive_engine import refresh_governance as refresh_module
from polymarket_predictive_engine.config import EngineConfig


def test_refresh_governance_rebuilds_price_action_paper_signals_before_audit(tmp_path, monkeypatch):
    cfg = EngineConfig(
        raw={
            "paths": {
                "output_root": str(tmp_path / "outputs"),
                "database_path": str(tmp_path / "work" / "paper.sqlite"),
            }
        },
        path=tmp_path / "config.yaml",
    )
    order: list[str] = []

    class _Connection:
        def close(self) -> None:
            order.append("close_db")

    monkeypatch.setattr(refresh_module, "connect_db", lambda _path: _Connection())
    monkeypatch.setattr(refresh_module, "write_signal_cohort_pnl", lambda _con, _cfg: {"cohorts": []})
    monkeypatch.setattr(
        refresh_module,
        "build_price_action_scout",
        lambda _cfg: order.append("price_action_scout") or {"decision": "collect_more", "closed_trades": 3},
    )
    monkeypatch.setattr(
        refresh_module,
        "build_microstructure_edge_lab",
        lambda _cfg: order.append("price_action_microstructure") or {"decision": "collect_more", "trade_events": 4},
    )
    monkeypatch.setattr(
        refresh_module,
        "build_paper_round_trip_evidence",
        lambda _cfg: order.append("paper_round_trip")
        or {"closed_round_trips": 2, "positive_round_trips": 1, "realized_pnl_usdc": 3.5},
    )
    monkeypatch.setattr(
        refresh_module,
        "train_price_action_model",
        lambda _cfg: (
            assert_order_contains(order, ["price_action_scout", "price_action_microstructure", "paper_round_trip"])
            or order.append("price_action_model")
            or {"decision": "collect_more", "promotion_ready": False}
        ),
    )
    monkeypatch.setattr(
        refresh_module,
        "build_price_action_feedback",
        lambda _cfg: order.append("price_action_feedback") or {"learning_state": "collect_more"},
    )
    monkeypatch.setattr(
        refresh_module,
        "build_price_action_paper_signals",
        lambda _cfg: order.append("price_action_paper_signals")
        or {"signals": 1, "rejections": 2, "decision": "signals_ready_for_paper_broker"},
    )
    monkeypatch.setattr(
        refresh_module,
        "build_quant_research_status",
        lambda _cfg: order.append("quant_research") or {"implementation_complete": True},
    )

    def _audit(_cfg):
        assert "price_action_paper_signals" in order
        order.append("trade_signal_audit")
        return {"verdict": "current_signals_require_broker_or_manual_review"}

    monkeypatch.setattr(refresh_module, "build_trade_signal_audit", _audit)
    monkeypatch.setattr(refresh_module, "build_promotion_review", lambda _cfg: order.append("promotion_review") or {"status": "ok"})
    monkeypatch.setattr(refresh_module, "build_goal_plan", lambda _cfg: order.append("goal_plan") or {"status": "ok"})
    monkeypatch.setattr(refresh_module, "build_profit_sprint", lambda _cfg: order.append("profit_sprint") or {"decision": "ok"})
    monkeypatch.setattr(refresh_module, "build_research_focus", lambda _cfg: order.append("research_focus") or {"status": "ok"})
    monkeypatch.setattr(
        refresh_module,
        "paper_live_promotion_gate",
        lambda _cfg: order.append("promotion_gate")
        or {"approved_for_paper_trading": False, "approved_for_live_trading": False, "paper_blockers": [], "live_blockers": []},
    )
    monkeypatch.setattr(refresh_module, "governance_report", lambda _cfg: order.append("governance_report") or {"status": "ok"})

    def _dashboard(_cfg):
        assert order.index("price_action_paper_signals") < order.index("trade_signal_audit")
        order.append("dashboard")
        return {"status": "ok"}

    monkeypatch.setattr(refresh_module, "render_dashboard", _dashboard)

    result = refresh_module.refresh_governance(cfg)

    assert order.index("price_action_scout") < order.index("price_action_model")
    assert order.index("price_action_microstructure") < order.index("price_action_model")
    assert order.index("paper_round_trip") < order.index("price_action_model")
    assert order.index("price_action_feedback") < order.index("price_action_paper_signals")
    assert order.index("price_action_paper_signals") < order.index("trade_signal_audit")
    assert order.index("trade_signal_audit") < order.index("dashboard")
    assert result["refreshed"]["price_action_paper_signals"] is True
    assert result["price_action_paper_signal_count"] == 1
    assert result["price_action_paper_rejection_count"] == 2
    assert result["refreshed"]["price_action_scout"] is True
    assert result["refreshed"]["price_action_microstructure"] is True
    assert result["refreshed"]["paper_round_trip_evidence"] is True
    assert result["paper_round_trip_closed_trades"] == 2
    assert result["paper_round_trip_positive_trades"] == 1
    assert Path(cfg.governance_root / "governance_refresh.json").exists()


def assert_order_contains(order: list[str], required: list[str]) -> None:
    missing = [item for item in required if item not in order]
    assert not missing, f"missing prior refresh steps: {missing}"
