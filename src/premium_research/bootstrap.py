"""Cluster and stationary block bootstraps for the WO-166 estimators.

Both functions are pure: a fixed seed gives byte-identical output. Any
non-finite input, or fewer than two clusters, yields NaN intervals, which the
gate code treats as a failed gate (fail-closed), never as a pass.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

import numpy as np

DEFAULT_DRAWS = 10_000
DEFAULT_SEED = 20260912
DEFAULT_LEVELS = (0.90, 0.95)
DEFAULT_EXPECTED_BLOCK = 4


def block_length_for(n_clusters: int) -> int:
    """Expected block length for the stationary bootstrap: ``round(n ** (1/3))``, at least 2.

    The cube-root rule of thumb (Hall, Horowitz and Jing 1995; Politis and Romano
    1994) gives 7 for 347 weekly units and 4 for 66 monthly windows.
    """
    if n_clusters <= 0:
        return 2
    return max(2, int(round(n_clusters ** (1.0 / 3.0))))


def _clean(values: Iterable[float]) -> np.ndarray:
    array = np.asarray(list(values), dtype=float)
    return array


def _nan_result(point: float, n: int, *, method: str, n_draws: int, seed: int, levels: tuple[float, ...]) -> dict[str, object]:
    return {
        "method": method,
        "point": point,
        "n_clusters": int(n),
        "n_draws": int(n_draws),
        "seed": int(seed),
        "intervals": {f"{level:.2f}": [float("nan"), float("nan")] for level in levels},
        "finite": False,
    }


def _summarise(samples: np.ndarray, point: float, n: int, *, method: str, n_draws: int, seed: int, levels: tuple[float, ...]) -> dict[str, object]:
    intervals: dict[str, list[float]] = {}
    finite = bool(np.isfinite(samples).all()) and math.isfinite(point)
    for level in levels:
        alpha = (1.0 - level) / 2.0
        if finite:
            lo, hi = np.quantile(samples, [alpha, 1.0 - alpha], method="linear")
            intervals[f"{level:.2f}"] = [float(lo), float(hi)]
        else:
            intervals[f"{level:.2f}"] = [float("nan"), float("nan")]
    return {
        "method": method,
        "point": float(point),
        "n_clusters": int(n),
        "n_draws": int(n_draws),
        "seed": int(seed),
        "intervals": intervals,
        "finite": finite,
    }


def cluster_bootstrap_mean(
    values: Iterable[float],
    *,
    n_draws: int = DEFAULT_DRAWS,
    seed: int = DEFAULT_SEED,
    levels: tuple[float, ...] = DEFAULT_LEVELS,
) -> dict[str, object]:
    """Percentile bootstrap of the mean, resampling whole clusters (one value per cluster) with replacement."""
    array = _clean(values)
    n = int(array.size)
    point = float(array.mean()) if n else float("nan")
    if n < 2 or not np.isfinite(array).all():
        return _nan_result(point, n, method="cluster", n_draws=n_draws, seed=seed, levels=levels)
    rng = np.random.default_rng(seed)
    index = rng.integers(0, n, size=(int(n_draws), n))
    samples = array[index].mean(axis=1)
    return _summarise(samples, point, n, method="cluster", n_draws=n_draws, seed=seed, levels=levels)


def stationary_block_bootstrap_mean(
    values: Iterable[float],
    *,
    expected_block: int = DEFAULT_EXPECTED_BLOCK,
    n_draws: int = DEFAULT_DRAWS,
    seed: int = DEFAULT_SEED,
    levels: tuple[float, ...] = DEFAULT_LEVELS,
) -> dict[str, object]:
    """Politis-Romano stationary bootstrap of the mean with geometric block lengths (mean ``expected_block``)."""
    array = _clean(values)
    n = int(array.size)
    point = float(array.mean()) if n else float("nan")
    if n < 2 or not np.isfinite(array).all() or expected_block < 1:
        return _nan_result(point, n, method="stationary_block", n_draws=n_draws, seed=seed, levels=levels)
    rng = np.random.default_rng(seed)
    p_restart = 1.0 / float(expected_block)
    draws = int(n_draws)
    starts = rng.integers(0, n, size=(draws, n))
    restart = rng.random(size=(draws, n)) < p_restart
    restart[:, 0] = True
    index = np.empty((draws, n), dtype=np.int64)
    index[:, 0] = starts[:, 0]
    for t in range(1, n):
        continued = (index[:, t - 1] + 1) % n
        index[:, t] = np.where(restart[:, t], starts[:, t], continued)
    samples = array[index].mean(axis=1)
    return _summarise(samples, point, n, method="stationary_block", n_draws=n_draws, seed=seed, levels=levels)


def lower_bound(result: dict[str, object], level: float) -> float:
    """The lower end of the requested interval, NaN when the result is not finite."""
    intervals = result.get("intervals")
    if not isinstance(intervals, dict):
        return float("nan")
    pair = intervals.get(f"{level:.2f}")
    if not pair or len(pair) != 2:
        return float("nan")
    value = float(pair[0])
    return value if math.isfinite(value) else float("nan")
