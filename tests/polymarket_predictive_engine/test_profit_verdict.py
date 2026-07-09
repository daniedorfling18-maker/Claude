"""Pre-registered $100/month verdict engine: the gates must be evidence-driven,
fail-closed, and impossible to flip without the registered thresholds being met.
Amendments registered 2026-07-09 pre-data: market-clustered Gate A units,
adverse-selection haircut in Gate B, regime-stamped Gate C."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.profit_verdict import _sign_test_p, build_profit_verdict
from polymarket_predictive_engine.utils import read_json, write_csv


def _config(tmp_path: Path):
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def _write_finals(
    cfg,
    unit_clvs: dict[str, list[float]],
    cohort: str = "sharp_anchor_wc",
    entry_price: float = 0.8,
    questions: dict[str, str] | None = None,
    close_times: dict[str, str] | None = None,
) -> None:
    """Write the append-only final-history ledger: market -> final CLVs.

    entry_price drives the taker-fee charge (rate x (1 - p) per dollar);
    0.8 gives 0.006 at the sports rate of 0.03. questions/close_times feed the
    amendment-5 fixture tagging when provided."""
    rows = []
    for market, clvs in unit_clvs.items():
        for i, clv in enumerate(clvs):
            rows.append(
                {
                    "shadow_position_id": f"{market}-pos-{i}",
                    "signal_cohort": cohort,
                    "market_id": market,
                    "question": (questions or {}).get(market, ""),
                    "close_time": (close_times or {}).get(market, ""),
                    "line_kind": "closing",
                    "clv": clv,
                    "entry_price": entry_price,
                    "beat_close": clv > 0,
                }
            )
    write_csv(
        cfg.governance_root / "closing_line_final_history.csv",
        rows,
        fieldnames=[
            "shadow_position_id",
            "signal_cohort",
            "market_id",
            "question",
            "close_time",
            "line_kind",
            "clv",
            "entry_price",
            "beat_close",
        ],
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


def test_correlated_finals_collapse_to_market_units(tmp_path):
    """Registered amendment 1: 30 all-positive finals concentrated in 3 markets
    are 3 independent observations, not 30 - Gate A must stay pending."""
    cfg = _config(tmp_path)
    _write_finals(cfg, {f"market-{m}": [0.05] * 10 for m in range(3)})

    verdict = build_profit_verdict(cfg)

    gate_a = verdict["gates"]["A_edge_exists"]
    assert gate_a["settled_finals_total"] == 30
    assert gate_a["independent_market_units"] == 3
    assert gate_a["state"] == "pending"
    assert verdict["verdict"] == "insufficient_evidence"


def test_same_fixture_side_markets_collapse_to_one_unit(tmp_path):
    """Registered amendment 5: with per-match side markets live on Polymarket
    (daily-win, advance, totals), four all-positive markets settling on the
    SAME football match are ONE observation, not four - Gate A must not reach
    the unit floor through same-fixture correlation."""
    cfg = _config(tmp_path)
    fixture_day = "2026-07-09T20:00:00Z"
    units = {f"independent-{m}": [0.05] for m in range(9)}
    fixture_markets = {
        "mkt-daily-win": "Will France win on 2026-07-09?",
        "mkt-advance": "Will France advance against Morocco?",
        "mkt-draw": "Will France vs. Morocco end in a draw?",
        "mkt-totals": "France vs. Morocco: O/U 2.5",
    }
    units.update({market: [0.05] for market in fixture_markets})
    _write_finals(
        cfg,
        units,
        questions=fixture_markets,
        close_times={market: fixture_day for market in fixture_markets},
    )

    verdict = build_profit_verdict(cfg)

    gate_a = verdict["gates"]["A_edge_exists"]
    assert gate_a["settled_finals_total"] == 13
    # 9 independent markets + 1 merged France-Morocco fixture unit.
    assert gate_a["independent_market_units"] == 10
    assert gate_a["state"] == "pending"
    assert verdict["verdict"] == "insufficient_evidence"


def test_frozen_cohort_finals_are_excluded_from_units(tmp_path):
    cfg = _config(tmp_path)
    _write_finals(cfg, {f"m{m}": [0.05] for m in range(20)}, cohort="crypto_updown_fast")

    verdict = build_profit_verdict(cfg)

    assert verdict["gates"]["A_edge_exists"]["independent_market_units"] == 0
    assert verdict["verdict"] == "insufficient_evidence"


def test_verdict_is_no_when_unit_floor_met_and_clv_nonpositive(tmp_path):
    cfg = _config(tmp_path)
    _write_finals(cfg, {f"m{m}": [-0.004] for m in range(14)})

    verdict = build_profit_verdict(cfg)

    assert verdict["gates"]["A_edge_exists"]["state"] == "fail"
    assert verdict["verdict"] == "no_for_tested_edge_classes"


def test_verdict_yes_requires_all_three_gates_and_stays_paper_gated(tmp_path):
    cfg = _config(tmp_path)
    # 13/14 units beat the close (sign p ~= 0.00092); equal-weight unit mean
    # (13 x 0.036 - 0.05) / 14 ~= 0.0299; net of taker fee 0.006 (entry 0.8)
    # plus 0.01 haircuts ~= 0.0139 -> required ~= $7,194/month.
    units = {f"m{m}": [0.036] for m in range(13)}
    units["m-loser"] = [-0.05]
    _write_finals(cfg, units, entry_price=0.8)
    # 60 entries over 10 days = 6/day -> $1,800/month achievable: Gate C FAILS.
    _write_positions(cfg, count=60, span_days=10)

    verdict = build_profit_verdict(cfg)
    assert verdict["gates"]["A_edge_exists"]["state"] == "pass"
    assert verdict["gates"]["B_edge_survives_costs"]["state"] == "pass"
    assert verdict["gates"]["C_scale_feasible"]["state"] == "fail"
    assert verdict["verdict"] == "no_for_tested_edge_classes"

    # Denser flow: 250 entries over 10 days = 25/day -> $7,500/month >= required.
    _write_positions(cfg, count=250, span_days=10)
    verdict = build_profit_verdict(cfg)
    assert verdict["gates"]["C_scale_feasible"]["state"] == "pass"
    assert verdict["gates"]["C_scale_feasible"]["regime"] == "world_cup_2026_window"
    assert verdict["verdict"] == "yes_edge_evidenced_pending_paper_confirmation"
    # Even the YES never authorises trading by itself.
    assert verdict["paper_trading_invoked"] is False
    assert verdict["live_trading_invoked"] is False

    persisted = read_json(cfg.governance_root / "profit_verdict.json")
    assert persisted["verdict"] == verdict["verdict"]


def test_adverse_selection_haircut_is_charged_in_gate_b(tmp_path):
    """Registered amendment 2: an edge that clears fees + exit haircut but not
    the adverse-selection charge must FAIL Gate B."""
    cfg = _config(tmp_path)
    # Unit mean 0.0125 at entry 0.8: fee 0.006 + exit 0.005 = 0.011 leaves
    # +0.0015 without the adverse charge; with it (0.005) the net is negative.
    _write_finals(cfg, {f"m{m}": [0.0125] for m in range(14)}, entry_price=0.8)

    verdict = build_profit_verdict(cfg)

    gate_b = verdict["gates"]["B_edge_survives_costs"]
    assert verdict["gates"]["A_edge_exists"]["state"] == "pass"
    assert gate_b["adverse_selection_haircut_per_dollar"] == 0.005
    assert gate_b["state"] == "fail"
    assert verdict["verdict"] == "no_for_tested_edge_classes"


def test_taker_fees_are_charged_from_entry_prices(tmp_path):
    """Registered amendment 4 (live fee schedule, verified 2026-07-09): sports
    takers pay rate x (1-p) per dollar. An edge that clears both haircuts but
    not the fee must FAIL, and cheaper entries must charge larger fees."""
    cfg = _config(tmp_path)
    # Unit mean 0.014 at entry 0.8: haircuts 0.010 leave +0.004, but the fee
    # 0.03 x 0.2 = 0.006 makes the net negative.
    _write_finals(cfg, {f"m{m}": [0.014] for m in range(14)}, entry_price=0.8)

    verdict = build_profit_verdict(cfg)
    gate_b = verdict["gates"]["B_edge_survives_costs"]
    assert gate_b["mean_taker_fee_per_dollar"] == 0.006
    assert gate_b["state"] == "fail"

    # Longshot entries (p=0.3) pay 0.03 x 0.7 = 0.021 per dollar.
    _write_finals(cfg, {f"m{m}": [0.014] for m in range(14)}, entry_price=0.3)
    verdict = build_profit_verdict(cfg)
    assert verdict["gates"]["B_edge_survives_costs"]["mean_taker_fee_per_dollar"] == 0.021


def test_frozen_cohort_entries_do_not_inflate_achievable_turnover(tmp_path):
    cfg = _config(tmp_path)
    _write_finals(cfg, {f"m{m}": [0.06] for m in range(14)})
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
