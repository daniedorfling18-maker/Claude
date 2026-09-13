"""Lane A — perpetual funding carry state machine (WO-166).

Long spot, short USDT-margined perpetual, inception notional ``N = 1`` per
underlying, capital ``C = 1.5 N`` fixed at inception. Everything below is a
pure function of in-memory frames; no clock, no network, no filesystem.

Accounts. ``q`` spot held and ``q`` perpetual short; ``margin`` is the
perpetual margin account, marked at every boundary and credited with
funding; ``cash`` holds everything else — the whole wealth when flat, and the
cumulative fees when a position is open. Wealth at a boundary is
``q x spot + margin + cash``. Fees never come out of the margin account, so
the margin ratio is exactly 0.5 at entry and after every resize, and the
registered thresholds hold exactly.

Rules, all registered in WO-166 and stated on quantities observable at the
acting site:

* Funding boundaries every 8 hours. Period ``t`` runs from boundary ``t-1`` to
  boundary ``t``; ``f_t`` is the rate settled at boundary ``t`` and is applied
  to the perpetual notional at that boundary (positive pays the short).
* Boundary prices are the 1h close of the hour ending at the boundary.
* Fees: spot taker 10 bps, perpetual taker 5 bps, on every leg traded.
* Forced liquidation inside a period when the intra-period perpetual high
  ``H`` drives the margin ratio below the 0.5% maintenance level, using the
  ratio ``m0`` and price ``P0`` at the period's start:
  ``(m0 - x) / (1 + x) < 0.005`` with ``x = H / P0 - 1``, i.e.
  ``x > (m0 - 0.005) / 1.005`` — 0.4925 when ``m0 = 0.5``. The margin is
  consumed, the spot rides unhedged to the boundary, and the structure is
  re-established there from remaining wealth at full cost.
* At a boundary, a margin ratio below 0.25 resizes the position so the ratio
  is exactly 0.5 again: ``q' = (q S + M) / (S + 0.5 P)``; the spot sold and
  the perpetual bought back are both charged taker fees.
* No re-leveraging when the price falls: a smaller notional earns less.
* A period whose boundary close is missing is merged into the next period and
  flagged; a period with fewer than 8 intra-period hourly highs is flagged.
  Flagged periods make their week ineligible; nothing is forward-filled. An
  open position inside a flagged period has unverifiable liquidation status
  and is counted as such; the gate reads that count.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

HOUR_MS = 3_600_000
PERIOD_MS = 8 * HOUR_MS
PERIODS_PER_YEAR = 1_095
WEEKS_PER_YEAR = 52
PERIODS_PER_WEEK = 21

SPOT_TAKER_FEE = 0.0010
PERP_TAKER_FEE = 0.0005
MARGIN_FRACTION = 0.50
CAPITAL_PER_NOTIONAL = 1.0 + MARGIN_FRACTION
MAINTENANCE_MARGIN = 0.005
REBALANCE_MARGIN_RATIO = 0.25

NAV_IDENTITY_TOLERANCE = 1e-12
# WO-170 delta 1: the conservation check runs on absolute USD-per-notional flows, so its
# floor is float64 rounding accumulated over a period, not the NAV identity's exact reassociation.
SELF_FINANCING_TOLERANCE = 1e-9  # WO-170: nav and wealth are the same three float64 terms in a different association order (true gap <= a few x 2.2e-16 on O(1) values)

V1_LOOKBACK = 3
V1_ENTRY_ANNUALISED = 0.08
V1_EXIT_LEVEL = 0.0


class CarryInputError(ValueError):
    """Raised when the inputs cannot support the registered estimator. Fail-closed: no result."""


def liquidation_move(margin_ratio_at_start: float) -> float:
    """The perpetual move above which the short is force-liquidated: ``(m0 - 0.005) / 1.005``."""
    return (margin_ratio_at_start - MAINTENANCE_MARGIN) / (1.0 + MAINTENANCE_MARGIN)


# ------------------------------------------------------------------ boundary table


def boundary_table(funding: pd.DataFrame, perp_1h: pd.DataFrame, spot_1h: pd.DataFrame) -> pd.DataFrame:
    """One row per funding boundary with the closes, the intra-period perpetual high, and flags.

    Columns: ``boundary_ms, rate, perp_close, spot_close, perp_high, high_hours, close_missing, high_partial``.
    ``perp_close``/``spot_close`` are NaN when the hour ending at the boundary is absent.
    """
    if funding.empty:
        raise CarryInputError("funding frame is empty")
    perp_close = dict(zip(perp_1h["open_time"].astype(np.int64), perp_1h["close"].astype(float)))
    perp_high = dict(zip(perp_1h["open_time"].astype(np.int64), perp_1h["high"].astype(float)))
    spot_close = dict(zip(spot_1h["open_time"].astype(np.int64), spot_1h["close"].astype(float)))
    records: list[dict[str, object]] = []
    for boundary, rate in zip(funding["calc_time"].astype(np.int64), funding["last_funding_rate"].astype(float)):
        boundary = int(boundary)
        if not math.isfinite(rate):
            raise CarryInputError(f"non-finite funding rate at {boundary}")
        hour_before = boundary - HOUR_MS
        pc = perp_close.get(hour_before, float("nan"))
        sc = spot_close.get(hour_before, float("nan"))
        highs = [perp_high[h] for h in range(boundary - PERIOD_MS, boundary, HOUR_MS) if h in perp_high]
        records.append(
            {
                "boundary_ms": boundary,
                "rate": float(rate),
                "perp_close": float(pc),
                "spot_close": float(sc),
                "perp_high": float(max(highs)) if highs else float("nan"),
                "high_hours": int(len(highs)),
                "close_missing": bool(not (math.isfinite(pc) and math.isfinite(sc))),
                "perp_close_missing": bool(not math.isfinite(pc)),
                "spot_close_missing": bool(not math.isfinite(sc)),
                "high_partial": bool(len(highs) < 8),
            }
        )
    return pd.DataFrame.from_records(records)


# ------------------------------------------------------------------- the position


@dataclass
class Position:
    q: float = 0.0
    margin: float = 0.0
    cash: float = 0.0
    last_perp: float = float("nan")
    last_spot: float = float("nan")
    open: bool = False

    def wealth(self, spot_price: float) -> float:
        return self.q * spot_price + self.margin + self.cash

    def margin_ratio(self, perp_price: float) -> float:
        notional = self.q * perp_price
        return self.margin / notional if notional > 0 else float("nan")


@dataclass
class Trade:
    fees: float
    traded_notional: float


@dataclass
class Ledger:
    """Per-boundary accounting. Money is in units of the inception notional ``N = 1``."""

    boundary_ms: list[int] = field(default_factory=list)
    wealth: list[float] = field(default_factory=list)
    funding_received: list[float] = field(default_factory=list)
    fees_paid: list[float] = field(default_factory=list)
    traded_notional: list[float] = field(default_factory=list)
    position_open: list[bool] = field(default_factory=list)
    flagged: list[bool] = field(default_factory=list)
    rebalances: list[int] = field(default_factory=list)
    forced_liquidations: list[int] = field(default_factory=list)
    unverifiable_open: list[int] = field(default_factory=list)
    rejected_open: list[int] = field(default_factory=list)  # open position inside a rejected period, under every scope (the WO-166 "either" count)
    # WO-170: the accounts behind ``wealth`` at every boundary, so the cash flows are identifiable and ``nav`` can be re-derived.
    cash: list[float] = field(default_factory=list)
    margin: list[float] = field(default_factory=list)
    spot_qty: list[float] = field(default_factory=list)
    spot_mark: list[float] = field(default_factory=list)
    perp_mark: list[float] = field(default_factory=list)
    marks_carried_forward: list[bool] = field(default_factory=list)

    def append(self, boundary: int, wealth: float, *, funding: float = 0.0, fees: float = 0.0, traded: float = 0.0, open: bool, flagged: bool = False, rebalanced: int = 0, liquidated: int = 0, unverifiable: int = 0, rejected: int = 0, cash: float = 0.0, margin: float = 0.0, spot_qty: float = 0.0, spot_mark: float = float("nan"), perp_mark: float = float("nan"), carried: bool = False) -> None:
        self.boundary_ms.append(int(boundary))
        self.wealth.append(float(wealth))
        self.funding_received.append(float(funding))
        self.fees_paid.append(float(fees))
        self.traded_notional.append(float(traded))
        self.position_open.append(bool(open))
        self.flagged.append(bool(flagged))
        self.rebalances.append(int(rebalanced))
        self.forced_liquidations.append(int(liquidated))
        self.unverifiable_open.append(int(unverifiable))
        self.rejected_open.append(int(rejected))
        self.cash.append(float(cash))
        self.margin.append(float(margin))
        self.spot_qty.append(float(spot_qty))
        self.spot_mark.append(float(spot_mark))
        self.perp_mark.append(float(perp_mark))
        self.marks_carried_forward.append(bool(carried))


def _check_prices(spot_price: float, perp_price: float) -> None:
    if not (math.isfinite(spot_price) and math.isfinite(perp_price)) or spot_price <= 0 or perp_price <= 0:
        raise CarryInputError("cannot trade on a missing or non-positive price")


def open_position(flat_wealth: float, spot_price: float, perp_price: float, *, notional: float | None = None, fee_mult: float = 1.0) -> tuple[Position, Trade]:
    """From flat, buy spot ``N`` and sell the perpetual ``N`` with margin ``0.5 N``; fees go to cash.

    ``notional`` defaults to ``flat_wealth / 1.5`` (the inception structure); the
    inception call passes ``notional=1.0`` explicitly.
    """
    _check_prices(spot_price, perp_price)
    target = flat_wealth / CAPITAL_PER_NOTIONAL if notional is None else notional
    if target <= 0:
        raise CarryInputError("cannot open a position with non-positive notional")
    q = target / spot_price
    fees = fee_mult * q * (SPOT_TAKER_FEE * spot_price + PERP_TAKER_FEE * perp_price)
    margin = MARGIN_FRACTION * q * perp_price
    cash = flat_wealth - q * spot_price - margin - fees
    position = Position(q=q, margin=margin, cash=cash, last_perp=perp_price, last_spot=spot_price, open=True)
    return position, Trade(fees=fees, traded_notional=q * (spot_price + perp_price))


def close_position(position: Position, spot_price: float, perp_price: float, *, fee_mult: float = 1.0) -> tuple[Position, Trade]:
    """Sell spot and buy back the perpetual (margin already marked to ``perp_price``); all wealth to cash."""
    _check_prices(spot_price, perp_price)
    q = position.q
    fees = fee_mult * q * (SPOT_TAKER_FEE * spot_price + PERP_TAKER_FEE * perp_price)
    cash = q * spot_price + position.margin + position.cash - fees
    return Position(cash=cash, last_perp=perp_price, last_spot=spot_price, open=False), Trade(fees=fees, traded_notional=q * (spot_price + perp_price))


def resize_to_half_margin(position: Position, spot_price: float, perp_price: float, *, fee_mult: float = 1.0) -> tuple[Position, Trade]:
    """Sell spot and buy back perpetual so the margin ratio is exactly 0.5: ``q' = (q S + M) / (S + 0.5 P)``."""
    _check_prices(spot_price, perp_price)
    q, margin = position.q, position.margin
    q_new = (q * spot_price + margin) / (spot_price + MARGIN_FRACTION * perp_price)
    if q_new <= 0 or q_new > q:
        raise CarryInputError("resize produced a non-positive or larger position")
    traded_q = q - q_new
    fees = fee_mult * traded_q * (SPOT_TAKER_FEE * spot_price + PERP_TAKER_FEE * perp_price)
    new = Position(q=q_new, margin=MARGIN_FRACTION * q_new * perp_price, cash=position.cash - fees, last_perp=perp_price, last_spot=spot_price, open=True)
    return new, Trade(fees=fees, traded_notional=traded_q * (spot_price + perp_price))


