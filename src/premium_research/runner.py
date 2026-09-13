"""WO-166 runner: load the committed inputs, compute both lanes, apply the registered gates, write results atomically.

Everything that could vary between runs (clock, git revision) is passed in and
stored in the results, so ``verify_results`` can recompute with the stored
values and byte-compare. The verdict line in the report is generated from the
gate booleans, never typed. Every gate comparison with a missing or
non-finite operand reads ``False``.
"""

from __future__ import annotations

import gzip
import io
import json
import math
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import hashlib
import io
import math as _math

import numpy as np
import pandas as pd
from quant_lab.risk import conditional_var, max_drawdown_from_returns

from . import carry, vrp
from .bootstrap import DEFAULT_DRAWS, DEFAULT_SEED, block_length_for, cluster_bootstrap_mean, lower_bound, stationary_block_bootstrap_mean
from .manifest import load_manifest, sha256_path, verify_manifest
from .report import render_report

RESULT_FILES = ("carry_v0.json", "carry_v1.json", "vrp.json", "report.md")
WORK_ORDER = "WO-166"

# WO-170: the drawdown basis G3 reads. "compounded_weekly" is WO-166's registered definition (the weekly-increment curve
# compounded by quant_lab.risk); "nav" is the ledger's own NAV path over every boundary.
DRAWDOWN_BASES = ("compounded_weekly", "nav")
RETURN_BASIS = "simple_on_inception_capital"
RECENT_CUTS = (52, 104)  # one and two years at WEEKS_PER_YEAR = 52
ROLLING_WEEKS = 52
RECONCILIATION_AGAINST = "results_wo167"
LEDGER_COLUMNS = (
    "boundary_ms", "boundary_iso", "position_open", "marks_carried_forward", "spot_qty", "spot_mark", "perp_mark", "spot_value",
    "margin", "cash", "nav", "funding_received", "fees_paid", "traded_notional", "period_return_on_capital", "period_return_on_nav",
    "flagged", "rebalances", "forced_liquidations", "unverifiable_open", "rejected_open",
)
BASES = {
    "mark_price": "Binance 1h kline close of the hour ending at the boundary (last traded price), not the venue mark price",
    "execution_price": "the same close plus taker fees (spot 10 bps, perpetual 5 bps); no spread, no slippage, no market impact — a favourable channel, covered only by the declared haircut",
    "funding_notional": "position size × that close; the venue settles on mark-price notional (WO-166 A8 bound: ≤ 1 × 10⁻⁵ of notional per period, direction indeterminate)",
    "liquidation_check": "kline high of the last traded price against the period-start margin ratio; the venue liquidates on the mark price, which is smoothed, so the last-price high triggers at least as often — a conservative channel",
    "collateral": "margin 0.5 × notional in USDT; cash and spot earn zero; no cross-margin netting — an unfavourable channel",
    "haircut": "2.0 pp per year, a declared assumption; it is not a measured bound on venue, stablecoin-depeg or liquidation risk and this WO measures none of them",
}

# Registered spans and universe (WO-166): the single implementation site; cli.py imports them.
SYMBOLS = ("BTCUSDT", "ETHUSDT")
CURRENCIES = ("BTC", "ETH")
DERIBIT_INSTRUMENTS = {"BTC": "BTC-PERPETUAL", "ETH": "ETH-PERPETUAL"}
BINANCE_START_MONTH = "2020-01"
BINANCE_END_MONTH = "2026-08"
LANE_A_START_MS = 1_577_836_800_000  # 2020-01-01T00:00:00Z
DERIBIT_FUNDING_START_MS = 1_569_888_000_000  # 2019-10-01T00:00:00Z
LANE_B_START_MS = 1_616_544_000_000  # 2021-03-24T00:00:00Z, the first DVOL candle
SPAN_END_MS = 1_788_220_800_000  # 2026-09-01T00:00:00Z, exclusive end of 2026-08-31

HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS


@dataclass(frozen=True)
class Config:
    """Registered spans and thresholds. Defaults are WO-166's literals; tests pass smaller spans."""

    symbols: tuple[str, ...] = SYMBOLS
    currencies: tuple[str, ...] = CURRENCIES
    lane_a_start_ms: int = LANE_A_START_MS  # entry at the first Monday boundary on/after
    lane_a_end_ms: int = SPAN_END_MS  # exit at the last Monday boundary on/before the last usable boundary
    lane_b_start_ms: int = LANE_B_START_MS
    lane_b_end_ms: int = SPAN_END_MS
    complete_years_a: tuple[int, ...] = (2020, 2021, 2022, 2023, 2024, 2025)  # ISO years
    complete_years_b: tuple[int, ...] = (2022, 2023, 2024, 2025)  # calendar years of the window start
    min_eligible_weeks_per_year: int = 45  # an ISO year with fewer eligible weeks does not count as positive
    min_windows_per_year: int = 10  # a calendar year with fewer accepted windows does not count as positive
    haircut_per_year: float = 0.020
    g2_hurdle: float = 0.060
    g3_max_drawdown: float = 0.20
    g4_min_positive_years: int = 4
    g5_min_positive_years: int = 3
    gate_quantile: float = 0.025  # lower end of the two-sided 95% interval; one-sided 2.5% per lane, family <= 5%
    report_quantile: float = 0.05
    n_draws: int = DEFAULT_DRAWS
    seed: int = DEFAULT_SEED
    sma_days: int = 200
    # WO-167: which absent bars make an open period's liquidation status unverifiable.
    # "either" is WO-166's registered rule (any leg) and stays the default so its committed results verify.
    unverifiable_scope: str = "either"
    work_order: str = WORK_ORDER
    results_dir: str = "results"
    # WO-170: the drawdown basis G3 reads, the realised-variance window alignment, and the files a pass writes and verifies.
    # WO-166's values stay the defaults so its and WO-167's committed results verify byte-for-byte.
    drawdown_basis: str = "compounded_weekly"
    rv_alignment: str = "open_time_in_window"
    result_files: tuple[str, ...] = RESULT_FILES

    @property
    def gate_level(self) -> float:
        return 1.0 - 2.0 * self.gate_quantile

    @property
    def discloses_scope(self) -> bool:
        """True for every configuration other than WO-166's own: the results JSON then names the scope and the rejected-open count."""
        return self.work_order != WORK_ORDER or self.unverifiable_scope != "either"

    @property
    def discloses_bases(self) -> bool:
        """True only when a WO-170 switch is off its WO-166 default: every WO-170 addition to a JSON or report is keyed on this, never on discloses_scope."""
        return self.drawdown_basis != "compounded_weekly" or self.rv_alignment != "open_time_in_window"

    def __post_init__(self) -> None:
        if self.unverifiable_scope not in carry.UNVERIFIABLE_SCOPES:
            raise ValueError(f"unknown unverifiable_scope {self.unverifiable_scope!r}")
        if self.drawdown_basis not in DRAWDOWN_BASES:
            raise ValueError(f"unknown drawdown_basis {self.drawdown_basis!r}; registered values are {DRAWDOWN_BASES}")
        if self.rv_alignment not in vrp.RV_ALIGNMENTS:
            raise ValueError(f"unknown rv_alignment {self.rv_alignment!r}; registered values are {vrp.RV_ALIGNMENTS}")

    @property
    def report_level(self) -> float:
        return 1.0 - 2.0 * self.report_quantile


