"""Resampling-based inference for backtests: confidence intervals and significance.

Point estimates such as ``avg_model_points`` or ``totals_delta_vs_h2h`` say nothing
about sampling error - on a few hundred matches a 0.02 points/match gap is well within
noise. These helpers attach distribution-free bootstrap confidence intervals and a
two-sided bootstrap p-value so a backtest reports *signal vs noise*, the way an
actuarial model review would require before acting on a result.
"""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np

DEFAULT_BOOTSTRAP = 10000
DEFAULT_ALPHA = 0.05
DEFAULT_SEED = 20260616


def bootstrap_mean_ci(
    values: Sequence[float],
    n_boot: int = DEFAULT_BOOTSTRAP,
    alpha: float = DEFAULT_ALPHA,
    seed: int = DEFAULT_SEED,
) -> dict[str, object]:
    """Percentile bootstrap confidence interval for the mean of ``values``."""
    sample = np.asarray(values, dtype=float)
    n = int(sample.size)
    if n == 0:
        return {"mean": None, "ci_low": None, "ci_high": None, "n": 0, "n_boot": int(n_boot), "alpha": float(alpha)}
    if n == 1:
        only = float(sample[0])
        return {"mean": only, "ci_low": only, "ci_high": only, "n": 1, "n_boot": int(n_boot), "alpha": float(alpha)}
    rng = np.random.default_rng(seed)
    boot_means = sample[rng.integers(0, n, size=(int(n_boot), n))].mean(axis=1)
    low, high = np.quantile(boot_means, [alpha / 2.0, 1.0 - alpha / 2.0])
    return {
        "mean": float(sample.mean()),
        "ci_low": float(low),
        "ci_high": float(high),
        "n": n,
        "n_boot": int(n_boot),
        "alpha": float(alpha),
    }


def paired_bootstrap_delta(
    values_a: Sequence[float],
    values_b: Sequence[float],
    n_boot: int = DEFAULT_BOOTSTRAP,
    alpha: float = DEFAULT_ALPHA,
    seed: int = DEFAULT_SEED,
) -> dict[str, object]:
    """Paired bootstrap on the per-item difference ``a - b``.

    Returns the mean paired delta, its percentile CI, a two-sided bootstrap p-value for
    H0: mean delta = 0, and whether the delta is significant at ``alpha``. Pairing keeps
    the comparison on the same matches, removing match-difficulty variance.
    """
    a = np.asarray(values_a, dtype=float)
    b = np.asarray(values_b, dtype=float)
    if a.shape != b.shape:
        raise ValueError(f"paired_bootstrap_delta needs aligned series, got {a.shape} vs {b.shape}")
    deltas = a - b
    n = int(deltas.size)
    if n == 0:
        return {
            "mean_delta": None, "ci_low": None, "ci_high": None, "p_value": None,
            "significant": None, "n": 0, "n_boot": int(n_boot), "alpha": float(alpha),
        }
    rng = np.random.default_rng(seed)
    boot = deltas[rng.integers(0, n, size=(int(n_boot), n))].mean(axis=1)
    low, high = np.quantile(boot, [alpha / 2.0, 1.0 - alpha / 2.0])
    # Two-sided bootstrap p-value: twice the smaller tail mass on either side of zero.
    p_value = min(1.0, 2.0 * float(min((boot <= 0.0).mean(), (boot >= 0.0).mean())))
    return {
        "mean_delta": float(deltas.mean()),
        "ci_low": float(low),
        "ci_high": float(high),
        "p_value": p_value,
        "significant": bool(p_value < alpha),
        "n": n,
        "n_boot": int(n_boot),
        "alpha": float(alpha),
    }
