import pytest

from superbru_score_engine.betting.long_short import (
    directional_signal,
    dutch_arb,
    market_make_quote,
    open_long,
    open_short,
    trade_out,
)


def test_open_long_pnl_and_fair_value():
    pos = open_long(0.5, 100.0, fees_enabled=False)
    assert pos.side == "LONG" and pos.token == "YES"
    assert pos.shares == pytest.approx(200.0)
    assert pos.pnl_if_outcome == pytest.approx(100.0)
    assert pos.pnl_if_not_outcome == pytest.approx(-100.0)
    assert pos.expected_pnl(0.5) == pytest.approx(0.0)  # fair-priced -> zero edge


def test_long_fee_reduces_pnl():
    free = open_long(0.5, 100.0, fees_enabled=False)
    paid = open_long(0.5, 100.0, fees_enabled=True)
    assert paid.fee_usdc > 0
    assert paid.pnl_if_outcome < free.pnl_if_outcome


def test_open_short_profits_when_outcome_fails():
    pos = open_short(0.5, 100.0, fees_enabled=False)
    assert pos.side == "SHORT" and pos.token == "NO"
    assert pos.shares == pytest.approx(200.0)
    assert pos.pnl_if_not_outcome == pytest.approx(100.0)
    assert pos.pnl_if_outcome == pytest.approx(-100.0)
    assert pos.expected_pnl(0.5) == pytest.approx(0.0)


def test_short_favourite_is_negative_ev_when_model_high():
    pos = open_short(0.8, 100.0, fees_enabled=False)  # NO at 0.2 -> 500 shares
    assert pos.shares == pytest.approx(500.0)
    assert pos.pnl_if_not_outcome == pytest.approx(400.0)
    assert pos.edge(0.85) < 0


def test_directional_long_short_none():
    long_sig = directional_signal(0.62, 0.55, 0.57, 100.0, min_edge=0.02, fees_enabled=False)
    short_sig = directional_signal(0.40, 0.50, 0.52, 100.0, min_edge=0.02, fees_enabled=False)
    none_sig = directional_signal(0.515, 0.50, 0.52, 100.0, min_edge=0.02, fees_enabled=False)
    assert long_sig.action == "LONG" and long_sig.position is not None
    assert short_sig.action == "SHORT" and short_sig.position.side == "SHORT"
    assert none_sig.action == "NONE" and none_sig.position is None


def test_directional_long_has_positive_model_edge():
    sig = directional_signal(0.62, 0.55, 0.57, 100.0, min_edge=0.0, fees_enabled=False)
    assert sig.position.edge(0.62) > 0


def test_market_make_quote_captures_spread_and_rebate():
    q = market_make_quote(0.50, 0.02, 100.0)
    assert q.bid < q.fair < q.ask
    assert q.spread == pytest.approx(0.04)
    assert q.expected_capture_usdc == pytest.approx(4.0)
    q2 = market_make_quote(0.50, 0.02, 100.0, maker_rebate_rate=0.01)
    assert q2.expected_capture_usdc > q.expected_capture_usdc


def test_dutch_arb_detects_lock():
    arb = dutch_arb([0.30, 0.30, 0.35])
    assert arb.is_arb and arb.locked_profit_per_set == pytest.approx(0.05)
    flat = dutch_arb([0.40, 0.35, 0.30])
    assert not flat.is_arb and flat.locked_profit_per_set == 0.0


def test_trade_out_close_and_green_up():
    to = trade_out(0.50, 0.60, 200.0, fees_enabled=False)
    assert to.locked_pnl_if_closed == pytest.approx(20.0)
    assert to.green_up_sell_shares == pytest.approx(200 * 0.5 / 0.6)
    assert to.free_shares == pytest.approx(200 - 200 * 0.5 / 0.6)
    assert to.green_up_floor_pnl == pytest.approx(0.0, abs=1e-9)


def test_price_validation():
    with pytest.raises(ValueError):
        open_long(0.0, 100.0)
    with pytest.raises(ValueError):
        directional_signal(1.0, 0.5, 0.6, 100.0)
