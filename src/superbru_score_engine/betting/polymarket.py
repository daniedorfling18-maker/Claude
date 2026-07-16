from __future__ import annotations

from dataclasses import dataclass

from polymarket_common.fees import (
    CATEGORY_TAKER_FEE_RATES,
    polymarket_taker_fee_usdc,
    taker_fee_rate_for_category,
)


@dataclass(frozen=True)
class PolymarketFlatStakeTrade:
    """A taker-side Polymarket YES purchase sized by flat USDC stake.

    `stake_usdc` is the amount spent buying outcome shares before protocol fees.
    The taker fee is modelled as an additional USDC cost at match time.
    """

    stake_usdc: float
    price: float
    shares: float
    taker_fee_rate: float
    taker_fee_usdc: float
    fees_enabled: bool
    win_net_pnl_usdc: float
    lose_net_pnl_usdc: float

    def expected_pnl(self, model_probability: float) -> float:
        probability = _validate_probability(model_probability, name="model_probability")
        return probability * self.win_net_pnl_usdc + (1.0 - probability) * self.lose_net_pnl_usdc


def build_flat_stake_yes_trade(
    *,
    stake_usdc: float,
    price: float,
    category: str = "sports",
    taker_fee_rate: float | None = None,
    fees_enabled: bool = True,
) -> PolymarketFlatStakeTrade:
    """Build a flat-stake YES-side trade for a binary Polymarket outcome.

    The trade buys `stake_usdc / price` YES shares. If the outcome resolves YES,
    the gross payout is one USDC per share. If it resolves NO, the shares expire
    worthless. In both cases the taker fee is charged once on the matched trade.
    """

    stake = _validate_non_negative(stake_usdc, name="stake_usdc")
    share_price = _validate_probability(price, name="price")
    fee_rate = taker_fee_rate_for_category(category) if taker_fee_rate is None else _validate_non_negative(
        taker_fee_rate,
        name="taker_fee_rate",
    )
    shares = 0.0 if stake == 0.0 else stake / share_price
    fee = polymarket_taker_fee_usdc(
        shares=shares,
        price=share_price,
        taker_fee_rate=fee_rate,
        fees_enabled=fees_enabled,
    )
    win_net = shares - stake - fee
    lose_net = -stake - fee
    return PolymarketFlatStakeTrade(
        stake_usdc=stake,
        price=share_price,
        shares=shares,
        taker_fee_rate=fee_rate,
        taker_fee_usdc=fee,
        fees_enabled=bool(fees_enabled),
        win_net_pnl_usdc=win_net,
        lose_net_pnl_usdc=lose_net,
    )


def flat_stake_yes_pnl_vector(trade: PolymarketFlatStakeTrade, wins) -> list[float]:
    """Return scenario P&L values for a flat-stake YES trade."""

    return [trade.win_net_pnl_usdc if bool(win) else trade.lose_net_pnl_usdc for win in wins]


def _validate_probability(value: float, *, name: str) -> float:
    number = float(value)
    if not 0.0 < number < 1.0:
        raise ValueError(f"{name} must be strictly between 0 and 1, got {value!r}")
    return number


def _validate_non_negative(value: float, *, name: str) -> float:
    number = float(value)
    if number < 0.0:
        raise ValueError(f"{name} must be non-negative, got {value!r}")
    return number
