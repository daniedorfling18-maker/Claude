from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from premium_research import carry

HOUR_MS = 3_600_000
PERIOD_MS = 8 * HOUR_MS
MONDAY = 1704067200000  # 2024-01-01T00:00Z, a Monday
WEDNESDAY = MONDAY + 2 * 24 * HOUR_MS


def _table(n_boundaries: int, *, rate=0.0001, perp=100.0, spot=100.0, high=None, start=MONDAY) -> pd.DataFrame:
    """A boundary table with constant or per-boundary sequences; ``high`` defaults to the perp close."""

    def seq(value, default=None):
        if value is None:
            value = default
        if np.isscalar(value):
            return [float(value)] * n_boundaries
        assert len(value) == n_boundaries
        return [float(v) for v in value]

    perp_seq = seq(perp)
    return pd.DataFrame(
        {
            "boundary_ms": [start + i * PERIOD_MS for i in range(n_boundaries)],
            "rate": seq(rate),
            "perp_close": perp_seq,
            "spot_close": seq(spot),
            "perp_high": seq(high, perp_seq),
            "high_hours": [8] * n_boundaries,
            "close_missing": [False] * n_boundaries,
            "perp_close_missing": [False] * n_boundaries,
            "spot_close_missing": [False] * n_boundaries,
            "high_partial": [False] * n_boundaries,
        }
    )


def _run(table: pd.DataFrame, variant: str = "V0", **kwargs) -> pd.DataFrame:
    ledger = carry.simulate(table, variant=variant, start_ms=int(table["boundary_ms"].iloc[0]), end_ms=int(table["boundary_ms"].iloc[-1]), **kwargs)
    return carry.ledger_frame(ledger)


def test_constant_funding_flat_basis_annualises_exactly() -> None:
    frame = _run(_table(1 + 1095, rate=0.0001))
    assert round(float(frame["funding_received"].sum()), 4) == 0.1095
    fees = float(frame["fees_paid"].sum())
    gross_on_notional = float(frame["wealth"].iloc[-1]) - 1.5 + fees
    assert round(gross_on_notional, 4) == 0.1095
    assert round(gross_on_notional / 1.5, 4) == 0.0730


def test_round_trip_costs_thirty_bps_of_notional() -> None:
    frame = _run(_table(2, rate=0.0))
    net_on_notional = float(frame["wealth"].iloc[-1]) - 1.5
    assert round(net_on_notional, 4) == -0.0030
    assert round(net_on_notional / 1.5, 4) == -0.0020
    assert float(frame["fees_paid"].sum()) == pytest.approx(0.0030)
    # the inception fee is inside the return series, not only in the wealth column
    assert float(frame["wealth"].iloc[0]) == 1.5
    assert round(float(frame["period_return_on_capital"].sum()), 4) == -0.0020
    weekly = carry.weekly_returns(_run(_table(1 + 21, rate=0.0)))
    assert round(float(weekly["return_on_capital"].sum()), 4) == -0.0020
    assert float(weekly["fees_paid"].sum()) == pytest.approx(0.0030)  # entry and exit fees both in the weekly frame


def test_basis_change_appears_only_at_entry_and_exit() -> None:
    round_trip = _run(_table(3, rate=0.0, perp=[100.0, 101.0, 100.0]))
    hedge_pnl = float(round_trip["wealth"].iloc[-1]) - 1.5 + float(round_trip["fees_paid"].sum())
    assert round(hedge_pnl, 4) == 0.0
    ends_wide = _run(_table(3, rate=0.0, perp=[100.0, 101.0, 101.0]))
    hedge_pnl = float(ends_wide["wealth"].iloc[-1]) - 1.5 + float(ends_wide["fees_paid"].sum())
    assert round(hedge_pnl, 4) == -0.0100  # the short lost 1% of N; spot was flat


def test_forced_liquidation_threshold_is_0_4925() -> None:
    assert round(carry.liquidation_move(0.5), 4) == 0.4925
    liquidated = _run(_table(3, rate=0.0, high=[100.0, 150.0, 100.0]))
    assert int(liquidated["forced_liquidations"].sum()) == 1
    # the whole 0.5 N margin is gone; spot survived; the structure is rebuilt from 0.9985 at full cost
    rebuilt_fee = (0.9985 / 1.5) * 0.0015
    assert float(liquidated["wealth"].iloc[1]) == pytest.approx(0.9985 - rebuilt_fee, abs=1e-9)
    safe = _run(_table(3, rate=0.0, high=[100.0, 149.0, 100.0]))
    assert int(safe["forced_liquidations"].sum()) == 0
    assert float(safe["wealth"].iloc[1]) == pytest.approx(1.5 - 0.0015)


