"""Dutch-book arbitrage *execution monitor* for Polymarket negRisk events.

The one-shot ``scripts/polymarket_event_arb_pnl.py`` answers "is there an arb right now?".
A monitor answers the operational questions you actually need before committing capital:

  1. *What exactly would I execute?* -> a dry-run execution plan: one BUY per outcome leg at
     its best ask, sized to the thinnest leg's depth, with capital and locked profit spelled out.
  2. *Is it stable or fleeting?* -> repeated polling with cross-poll state diffing, so each arb is
     tagged appeared / persisting / cleared. A lock that survives several polls is executable;
     one that blinks out in a second was a stale quote.
  3. *Which ones are worth it?* -> ranked by annualised return on capital (a 3% lock that resolves
     in two years is ~1.5%/yr), with threshold alerts when an opportunity clears a bar.

The core (plan construction, ranking, state diffing, alerting) is pure and unit-tested; the network
layer is a thin Gamma/CLOB reader. **It never places an order** - every plan is dry-run and the
summary always reports ``live_trading: False`` regardless of ``trading.mode``.
"""
from __future__ import annotations

import csv
import itertools
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import requests

from polymarket_common.fees import resolve_taker_fee_schedule, taker_fee_per_share
from superbru_score_engine.betting.long_short import dutch_arb

from .config import EngineConfig, kill_switch_active, load_config
from .h2_dutch_evaluator import evaluate_h2_dutch, record_h2_scan_observations
from .utils import (
    ensure_dir,
    now_utc,
    parse_timestamp,
    read_json,
    safe_float,
    serialize_value,
    write_csv,
    write_json,
)

GAMMA_MARKETS = "https://gamma-api.polymarket.com/markets"
GAMMA_EVENTS = "https://gamma-api.polymarket.com/events"
CLOB_BOOK = "https://clob.polymarket.com/book"
_HEADERS = {"User-Agent": "polymarket-predictive-engine/1.0"}


# --------------------------------------------------------------------------- core data model
@dataclass(frozen=True)
class ArbLeg:
    """One priced outcome of a mutually-exclusive, exhaustive event basket."""

    outcome: str
    token_id: str
    best_ask: float
    ask_size: float
    taker_fee_per_share: float | None = None
    taker_fee_rate: float | None = None
    taker_fee_category: str = ""
    taker_fee_source: str = ""
    taker_fee_enabled: bool | None = None
    taker_fee_model_version: str = ""


@dataclass(frozen=True)
class ExecutionPlan:
    """A complete dry-run plan to lock a dutch-book arb across an event's outcomes."""

    event_id: str
    event_title: str
    legs: tuple[ArbLeg, ...]
    ask_sum: float
    lock_per_set: float
    is_arb: bool
    complete: bool
    executable_sets: float
    capital_usdc: float
    profit_usdc: float
    days_to_resolution: int | None
    annualised_return_on_capital: float | None
    resolution_at_utc: str = ""

    def as_row(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event": self.event_title[:70],
            "outcomes": len(self.legs),
            "ask_sum": round(self.ask_sum, 4),
            "lock_per_set": round(self.lock_per_set, 4),
            "is_arb": self.is_arb,
            "complete": self.complete,
            "executable_sets": round(self.executable_sets, 2),
            "capital_usdc": round(self.capital_usdc, 2),
            "profit_usdc": round(self.profit_usdc, 2),
            "days_to_resolution": self.days_to_resolution,
            "annualised_return_on_capital": self.annualised_return_on_capital,
        }


def annualised_return_on_capital(lock_per_set: float, ask_sum: float, days: int | None) -> float | None:
    """Profit per unit capital (``lock/ask_sum``) annualised over days-to-resolution.

    A lock ties capital up until the event resolves, so the honest comparison across
    opportunities is per-year, not the headline lock. Returns ``None`` when the horizon
    is unknown (cannot annualise) and ``0.0`` for a non-arb."""
    if ask_sum <= 0 or lock_per_set <= 0:
        return 0.0
    roi = lock_per_set / ask_sum
    if not days or days <= 0:
        return None
    return round(roi * 365.0 / days, 4)