# --------------------------------------------------------------------------- inputs


def _read_csv(path: Path) -> pd.DataFrame:
    if path.suffix == ".gz":
        with gzip.open(path, "rb") as handle:
            return pd.read_csv(io.BytesIO(handle.read()))
    return pd.read_csv(path)


def _find(root: Path, relative: str) -> Path:
    plain = root / relative
    if plain.is_file():
        return plain
    gz = Path(str(plain) + ".gz")
    if gz.is_file():
        return gz
    raise FileNotFoundError(f"committed input missing: {relative}")


def _validate_series(frame: pd.DataFrame, column: str, *, label: str, grid_ms: int | None = None) -> None:
    """Committed inputs are re-checked at run time: unique, sorted timestamps; a gap-free grid when one is registered."""
    if frame.empty or column not in frame.columns:
        raise RuntimeError(f"{label}: empty or missing column {column}")
    values = frame[column].to_numpy(dtype=np.int64)
    if len(set(values.tolist())) != len(values):
        raise RuntimeError(f"{label}: duplicated {column} values")
    if not np.all(np.diff(values) > 0):
        raise RuntimeError(f"{label}: {column} is not strictly increasing")
    if grid_ms is not None:
        if np.any(values % grid_ms != 0):
            raise RuntimeError(f"{label}: {column} off the {grid_ms} ms grid")
        expected = (int(values[-1]) - int(values[0])) // grid_ms + 1
        if expected != len(values):
            raise RuntimeError(f"{label}: {expected - len(values)} missing grid points")
    for name in frame.columns:
        if name.endswith("_iso") or name == column or name == "calc_time_raw":
            continue
        numeric = pd.to_numeric(frame[name], errors="coerce")
        if not np.isfinite(numeric.to_numpy(dtype=float)).all():
            raise RuntimeError(f"{label}: non-finite or non-numeric value in {name}")


def load_inputs(root: Path, config: Config) -> dict[str, Any]:
    failures = verify_manifest(root / "manifest.json", root)
    if failures:
        raise RuntimeError("manifest verification failed: " + "; ".join(failures[:5]))
    manifest = load_manifest(root / "manifest.json")
    inputs: dict[str, Any] = {"manifest_sha256": sha256_path(root / "manifest.json"), "files": {}}
    for entry in manifest["entries"]:
        inputs["files"][entry["path"]] = entry["sha256"]
    for symbol in config.symbols:
        inputs[f"{symbol}_funding"] = _read_csv(_find(root, f"data/binance/{symbol}_funding_8h.csv"))
        inputs[f"{symbol}_perp"] = _read_csv(_find(root, f"data/binance/{symbol}_perp_1h.csv"))
        inputs[f"{symbol}_spot"] = _read_csv(_find(root, f"data/binance/{symbol}_spot_1h.csv"))
    for currency in config.currencies:
        inputs[f"{currency}_deribit_funding"] = _read_csv(_find(root, f"data/deribit/{currency}_funding_1h.csv"))
        inputs[f"{currency}_dvol"] = _read_csv(_find(root, f"data/deribit/{currency}_dvol_daily.csv"))
    for symbol in config.symbols:
        _validate_series(inputs[f"{symbol}_funding"], "calc_time", label=f"{symbol} funding", grid_ms=carry.PERIOD_MS)
        _validate_series(inputs[f"{symbol}_perp"], "open_time", label=f"{symbol} perp 1h")
        _validate_series(inputs[f"{symbol}_spot"], "open_time", label=f"{symbol} spot 1h")
    for currency in config.currencies:
        _validate_series(inputs[f"{currency}_deribit_funding"], "timestamp", label=f"{currency} deribit funding")
        _validate_series(inputs[f"{currency}_dvol"], "timestamp", label=f"{currency} dvol")
    return inputs


# --------------------------------------------------------------------------- helpers


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _sharpe(weekly: pd.Series) -> float:
    if len(weekly) < 2 or float(weekly.std(ddof=1)) == 0.0:
        return float("nan")
    return float(weekly.mean() / weekly.std(ddof=1) * math.sqrt(carry.WEEKS_PER_YEAR))


def _max_drawdown_nav(nav: np.ndarray) -> float:
    """WO-170: ``min_t (nav_t / max_{s<=t} nav_s - 1)`` over every boundary; NaN if any NAV is non-finite (G3 then reads False)."""
    values = np.asarray(nav, dtype=float)
    if values.size == 0 or not np.isfinite(values).all() or (values <= 0).any():
        return float("nan")
    peak = np.maximum.accumulate(values)
    return float(np.min(values / peak - 1.0))


def _cagr(nav_start: float, nav_end: float, years: float) -> float:
    if not (_finite(nav_start) and _finite(nav_end)) or nav_start <= 0 or nav_end <= 0 or years <= 0:
        return float("nan")
    return float((nav_end / nav_start) ** (1.0 / years) - 1.0)


def _yearly_sums(weekly: pd.DataFrame, value: str) -> dict[str, float]:
    """Per-ISO-year sums over ELIGIBLE weeks only; ineligible weeks feed nothing but the drawdown."""
    eligible = weekly[weekly["eligible"]]
    return {str(int(year)): float(total) for year, total in eligible.groupby("iso_year")[value].sum().items()}


def _yearly_eligible_counts(weekly: pd.DataFrame) -> dict[str, int]:
    eligible = weekly[weekly["eligible"]]
    return {str(int(year)): int(count) for year, count in eligible.groupby("iso_year").size().items()}


def _two_bootstraps(values: np.ndarray, config: Config) -> dict[str, Any]:
    """Cluster and stationary-block bootstraps of the mean; the gate reads the smaller lower bound. Block length: round(n^(1/3)), minimum 2."""
    levels = (config.report_level, config.gate_level)
    block_len = block_length_for(int(len(values)))
    cluster = cluster_bootstrap_mean(values, n_draws=config.n_draws, seed=config.seed, levels=levels)
    block = stationary_block_bootstrap_mean(values, expected_block=block_len, n_draws=config.n_draws, seed=config.seed, levels=levels)
    lb_cluster = lower_bound(cluster, config.gate_level)
    lb_block = lower_bound(block, config.gate_level)
    lb_min = min(lb_cluster, lb_block) if (_finite(lb_cluster) and _finite(lb_block)) else float("nan")
    return {
        "cluster": cluster,
        "stationary_block": block,
        "lower_bound_gate": {"cluster": lb_cluster, "stationary_block": lb_block, "minimum": lb_min, "quantile": config.gate_quantile, "level": config.gate_level, "block_length": block_len},
    }


