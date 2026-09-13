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


def _pooled_table(pooled: dict[str, Any], *, gated: bool = True, hurdle: float | None = None, scope: str | None = None, nav_basis: bool = False) -> list[str]:
    lb = pooled["lower_bound_gate_level"]
    g3_reads = "; G3 reads its magnitude" if gated else ""
    if nav_basis:
        # WO-170: G3 reads the NAV path; the WO-166 curve stays for reconciliation and is labelled as not read.
        drawdown_rows = [
            f"| max drawdown, compounded weekly-increment curve (WO-166 basis; not read by G3 under this configuration) | {_pct(pooled['max_drawdown_all_weeks'])} |",
            f"| max drawdown, NAV path over every boundary (peak-to-trough, negative{g3_reads}) | {_pct(pooled.get('max_drawdown_nav'))} |",
        ]
    else:
        drawdown_rows = [f"| max drawdown, all weeks (peak-to-trough, negative{g3_reads}) | {_pct(pooled['max_drawdown_all_weeks'])} |"]
    g3_suffix = "; G3 requires 0" if gated else ""
    if scope == "perp":
        unverifiable_label = f"open-position periods with perpetual-side data absent (liquidation unverifiable{g3_suffix})"
    else:
        unverifiable_label = f"open-position periods with missing bars (liquidation unverifiable{g3_suffix})"
    rejected_rows: list[str] = []
    if scope is not None:  # WO-167 and later: the WO-166 \"either\" count is always shown beside the scoped one
        not_read = "; not read by G3 under the perp scope" if scope == "perp" else ""
        rejected_rows = [f"| open-position periods rejected for an absent bar on either leg (excluded from every estimator; the WO-166 either-scope count{not_read}) | {pooled.get('rejected_open_periods', 'n/a')} |"]
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
        *drawdown_rows,
        f"| CVaR 95% weekly (mean loss magnitude in the worst 5% of weeks) | {_pct(pooled['cvar_95_weekly'])} |",
        f"| forced liquidations | {pooled['forced_liquidations']} |",
        f"| {unverifiable_label} | {pooled['unverifiable_open_periods']} |",
        *rejected_rows,
        f"| rebalances | {pooled['rebalances']} |",
        f"| complete ISO years positive (a year needs >= 45 eligible weeks) | {pooled['positive_complete_years']} of {len(pooled['complete_years'])} |",
    ]


def _yearly_table(yearly: dict[str, float], *, label: str, fmt=_pct) -> list[str]:
    lines = [f"| ISO year | {label} |", "|---|---|"]
    for year in sorted(yearly):
        lines.append(f"| {year} | {fmt(yearly[year])} |")
    return lines


def _bases_sections(payload: dict[str, Any], *, label: str) -> list[str]:
    """WO-170: the NAV-path descriptives and the recent-period cuts for one variant; rendered only when the JSON carries them."""
    pooled = payload["pooled"]
    lines = [
        f"### NAV-path statistics ({label}; descriptive, never gated)",
        "",
        "| quantity | value |",
        "|---|---|",
        f"| compounded annual growth of the pooled NAV | {_pct(pooled.get('cagr_nav'))} |",
        f"| total simple return on inception capital | {_pct(pooled.get('total_return_on_capital_simple'))} |",
        f"| mean weekly return on NAV (eligible weeks) | {_pct(pooled.get('mean_weekly_return_on_nav'), 4)} |",
        f"| annualised return on NAV (mean weekly x 52) | {_pct(pooled.get('annualised_return_on_nav'))} |",
        "",
        f"### Recent-period stability ({label}; retrospective diagnostic, not a prospective validation)",
        "",
        "| cut | state | weeks | mean weekly | annualised simple | 90% week-cluster interval (weekly) | Sharpe | NAV drawdown over the span |",
        "|---|---|---|---|---|---|---|---|",
    ]
    recent = pooled.get("recent_period", {})
    for name in ("last_52", "last_104"):
        cut = recent.get(name, {})
        interval = cut.get("interval_90_week_cluster", [float("nan"), float("nan")])
        lines.append(
            f"| {name} | {cut.get('state', 'n/a')} | {cut.get('weeks', 'n/a')} | {_pct(cut.get('mean_weekly_return_on_capital'), 4)} | {_pct(cut.get('annualised_simple'))} | "
            f"[{_pct(interval[0], 4)}, {_pct(interval[1], 4)}] | {_num(cut.get('sharpe_weekly_annualised'), 2)} | {_pct(cut.get('max_drawdown_nav'))} |"
        )
    rolling = recent.get("rolling_52", {})
    lines.append("")
    lines.append(f"Rolling 52-eligible-week annualised simple return: state {rolling.get('state', 'n/a')}, windows {rolling.get('windows', 'n/a')}, min {_pct(rolling.get('min_annualised_simple'))}, max {_pct(rolling.get('max_annualised_simple'))}, last {_pct(rolling.get('last_annualised_simple'))} (overlapping windows; no share-positive statistic is reported).")
    lines.append("")
    return lines


