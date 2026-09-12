"""Markdown report for WO-166. The verdict lines are generated from the gate booleans, never typed."""

from __future__ import annotations

import math
from typing import Any

from .vrp import VARIANCE_POINTS

GATE_KEYS_A = (
    "G1_lower_bound_after_haircut_positive",
    "G2_point_after_haircut_at_least_hurdle",
    "G3_drawdown_bounded_and_no_forced_liquidation",
    "G4_positive_in_enough_qualifying_years",
)
GATE_KEYS_B = ("G5_lower_bound_positive_and_persistent",)


def verdict_line(gates: dict[str, Any], keys: tuple[str, ...], label: str) -> str:
    missing = [key for key in keys if key not in gates]
    if missing:
        raise ValueError(f"gate keys missing from results: {missing}")
    states = {key: gates[key] for key in keys}
    if any(not isinstance(value, bool) for value in states.values()):
        raise ValueError("gate values must be booleans")
    verdict = "GO" if all(states.values()) else "NO-GO"
    detail = ", ".join(f"{key.split('_', 1)[0]}={'pass' if value else 'FAIL'}" for key, value in states.items())
    return f"**{label}: {verdict}** ({detail})"


def _pct(value: Any, digits: int = 2) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    return "n/a" if not math.isfinite(number) else f"{number * 100:.{digits}f}%"


def _num(value: Any, digits: int = 4) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    return "n/a" if not math.isfinite(number) else f"{number:.{digits}f}"


def _iso(ms: Any) -> str:
    try:
        import datetime as _dt

        return _dt.datetime.fromtimestamp(int(ms) / 1000.0, tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OSError, OverflowError):
        return "n/a"


def _pooled_table(pooled: dict[str, Any], *, gated: bool = True, hurdle: float | None = None) -> list[str]:
    lb = pooled["lower_bound_gate_level"]
    g1_label = "G1 quantity: that lower bound annualised minus the haircut" if gated else "lower bound annualised minus the haircut (descriptive; V1 is never gated)"
    g2_label = "G2 quantity: point estimate minus the declared" if gated else "point estimate minus the declared"
    margin_rows: list[str] = []
    if gated and hurdle is not None:
        g2 = pooled["annualised_after_haircut"]
        g1 = pooled["annualised_lower_bound_after_haircut"]
        g2_margin = (float(g2) - hurdle) * 100.0 if isinstance(g2, (int, float)) and math.isfinite(float(g2)) else float("nan")
        g1_margin = float(g1) * 100.0 if isinstance(g1, (int, float)) and math.isfinite(float(g1)) else float("nan")
        margin_rows = [
            f"| margin of the G2 quantity over the {hurdle * 100:.1f}% hurdle | {_num(g2_margin, 2)} pp |",
            f"| margin of the G1 quantity over zero | {_num(g1_margin, 2)} pp |",
        ]
    b90 = pooled["bootstrap"]["cluster"]["intervals"].get("0.90", [float("nan"), float("nan")])
    s90 = pooled["bootstrap"]["stationary_block"]["intervals"].get("0.90", [float("nan"), float("nan")])
    return [
        "| quantity | value |",
        "|---|---|",
        f"| eligible weeks | {pooled['eligible_weeks']} of {pooled['weeks_total']} |",
        f"| mean weekly net return on capital | {_pct(pooled['mean_weekly_return_on_capital'], 4)} |",
        f"| annualised net return on capital (mean weekly x 52) | {_pct(pooled['annualised_return_on_capital'])} |",
        f"| {g2_label} {_pct(pooled['haircut_per_year'], 1)} haircut | {_pct(pooled['annualised_after_haircut'])} |",
        f"| 90% interval, week-cluster | [{_pct(b90[0], 4)}, {_pct(b90[1], 4)}] weekly |",
        f"| 90% interval, stationary block (L = {lb.get('block_length', 'n/a')}) | [{_pct(s90[0], 4)}, {_pct(s90[1], 4)}] weekly |",
        f"| 0.025-quantile lower bound, minimum of the two | {_pct(lb['minimum'], 4)} weekly = {_pct(pooled['annualised_lower_bound'])} annualised |",
        f"| {g1_label} | {_pct(pooled['annualised_lower_bound_after_haircut'])} |",
        *margin_rows,
        f"| Sharpe (weekly, annualised) | {_num(pooled['sharpe_weekly_annualised'], 2)} |",
        f"| max drawdown, all weeks (peak-to-trough, negative; G3 reads its magnitude) | {_pct(pooled['max_drawdown_all_weeks'])} |",
        f"| CVaR 95% weekly (mean loss magnitude in the worst 5% of weeks) | {_pct(pooled['cvar_95_weekly'])} |",
        f"| forced liquidations | {pooled['forced_liquidations']} |",
        f"| open-position periods with missing bars (liquidation unverifiable; G3 requires 0) | {pooled['unverifiable_open_periods']} |",
        f"| rebalances | {pooled['rebalances']} |",
        f"| complete ISO years positive (a year needs >= 45 eligible weeks) | {pooled['positive_complete_years']} of {len(pooled['complete_years'])} |",
    ]


