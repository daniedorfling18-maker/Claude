import json

from pytest import approx

import polymarket_predictive_engine.dutch_arb_monitor as monitor
from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.dutch_arb_monitor import (
    ArbLeg,
    annualised_return_on_capital,
    build_alerts,
    build_execution_plan,
    diff_states,
    dry_run_orders,
    rank_plans,
    run_dutch_arb_monitor,
)


def _legs(asks, sizes=None):
    sizes = sizes or [100.0] * len(asks)
    return [ArbLeg(outcome=f"o{i}", token_id=f"t{i}", best_ask=a, ask_size=s)
            for i, (a, s) in enumerate(zip(asks, sizes))]


def test_build_execution_plan_locks_when_asks_sum_below_one():
    plan = build_execution_plan("e1", "Event 1", _legs([0.30, 0.30, 0.35], [100, 50, 200]), days_to_resolution=365)
    assert plan.is_arb is True and plan.complete is True
    assert plan.ask_sum == approx(0.95)
    assert plan.lock_per_set == approx(0.05)
    assert plan.executable_sets == approx(50.0)              # thinnest leg
    assert plan.capital_usdc == approx(47.5)                 # 0.95 * 50
    assert plan.profit_usdc == approx(2.5)                   # 0.05 * 50
    assert plan.annualised_return_on_capital == approx(round((0.05 / 0.95) * 365 / 365, 4))


def test_incomplete_basket_is_not_an_arb():
    # Two priced legs summing to 0.50 -> outcome set is missing legs, the apparent lock is an artefact.
    plan = build_execution_plan("e2", "Partial", _legs([0.30, 0.20]), days_to_resolution=100, min_ask_sum=0.85)
    assert plan.complete is False
    assert plan.is_arb is False


def test_overround_basket_has_no_lock():
    plan = build_execution_plan("e3", "Overround", _legs([0.40, 0.35, 0.30]), days_to_resolution=30)
    assert plan.is_arb is False
    assert plan.lock_per_set == approx(0.0)
    assert plan.annualised_return_on_capital == approx(0.0)


def test_annualised_scales_with_horizon_and_is_none_when_unknown():
    one_year = annualised_return_on_capital(0.05, 0.95, 365)
    two_year = annualised_return_on_capital(0.05, 0.95, 730)
    assert two_year == approx(one_year / 2, rel=1e-3)
    assert annualised_return_on_capital(0.05, 0.95, None) is None    # unknown horizon
    assert annualised_return_on_capital(0.0, 0.95, 365) == approx(0.0)  # no lock


def test_dry_run_orders_one_buy_per_leg_sized_to_sets():
    plan = build_execution_plan("e1", "Event 1", _legs([0.30, 0.30, 0.35], [100, 50, 200]), days_to_resolution=365)
    orders = dry_run_orders(plan)
    assert len(orders) == 3
    assert all(o["side"] == "BUY" and o["dry_run"] is True for o in orders)
    assert all(o["size"] == approx(50.0) for o in orders)           # every leg sized to thinnest depth
    assert sum(o["cost_usdc"] for o in orders) == approx(plan.capital_usdc)


def test_rank_plans_filters_and_sorts_by_annualised():
    hi = build_execution_plan("hi", "hi", _legs([0.30, 0.30, 0.30]), days_to_resolution=30)    # big ann
    lo = build_execution_plan("lo", "lo", _legs([0.32, 0.33, 0.34]), days_to_resolution=365)   # small ann
    over = build_execution_plan("ov", "ov", _legs([0.40, 0.35, 0.30]), days_to_resolution=30)  # not an arb
    ranked = rank_plans([lo, over, hi], min_annualised=0.0)
    assert [p.event_id for p in ranked] == ["hi", "lo"]            # over filtered out, hi first
    # threshold drops the low-annualised one
    assert [p.event_id for p in rank_plans([lo, hi], min_annualised=hi.annualised_return_on_capital)] == ["hi"]