def _reconciliation_sections(reconciliation: dict[str, Any]) -> list[str]:
    lines = ["## Reconciliation against WO-167", "", f"Every leaf differing from the committed `{reconciliation['against']}/` files, with its registered reason; exact-path differences are listed one by one and prefix-matched groups are summarised.", ""]
    for name, block in reconciliation["files"].items():
        lines.append(f"### {name}: {block['differing_leaves']} differing leaves, {block['identical_leaves']} identical")
        lines.append("")
        lines.append("| leaf | WO-167 | WO-170 | reason |")
        lines.append("|---|---|---|---|")
        groups: dict[str, list[dict[str, Any]]] = {}
        for diff in block["differences"]:
            path = str(diff["path"])
            grouped = None
            for prefix in ("bases.", "pooled.recent_period.", "ledger_files.", "pooled.bootstrap.", "pooled.lower_bound_gate_level.", "pooled.yearly_mean_vrp.", "pooled.year_check."):
                if path.startswith(prefix):
                    grouped = prefix
            if ".windows." in path or ".yearly_mean_vrp." in path:
                grouped = path.split(".windows.")[0] + ".windows." if ".windows." in path else path.rsplit(".yearly_mean_vrp.", 1)[0] + ".yearly_mean_vrp."
            if grouped is None:
                lines.append(f"| `{path}` | {diff['wo167']} | {diff['wo170']} | {diff['reason']} |")
            else:
                groups.setdefault(grouped, []).append(diff)
        for prefix, items in groups.items():
            lines.append(f"| `{prefix}*` ({len(items)} leaves) | — | — | {items[0]['reason']} |")
        lines.append("")
    lines.append("## Reconciliation against WO-166")
    lines.append("")
    lines.append("WO-167 changed exactly two things against WO-166's committed results, recorded in WO-167's charter entry of 2026-09-13: the completeness scope of G3's unverifiable count (`\"either\"` → `\"perp\"`, 16 → 0, with the 16 spot-rejected periods disclosed beside it) and the resulting G3 and Lane A verdict. Every other leaf was identical to the last digit. This pass reconciles against WO-167's files, so those two changes are inherited here and every further difference is listed above.")
    lines.append("")
    return lines