def test_rebalance_triggers_above_twenty_percent_and_restores_half() -> None:
    position, _ = carry.open_position(1.5, 100.0, 100.0, notional=1.0)
    position.margin -= position.q * (121.0 - 100.0)  # +21% move marked at the boundary
    assert position.margin_ratio(121.0) < 0.25
    resized, trade = carry.resize_to_half_margin(position, 121.0, 121.0)
    spot_sold = (position.q - resized.q) * 121.0
    assert spot_sold == pytest.approx(0.21, abs=1e-9)
    assert trade.traded_notional == pytest.approx(0.42, abs=1e-9)  # 0.21 spot + 0.21 perpetual
    assert trade.fees * 1e4 == pytest.approx(3.15, abs=1e-6)  # bps of N
    assert round(resized.margin_ratio(121.0), 4) == 0.5000
    assert resized.q * 121.0 == pytest.approx(1.0, abs=1e-9)  # notional back to N
    triggered = _run(_table(3, rate=0.0, perp=[100.0, 121.0, 121.0], spot=[100.0, 121.0, 121.0]))
    assert int(triggered["rebalances"].sum()) == 1
    untriggered = _run(_table(3, rate=0.0, perp=[100.0, 119.0, 119.0], spot=[100.0, 119.0, 119.0]))
    assert int(untriggered["rebalances"].sum()) == 0
    assert float(untriggered["traded_notional"].iloc[1]) == 0.0


def test_no_releveraging_on_price_fall() -> None:
    frame = _run(_table(3, rate=[0.0, 0.0001, 0.0], perp=[100.0, 70.0, 70.0], spot=[100.0, 70.0, 70.0]))
    assert float(frame["traded_notional"].iloc[1]) == 0.0
    assert int(frame["rebalances"].sum()) == 0
    assert float(frame["funding_received"].iloc[1]) == pytest.approx(0.0001 * 0.7)  # notional is now 0.7 N
    assert float(frame["wealth"].iloc[1]) == pytest.approx(1.5 - 0.0015 + 0.00007)  # delta-neutral: no price P&L


def test_v1_cannot_see_the_next_rate() -> None:
    # Flat base: 0.00001 per period is 1.1% annualised, far below the 8% entry level, so the
    # signal never enters. A spike at t+1 that WOULD trigger entry must leave the decision
    # at t unchanged; a signal that reads f_{t+1} enters at t and the test fails.
    flat = np.full(200, 0.00001)
    assert not carry.v1_targets(flat).any()
    for t in (10, 50, 120):
        altered = flat.copy()
        altered[t + 1] = 0.05
        targets = carry.v1_targets(altered)
        assert not targets[: t + 1].any(), f"decision at or before t={t} saw f_{{t+1}}"
        assert targets[t + 1]  # the spike is visible one boundary later, as it should be
    # Exit side: holding on strongly positive rates, a crash at t+1 must not exit at t.
    hot = np.full(200, 0.0003)
    held = carry.v1_targets(hot)
    assert held[5:].all()
    for t in (10, 50, 120):
        altered = hot.copy()
        altered[t + 1] = -0.01
        targets = carry.v1_targets(altered)
        assert targets[t], f"decision at t={t} saw the crash at t+1"
        assert not targets[t + 1]


def test_v1_entry_threshold_is_eight_percent() -> None:
    enters = np.full(6, 0.0801 / 1095)
    targets = carry.v1_targets(enters)
    assert targets.tolist() == [False, False, True, True, True, True]
    stays_flat = carry.v1_targets(np.full(6, 0.0799 / 1095))
    assert not stays_flat.any()
    exits = np.array([0.0801 / 1095] * 3 + [-0.000001] * 3)
    assert carry.v1_targets(exits).tolist() == [False, False, True, True, True, False]
    # entry executes at the decision boundary; the first funding received is at the next one
    frame = _run(_table(6, rate=0.0801 / 1095), variant="V1")
    assert frame["position_open"].tolist() == [False, False, True, True, True, False]
    assert float(frame["funding_received"].iloc[2]) == 0.0
    assert float(frame["funding_received"].iloc[3]) == pytest.approx(0.0801 / 1095)