def _positive_qualifying_years(yearly_values: dict[str, float], yearly_counts: dict[str, int], years: tuple[int, ...], minimum: int) -> dict[str, Any]:
    detail: dict[str, dict[str, Any]] = {}
    positive = 0
    for year in years:
        key = str(year)
        count = int(yearly_counts.get(key, 0))
        value = yearly_values.get(key, float("nan"))
        qualifies = count >= minimum and _finite(value)
        is_positive = bool(qualifies and float(value) > 0.0)
        positive += int(is_positive)
        detail[key] = {"units": count, "value": float(value) if _finite(value) else float("nan"), "qualifies": bool(qualifies), "positive": is_positive}
    return {"positive_years": positive, "detail": detail}


# --------------------------------------------------------------------------- lane A


def _sma_regime(btc_spot: pd.DataFrame, weekly: pd.DataFrame, *, sma_days: int) -> pd.Series:
    """'above' / 'below' / 'unknown' per week from the BTC spot close at the week's start against its trailing SMA."""
    daily = btc_spot[(btc_spot["open_time"] % DAY_MS) == (DAY_MS - HOUR_MS)][["open_time", "close"]].copy()
    daily["day_ms"] = daily["open_time"] + HOUR_MS
    daily = daily.sort_values("day_ms").reset_index(drop=True)
    daily["sma"] = daily["close"].rolling(sma_days, min_periods=sma_days).mean()
    lookup_close = dict(zip(daily["day_ms"].astype(np.int64), daily["close"].astype(float)))
    lookup_sma = dict(zip(daily["day_ms"].astype(np.int64), daily["sma"].astype(float)))
    regimes: list[str] = []
    for week_end in weekly["week_end_ms"].astype(np.int64):
        week_start = int(week_end) - 7 * DAY_MS
        close = lookup_close.get(week_start, float("nan"))
        sma = lookup_sma.get(week_start, float("nan"))
        regimes.append("unknown" if not (math.isfinite(close) and math.isfinite(sma)) else ("above" if close >= sma else "below"))
    return pd.Series(regimes, index=weekly.index)


def _asset_summary(weekly: pd.DataFrame, frame: pd.DataFrame, *, years: float, disclose_scope: bool = False, disclose_bases: bool = False) -> dict[str, Any]:
    eligible = weekly[weekly["eligible"]]
    extra = {"rejected_open_periods": int(frame["rejected_open"].sum())} if disclose_scope else {}
    if disclose_bases:
        nav = frame["nav"].to_numpy(dtype=float)
        extra["max_drawdown_nav"] = _max_drawdown_nav(nav)
        extra["cagr_nav"] = _cagr(float(nav[0]), float(nav[-1]), years)
    return {
        **extra,
        "weeks_total": int(len(weekly)),
        "eligible_weeks": int(len(eligible)),
        "dropped_incomplete_weeks": int((weekly["periods"] != carry.PERIODS_PER_WEEK).sum()),
        "dropped_flagged_weeks": int((weekly["flagged"] & (weekly["periods"] == carry.PERIODS_PER_WEEK)).sum()),
        "mean_weekly_return_on_capital": float(eligible["return_on_capital"].mean()) if len(eligible) else float("nan"),
        "annualised_return_on_capital": float(eligible["return_on_capital"].mean() * carry.WEEKS_PER_YEAR) if len(eligible) else float("nan"),
        "sharpe_weekly_annualised": _sharpe(eligible["return_on_capital"]) if len(eligible) else float("nan"),
        "max_drawdown_all_weeks": float(max_drawdown_from_returns(weekly["return_on_capital"])) if len(weekly) else float("nan"),
        "cvar_95_weekly": float(conditional_var(eligible["return_on_capital"])) if len(eligible) > 1 else float("nan"),
        "funding_received_total": float(frame["funding_received"].sum()),
        "fees_paid_total": float(frame["fees_paid"].sum()),
        "turnover_notional_per_year": float(frame["traded_notional"].sum() / carry.CAPITAL_PER_NOTIONAL / max(years, 1e-9)),
        "rebalances": int(frame["rebalances"].sum()),
        "forced_liquidations": int(frame["forced_liquidations"].sum()),
        "unverifiable_open_periods": int(frame["unverifiable_open"].sum()),
        "share_of_weeks_with_mostly_negative_funding": float((weekly["negative_funding_periods"] > carry.PERIODS_PER_WEEK / 2).mean()) if len(weekly) else float("nan"),
        "final_wealth_per_1_5_capital": float(frame["wealth"].iloc[-1]),
        "yearly_return_on_capital": _yearly_sums(weekly, "return_on_capital"),
        "yearly_eligible_weeks": _yearly_eligible_counts(weekly),
    }


