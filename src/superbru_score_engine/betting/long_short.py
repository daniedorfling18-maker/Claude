"""Long/short trading primitives for binary prediction markets (Polymarket).

Each outcome is a tradeable YES token priced in (0, 1). This module turns the
"bet an outcome" view into a "trade a contract" view, so the engine can go long or
short and generate capital from price structure rather than only from match results.

It provides:

- ``open_long``  - buy YES (long the outcome).
- ``open_short`` - buy the complementary NO (short the outcome) at ``1 - YES_bid``.
- ``directional_signal`` - long when an independent model price beats the ask, short
  when it is below the bid.
- ``market_make_quote`` - post a bid below / ask above fair to earn the spread; on
  Polymarket makers pay zero taker fee and may earn a rebate.
- ``dutch_arb`` - locked profit when the best obtainable prices of mutually exclusive
  outcomes (possibly on different venues) sum below 1.
- ``trade_out`` - close (or green-up) a position after a favourable move to bank a
  guaranteed profit / remove downside.

All taker-side fees reuse ``betting.polymarket`` so fee accounting matches the bot.

Capital-generation note: pre-match WC odds barely move (median ~0.4pp swing in the
captured window), so directional drift pays little. Market-making (spread + rebate)
and cross-venue arbitrage are the market-neutral engines; directional long/short only
pays where an independent model genuinely disagrees with the book.
"""
from __future__ import annotations

from dataclasses import dataclass

from .polymarket import polymarket_taker_fee_usdc, taker_fee_rate_for_category


def _price(value: float, name: str = "price") -> float:
    number = float(value)
    if not 0.0 < number < 1.0:
        raise ValueError(f"{name} must be strictly between 0 and 1, got {value!r}")
    return number


def _non_negative(value: float, name: str) -> float:
    number = float(value)
    if number < 0.0:
        raise ValueError(f"{name} must be non-negative, got {value!r}")
    return number


@dataclass(frozen=True)
class Position:
    """A long or short position on a single binary outcome.

    P&L is expressed relative to the *outcome* (YES resolving true) so a uniform
    ``expected_pnl(true_prob)`` works for both sides.
    """

    side: str               # "LONG" or "SHORT"
    token: str              # token actually bought: "YES" (long) or "NO" (short)
    outcome_yes_price: float  # YES price of the outcome at entry
    fill_price: float       # price paid for the bought token
    stake_usdc: float
    shares: float
    fee_usdc: float
    pnl_if_outcome: float       # P&L if the outcome occurs (YES true)
    pnl_if_not_outcome: float   # P&L if the outcome does not occur

    def expected_pnl(self, true_prob: float) -> float:
        q = _price(true_prob, "true_prob")
        return q * self.pnl_if_outcome + (1.0 - q) * self.pnl_if_not_outcome

    def edge(self, true_prob: float) -> float:
        """Expected P&L per USDC staked, given a true outcome probability."""
        return self.expected_pnl(true_prob) / self.stake_usdc if self.stake_usdc else 0.0


def open_long(outcome_yes_ask: float, stake_usdc: float, *, category: str = "sports",
              fees_enabled: bool = True) -> Position:
    """Long the outcome by buying YES shares at the ask."""
    ask = _price(outcome_yes_ask, "outcome_yes_ask")
    stake = _non_negative(stake_usdc, "stake_usdc")
    shares = 0.0 if stake == 0.0 else stake / ask
    rate = taker_fee_rate_for_category(category)
    fee = polymarket_taker_fee_usdc(shares=shares, price=ask, taker_fee_rate=rate, fees_enabled=fees_enabled)
    return Position("LONG", "YES", ask, ask, stake, shares, fee,
                    pnl_if_outcome=shares - stake - fee, pnl_if_not_outcome=-stake - fee)


def open_short(outcome_yes_bid: float, stake_usdc: float, *, category: str = "sports",
               fees_enabled: bool = True) -> Position:
    """Short the outcome by buying the complementary NO token at ``1 - YES_bid``.

    NO pays 1 when the outcome does NOT occur, so the short profits if YES resolves false.
    """
    bid = _price(outcome_yes_bid, "outcome_yes_bid")
    no_ask = _price(1.0 - bid, "implied_no_ask")
    stake = _non_negative(stake_usdc, "stake_usdc")
    shares = 0.0 if stake == 0.0 else stake / no_ask
    rate = taker_fee_rate_for_category(category)
    fee = polymarket_taker_fee_usdc(shares=shares, price=no_ask, taker_fee_rate=rate, fees_enabled=fees_enabled)
    return Position("SHORT", "NO", bid, no_ask, stake, shares, fee,
                    pnl_if_outcome=-stake - fee, pnl_if_not_outcome=shares - stake - fee)


@dataclass(frozen=True)
class DirectionalSignal:
    action: str             # "LONG", "SHORT", or "NONE"
    reason: str
    model_prob: float
    yes_bid: float
    yes_ask: float
    raw_edge: float         # model - ask (long) or bid - model (short); price-space edge
    position: Position | None