def test_weeks_with_fewer_than_21_periods_are_dropped_and_counted() -> None:
    frame = _run(_table(1 + 15 + 21 * 2 + 5, rate=0.0001, start=WEDNESDAY))  # Wed 00:00 entry: 15-period stub, 2 full weeks, 5-period stub
    weekly = carry.weekly_returns(frame)
    assert weekly["periods"].tolist() == [15, 21, 21, 5]
    assert weekly["eligible"].tolist() == [False, True, True, False]
    assert int((~weekly["eligible"]).sum()) == 2


def test_missing_close_merges_the_period_and_flags_its_week() -> None:
    table = _table(1 + 21, rate=0.0001)
    table.loc[5, ["perp_close", "spot_close"]] = float("nan")
    table.loc[5, "close_missing"] = True
    frame = _run(table)
    assert bool(frame["flagged"].iloc[5]) and bool(frame["flagged"].iloc[6])
    assert float(frame["funding_received"].iloc[5]) == 0.0
    assert float(frame["funding_received"].iloc[6]) == pytest.approx(2 * 0.0001)  # both boundaries' funding applied here
    weekly = carry.weekly_returns(frame)
    assert weekly["periods"].tolist() == [21]
    assert weekly["eligible"].tolist() == [False]
    with pytest.raises(carry.CarryInputError):
        bad = _table(3)
        bad.loc[2, "close_missing"] = True
        carry.simulate(bad, variant="V0", start_ms=int(bad["boundary_ms"].iloc[0]), end_ms=int(bad["boundary_ms"].iloc[-1]))


def test_boundary_table_reads_the_hour_ending_at_the_boundary_and_the_eight_highs() -> None:
    funding = pd.DataFrame({"calc_time": [MONDAY, MONDAY + PERIOD_MS], "last_funding_rate": [0.0001, 0.0002]})
    hours = [MONDAY - HOUR_MS + i * HOUR_MS for i in range(9)]  # hour ending at MONDAY, then 8 more
    perp = pd.DataFrame({"open_time": hours, "open": 1.0, "high": [10.0] + [float(i) for i in range(1, 9)], "low": 1.0, "close": [100.0] + [float(100 + i) for i in range(1, 9)]})
    spot = pd.DataFrame({"open_time": hours, "open": 1.0, "high": 1.0, "low": 1.0, "close": [99.0] + [float(99 + i) for i in range(1, 9)]})
    table = carry.boundary_table(funding, perp, spot)
    assert table["perp_close"].tolist() == [100.0, 108.0]
    assert table["spot_close"].tolist() == [99.0, 107.0]
    assert table["perp_high"].tolist()[1] == 8.0  # max over the 8 hours inside (MONDAY, MONDAY + 8h]
    assert table["high_hours"].tolist() == [1, 8]
    assert table["high_partial"].tolist() == [True, False]
    assert not table["close_missing"].any()


def test_two_x_fee_sensitivity_doubles_the_round_trip() -> None:
    frame = _run(_table(2, rate=0.0), fee_mult=2.0)
    assert round(float(frame["wealth"].iloc[-1]) - 1.5, 4) == -0.0060


def test_open_position_inside_a_flagged_period_is_unverifiable_and_a_flat_one_is_not() -> None:
    table = _table(1 + 21, rate=0.0001)
    table.loc[5, ["perp_close", "spot_close"]] = float("nan")
    table.loc[5, "close_missing"] = True
    table.loc[9, "high_hours"] = 6
    table.loc[9, "high_partial"] = True
    frame = _run(table)
    assert frame["unverifiable_open"].tolist()[5:7] == [1, 1]  # the merged period and the one it merges into
    assert int(frame["unverifiable_open"].iloc[9]) == 1
    assert int(frame["unverifiable_open"].sum()) == 3
    weekly = carry.weekly_returns(frame)
    assert int(weekly["unverifiable_open"].sum()) == 3
    # flat throughout (V1 never enters at 1.1% annualised): flagged periods are not unverifiable
    flat = _table(1 + 21, rate=0.00001)
    flat.loc[9, "high_hours"] = 6
    flat.loc[9, "high_partial"] = True
    frame_flat = _run(flat, variant="V1")
    assert not frame_flat["position_open"].any()
    assert int(frame_flat["unverifiable_open"].sum()) == 0
    assert bool(frame_flat["flagged"].iloc[9])


