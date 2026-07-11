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

import re
from datetime import datetime, timezone
from math import comb
from pathlib import Path
from typing import Any

from .config import EngineConfig
from .sharp_anchor import _event_teams_from_question, _event_teams_from_text, _match_subject_from_question, _team_key
from .utils import now_utc, parse_timestamp, read_csv_rows, read_json, safe_float, write_json

VERDICT_FILENAME = "profit_verdict.json"

DEFAULT_SETTINGS = {
    "minimum_final_samples": 12,
    "sign_test_alpha": 0.10,
    "exit_cost_haircut_per_dollar": 0.005,
    "adverse_selection_haircut_per_dollar": 0.005,
    # Polymarket taker fee (docs.polymarket.com/trading/fees): takers pay
    # rate x p x (1-p) per share; per dollar staked that is rate x (1-p).
    # Amendment 6 (2026-07-10 full-docs read): the canonical fees page lists
    # SPORTS at 0.05 (crypto 0.07, finance/politics 0.04, geopolitics 0);
    # the 0.03 charged since amendment 4 UNDERCHARGED. Tightened to 0.05.
    # Makers pay nothing - relevant to the WO-36 making lane.
    "taker_fee_rate": 0.05,
    "max_stake_per_trade_usdc": 10.0,
    "days_per_month": 30.0,
}

REGISTERED_AT_UTC = "2026-07-09T04:00:00Z"

# Amendments registered 2026-07-09 BEFORE the first focus final settled
# (validity review; all three tighten the gates, none loosen them):
#   1. Gate A clusters finals by market: one unit per market (mean CLV of its
#      finals), sign test and the 12-sample floor apply to independent units,
#      so correlated tokens from one market cannot inflate significance.
#   2. Gate B adds an adverse-selection haircut (shadow fills never experience
#      being filled preferentially when wrong; real fills do).
#   3. Gate C output carries its liquidity regime (world_cup_2026_window), and
#      an underpowered positive read extends the evidence window to the next
#      event regime instead of resolving.
#   4. Gate B charges Polymarket taker fees (verified against the live fee
#      docs 2026-07-09: sports takers pay rate x p x (1-p) per share, i.e.
#      rate x (1-p) per dollar staked, computed per final from its actual
#      entry price). The engine was built on a zero-fee prior that is no
#      longer true.
#   5. Gate A units merge across MARKETS that share a fixture (any common
#      team-key on the same settlement date, transitively), not just across
#      tokens of one market. Registered together with - and specifically
#      because of - the coverage expansion below: as of the knockout rounds
#      Polymarket lists several markets per match (daily-win, advance, draw,
#      totals/spread side markets), and counting them as separate units would
#      inflate the sign test with correlated observations. The focus edge
#      class prospectively includes every sharp-anchored market on covered
#      fixtures (the existing h2h mapper already joins the new shapes; the
#      Odds API fetch already pays for soccer_fifa_world_cup h2h). Expansion
#      only ever adds units THROUGH this tightened clustering.
#   6. (2026-07-10, still pre-first-verdict-read) Sports taker fee raised
#      0.03 -> 0.05 per the canonical fees page (full-docs assimilation, see
#      docs/POLYMARKET_API_ASSIMILATION.md). Tightens Gate B only.
#   7. (2026-07-10, registered BEFORE the first interim read, first true read
#      showing 8 units / p=0.637) Operationalizes amendment 3's window
#      extension so no discretion remains when the final read lands:
#      - If the final read (end of the World Cup window, 2026-07-19/20)
#        returns insufficient_evidence (< 12 independent units), the evidence
#        window extends EXACTLY ONCE: 2026-07-20 through 2026-08-19T23:59Z,
#        regime post_wc_2026. Gates, metrics, fees, clustering unchanged.
#        The extension applies regardless of the interim sign of the mean
#        (n < 12 is uninformative in both directions); this supersedes
#        amendment 3's "positive read" wording with a stricter rule: one
#        extension, then forced resolution.
#      - The 2026-08-19/20 read is TERMINAL: all gates pass -> the YES path;
#        >= 12 units with any gate failing -> NO; still < 12 units -> NO on
#        turnover grounds (an edge class that cannot produce 12 independent
#        graded fixtures in ~6 weeks cannot clear $100/month at the $10
#        stake cap; Gate C fails by construction).
REGISTERED_AMENDMENTS_AT_UTC = "2026-07-09T11:00:00Z"

