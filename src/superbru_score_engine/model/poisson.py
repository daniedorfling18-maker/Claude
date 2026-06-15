from __future__ import annotations

import math

import numpy as np

try:
    from scipy.optimize import minimize
    from scipy.stats import poisson
except ImportError:  # pragma: no cover - used in minimal runtimes.
    minimize = None
    poisson = None

from .devig import FairOutcomeMarket, FairTotalMarket


def independent_poisson_matrix(lambda_home: float, lambda_away: float, max_goals: int) -> np.ndarray:
    goals = np.arange(max_goals + 1)
    home = poisson_pmf(goals, lambda_home)
    away = poisson_pmf(goals, lambda_away)
    matrix = np.outer(home, away)
    return matrix / matrix.sum()


def outcome_probabilities(matrix: np.ndarray) -> np.ndarray:
    home = float(np.tril(matrix, k=-1).sum())
    draw = float(np.trace(matrix))
    away = float(np.triu(matrix, k=1).sum())
    total = home + draw + away
    return np.array([home, draw, away], dtype=float) / total


def over_probability(total_lambda: float, line: float) -> float:
    threshold = math.floor(line) + 1 if not float(line).is_integer() else int(line) + 1
    return float(1.0 - poisson_cdf(threshold - 1, total_lambda))


def solve_lambdas(
    fair_1x2: FairOutcomeMarket,
    fair_total: FairTotalMarket | None,
    solver_grid_goals: int,
    initial_total_goals: float = 2.55,
) -> tuple[float, float, dict[str, float]]:
    target_outcomes = fair_1x2.as_array()

    if fair_total:
        initial_total_goals = _solve_total_from_over(fair_total.line, fair_total.over, initial_total_goals)

    home_share = max(0.15, min(0.85, fair_1x2.home + 0.5 * fair_1x2.draw))
    initial_home = max(0.05, initial_total_goals * home_share)
    initial_away = max(0.05, initial_total_goals - initial_home)

    def objective(log_rates: np.ndarray) -> float:
        home_rate = float(np.exp(log_rates[0]))
        away_rate = float(np.exp(log_rates[1]))
        matrix = independent_poisson_matrix(home_rate, away_rate, solver_grid_goals)
        model_outcomes = outcome_probabilities(matrix)
        loss = float(np.square(model_outcomes - target_outcomes).sum() * 8.0)
        if fair_total:
            model_over = over_probability(home_rate + away_rate, fair_total.line)
            loss += float((model_over - fair_total.over) ** 2 * 4.0)
        else:
            loss += float(((home_rate + away_rate) - initial_total_goals) ** 2 * 0.15)
        return loss

    x0 = np.log([initial_home, initial_away])
    if minimize is not None:
        result = minimize(
            objective,
            x0=x0,
            method="Nelder-Mead",
            options={"maxiter": 600, "xatol": 1e-8, "fatol": 1e-8},
        )
        x = result.x
        success = bool(result.success)
        loss = float(result.fun)
    else:
        x, loss = _coordinate_descent(objective, x0)
        success = True
    rates = np.exp(x)
    diagnostics = {
        "solver_loss": loss,
        "solver_success": float(success),
        "lambda_home_raw": float(rates[0]),
        "lambda_away_raw": float(rates[1]),
    }
    return float(rates[0]), float(rates[1]), diagnostics


def _solve_total_from_over(line: float, target_over: float, fallback: float) -> float:
    if minimize is None:
        low, high = 0.05, 7.0
        for _ in range(80):
            mid = (low + high) / 2.0
            if over_probability(mid, line) < target_over:
                low = mid
            else:
                high = mid
        return (low + high) / 2.0

    def objective(log_mu: np.ndarray) -> float:
        mu = float(np.exp(log_mu[0]))
        return float((over_probability(mu, line) - target_over) ** 2)

    result = minimize(
        objective,
        x0=np.log([fallback]),
        method="Nelder-Mead",
        options={"maxiter": 200, "xatol": 1e-8, "fatol": 1e-8},
    )
    if result.success:
        return float(np.exp(result.x[0]))
    return fallback


def poisson_pmf(goals: np.ndarray, rate: float) -> np.ndarray:
    if poisson is not None:
        return poisson.pmf(goals, rate)
    values = np.zeros_like(goals, dtype=float)
    values[0] = math.exp(-rate)
    for idx in range(1, len(goals)):
        values[idx] = values[idx - 1] * rate / idx
    return values


def poisson_cdf(goal: int, rate: float) -> float:
    if poisson is not None:
        return float(poisson.cdf(goal, rate))
    if goal < 0:
        return 0.0
    pmf = math.exp(-rate)
    total = pmf
    for idx in range(1, goal + 1):
        pmf *= rate / idx
        total += pmf
    return float(total)


def _coordinate_descent(objective, x0: np.ndarray) -> tuple[np.ndarray, float]:
    x = np.array(x0, dtype=float)
    best = objective(x)
    step = 0.35
    while step > 1e-5:
        improved = False
        for dx in (-step, 0.0, step):
            for dy in (-step, 0.0, step):
                if dx == 0.0 and dy == 0.0:
                    continue
                candidate = x + np.array([dx, dy])
                loss = objective(candidate)
                if loss + 1e-12 < best:
                    x = candidate
                    best = loss
                    improved = True
        if not improved:
            step *= 0.5
    return x, float(best)
