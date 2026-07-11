"""WO-60 evidence-classed performance factsheet.

Reporting only. This module reads existing paper, shadow, modelled, and
human-live score ledgers; it never feeds a gate, sizing rule, policy, broker,
or order path. Annualised statistics are suppressed below the configured
daily sample floor, and only the explicitly real-money series can ever be
marked externally presentable.
"""
from __future__ import annotations

from datetime import date
import hashlib
import math
from pathlib import Path
import random
from typing import Any, Callable

from .config import EngineConfig, load_config
from .cost_ledger import aggregate_costs, registered_hypothetical_cost_stack
from .utils import now_utc, parse_timestamp, read_csv_rows, read_json, safe_float, write_json


SIMULATED_BANNER = "SIMULATED - paper/model results do not represent live trading"
LIVE_BANNER = "LIVE REAL MONEY - presentation requires reconciliation and the daily sample floor"


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = cfg.raw.get("performance_factsheet", {}) if isinstance(cfg.raw.get("performance_factsheet"), dict) else {}
    merged = {
        "enabled": True,
        "risk_free_rate_annual": 0.0,
        "min_daily_observations": 20,
        "bootstrap_iterations": 2000,
        "bootstrap_seed": 20260711,
        "periods_per_year": 365,
    }
    merged.update({key: value for key, value in raw.items() if value is not None})
    return merged


def _day(row: dict[str, Any]) -> str:
    for key in ("generated_at_utc", "created_at", "timestamp", "date", "snapshot_date"):
        raw = row.get(key)
        text = str(raw or "").strip()
        if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
            return text[:10]
        parsed = parse_timestamp(raw)
        if parsed is not None:
            return parsed.date().isoformat()
    return ""