# ------------------------------------------------------------------- simulation


def v1_targets(rates: np.ndarray) -> np.ndarray:
    """Target position after each boundary using only rates settled at or before it (effective next period)."""
    n = len(rates)
    target = np.zeros(n, dtype=bool)
    holding = False
    for t in range(n):
        if t < V1_LOOKBACK - 1:
            target[t] = holding
            continue
        signal = float(np.mean(rates[t - V1_LOOKBACK + 1 : t + 1]))
        if not math.isfinite(signal):
            holding = False  # registered: a non-finite signal neither enters nor stays in
        elif not holding and signal * PERIODS_PER_YEAR > V1_ENTRY_ANNUALISED:
            holding = True
        elif holding and signal < V1_EXIT_LEVEL:
            holding = False
        target[t] = holding
    return target


UNVERIFIABLE_SCOPES = ("either", "perp")  # "either": WO-166 (any absent bar); "perp": WO-167 (perpetual-side data the liquidation check reads)


def simulate(table: pd.DataFrame, *, variant: str, start_ms: int, end_ms: int, capital: float = CAPITAL_PER_NOTIONAL, fee_mult: float = 1.0, unverifiable_scope: str = "either") -> Ledger:
    """Run V0 (always on between ``start_ms`` and ``end_ms``) or V1 (signal-driven) over the boundary table.

    ``start_ms`` is the boundary at which V0 enters; ``end_ms`` is the boundary
    at which any open position is closed. Both must carry a close.
    """
    if variant not in {"V0", "V1"}:
        raise CarryInputError(f"unknown variant {variant!r}")
    if unverifiable_scope not in UNVERIFIABLE_SCOPES:
        raise CarryInputError(f"unknown unverifiable_scope {unverifiable_scope!r}; registered values are {UNVERIFIABLE_SCOPES}")
    perp_only = unverifiable_scope == "perp"
    rows = table[(table["boundary_ms"] >= start_ms) & (table["boundary_ms"] <= end_ms)].reset_index(drop=True)
    if len(rows) < 2:
        raise CarryInputError("fewer than two boundaries inside the requested span")
    if int(rows.iloc[0]["boundary_ms"]) != int(start_ms) or int(rows.iloc[-1]["boundary_ms"]) != int(end_ms):
        raise CarryInputError("the entry or exit boundary is not a row of the boundary table; refuse to shift it")
    if bool(rows.iloc[0]["close_missing"]) or bool(rows.iloc[-1]["close_missing"]):
        raise CarryInputError("the entry or exit boundary has no close; refuse to guess")
    targets = v1_targets(rows["rate"].to_numpy(dtype=float)) if variant == "V1" else np.ones(len(rows), dtype=bool)
    ledger = Ledger()
    position = Position(cash=capital)
    pending_rates: list[float] = []
    pending_high = float("nan")
    pending_flag = False
    prev_perp_close_missing = False  # the entry boundary carries a close (asserted above)

    for index, row in rows.iterrows():
        boundary = int(row["boundary_ms"])
        perp_close = float(row["perp_close"])
        spot_close = float(row["spot_close"])
        rate = float(row["rate"])
        is_last = index == len(rows) - 1
        open_at_start = position.open
        fees = 0.0
        traded = 0.0
        funding = 0.0
        rebalanced = 0
        liquidated = 0

        if index == 0:
            starting_wealth = position.cash  # the pre-trade capital: the entry fee is then inside the first period's return
            if variant == "V0" or bool(targets[0]):
                position, trade = open_position(position.cash, spot_close, perp_close, notional=min(1.0, position.cash / CAPITAL_PER_NOTIONAL), fee_mult=fee_mult)
                fees, traded = trade.fees, trade.traded_notional
            # WO-170: the entry row records the PRE-trade accounts (cash = capital, nothing held), so nav == wealth there and the
            # entry fee falls inside the first period, exactly where WO-166 placed it.
            ledger.append(boundary, starting_wealth, fees=fees, traded=traded, open=position.open, cash=starting_wealth, margin=0.0, spot_qty=0.0, spot_mark=spot_close, perp_mark=perp_close, carried=False)
            continue

        row_high = float(row["perp_high"])
        if math.isfinite(row_high):
            pending_high = row_high if not math.isfinite(pending_high) else max(pending_high, row_high)
        pending_flag = pending_flag or bool(row["high_partial"])
        # WO-167 scope: this period's liquidation check is unverifiable only if one of its perpetual
        # highs is absent or the perpetual close at its start boundary (the previous row) is absent.
        perp_incomplete = bool(row["high_partial"]) or prev_perp_close_missing
        prev_perp_close_missing = bool(row["perp_close_missing"])  # strict: a table without the per-leg flag is not a boundary table

        if bool(row["close_missing"]):
            pending_rates.append(rate)
            # An open position inside a period with missing bars has unverifiable liquidation status.
            merged_unverifiable = int(open_at_start and (perp_incomplete if perp_only else True))
            ledger.append(boundary, position.wealth(position.last_spot) if position.open else position.cash, open=position.open, flagged=True, unverifiable=merged_unverifiable, rejected=int(open_at_start), cash=position.cash, margin=position.margin, spot_qty=position.q, spot_mark=position.last_spot, perp_mark=position.last_perp, carried=True)
            continue

        flagged = pending_flag or bool(pending_rates)
        unverifiable = int(open_at_start and (perp_incomplete if perp_only else flagged))
        rejected = int(open_at_start and flagged)
        if open_at_start and not math.isfinite(pending_high):
            unverifiable = 1  # no intra-period perpetual high to check against: liquidation status is unverifiable under every scope

        if position.open:
            m0 = position.margin_ratio(position.last_perp)
            if math.isfinite(pending_high) and pending_high / position.last_perp - 1.0 > liquidation_move(m0):
                # Forced liquidation: the margin is consumed; spot rides unhedged to the boundary;
                # the structure is re-established there from remaining wealth at full cost.
                liquidated = 1
                remaining = position.q * spot_close + position.cash
                position, trade = open_position(remaining, spot_close, perp_close, fee_mult=fee_mult)
                fees += trade.fees
                traded += trade.traded_notional
            else:
                position.margin -= position.q * (perp_close - position.last_perp)
                for merged_rate in pending_rates:
                    funding += merged_rate * position.q * perp_close
                funding += rate * position.q * perp_close
                position.margin += funding
                position.last_perp = perp_close
                position.last_spot = spot_close
                if position.margin_ratio(perp_close) < REBALANCE_MARGIN_RATIO:
                    position, trade = resize_to_half_margin(position, spot_close, perp_close, fee_mult=fee_mult)
                    fees += trade.fees
                    traded += trade.traded_notional
                    rebalanced = 1

        want_open = False if is_last else (variant == "V0" or bool(targets[index]))
        if position.open and not want_open:
            position, trade = close_position(position, spot_close, perp_close, fee_mult=fee_mult)
            fees += trade.fees
            traded += trade.traded_notional
        elif not position.open and want_open:
            position, trade = open_position(position.cash, spot_close, perp_close, fee_mult=fee_mult)
            fees += trade.fees
            traded += trade.traded_notional

        pending_rates = []
        pending_high = float("nan")
        pending_flag = False
        ledger.append(boundary, position.wealth(spot_close), funding=funding, fees=fees, traded=traded, open=position.open, flagged=flagged, rebalanced=rebalanced, liquidated=liquidated, unverifiable=unverifiable, rejected=rejected, cash=position.cash, margin=position.margin, spot_qty=position.q, spot_mark=spot_close, perp_mark=perp_close, carried=False)
    return ledger


