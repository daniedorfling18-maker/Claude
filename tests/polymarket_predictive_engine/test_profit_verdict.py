"""Pre-registered $100/month verdict engine: the gates must be evidence-driven,
fail-closed, and impossible to flip without the registered thresholds being met.
Amendments registered 2026-07-09 pre-data: market-clustered Gate A units,
adverse-selection haircut in Gate B, regime-stamped Gate C."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.profit_verdict import (
    DEFAULT_SETTINGS,
    GATE_A_SEMANTICS_CAVEAT,
    REGISTERED_AMENDMENTS,
    REGISTERED_EXTENSION_PROTOCOL,
    VERDICT_GATE_DEFINITIONS,
    _sign_test_p,
    build_profit_verdict,
)
from polymarket_predictive_engine.utils import now_utc, parse_timestamp, read_json, write_csv


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
    token_ids: dict[str, str] | None = None,
) -> None:
    """Write the append-only final-history ledger: market -> settlement returns.

    entry_price drives the taker-fee charge (rate x (1 - p) per dollar);
    0.8 gives 0.01 at the amendment-6 sports rate of 0.05. questions/close_times
    feed the amendment-5 fixture tagging when provided."""
    rows = []
    for market, clvs in unit_clvs.items():
        for i, clv in enumerate(clvs):
            rows.append(
                {
                    "shadow_position_id": f"{market}-pos-{i}",
                    "signal_cohort": cohort,
                    "market_id": market,
                    "token_id": (token_ids or {}).get(market, f"token-{market}"),
                    "question": (questions or {}).get(market, ""),
                    "close_time": (close_times or {}).get(market, ""),
                    "line_kind": "closing",
                    "clv": clv,
                    "entry_price": entry_price,
                    "line_price": entry_price + clv,
                    "settled_profitable": clv > 0,
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
            "token_id",
            "question",
            "close_time",
            "line_kind",
            "clv",
            "entry_price",
            "line_price",
            "settled_profitable",
            "beat_close",
        ],
    )


def _write_price_history(cfg, rows: list[dict[str, object]]) -> None:
    write_csv(
        cfg.output_root / "polymarket_training" / "historical_price_snapshots.csv",
        rows,
        fieldnames=["token_id", "timestamp", "price", "source"],
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
    # 10 of 12 settled-profitable units under a fair coin.
    assert abs(_sign_test_p(10, 12) - (66 + 12 + 1) / 4096) < 1e-12
    assert _sign_test_p(0, 0) is None


def test_wo87_relabels_gate_without_changing_registered_threshold_bytes(tmp_path):
    expected = (
        b'{"adverse_selection_haircut_per_dollar":0.005,"days_per_month":30.0,'
        b'"exit_cost_haircut_per_dollar":0.005,"max_stake_per_trade_usdc":10.0,'
        b'"minimum_final_samples":12,"sign_test_alpha":0.1,"taker_fee_rate":0.05}'
    )
    assert json.dumps(DEFAULT_SETTINGS, sort_keys=True, separators=(",", ":")).encode() == expected

    cfg = _config(tmp_path)
    _write_finals(cfg, {f"near-settled-{index}": [0.499] for index in range(12)}, entry_price=0.5)
    verdict = build_profit_verdict(cfg)
    gate_a = verdict["gates"]["A_edge_exists"]

    assert gate_a["state"] == "pass"
    assert gate_a["unit_mean_net_settlement_return_per_dollar"] == 0.499
    assert gate_a["units_settled_profitable"] == 12
    # One-release aliases prove the same near-settled observations grade
    # identically; only their labels changed.
    assert gate_a["unit_mean_final_clv"] == gate_a["unit_mean_net_settlement_return_per_dollar"]
    assert gate_a["units_beating_close"] == gate_a["units_settled_profitable"]
    assert gate_a["sign_test_p"] == round(_sign_test_p(12, 12), 6)
    assert verdict["semantics_caveat"] == GATE_A_SEMANTICS_CAVEAT
    assert "settlement return" in gate_a["registered_rule"]
    assert "settled-profitable" in gate_a["registered_rule"]
    assert "final CLV" not in gate_a["registered_rule"]
    assert "beat-close" not in gate_a["registered_rule"]
    assert all("final CLV" not in row["rule"] for row in VERDICT_GATE_DEFINITIONS[:2])


def test_true_pre_event_clv_uses_last_official_in_band_point_before_cutoff(tmp_path):
    cfg = _config(tmp_path)
    market = "world-cup-final"
    close_time = "2026-07-10T18:00:00Z"
    token_id = "token-world-cup-final"
    _write_finals(
        cfg,
        {market: [0.599]},
        entry_price=0.4,
        close_times={market: close_time},
        token_ids={market: token_id},
    )
    _write_price_history(
        cfg,
        [
            {"token_id": token_id, "timestamp": "2026-07-10T10:00:00Z", "price": 0.52, "source": "polymarket_clob_prices_history:interval=1h"},
            {"token_id": token_id, "timestamp": "2026-07-10T11:59:00Z", "price": 0.56, "source": "polymarket_clob_prices_history:interval=1h"},
            {"token_id": token_id, "timestamp": "2026-07-10T11:59:30Z", "price": 0.60, "source": "unofficial_fixture"},
            {"token_id": token_id, "timestamp": "2026-07-10T12:01:00Z", "price": 0.70, "source": "polymarket_clob_prices_history:interval=1h"},
        ],
    )

    verdict = build_profit_verdict(cfg)
    diagnostic = verdict["pre_event_clv_diagnostic"]
    unit = diagnostic["units"][0]

    assert diagnostic["gate_binding"] is False
    assert diagnostic["same_clustering_as_gate_a"] is True
    assert diagnostic["units_gradeable"] == 1
    assert diagnostic["unit_mean_pre_event_clv"] == 0.16
    assert unit["unit_mean_net_settlement_return_per_dollar"] == 0.599
    assert unit["unit_mean_pre_event_clv"] == 0.16
    assert unit["finals"][0]["reference_line_timestamp_utc"] == "2026-07-10T11:59:00Z"
    assert unit["finals"][0]["reference_line_price"] == 0.56


def test_true_pre_event_clv_never_guesses_when_no_point_qualifies(tmp_path):
    cfg = _config(tmp_path)
    market = "ungradeable-final"
    token_id = "token-ungradeable-final"
    _write_finals(
        cfg,
        {market: [0.499]},
        entry_price=0.5,
        close_times={market: "2026-07-10T18:00:00Z"},
        token_ids={market: token_id},
    )
    _write_price_history(
        cfg,
        [
            {"token_id": token_id, "timestamp": "2026-07-10T11:59:00Z", "price": 0.99, "source": "polymarket_clob_prices_history:interval=1h"},
            {"token_id": token_id, "timestamp": "2026-07-10T12:01:00Z", "price": 0.60, "source": "polymarket_clob_prices_history:interval=1h"},
        ],
    )

    diagnostic = build_profit_verdict(cfg)["pre_event_clv_diagnostic"]
    unit = diagnostic["units"][0]

    assert diagnostic["status"] == "pre_event_clv_ungradeable"
    assert diagnostic["unit_mean_pre_event_clv"] is None
    assert diagnostic["units_gradeable"] == 0
    assert unit["status"] == "pre_event_clv_ungradeable"
    assert unit["unit_mean_pre_event_clv"] is None
    assert unit["finals"][0]["status"] == "pre_event_clv_ungradeable"
    assert unit["finals"][0]["reason"] == "no_official_in_band_observation_at_or_before_cutoff"


def test_correlated_finals_collapse_to_market_units(tmp_path, monkeypatch):
    """Registered amendment 1: 30 all-positive finals concentrated in 3 markets
    are 3 independent observations, not 30 - Gate A must stay pending."""
    cfg = _config(tmp_path)
    _write_finals(cfg, {f"market-{m}": [0.05] * 10 for m in range(3)})

    # Clock pinned to the pre-final-read regime. The verdict string is
    # REGIME-dependent (amendment 7/8): after the registered terminal instant
    # 2026-08-19T23:59Z every non-all-pass read resolves to
    # no_for_tested_edge_classes, so an unpinned assertion of
    # "insufficient_evidence" is a time bomb that passes until that date and
    # fails forever after. This test is about the UNIT-COLLAPSE rule, not the
    # calendar, so it states the regime it means to test.
    monkeypatch.setattr(
        "polymarket_predictive_engine.profit_verdict.now_utc", lambda: "2026-07-01T00:00:00Z"
    )

    verdict = build_profit_verdict(cfg)

    gate_a = verdict["gates"]["A_edge_exists"]
    assert gate_a["settled_finals_total"] == 30
    assert gate_a["independent_market_units"] == 3
    assert gate_a["state"] == "pending"
    assert verdict["verdict"] == "insufficient_evidence"


def test_same_fixture_side_markets_collapse_to_one_unit(tmp_path, monkeypatch):
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

    # Clock pinned to the pre-final-read regime. The verdict string is
    # REGIME-dependent (amendment 7/8): after the registered terminal instant
    # 2026-08-19T23:59Z every non-all-pass read resolves to
    # no_for_tested_edge_classes, so an unpinned assertion of
    # "insufficient_evidence" is a time bomb that passes until that date and
    # fails forever after. This test is about the UNIT-COLLAPSE rule, not the
    # calendar, so it states the regime it means to test.
    monkeypatch.setattr(
        "polymarket_predictive_engine.profit_verdict.now_utc", lambda: "2026-07-01T00:00:00Z"
    )

    verdict = build_profit_verdict(cfg)

    gate_a = verdict["gates"]["A_edge_exists"]
    assert gate_a["settled_finals_total"] == 13
    # 9 independent markets + 1 merged France-Morocco fixture unit.
    assert gate_a["independent_market_units"] == 10
    assert gate_a["state"] == "pending"
    assert verdict["verdict"] == "insufficient_evidence"


def test_frozen_cohort_finals_are_excluded_from_units(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    _write_finals(cfg, {f"m{m}": [0.05] for m in range(20)}, cohort="crypto_updown_fast")

    # Clock pinned to the pre-final-read regime. The verdict string is
    # REGIME-dependent (amendment 7/8): after the registered terminal instant
    # 2026-08-19T23:59Z every non-all-pass read resolves to
    # no_for_tested_edge_classes, so an unpinned assertion of
    # "insufficient_evidence" is a time bomb that passes until that date and
    # fails forever after. This test is about the UNIT-COLLAPSE rule, not the
    # calendar, so it states the regime it means to test.
    monkeypatch.setattr(
        "polymarket_predictive_engine.profit_verdict.now_utc", lambda: "2026-07-01T00:00:00Z"
    )

    verdict = build_profit_verdict(cfg)

    assert verdict["gates"]["A_edge_exists"]["independent_market_units"] == 0
    assert verdict["verdict"] == "insufficient_evidence"


def test_position_id_fallback_cannot_inflate_gate_a_units(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    rows = [
        {
            "shadow_position_id": f"position-{index}",
            "signal_cohort": "sharp_anchor_wc",
            "market_id": "",
            "market_slug": "",
            "question": "",
            "close_time": "",
            "line_kind": "closing",
            "clv": 0.05,
            "entry_price": 0.8,
            "beat_close": True,
        }
        for index in range(12)
    ]
    write_csv(
        cfg.governance_root / "closing_line_final_history.csv",
        rows,
        fieldnames=list(rows[0]),
    )

    # Clock pinned to the pre-final-read regime. The verdict string is
    # REGIME-dependent (amendment 7/8): after the registered terminal instant
    # 2026-08-19T23:59Z every non-all-pass read resolves to
    # no_for_tested_edge_classes, so an unpinned assertion of
    # "insufficient_evidence" is a time bomb that passes until that date and
    # fails forever after. This test is about the UNIT-COLLAPSE rule, not the
    # calendar, so it states the regime it means to test.
    monkeypatch.setattr(
        "polymarket_predictive_engine.profit_verdict.now_utc", lambda: "2026-07-01T00:00:00Z"
    )

    verdict = build_profit_verdict(cfg)
    gate_a = verdict["gates"]["A_edge_exists"]

    # The legacy fallback would count 12 apparent units and pass the sample
    # floor. WO-85 preserves the count for audit but holds the gate pending.
    assert gate_a["independent_market_units"] == 12
    assert gate_a["clustering_coverage"] == {
        "state": "insufficient_clustering_coverage",
        "eligible_finals": 12,
        "position_id_fallback_finals": 12,
        "position_id_fallback_fraction": 1.0,
        "maximum_position_id_fallback_fraction": 0.1,
    }
    assert gate_a["state"] == "pending"
    assert "insufficient_clustering_coverage" in gate_a["reason"]
    assert verdict["verdict"] == "insufficient_evidence"


def test_verdict_is_no_when_unit_floor_met_and_clv_nonpositive(tmp_path, monkeypatch):
    # Amendment 8 defers ANY non-all-pass read to insufficient_evidence inside the
    # 2026-07-20..2026-08-19 extension window, so a failing gate resolves to
    # no_for_tested_edge_classes only at an ordinary (pre-final-read) or the terminal
    # read. This test asserts the ordinary gate->verdict mapping, so it pins the read to
    # the pre-final-read regime where a fail is distinct from a pending (insufficient).
    cfg = _config(tmp_path)
    _write_finals(cfg, {f"m{m}": [-0.004] for m in range(14)})
    monkeypatch.setattr(
        "polymarket_predictive_engine.profit_verdict.now_utc", lambda: "2026-07-01T00:00:00Z"
    )

    verdict = build_profit_verdict(cfg)

    assert verdict["gates"]["A_edge_exists"]["state"] == "fail"
    assert verdict["verdict"] == "no_for_tested_edge_classes"


def test_verdict_yes_requires_all_three_gates_and_stays_paper_gated(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    # Pre-final-read regime: a failing Gate C resolves NO here; inside the extension
    # window amendment 8 would defer it to insufficient_evidence (see the pre-final-read
    # note on test_verdict_is_no_when_unit_floor_met_and_clv_nonpositive). The all-pass
    # branch below resolves the YES path in every regime.
    monkeypatch.setattr(
        "polymarket_predictive_engine.profit_verdict.now_utc", lambda: "2026-07-01T00:00:00Z"
    )
    # 13/14 units beat the close (sign p ~= 0.00092); equal-weight unit mean
    # (13 x 0.036 - 0.05) / 14 ~= 0.0299; net of taker fee 0.01 (entry 0.8,
    # amendment-6 rate 0.05) plus 0.01 haircuts ~= 0.0099 -> required
    # ~= $10,101/month.
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

    # Denser flow: 350 entries over 10 days = 35/day -> $10,500/month >= required.
    _write_positions(cfg, count=350, span_days=10)
    verdict = build_profit_verdict(cfg)
    assert verdict["gates"]["C_scale_feasible"]["state"] == "pass"
    assert verdict["gates"]["C_scale_feasible"]["regime"] == "world_cup_2026_window"
    assert verdict["verdict"] == "yes_edge_evidenced_pending_paper_confirmation"
    # Even the YES never authorises trading by itself.
    assert verdict["paper_trading_invoked"] is False
    assert verdict["live_trading_invoked"] is False

    persisted = read_json(cfg.governance_root / "profit_verdict.json")
    assert persisted["verdict"] == verdict["verdict"]


def test_adverse_selection_haircut_is_charged_in_gate_b(tmp_path, monkeypatch):
    """Registered amendment 2: an edge that clears fees + exit haircut but not
    the adverse-selection charge must FAIL Gate B."""
    cfg = _config(tmp_path)
    # Pre-final-read regime: a failing Gate B resolves NO here; inside the extension
    # window amendment 8 defers it to insufficient_evidence.
    monkeypatch.setattr(
        "polymarket_predictive_engine.profit_verdict.now_utc", lambda: "2026-07-01T00:00:00Z"
    )
    # Unit mean 0.0175 at entry 0.8: fee 0.01 + exit 0.005 = 0.015 leaves
    # +0.0025 without the adverse charge; with it (0.005) the net is negative.
    _write_finals(cfg, {f"m{m}": [0.0175] for m in range(14)}, entry_price=0.8)

    verdict = build_profit_verdict(cfg)

    gate_b = verdict["gates"]["B_edge_survives_costs"]
    assert verdict["gates"]["A_edge_exists"]["state"] == "pass"
    assert gate_b["adverse_selection_haircut_per_dollar"] == 0.005
    assert gate_b["state"] == "fail"
    assert verdict["verdict"] == "no_for_tested_edge_classes"


def test_taker_fees_are_charged_from_entry_prices(tmp_path):
    """Registered amendments 4+6 (canonical fee schedule, 2026-07-10): sports
    takers pay rate x (1-p) per dollar at rate 0.05. An edge that clears both
    haircuts but not the fee must FAIL, and cheaper entries must charge more."""
    cfg = _config(tmp_path)
    # Unit mean 0.014 at entry 0.8: haircuts 0.010 leave +0.004, but the fee
    # 0.05 x 0.2 = 0.01 makes the net negative.
    _write_finals(cfg, {f"m{m}": [0.014] for m in range(14)}, entry_price=0.8)

    verdict = build_profit_verdict(cfg)
    gate_b = verdict["gates"]["B_edge_survives_costs"]
    assert gate_b["mean_taker_fee_per_dollar"] == 0.01
    assert gate_b["state"] == "fail"

    # Longshot entries (p=0.3) pay 0.05 x 0.7 = 0.035 per dollar.
    _write_finals(cfg, {f"m{m}": [0.014] for m in range(14)}, entry_price=0.3)
    verdict = build_profit_verdict(cfg)
    assert verdict["gates"]["B_edge_survives_costs"]["mean_taker_fee_per_dollar"] == 0.035


def test_frozen_cohort_entries_do_not_inflate_achievable_turnover(tmp_path, monkeypatch):
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

    # Clock pinned to the pre-final-read regime. The verdict string is
    # REGIME-dependent (amendment 7/8): after the registered terminal instant
    # 2026-08-19T23:59Z every non-all-pass read resolves to
    # no_for_tested_edge_classes, so an unpinned assertion of
    # "insufficient_evidence" is a time bomb that passes until that date and
    # fails forever after. This test is about the UNIT-COLLAPSE rule, not the
    # calendar, so it states the regime it means to test.
    monkeypatch.setattr(
        "polymarket_predictive_engine.profit_verdict.now_utc", lambda: "2026-07-01T00:00:00Z"
    )

    verdict = build_profit_verdict(cfg)

    gate_c = verdict["gates"]["C_scale_feasible"]
    assert gate_c["observed_focus_entries"] == 0
    assert gate_c["state"] == "pending"
    assert verdict["verdict"] == "insufficient_evidence"


def test_terminal_read_forces_pending_verdict_to_no(tmp_path, monkeypatch):
    # #59 red-team F3: amendment 7/8 is now ENFORCED, not just emitted. At/after the
    # 2026-08-19/20 terminal read, any non-all-pass outcome resolves terminally to
    # no_for_tested_edge_classes -- a pending Gate A no longer lingers as
    # insufficient_evidence.
    cfg = _config(tmp_path)
    _write_finals(cfg, {"m0": [0.01]})
    monkeypatch.setattr(
        "polymarket_predictive_engine.profit_verdict.now_utc", lambda: "2026-08-20T00:00:00Z"
    )

    verdict = build_profit_verdict(cfg)

    assert verdict["verdict"] == "no_for_tested_edge_classes"
    assert verdict["extension_resolution"]["regime"] == "terminal"
    assert verdict["extension_resolution"]["terminal_no_applied"] is True


def test_registered_terminal_instant_has_passed_and_the_live_read_is_terminal(tmp_path):
    """The live regime under the REAL clock, which nothing covered.

    Five unit-collapse tests and one dashboard test asserted
    verdict == "insufficient_evidence" without pinning the clock. That held
    through the pre-final-read regime AND the extension window, so it looked
    stable for months -- then the registered terminal instant
    (extension_window_end_utc, 2026-08-19T23:59Z) passed and all six failed at
    once, on main, blocking every PR. The gap was that no test exercised the
    engine under the ambient clock, so nothing said out loud which regime the
    system is actually in.

    This test does, and it is stable in the only direction time runs: once the
    terminal instant is past it stays past, so the assertion never flips back.
    The bound is read from the registered protocol, never re-typed.
    """
    cfg = _config(tmp_path)
    terminal = parse_timestamp(REGISTERED_EXTENSION_PROTOCOL["extension_window_end_utc"])
    assert parse_timestamp(now_utc()) >= terminal, (
        "this test encodes the post-terminal regime; before the terminal instant "
        "the live read is legitimately insufficient_evidence"
    )
    _write_finals(cfg, {"m0": [0.01]})

    verdict = build_profit_verdict(cfg)

    # A pending Gate A is no longer deferred: the single registered extension is spent.
    assert verdict["gates"]["A_edge_exists"]["state"] == "pending"
    assert verdict["extension_resolution"]["regime"] == "terminal"
    assert verdict["extension_resolution"]["terminal_no_applied"] is True
    assert verdict["verdict"] == "no_for_tested_edge_classes"
    # Terminal is a NO, not a licence: nothing here invokes a trading path.
    assert verdict["paper_trading_invoked"] is False
    assert verdict["live_trading_invoked"] is False


def test_extension_window_defers_a_pending_read(tmp_path, monkeypatch):
    # Within the single extension window (2026-07-20 .. 2026-08-18) a pending read takes
    # the extension and stays insufficient_evidence; it is NOT forced to NO yet.
    cfg = _config(tmp_path)
    _write_finals(cfg, {"m0": [0.01]})
    monkeypatch.setattr(
        "polymarket_predictive_engine.profit_verdict.now_utc", lambda: "2026-07-25T00:00:00Z"
    )

    verdict = build_profit_verdict(cfg)

    assert verdict["verdict"] == "insufficient_evidence"
    assert verdict["extension_resolution"]["regime"] == "extension_window"
    assert verdict["extension_resolution"]["terminal_no_applied"] is False


def test_amendment_7_extension_protocol_is_registered_and_terminal(tmp_path):
    # Amendment 7 pre-commits the window-extension terms BEFORE the first
    # interim read: exact dates, single use, forced terminal resolution.
    # This test pins the registration so the terms cannot silently drift
    # after evidence arrives.
    cfg = _config(tmp_path)
    _write_finals(cfg, {"m0": [0.01]})

    verdict = build_profit_verdict(cfg)

    protocol = verdict["registered_extension_protocol"]
    assert protocol["amendment"] == 7
    assert protocol["single_use"] is True
    assert protocol["extension_window_start_utc"] == "2026-07-20T00:00:00Z"
    assert protocol["extension_window_end_utc"] == "2026-08-19T23:59:00Z"
    assert protocol["extension_regime"] == "post_wc_2026"
    # Registered before the final read it governs.
    assert protocol["registered_at_utc"] < protocol["final_read_due_utc"]
    # Amendment 8 (2026-07-17, owner-approved PR, registered before the
    # 2026-07-19/20 final read) closed the pending-on-significance gap: the
    # terminal rule now resolves EVERY branch, and the final read extends on
    # any verdict other than all-gates-pass.
    for fragment in ("yes path", "ANY other outcome", "pending significance", "amendment 8"):
        assert fragment in protocol["terminal_rule"]
    for fragment in ("other than all-gates-pass", "single extension", "pending on significance"):
        assert fragment in protocol["final_read_rule"]
    amendment_8 = next(row for row in REGISTERED_AMENDMENTS if row["number"] == 8)
    assert amendment_8["direction"] == "tighten_only"
    assert amendment_8["registered_at_utc"] < "2026-07-19T00:00:00Z"