def _pooled_nav_frame(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """WO-170: the summed NAV of the assets at every boundary (capital 3.0 for two assets); every ledger must share the boundary grid."""
    names = list(frames)
    base = frames[names[0]][["boundary_ms"]].copy()
    total = np.zeros(len(base), dtype=float)
    for name in names:
        frame = frames[name]
        if len(frame) != len(base) or not np.array_equal(frame["boundary_ms"].to_numpy(dtype=np.int64), base["boundary_ms"].to_numpy(dtype=np.int64)):
            raise RuntimeError("the per-asset ledgers do not share one boundary grid; refuse to pool NAV")
        total = total + frame["nav"].to_numpy(dtype=float)
    base["nav_pooled"] = total
    keys = base["boundary_ms"].map(carry.iso_week_key)
    base["iso_year"] = keys.map(lambda key: key[0])
    base["iso_week"] = keys.map(lambda key: key[1])
    return base


def _pooled_weekly_return_on_nav(pooled_weekly: pd.DataFrame, nav_frame: pd.DataFrame) -> pd.Series:
    """WO-170: ``nav_end_pooled / nav_start_pooled - 1`` per ISO week on the summed NAV; the first week starts at the entry-row NAV."""
    nav_at = dict(zip(nav_frame["boundary_ms"].astype(np.int64), nav_frame["nav_pooled"].astype(float)))
    entry_nav = float(nav_frame["nav_pooled"].iloc[0])
    ordered = pooled_weekly.sort_values("week_end_ms")
    out: list[float] = []
    prev_end = entry_nav
    for week_end in ordered["week_end_ms"].astype(np.int64):
        nav_end = nav_at.get(int(week_end), float("nan"))
        out.append(float(nav_end / prev_end - 1.0) if _finite(nav_end) and _finite(prev_end) and prev_end > 0 else float("nan"))
        prev_end = nav_end
    return pd.Series(out, index=ordered.index).reindex(pooled_weekly.index)


def _recent_period(pooled_weekly: pd.DataFrame, nav_frame: pd.DataFrame, config: Config) -> dict[str, Any]:
    """WO-170: fixed-length trailing cuts and a rolling window over eligible weeks; descriptive, never gated."""
    eligible = pooled_weekly[pooled_weekly["eligible"]].sort_values("week_end_ms")
    nan = float("nan")
    out: dict[str, Any] = {"note": "retrospective diagnostic; not a prospective validation"}
    week_first_boundary: dict[tuple[int, int], int] = {}
    for key, boundary in zip(zip(nav_frame["iso_year"], nav_frame["iso_week"]), nav_frame["boundary_ms"].astype(np.int64)):
        week_first_boundary.setdefault((int(key[0]), int(key[1])), int(boundary))
    for length in RECENT_CUTS:
        name = f"last_{length}"
        if len(eligible) < length:
            out[name] = {"state": "insufficient_weeks", "weeks": int(len(eligible)), "mean_weekly_return_on_capital": nan, "annualised_simple": nan, "interval_90_week_cluster": [nan, nan], "sharpe_weekly_annualised": nan, "max_drawdown_nav": nan}
            continue
        tail = eligible.iloc[-length:]
        values = tail["return_on_capital"].to_numpy(dtype=float)
        boot = cluster_bootstrap_mean(values, n_draws=config.n_draws, seed=config.seed, levels=(config.report_level,))
        first_key = (int(tail.iloc[0]["iso_year"]), int(tail.iloc[0]["iso_week"]))
        start_ms = week_first_boundary.get(first_key)
        end_ms = int(tail.iloc[-1]["week_end_ms"])
        span = nav_frame[(nav_frame["boundary_ms"] >= (start_ms if start_ms is not None else end_ms + 1)) & (nav_frame["boundary_ms"] <= end_ms)]
        out[name] = {
            "state": "ok",
            "weeks": int(length),
            "first_week": f"{first_key[0]}-W{first_key[1]:02d}",
            "last_week": f"{int(tail.iloc[-1]['iso_year'])}-W{int(tail.iloc[-1]['iso_week']):02d}",
            "mean_weekly_return_on_capital": float(values.mean()),
            "annualised_simple": float(values.mean() * carry.WEEKS_PER_YEAR),
            "interval_90_week_cluster": list(boot["intervals"][f"{config.report_level:.2f}"]),
            "sharpe_weekly_annualised": _sharpe(tail["return_on_capital"]),
            "max_drawdown_nav": _max_drawdown_nav(span["nav_pooled"].to_numpy(dtype=float)) if len(span) else nan,
        }
    if len(eligible) < ROLLING_WEEKS:
        out["rolling_52"] = {"state": "insufficient_weeks", "windows": 0, "min_annualised_simple": nan, "max_annualised_simple": nan, "last_annualised_simple": nan}
    else:
        values = eligible["return_on_capital"].to_numpy(dtype=float)
        rolling = np.array([values[i : i + ROLLING_WEEKS].mean() * carry.WEEKS_PER_YEAR for i in range(len(values) - ROLLING_WEEKS + 1)], dtype=float)
        out["rolling_52"] = {"state": "ok", "windows": int(len(rolling)), "min_annualised_simple": float(rolling.min()), "max_annualised_simple": float(rolling.max()), "last_annualised_simple": float(rolling[-1])}
    return out


def _lane_a_full(inputs: dict[str, Any], config: Config, *, variant: str, fee_mult: float = 1.0) -> tuple[dict[str, Any], dict[str, pd.DataFrame]]:
    tables: dict[str, pd.DataFrame] = {}
    entry_ms = exit_ms = None
    for symbol in config.symbols:
        table = carry.boundary_table(inputs[f"{symbol}_funding"], inputs[f"{symbol}_perp"], inputs[f"{symbol}_spot"])
        tables[symbol] = table
        usable = table[~table["close_missing"]]
        if usable.empty:
            raise RuntimeError(f"{symbol}: no boundary carries a close")
        start = carry.first_monday_boundary_on_or_after(max(config.lane_a_start_ms, int(usable["boundary_ms"].iloc[0])))
        end = carry.last_monday_boundary_on_or_before(min(config.lane_a_end_ms, int(usable["boundary_ms"].iloc[-1])))
        if end <= start:
            raise RuntimeError(f"{symbol}: span has no complete week")
        entry_ms = start if entry_ms is None else max(entry_ms, start)
        exit_ms = end if exit_ms is None else min(exit_ms, end)
    assert entry_ms is not None and exit_ms is not None
    years = (exit_ms - entry_ms) / (365.0 * DAY_MS)
    per_asset: dict[str, Any] = {}
    weekly_by_asset: dict[str, pd.DataFrame] = {}
    frames_by_asset: dict[str, pd.DataFrame] = {}
    for symbol, table in tables.items():
        ledger = carry.simulate(table, variant=variant, start_ms=entry_ms, end_ms=exit_ms, fee_mult=fee_mult, unverifiable_scope=config.unverifiable_scope)
        frame = carry.ledger_frame(ledger)
        weekly = carry.weekly_returns(frame)
        weekly_by_asset[symbol] = weekly
        frames_by_asset[symbol] = frame
        per_asset[symbol] = _asset_summary(weekly, frame, years=years, disclose_scope=config.discloses_scope, disclose_bases=config.discloses_bases)

    keys = ["iso_year", "iso_week", "week_end_ms"]
    pooled = None
    for symbol, weekly in weekly_by_asset.items():
        part = weekly[keys + ["return_on_capital", "eligible", "rebalances", "forced_liquidations", "unverifiable_open", "rejected_open"]].rename(
            columns={"return_on_capital": f"r_{symbol}", "eligible": f"e_{symbol}", "rebalances": f"rb_{symbol}", "forced_liquidations": f"fl_{symbol}", "unverifiable_open": f"uv_{symbol}", "rejected_open": f"ro_{symbol}"}
        )
        pooled = part if pooled is None else pooled.merge(part, on=keys, how="inner")
    assert pooled is not None
    pooled["return_on_capital"] = pooled[[f"r_{s}" for s in config.symbols]].mean(axis=1)
    pooled["eligible"] = pooled[[f"e_{s}" for s in config.symbols]].all(axis=1)
    eligible = pooled[pooled["eligible"]]
    values = eligible["return_on_capital"].to_numpy(dtype=float)
    boots = _two_bootstraps(values, config)
    lb_min_weekly = boots["lower_bound_gate"]["minimum"]
    mean_weekly = float(values.mean()) if len(values) else float("nan")
    annualised = mean_weekly * carry.WEEKS_PER_YEAR if _finite(mean_weekly) else float("nan")
    annualised_lower_bound = lb_min_weekly * carry.WEEKS_PER_YEAR if _finite(lb_min_weekly) else float("nan")
    lower_bound_after_haircut = annualised_lower_bound - config.haircut_per_year if _finite(annualised_lower_bound) else float("nan")
    point_after_haircut = annualised - config.haircut_per_year if _finite(annualised) else float("nan")
    max_dd = float(max_drawdown_from_returns(pooled["return_on_capital"])) if len(pooled) else float("nan")
    forced = int(sum(int(pooled[f"fl_{s}"].sum()) for s in config.symbols))
    unverifiable = int(sum(int(pooled[f"uv_{s}"].sum()) for s in config.symbols))
    yearly = _yearly_sums(pooled, "return_on_capital")
    yearly_counts = _yearly_eligible_counts(pooled)
    year_check = _positive_qualifying_years(yearly, yearly_counts, config.complete_years_a, config.min_eligible_weeks_per_year)
    regime = _sma_regime(inputs[f"{config.symbols[0]}_spot"], pooled, sma_days=config.sma_days)
    regime_cut = {}
    for label in ("above", "below", "unknown"):
        mask = (regime == label) & pooled["eligible"]
        subset = pooled.loc[mask, "return_on_capital"]
        regime_cut[label] = {"weeks": int(mask.sum()), "mean_weekly_return_on_capital": float(subset.mean()) if len(subset) else float("nan")}

    # WO-170: the NAV path G3 reads under drawdown_basis = "nav"; computed only when disclosed so nothing else changes.
    nav_frame = _pooled_nav_frame(frames_by_asset) if config.discloses_bases else None
    max_dd_nav = _max_drawdown_nav(nav_frame["nav_pooled"].to_numpy(dtype=float)) if nav_frame is not None else float("nan")
    if config.drawdown_basis == "nav":
        drawdown_bounded = bool(_finite(max_dd_nav) and abs(max_dd_nav) <= config.g3_max_drawdown)
    else:
        # quant_lab.risk.max_drawdown_from_returns returns the most negative peak-to-trough ratio, in [-1, 0]; the gate reads its magnitude.
        drawdown_bounded = bool(_finite(max_dd) and abs(max_dd) <= config.g3_max_drawdown)
    gates = {
        "G1_lower_bound_after_haircut_positive": bool(_finite(lower_bound_after_haircut) and lower_bound_after_haircut > 0.0),
        "G2_point_after_haircut_at_least_hurdle": bool(_finite(point_after_haircut) and point_after_haircut >= config.g2_hurdle),
        "G3_drawdown_bounded_and_no_forced_liquidation": bool(drawdown_bounded and forced == 0 and unverifiable == 0),
        "G4_positive_in_enough_qualifying_years": bool(year_check["positive_years"] >= config.g4_min_positive_years),
    }
    gates["lane_a_go"] = bool(all(gates.values()))
    result = {
        "variant": variant,
        "gated": variant == "V0",
        "fee_multiplier": fee_mult,
        "span": {"entry_boundary_ms": int(entry_ms), "exit_boundary_ms": int(exit_ms), "years": float(years)},
        "per_asset": per_asset,
        "pooled": {
            "weeks_total": int(len(pooled)),
            "eligible_weeks": int(len(eligible)),
            "mean_weekly_return_on_capital": mean_weekly,
            "annualised_return_on_capital": annualised,
            "haircut_per_year": config.haircut_per_year,
            "annualised_after_haircut": point_after_haircut,
            "annualised_lower_bound": annualised_lower_bound,
            "annualised_lower_bound_after_haircut": lower_bound_after_haircut,
            "sharpe_weekly_annualised": _sharpe(eligible["return_on_capital"]) if len(eligible) else float("nan"),
            "max_drawdown_all_weeks": max_dd,
            "cvar_95_weekly": float(conditional_var(eligible["return_on_capital"])) if len(eligible) > 1 else float("nan"),
            "forced_liquidations": forced,
            "unverifiable_open_periods": unverifiable,
            "rebalances": int(sum(int(pooled[f"rb_{s}"].sum()) for s in config.symbols)),
            "bootstrap": {"cluster": boots["cluster"], "stationary_block": boots["stationary_block"]},
            "lower_bound_gate_level": boots["lower_bound_gate"],
            "yearly_return_on_capital": yearly,
            "yearly_eligible_weeks": yearly_counts,
            "year_check": year_check,
            "positive_complete_years": int(year_check["positive_years"]),
            "complete_years": list(config.complete_years_a),
            "regime_btc_sma200": regime_cut,
        },
    }
    if config.discloses_scope:
        # WO-167 and later: the count G3 would have read under WO-166's "either" scope, so a narrower scope never hides a rejected open period.
        result["pooled"]["rejected_open_periods"] = int(sum(int(pooled[f"ro_{s}"].sum()) for s in config.symbols))
    if nav_frame is not None:
        nav_pooled = nav_frame["nav_pooled"].to_numpy(dtype=float)
        weekly_nav = _pooled_weekly_return_on_nav(pooled, nav_frame)
        eligible_nav = weekly_nav[pooled["eligible"]].to_numpy(dtype=float)
        mean_weekly_nav = float(eligible_nav.mean()) if len(eligible_nav) and np.isfinite(eligible_nav).all() else float("nan")
        result["pooled"]["max_drawdown_nav"] = max_dd_nav
        result["pooled"]["cagr_nav"] = _cagr(float(nav_pooled[0]), float(nav_pooled[-1]), years)
        result["pooled"]["total_return_on_capital_simple"] = float((nav_pooled[-1] - nav_pooled[0]) / nav_pooled[0]) if nav_pooled[0] > 0 else float("nan")
        result["pooled"]["mean_weekly_return_on_nav"] = mean_weekly_nav
        result["pooled"]["annualised_return_on_nav"] = mean_weekly_nav * carry.WEEKS_PER_YEAR if _finite(mean_weekly_nav) else float("nan")
        result["pooled"]["recent_period"] = _recent_period(pooled, nav_frame, config)
    if variant == "V0":
        result["gates"] = gates  # V1 is descriptive and never gated: it carries no gate booleans
    return result, frames_by_asset


def lane_a(inputs: dict[str, Any], config: Config, *, variant: str, fee_mult: float = 1.0) -> dict[str, Any]:
    return _lane_a_full(inputs, config, variant=variant, fee_mult=fee_mult)[0]


def deribit_cross_check(inputs: dict[str, Any], config: Config) -> dict[str, Any]:
    """Descriptive only: gross funding per 8h period from hourly Deribit rates, weekly, annualised, minus one amortised round trip."""
    out: dict[str, Any] = {"note": "coin-margined perpetual; funding only; no basis, no liquidation model; never pooled with Binance"}
    for currency in config.currencies:
        frame = inputs[f"{currency}_deribit_funding"].copy()
        frame["boundary_ms"] = ((frame["timestamp"] - 1) // carry.PERIOD_MS + 1) * carry.PERIOD_MS
        periods = frame.groupby("boundary_ms").agg(rate=("interest_1h", "sum"), hours=("interest_1h", "size")).reset_index()
        periods = periods[periods["hours"] == 8]
        keys = periods["boundary_ms"].map(carry.iso_week_key)
        periods["iso_year"] = keys.map(lambda k: k[0])
        periods["iso_week"] = keys.map(lambda k: k[1])
        weekly = periods.groupby(["iso_year", "iso_week"]).agg(rate=("rate", "sum"), periods=("rate", "size")).reset_index()
        weekly = weekly[weekly["periods"] == carry.PERIODS_PER_WEEK]
        values = weekly["rate"].to_numpy(dtype=float)
        years = len(values) / carry.WEEKS_PER_YEAR
        gross = float(values.mean() * carry.WEEKS_PER_YEAR) if len(values) else float("nan")
        out[currency] = {
            "eligible_weeks": int(len(values)),
            "gross_funding_on_notional_annualised": gross,
            "net_of_one_round_trip_annualised": gross - (2.0 * (carry.SPOT_TAKER_FEE + carry.PERP_TAKER_FEE) / years) if years > 0 and _finite(gross) else float("nan"),
            "yearly_gross_on_notional": {str(int(y)): float(v) for y, v in weekly.groupby("iso_year")["rate"].sum().items()},
        }
    return out


# --------------------------------------------------------------------------- lane B


def lane_b(inputs: dict[str, Any], config: Config) -> dict[str, Any]:
    per_currency: dict[str, Any] = {}
    series_by_currency: dict[str, pd.DataFrame] = {}
    for currency, symbol in zip(config.currencies, config.symbols):
        series = vrp.vrp_series(inputs[f"{currency}_dvol"], inputs[f"{symbol}_spot"], start_ms=config.lane_b_start_ms, end_ms=config.lane_b_end_ms, alignment=config.rv_alignment)
        series_by_currency[currency] = series
        accepted = series[~series["rejected"]]
        windows_table: list[dict[str, Any]] | None = None
        if config.discloses_bases:
            # WO-170: the per-window table, with the valid-hour count WO-166's alignment would have given, so the alignment is observable.
            legacy = series if config.rv_alignment == "open_time_in_window" else vrp.vrp_series(inputs[f"{currency}_dvol"], inputs[f"{symbol}_spot"], start_ms=config.lane_b_start_ms, end_ms=config.lane_b_end_ms, alignment="open_time_in_window")
            legacy_hours = dict(zip(legacy["window_start_ms"].astype(np.int64), legacy["valid_hours"].astype(int)))
            windows_table = [
                {
                    "window_start_ms": int(row.window_start_ms),
                    "valid_hours": int(row.valid_hours),
                    "valid_hours_wo166_alignment": int(legacy_hours.get(int(row.window_start_ms), 0)),
                    "implied_variance": float(row.implied_variance),
                    "realised_variance": float(row.realised_variance),
                    "vrp": float(row.vrp),
                    "rejected": bool(row.rejected),
                    "rejected_reason": str(row.rejected_reason),
                }
                for row in series.itertuples(index=False)
            ]
        per_currency[currency] = {
            "windows_total": int(len(series)),
            "windows_accepted": int(len(accepted)),
            "windows_rejected": int(series["rejected"].sum()),
            "rejection_reasons": {reason: int(count) for reason, count in series.loc[series["rejected"], "rejected_reason"].value_counts().items()},
            "mean_vrp": float(accepted["vrp"].mean()) if len(accepted) else float("nan"),
            "mean_vrp_points": float(accepted["vrp_points"].mean()) if len(accepted) else float("nan"),
            "mean_implied_variance": float(accepted["implied_variance"].mean()) if len(accepted) else float("nan"),
            "mean_realised_variance": float(accepted["realised_variance"].mean()) if len(accepted) else float("nan"),
            "share_of_windows_positive": float((accepted["vrp"] > 0).mean()) if len(accepted) else float("nan"),
            "yearly_mean_vrp": vrp.yearly_means(series),
        }
        if windows_table is not None:
            per_currency[currency]["windows"] = windows_table
    pooled = vrp.pooled_windows(series_by_currency)
    accepted = pooled[~pooled["rejected"]]
    values = accepted["vrp"].to_numpy(dtype=float)
    boots = _two_bootstraps(values, config)
    lb_min = boots["lower_bound_gate"]["minimum"]
    yearly = vrp.yearly_means(pooled)
    counts = vrp.yearly_counts(pooled)
    year_check = _positive_qualifying_years(yearly, counts, config.complete_years_b, config.min_windows_per_year)
    lower_bound_positive = bool(_finite(lb_min) and lb_min > 0.0)
    persistent = bool(year_check["positive_years"] >= config.g5_min_positive_years)
    gates = {"G5_lower_bound_positive_and_persistent": bool(lower_bound_positive and persistent)}
    gates["lane_b_go"] = gates["G5_lower_bound_positive_and_persistent"]
    return {
        "per_currency": per_currency,
        "pooled": {
            "windows_total": int(len(pooled)),
            "windows_accepted": int(len(accepted)),
            "mean_vrp": float(values.mean()) if len(values) else float("nan"),
            "mean_vrp_points": float(values.mean() * vrp.VARIANCE_POINTS) if len(values) else float("nan"),
            "bootstrap": {"cluster": boots["cluster"], "stationary_block": boots["stationary_block"]},
            "lower_bound_gate_level": boots["lower_bound_gate"],
            "lower_bound_positive": lower_bound_positive,
            "yearly_mean_vrp": yearly,
            "yearly_accepted_windows": counts,
            "year_check": year_check,
            "positive_complete_years": int(year_check["positive_years"]),
            "complete_years": list(config.complete_years_b),
        },
        "gates": gates,
    }


# --------------------------------------------------------------------------- results


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True, allow_nan=True) + "\n").encode("utf-8")