def _last_per_day(rows: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    latest: dict[str, tuple[str, int, dict[str, Any]]] = {}
    for index, row in enumerate(rows):
        day = _day(row)
        if not day:
            continue
        stamp = str(
            row.get("generated_at_utc")
            or row.get("created_at")
            or row.get("timestamp")
            or row.get("date")
            or row.get("snapshot_date")
            or ""
        )
        current = latest.get(day)
        if current is None or (stamp, index) >= (current[0], current[1]):
            latest[day] = (stamp, index, row)
    return [(day, latest[day][2]) for day in sorted(latest)]


def _sample_std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _annualised_sharpe(returns: list[float], *, risk_free_daily: float, periods_per_year: int) -> float | None:
    volatility = _sample_std(returns)
    if volatility <= 0:
        return None
    excess = sum(value - risk_free_daily for value in returns) / len(returns)
    return excess / volatility * math.sqrt(periods_per_year)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _bootstrap_sharpe_ci(
    returns: list[float],
    *,
    risk_free_daily: float,
    periods_per_year: int,
    iterations: int,
    seed: int,
) -> tuple[float | None, float | None, int]:
    if len(returns) < 2 or iterations <= 0:
        return None, None, 0
    rng = random.Random(seed)
    estimates: list[float] = []
    for _ in range(iterations):
        sample = [returns[rng.randrange(len(returns))] for _ in returns]
        estimate = _annualised_sharpe(
            sample,
            risk_free_daily=risk_free_daily,
            periods_per_year=periods_per_year,
        )
        if estimate is not None and math.isfinite(estimate):
            estimates.append(estimate)
    return _percentile(estimates, 0.05), _percentile(estimates, 0.95), len(estimates)


def _drawdown(returns: list[float]) -> tuple[float, int]:
    wealth = 1.0
    peak = 1.0
    max_depth = 0.0
    underwater_days = 0
    max_duration = 0
    for daily_return in returns:
        wealth *= 1.0 + daily_return
        if wealth >= peak:
            peak = wealth
            underwater_days = 0
        else:
            underwater_days += 1
            max_duration = max(max_duration, underwater_days)
            if peak > 0:
                max_depth = max(max_depth, 1.0 - wealth / peak)
    return max_depth, max_duration


def _stable_seed(base_seed: int, series_id: str) -> int:
    digest = hashlib.sha256(series_id.encode("utf-8")).hexdigest()
    return base_seed + int(digest[:8], 16)


def _performance_stats(
    returns: list[float],
    *,
    dates: list[str] | None,
    settings: dict[str, Any],
    series_id: str,
) -> dict[str, Any]:
    clean_pairs = [
        (str((dates or [""] * len(returns))[index] or ""), float(value))
        for index, value in enumerate(returns)
        if math.isfinite(float(value)) and float(value) >= -1.0
    ]
    clean_dates = [pair[0] for pair in clean_pairs]
    clean = [pair[1] for pair in clean_pairs]
    n_days = len(clean)
    periods = max(1, int(settings["periods_per_year"]))
    min_days = max(1, int(settings["min_daily_observations"]))
    annual_rf = float(settings["risk_free_rate_annual"])
    risk_free_daily = (1.0 + annual_rf) ** (1.0 / periods) - 1.0 if annual_rf > -1 else 0.0
    mean = sum(clean) / n_days if n_days else None
    volatility = _sample_std(clean) if n_days else None
    wealth = math.prod(1.0 + value for value in clean) if clean else 1.0
    cumulative_return = wealth - 1.0 if clean else None
    max_drawdown, max_duration = _drawdown(clean)
    positive = sum(value for value in clean if value > 0)
    negative = abs(sum(value for value in clean if value < 0))
    profit_factor = positive / negative if negative > 0 else None
    hit_rate = sum(1 for value in clean if value > 0) / n_days if n_days else None
    annualised_reason = ""
    annualised_return: float | None = None
    sharpe: float | None = None
    sortino: float | None = None
    calmar: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    valid_bootstraps = 0
    if n_days < min_days:
        annualised_reason = f"requires at least {min_days} daily observations; found {n_days}"
    else:
        annualised_return = wealth ** (periods / n_days) - 1.0 if wealth >= 0 else None
        sharpe = _annualised_sharpe(clean, risk_free_daily=risk_free_daily, periods_per_year=periods)
        downside = math.sqrt(sum(min(value, 0.0) ** 2 for value in clean) / n_days)
        if downside > 0 and mean is not None:
            sortino = (mean - risk_free_daily) / downside * math.sqrt(periods)
        if annualised_return is not None and max_drawdown > 0:
            calmar = annualised_return / max_drawdown
        ci_low, ci_high, valid_bootstraps = _bootstrap_sharpe_ci(
            clean,
            risk_free_daily=risk_free_daily,
            periods_per_year=periods,
            iterations=max(0, int(settings["bootstrap_iterations"])),
            seed=_stable_seed(int(settings["bootstrap_seed"]), series_id),
        )
        if sharpe is None:
            annualised_reason = "daily return volatility is zero; Sharpe is undefined"

    start_date = next((value for value in clean_dates if value), None)
    end_date = next((value for value in reversed(clean_dates) if value), None)
    span_days = None
    if start_date and end_date:
        try:
            span_days = (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days + 1
        except ValueError:
            span_days = None
    return {
        "n_days": n_days,
        "start_date": start_date,
        "end_date": end_date,
        "span_calendar_days": span_days,
        "cumulative_return": None if cumulative_return is None else round(cumulative_return, 8),
        "mean_daily_return": None if mean is None else round(mean, 8),
        "daily_return_volatility": None if volatility is None else round(volatility, 8),
        "annualised_return": None if annualised_return is None else round(annualised_return, 8),
        "annualised_sharpe": None if sharpe is None else round(sharpe, 8),
        "annualised_sortino": None if sortino is None else round(sortino, 8),
        "max_drawdown_depth": round(max_drawdown, 8),
        "max_drawdown_duration_days": max_duration,
        "annualised_calmar": None if calmar is None else round(calmar, 8),
        "hit_rate": None if hit_rate is None else round(hit_rate, 8),
        "profit_factor": None if profit_factor is None else round(profit_factor, 8),
        "best_day_return": None if not clean else round(max(clean), 8),
        "worst_day_return": None if not clean else round(min(clean), 8),
        "bootstrap_sharpe_90_ci": {
            "low": None if ci_low is None else round(ci_low, 8),
            "high": None if ci_high is None else round(ci_high, 8),
            "requested_iterations": max(0, int(settings["bootstrap_iterations"])),
            "valid_iterations": valid_bootstraps,
        },
        "annualised_metrics_available": bool(n_days >= min_days and sharpe is not None),
        "annualised_metrics_reason": annualised_reason,
        "minimum_daily_observations": min_days,
        "periods_per_year": periods,
        "risk_free_rate_annual": annual_rf,
    }


def _metric_section(
    *,
    system_id: str,
    series_id: str,
    label: str,
    evidence_class: str,
    source_path: Path,
    source_observations: int,
    returns: list[float],
    dates: list[str],
    daily_pnl: list[float],
    settings: dict[str, Any],
    return_basis: str,
    unavailable_reason: str = "",
    external_ready_prerequisite: bool = True,
    hypothetical_turnover_usd: float | None = None,
    hypothetical_turnover_basis: str = "",
) -> dict[str, Any]:
    stats = _performance_stats(returns, dates=dates, settings=settings, series_id=series_id)
    is_live = evidence_class == "live_real_money"
    section = {
        "system_id": system_id,
        "series_id": series_id,
        "label": label,
        "evidence_class": evidence_class,
        "banner": LIVE_BANNER if is_live else SIMULATED_BANNER,
        "source_path": str(source_path),
        "source_observations": source_observations,
        "return_basis": return_basis,
        "status": "ok" if stats["n_days"] else "insufficient_daily_data",
        "unavailable_reason": unavailable_reason if not stats["n_days"] else "",
        "cumulative_pnl_usd": round(sum(daily_pnl), 8) if daily_pnl else None,
        "hypothetical_turnover_usd": (
            None if hypothetical_turnover_usd is None else round(max(0.0, hypothetical_turnover_usd), 8)
        ),
        "hypothetical_turnover_basis": hypothetical_turnover_basis,
        "external_presentation_eligible": is_live,
        "external_presentation_ready": bool(
            is_live and external_ready_prerequisite and stats["annualised_metrics_available"]
        ),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
        **stats,
    }
    return section


def _paper_section(cfg: EngineConfig, settings: dict[str, Any]) -> dict[str, Any]:
    path = cfg.output_root / "polymarket_portfolio" / "portfolio_snapshots.csv"
    rows = read_csv_rows(path)
    daily = _last_per_day(rows)
    levels = [
        (day, safe_float(row.get("equity_usdc")))
        for day, row in daily
        if safe_float(row.get("equity_usdc")) is not None
    ]
    returns: list[float] = []
    dates: list[str] = []
    pnl: list[float] = []
    for (previous_day, previous), (day, current) in zip(levels, levels[1:]):
        del previous_day
        if previous is None or current is None or previous <= 0:
            continue
        returns.append(current / previous - 1.0)
        dates.append(day)
        pnl.append(current - previous)
    fills = read_csv_rows(cfg.output_root / "polymarket_portfolio" / "paper_fills.csv")
    turnover = sum(abs(safe_float(row.get("gross_notional_usdc")) or 0.0) for row in fills)
    return _metric_section(
        system_id="paper_portfolio",
        series_id="paper_portfolio",
        label="Paper portfolio",
        evidence_class="paper",
        source_path=path,
        source_observations=len(rows),
        returns=returns,
        dates=dates,
        daily_pnl=pnl,
        settings=settings,
        return_basis="last paper equity per UTC day; close-to-close percentage return",
        unavailable_reason="need at least two UTC days with positive paper equity",
        hypothetical_turnover_usd=turnover,
        hypothetical_turnover_basis="sum of absolute paper fill gross notional across both sides",
    )


def _shadow_level(row: dict[str, Any]) -> float | None:
    roi = safe_float(row.get("shadow_roi"))
    if roi is not None and roi > -1:
        return 1.0 + roi
    pnl = safe_float(row.get("shadow_total_pnl_usdc"))
    capital = safe_float(row.get("shadow_total_buy_cost_usdc")) or safe_float(row.get("capital_usd"))
    if pnl is not None and capital is not None and capital > 0 and pnl / capital > -1:
        return 1.0 + pnl / capital
    return None


def _shadow_cumulative_pnl(row: dict[str, Any]) -> float | None:
    return safe_float(row.get("shadow_total_pnl_usdc"))


def _level_returns(
    daily: list[tuple[str, dict[str, Any]]],
    level_fn: Callable[[dict[str, Any]], float | None],
    pnl_fn: Callable[[dict[str, Any]], float | None],
) -> tuple[list[float], list[str], list[float]]:
    returns: list[float] = []
    dates: list[str] = []
    pnl: list[float] = []
    previous_level = 1.0
    previous_pnl = 0.0
    for day, row in daily:
        direct = safe_float(row.get("shadow_daily_return"))
        if direct is None:
            direct = safe_float(row.get("daily_return"))
        level = level_fn(row)
        if direct is not None:
            daily_return = direct
        elif level is not None and previous_level > 0:
            daily_return = level / previous_level - 1.0
        else:
            continue
        current_pnl = pnl_fn(row)
        direct_pnl = safe_float(row.get("shadow_daily_pnl_usdc"))
        if direct_pnl is None:
            direct_pnl = safe_float(row.get("daily_pnl_usdc"))
        if direct_pnl is None and current_pnl is not None:
            direct_pnl = current_pnl - previous_pnl
        returns.append(daily_return)
        dates.append(day)
        if direct_pnl is not None:
            pnl.append(direct_pnl)
        if level is not None:
            previous_level = level
        if current_pnl is not None:
            previous_pnl = current_pnl
    return returns, dates, pnl


def _shadow_sections(cfg: EngineConfig, settings: dict[str, Any]) -> list[dict[str, Any]]:
    path = cfg.governance_root / "shadow_signal_cohort_pnl.csv"
    rows = read_csv_rows(path)
    by_cohort: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        cohort = str(row.get("signal_cohort") or row.get("family") or "unknown").strip() or "unknown"
        by_cohort.setdefault(cohort, []).append(row)
    sections: list[dict[str, Any]] = []
    aggregate_daily: dict[str, list[dict[str, Any]]] = {}
    for cohort, cohort_rows in sorted(by_cohort.items()):
        daily = _last_per_day(cohort_rows)
        returns, dates, pnl = _level_returns(daily, _shadow_level, _shadow_cumulative_pnl)
        turnover = max((safe_float(row.get("shadow_total_buy_cost_usdc")) or 0.0 for row in cohort_rows), default=0.0)
        sections.append(
            _metric_section(
                system_id="shadow_signal_cohorts",
                series_id=f"shadow_signal_cohorts:{cohort}",
                label=f"Shadow cohort: {cohort}",
                evidence_class="shadow",
                source_path=path,
                source_observations=len(cohort_rows),
                returns=returns,
                dates=dates,
                daily_pnl=pnl,
                settings=settings,
                return_basis="dated cumulative shadow ROI snapshots converted to daily returns",
                unavailable_reason="shadow cohort CSV has no dated daily ROI/PnL history",
                hypothetical_turnover_usd=turnover,
                hypothetical_turnover_basis="latest cumulative shadow buy cost for this cohort",
            )
        )
        for day, row in daily:
            aggregate_daily.setdefault(day, []).append(row)

    aggregate_rows: list[dict[str, Any]] = []
    for day in sorted(aggregate_daily):
        day_rows = aggregate_daily[day]
        total_pnl = sum(safe_float(row.get("shadow_total_pnl_usdc")) or 0.0 for row in day_rows)
        total_capital = sum(safe_float(row.get("shadow_total_buy_cost_usdc")) or 0.0 for row in day_rows)
        if total_capital <= 0:
            rois = [safe_float(row.get("shadow_roi")) for row in day_rows]
            rois = [value for value in rois if value is not None]
            aggregate_roi = sum(rois) / len(rois) if rois else None
        else:
            aggregate_roi = total_pnl / total_capital
        aggregate_rows.append(
            {
                "generated_at_utc": day,
                "shadow_total_pnl_usdc": total_pnl,
                "shadow_total_buy_cost_usdc": total_capital,
                "shadow_roi": aggregate_roi,
            }
        )
    aggregate_returns, aggregate_dates, aggregate_pnl = _level_returns(
        _last_per_day(aggregate_rows),
        _shadow_level,
        _shadow_cumulative_pnl,
    )
    aggregate = _metric_section(
        system_id="shadow_signal_cohorts",
        series_id="shadow_signal_cohorts:aggregate",
        label="Shadow signal cohorts (aggregate)",
        evidence_class="shadow",
        source_path=path,
        source_observations=len(rows),
        returns=aggregate_returns,
        dates=aggregate_dates,
        daily_pnl=aggregate_pnl,
        settings=settings,
        return_basis="capital-weighted aggregate of dated cumulative shadow cohort ROI snapshots",
        unavailable_reason="shadow cohort CSV has no dated daily ROI/PnL history",
        hypothetical_turnover_usd=sum(
            max((safe_float(row.get("shadow_total_buy_cost_usdc")) or 0.0 for row in cohort_rows), default=0.0)
            for cohort_rows in by_cohort.values()
        ),
        hypothetical_turnover_basis="sum of latest cumulative shadow buy cost across cohorts",
    )
    return [aggregate, *sections]


def _maker_model_section(cfg: EngineConfig, settings: dict[str, Any]) -> dict[str, Any]:
    path = cfg.output_root / "maker_carry" / "maker_carry_history.csv"
    rows = read_csv_rows(path)
    returns: list[float] = []
    dates: list[str] = []
    pnl: list[float] = []
    for day, row in _last_per_day(rows):
        net = safe_float(row.get("portfolio_net_carry_usd_per_day"))
        capital = safe_float(row.get("portfolio_capital_usd"))
        if net is None or capital is None or capital <= 0:
            continue
        returns.append(net / capital)
        dates.append(day)
        pnl.append(net)
    modeled_capital = max((safe_float(row.get("portfolio_capital_usd")) or 0.0 for row in rows), default=0.0)
    return _metric_section(
        system_id="maker_carry_model",
        series_id="maker_carry_model",
        label="Maker carry model",
        evidence_class="modeled",
        source_path=path,
        source_observations=len(rows),
        returns=returns,
        dates=dates,
        daily_pnl=pnl,
        settings=settings,
        return_basis="daily modelled net carry divided by modelled portfolio capital",
        unavailable_reason="need dated maker model rows with positive portfolio capital",
        hypothetical_turnover_usd=modeled_capital,
        hypothetical_turnover_basis="one conservative turn of maximum modeled portfolio capital",
    )


def _maker_live_section(cfg: EngineConfig, settings: dict[str, Any]) -> dict[str, Any]:
    path = cfg.output_root / "maker_carry" / "maker_live_test_history.csv"
    rows = read_csv_rows(path)
    daily = _last_per_day(rows)
    default_capital = safe_float((cfg.raw.get("decision_policy", {}) or {}).get("stage0_capital_usd")) or 100.0
    returns: list[float] = []
    dates: list[str] = []
    pnl: list[float] = []
    previous_cumulative: float | None = None
    for day, row in daily:
        cumulative = safe_float(row.get("net_score_usd"))
        daily_net = safe_float(row.get("daily_net_score_usd"))
        if daily_net is None and cumulative is not None:
            daily_net = cumulative if previous_cumulative is None else cumulative - previous_cumulative
        if cumulative is not None:
            previous_cumulative = cumulative
        capital = (
            safe_float(row.get("capital_usd"))
            or safe_float(row.get("starting_capital_usd"))
            or default_capital
        )
        if daily_net is None or capital <= 0:
            continue
        returns.append(daily_net / capital)
        dates.append(day)
        pnl.append(daily_net)
    wallet_configured = bool(str((cfg.raw.get("maker_live_test", {}) or {}).get("wallet_address") or "").strip())
    return _metric_section(
        system_id="maker_live_test",
        series_id="maker_live_test",
        label="Maker live test",
        evidence_class="live_real_money",
        source_path=path,
        source_observations=len(rows),
        returns=returns,
        dates=dates,
        daily_pnl=pnl,
        settings=settings,
        return_basis="daily change in real-wallet net score divided by recorded capital or stage-0 capital",
        unavailable_reason="no dated maker live-test history from a configured wallet",
        external_ready_prerequisite=wallet_configured,
    )


def _apply_cost_accounting(
    cfg: EngineConfig,
    series: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Add actual live costs and scenario-only simulated execution drag."""

    stack = registered_hypothetical_cost_stack(cfg)
    rate = float(stack["registered_ceiling_per_dollar"])
    for row in series:
        gross = safe_float(row.get("cumulative_pnl_usd"))
        if row.get("evidence_class") == "live_real_money":
            costs = aggregate_costs(
                cfg,
                start_date=row.get("start_date"),
                end_date=row.get("end_date"),
            )
            booked = float(costs["total_usd"])
            row.update(
                {
                    "gross_cumulative_pnl_usd": None if gross is None else round(gross, 8),
                    "booked_costs_usd": round(booked, 8),
                    "net_net_cumulative_pnl_usd": None if gross is None else round(gross - booked, 8),
                    "net_of_all_costs": {
                        "gross_usd": None if gross is None else round(gross, 8),
                        "booked_costs_usd": round(booked, 8),
                        "net_net_usd": None if gross is None else round(gross - booked, 8),
                        "window": {"start_date": row.get("start_date"), "end_date": row.get("end_date")},
                        "by_category_usd": costs["by_category_usd"],
                        "actual_booked_costs": True,
                    },
                }
            )
            row.pop("hypothetical_turnover_usd", None)
            row.pop("hypothetical_turnover_basis", None)
            continue

        turnover = safe_float(row.get("hypothetical_turnover_usd")) or 0.0
        drag = turnover * rate
        row["hypothetical_cost_drag"] = {
            "turnover_usd": round(turnover, 8),
            "turnover_basis": row.get("hypothetical_turnover_basis") or "turnover unavailable",
            "registered_ceiling_per_dollar": round(rate, 8),
            "usd_per_100_turnover": stack["usd_per_100_turnover"],
            "components": stack["components"],
            "estimated_drag_usd": round(drag, 8),
            "gross_cumulative_pnl_usd": None if gross is None else round(gross, 8),
            "hypothetical_net_after_drag_usd": None if gross is None else round(gross - drag, 8),
            "scenario_only": True,
            "booked_to_cost_ledger": False,
        }
    return series, stack


def _fmt(value: Any, *, percent: bool = False) -> str:
    number = safe_float(value)
    if number is None:
        return "n/a"
    if percent:
        return f"{number * 100:.2f}%"
    return f"{number:.4f}"


def _fmt_usd(value: Any) -> str:
    number = safe_float(value)
    return "n/a" if number is None else f"${number:.2f}"


def _wallet_reconciliation_alert(payload: dict[str, Any]) -> str:
    reconciliation = payload.get("wallet_reconciliation")
    if not isinstance(reconciliation, dict):
        return ""
    discrepancy = safe_float(reconciliation.get("unexplained_discrepancy_usd"))
    threshold = safe_float(reconciliation.get("discrepancy_threshold_usd")) or 1.0
    if (
        str(reconciliation.get("reconciliation_status") or "") == "DISCREPANCY"
        and discrepancy is not None
        and discrepancy > threshold
    ):
        return f"> 🔴 **WALLET RECONCILIATION DISCREPANCY — ${discrepancy:.2f} unexplained. Human review required.**"
    return ""


def _write_markdown(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# Performance factsheet (WO-60)",
        "",
        f"Generated: {payload.get('generated_at_utc')}",
        "",
        "**REPORTING ONLY — no gate, sizing rule, policy, or broker reads this artifact.**",
        "",
    ]
    alert = _wallet_reconciliation_alert(payload)
    if alert:
        lines += [alert, ""]
    for row in payload.get("series", []):
        ci = row.get("bootstrap_sharpe_90_ci") or {}
        lines += [
            f"## {row.get('label')}",
            "",
            f"> {row.get('banner')}",
            "",
            f"- Evidence class: `{row.get('evidence_class')}`",
            f"- Status: `{row.get('status')}`",
            f"- Daily observations: {row.get('n_days')} ({row.get('start_date') or '-'} to {row.get('end_date') or '-'})",
            f"- Cumulative return: {_fmt(row.get('cumulative_return'), percent=True)}",
            f"- Cumulative P&L: {_fmt_usd(row.get('cumulative_pnl_usd'))}",
            f"- Daily mean / volatility: {_fmt(row.get('mean_daily_return'), percent=True)} / {_fmt(row.get('daily_return_volatility'), percent=True)}",
            f"- Annualised Sharpe: {_fmt(row.get('annualised_sharpe'))} (bootstrap 90% CI: {_fmt(ci.get('low'))} to {_fmt(ci.get('high'))})",
            f"- Annualised Sortino / Calmar: {_fmt(row.get('annualised_sortino'))} / {_fmt(row.get('annualised_calmar'))}",
            f"- Max drawdown: {_fmt(row.get('max_drawdown_depth'), percent=True)} over {row.get('max_drawdown_duration_days')} days",
            f"- Hit rate / profit factor: {_fmt(row.get('hit_rate'), percent=True)} / {_fmt(row.get('profit_factor'))}",
            f"- Best / worst day: {_fmt(row.get('best_day_return'), percent=True)} / {_fmt(row.get('worst_day_return'), percent=True)}",
            f"- Annualisation note: {row.get('annualised_metrics_reason') or 'sample floor met'}",
            f"- Return basis: {row.get('return_basis')}",
            f"- External presentation ready: **{str(bool(row.get('external_presentation_ready'))).lower()}**",
        ]
        if row.get("evidence_class") == "live_real_money":
            net = row.get("net_of_all_costs") or {}
            lines.append(
                f"- Net of all costs: gross {_fmt_usd(net.get('gross_usd'))} - booked costs "
                f"{_fmt_usd(net.get('booked_costs_usd'))} = **net-net {_fmt_usd(net.get('net_net_usd'))}**"
            )
        else:
            drag = row.get("hypothetical_cost_drag") or {}
            lines.append(
                f"- Hypothetical cost drag: {_fmt_usd(drag.get('estimated_drag_usd'))} on "
                f"{_fmt_usd(drag.get('turnover_usd'))} turnover at the registered "
                f"{_fmt(drag.get('registered_ceiling_per_dollar'), percent=True)} ceiling "
                "(scenario only; not booked)"
            )
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def build_performance_factsheet(cfg: EngineConfig) -> dict[str, Any]:
    settings = _settings(cfg)
    out_root = cfg.output_root / "performance"
    json_path = out_root / "performance_factsheet.json"
    markdown_path = out_root / "performance_factsheet.md"
    wallet_reconciliation = read_json(out_root / "wallet_reconciliation.json", default={}) or {}
    cost_ledger_summary = read_json(out_root / "cost_ledger_summary.json", default={}) or {}
    payload: dict[str, Any] = {
        "status": "disabled",
        "generated_at_utc": now_utc(),
        "work_order": "WO-60",
        "reporting_only": True,
        "policy_or_gate_inputs": False,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
        "wallet_reconciliation": wallet_reconciliation,
        "cost_ledger": cost_ledger_summary,
    }
    if str(settings.get("enabled", True)).strip().lower() in {"0", "false", "no", "off"}:
        write_json(json_path, payload)
        _write_markdown(markdown_path, payload)
        return payload

    series = [
        _paper_section(cfg, settings),
        *_shadow_sections(cfg, settings),
        _maker_model_section(cfg, settings),
        _maker_live_section(cfg, settings),
    ]
    series, hypothetical_cost_stack = _apply_cost_accounting(cfg, series)
    payload.update(
        {
            "status": "ok",
            "settings": {
                "risk_free_rate_annual": float(settings["risk_free_rate_annual"]),
                "min_daily_observations": int(settings["min_daily_observations"]),
                "bootstrap_iterations": int(settings["bootstrap_iterations"]),
                "bootstrap_seed": int(settings["bootstrap_seed"]),
                "periods_per_year": int(settings["periods_per_year"]),
            },
            "series": series,
            "registered_hypothetical_cost_stack": hypothetical_cost_stack,
            "series_count": len(series),
            "annualised_series_count": sum(1 for row in series if row.get("annualised_metrics_available")),
            "external_presentation_ready_series": [
                row["series_id"] for row in series if row.get("external_presentation_ready")
            ],
            "honesty_note": (
                "Paper, shadow, and modelled statistics are simulated/model outputs, not live performance. "
                "Only maker_live_test can ever become externally presentable, and only after its sample floor."
            ),
        }
    )
    write_json(json_path, payload)
    _write_markdown(markdown_path, payload)
    return payload


def main(config_path: str) -> dict[str, Any]:
    return build_performance_factsheet(load_config(config_path))