def build_execution_plan(
    event_id: str,
    event_title: str,
    legs: Sequence[ArbLeg],
    days_to_resolution: int | None,
    *,
    min_ask_sum: float = 0.85,
    resolution_at_utc: str = "",
) -> ExecutionPlan:
    """Assemble the dry-run plan from fully-priced legs.

    Every outcome must already be priced (the caller drops events with an unpriceable leg),
    so completeness reduces to an *exhaustiveness* sanity check: a complete mutually-exclusive
    basket's asks sum to roughly 1, so an ``ask_sum`` below ``min_ask_sum`` means the outcome
    set is missing legs (not a real arb) and the giant apparent lock is an artefact. ``sets`` is
    the thinnest leg's ask depth - you can only complete as many baskets as the scarcest outcome
    allows."""
    asks = [leg.best_ask for leg in legs]
    arb = dutch_arb(asks)
    complete = len(legs) >= 2 and arb.total_cost >= min_ask_sum
    is_arb = complete and arb.is_arb
    sets = min((leg.ask_size for leg in legs), default=0.0)
    capital = arb.total_cost * sets
    profit = arb.locked_profit_per_set * sets
    ann = annualised_return_on_capital(arb.locked_profit_per_set, arb.total_cost, days_to_resolution) if is_arb else (0.0 if not is_arb else None)
    return ExecutionPlan(
        event_id=event_id,
        event_title=event_title,
        legs=tuple(legs),
        ask_sum=arb.total_cost,
        lock_per_set=arb.locked_profit_per_set,
        is_arb=is_arb,
        complete=complete,
        executable_sets=sets,
        capital_usdc=capital,
        profit_usdc=profit,
        days_to_resolution=days_to_resolution,
        annualised_return_on_capital=ann,
        resolution_at_utc=resolution_at_utc,
    )


def dry_run_orders(plan: ExecutionPlan) -> list[dict[str, Any]]:
    """The per-leg orders that *would* lock ``plan`` - one BUY per outcome at its best ask,
    sized to the executable set count. Always tagged ``dry_run`` - this is never submitted."""
    sets = plan.executable_sets
    return [
        {
            "event_id": plan.event_id,
            "outcome": leg.outcome,
            "token_id": leg.token_id,
            "side": "BUY",
            "price": round(leg.best_ask, 4),
            "size": round(sets, 2),
            "cost_usdc": round(leg.best_ask * sets, 2),
            "dry_run": True,
        }
        for leg in plan.legs
    ]


def rank_plans(plans: Iterable[ExecutionPlan], *, min_annualised: float = 0.0,
               min_lock_per_set: float = 0.0) -> list[ExecutionPlan]:
    """Executable arbs only (complete + locked), filtered by thresholds, best annualised first.

    Plans with an unknown horizon (``annualised`` is ``None``) are kept but sort last, since they
    still lock profit - we just cannot annualise them."""
    keep = [
        p for p in plans
        if p.is_arb and p.complete and p.lock_per_set >= min_lock_per_set
        and (p.annualised_return_on_capital is None or p.annualised_return_on_capital >= min_annualised)
    ]
    keep.sort(key=lambda p: (p.annualised_return_on_capital is None, -(p.annualised_return_on_capital or 0.0)))
    return keep


@dataclass
class StateDiff:
    appeared: list[str] = field(default_factory=list)
    persisting: list[str] = field(default_factory=list)
    cleared: list[str] = field(default_factory=list)


def diff_states(prev_arb_ids: Iterable[str], current_plans: Iterable[ExecutionPlan]) -> StateDiff:
    """Cross-poll transitions: which arbs are newly appeared, still standing, or just cleared.

    Persistence across polls is the executability signal - a lock that survives several polls is
    real depth; one that vanishes next poll was a stale or already-taken quote."""
    prev = set(prev_arb_ids)
    curr = {p.event_id for p in current_plans if p.is_arb}
    return StateDiff(
        appeared=sorted(curr - prev),
        persisting=sorted(curr & prev),
        cleared=sorted(prev - curr),
    )