# Amendment 7 terms, machine-readable so the dashboard and audits can show
# the pre-committed schedule next to every verdict read.
REGISTERED_EXTENSION_PROTOCOL = {
    "amendment": 7,
    "registered_at_utc": "2026-07-10T21:30:00Z",
    "final_read_due_utc": "2026-07-20T00:00:00Z",
    "extension_window_start_utc": "2026-07-20T00:00:00Z",
    "extension_window_end_utc": "2026-08-19T23:59:00Z",
    "extension_regime": "post_wc_2026",
    "single_use": True,
    "terminal_rule": (
        "at the 2026-08-19/20 read the verdict resolves: all gates pass -> yes path; "
        ">= 12 units with a failing gate -> no; < 12 units -> no on turnover grounds "
        "(Gate C by construction)."
    ),
}

VERDICT_GATE_DEFINITIONS: list[dict[str, Any]] = [
    {
        "id": "A_edge_exists",
        "rule": "Use equal-weight independent fixture units; pass only with positive mean final CLV and the registered one-sided sign-test threshold after the sample floor.",
        "setting_keys": ["minimum_final_samples", "sign_test_alpha"],
    },
    {
        "id": "B_edge_survives_costs",
        "rule": "Pass only when final CLV remains positive after the exit, adverse-selection, and taker-fee charges.",
        "setting_keys": [
            "exit_cost_haircut_per_dollar",
            "adverse_selection_haircut_per_dollar",
            "taker_fee_rate",
        ],
    },
    {
        "id": "C_scale_feasible",
        "rule": "Pass only when observed focus-entry capacity at the registered per-trade cap can fund the monthly target turnover.",
        "setting_keys": ["max_stake_per_trade_usdc", "days_per_month"],
    },
]

REGISTERED_AMENDMENTS: list[dict[str, Any]] = [
    {
        "number": 1,
        "registered_at_utc": REGISTERED_AMENDMENTS_AT_UTC,
        "effect": "Gate A clusters correlated finals into one market-level unit before sample counting and the sign test.",
        "direction": "tighten_only",
    },
    {
        "number": 2,
        "registered_at_utc": REGISTERED_AMENDMENTS_AT_UTC,
        "effect": "Gate B adds an adverse-selection haircut for preferential real-world fills absent from shadow execution.",
        "direction": "tighten_only",
    },
    {
        "number": 3,
        "registered_at_utc": REGISTERED_AMENDMENTS_AT_UTC,
        "effect": "Gate C labels the World Cup liquidity regime and requires an underpowered read to extend rather than resolve.",
        "direction": "tighten_only",
    },
    {
        "number": 4,
        "registered_at_utc": REGISTERED_AMENDMENTS_AT_UTC,
        "effect": "Gate B charges the actual outcome-price-dependent Polymarket taker fee.",
        "direction": "tighten_only",
    },
    {
        "number": 5,
        "registered_at_utc": REGISTERED_AMENDMENTS_AT_UTC,
        "effect": "Gate A transitively merges markets sharing a real-world fixture so side markets cannot inflate independence.",
        "direction": "tighten_only",
    },
    {
        "number": 6,
        "registered_at_utc": "2026-07-10T00:00:00Z",
        "effect": "The registered sports taker-fee rate increases from 0.03 to 0.05 under the canonical venue fee schedule.",
        "direction": "tighten_only",
    },
    {
        "number": 7,
        "registered_at_utc": str(REGISTERED_EXTENSION_PROTOCOL["registered_at_utc"]),
        "effect": "The evidence window may extend exactly once through 2026-08-19, after which the verdict resolves terminally.",
        "direction": "tighten_only",
    },
]


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = cfg.raw.get("profit_verdict", {}) if isinstance(cfg.raw.get("profit_verdict"), dict) else {}
    merged = dict(DEFAULT_SETTINGS)
    merged.update({k: v for k, v in raw.items() if v is not None})
    return merged


