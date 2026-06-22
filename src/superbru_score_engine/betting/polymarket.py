from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

CATEGORY_TAKER_FEE_RATES: Mapping[str, float] = {
    "crypto": 0.07,
    "sports": 0.03,
    "finance": 0.04,
    "politics": 0.04,
    "economics": 0.05,
    "culture": 0.05,
    "weather": 0.05,
    "other": 0.05,
    "general": 0.05,
    "mentions": 0.04,
    "tech": 0.04,
    "geopolitics": 0.0,
}


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


def taker_fee_rate_for_category(category: str) -> float:
    """Return the current documented Polymarket taker fee rate for a category."""

    key = str(category or "other").strip().lower().replace(" / ", "_").replace(" ", "_")
    if key in {"other_general", "other__general"}:
        key = "other"
    return float(CATEGORY_TAKER_FEE_RATES.get(key, CATEGORY_TAKER_FEE_RATES["other"]))


def polymarket_taker_fee_usdc(
    *,
    shares: float,
    price: float,
    taker_fee_rate: float,
    fees_enabled: bool = True,
) -> float:
    """Calculate the Polymarket taker fee in USDC.

    Formula: fee = shares * taker_fee_rate * price * (1 - price).
    Polymarket rounds fees to 5 decimal places, and values that round to zero
    are treated as zero.
    """

    share_count = _validate_non_negative(shares, name="shares")
    share_price = _validate_probability(price, name="price")
    fee_rate = _validate_non_negative(taker_fee_rate, name="taker_fee_rate")
    if not fees_enabled or share_count == 0.0 or fee_rate == 0.0:
        return 0.0
    return round(share_count * fee_rate * share_price * (1.0 - share_price), 5)


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