def test_v1_non_finite_signal_neither_enters_nor_stays_in() -> None:
    hot = np.full(10, 0.0003)
    hot[6] = float("nan")
    targets = carry.v1_targets(hot)
    assert targets[5] and not targets[6] and not targets[7] and not targets[8]  # exits on NaN, re-enters once the window is finite
    assert targets[9]
    flat = np.full(6, 0.00001)
    flat[3] = float("nan")
    assert not carry.v1_targets(flat).any()


def test_entry_helper_never_returns_a_boundary_before_its_argument_and_simulate_refuses_to_shift() -> None:
    assert carry.first_monday_boundary_on_or_after(MONDAY) == MONDAY
    assert carry.first_monday_boundary_on_or_after(MONDAY + PERIOD_MS) == MONDAY + 7 * 24 * HOUR_MS  # Monday 08:00 -> next Monday
    assert carry.first_monday_boundary_on_or_after(WEDNESDAY) == MONDAY + 7 * 24 * HOUR_MS
    assert carry.last_monday_boundary_on_or_before(MONDAY + PERIOD_MS) == MONDAY
    table = _table(4)
    with pytest.raises(carry.CarryInputError, match="refuse to shift"):
        carry.simulate(table, variant="V0", start_ms=MONDAY - HOUR_MS, end_ms=int(table["boundary_ms"].iloc[-1]))
    with pytest.raises(carry.CarryInputError, match="refuse to shift"):
        carry.simulate(table, variant="V0", start_ms=MONDAY, end_ms=int(table["boundary_ms"].iloc[-1]) + HOUR_MS)


def test_boundary_table_flags_each_leg_separately() -> None:
    funding = pd.DataFrame({"calc_time": [MONDAY, MONDAY + PERIOD_MS, MONDAY + 2 * PERIOD_MS], "last_funding_rate": [0.0001] * 3})
    hours = [MONDAY - HOUR_MS + i * HOUR_MS for i in range(17)]
    perp = pd.DataFrame({"open_time": hours, "open": 1.0, "high": 1.0, "low": 1.0, "close": 100.0})
    spot = pd.DataFrame({"open_time": hours, "open": 1.0, "high": 1.0, "low": 1.0, "close": 99.0})
    spot_gap = spot[spot["open_time"] != MONDAY + PERIOD_MS - HOUR_MS]  # the hour ending at boundary 1, spot only
    table = carry.boundary_table(funding, perp, spot_gap)
    assert table["spot_close_missing"].tolist() == [False, True, False]
    assert table["perp_close_missing"].tolist() == [False, False, False]
    assert table["close_missing"].tolist() == [False, True, False]
    perp_gap = perp[perp["open_time"] != MONDAY + PERIOD_MS - HOUR_MS]
    table = carry.boundary_table(funding, perp_gap, spot)
    assert table["perp_close_missing"].tolist() == [False, True, False]
    assert table["spot_close_missing"].tolist() == [False, False, False]
    assert table["close_missing"].tolist() == [False, True, False]
    assert table["high_partial"].tolist() == [True, True, False]  # row 0 has one hour; row 1 lost one of its eight


def _with_gap(table: pd.DataFrame, row: int, *, spot: bool = False, perp: bool = False) -> pd.DataFrame:
    table = table.copy()
    if spot:
        table.loc[row, "spot_close"] = float("nan")
    if perp:
        table.loc[row, "perp_close"] = float("nan")
    table.loc[row, "close_missing"] = True
    table["perp_close_missing"] = table["perp_close"].isna()
    table["spot_close_missing"] = table["spot_close"].isna()
    return table


def test_spot_only_gap_is_rejected_but_verifiable_under_perp_scope() -> None:
    table = _with_gap(_table(1 + 21, rate=0.0001), 5, spot=True)
    either = _run(table)
    perp = _run(table, unverifiable_scope="perp")
    assert either["unverifiable_open"].tolist()[5:7] == [1, 1] and int(either["unverifiable_open"].sum()) == 2
    assert int(perp["unverifiable_open"].sum()) == 0
    assert not carry.weekly_returns(either)["eligible"].any() and not carry.weekly_returns(perp)["eligible"].any()
    assert either["wealth"].tolist() == perp["wealth"].tolist() and either["funding_received"].tolist() == perp["funding_received"].tolist()