def effective_verdict_settings(cfg: EngineConfig) -> dict[str, Any]:
    return _settings(cfg)


def verdict_gate_definition(gate_id: str) -> dict[str, Any]:
    return next(row for row in VERDICT_GATE_DEFINITIONS if row["id"] == gate_id)


def _sign_test_p(successes: int, trials: int) -> float | None:
    """One-sided exact binomial P(X >= successes) under p=0.5."""
    if trials <= 0 or successes < 0 or successes > trials:
        return None
    total = sum(comb(trials, k) for k in range(successes, trials + 1))
    return total / (2**trials)


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


def _fixture_tags(row: dict[str, Any]) -> set[str]:
    """Team-plus-settlement-date tags identifying the real-world fixture.

    Registered amendment 5: markets sharing any tag are the SAME independence
    unit (a France daily-win, a France-Morocco advance market and the match
    totals all settle on one football match). Extraction is best-effort - a
    row with no parsable team or date simply contributes no tags and clusters
    by market as before. False merges are acceptable: they only ever TIGHTEN
    the unit count."""
    close_stamp = parse_timestamp(row.get("close_time") or row.get("line_timestamp"))
    if close_stamp is None:
        return set()
    day = close_stamp.strftime("%Y-%m-%d")
    question = str(row.get("question") or "")
    slug = str(row.get("market_slug") or "")
    teams: set[str] = set()
    pair = _match_subject_from_question(question) or _event_teams_from_question(question) or _event_teams_from_text(question) or _event_teams_from_text(slug)
    if pair:
        teams.update(pair)
    single = re.match(r"^will\s+(.+?)\s+(?:win|advance|qualify|beat|defeat)\b", question.strip().lower())
    if single:
        teams.add(single.group(1))
    return {f"{_team_key(team)}:{day}" for team in teams if _team_key(team)}


def _clustered_focus_finals(cfg: EngineConfig, diagnostic_substrings: list[str], taker_fee_rate: float) -> dict[str, list[tuple[float, float]]]:
    """Settled focus finals grouped into independent fixture-level units.

    Reads the append-only final history ledger; excludes frozen diagnostic
    cohorts; returns unit -> [(final CLV, taker fee per dollar)] where the
    fee uses each final's actual entry price: rate x (1 - p). Missing entry
    prices charge the worst case (p -> 0 gives rate x 1) - fail conservative.

    Units start as markets (amendment 1) and merge transitively across
    markets sharing a fixture tag (amendment 5) via union-find."""
    rows = read_csv_rows(cfg.governance_root / "closing_line_final_history.csv")

    parent: dict[str, str] = {}

    def find(node: str) -> str:
        while parent.setdefault(node, node) != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: str, right: str) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    entries: list[tuple[str, float, float]] = []
    for row in rows:
        if str(row.get("line_kind") or "") != "closing":
            continue
        cohort = str(row.get("signal_cohort") or "").lower()
        if any(sub in cohort for sub in diagnostic_substrings):
            continue
        clv = safe_float(row.get("clv"))
        if clv is None:
            continue
        key = str(row.get("market_id") or row.get("market_slug") or row.get("shadow_position_id") or "").strip()
        if not key:
            continue
        node = f"market:{key}"
        for tag in _fixture_tags(row):
            union(node, f"fixture:{tag}")
        entry_price = safe_float(row.get("entry_price"))
        if entry_price is not None and 0 < entry_price <= 1:
            fee_per_dollar = taker_fee_rate * (1.0 - entry_price)
        else:
            fee_per_dollar = taker_fee_rate
        entries.append((node, clv, fee_per_dollar))

    clusters: dict[str, list[tuple[float, float]]] = {}
    for node, clv, fee_per_dollar in entries:
        clusters.setdefault(find(node), []).append((clv, fee_per_dollar))
    return clusters