def render_report(v0: dict[str, Any], v1: dict[str, Any], b: dict[str, Any], reconciliation: dict[str, Any] | None = None) -> str:
    lines: list[str] = []
    work_order = str(v0.get("work_order", "WO-166"))
    nav_basis = str(v0.get("drawdown_basis", "")) == "nav"
    bases = "bases" in v0  # WO-170 and later only; WO-166's and WO-167's rendered reports must not gain a byte
    lines.append(f"# {work_order} proof-of-concept results (historical-class diagnostic)")
    lines.append("")
    if "unverifiable_scope" in v0:  # WO-167 and later only; WO-166's rendered report must not gain a byte
        scope = str(v0["unverifiable_scope"])
        scope_text = "any absent bar on either leg (WO-166's registered rule)" if scope == "either" else "absent perpetual-side data only, the leg the liquidation check reads (WO-167's registered rule)"
        lines.append(f"Completeness scope for unverifiable liquidation status: **{scope}** — {scope_text}.")
        lines.append("")
    if bases:
        lines.append(f"Drawdown basis: **{v0['drawdown_basis']}** (G3 reads the ledger's own NAV path over every boundary; the WO-166 compounded weekly-increment curve is reported for reconciliation and not read). Realised-variance alignment: **{v0['rv_alignment']}** (721 closes, 720 return intervals covering each 30-day window exactly). Return basis for G1, G2 and G4: `{v0['return_basis']}` — the mean eligible weekly change in wealth divided by the inception capital, annualised by 52, as WO-166 registered; nothing about those gates changes here. This pass inherits WO-167's completeness scope and its rejected-open disclosure.")
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
    scope = v0.get("unverifiable_scope")
    lines.extend(_pooled_table(v0["pooled"], gated=True, hurdle=float(v0["parameters"]["g2_hurdle"]), scope=scope, nav_basis=nav_basis))
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
    if bases:
        lines.append("### What the prices and cash flows are")
        lines.append("")
        lines.append("| quantity | basis |")
        lines.append("|---|---|")
        for key, text in v0["bases"].items():
            lines.append(f"| {key} | {text} |")
        lines.append("")
        lines.extend(_bases_sections(v0, label="V0"))
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
    lines.extend(_pooled_table(v1["pooled"], gated=False, scope=scope, nav_basis=nav_basis))
    lines.append("")
    if bases:
        lines.extend(_bases_sections(v1, label="V1"))
        lines.append("### Ledger columns (money in units of the inception notional N = 1; one CSV per asset and variant)")
        lines.append("")
        lines.append("| column | meaning |")
        lines.append("|---|---|")
        for column, meaning in (
            ("boundary_ms / boundary_iso", "the funding boundary (UTC)"),
            ("position_open", "a position is held after this boundary's actions"),
            ("marks_carried_forward", "true at a merged boundary whose close is absent: the marks are the last marked closes"),
            ("spot_qty", "spot units held (also the perpetual short size)"),
            ("spot_mark / perp_mark", "the 1h close of the hour ending at the boundary (last traded price)"),
            ("spot_value", "spot_qty x spot_mark, 0 when nothing is held"),
            ("margin", "the perpetual margin account, marked and credited with funding"),
            ("cash", "everything else: the whole wealth when flat, the cumulative fees while open"),
            ("nav", "cash + margin + spot_value; equals wealth at every row within 1e-12"),
            ("funding_received / fees_paid / traded_notional", "this period's funding, fees and traded notional"),
            ("period_return_on_capital", "change in nav divided by the inception capital 1.5 (the gates' basis)"),
            ("period_return_on_nav", "change in nav divided by the previous nav (0 at the entry row)"),
            ("flagged / rebalances / forced_liquidations / unverifiable_open / rejected_open", "the period's flags and counts"),
        ):
            lines.append(f"| {column} | {meaning} |")
        lines.append("")
        ledger_files = v0.get("ledger_files", {})
        if ledger_files:
            lines.append("V0 ledgers (sha256): " + ", ".join(f"`{name}` `{digest[:12]}…`" for name, digest in sorted(ledger_files.items())) + ".")
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
    if bases:
        windows = {currency: stats.get("windows", []) for currency, stats in b["per_currency"].items() if isinstance(stats, dict)}
        counts = {currency: (sum(1 for w in rows if int(w.get("valid_hours", 0)) == int(w.get("valid_hours_wo166_alignment", 0)) + 1), len(rows)) for currency, rows in windows.items()}
        lines.append("Realised variance is computed over the 720 return intervals of each window (the closes from the window's start to its end, 721 closes); WO-166's alignment used the 720 closes whose bars open inside the window, 719 intervals. " + "; ".join(f"{currency}: {n} of {total} windows carry exactly one more valid interval than under WO-166's alignment" for currency, (n, total) in counts.items()) + ".")
        lines.append("")
    if reconciliation is not None:
        lines.extend(_reconciliation_sections(reconciliation))
    return "\n".join(lines) + "\n"