def test_diff_states_tracks_appeared_persisting_cleared():
    a = build_execution_plan("A", "A", _legs([0.30, 0.30, 0.30]), days_to_resolution=30)
    c = build_execution_plan("C", "C", _legs([0.31, 0.31, 0.31]), days_to_resolution=30)
    diff = diff_states({"A", "B"}, [a, c])
    assert diff.appeared == ["C"]
    assert diff.persisting == ["A"]
    assert diff.cleared == ["B"]


def test_build_alerts_respects_threshold_and_unknown_horizon():
    arb = build_execution_plan("A", "A", _legs([0.30, 0.30, 0.30]), days_to_resolution=3650)   # tiny ann
    big = build_execution_plan("B", "B", _legs([0.30, 0.30, 0.30]), days_to_resolution=10)     # large ann
    no_h = build_execution_plan("H", "H", _legs([0.30, 0.30, 0.30]), days_to_resolution=None)  # unknown horizon
    alerts = build_alerts([arb, big, no_h], alert_annualised=0.10, poll=1)
    ids = {a["event_id"] for a in alerts}
    assert "B" in ids                # clears the bar
    assert "H" in ids                # unknown horizon always alerts
    assert "A" not in ids            # below the bar


def test_run_monitor_bounded_persists_state_and_stays_dry_run(tmp_path, monkeypatch):
    cfg = EngineConfig(raw={"paths": {"output_root": str(tmp_path / "outputs")}}, path=tmp_path / "cfg.yaml")
    arb = build_execution_plan("A", "A", _legs([0.30, 0.30, 0.30], [40, 50, 60]), days_to_resolution=30)
    # Patch the network scan so the loop runs offline.
    monkeypatch.setattr(monitor, "scan_once", lambda cfg, **k: ([arb], {"discovered": 1, "priced_complete": 1}))
    summary = run_dutch_arb_monitor(cfg, polls=2, poll_seconds=0, alert_annualised=0.0)

    assert summary["live_trading"] is False and summary["dry_run"] is True
    assert summary["paper_trading_invoked"] is False
    assert summary["live_trading_invoked"] is False
    assert summary["continuous"] is False and summary["polls_run"] == 2
    assert summary["complete_arbs_latest_poll"] == 1
    assert summary["alerts_total"] >= 1
    assert summary["best_execution_plan"]                       # non-empty dry-run orders
    assert all(o["dry_run"] is True for o in summary["best_execution_plan"])

    arb_dir = tmp_path / "outputs" / "polymarket_arbitrage"
    assert (arb_dir / "dutch_arb_monitor_opportunities.csv").exists()
    assert (arb_dir / "dutch_arb_opportunities.csv").exists()
    assert (arb_dir / "dutch_arb_monitor_alerts.csv").exists()
    assert (arb_dir / "dutch_arb_latest.json").exists()
    persisted = json.loads((arb_dir / "dutch_arb_monitor_summary.json").read_text())
    assert persisted["best_opportunity"]["event_id"] == "A"    # summary written to disk each poll


def test_run_monitor_tracks_persistent_alerts_across_bounded_passes(tmp_path, monkeypatch):
    cfg = EngineConfig(raw={"paths": {"output_root": str(tmp_path / "outputs")}}, path=tmp_path / "cfg.yaml")
    arb = build_execution_plan("PERSIST", "Persistent basket", _legs([0.30, 0.30, 0.30]), days_to_resolution=30)
    monkeypatch.setattr(monitor, "scan_once", lambda cfg, **k: ([arb], {"discovered": 1, "priced_complete": 1}))

    first = run_dutch_arb_monitor(cfg, polls=1, poll_seconds=0, alert_annualised=0.0)
    second = run_dutch_arb_monitor(cfg, polls=1, poll_seconds=0, alert_annualised=0.0)
    third = run_dutch_arb_monitor(cfg, polls=1, poll_seconds=0, alert_annualised=0.0)

    assert first["persistent_alert_count"] == 0
    assert second["persistent_alert_count"] == 0
    assert third["persistent_alert_count"] == 1
    assert third["persistent_alerts"][0]["event_id"] == "PERSIST"
    assert third["persistent_alerts"][0]["consecutive_scans_above_alert"] == 3
    assert third["alert_persistence_counts"] == {"PERSIST": 3}