def test_perp_gap_is_unverifiable_under_both_scopes() -> None:
    partial = _table(1 + 21, rate=0.0001)
    partial.loc[9, "high_hours"] = 6
    partial.loc[9, "high_partial"] = True
    partial["perp_close_missing"] = False
    partial["spot_close_missing"] = False
    assert int(_run(partial)["unverifiable_open"].sum()) == 1
    assert int(_run(partial, unverifiable_scope="perp")["unverifiable_open"].sum()) == 1
    # a whole absent perpetual bar at boundary 5: it is row 5's 8th high and row 6's start close
    whole = _with_gap(_table(1 + 21, rate=0.0001), 5, perp=True)
    whole.loc[5, "high_hours"] = 7
    whole.loc[5, "high_partial"] = True
    assert int(_run(whole)["unverifiable_open"].sum()) == 2
    assert _run(whole, unverifiable_scope="perp")["unverifiable_open"].tolist()[5:7] == [1, 1]
    # a NaN perpetual close with the high left present (unreachable from fetched data)
    closed = _with_gap(_table(1 + 21, rate=0.0001), 5, perp=True)
    assert int(_run(closed)["unverifiable_open"].sum()) == 2
    assert _run(closed, unverifiable_scope="perp")["unverifiable_open"].tolist()[5:7] == [0, 1]


def test_unknown_scope_aborts() -> None:
    table = _table(3)
    with pytest.raises(carry.CarryInputError, match="unknown unverifiable_scope"):
        carry.simulate(table, variant="V0", start_ms=int(table["boundary_ms"].iloc[0]), end_ms=int(table["boundary_ms"].iloc[-1]), unverifiable_scope="spot")


def test_non_finite_high_with_open_position_is_unverifiable_under_both_scopes() -> None:
    # Unreachable through the runner (load_inputs rejects a non-finite high before boundary_table runs), but simulate is public:
    # an open position with no intra-period high to check against must not read as verified under any scope.
    for scope in ("either", "perp"):
        table = _table(6)
        table.loc[3, "perp_high"] = float("nan")
        frame = _run(table, unverifiable_scope=scope)
        assert frame["unverifiable_open"].tolist() == [0, 0, 0, 1, 0, 0], scope
        assert frame["rejected_open"].sum() == 0, scope  # not a rejected period: the close is present and high_partial is False
        assert frame["forced_liquidations"].sum() == 0


def test_rejected_open_is_the_either_scope_count_under_every_scope() -> None:
    table = _table(10)
    table.loc[5, "spot_close"] = float("nan")
    table.loc[5, "close_missing"] = True
    table.loc[5, "spot_close_missing"] = True
    either = _run(table, unverifiable_scope="either")
    perp = _run(table, unverifiable_scope="perp")
    assert either["unverifiable_open"].tolist() == perp["rejected_open"].tolist() == either["rejected_open"].tolist()
    assert int(either["unverifiable_open"].sum()) == 2 and int(perp["unverifiable_open"].sum()) == 0


def test_table_without_per_leg_flags_is_refused() -> None:
    table = _table(4).drop(columns=["perp_close_missing"])
    with pytest.raises(KeyError):
        _run(table, unverifiable_scope="perp")


# ---------------------------------------------------------------------------- WO-170


def test_nav_identity_holds_at_every_boundary() -> None:
    table = _table(1096, rate=0.0001, perp=100.0, spot=100.0)
    frame = _run(table)
    assert (np.abs(frame["nav"] - (frame["cash"] + frame["margin"] + frame["spot_value"])) <= 1e-12).all()
    assert (np.abs(frame["nav"] - frame["wealth"]) <= 1e-12).all()
    entry = frame.iloc[0]
    assert entry["cash"] == 1.5 and entry["margin"] == 0.0 and entry["spot_qty"] == 0.0 and entry["nav"] == 1.5 and not entry["marks_carried_forward"]
    assert round(float(frame["nav"].iloc[-1]), 4) == round(1.5 + 0.1095 - 0.0015 - 0.0015, 4) == 1.6065