def build_alerts(plans: Iterable[ExecutionPlan], *, alert_annualised: float, poll: int) -> list[dict[str, Any]]:
    """Alert records for arbs whose annualised return clears the bar (unknown-horizon arbs with a
    positive lock always alert, since they cannot be ranked out)."""
    alerts: list[dict[str, Any]] = []
    for p in plans:
        if not (p.is_arb and p.complete):
            continue
        ann = p.annualised_return_on_capital
        if ann is None or ann >= alert_annualised:
            alerts.append({
                "poll": poll,
                "at_utc": now_utc(),
                "event_id": p.event_id,
                "event": p.event_title[:70],
                "ask_sum": round(p.ask_sum, 4),
                "lock_per_set": round(p.lock_per_set, 4),
                "annualised_return_on_capital": ann,
                "executable_sets": round(p.executable_sets, 2),
                "capital_usdc": round(p.capital_usdc, 2),
                "profit_usdc": round(p.profit_usdc, 2),
            })
    return alerts


def clears_alert_threshold(plan: ExecutionPlan, *, alert_annualised: float) -> bool:
    """Return whether a complete arb clears the human-look alert threshold."""
    if not (plan.is_arb and plan.complete):
        return False
    ann = plan.annualised_return_on_capital
    return ann is None or ann >= alert_annualised


def _append_csv(path: Path, rows: Iterable[Mapping[str, Any]], fieldnames: Sequence[str]) -> None:
    rows = list(rows)
    if not rows:
        return
    ensure_dir(path.parent)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow({k: serialize_value(row.get(k, "")) for k in fieldnames})


# --------------------------------------------------------------------------- network layer (thin)
def _get(url: str, params: Mapping[str, Any] | None = None, timeout: int = 20) -> Any:
    resp = requests.get(url, params=params, headers=_HEADERS, timeout=timeout)  # nosec B113: public API
    resp.raise_for_status()
    return resp.json()


def _jl(value: Any) -> list:
    import json
    try:
        return json.loads(value) if isinstance(value, str) else (value or [])
    except Exception:
        return []


def _best_ask(token: str, timeout: int) -> tuple[float, float] | None:
    """Cheapest ask for a token, or ``None`` if the token has no tradeable book. A 404 means the
    outcome's book does not exist (dead/closed leg, e.g. an eliminated team) - that is an
    unbuyable leg, not a scan error, so we return ``None`` rather than letting it abort the event."""
    try:
        book = _get(CLOB_BOOK, {"token_id": token}, timeout)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None
        raise
    asks = [(safe_float(a.get("price")), safe_float(a.get("size"))) for a in book.get("asks", [])]
    asks = [(p, s) for p, s in asks if p is not None and s is not None and s > 0]
    return min(asks, key=lambda ps: ps[0]) if asks else None


def discover_event_ids(max_pages: int, timeout: int) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for off in range(0, max_pages * 100, 100):
        page = _get(GAMMA_MARKETS, {"closed": "false", "active": "true", "limit": "100",
                                    "offset": str(off), "order": "volumeNum", "ascending": "false"}, timeout)
        if not isinstance(page, list) or not page:
            break
        for m in page:
            if str(m.get("negRisk")).lower() not in ("true", "1"):
                continue
            for ev in (m.get("events") or []):
                eid = str(ev.get("id") or "")
                if eid and eid not in seen:
                    seen.add(eid)
                    ids.append(eid)
    return ids


def _event_end(end: Any) -> datetime | None:
    parsed = parse_timestamp(end)
    if parsed is None:
        return None
    return parsed.astimezone(timezone.utc)


