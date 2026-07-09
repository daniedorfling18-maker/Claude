"""Pre-registered verdict engine for the $100/month question.

Computes, from accrued evidence only, the answer to: "can this bot generate
$100/month profit?" The decision rules below were registered 2026-07-09,
BEFORE the first World Cup focus-cohort final settled, so the verdict cannot
be fitted to the data after the fact.

Decision rules (all thresholds config-overridable under ``profit_verdict:``):

Gate A - edge existence (settlement-independent):
    Uses the closing-line focus view (frozen diagnostic cohorts excluded).
    PENDING until ``focus_final_positions >= minimum_final_samples`` (12).
    PASS  when mean final CLV > 0 AND a one-sided sign test on beat-close
          among finals gives p <= ``sign_test_alpha`` (0.10).
    FAIL  when the sample floor is met and mean final CLV <= 0.
    Otherwise (mean > 0 but not significant) stays PENDING: more finals.

Gate B - edge survives execution costs (evaluated only after A passes):
    Shadow entry fills already embed entry-side costs (ask + slippage), so
    the haircut covers the exit side only. Edge per dollar of turnover is
    taken as mean final CLV directly (conservative: buying price p <= 1
    yields >= CLV per dollar).
    PASS when mean_final_clv - exit_cost_haircut_per_dollar > 0.

Gate C - scale feasibility (evaluated only after B passes):
    required_monthly_turnover = target_usd / net_edge_per_dollar.
    achievable_monthly_turnover = observed focus entries/day (from shadow
    position entry timestamps) x per-trade stake cap x 30.
    PASS when achievable >= required.

Verdict states:
    insufficient_evidence                    - a gate is PENDING; keep accruing.
    no_for_tested_edge_classes               - a gate FAILED conclusively.
    yes_edge_evidenced_pending_paper_confirm - A+B+C pass; the final YES still
        requires the governed paper-confirmation round trips (existing gates,
        unchanged). This engine never authorises trading.

Closed lanes are carried on the record: dutch-book arb (zero persistent
baskets across the whole snapshot ledger) and crypto up/down intraday
(frozen after the execution-cost audit) are both NO independently of the
gates above.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import comb
from pathlib import Path
from typing import Any

from .config import EngineConfig
from .utils import now_utc, parse_timestamp, read_csv_rows, read_json, safe_float, write_json

VERDICT_FILENAME = "profit_verdict.json"

DEFAULT_SETTINGS = {
    "minimum_final_samples": 12,
    "sign_test_alpha": 0.10,
    "exit_cost_haircut_per_dollar": 0.005,
    "max_stake_per_trade_usdc": 10.0,
    "days_per_month": 30.0,
}

REGISTERED_AT_UTC = "2026-07-09T04:00:00Z"


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = cfg.raw.get("profit_verdict", {}) if isinstance(cfg.raw.get("profit_verdict"), dict) else {}
    merged = dict(DEFAULT_SETTINGS)
    merged.update({k: v for k, v in raw.items() if v is not None})
    return merged


def _sign_test_p(successes: int, trials: int) -> float | None:
    """One-sided exact binomial P(X >= successes) under p=0.5."""
    if trials <= 0 or successes < 0 or successes > trials:
        return None
    total = sum(comb(trials, k) for k in range(successes, trials + 1))
    return total / (2 ** trials)


def _entries_per_day(positions_path: Path, diagnostic_substrings: list[str]) -> tuple[float | None, int, float | None]:
    """Observed focus shadow-entry rate from the positions ledger."""
    rows = read_csv_rows(positions_path)
    stamps: list[datetime] = []
    for row in rows:
        cohort = str(row.get("signal_cohort") or "").lower()
        if any(sub in cohort for sub in diagnostic_substrings):
            continue
        stamp = parse_timestamp(row.get("opened_at") or row.get("entry_timestamp_utc") or row.get("timestamp_utc"))
        if stamp is not None:
            stamps.append(stamp)
    if not stamps:
        return None, 0, None
    span_days = max((max(stamps) - min(stamps)).total_seconds() / 86400.0, 1.0)
    return len(stamps) / span_days, len(stamps), round(span_days, 2)


def build_profit_verdict(cfg: EngineConfig) -> dict[str, Any]:
    settings = _settings(cfg)
    target = safe_float((cfg.raw.get("profit_tracking", {}) or {}).get("target_monthly_profit_usdc")) or 100.0
    clv_summary = read_json(cfg.governance_root / "closing_line_value.json", default={}) or {}
    focus = clv_summary.get("focus_view", {}) if isinstance(clv_summary.get("focus_view"), dict) else {}
    proof = read_json(cfg.governance_root / "proof_questions.json", default=None)

    minimum_samples = int(settings["minimum_final_samples"])
    alpha = float(settings["sign_test_alpha"])
    haircut = float(settings["exit_cost_haircut_per_dollar"])

    finals = int(safe_float(focus.get("focus_final_positions")) or 0)
    mean_final_clv = safe_float(focus.get("focus_mean_final_clv"))
    beat_close_rate = safe_float(focus.get("focus_beat_close_rate"))

    # Gate A - edge existence.
    sign_p: float | None = None
    if finals > 0 and beat_close_rate is not None:
        sign_p = _sign_test_p(round(beat_close_rate * finals), finals)
    if finals < minimum_samples:
        gate_a = "pending"
        gate_a_reason = f"{finals}/{minimum_samples} settled focus finals; verdict needs the sample floor."
    elif mean_final_clv is None or mean_final_clv <= 0:
        gate_a = "fail"
        gate_a_reason = (
            f"{finals} settled focus finals with mean final CLV "
            f"{mean_final_clv if mean_final_clv is not None else 'unavailable'} <= 0: entries do not beat the close."
        )
    elif sign_p is not None and sign_p <= alpha:
        gate_a = "pass"
        gate_a_reason = f"mean final CLV {mean_final_clv} > 0 with sign-test p={round(sign_p, 4)} <= {alpha} on {finals} finals."
    else:
        gate_a = "pending"
        gate_a_reason = (
            f"mean final CLV {mean_final_clv} > 0 but sign-test p="
            f"{round(sign_p, 4) if sign_p is not None else 'n/a'} > {alpha}; more finals required."
        )

    # Gate B - net of execution costs (entry side already inside CLV fills).
    net_edge_per_dollar: float | None = None
    if gate_a == "pass" and mean_final_clv is not None:
        net_edge_per_dollar = mean_final_clv - haircut
        gate_b = "pass" if net_edge_per_dollar > 0 else "fail"
        gate_b_reason = (
            f"edge/dollar {mean_final_clv} minus exit haircut {haircut} = {round(net_edge_per_dollar, 6)}"
        )
    else:
        gate_b = "pending" if gate_a != "fail" else "not_evaluated"
        gate_b_reason = "evaluated only after Gate A passes."

    # Gate C - scale feasibility.
    diagnostic_substrings = [
        str(sub).lower().strip()
        for sub in (focus.get("diagnostic_cohort_substrings") or ["updown", "up_down", "up-down"])
        if str(sub).strip()
    ]
    entries_per_day, focus_entries_seen, observed_span_days = _entries_per_day(
        cfg.governance_root / "closing_line_value_positions.csv", diagnostic_substrings
    )
    required_turnover: float | None = None
    achievable_turnover: float | None = None
    if gate_b == "pass" and net_edge_per_dollar and net_edge_per_dollar > 0:
        required_turnover = target / net_edge_per_dollar
        if entries_per_day is not None:
            achievable_turnover = (
                entries_per_day * float(settings["max_stake_per_trade_usdc"]) * float(settings["days_per_month"])
            )
            gate_c = "pass" if achievable_turnover >= required_turnover else "fail"
            gate_c_reason = (
                f"required ${round(required_turnover, 2)}/month vs achievable "
                f"${round(achievable_turnover, 2)}/month ({round(entries_per_day, 2)} focus entries/day x "
                f"${settings['max_stake_per_trade_usdc']} stake cap x {settings['days_per_month']} days)."
            )
        else:
            gate_c = "pending"
            gate_c_reason = "no focus entry ledger yet to estimate achievable turnover."
    else:
        gate_c = "pending" if gate_b not in ("fail",) and gate_a != "fail" else "not_evaluated"
        gate_c_reason = "evaluated only after Gate B passes."

    if gate_a == "fail" or gate_b == "fail" or gate_c == "fail":
        verdict = "no_for_tested_edge_classes"
    elif gate_a == "pass" and gate_b == "pass" and gate_c == "pass":
        verdict = "yes_edge_evidenced_pending_paper_confirmation"
    else:
        verdict = "insufficient_evidence"

    payload = {
        "verdict": verdict,
        "generated_at_utc": now_utc(),
        "registered_at_utc": REGISTERED_AT_UTC,
        "question": "Can this bot generate $100/month profit?",
        "target_monthly_profit_usdc": target,
        "gates": {
            "A_edge_exists": {
                "state": gate_a,
                "reason": gate_a_reason,
                "focus_final_positions": finals,
                "minimum_final_samples": minimum_samples,
                "focus_mean_final_clv": mean_final_clv,
                "focus_beat_close_rate": beat_close_rate,
                "sign_test_p": round(sign_p, 6) if sign_p is not None else None,
                "sign_test_alpha": alpha,
            },
            "B_edge_survives_costs": {
                "state": gate_b,
                "reason": gate_b_reason,
                "exit_cost_haircut_per_dollar": haircut,
                "net_edge_per_dollar": round(net_edge_per_dollar, 6) if net_edge_per_dollar is not None else None,
                "note": "entry-side costs are embedded in shadow fills (entry ask + slippage).",
            },
            "C_scale_feasible": {
                "state": gate_c,
                "reason": gate_c_reason,
                "required_monthly_turnover_usdc": round(required_turnover, 2) if required_turnover else None,
                "achievable_monthly_turnover_usdc": round(achievable_turnover, 2) if achievable_turnover else None,
                "observed_focus_entries": focus_entries_seen,
                "observed_span_days": observed_span_days,
                "entries_per_day": round(entries_per_day, 3) if entries_per_day is not None else None,
                "max_stake_per_trade_usdc": settings["max_stake_per_trade_usdc"],
            },
        },
        "closed_lanes": {
            "dutch_book_arb": "NO - zero persistent baskets across the full proof-snapshot ledger; the book does not leave riskless spreads.",
            "crypto_updown_intraday": "NO - frozen 2026-07 after the execution-cost audit; raw run rate did not survive spreads at short horizons.",
        },
        "next_evidence": (
            "World Cup focus finals settle 2026-07-09 through 2026-07-19; each settlement adds a Gate A sample. "
            "A YES additionally requires the existing governed paper-confirmation round trips before any run-rate claim."
        ),
        "proof_questions_seen": bool(proof),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    write_json(cfg.governance_root / VERDICT_FILENAME, payload)
    return payload