def test_hand_calculated_cash_flows_three_periods() -> None:
    table = _table(3, rate=0.001, perp=[101.0, 111.0, 100.0], spot=[100.0, 110.0, 99.0], high=[101.0, 111.0, 100.0])
    frame = _run(table)
    r0, r1, r2 = (frame.iloc[i] for i in range(3))
    assert r0["nav"] == 1.5
    assert abs(r1["spot_qty"] - 0.01) < 1e-12
    assert abs(r1["margin"] - 0.40611) < 1e-9 and abs(r1["spot_value"] - 1.1) < 1e-9 and abs(r1["cash"] - (-0.006505)) < 1e-9
    assert abs(r1["nav"] - 1.499605) < 1e-9
    assert abs(r1["margin"] / (r1["spot_qty"] * r1["perp_mark"]) - 0.36586) < 1e-5 and r1["rebalances"] == 0
    assert r2["spot_qty"] == 0.0 and r2["margin"] == 0.0 and abs(r2["cash"] - 1.499115) < 1e-9 and abs(r2["nav"] - 1.499115) < 1e-9
    assert abs(frame["funding_received"].sum() - 0.00211) < 1e-9 and abs(frame["fees_paid"].sum() - 0.002995) < 1e-9
    assert abs((r2["nav"] - r0["nav"]) - (-0.000885)) < 1e-9


def test_period_return_on_nav_compounds_to_the_nav_path() -> None:
    frame = _run(_table(3, rate=0.001, perp=[101.0, 111.0, 100.0], spot=[100.0, 110.0, 99.0], high=[101.0, 111.0, 100.0]))
    assert frame["period_return_on_nav"].iloc[0] == 0.0
    assert abs(frame["nav"].iloc[0] * np.prod(1.0 + frame["period_return_on_nav"].to_numpy()) - frame["nav"].iloc[-1]) < 1e-12
    rng = np.random.default_rng(7)
    prices = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, size=31)))
    walk = _table(31, rate=0.0002, perp=list(prices * 1.001), spot=list(prices), high=list(prices * 1.002))
    frame = _run(walk)
    assert abs(frame["nav"].iloc[0] * np.prod(1.0 + frame["period_return_on_nav"].to_numpy()) - frame["nav"].iloc[-1]) < 1e-12


def test_merged_boundary_carries_marks_forward_and_keeps_the_identity() -> None:
    table = _table(10)
    table.loc[5, "spot_close"] = float("nan")
    table.loc[5, "close_missing"] = True
    table.loc[5, "spot_close_missing"] = True
    frame = _run(table, unverifiable_scope="perp")
    assert bool(frame["marks_carried_forward"].iloc[5]) and not bool(frame["marks_carried_forward"].iloc[0])
    assert frame["spot_mark"].iloc[5] == frame["spot_mark"].iloc[4]
    assert (np.abs(frame["nav"] - frame["wealth"]) <= 1e-12).all()
    assert np.isfinite(frame["period_return_on_nav"].iloc[5])
    # flat across a merged boundary (V1 never enters at 0.00001 per period): nothing held, cash is the wealth, marks carried
    low = _table(10, rate=0.00001)
    low.loc[5, "spot_close"] = float("nan")
    low.loc[5, "close_missing"] = True
    low.loc[5, "spot_close_missing"] = True
    flat = _run(low, variant="V1", unverifiable_scope="perp")
    assert flat["spot_qty"].iloc[5] == 0.0 and flat["margin"].iloc[5] == 0.0 and flat["nav"].iloc[5] == flat["cash"].iloc[5]


def test_pooled_nav_drawdown_uses_the_summed_nav() -> None:
    from premium_research.runner import _max_drawdown_nav

    a = np.array([1.5, 1.6, 1.5]); b = np.array([1.5, 1.4, 1.5])
    assert abs(_max_drawdown_nav(a) - (1.5 / 1.6 - 1.0)) < 1e-12 and abs(_max_drawdown_nav(a) + 0.0625) < 1e-12
    assert abs(_max_drawdown_nav(b) - (1.4 / 1.5 - 1.0)) < 1e-12 and abs(_max_drawdown_nav(b) + 0.0667) < 1e-4
    assert _max_drawdown_nav(a + b) == 0.0
    assert math.isnan(_max_drawdown_nav(np.array([1.5, float("nan"), 1.5])))


