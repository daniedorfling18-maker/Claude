from __future__ import annotations

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
