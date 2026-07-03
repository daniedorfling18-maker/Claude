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
    monkeypatch.setattr(refresh_module, "write_signal_cohort_pnl", lambda _con, _cfg: order.append("signal_cohort_pnl") or {"cohorts": []})
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

    def _closing_line(_cfg):
        order.append("closing_line_value")
        payload = {
            "status": "ok",
            "positions_scored": 3,
            "final_line_positions": 2,
            "mean_final_clv": 0.0123,
            "positive_clv_cohorts": ["macro_rates"],
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        }
        refresh_module.write_json(_cfg.governance_root / "closing_line_value.json", payload)
        return payload

    monkeypatch.setattr(refresh_module, "build_closing_line_value", _closing_line)
    monkeypatch.setattr(
        refresh_module,
        "build_smart_flow_clv",
        lambda _cfg: order.append("smart_flow_clv")
        or {"status": "ok", "fills_scored": 2, "positive_wallets": ["0xalpha"]},
    )
    monkeypatch.setattr(
        refresh_module,
        "build_edge_attribution",
        lambda _cfg: order.append("edge_attribution")
        or {
            "status": "ok",
            "attributed_positions": 2,
            "cohorts": [{"signal_cohort": "macro_rates", "attribution_class": "cost_dominated"}],
        },
    )
    monkeypatch.setattr(
        refresh_module,
        "run_algo_sweep",
        lambda _cfg: order.append("algo_sweep") or {"decision": "no_sweep_candidate_reached_minimum_train_fills"},
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

    assert order.index("paper_round_trip") < order.index("signal_cohort_pnl")
    assert order.index("paper_round_trip") < order.index("closing_line_value")
    assert order.index("closing_line_value") < order.index("smart_flow_clv")
    assert order.index("smart_flow_clv") < order.index("edge_attribution")
    assert order.index("closing_line_value") < order.index("edge_attribution")
    assert order.index("edge_attribution") < order.index("algo_sweep")
    assert order.index("algo_sweep") < order.index("signal_cohort_pnl")
    assert order.index("closing_line_value") < order.index("promotion_review")
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
    assert result["refreshed"]["closing_line_value"] is True
    assert result["refreshed"]["smart_flow_clv"] is True
    assert result["refreshed"]["edge_attribution"] is True
    assert result["refreshed"]["algo_sweep"] is True
    assert result["paper_round_trip_closed_trades"] == 2
    assert result["paper_round_trip_positive_trades"] == 1
    assert result["closing_line_positions_scored"] == 3
    assert result["closing_line_final_positions"] == 2
    assert result["closing_line_mean_final_clv"] == 0.0123
    assert result["closing_line_positive_cohorts"] == ["macro_rates"]
    assert result["smart_flow_fills_scored"] == 2
    assert result["smart_flow_positive_wallets"] == ["0xalpha"]
    assert result["edge_attribution_status"] == "ok"
    assert result["edge_attribution_positions"] == 2
    assert result["edge_attribution_cohort_classes"] == {"macro_rates": "cost_dominated"}
    assert result["algo_sweep_decision"] == "no_sweep_candidate_reached_minimum_train_fills"
    assert Path(cfg.governance_root / "closing_line_value.json").exists()
    assert Path(cfg.governance_root / "governance_refresh.json").exists()


def assert_order_contains(order: list[str], required: list[str]) -> None:
    missing = [item for item in required if item not in order]
    assert not missing, f"missing prior refresh steps: {missing}"