def test_nav_identity_violation_aborts() -> None:
    # The identity holds by construction; the guard must still fire if a ledger is ever inconsistent.
    table = _table(3, rate=0.001, perp=[101.0, 111.0, 100.0], spot=[100.0, 110.0, 99.0], high=[101.0, 111.0, 100.0])
    ledger = carry.simulate(table, variant="V0", start_ms=int(table["boundary_ms"].iloc[0]), end_ms=int(table["boundary_ms"].iloc[-1]))
    ledger.cash[1] += 1e-6
    with pytest.raises(carry.CarryInputError, match="NAV identity violated"):
        carry.ledger_frame(ledger)


def test_nav_drawdown_differs_from_compounded_weekly_on_a_grown_ledger() -> None:
    """WO-170 test 4: the two bases disagree once the ledger has grown, and they
    disagree in both directions.

    The legacy basis compounds weekly P&L increments normalised to INCEPTION
    capital, so it cannot see a trough inside a week and it divides a late loss by
    a stale denominator. The NAV basis reads the ledger's own path at every
    boundary. Neither is a relabelling of the other."""
    from quant_lab.risk import max_drawdown_from_returns

    from premium_research.runner import _max_drawdown_nav

    # NAV 1.5 -> 3.0, then -0.15 inside the week with +0.10 recovered by its end.
    intra_week = np.array([1.5, 3.0, 2.85, 2.95])
    week_end_returns = pd.Series([(3.0 - 1.5) / 1.5, (2.95 - 3.0) / 1.5])
    assert abs(max_drawdown_from_returns(week_end_returns) - (-0.05 / 1.5)) < 1e-9
    assert abs(max_drawdown_from_returns(week_end_returns) + 0.0333) < 1e-4
    assert abs(_max_drawdown_nav(intra_week) - (-0.15 / 3.0)) < 1e-12
    assert abs(_max_drawdown_nav(intra_week) + 0.05) < 1e-12
    # Here the legacy basis UNDERSTATES: it never sees the 0.15 trough.
    assert abs(_max_drawdown_nav(intra_week)) > abs(max_drawdown_from_returns(week_end_returns))

    # The same 0.15 lost at the week's end with no recovery: now the legacy basis
    # OVERSTATES, because it divides by 1.5 while the ledger stands at 3.0.
    no_recovery = np.array([1.5, 3.0, 2.85])
    held_returns = pd.Series([(3.0 - 1.5) / 1.5, (2.85 - 3.0) / 1.5])
    assert abs(max_drawdown_from_returns(held_returns) + 0.10) < 1e-9
    assert abs(_max_drawdown_nav(no_recovery) + 0.05) < 1e-12
    assert abs(max_drawdown_from_returns(held_returns)) > abs(_max_drawdown_nav(no_recovery))


def test_phantom_collateral_yield_trips_the_self_financing_guard() -> None:
    """WO-170 delta 1: the check the NAV identity could not be.

    `nav = cash + margin + spot_value` and `wealth = q*spot + margin + cash` are the
    same terms reassociated, so the NAV assertion fires only on a ledger mutated
    after `simulate` returns. The build review credited 1 bp of phantom collateral
    yield to `margin` inside the state machine with no cash flow recorded: the
    annualised return rose from 7.10% to 11.46% and the NAV check stayed silent.
    Conservation catches it, because the extra value has no source."""
    table = _table(40, rate=0.0001, perp=100.0, spot=100.0)
    start, end = int(table["boundary_ms"].iloc[0]), int(table["boundary_ms"].iloc[-1])
    clean = carry.ledger_frame(carry.simulate(table, variant="V0", start_ms=start, end_ms=end))
    assert clean["nav"].iloc[-1] > 1.5  # funding was earned, so the ledger did grow

    tampered = carry.simulate(table, variant="V0", start_ms=start, end_ms=end)
    for index in range(2, len(tampered.margin) - 1):
        if tampered.position_open[index] and tampered.traded_notional[index] == 0.0:
            credit = 0.0001 * (tampered.margin[index] + tampered.cash[index])
            tampered.margin[index] += credit
            tampered.wealth[index] += credit  # the NAV identity stays satisfied: both sides move together
    with pytest.raises(carry.CarryInputError, match="self-financing identity violated"):
        carry.ledger_frame(tampered)