def _parameters(config: Config) -> dict[str, Any]:
    return {
        "spot_taker_fee": carry.SPOT_TAKER_FEE,
        "perp_taker_fee": carry.PERP_TAKER_FEE,
        "margin_fraction": carry.MARGIN_FRACTION,
        "maintenance_margin": carry.MAINTENANCE_MARGIN,
        "rebalance_margin_ratio": carry.REBALANCE_MARGIN_RATIO,
        "liquidation_move_at_half_margin": carry.liquidation_move(carry.MARGIN_FRACTION),
        "v1_lookback": carry.V1_LOOKBACK,
        "v1_entry_annualised": carry.V1_ENTRY_ANNUALISED,
        "v1_exit_level": carry.V1_EXIT_LEVEL,
        "periods_per_year": carry.PERIODS_PER_YEAR,
        "weeks_per_year": carry.WEEKS_PER_YEAR,
        "haircut_per_year": config.haircut_per_year,
        "g2_hurdle": config.g2_hurdle,
        "g3_max_drawdown": config.g3_max_drawdown,
        "g4_min_positive_years": config.g4_min_positive_years,
        "g5_min_positive_years": config.g5_min_positive_years,
        "min_eligible_weeks_per_year": config.min_eligible_weeks_per_year,
        "min_windows_per_year": config.min_windows_per_year,
        "gate_quantile": config.gate_quantile,
        "report_quantile": config.report_quantile,
        "n_draws": config.n_draws,
        "seed": config.seed,
        "block_length_rule": "round(n_clusters ** (1/3)), minimum 2",
        "sma_days": config.sma_days,
        "vrp_window_hours": vrp.WINDOW_HOURS,
        "vrp_min_valid_hours": vrp.MIN_VALID_HOURS,
        "vrp_step_days": vrp.STEP_DAYS,
    }


