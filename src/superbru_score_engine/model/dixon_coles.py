from __future__ import annotations

import numpy as np


def apply_dixon_coles(matrix: np.ndarray, lambda_home: float, lambda_away: float, rho: float) -> np.ndarray:
    adjusted = matrix.copy()
    factors = {
        (0, 0): 1.0 - lambda_home * lambda_away * rho,
        (0, 1): 1.0 + lambda_home * rho,
        (1, 0): 1.0 + lambda_away * rho,
        (1, 1): 1.0 - rho,
    }
    for (home_goals, away_goals), factor in factors.items():
        if home_goals < adjusted.shape[0] and away_goals < adjusted.shape[1]:
            adjusted[home_goals, away_goals] *= max(0.001, factor)
    return adjusted / adjusted.sum()


def geometric_blend(base: np.ndarray, market: np.ndarray, market_weight: float) -> np.ndarray:
    if market.shape != base.shape:
        raise ValueError("Cannot blend scoreline matrices with different shapes")
    weight = min(1.0, max(0.0, market_weight))
    epsilon = 1e-12
    blended = np.exp((1.0 - weight) * np.log(base + epsilon) + weight * np.log(market + epsilon))
    return blended / blended.sum()