def _days_to(end: Any, *, as_of: datetime | None = None) -> int | None:
    resolution = _event_end(end)
    if resolution is None:
        return None
    reference = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    seconds = (resolution - reference).total_seconds()
    if seconds <= 0:
        return None
    return max(1, int(seconds // 86_400))


def price_event(event_id: str, *, pause: float, max_outcomes: int, min_ask_sum: float,
                timeout: int, observed_at: datetime | None = None) -> tuple[ExecutionPlan | None, str]:
    """Fetch an event's complete outcome set and price every leg. Returns ``(plan, "ok")`` for a
    tradeable complete basket, else ``(None, reason)`` so the caller can tally *why* an event was
    skipped (not negRisk, wrong cardinality, a leg with no tokens, or an unbuyable leg)."""
    event = _get(GAMMA_EVENTS, {"id": event_id}, timeout)
    if isinstance(event, list):
        event = event[0] if event else {}
    markets = event.get("markets", []) or []
    if str(event.get("negRisk")).lower() not in ("true", "1"):
        return None, "not_negrisk"
    if not (3 <= len(markets) <= max_outcomes):
        return None, "cardinality"
    legs: list[ArbLeg] = []
    for m in markets:
        toks = _jl(m.get("clobTokenIds"))
        if not toks:
            return None, "no_tokens"
        best = _best_ask(str(toks[0]), timeout)
        if best is None:
            return None, "unpriceable_leg"          # a leg you cannot buy -> basket not lockable
        fee_schedule = resolve_taker_fee_schedule(m)
        legs.append(ArbLeg(outcome=(m.get("groupItemTitle") or m.get("question") or "")[:40],
                           token_id=str(toks[0]), best_ask=best[0], ask_size=best[1],
                           taker_fee_per_share=taker_fee_per_share(
                               price=best[0], schedule=fee_schedule
                           ),
                           taker_fee_rate=fee_schedule.rate,
                           taker_fee_category=fee_schedule.category,
                           taker_fee_source=fee_schedule.source,
                           taker_fee_enabled=fee_schedule.enabled,
                           taker_fee_model_version=fee_schedule.model_version))
        if pause:
            time.sleep(pause)
    end_value = event.get("endDate") or event.get("endDateIso") or ""
    resolution = _event_end(end_value)
    days = _days_to(end_value, as_of=observed_at)
    resolution_at_utc = (
        resolution.strftime("%Y-%m-%dT%H:%M:%SZ") if resolution is not None else ""
    )
    return build_execution_plan(
        event_id,
        event.get("title") or "",
        legs,
        days,
        min_ask_sum=min_ask_sum,
        resolution_at_utc=resolution_at_utc,
    ), "ok"


def scan_once(cfg: EngineConfig, *, max_pages: int = 4, max_events: int = 20, max_outcomes: int = 80,
              pause: float = 0.02, min_ask_sum: float = 0.85,
              timeout: int = 20, observed_at: datetime | None = None) -> tuple[list[ExecutionPlan], dict[str, int]]:
    """One full pass: discover liquid negRisk events and build a plan for each, returning the plans
    plus skip-reason counts so a "no arbs" result is interpretable (efficient pricing vs illiquid
    legs vs oversized baskets) rather than a silent zero."""
    scan_at = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    ids = discover_event_ids(max_pages, timeout)[:max_events]
    plans: list[ExecutionPlan] = []
    stats: dict[str, int] = {"discovered": len(ids), "priced_complete": 0, "skipped_not_negrisk": 0,
                             "skipped_cardinality": 0, "skipped_no_tokens": 0,
                             "skipped_unpriceable_leg": 0, "skipped_error": 0}
    for eid in ids:
        try:
            plan, reason = price_event(eid, pause=pause, max_outcomes=max_outcomes,
                                       min_ask_sum=min_ask_sum, timeout=timeout,
                                       observed_at=scan_at)
        except Exception:
            stats["skipped_error"] += 1
            continue
        if plan is not None:
            plans.append(plan)
            stats["priced_complete"] += 1
        else:
            stats[f"skipped_{reason}"] = stats.get(f"skipped_{reason}", 0) + 1
    return plans, stats


# --------------------------------------------------------------------------- monitor orchestration
def _monitor_summary(cfg: EngineConfig, *, polls: int, poll_seconds: int, min_annualised: float,
                     alert_annualised: float, latest_ranked: Sequence[ExecutionPlan],
                     latest_stats: Mapping[str, int], transitions: Sequence[dict[str, Any]],
                     alerts_total: int, polls_run: int, interrupted: bool,
                     active_arb_ids: Iterable[str] = (),
                     alert_persistence_counts: Mapping[str, int] | None = None,
                     persistent_alerts: Sequence[dict[str, Any]] = (),
                     h2_exact_scan: Mapping[str, Any] | None = None) -> dict[str, Any]:
    top = latest_ranked[0] if latest_ranked else None
    return {
        "status": "paper_analysis",
        "live_trading": False,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
        "dry_run": True,
        "generated_at_utc": now_utc(),
        "trading_mode": cfg.trading_mode,
        "kill_switch_active": kill_switch_active(),
        "polls": polls,
        "polls_run": polls_run,
        "continuous": polls <= 0,
        "interrupted": interrupted,
        "poll_seconds": poll_seconds,
        "min_annualised": min_annualised,
        "alert_annualised": alert_annualised,
        "complete_arbs_latest_poll": len(latest_ranked),
        "events_priced_complete_latest_poll": latest_stats.get("priced_complete", 0),
        "scan_stats_latest_poll": dict(latest_stats),
        "alerts_total": alerts_total,
        "transitions": list(transitions)[-10:],
        "active_arb_ids": sorted(set(active_arb_ids)),
        "alert_persistence_counts": dict(alert_persistence_counts or {}),
        "persistent_alert_count": len(persistent_alerts),
        "persistent_alerts": list(persistent_alerts),
        "h2_exact_scan": dict(h2_exact_scan or {}),
        "best_annualised_return_on_capital": top.annualised_return_on_capital if top else 0.0,
        "best_opportunity": top.as_row() if top else None,
        "best_execution_plan": dry_run_orders(top) if top else [],
        "top_opportunities": [p.as_row() for p in latest_ranked[:5]],
        "note": "Complete outcome sets only (every leg priced). Ranked by annualised return on "
                "capital since a lock ties capital up until resolution. Never places an order.",
    }


def run_dutch_arb_monitor(cfg: EngineConfig, *, polls: int = 1, poll_seconds: int = 30,
                          max_pages: int = 4, max_events: int = 20, max_outcomes: int = 80,
                          min_ask_sum: float = 0.85, min_annualised: float = 0.0,
                          alert_annualised: float = 0.10, pause: float = 0.02,
                          timeout: int = 20, max_alerts: int = 1000,
                          clock: Callable[[], datetime] | None = None) -> dict[str, Any]:
    """Poll the live book, ranking and diffing complete-set dutch-book arbs and alerting when one
    clears ``alert_annualised``. ``polls <= 0`` runs continuously until interrupted (the Docker
    mode); a bounded ``polls`` runs that many times. State (opportunities, alerts, summary) is
    persisted *every* poll so an external dashboard always sees current state, and in-memory
    buffers are bounded so a long-lived process stays flat. Pure analysis + dry-run: never trades."""
    out_dir = cfg.output_root / "polymarket_arbitrage"
    previous_summary = read_json(out_dir / "dutch_arb_monitor_summary.json", default={}) or {}
    if not isinstance(previous_summary, dict):
        previous_summary = {}
    prev_arb_ids: set[str] = set(str(v) for v in previous_summary.get("active_arb_ids", []) if str(v))
    raw_counts = previous_summary.get("alert_persistence_counts", {})
    alert_persistence_counts: dict[str, int] = {
        str(k): int(v) for k, v in raw_counts.items()
        if str(k) and str(v).replace("-", "").isdigit()
    } if isinstance(raw_counts, dict) else {}
    recent_alerts: deque[dict[str, Any]] = deque(maxlen=max_alerts)
    transitions: deque[dict[str, Any]] = deque(maxlen=200)
    latest_ranked: list[ExecutionPlan] = []
    latest_stats: dict[str, int] = {}
    active_arb_ids: set[str] = set(prev_arb_ids)
    persistent_alerts: list[dict[str, Any]] = []
    alerts_total = 0
    polls_run = 0
    interrupted = False
    h2_exact_scan: dict[str, Any] = {}
    clock_fn = clock or (lambda: datetime.now(timezone.utc))

    poll_iter: Iterable[int] = itertools.count(1) if polls <= 0 else range(1, polls + 1)
    try:
        for poll in poll_iter:
            scan_at = clock_fn()
            if scan_at.tzinfo is None:
                scan_at = scan_at.replace(tzinfo=timezone.utc)
            scan_at = scan_at.astimezone(timezone.utc).replace(microsecond=0)
            plans, latest_stats = scan_once(cfg, max_pages=max_pages, max_events=max_events,
                                            max_outcomes=max_outcomes, pause=pause,
                                            min_ask_sum=min_ask_sum, timeout=timeout,
                                            observed_at=scan_at)
            h2_exact_scan = record_h2_scan_observations(
                cfg,
                plans,
                observed_at=scan_at,
            )
            h2_evaluation = evaluate_h2_dutch(cfg, as_of=scan_at)
            h2_exact_scan = {
                **h2_exact_scan,
                "evaluation_status": h2_evaluation.get("status"),
                "independent_episodes": h2_evaluation.get("independent_episodes", 0),
                "persistent_episodes": h2_evaluation.get("persistent_episodes_current", 0),
                "next_unmet_condition": h2_evaluation.get("next_unmet_condition"),
            }
            ranked = rank_plans(plans, min_annualised=min_annualised)
            diff = diff_states(prev_arb_ids, plans)
            if diff.appeared or diff.cleared:
                transitions.append({"poll": poll, "at_utc": now_utc(), "appeared": diff.appeared,
                                    "cleared": diff.cleared, "persisting": diff.persisting})
            new_alerts = build_alerts(ranked, alert_annualised=alert_annualised, poll=poll)
            recent_alerts.extend(new_alerts)
            alerts_total += len(new_alerts)
            active_arb_ids = {p.event_id for p in plans if p.is_arb}
            alert_ids = {p.event_id for p in ranked if clears_alert_threshold(p, alert_annualised=alert_annualised)}
            alert_persistence_counts = {
                event_id: (alert_persistence_counts.get(event_id, 0) + 1)
                for event_id in sorted(alert_ids)
            }
            persistent_alerts = []
            ranked_by_id = {p.event_id: p for p in ranked}
            for event_id, consecutive_scans in sorted(alert_persistence_counts.items()):
                if consecutive_scans < 3:
                    continue
                plan = ranked_by_id.get(event_id)
                if plan is None:
                    continue
                persistent_alerts.append(
                    {
                        **plan.as_row(),
                        "consecutive_scans_above_alert": consecutive_scans,
                        "alert_annualised": alert_annualised,
                        "severity": "info",
                        "title": "Dutch-book arb basket persisted above alert threshold",
                    }
                )
            prev_arb_ids = active_arb_ids
            latest_ranked = ranked
            polls_run = poll if polls <= 0 else poll  # poll is 1-based count of completed passes

            # Persist current state every poll (latest opportunities + rolling alerts + summary).
            write_csv(out_dir / "dutch_arb_monitor_opportunities.csv", [p.as_row() for p in latest_ranked])
            alert_rows = [
                {
                    **p.as_row(),
                    "observed_at_utc": now_utc(),
                    "poll": poll,
                    "alert_annualised": alert_annualised,
                }
                for p in latest_ranked
                if clears_alert_threshold(p, alert_annualised=alert_annualised)
            ]
            _append_csv(
                out_dir / "dutch_arb_opportunities.csv",
                alert_rows,
                [
                    "observed_at_utc",
                    "poll",
                    "event_id",
                    "event",
                    "outcomes",
                    "ask_sum",
                    "lock_per_set",
                    "is_arb",
                    "complete",
                    "executable_sets",
                    "capital_usdc",
                    "profit_usdc",
                    "days_to_resolution",
                    "annualised_return_on_capital",
                    "alert_annualised",
                ],
            )
            if recent_alerts:
                write_csv(out_dir / "dutch_arb_monitor_alerts.csv", list(recent_alerts))
            summary = _monitor_summary(
                cfg, polls=polls, poll_seconds=poll_seconds, min_annualised=min_annualised,
                alert_annualised=alert_annualised, latest_ranked=latest_ranked, latest_stats=latest_stats,
                transitions=transitions, alerts_total=alerts_total, polls_run=polls_run, interrupted=False,
                active_arb_ids=active_arb_ids, alert_persistence_counts=alert_persistence_counts,
                persistent_alerts=persistent_alerts, h2_exact_scan=h2_exact_scan,
            )
            write_json(out_dir / "dutch_arb_monitor_summary.json", summary)
            write_json(out_dir / "dutch_arb_latest.json", summary)

            is_last_bounded = polls > 0 and poll >= polls
            if not is_last_bounded and poll_seconds > 0:
                time.sleep(poll_seconds)
    except KeyboardInterrupt:
        interrupted = True

    summary = _monitor_summary(cfg, polls=polls, poll_seconds=poll_seconds, min_annualised=min_annualised,
                               alert_annualised=alert_annualised, latest_ranked=latest_ranked,
                               latest_stats=latest_stats, transitions=transitions, alerts_total=alerts_total,
                               polls_run=polls_run, interrupted=interrupted,
                               active_arb_ids=active_arb_ids,
                               alert_persistence_counts=alert_persistence_counts,
                               persistent_alerts=persistent_alerts,
                               h2_exact_scan=h2_exact_scan)
    write_json(out_dir / "dutch_arb_monitor_summary.json", summary)
    write_json(out_dir / "dutch_arb_latest.json", summary)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return run_dutch_arb_monitor(load_config(config_path))