def _yearly_table(yearly: dict[str, float], *, label: str, fmt=_pct) -> list[str]:
    lines = [f"| ISO year | {label} |", "|---|---|"]
    for year in sorted(yearly):
        lines.append(f"| {year} | {fmt(yearly[year])} |")
    return lines


def render_report(v0: dict[str, Any], v1: dict[str, Any], b: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# WO-166 proof-of-concept results (historical-class diagnostic)")
    lines.append("")
    lines.append(
        "This is a historical-class result computed from the committed inputs listed in `manifest.json`. "
        "The gate in Lane A is applied to the lower bound of a bootstrap interval minus a haircut of "
        f"{_pct(v0['parameters']['haircut_per_year'], 1)} per year for bias channels this data cannot measure "
        "(slippage beyond the taker fee, the intra-hour liquidation path, venue operational frictions, and "
        "selection). **That haircut is a declared assumption, not a measurement.** Unfavourable channels "
        "(VIP0 taker fees with no rebate, zero collateral yield, capital at 1.5x notional, no re-leveraging) "
        "are not credited back. Nothing here is verification of record, registers a primary, or authorises capital."
    )
    lines.append("")
    lines.append(f"Generated at {v0['generated_at']} from code revision `{v0['code_revision']}`; manifest sha256 `{v0['manifest_sha256']}`.")
    lines.append("")
    lines.append("## Verdicts")
    lines.append("")
    lines.append(verdict_line(v0["gates"], GATE_KEYS_A, "Lane A — funding carry (V0, always on)"))
    lines.append("")
    lane_b_line = verdict_line(b["gates"], GATE_KEYS_B, "Lane B — variance risk premium existence")
    if b["gates"].get("lane_b_go") is True:
        lane_b_line += " — existence observed, selection bias unaddressed (no haircut can be derived in variance points)"
    lines.append(lane_b_line)
    lines.append("")
    lines.append("## Lane A — pooled BTC + ETH, V0")
    lines.append("")
    lines.append(f"Entry boundary {_iso(v0['span']['entry_boundary_ms'])}, exit boundary {_iso(v0['span']['exit_boundary_ms'])} ({_num(v0['span']['years'], 2)} years).")
    lines.append("")
    lines.extend(_pooled_table(v0["pooled"], gated=True, hurdle=float(v0["parameters"]["g2_hurdle"])))
    lines.append("")
    lines.extend(_yearly_table(v0["pooled"]["yearly_return_on_capital"], label="pooled net return on capital (eligible weeks only)"))
    lines.append("")
    lines.append("### Per asset (V0)")
    lines.append("")
    lines.append("| asset | eligible weeks | annualised net on capital | Sharpe | max drawdown | rebalances | forced liquidations | turnover / yr |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for asset, stats in v0["per_asset"].items():
        lines.append(
            f"| {asset} | {stats['eligible_weeks']} | {_pct(stats['annualised_return_on_capital'])} | {_num(stats['sharpe_weekly_annualised'], 2)} | "
            f"{_pct(stats['max_drawdown_all_weeks'])} | {stats['rebalances']} | {stats['forced_liquidations']} | {_num(stats['turnover_notional_per_year'], 2)} |"
        )
    lines.append("")
    lines.append("### Regime cut (BTC spot against its 200-day SMA at the week's start; descriptive)")
    lines.append("")
    lines.append("| regime | eligible weeks | mean weekly net return on capital |")
    lines.append("|---|---|---|")
    for label, stats in v0["pooled"]["regime_btc_sma200"].items():
        lines.append(f"| {label} | {stats['weeks']} | {_pct(stats['mean_weekly_return_on_capital'], 4)} |")
    lines.append("")
    fs = v0["fee_sensitivity_2x"]
    lines.append("### Fee sensitivity (2x taker fees; descriptive)")
    lines.append("")
    lines.append(f"Annualised net on capital {_pct(fs['annualised_return_on_capital'])}, point after haircut {_pct(fs['annualised_after_haircut'])}, lower bound after haircut {_pct(fs['annualised_lower_bound_after_haircut'])}, max drawdown {_pct(fs['max_drawdown_all_weeks'])}.")
    lines.append("")
    lines.append("### V1 (conditional entry; descriptive, never gated)")
    lines.append("")
    lines.extend(_pooled_table(v1["pooled"], gated=False))
    lines.append("")
    lines.append("### Deribit cross-check (coin-margined, funding only; descriptive)")
    lines.append("")
    lines.append("| currency | eligible weeks | gross funding on notional, annualised | net of one amortised round trip |")
    lines.append("|---|---|---|---|")
    for currency, stats in v0["deribit_cross_check"].items():
        if not isinstance(stats, dict):
            continue
        lines.append(f"| {currency} | {stats['eligible_weeks']} | {_pct(stats['gross_funding_on_notional_annualised'])} | {_pct(stats['net_of_one_round_trip_annualised'])} |")
    lines.append("")
    lines.append("## Lane B — variance risk premium existence (DVOL² minus realised, 30-day non-overlapping windows)")
    lines.append("")
    pooled_b = b["pooled"]
    lb = pooled_b["lower_bound_gate_level"]
    lines.append("| quantity | value |")
    lines.append("|---|---|")
    lines.append(f"| windows accepted (pooled, one cluster per window) | {pooled_b['windows_accepted']} of {pooled_b['windows_total']} |")
    lines.append(f"| mean VRP (variance points) | {_num(pooled_b['mean_vrp_points'], 1)} |")
    lines.append(f"| 0.025-quantile lower bound, minimum of the two | {_num(lb['minimum'] * VARIANCE_POINTS if isinstance(lb['minimum'], (int, float)) and math.isfinite(lb['minimum']) else float('nan'), 1)} points |")
    lines.append(f"| complete years positive (a year needs >= 10 accepted windows) | {pooled_b['positive_complete_years']} of {len(pooled_b['complete_years'])} |")
    lines.append("")
    lines.extend(_yearly_table({k: v * VARIANCE_POINTS for k, v in pooled_b["yearly_mean_vrp"].items()}, label="mean VRP (variance points)", fmt=lambda v: _num(v, 1)))
    lines.append("")
    lines.append("| currency | windows accepted / total | mean VRP (points) | share positive | rejections |")
    lines.append("|---|---|---|---|---|")
    for currency, stats in b["per_currency"].items():
        lines.append(f"| {currency} | {stats['windows_accepted']} / {stats['windows_total']} | {_num(stats['mean_vrp_points'], 1)} | {_pct(stats['share_of_windows_positive'], 1)} | {stats['rejection_reasons']} |")
    lines.append("")
    lines.append("Lane B answers only whether a premium exists; whether a defined-risk option structure captures it net of option spreads needs historical option quotes, which are paid data.")
    lines.append("")
    return "\n".join(lines) + "\n"