# ------------------------------------------------------------------- aggregation


def ledger_frame(ledger: Ledger, *, capital: float = CAPITAL_PER_NOTIONAL) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "boundary_ms": ledger.boundary_ms,
            "wealth": ledger.wealth,
            "funding_received": ledger.funding_received,
            "fees_paid": ledger.fees_paid,
            "traded_notional": ledger.traded_notional,
            "position_open": ledger.position_open,
            "flagged": ledger.flagged,
            "rebalances": ledger.rebalances,
            "forced_liquidations": ledger.forced_liquidations,
            "unverifiable_open": ledger.unverifiable_open,
            "rejected_open": ledger.rejected_open,
            "cash": ledger.cash,
            "margin": ledger.margin,
            "spot_qty": ledger.spot_qty,
            "spot_mark": ledger.spot_mark,
            "perp_mark": ledger.perp_mark,
            "marks_carried_forward": ledger.marks_carried_forward,
        }
    )
    frame["period_return_on_notional"] = frame["wealth"].diff().fillna(0.0)
    frame["period_return_on_capital"] = frame["period_return_on_notional"] / capital
    # WO-170: the NAV identity. ``spot_value`` is 0 when nothing is held (a flat position may carry NaN marks).
    qty = frame["spot_qty"].to_numpy(dtype=float)
    frame["spot_value"] = np.where(qty != 0.0, qty * frame["spot_mark"].to_numpy(dtype=float), 0.0)
    frame["nav"] = frame["cash"] + frame["margin"] + frame["spot_value"]
    gap = np.abs(frame["nav"].to_numpy(dtype=float) - frame["wealth"].to_numpy(dtype=float))
    if not bool(np.all(gap <= NAV_IDENTITY_TOLERANCE)):
        worst = int(np.nanargmax(np.where(np.isfinite(gap), gap, np.inf)))
        raise CarryInputError(f"NAV identity violated at boundary {int(frame['boundary_ms'].iloc[worst])}: nav {frame['nav'].iloc[worst]!r} vs wealth {frame['wealth'].iloc[worst]!r}")
    _assert_self_financing(frame)
    nav = frame["nav"].to_numpy(dtype=float)
    prev = np.concatenate(([float("nan")], nav[:-1]))
    with np.errstate(divide="ignore", invalid="ignore"):
        ret = (nav - prev) / prev
    ret = np.where(np.isfinite(prev) & (prev > 0.0), ret, float("nan"))
    ret[0] = 0.0  # the entry row carries no period, mirroring period_return_on_capital's diff().fillna(0.0)
    frame["period_return_on_nav"] = ret
    return frame


