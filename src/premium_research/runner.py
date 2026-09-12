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

import numpy as np
import pandas as pd
from quant_lab.risk import conditional_var, max_drawdown_from_returns

from . import carry, vrp
from .bootstrap import block_length_for, cluster_bootstrap_mean, lower_bound, stationary_block_bootstrap_mean
from .manifest import load_manifest, sha256_path, verify_manifest
from .report import render_report

RESULT_FILES = ("carry_v0.json", "carry_v1.json", "vrp.json", "report.md")
WORK_ORDER = "WO-166"

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
    n_draws: int = 10_000
    seed: int = 20260912
    sma_days: int = 200

    @property
    def gate_level(self) -> float:
        return 1.0 - 2.0 * self.gate_quantile

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


def _asset_summary(weekly: pd.DataFrame, frame: pd.DataFrame, *, years: float) -> dict[str, Any]:
    eligible = weekly[weekly["eligible"]]
    return {
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


def lane_a(inputs: dict[str, Any], config: Config, *, variant: str, fee_mult: float = 1.0) -> dict[str, Any]:
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
    for symbol, table in tables.items():
        ledger = carry.simulate(table, variant=variant, start_ms=entry_ms, end_ms=exit_ms, fee_mult=fee_mult)
        frame = carry.ledger_frame(ledger)
        weekly = carry.weekly_returns(frame)
        weekly_by_asset[symbol] = weekly
        per_asset[symbol] = _asset_summary(weekly, frame, years=years)

    keys = ["iso_year", "iso_week", "week_end_ms"]
    pooled = None
    for symbol, weekly in weekly_by_asset.items():
        part = weekly[keys + ["return_on_capital", "eligible", "rebalances", "forced_liquidations", "unverifiable_open"]].rename(
            columns={"return_on_capital": f"r_{symbol}", "eligible": f"e_{symbol}", "rebalances": f"rb_{symbol}", "forced_liquidations": f"fl_{symbol}", "unverifiable_open": f"uv_{symbol}"}
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
    gates = {
        "G1_lower_bound_after_haircut_positive": bool(_finite(lower_bound_after_haircut) and lower_bound_after_haircut > 0.0),
        "G2_point_after_haircut_at_least_hurdle": bool(_finite(point_after_haircut) and point_after_haircut >= config.g2_hurdle),
        # quant_lab.risk.max_drawdown_from_returns returns the most negative peak-to-trough ratio, in [-1, 0]; the gate reads its magnitude.
        "G3_drawdown_bounded_and_no_forced_liquidation": bool(_finite(max_dd) and abs(max_dd) <= config.g3_max_drawdown and forced == 0 and unverifiable == 0),
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
    if variant == "V0":
        result["gates"] = gates  # V1 is descriptive and never gated: it carries no gate booleans
    return result


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
        series = vrp.vrp_series(inputs[f"{currency}_dvol"], inputs[f"{symbol}_spot"], start_ms=config.lane_b_start_ms, end_ms=config.lane_b_end_ms)
        series_by_currency[currency] = series
        accepted = series[~series["rejected"]]
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


def compute_all(root: Path, *, config: Config, code_revision: str, generated_at: str) -> dict[str, bytes]:
    inputs = load_inputs(root, config)
    common = {
        "work_order": WORK_ORDER,
        "evidence_class": "historical",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
        "generated_at": generated_at,
        "code_revision": code_revision,
        "manifest_sha256": inputs["manifest_sha256"],
        "inputs": inputs["files"],
        "parameters": _parameters(config),
    }
    v0 = {**common, **lane_a(inputs, config, variant="V0")}
    sensitivity = lane_a(inputs, config, variant="V0", fee_mult=2.0)["pooled"]
    v0["fee_sensitivity_2x"] = {k: sensitivity[k] for k in ("annualised_return_on_capital", "annualised_after_haircut", "annualised_lower_bound_after_haircut", "max_drawdown_all_weeks")}
    v0["deribit_cross_check"] = deribit_cross_check(inputs, config)
    v1 = {**common, **lane_a(inputs, config, variant="V1")}
    b = {**common, **lane_b(inputs, config)}
    files = {"carry_v0.json": _json_bytes(v0), "carry_v1.json": _json_bytes(v1), "vrp.json": _json_bytes(b)}
    files["report.md"] = render_report(v0, v1, b).encode("utf-8")
    return files


def run_all(root: Path, *, code_revision: str, generated_at: str, force: bool = False, config: Config | None = None) -> str:
    root = Path(root)
    config = config or Config()
    results_dir = root / "results"
    if results_dir.exists() and not force:
        raise RuntimeError(f"{results_dir} already exists; one analysis pass is registered — pass --force only to redo a failed write")
    files = compute_all(root, config=config, code_revision=code_revision, generated_at=generated_at)
    tmp_dir = root / f".results-tmp-{os.getpid()}"
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
    """Recompute with the stored clock and revision and byte-compare every committed results file."""
    root = Path(root)
    results_dir = root / "results"
    if not results_dir.is_dir():
        return ["results directory missing"]
    try:
        stored = json.loads((results_dir / "carry_v0.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"carry_v0.json unreadable: {exc}"]
    try:
        files = compute_all(root, config=config or Config(), code_revision=str(stored.get("code_revision", "")), generated_at=str(stored.get("generated_at", "")))
    except Exception as exc:  # noqa: BLE001 - any recompute failure is a verification failure
        return [f"recompute failed: {exc}"]
    failures: list[str] = []
    for name in RESULT_FILES:
        target = results_dir / name
        if not target.is_file():
            failures.append(f"missing: {name}")
            continue
        if target.read_bytes() != files[name]:
            failures.append(f"byte difference: {name}")
    for extra in sorted(path.name for path in results_dir.iterdir() if path.name not in RESULT_FILES):
        failures.append(f"extra file: {extra}")
    if not failures and stored.get("manifest_sha256") != sha256_path(root / "manifest.json"):
        failures.append("manifest_sha256 in results does not match the committed manifest")
    return failures
