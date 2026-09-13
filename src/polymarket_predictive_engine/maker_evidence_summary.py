"""WO-173: the maker lane's figures, each carrying the evidence class it actually has.

Three artifacts describe this lane and they are not the same kind of thing. The study's
+$1.68/day is a simulation whose own honesty clause calls it an upper bound. The replay's
adverse-selection charge rests on three replay-confirmed hypothetical fills against a registered
Tier-0 floor of ten, with 77% of opportunities lacking contemporaneous book state. The scoreboard's
$0 is real money, observed read-only on a human-run wallet. Printed side by side without their
classes, the modelled number reads as the measured one.

This copies each figure beside its class, its producer and its source's age, and it says plainly
where a number is not a measurement: an adverse-selection figure below the Tier-0 floor does not
bound adverse selection, and a capacity curve that flattens because the sizing model caps it is a
modelling constraint, not a market. It recomputes no gate: `maker_gates` is copied verbatim, so
this artifact can neither pass nor fail M-A, M-B or M-C.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import EngineConfig, load_config
from .maker_carry_study import MB_TIER0_MIN_CONFIRMED_FILLS, maker_policy_settings
from .utils import parse_timestamp, safe_float, write_json

PRESENT = "present"
SOURCE_ABSENT = "source_absent"
TIMESTAMP_UNREADABLE = "timestamp_unreadable"
CURVE_READABLE = "readable"
CURVE_UNREADABLE = "curve_unreadable"

EVIDENCE_MODELED = "modeled"
EVIDENCE_LIVE = "live-real-money"
MODELLED_NOTE = "simulated maker fills; an upper bound per the study's honesty_clause"
SHARE_MODEL_NOTE = "assumed reward-sharing model, not observed receipts"
COVERAGE_NOTE = (
    "share of simulated fill opportunities with contemporaneous book state; distinct from the "
    "registered markout-window `coverage`"
)
TIER0_SENTENCE = (
    "fills below the Tier-0 floor do not bound adverse selection; the figure is a point on a "
    "distribution whose width is the min/max shown"
)
CAPACITY_NOTE = (
    "a flat curve bounded by `max_size_multiple` and by the number of portfolio markets is a "
    "modelling constraint, not a measured market capacity; a curve that never flattens says "
    "nothing about capacity"
)
REALIZED_NOTE = "read-only observation of a human-run wallet; no order path; zero to date"

OUTPUT_RELATIVE_PATH = "maker_evidence_summary.json"


def _number(value: Any) -> float | None:
    """A finite number, or None. A non-finite reading is not a measurement."""
    parsed = safe_float(value)
    return parsed if parsed is not None and math.isfinite(parsed) else None


def _integral(value: Any) -> int | None:
    """A finite number with an integral value, as an int. `10.0` reads as 10; `2.5` does not read."""
    parsed = _number(value)
    if parsed is None or parsed != int(parsed):
        return None
    return int(parsed)


def _read(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _source(path: Path, payload: dict[str, Any] | None, now: datetime) -> dict[str, Any]:
    if payload is None:
        return {"path": str(path), "generated_at_utc": None, "state": SOURCE_ABSENT, "age_seconds": None}
    stamp = payload.get("generated_at_utc")
    produced = parse_timestamp(stamp)
    # A source dated after the run clock is not readable as an age; it is reported, never guessed.
    if produced is None or produced > now:
        return {"path": str(path), "generated_at_utc": stamp, "state": TIMESTAMP_UNREADABLE, "age_seconds": None}
    return {
        "path": str(path),
        "generated_at_utc": stamp,
        "state": PRESENT,
        "age_seconds": round((now - produced).total_seconds(), 3),
    }


def _modelled(study: dict[str, Any] | None) -> dict[str, Any]:
    if study is None:
        return {
            "state": SOURCE_ABSENT,
            "net_carry_usd_per_day": None,
            "target_usd_per_day": None,
            "evidence_class": EVIDENCE_MODELED,
            "evidence_note": MODELLED_NOTE,
            "honesty_clause": None,
            "share_model": None,
            "share_model_note": SHARE_MODEL_NOTE,
            "portfolio_markets": None,
            "portfolio_capital_usd": None,
        }
    return {
        "state": PRESENT,
        "net_carry_usd_per_day": _number(study.get("portfolio_net_carry_usd_per_day")),
        "target_usd_per_day": _number(study.get("target_net_usd_per_day")),
        "evidence_class": EVIDENCE_MODELED,
        "evidence_note": MODELLED_NOTE,
        "honesty_clause": str(study["honesty_clause"]) if study.get("honesty_clause") is not None else None,
        "share_model": study.get("share_model"),
        "share_model_note": SHARE_MODEL_NOTE,
        "portfolio_markets": _integral(study.get("portfolio_markets")),
        "portfolio_capital_usd": _number(study.get("portfolio_capital_usd")),
    }


def _hypothetical_fills(replay: dict[str, Any] | None) -> dict[str, Any]:
    if replay is None:
        return {
            "state": SOURCE_ABSENT,
            "simulated_fills": None,
            "confirmed_fills": None,
            "confirmed_fill_ratio": None,
            "last_in_queue_evaluable_opportunities": None,
            "no_contemporaneous_state_opportunities": None,
            "no_contemporaneous_state_rate": None,
            "contemporaneous_book_coverage_rate": None,
            "contemporaneous_book_coverage_note": COVERAGE_NOTE,
            "replay_days": None,
            "quoting_basis": None,
        }
    rate = _number(replay.get("no_contemporaneous_state_rate"))
    coverage = round(1.0 - rate, 6) if rate is not None and 0.0 <= rate <= 1.0 else None
    return {
        "state": PRESENT,
        "simulated_fills": _integral(replay.get("simulated_fills")),
        "confirmed_fills": _integral(replay.get("confirmed_fills")),
        "confirmed_fill_ratio": _number(replay.get("confirmed_fill_ratio")),
        "last_in_queue_evaluable_opportunities": _integral(replay.get("last_in_queue_evaluable_opportunities")),
        "no_contemporaneous_state_opportunities": _integral(replay.get("no_contemporaneous_state_opportunities")),
        "no_contemporaneous_state_rate": rate,
        "contemporaneous_book_coverage_rate": coverage,
        "contemporaneous_book_coverage_note": COVERAGE_NOTE,
        "replay_days": _number(replay.get("replay_days")),
        "quoting_basis": replay.get("quoting_basis"),
    }


def _tier0_floor(cfg: EngineConfig) -> tuple[float, str]:
    """Tighten-only, mirroring the study's own rule: a config may raise the floor, never lower it."""
    configured = _number((cfg.raw.get("maker_carry_study", {}) or {}).get("mb_tier0_min_confirmed_fills"))
    if configured is None or configured < 0:
        return MB_TIER0_MIN_CONFIRMED_FILLS, "registered"
    # Build-review finding: `int(configured)` truncated a fractional floor, so a config of 12.9
    # gave a floor of 12 and 12 confirmed fills read `measured_on_12_fills` while the study's own
    # `_mb_tighter_min` — which does not truncate — called the same count insufficient. That is
    # exactly the favourable channel this block's A11 claims to have removed.
    floor = max(float(MB_TIER0_MIN_CONFIRMED_FILLS), float(configured))
    return floor, "config_tightened" if floor > MB_TIER0_MIN_CONFIRMED_FILLS else "registered"