def _assert_self_financing(frame: pd.DataFrame) -> None:
    """WO-170 delta 1: the identity the NAV check could not see.

    ``nav = cash + margin + spot_qty x spot_mark`` and ``wealth = q x spot + margin
    + cash`` are the same three terms reassociated, so the NAV assertion is an
    algebraic tautology: it fires only if the exported ledger is mutated after the
    fact. The build review demonstrated a single-site credit of phantom collateral
    yield inside the state machine that raised the annualised return from 7.10% to
    11.46% with the NAV check silent. This asserts CONSERVATION instead — every
    change in NAV must be explained by the position's mark-to-market, the funding it
    received and the fees it paid, with no unexplained source of value:

        dNAV_t = q_{t-1} x [(S_t - S_{t-1}) - (P_t - P_{t-1})] + funding_t - fees_t

    Two kinds of transition are skipped. Where either mark is absent or carried
    forward, the ledger has no price at which to state the mark-to-market, and those
    periods are excluded from every estimator anyway. Where either endpoint traded
    (entry, exit or a resize), the fee is booked to the row that decided the trade
    while its NAV effect lands on the next, so the one-step form does not apply; on
    the committed ledgers that is 22 transitions out of 7,288 per ledger, and the
    identity is asserted on every other one.
    """
    qty = frame["spot_qty"].to_numpy(dtype=float)
    spot = frame["spot_mark"].to_numpy(dtype=float)
    perp = frame["perp_mark"].to_numpy(dtype=float)
    nav = frame["nav"].to_numpy(dtype=float)
    funding = frame["funding_received"].to_numpy(dtype=float)
    fees = frame["fees_paid"].to_numpy(dtype=float)
    carried = frame["marks_carried_forward"].to_numpy(dtype=bool)
    traded = frame["traded_notional"].to_numpy(dtype=float) != 0.0
    if nav.size < 2:
        return
    held = qty[:-1]
    priced = np.isfinite(spot[:-1]) & np.isfinite(spot[1:]) & np.isfinite(perp[:-1]) & np.isfinite(perp[1:])
    checkable = priced & ~carried[1:] & ~carried[:-1] & ~traded[1:] & ~traded[:-1]
    expected = held * ((spot[1:] - spot[:-1]) - (perp[1:] - perp[:-1])) + funding[1:] - fees[1:]
    gap = np.abs((nav[1:] - nav[:-1]) - expected)
    gap = np.where(checkable, gap, 0.0)
    if not bool(np.all(gap <= SELF_FINANCING_TOLERANCE)):
        worst = int(np.argmax(gap))
        boundary = int(frame["boundary_ms"].iloc[worst + 1])
        raise CarryInputError(
            f"self-financing identity violated at boundary {boundary}: NAV moved by "
            f"{nav[worst + 1] - nav[worst]!r} but mark-to-market, funding and fees explain {expected[worst]!r}"
        )