def directional_signal(model_prob: float, yes_bid: float, yes_ask: float, stake_usdc: float, *,
                       min_edge: float = 0.0, category: str = "sports",
                       fees_enabled: bool = True) -> DirectionalSignal:
    """Long when the model price beats the ask, short when it is below the bid.

    ``min_edge`` is a price-space margin (e.g. 0.02 = 2 cents) required before acting,
    covering spread noise and model/execution error. Inside the spread it returns NONE.
    """
    m = _price(model_prob, "model_prob")
    bid = _price(yes_bid, "yes_bid")
    ask = _price(yes_ask, "yes_ask")
    if m - ask >= min_edge:
        pos = open_long(ask, stake_usdc, category=category, fees_enabled=fees_enabled)
        return DirectionalSignal("LONG", f"model {m:.3f} >= ask {ask:.3f} + {min_edge:.3f}", m, bid, ask, m - ask, pos)
    if bid - m >= min_edge:
        pos = open_short(bid, stake_usdc, category=category, fees_enabled=fees_enabled)
        return DirectionalSignal("SHORT", f"model {m:.3f} <= bid {bid:.3f} - {min_edge:.3f}", m, bid, ask, bid - m, pos)
    return DirectionalSignal("NONE", "inside spread / no edge", m, bid, ask, 0.0, None)


@dataclass(frozen=True)
class MarketMakeQuote:
    fair: float
    bid: float
    ask: float
    spread: float
    target_shares: float
    maker_rebate_rate: float
    expected_capture_usdc: float


def market_make_quote(fair: float, half_spread: float, target_shares: float, *,
                      maker_rebate_rate: float = 0.0) -> MarketMakeQuote:
    """Quote a bid below / ask above fair. Makers pay no taker fee on Polymarket.

    If both sides fill once, the round-trip captures the full spread per share. A maker
    rebate (a share of taker fees) is added on notional. Adverse selection is NOT modelled
    here, so ``expected_capture_usdc`` is a gross upper bound on per-round-trip edge.
    """
    f = _price(fair, "fair")
    half = _non_negative(half_spread, "half_spread")
    shares = _non_negative(target_shares, "target_shares")
    bid = max(0.01, round(f - half, 4))
    ask = min(0.99, round(f + half, 4))
    spread = max(0.0, ask - bid)
    rebate = maker_rebate_rate * ((bid + ask) / 2.0) * shares
    return MarketMakeQuote(f, bid, ask, spread, shares, maker_rebate_rate, spread * shares + rebate)


@dataclass(frozen=True)
class ArbOpportunity:
    outcome_prices: tuple[float, ...]
    total_cost: float
    locked_profit_per_set: float   # per 1-USDC guaranteed payout set
    is_arb: bool


def dutch_arb(outcome_prices) -> ArbOpportunity:
    """Locked profit when best asks of mutually exclusive outcomes sum below 1.

    Pass the cheapest obtainable YES ask per outcome (each may be on a different venue,
    e.g. Polymarket vs a bookmaker's 1/decimal_odds). Buying one share of each guarantees
    a 1-USDC payout for ``sum(prices)``, so the lock is ``1 - sum`` per set when positive.
    """
    prices = tuple(_price(p, "outcome_price") for p in outcome_prices)
    total = sum(prices)
    return ArbOpportunity(prices, total, max(0.0, 1.0 - total), total < 1.0)


@dataclass(frozen=True)
class TradeOut:
    locked_pnl_if_closed: float   # P&L if the whole position is sold now
    green_up_sell_shares: float   # sell this many to remove downside, keep the rest free
    free_shares: float            # remaining YES shares carried as risk-free upside
    green_up_floor_pnl: float     # guaranteed P&L after green-up (>= ~0 when price rose)


def trade_out(entry_price: float, current_price: float, shares: float, *,
              category: str = "sports", fees_enabled: bool = True) -> TradeOut:
    """Close or green-up a long YES position after a price move.

    Full close banks ``shares * (current - entry)`` minus the taker fee. Green-up instead
    sells just enough (``shares * entry / current``) to recover the cost, leaving the rest
    as free upside if the outcome still resolves YES, with a ~zero downside floor.
    """
    entry = _price(entry_price, "entry_price")
    current = _price(current_price, "current_price")
    qty = _non_negative(shares, "shares")
    rate = taker_fee_rate_for_category(category)

    close_fee = polymarket_taker_fee_usdc(shares=qty, price=current, taker_fee_rate=rate, fees_enabled=fees_enabled)
    locked = qty * (current - entry) - close_fee

    sell = min(qty, qty * entry / current)
    free = qty - sell
    gu_fee = polymarket_taker_fee_usdc(shares=sell, price=current, taker_fee_rate=rate, fees_enabled=fees_enabled)
    floor = sell * current - qty * entry - gu_fee
    return TradeOut(locked, sell, free, floor)