def _adverse_selection(cfg: EngineConfig, replay: dict[str, Any] | None) -> dict[str, Any]:
    floor, source = _tier0_floor(cfg)
    block: dict[str, Any] = {
        "tier0_min_confirmed_fills": floor,
        "tier0_min_confirmed_fills_printed": int(math.ceil(floor)),
        "tier0_floor_source": source,
        "tier0_sentence": TIER0_SENTENCE,
    }
    if replay is None:
        block.update(
            {
                "state": SOURCE_ABSENT,
                "status": "unmeasured",
                "implied_usd_per_day": None,
                "markout_per_fill": None,
                "simulation_to_reality_haircut": None,
                "simulation_to_reality_haircut_status": "unmeasured",
            }
        )
        return block
    confirmed = _integral(replay.get("confirmed_fills"))
    # The comparison uses the untruncated floor; the label prints its ceiling, so a fractional
    # floor can never be printed looser than the number actually compared against.
    printed_floor = int(math.ceil(floor))
    if confirmed is None or confirmed < 1:
        status = "unmeasured"
    elif confirmed < floor:
        status = f"below_tier0_minimum_{confirmed}_of_{printed_floor}_fills"
    else:
        status = f"measured_on_{confirmed}_fills"
    markouts = replay.get("markout_per_fill") if isinstance(replay.get("markout_per_fill"), dict) else {}
    distribution = replay.get("realized_markout_distribution") if isinstance(replay.get("realized_markout_distribution"), dict) else {}
    per_horizon: dict[str, Any] = {}
    for horizon in sorted(set(markouts) | set(distribution)):
        spread = distribution.get(horizon) if isinstance(distribution.get(horizon), dict) else {}
        per_horizon[horizon] = {
            "markout": _number(markouts.get(horizon)),
            # The uncertainty statement: a point estimate on three fills is a point on this width.
            "min": _number(spread.get("min")),
            "max": _number(spread.get("max")),
            "count": _integral(spread.get("count")),
        }
    haircut = _number(replay.get("simulation_to_reality_haircut"))
    block.update(
        {
            "state": PRESENT,
            "status": status,
            "implied_usd_per_day": _number(replay.get("implied_adverse_usd_per_day")),
            "markout_per_fill": per_horizon,
            "simulation_to_reality_haircut": haircut,
            "simulation_to_reality_haircut_status": "reported" if haircut is not None else "unmeasured",
        }
    )
    return block