def _ledger_csv_bytes(frame: pd.DataFrame) -> bytes:
    """WO-170: the per-boundary ledger, money in units of the inception notional; a defined encoding so verify-results can byte-compare it."""
    out = frame.copy()
    out["boundary_iso"] = pd.to_datetime(out["boundary_ms"].astype(np.int64), unit="ms", utc=True).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    out = out[list(LEDGER_COLUMNS)]
    buffer = io.StringIO()
    out.to_csv(buffer, index=False, lineterminator="\n")
    return buffer.getvalue().encode("utf-8")


def _flatten(payload: Any, prefix: str = "") -> dict[str, Any]:
    """Dotted leaf paths with list indexes (``pooled.bootstrap.cluster.intervals.0.90.0``)."""
    out: dict[str, Any] = {}
    if isinstance(payload, dict):
        for key, value in payload.items():
            out.update(_flatten(value, f"{prefix}{key}."))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            out.update(_flatten(value, f"{prefix}{index}."))
    else:
        out[prefix[:-1]] = payload
    return out


def _leaf_equal(a: Any, b: Any) -> bool:
    if isinstance(a, float) and isinstance(b, float) and _math.isnan(a) and _math.isnan(b):
        return True  # two NaN leaves compare equal
    return bool(a == b) and type(a) is type(b)