def build_profit_verdict(cfg: EngineConfig) -> dict[str, Any]:
    settings = effective_verdict_settings(cfg)
    target = safe_float((cfg.raw.get("profit_tracking", {}) or {}).get("target_monthly_profit_usdc")) or 100.0
    clv_summary = read_json(cfg.governance_root / "closing_line_value.json", default={}) or {}
    focus = clv_summary.get("focus_view", {}) if isinstance(clv_summary.get("focus_view"), dict) else {}
    proof = read_json(cfg.governance_root / "proof_questions.json", default=None)

    minimum_samples = int(settings["minimum_final_samples"])
    alpha = float(settings["sign_test_alpha"])
    exit_haircut = float(settings["exit_cost_haircut_per_dollar"])
    adverse_haircut = float(settings["adverse_selection_haircut_per_dollar"])
    haircut = exit_haircut + adverse_haircut

    diagnostic_substrings = [
        str(sub).lower().strip() for sub in (focus.get("diagnostic_cohort_substrings") or ["updown", "up_down", "up-down"]) if str(sub).strip()
    ]

    taker_fee_rate = float(settings["taker_fee_rate"])

    # Gate A - edge existence, on INDEPENDENT units (registered amendments 1+5):
    # finals cluster by market, and markets sharing a fixture merge into one
    # unit, so neither correlated tokens nor per-match side markets can
    # inflate the sign test or reach the floor early.
    clusters = _clustered_focus_finals(cfg, diagnostic_substrings, taker_fee_rate)
    finals_total = sum(len(values) for values in clusters.values())
    unit_means = [sum(clv for clv, _ in values) / len(values) for values in clusters.values()]
    unit_fee_means = [sum(fee for _, fee in values) / len(values) for values in clusters.values()]
    n_units = len(unit_means)
    mean_final_clv = round(sum(unit_means) / n_units, 6) if unit_means else None
    mean_taker_fee = round(sum(unit_fee_means) / n_units, 6) if unit_fee_means else None
    beaten_units = sum(1 for value in unit_means if value > 0)
    sign_p = _sign_test_p(beaten_units, n_units) if n_units else None
    if n_units < minimum_samples:
        gate_a = "pending"
        gate_a_reason = f"{n_units}/{minimum_samples} independent settled market units ({finals_total} finals); verdict needs the unit floor."
    elif mean_final_clv is None or mean_final_clv <= 0:
        gate_a = "fail"
        gate_a_reason = (
            f"{n_units} independent settled market units with equal-weight mean final CLV "
            f"{mean_final_clv if mean_final_clv is not None else 'unavailable'} <= 0: entries do not beat the close."
        )
    elif sign_p is not None and sign_p <= alpha:
        gate_a = "pass"
        gate_a_reason = (
            f"unit mean final CLV {mean_final_clv} > 0 with sign-test p={round(sign_p, 4)} <= {alpha} on {beaten_units}/{n_units} independent market units."
        )
    else:
        gate_a = "pending"
        gate_a_reason = (
            f"unit mean final CLV {mean_final_clv} > 0 but sign-test p="
            f"{round(sign_p, 4) if sign_p is not None else 'n/a'} > {alpha}; more settled units required."
        )

    # Gate B - net of execution costs. Entry-side slippage lives inside shadow
    # fills; the charges here are the exit haircut, the registered
    # adverse-selection charge (amendment 2), and Polymarket taker fees
    # (amendment 4: rate x (1-p) per dollar from each final's entry price).
    net_edge_per_dollar: float | None = None
    if gate_a == "pass" and mean_final_clv is not None:
        fee_charge = mean_taker_fee if mean_taker_fee is not None else taker_fee_rate
        net_edge_per_dollar = mean_final_clv - haircut - fee_charge
        gate_b = "pass" if net_edge_per_dollar > 0 else "fail"
        gate_b_reason = (
            f"edge/dollar {mean_final_clv} minus taker fees {fee_charge} and haircuts (exit {exit_haircut} "
            f"+ adverse selection {adverse_haircut}) = {round(net_edge_per_dollar, 6)}"
        )
    else:
        gate_b = "pending" if gate_a != "fail" else "not_evaluated"
        gate_b_reason = "evaluated only after Gate A passes."

    # Gate C - scale feasibility.
    entries_per_day, focus_entries_seen, observed_span_days = _entries_per_day(cfg.governance_root / "closing_line_value_positions.csv", diagnostic_substrings)
    required_turnover: float | None = None
    achievable_turnover: float | None = None
    if gate_b == "pass" and net_edge_per_dollar and net_edge_per_dollar > 0:
        required_turnover = target / net_edge_per_dollar
        if entries_per_day is not None:
            achievable_turnover = entries_per_day * float(settings["max_stake_per_trade_usdc"]) * float(settings["days_per_month"])
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
        "registered_amendments_at_utc": REGISTERED_AMENDMENTS_AT_UTC,
        "question": "Can this bot generate $100/month profit?",
        "target_monthly_profit_usdc": target,
        "gates": {
            "A_edge_exists": {
                "registered_rule": verdict_gate_definition("A_edge_exists")["rule"],
                "state": gate_a,
                "reason": gate_a_reason,
                "independent_market_units": n_units,
                "settled_finals_total": finals_total,
                "minimum_final_samples": minimum_samples,
                "unit_mean_final_clv": mean_final_clv,
                "units_beating_close": beaten_units,
                "sign_test_p": round(sign_p, 6) if sign_p is not None else None,
                "sign_test_alpha": alpha,
                "note": "units cluster finals by market AND merge markets sharing a fixture (team + settlement date), so neither correlated tokens nor same-match side markets can inflate significance.",
            },
            "B_edge_survives_costs": {
                "registered_rule": verdict_gate_definition("B_edge_survives_costs")["rule"],
                "state": gate_b,
                "reason": gate_b_reason,
                "exit_cost_haircut_per_dollar": exit_haircut,
                "adverse_selection_haircut_per_dollar": adverse_haircut,
                "taker_fee_rate": taker_fee_rate,
                "mean_taker_fee_per_dollar": mean_taker_fee,
                "net_edge_per_dollar": round(net_edge_per_dollar, 6) if net_edge_per_dollar is not None else None,
                "note": "entry-side costs are embedded in shadow fills; adverse-selection charge covers fill-quality bias shadow entries cannot experience; taker fees per live Polymarket fee schedule (makers pay none - see WO-36).",
            },
            "C_scale_feasible": {
                "registered_rule": verdict_gate_definition("C_scale_feasible")["rule"],
                "state": gate_c,
                "reason": gate_c_reason,
                "regime": "world_cup_2026_window",
                "regime_note": "turnover observed during a major-event liquidity regime; a YES is conditional on event-regime flow, not steady state.",
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
            "World Cup focus finals settle 2026-07-09 through 2026-07-19; each settled market adds a Gate A unit. "
            "If the final read is underpowered (< 12 units) the window extends exactly once, 2026-07-20 through "
            "2026-08-19, regime post_wc_2026, then resolves terminally (amendment 7; see "
            "registered_extension_protocol). A YES additionally requires the existing governed "
            "paper-confirmation round trips before any run-rate claim."
        ),
        "registered_extension_protocol": REGISTERED_EXTENSION_PROTOCOL,
        "proof_questions_seen": bool(proof),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    write_json(cfg.governance_root / VERDICT_FILENAME, payload)
    return payload