def _realized(live: dict[str, Any] | None) -> dict[str, Any]:
    if live is None:
        return {
            "state": SOURCE_ABSENT,
            "rewards_usd_total": None,
            "fills": None,
            "fills_last_24h": None,
            "inventory_pnl_usd": None,
            "scoreboard": None,
            "read_only": None,
            "evidence_class": EVIDENCE_LIVE,
            "evidence_note": REALIZED_NOTE,
        }
    attribution = live.get("fill_attribution") if isinstance(live.get("fill_attribution"), dict) else {}
    return {
        "state": PRESENT,
        "rewards_usd_total": _number(live.get("rewards_usd_total")),
        "fills": _integral(attribution.get("maker_test_fills")),
        "fills_last_24h": _integral(live.get("maker_test_fills_last_24h")),
        "inventory_pnl_usd": _number(live.get("inventory_pnl_usd")),
        "scoreboard": live.get("scoreboard"),
        "read_only": live.get("read_only"),
        "evidence_class": EVIDENCE_LIVE,
        "evidence_note": REALIZED_NOTE,
    }


def _capacity(cfg: EngineConfig, study: dict[str, Any] | None) -> dict[str, Any]:
    block: dict[str, Any] = {"note": CAPACITY_NOTE}
    if study is None:
        # Build-review finding: the cap and its source were read from policy settings BEFORE this
        # branch, so "the study file absent → every capacity figure null" was false of two of the
        # seven, and the test enumerated only the five that were null. With no study there is no
        # portfolio and no curve, so the cap bounds nothing and is not reported as a capacity fact.
        block.update(
            {
                "state": SOURCE_ABSENT,
                "max_size_multiple": None,
                "max_size_multiple_source": "unknown",
                "curve_state": CURVE_UNREADABLE,
                "cap_binding": None,
                "capital_curve": None,
                "curve_flat_beyond_usd": None,
                "flat_curve_is_model_bounded": None,
            }
        )
        return block
    settings = maker_policy_settings(cfg)
    cap = _integral(settings.get("max_size_multiple"))
    if cap is not None and cap < 1:
        cap = None
    block["max_size_multiple"] = cap
    block["max_size_multiple_source"] = "policy_settings" if cap is not None else "unknown"
    block["state"] = PRESENT

    portfolio = study.get("portfolio") if isinstance(study.get("portfolio"), list) else []
    readable_entries = [entry for entry in portfolio if isinstance(entry, dict) and _integral(entry.get("size_multiple")) is not None]
    if not portfolio or cap is None or len(readable_entries) != len(portfolio):
        # Build-review finding: `all(... for e in portfolio if isinstance(e, dict))` was vacuously
        # True on a non-empty list of unreadable entries, asserting that the cap binds on data that
        # says nothing. An unreadable portfolio reads null.
        block["cap_binding"] = None
    else:
        block["cap_binding"] = all(_integral(entry.get("size_multiple")) == cap for entry in readable_entries)

    curve = study.get("capital_curve") if isinstance(study.get("capital_curve"), list) else []
    block["capital_curve"] = curve
    readable = len(curve) >= 2 and all(
        isinstance(entry, dict)
        and _number(entry.get("capital_cap_usd")) is not None
        and _number(entry.get("capital_used_usd")) is not None
        and _integral(entry.get("portfolio_markets")) is not None
        for entry in curve
    )
    block["curve_state"] = CURVE_READABLE if readable else CURVE_UNREADABLE
    if not readable:
        block["curve_flat_beyond_usd"] = None
        block["flat_curve_is_model_bounded"] = None
        return block

    ordered = sorted(curve, key=lambda entry: float(_number(entry.get("capital_cap_usd"))))
    flat_from: float | None = None
    for index in range(len(ordered) - 1):
        used = _number(ordered[index].get("capital_used_usd"))
        if all(_number(later.get("capital_used_usd")) == used for later in ordered[index + 1:]):
            flat_from = float(_number(ordered[index].get("capital_cap_usd")))
            break
    block["curve_flat_beyond_usd"] = flat_from
    if flat_from is None:
        # A curve that never flattens says nothing about capacity: not false, unknown.
        block["flat_curve_is_model_bounded"] = None
        return block
    segment = [entry for entry in ordered if float(_number(entry.get("capital_cap_usd"))) >= flat_from]
    block["flat_curve_is_model_bounded"] = bool(
        all(_integral(entry.get("portfolio_markets")) <= 1 for entry in segment) or block["cap_binding"] is True
    )
    return block


