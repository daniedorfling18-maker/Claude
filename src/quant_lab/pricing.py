from __future__ import annotations

from dataclasses import dataclass
from math import erf, exp, log, sqrt
from typing import Literal

OptionType = Literal["call", "put"]


@dataclass(frozen=True)
class BlackScholesGreeks:
    price: float
    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def normal_pdf(x: float) -> float:
    from math import pi

    return exp(-0.5 * x * x) / sqrt(2.0 * pi)


def _validate_inputs(spot: float, strike: float, rate: float, volatility: float, years: float) -> None:
    del rate
    if spot <= 0:
        raise ValueError("spot must be positive")
    if strike <= 0:
        raise ValueError("strike must be positive")
    if volatility <= 0:
        raise ValueError("volatility must be positive")
    if years <= 0:
        raise ValueError("years must be positive")


def _d1_d2(spot: float, strike: float, rate: float, volatility: float, years: float) -> tuple[float, float]:
    _validate_inputs(spot, strike, rate, volatility, years)
    d1 = (log(spot / strike) + (rate + 0.5 * volatility**2) * years) / (volatility * sqrt(years))
    return d1, d1 - volatility * sqrt(years)


def black_scholes_price(
    *,
    spot: float,
    strike: float,
    rate: float,
    volatility: float,
    years: float,
    option_type: OptionType = "call",
) -> float:
    """Closed-form European option price for a non-dividend-paying asset."""
    d1, d2 = _d1_d2(spot, strike, rate, volatility, years)
    if option_type == "call":
        return spot * normal_cdf(d1) - strike * exp(-rate * years) * normal_cdf(d2)
    if option_type == "put":
        return strike * exp(-rate * years) * normal_cdf(-d2) - spot * normal_cdf(-d1)
    raise ValueError("option_type must be 'call' or 'put'")


def black_scholes_greeks(
    *,
    spot: float,
    strike: float,
    rate: float,
    volatility: float,
    years: float,
    option_type: OptionType = "call",
) -> BlackScholesGreeks:
    """Return price and first-order Greeks.

    Theta is annualised and vega/rho are returned per 1.00 change, not per 1%.
    """
    d1, d2 = _d1_d2(spot, strike, rate, volatility, years)
    pdf = normal_pdf(d1)
    discounted_strike = strike * exp(-rate * years)
    price = black_scholes_price(
        spot=spot,
        strike=strike,
        rate=rate,
        volatility=volatility,
        years=years,
        option_type=option_type,
    )
    gamma = pdf / (spot * volatility * sqrt(years))
    vega = spot * pdf * sqrt(years)
    if option_type == "call":
        delta = normal_cdf(d1)
        theta = -(spot * pdf * volatility) / (2 * sqrt(years)) - rate * discounted_strike * normal_cdf(d2)
        rho = years * discounted_strike * normal_cdf(d2)
    elif option_type == "put":
        delta = normal_cdf(d1) - 1.0
        theta = -(spot * pdf * volatility) / (2 * sqrt(years)) + rate * discounted_strike * normal_cdf(-d2)
        rho = -years * discounted_strike * normal_cdf(-d2)
    else:
        raise ValueError("option_type must be 'call' or 'put'")
    return BlackScholesGreeks(price=float(price), delta=float(delta), gamma=float(gamma), vega=float(vega), theta=float(theta), rho=float(rho))


def crr_binomial_price(
    *,
    spot: float,
    strike: float,
    rate: float,
    volatility: float,
    years: float,
    steps: int = 100,
    option_type: OptionType = "call",
    american: bool = False,
) -> float:
    """Cox-Ross-Rubinstein binomial option price."""
    _validate_inputs(spot, strike, rate, volatility, years)
    if steps <= 0:
        raise ValueError("steps must be positive")
    dt = years / steps
    up = exp(volatility * sqrt(dt))
    down = 1.0 / up
    growth = exp(rate * dt)
    q = (growth - down) / (up - down)
    if not 0 < q < 1:
        raise ValueError("risk-neutral probability outside (0, 1); increase steps or check inputs")
    discount = exp(-rate * dt)

    values: list[float] = []
    for i in range(steps + 1):
        terminal_spot = spot * (up ** (steps - i)) * (down**i)
        if option_type == "call":
            values.append(max(terminal_spot - strike, 0.0))
        elif option_type == "put":
            values.append(max(strike - terminal_spot, 0.0))
        else:
            raise ValueError("option_type must be 'call' or 'put'")

    for step in range(steps - 1, -1, -1):
        next_values: list[float] = []
        for i in range(step + 1):
            continuation = discount * (q * values[i] + (1.0 - q) * values[i + 1])
            if american:
                node_spot = spot * (up ** (step - i)) * (down**i)
                exercise = max(node_spot - strike, 0.0) if option_type == "call" else max(strike - node_spot, 0.0)
                continuation = max(continuation, exercise)
            next_values.append(continuation)
        values = next_values
    return float(values[0])
