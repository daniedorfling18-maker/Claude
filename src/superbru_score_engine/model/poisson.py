from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

try:
    from scipy.optimize import minimize
    from scipy.stats import poisson
except ImportError:  # pragma: no cover - used in minimal runtimes.
    minimize = None
    poisson = None

from .devig import FairOutcomeMarket, FairTotalMarket
from .dixon_coles import apply_dixon_coles


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


def matrix_over_probability(matrix: np.ndarray, line: float) -> float:
    threshold = math.floor(line) + 1 if not float(line).is_integer() else int(line) + 1
    home_goals = np.arange(matrix.shape[0])[:, None]
    away_goals = np.arange(matrix.shape[1])[None, :]
    return float(matrix[(home_goals + away_goals) >= threshold].sum())


def solve_lambdas(
    fair_1x2: FairOutcomeMarket,
    fair_total: FairTotalMarket | None,
    solver_grid_goals: int,
    initial_total_goals: float = 2.55,
    dixon_coles_rho: float = 0.0,
    fair_totals: Sequence[FairTotalMarket] | None = None,
) -> tuple[float, float, dict[str, object]]:
    target_outcomes = fair_1x2.as_array()
    rho = float(dixon_coles_rho or 0.0)

    # Working set of over/under lines: prefer the explicit multi-line set, fall back to
    # the single primary line, else fit only the 1X2 with a soft total-goals prior.
    lines: list[FairTotalMarket] = [market for market in (fair_totals or []) if market is not None]
    if not lines and fair_total is not None:
        lines = [fair_total]

    # Seed the search from the most-reliable line (most books, then closest to 2.5).
    primary = None
    if lines:
        primary = sorted(lines, key=lambda market: (-market.count, abs(market.line - 2.5), market.line))[0]
        initial_total_goals = _solve_total_from_over(primary.line, primary.over, initial_total_goals)

    # Spread a fixed totals-fit budget across the lines by book count, so adding lines
    # sharpens the goal distribution without ever swamping the 1X2 fit. A single line
    # therefore reproduces the original 4.0 weight exactly.
    totals_budget = 4.0
    count_sum = float(sum(max(1, market.count) for market in lines))
    line_weights = (
        [(market, totals_budget * max(1, market.count) / count_sum) for market in lines]
        if count_sum > 0
        else []
    )

    home_share = max(0.15, min(0.85, fair_1x2.home + 0.5 * fair_1x2.draw))
    initial_home = max(0.05, initial_total_goals * home_share)
    initial_away = max(0.05, initial_total_goals - initial_home)

    def objective(log_rates: np.ndarray) -> float:
        home_rate = float(np.exp(log_rates[0]))
        away_rate = float(np.exp(log_rates[1]))
        matrix = independent_poisson_matrix(home_rate, away_rate, solver_grid_goals)
        if abs(rho) > 1e-12:
            matrix = apply_dixon_coles(matrix, home_rate, away_rate, rho)
        model_outcomes = outcome_probabilities(matrix)
        loss = float(np.square(model_outcomes - target_outcomes).sum() * 8.0)
        if line_weights:
            for market, weight in line_weights:
                model_over = matrix_over_probability(matrix, market.line)
                loss += float((model_over - market.over) ** 2 * weight)
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

    # Report how well every line is simultaneously matched at the solution.
    over_rmse: float | None = None
    if lines:
        fitted = independent_poisson_matrix(float(rates[0]), float(rates[1]), solver_grid_goals)
        if abs(rho) > 1e-12:
            fitted = apply_dixon_coles(fitted, float(rates[0]), float(rates[1]), rho)
        errors = [matrix_over_probability(fitted, market.line) - market.over for market in lines]
        over_rmse = float(np.sqrt(np.mean(np.square(errors))))

    diagnostics: dict[str, object] = {
        "solver_loss": loss,
        "solver_success": float(success),
        "lambda_home_raw": float(rates[0]),
        "lambda_away_raw": float(rates[1]),
        "solver_dixon_coles_rho": rho,
        "solver_fitted_post_dixon_coles": abs(rho) > 1e-12,
        "solver_total_lines_used": len(lines),
        "solver_total_lines": ",".join(f"{market.line:g}" for market in sorted(lines, key=lambda m: m.line)),
        "solver_total_over_rmse": over_rmse,
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