def run_maker_evidence_summary(cfg: EngineConfig, *, now: datetime | None = None) -> dict[str, Any]:
    run_clock = now or datetime.now(timezone.utc)
    root = cfg.output_root / "maker_carry"
    study_path, replay_path, live_path = (
        root / "maker_carry_study.json",
        root / "maker_fill_replay.json",
        root / "maker_live_test.json",
    )
    study, replay, live = _read(study_path), _read(replay_path), _read(live_path)

    sources = {
        "maker_carry_study": _source(study_path, study, run_clock),
        "maker_fill_replay": _source(replay_path, replay, run_clock),
        "maker_live_test": _source(live_path, live, run_clock),
    }
    capacity = _capacity(cfg, study)
    sections = {
        "modelled": _modelled(study),
        "hypothetical_fills": _hypothetical_fills(replay),
        "adverse_selection": _adverse_selection(cfg, replay),
        "realized": _realized(live),
        "capacity": capacity,
    }
    healthy = (
        all(source["state"] == PRESENT for source in sources.values())
        # Registered conjunct, and today implied by the one above: a section reads `source_absent`
        # exactly when its file is missing or unparseable, which is also when its source does. It
        # is kept because the register names it and because the two could diverge if a future
        # section were derived from more than one file. The mutation harness records it as the one
        # WO-173 guard no mutation can isolate, for that reason.
        and all(section.get("state") != SOURCE_ABSENT for section in sections.values())
        and capacity["curve_state"] != CURVE_UNREADABLE
    )
    payload = {
        "work_order": "WO-173",
        "status": "ok" if healthy else "partial",
        "generated_at_utc": run_clock.strftime("%Y-%m-%dT%H:%M:%SZ"),
        **sections,
        # Copied, never recomputed: this artifact can neither pass nor fail a maker gate.
        "gates_reference": (study or {}).get("maker_gates"),
        "gates_reference_note": "copied verbatim from maker_carry_study.json; never recomputed here",
        "sources": sources,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    write_json(root / OUTPUT_RELATIVE_PATH, payload)
    return payload


def main(config_path: str) -> dict[str, Any]:
    return run_maker_evidence_summary(load_config(config_path))