def _compound(series: pd.Series) -> float:
    """WO-170: ``prod(1 + r) - 1`` over a week's periods; NaN if any period return is not finite."""
    values = series.to_numpy(dtype=float)
    if values.size == 0 or not np.isfinite(values).all():
        return float("nan")
    return float(np.prod(1.0 + values) - 1.0)


def iso_week_key(boundary_ms: int) -> tuple[int, int]:
    """ISO (year, week) of the instant just before the boundary, so a period ending Monday 00:00 belongs to the week it closes."""
    stamp = pd.Timestamp(int(boundary_ms) - 1, unit="ms", tz="UTC")
    iso = stamp.isocalendar()
    return int(iso.year), int(iso.week)


def weekly_returns(frame: pd.DataFrame) -> pd.DataFrame:
    """Sum period returns per ISO week; a week is eligible only when complete (21 periods) and unflagged."""
    periods = frame.iloc[1:].copy()  # the entry boundary carries no period; its fees and traded notional belong to the first period
    if periods.empty:
        raise CarryInputError("no periods after the entry boundary")
    first = periods.index[0]
    periods.loc[first, "fees_paid"] = float(periods.loc[first, "fees_paid"]) + float(frame["fees_paid"].iloc[0])
    periods.loc[first, "traded_notional"] = float(periods.loc[first, "traded_notional"]) + float(frame["traded_notional"].iloc[0])
    keys = periods["boundary_ms"].map(iso_week_key)
    periods["iso_year"] = keys.map(lambda key: key[0])
    periods["iso_week"] = keys.map(lambda key: key[1])
    out = (
        periods.groupby(["iso_year", "iso_week"], sort=True)
        .agg(
            return_on_capital=("period_return_on_capital", "sum"),
            return_on_notional=("period_return_on_notional", "sum"),
            periods=("boundary_ms", "size"),
            flagged=("flagged", "any"),
            funding_received=("funding_received", "sum"),
            fees_paid=("fees_paid", "sum"),
            traded_notional=("traded_notional", "sum"),
            rebalances=("rebalances", "sum"),
            forced_liquidations=("forced_liquidations", "sum"),
            unverifiable_open=("unverifiable_open", "sum"),
            rejected_open=("rejected_open", "sum"),
            return_on_nav=("period_return_on_nav", _compound),
            nav_end=("nav", "last"),
            week_end_ms=("boundary_ms", "max"),
            negative_funding_periods=("funding_received", lambda s: int((s < 0).sum())),
        )
        .reset_index()
    )
    out["eligible"] = (out["periods"] == PERIODS_PER_WEEK) & (~out["flagged"])
    return out


def first_monday_boundary_on_or_after(ts_ms: int) -> int:
    """The first Monday 00:00 UTC at or after ``ts_ms`` (a Monday 08:00 input yields the following Monday)."""
    stamp = pd.Timestamp(int(ts_ms), unit="ms", tz="UTC").normalize()
    if int(stamp.value // 1_000_000) < int(ts_ms):
        stamp += pd.Timedelta(days=1)
    while stamp.weekday() != 0:
        stamp += pd.Timedelta(days=1)
    return int(stamp.value // 1_000_000)


def last_monday_boundary_on_or_before(ts_ms: int) -> int:
    stamp = pd.Timestamp(int(ts_ms), unit="ms", tz="UTC").normalize()
    while stamp.weekday() != 0:
        stamp -= pd.Timedelta(days=1)
    return int(stamp.value // 1_000_000)