def _reconciliation_tables(config: Config) -> dict[str, list[tuple[str, str, str]]]:
    """The registered per-file tables: (kind, key, reason); kind is 'exact' or 'prefix' (a prefix ends in '.')."""
    common = [
        ("exact", "code_revision", "clock_or_revision"), ("exact", "generated_at", "clock_or_revision"),
        ("exact", "work_order", "work_order_label"),
        ("exact", "drawdown_basis", "configuration_switch"), ("exact", "rv_alignment", "configuration_switch"), ("exact", "return_basis", "configuration_switch"),
        ("prefix", "bases.", "bases_block"),
    ]
    carry_table = list(common) + [
        ("exact", "gates.G3_drawdown_bounded_and_no_forced_liquidation", "drawdown_basis"), ("exact", "gates.lane_a_go", "drawdown_basis"),
        ("exact", "pooled.max_drawdown_nav", "drawdown_basis"),
        ("exact", "pooled.cagr_nav", "new_descriptive_field"), ("exact", "pooled.total_return_on_capital_simple", "new_descriptive_field"),
        ("exact", "pooled.mean_weekly_return_on_nav", "new_descriptive_field"), ("exact", "pooled.annualised_return_on_nav", "new_descriptive_field"),
        ("prefix", "pooled.recent_period.", "recent_period_cut"), ("prefix", "ledger_files.", "ledger_export"),
    ]
    for symbol in config.symbols:
        carry_table.append(("exact", f"per_asset.{symbol}.max_drawdown_nav", "drawdown_basis"))
        carry_table.append(("exact", f"per_asset.{symbol}.cagr_nav", "new_descriptive_field"))
    vrp_table = list(common) + [
        ("exact", "pooled.mean_vrp", "rv_alignment"), ("exact", "pooled.mean_vrp_points", "rv_alignment"),
        ("prefix", "pooled.bootstrap.", "rv_alignment"), ("prefix", "pooled.lower_bound_gate_level.", "rv_alignment"),
        ("prefix", "pooled.yearly_mean_vrp.", "rv_alignment"), ("prefix", "pooled.year_check.", "rv_alignment"),
    ]
    for currency in config.currencies:
        vrp_table.append(("prefix", f"per_currency.{currency}.windows.", "rv_alignment"))
        for leaf in ("mean_realised_variance", "mean_vrp", "mean_vrp_points", "share_of_windows_positive"):
            vrp_table.append(("exact", f"per_currency.{currency}.{leaf}", "rv_alignment"))
        vrp_table.append(("prefix", f"per_currency.{currency}.yearly_mean_vrp.", "rv_alignment"))
    return {"carry_v0.json": carry_table, "carry_v1.json": carry_table, "vrp.json": vrp_table}


def _match_reason(path: str, table: list[tuple[str, str, str]]) -> str:
    exact = [reason for kind, key, reason in table if kind == "exact" and key == path]
    if len(exact) > 1:
        raise RuntimeError(f"reconciliation table defect: two exact entries for {path}")
    if exact:
        return exact[0]
    prefixes = [(len(key), reason) for kind, key, reason in table if kind == "prefix" and path.startswith(key)]
    if not prefixes:
        raise RuntimeError(f"unexplained difference at {path}: no reconciliation table entry matches; this is a defect in the registered table, not a result")
    prefixes.sort(reverse=True)
    if len(prefixes) > 1 and prefixes[0][0] == prefixes[1][0]:
        raise RuntimeError(f"reconciliation table defect: two prefixes of equal length match {path}")
    return prefixes[0][1]


def reconcile(root: Path, config: Config, payloads: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """WO-170: every leaf that differs from the committed WO-167 files must match exactly one registered reason; anything else aborts."""
    against = Path(root) / RECONCILIATION_AGAINST
    tables = _reconciliation_tables(config)
    files: dict[str, Any] = {}
    for name, table in tables.items():
        path = against / name
        try:
            reference = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"reconciliation input missing or unreadable: {path}: {exc}") from exc
        old = _flatten(reference)
        new = _flatten(payloads[name])
        removed = sorted(set(old) - set(new))
        if removed:
            raise RuntimeError(f"{name}: leaves present in {RECONCILIATION_AGAINST} are absent from this pass: {removed[:5]}")
        differences: list[dict[str, Any]] = []
        for leaf in sorted(set(new)):
            if leaf in old and _leaf_equal(old[leaf], new[leaf]):
                continue
            differences.append({"path": leaf, "reason": _match_reason(leaf, table), "wo167": old.get(leaf, "<absent>"), "wo170": new[leaf]})
        files[name] = {"differing_leaves": len(differences), "identical_leaves": int(len(new) - len(differences)), "differences": differences}
    return {
        "against": RECONCILIATION_AGAINST,
        "work_order": config.work_order,
        "reason_codes": sorted({reason for table in tables.values() for _, _, reason in table}),
        "rule": "leaves addressed by dotted path with list indexes; an exact path beats a prefix; the longest prefix wins; no match, two matches of equal precedence, or a removed leaf aborts before any file is written; two NaN leaves compare equal",
        "files": files,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }


def compute_all(root: Path, *, config: Config, code_revision: str, generated_at: str) -> dict[str, bytes]:
    inputs = load_inputs(root, config)
    common = {
        "work_order": config.work_order,
        "evidence_class": "historical",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
        "generated_at": generated_at,
        "code_revision": code_revision,
        "manifest_sha256": inputs["manifest_sha256"],
        "inputs": inputs["files"],
        "parameters": _parameters(config),
    }
    if config.discloses_scope:
        # Every configuration other than WO-166's own names the scope in every results JSON; WO-166's committed files must not gain a byte.
        common["unverifiable_scope"] = config.unverifiable_scope
    if config.discloses_bases:
        # WO-170: the switches, the return basis the gates read, and what every price and cash flow is.
        common["drawdown_basis"] = config.drawdown_basis
        common["rv_alignment"] = config.rv_alignment
        common["return_basis"] = RETURN_BASIS
        common["bases"] = dict(BASES)
    v0_result, v0_frames = _lane_a_full(inputs, config, variant="V0")
    v0 = {**common, **v0_result}
    sensitivity = lane_a(inputs, config, variant="V0", fee_mult=2.0)["pooled"]
    v0["fee_sensitivity_2x"] = {k: sensitivity[k] for k in ("annualised_return_on_capital", "annualised_after_haircut", "annualised_lower_bound_after_haircut", "max_drawdown_all_weeks")}
    v0["deribit_cross_check"] = deribit_cross_check(inputs, config)
    v1_result, v1_frames = _lane_a_full(inputs, config, variant="V1")
    v1 = {**common, **v1_result}
    b = {**common, **lane_b(inputs, config)}
    files: dict[str, bytes] = {}
    if config.discloses_bases:
        for variant, frames, payload in (("V0", v0_frames, v0), ("V1", v1_frames, v1)):
            ledger_files: dict[str, str] = {}
            for symbol, frame in frames.items():
                name = f"ledger_{symbol}_{variant}.csv"
                files[name] = _ledger_csv_bytes(frame)
                ledger_files[name] = hashlib.sha256(files[name]).hexdigest()
            payload["ledger_files"] = ledger_files
    files["carry_v0.json"] = _json_bytes(v0)
    files["carry_v1.json"] = _json_bytes(v1)
    files["vrp.json"] = _json_bytes(b)
    reconciliation = None
    if config.discloses_bases:
        reconciliation = reconcile(root, config, {"carry_v0.json": v0, "carry_v1.json": v1, "vrp.json": b})
        files["reconciliation.json"] = _json_bytes(reconciliation)
    files["report.md"] = render_report(v0, v1, b, reconciliation=reconciliation).encode("utf-8")
    return files


def run_all(root: Path, *, code_revision: str, generated_at: str, force: bool = False, config: Config | None = None) -> str:
    root = Path(root)
    config = config or Config()
    results_dir = root / config.results_dir
    if results_dir.exists() and not force:
        raise RuntimeError(f"{results_dir} already exists; one analysis pass is registered — pass --force only to redo a failed write")
    files = compute_all(root, config=config, code_revision=code_revision, generated_at=generated_at)
    if set(files) != set(config.result_files):
        raise RuntimeError(f"the pass produced {sorted(files)} but the configuration registers {sorted(config.result_files)}; nothing written")
    tmp_dir = root / f".{config.results_dir}-tmp-{os.getpid()}"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)
    try:
        for name, payload in files.items():
            (tmp_dir / name).write_bytes(payload)
    except BaseException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    if results_dir.exists():
        shutil.rmtree(results_dir)
    os.replace(tmp_dir, results_dir)
    v0 = json.loads(files["carry_v0.json"])
    b = json.loads(files["vrp.json"])
    return f"lane A (V0) GO={v0['gates']['lane_a_go']} gates={v0['gates']}; lane B GO={b['gates']['lane_b_go']}; results in {results_dir}"


def verify_results(root: Path, *, config: Config | None = None) -> list[str]:
    """Recompute with the stored clock and revision and byte-compare every file the configuration registers."""
    root = Path(root)
    config = config or Config()
    results_dir = root / config.results_dir
    if not results_dir.is_dir():
        return [f"results directory missing: {config.results_dir}"]
    try:
        stored = json.loads((results_dir / "carry_v0.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"carry_v0.json unreadable: {exc}"]
    try:
        files = compute_all(root, config=config, code_revision=str(stored.get("code_revision", "")), generated_at=str(stored.get("generated_at", "")))
    except Exception as exc:  # noqa: BLE001 - any recompute failure is a verification failure
        return [f"recompute failed: {exc}"]
    failures: list[str] = []
    for name in config.result_files:
        target = results_dir / name
        if not target.is_file():
            failures.append(f"missing: {name}")
            continue
        if target.read_bytes() != files[name]:
            failures.append(f"byte difference: {name}")
    for extra in sorted(path.name for path in results_dir.iterdir() if path.name not in config.result_files):
        failures.append(f"extra file: {extra}")
    if not failures and stored.get("manifest_sha256") != sha256_path(root / "manifest.json"):
        failures.append("manifest_sha256 in results does not match the committed manifest")
    return failures


# WO-167: the refined completeness scope, its own work-order label and results directory. Every other literal is WO-166's.
WO167_CONFIG = Config(unverifiable_scope="perp", work_order="WO-167", results_dir="results_wo167")
# WO-170: WO-167's scope, the NAV drawdown basis, the 720-interval realised-variance window, and the nine files it writes and verifies.
WO170_RESULT_FILES = RESULT_FILES + ("reconciliation.json", "ledger_BTCUSDT_V0.csv", "ledger_ETHUSDT_V0.csv", "ledger_BTCUSDT_V1.csv", "ledger_ETHUSDT_V1.csv")
WO170_CONFIG = Config(unverifiable_scope="perp", work_order="WO-170", results_dir="results_wo170", drawdown_basis="nav", rv_alignment="return_intervals", result_files=WO170_RESULT_FILES)
CONFIGS: dict[str, Config] = {"WO-166": Config(), "WO-167": WO167_CONFIG, "WO-170": WO170_CONFIG}
