"""Pre-committed maker live-test decision policy (WO-50).

This module mechanises the registered 2026-07-10 maker-lane funding policy.
It writes an indicated human action only; it never places, amends, cancels, or
authorises orders.  The policy constants are frozen by default and future
changes may only tighten with a dated amendment.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from pathlib import Path
import math
import re
from typing import Any

from .config import EngineConfig, load_config
from .executor_ops_monitor import EXECUTION_LEDGER, EXECUTOR_HEARTBEAT
from .risk import shrunk_kelly_fraction
from .stage_ticket_eligibility import CONDITION_ORDER, evaluate_stage_ticket_eligibility
from .utils import boolish, now_utc, parse_timestamp, read_csv_rows, read_json, safe_float, write_json, write_text_atomic

REGISTERED_AT_UTC = "2026-07-10T00:00:00Z"
KELLY_FULL_WEIGHT_DAYS_FLOOR = 20
REGISTERED_KILL_INPUT_MAX_AGE_SECONDS = 30 * 60
# Owner decision 2026-07-17 (owner-approved PR; an interpretation, not a
# threshold change): the registered stage instrument is ONE minimum-size
# quote on the indicated market. The WO-59 quarter-Kelly overlay caps every
# size-up ABOVE the venue minimum but does not veto the minimum-size stage
# entry — at short histories the shrunk fraction is deliberately near zero,
# and reading it as an entry veto would deadlock the registered fund_*
# actions the policy table indicates. Tighten-only in effect: the overlay
# still binds all sizing above the venue minimum.
KELLY_OVERLAY_INTERPRETATION = (
    "Owner decision 2026-07-17: binding_capital_usd caps size-ups above the venue "
    "minimum; the minimum-size stage quote indicated by a fund_* action is the "
    "registered floor and is not vetoed by the overlay."
)

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "decision_date": "2026-07-20",
    "composition_stable_days": 7,
    "composition_required_recurrence": 4,
    "below_target_review_runs": 7,
    "below_target_review_threshold": 3,
    "stage0_capital_usd": 100.0,
    "stage1_capital_usd": 250.0,
    "stage2_capital_usd": 500.0,
    "stage1_min_consecutive_days": 7,
    "stage2_additional_days": 14,
    "stage2_reward_realisation_multiple": 0.5,
    "kill_cumulative_net_score_usd": -25.0,
    "kill_single_day_net_usd": -15.0,
    "kill_fill_overrun_days": 2,
    "fill_alert_multiple": 2.0,
    # WO-86 (registered 2026-07-14): once a live stage is configured or
    # observed, blind safety data forces STOP. Configuration may shorten this
    # maximum but can never widen it.
    "kill_input_max_age_seconds": REGISTERED_KILL_INPUT_MAX_AGE_SECONDS,
    "kelly_fraction_cap": 1.0,
    "quarter_kelly_multiplier": 0.25,
    # 2026-07-11 WO-59 tighten-only amendment: short maker histories are
    # shrunk toward the no-edge prior. Overrides may lengthen, never shorten,
    # the 20-observation evidence floor.
    "kelly_full_weight_days": KELLY_FULL_WEIGHT_DAYS_FLOOR,
}

REGISTERED_ACTION_POLICY: list[dict[str, Any]] = [
    {
        "priority": 1,
        "id": "kill_criteria",
        "condition": "Any registered kill criterion is triggered.",
        "indicated_action": "stop_quoting_review_before_resume",
        "reason": "one_or_more_registered_kill_criteria_triggered",
    },
    {
        "priority": 2,
        "id": "post_gate_below_target",
        "condition": "M-A and M-B pass, but net is below target on more than the registered recent-run threshold.",
        "indicated_action": "maker_lane_not_supported_program_review",
        "reason": "net_below_target_on_more_than_three_of_last_seven_runs_after_gates_reachable",
    },
    {
        "priority": 3,
        "id": "gates_pass_composition_stable",
        "condition": "M-A and M-B pass and the top portfolio market meets the registered recurrence floor.",
        "indicated_action": "fund_100_min_size_single_calmest_market",
        "reason": "M_A_and_M_B_pass_with_stable_top_portfolio_market",
    },
    {
        "priority": 4,
        "id": "gates_pass_composition_churning",
        "condition": "M-A and M-B pass but portfolio composition is not stable.",
        "indicated_action": "fund_100_but_only_most_recurrent_market_half_target",
        "reason": "M_A_and_M_B_pass_but_portfolio_composition_churning",
    },
    {
        "priority": 5,
        "id": "decision_date_pending_gate",
        "condition": "The registered decision date is reached while M-A or M-B remains pending.",
        "indicated_action": "defer_funding_continue_study",
        "reason": "policy_decision_date_reached_with_pending_maker_gate",
    },
    {
        "priority": 6,
        "id": "study_missing",
        "condition": "The maker-carry study artifact is absent.",
        "indicated_action": "collect_maker_carry_study",
        "reason": "maker_carry_study_artifact_missing",
    },
    {
        "priority": 7,
        "id": "pre_decision_evidence_pending",
        "condition": "No earlier policy row binds before the registered decision date.",
        "indicated_action": "continue_study_until_policy_date",
        "reason": "maker_gates_not_yet_passed_before_registered_decision_date",
    },
]

KILL_CRITERIA_REGISTRATION: list[dict[str, Any]] = [
    {
        "id": "kill_data_stale",
        "rule": "Stop when a configured or observed live stage has no kill-input observation inside the registered freshness maximum.",
        "threshold_config_key": "kill_input_max_age_seconds",
    },
    {
        "id": "cumulative_real_net_score",
        "rule": "Stop when cumulative real net score is at or below the registered loss floor.",
        "threshold_config_key": "kill_cumulative_net_score_usd",
    },
    {
        "id": "single_day_net_score",
        "rule": "Stop when any single UTC-day net score is at or below the registered loss floor.",
        "threshold_config_key": "kill_single_day_net_usd",
    },
    {
        "id": "fills_outrunning_model_two_days",
        "rule": "Stop when realised fills outrun the model for the registered number of days.",
        "threshold_config_key": "kill_fill_overrun_days",
    },
    {
        "id": "uma_dispute_inventory",
        "rule": "Stop on a UMA dispute affecting held inventory.",
    },
    {
        "id": "scoreboard_stop",
        "rule": "Stop when the read-only live-test scoreboard emits its registered STOP state.",
    },
]


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = cfg.raw.get("decision_policy", {}) if isinstance(cfg.raw.get("decision_policy"), dict) else {}
    merged = dict(DEFAULT_SETTINGS)
    merged.update({key: value for key, value in raw.items() if value is not None})
    return merged


def effective_policy_settings(cfg: EngineConfig) -> dict[str, Any]:
    """Return policy settings after the same tighten-only clamps used at runtime."""

    settings = _settings(cfg)
    settings["kelly_full_weight_days"] = max(
        KELLY_FULL_WEIGHT_DAYS_FLOOR,
        int(settings.get("kelly_full_weight_days") or KELLY_FULL_WEIGHT_DAYS_FLOOR),
    )
    configured_freshness = safe_float(settings.get("kill_input_max_age_seconds"))
    settings["kill_input_max_age_seconds"] = min(
        REGISTERED_KILL_INPUT_MAX_AGE_SECONDS,
        configured_freshness
        if configured_freshness is not None and configured_freshness > 0
        else REGISTERED_KILL_INPUT_MAX_AGE_SECONDS,
    )
    return settings


def _policy_row(policy_id: str) -> dict[str, Any]:
    return next(row for row in REGISTERED_ACTION_POLICY if row["id"] == policy_id)


def _out_root(cfg: EngineConfig) -> Path:
    return cfg.output_root / "maker_carry"


def _state(mapping: dict[str, Any], *path: str) -> str:
    value: Any = mapping
    for key in path:
        if not isinstance(value, dict):
            return ""
        value = value.get(key)
    return str(value or "").strip().lower()


def _latest_top_market(study: dict[str, Any]) -> str:
    portfolio = study.get("portfolio") if isinstance(study.get("portfolio"), list) else []
    if not portfolio:
        return ""
    first = portfolio[0] if isinstance(portfolio[0], dict) else {}
    return str(first.get("condition_id") or first.get("market_id") or first.get("market_slug") or first.get("question") or "").strip()


def _history_top_market(row: dict[str, Any]) -> str:
    return str(row.get("top_portfolio_market") or row.get("top_condition_id") or row.get("top_market") or row.get("condition_id") or "").strip()


def _latest_per_utc_day(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_day: dict[str, dict[str, Any]] = {}
    for row in rows:
        stamp = str(row.get("generated_at_utc") or "")
        day = stamp[:10]
        if not day:
            continue
        by_day[day] = row
    return [by_day[day] for day in sorted(by_day)]


def _composition(study: dict[str, Any], history: list[dict[str, Any]], settings: dict[str, Any]) -> dict[str, Any]:
    window = int(settings["composition_stable_days"])
    required = int(settings["composition_required_recurrence"])
    daily = _latest_per_utc_day(history)
    recent = daily[-window:]
    top_markets = [_history_top_market(row) for row in recent if _history_top_market(row)]
    current_top = _latest_top_market(study)
    if current_top:
        top_markets.append(current_top)
    top_markets = top_markets[-window:]
    counts = Counter(top_markets)
    market, count = counts.most_common(1)[0] if counts else ("", 0)
    return {
        "window_days": window,
        "required_recurrence": required,
        "observations": len(top_markets),
        "most_recurrent_market": market,
        "most_recurrent_count": count,
        "stable": bool(count >= required),
        "recent_top_markets": top_markets,
    }


def _daily_net_rows(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = _latest_per_utc_day(history)
    out: list[dict[str, Any]] = []
    previous_net: float | None = None
    for row in rows:
        cumulative = safe_float(row.get("net_score_usd"))
        daily = safe_float(row.get("daily_net_score_usd"))
        if daily is None and cumulative is not None and previous_net is not None:
            daily = cumulative - previous_net
        if daily is None:
            daily = cumulative
        if cumulative is not None:
            previous_net = cumulative
        out.append({**row, "_daily_net_score_usd": daily, "_cumulative_net_score_usd": cumulative})
    return out


def _live_history(cfg: EngineConfig) -> list[dict[str, Any]]:
    return read_csv_rows(_out_root(cfg) / "maker_live_test_history.csv")


def _modelled_gross_per_day(cfg: EngineConfig, study: dict[str, Any]) -> float | None:
    portfolio = study.get("portfolio") if isinstance(study.get("portfolio"), list) else []
    if not portfolio:
        return None
    wanted = {str(row.get("condition_id") or "") for row in portfolio if isinstance(row, dict)}
    total = 0.0
    seen = False
    for row in read_csv_rows(_out_root(cfg) / "maker_carry_candidates.csv"):
        if str(row.get("condition_id") or "") not in wanted:
            continue
        gross = safe_float(row.get("gross_reward_usd_per_day"))
        if gross is None:
            continue
        total += gross
        seen = True
    if seen:
        return round(total, 6)
    net = safe_float(study.get("portfolio_net_carry_usd_per_day"))
    return net if net is not None and net > 0 else None


def _live_ok_day(row: dict[str, Any], settings: dict[str, Any]) -> bool:
    if str(row.get("scoreboard") or "") == "STOP_fills_outrunning_model":
        return False
    fill_alert = str(row.get("fill_alert") or "").strip().lower() in {"1", "true", "yes"}
    if fill_alert:
        return False
    modelled = safe_float(row.get("modelled_fills_per_day"))
    fills = safe_float(row.get("fills_last_24h"))
    multiple = safe_float(row.get("fill_alert_multiple")) or float(settings["fill_alert_multiple"])
    if modelled is not None and modelled > 0 and fills is not None and fills > multiple * modelled:
        return False
    cumulative = safe_float(row.get("net_score_usd"))
    return cumulative is not None and cumulative > 0


def _owner_activity_only_day(row: dict[str, Any]) -> bool:
    """WO-88 owner drills neither earn nor break ladder evidence."""

    return str(row.get("scoreboard") or "").strip().lower() == "owner_activity_only"


def _ladder_stage(
    cfg: EngineConfig,
    study: dict[str, Any],
    live_test: dict[str, Any],
    live_history: list[dict[str, Any]],
    settings: dict[str, Any],
) -> dict[str, Any]:
    daily = _daily_net_rows(live_history)
    ok_tail = 0
    owner_activity_days_ignored = 0
    for row in reversed(daily):
        if _owner_activity_only_day(row):
            owner_activity_days_ignored += 1
            continue
        if not _live_ok_day(row, settings):
            break
        ok_tail += 1
    stage1_days = int(settings["stage1_min_consecutive_days"])
    stage2_days = stage1_days + int(settings["stage2_additional_days"])
    stage = 0
    ladder_cap = float(settings["stage0_capital_usd"])
    reason = "stage_0_default_or_no_live_test"
    if ok_tail >= stage1_days:
        stage = 1
        ladder_cap = float(settings["stage1_capital_usd"])
        reason = "stage_1_live_test_positive"
    modelled_gross = _modelled_gross_per_day(cfg, study)
    latest_rewards = safe_float(live_test.get("rewards_usd_total"))
    reward_floor = None
    if ok_tail >= stage2_days and modelled_gross is not None:
        reward_floor = float(settings["stage2_reward_realisation_multiple"]) * modelled_gross * stage2_days
        if latest_rewards is not None and latest_rewards >= reward_floor:
            stage = 2
            ladder_cap = float(settings["stage2_capital_usd"])
            reason = "stage_2_extended_positive_live_test_with_reward_realisation"
    return {
        "stage": stage,
        "ladder_capital_usd": ladder_cap,
        "consecutive_live_ok_days": ok_tail,
        "owner_activity_days_ignored": owner_activity_days_ignored,
        "stage_reason": reason,
        "modelled_gross_usd_per_day": modelled_gross,
        "stage2_reward_floor_usd": None if reward_floor is None else round(reward_floor, 4),
        "rewards_usd_total": latest_rewards,
    }


def _quarter_kelly_cap(history: list[dict[str, Any]], ladder_cap: float, settings: dict[str, Any]) -> dict[str, Any]:
    daily = _latest_per_utc_day(history)
    values = [safe_float(row.get("portfolio_net_carry_usd_per_day")) for row in daily if safe_float(row.get("portfolio_net_carry_usd_per_day")) is not None]
    if len(values) < 2:
        return {
            "daily_net_mean_usd": values[0] if values else None,
            "daily_net_std_usd": None,
            "quarter_kelly_fraction": None,
            "inline_quarter_kelly_fraction": None,
            "kelly_shrinkage": None,
            "kelly_observations": len(values),
            "kelly_lineage": "risk.shrunk_kelly_fraction",
            "kelly_capital_usd": ladder_cap,
            "binding_capital_usd": ladder_cap,
            "binding_cap": "ladder_no_kelly_history",
        }
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / max(1, len(values) - 1)
    std = math.sqrt(variance)
    if std <= 0:
        raw_fraction = float(settings["kelly_fraction_cap"]) if mean > 0 else 0.0
    else:
        raw_fraction = max(0.0, mean / (std * std))
    fraction_cap = min(1.0, max(0.0, float(settings["kelly_fraction_cap"])))
    inline_quarter = min(fraction_cap, raw_fraction * float(settings["quarter_kelly_multiplier"]))
    full_weight_days = max(
        KELLY_FULL_WEIGHT_DAYS_FLOOR,
        int(settings.get("kelly_full_weight_days") or KELLY_FULL_WEIGHT_DAYS_FLOOR),
    )
    shrinkage = max(0.0, min(1.0, 1.0 - len(values) / full_weight_days))
    # 2026-07-11 WO-59 tighten-only adapter: at a 0.50 no-edge price,
    # p=0.50+f/2 has plain Kelly fraction f. The shared uncertainty shrinker
    # therefore pulls the old inline quarter-Kelly ceiling toward zero and is
    # mathematically incapable of making it larger. At the evidence floor,
    # shrinkage reaches zero and reproduces the registered inline value.
    effective_probability = 0.5 + inline_quarter / 2.0
    quarter = min(
        inline_quarter,
        shrunk_kelly_fraction(
            effective_probability,
            0.5,
            fraction_cap,
            shrinkage=shrinkage,
        ),
    )
    kelly_capital = round(ladder_cap * quarter, 4)
    binding = min(ladder_cap, kelly_capital)
    return {
        "daily_net_mean_usd": round(mean, 6),
        "daily_net_std_usd": round(std, 6),
        "quarter_kelly_fraction": round(quarter, 6),
        "inline_quarter_kelly_fraction": round(inline_quarter, 6),
        "kelly_shrinkage": round(shrinkage, 6),
        "kelly_observations": len(values),
        "kelly_full_weight_days": full_weight_days,
        "kelly_lineage": "risk.shrunk_kelly_fraction",
        "kelly_capital_usd": kelly_capital,
        "binding_capital_usd": binding,
        "binding_cap": "quarter_kelly" if kelly_capital < ladder_cap else "ladder",
    }


def _observation_timestamp(row: dict[str, Any]) -> datetime | None:
    for field in ("generated_at_utc", "updated_at_utc", "observed_at_utc", "timestamp"):
        parsed = parse_timestamp(row.get(field))
        if parsed is not None:
            return parsed
    return None


def _latest_live_observation(
    live_test: dict[str, Any], live_history: list[dict[str, Any]]
) -> tuple[dict[str, Any], str, datetime | None]:
    ranked: list[tuple[datetime, int, dict[str, Any], str]] = []
    if live_test and (stamp := _observation_timestamp(live_test)) is not None:
        ranked.append((stamp, len(live_history) + 1, live_test, "maker_live_test"))
    for index, row in enumerate(live_history):
        if (stamp := _observation_timestamp(row)) is not None:
            ranked.append((stamp, index, row, "maker_live_test_history"))
    if ranked:
        stamp, _, row, source = max(ranked, key=lambda item: (item[0], item[1]))
        return row, source, stamp
    fallback = live_test or (live_history[-1] if live_history else {})
    source = "maker_live_test" if live_test else ("maker_live_test_history" if live_history else "none")
    return fallback, source, None


def _live_stage_activation(cfg: EngineConfig, ladder: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    live_cfg = cfg.raw.get("live_trading", {}) if isinstance(cfg.raw.get("live_trading"), dict) else {}
    maker_cfg = cfg.raw.get("maker_live_test", {}) if isinstance(cfg.raw.get("maker_live_test"), dict) else {}
    if cfg.trading_mode == "live" or boolish(live_cfg.get("enabled")):
        reasons.append("live_trading_configured")
    maker_wallet = str(
        maker_cfg.get("wallet_address") or maker_cfg.get("executor_wallet_address") or ""
    ).strip()
    if boolish(maker_cfg.get("enabled")) and maker_wallet:
        reasons.append("maker_live_test_wallet_configured")
    stage = int(safe_float(ladder.get("stage")) or 0)
    if stage > 0:
        reasons.append("ladder_stage_permitted_above_zero")
    ledger_path = cfg.output_root / EXECUTION_LEDGER
    heartbeat_path = cfg.output_root / EXECUTOR_HEARTBEAT
    if ledger_path.exists():
        reasons.append("live_execution_ledger_exists")
    if heartbeat_path.exists():
        reasons.append("live_executor_heartbeat_exists")
    return {
        "active": bool(reasons),
        "reasons": reasons,
        "ladder_stage_permitted": stage,
        "execution_ledger_exists": ledger_path.exists(),
        "executor_heartbeat_exists": heartbeat_path.exists(),
    }


def _kill_input_freshness(
    live_test: dict[str, Any],
    live_history: list[dict[str, Any]],
    settings: dict[str, Any],
    activation: dict[str, Any],
    *,
    as_of: datetime,
) -> dict[str, Any]:
    _, source, latest_at = _latest_live_observation(live_test, live_history)
    threshold = float(settings["kill_input_max_age_seconds"])
    age_seconds = (
        max(0.0, (as_of - latest_at).total_seconds()) if latest_at is not None else None
    )
    active = bool(activation.get("active"))
    stale = bool(active and (age_seconds is None or age_seconds > threshold))
    if not active:
        state = "inactive_pre_live"
    elif latest_at is None:
        state = "stale_missing"
    elif stale:
        state = "stale"
    else:
        state = "fresh"
    return {
        "state": state,
        "guard_active": active,
        "guard_activation_reasons": list(activation.get("reasons") or []),
        "kill_data_stale": stale,
        "latest_observation_source": source,
        "latest_observation_utc": (
            latest_at.isoformat().replace("+00:00", "Z") if latest_at is not None else None
        ),
        "age_seconds": round(age_seconds, 3) if age_seconds is not None else None,
        "maximum_age_seconds": threshold,
        "tighten_only_maximum_seconds": REGISTERED_KILL_INPUT_MAX_AGE_SECONDS,
    }


def _kill_criteria(
    live_test: dict[str, Any],
    live_history: list[dict[str, Any]],
    settings: dict[str, Any],
    freshness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    daily = _daily_net_rows(live_history)
    # Preserve every pre-WO-86 fresh-data criterion exactly. The new
    # freshness guard is additive and may only force STOP when blind.
    latest = live_test or (daily[-1] if daily else {})
    freshness = freshness or {
        "state": "inactive_pre_live",
        "guard_active": False,
        "guard_activation_reasons": [],
        "kill_data_stale": False,
        "latest_observation_source": "none",
        "latest_observation_utc": None,
        "age_seconds": None,
        "maximum_age_seconds": float(settings["kill_input_max_age_seconds"]),
        "tighten_only_maximum_seconds": REGISTERED_KILL_INPUT_MAX_AGE_SECONDS,
    }
    cumulative_net = safe_float(latest.get("net_score_usd"))
    single_day_values = [safe_float(row.get("_daily_net_score_usd")) for row in daily if safe_float(row.get("_daily_net_score_usd")) is not None]
    fill_overrun_days = 0
    for row in daily:
        modelled = safe_float(row.get("modelled_fills_per_day"))
        fills = safe_float(row.get("fills_last_24h"))
        alert = str(row.get("fill_alert") or "").strip().lower() in {"1", "true", "yes"}
        multiple = safe_float(row.get("fill_alert_multiple")) or float(settings["fill_alert_multiple"])
        if alert or (modelled is not None and modelled > 0 and fills is not None and fills > multiple * modelled):
            fill_overrun_days += 1
    dispute = bool(
        live_test.get("uma_dispute_detected")
        or live_test.get("disputed_inventory_markets")
        or str(live_test.get("resolution_status") or "").lower() == "disputed"
    )
    criteria = {
        "kill_data_stale": {
            "triggered": bool(freshness.get("kill_data_stale")),
            "guard_active": bool(freshness.get("guard_active")),
            "age_seconds": freshness.get("age_seconds"),
            "threshold_seconds": freshness.get("maximum_age_seconds"),
            "latest_observation_utc": freshness.get("latest_observation_utc"),
            "latest_observation_source": freshness.get("latest_observation_source"),
        },
        "cumulative_real_net_score": {
            "triggered": cumulative_net is not None and cumulative_net <= float(settings["kill_cumulative_net_score_usd"]),
            "value": cumulative_net,
            "threshold": float(settings["kill_cumulative_net_score_usd"]),
        },
        "single_day_net_score": {
            "triggered": bool(single_day_values and min(single_day_values) <= float(settings["kill_single_day_net_usd"])),
            "worst_day": min(single_day_values) if single_day_values else None,
            "threshold": float(settings["kill_single_day_net_usd"]),
        },
        "fills_outrunning_model_two_days": {
            "triggered": fill_overrun_days >= int(settings["kill_fill_overrun_days"]),
            "days": fill_overrun_days,
            "threshold_days": int(settings["kill_fill_overrun_days"]),
        },
        "uma_dispute_inventory": {
            "triggered": dispute,
            "value": live_test.get("disputed_inventory_markets") or live_test.get("resolution_status") or "",
        },
        "scoreboard_stop": {
            "triggered": str(live_test.get("scoreboard") or "") == "STOP_fills_outrunning_model",
            "value": live_test.get("scoreboard", ""),
        },
    }
    registered_ids = {str(row["id"]) for row in KILL_CRITERIA_REGISTRATION}
    if set(criteria) != registered_ids:
        raise RuntimeError("kill-criteria code and registration have drifted")
    triggered = [name for name, row in criteria.items() if row["triggered"]]
    return {
        "triggered": triggered,
        "status": "triggered" if triggered else "clear",
        "kill_data_stale": bool(freshness.get("kill_data_stale")),
        "kill_input_freshness": freshness,
        "criteria": criteria,
    }


def _policy_date(settings: dict[str, Any]) -> date:
    parsed = parse_timestamp(str(settings["decision_date"]) + "T00:00:00Z")
    return (parsed or parse_timestamp("2026-07-20T00:00:00Z")).date()  # type: ignore[union-attr]


def _patch_quote_sheet(
    out_root: Path,
    policy: dict[str, Any],
    stage_ticket: dict[str, Any] | None = None,
) -> None:
    path = out_root / "maker_quote_sheet.md"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    start = "<!-- decision-policy:start -->"
    end = "<!-- decision-policy:end -->"
    stage_ticket = stage_ticket or {}
    stage_conditions = (
        stage_ticket.get("conditions")
        if isinstance(stage_ticket.get("conditions"), dict)
        else {}
    )
    block_lines = [
        start,
        "## Registered decision policy (WO-50)",
        "",
        f"- Indicated action: **{policy.get('indicated_action', '-')}**",
        f"- Ladder stage permitted: **{policy.get('ladder_stage_permitted', '-')}** "
        f"(binding capital ${policy.get('sizing', {}).get('binding_capital_usd', '-')})",
        f"- Kill criteria: **{policy.get('kill_criteria_status', {}).get('status', '-')}**",
        f"- Kill-input freshness: **{policy.get('kill_criteria_status', {}).get('kill_input_freshness', {}).get('state', '-')}** "
        f"(kill_data_stale={policy.get('kill_criteria_status', {}).get('kill_data_stale', False)}, "
        f"age={policy.get('kill_criteria_status', {}).get('kill_input_freshness', {}).get('age_seconds', '-')}s, "
        f"max={policy.get('kill_criteria_status', {}).get('kill_input_freshness', {}).get('maximum_age_seconds', '-')}s)",
        "- Policy note: indicates only; the human decides; this system never trades.",
        "",
        "### Stage-ticket eligibility (WO-99)",
        f"- State: **{stage_ticket.get('state', 'not_evaluated')}**",
        f"- First failing condition: **{stage_ticket.get('first_failing_condition') or 'none'}**",
    ]
    for name in CONDITION_ORDER:
        condition = stage_conditions.get(name) if isinstance(stage_conditions.get(name), dict) else {}
        block_lines.append(
            f"- Condition `{name}`: **{'pass' if condition.get('passed') else 'fail'}**"
        )
    notification = (
        stage_ticket.get("notification")
        if isinstance(stage_ticket.get("notification"), dict)
        else {}
    )
    block_lines.extend(
        [
            f"- Owner push: **{notification.get('delivery_state', 'not_configured')}**",
            "- Stage-ticket note: reporting and owner notification only; no gate, sizing, funding, or order path reads this artifact.",
            end,
            "",
        ]
    )
    block = "\n".join(block_lines)
    pattern = re.compile(r"<!-- decision-policy:start -->.*?<!-- decision-policy:end -->\n?", re.S)
    if start in text and end in text:
        text = pattern.sub(block, text)
    else:
        parts = text.split("\n", 2)
        text = "\n".join(parts[:2]) + "\n\n" + block + (parts[2] if len(parts) > 2 else "")
    write_text_atomic(path, text)


def run_decision_policy(cfg: EngineConfig) -> dict[str, Any]:
    settings = effective_policy_settings(cfg)
    out_root = _out_root(cfg)
    path = out_root / "decision_policy.json"
    if str(settings.get("enabled", True)).strip().lower() in {"0", "false", "no", "off"}:
        generated_at = now_utc()
        payload = {
            "status": "disabled",
            "registered_at_utc": REGISTERED_AT_UTC,
            "generated_at_utc": generated_at,
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        }
        write_json(path, payload)
        study = read_json(out_root / "maker_carry_study.json", default={}) or {}
        stage_ticket = evaluate_stage_ticket_eligibility(
            cfg,
            decision_policy=payload,
            study=study if isinstance(study, dict) else {},
            as_of=generated_at,
        )
        _patch_quote_sheet(out_root, payload, stage_ticket)
        return payload

    study = read_json(out_root / "maker_carry_study.json", default={}) or {}
    live_test = read_json(out_root / "maker_live_test.json", default={}) or {}
    carry_history = read_csv_rows(out_root / "maker_carry_history.csv")
    live_history = _live_history(cfg)
    generated_at = now_utc()
    observed_at = parse_timestamp(generated_at)
    if observed_at is None:
        raise RuntimeError("decision-policy clock did not produce a valid timestamp")
    gates = study.get("maker_gates", {}) if isinstance(study.get("maker_gates"), dict) else {}
    gate_a = _state(gates, "M_A_carry_evidence", "state") or "pending"
    gate_b = _state(gates, "M_B_adverse_realism", "state") or "pending"
    gates_pass = gate_a == "pass" and gate_b == "pass"
    target = safe_float(study.get("target_net_usd_per_day")) or float((cfg.raw.get("maker_carry_study", {}) or {}).get("target_net_usd_per_day", 3.33))
    composition = _composition(study, carry_history, settings)
    recent = _latest_per_utc_day(carry_history)[-int(settings["below_target_review_runs"]) :]
    below_target = [
        row for row in recent if (safe_float(row.get("portfolio_net_carry_usd_per_day")) is not None and float(row["portfolio_net_carry_usd_per_day"]) < target)
    ]
    ladder = _ladder_stage(cfg, study, live_test, live_history, settings)
    activation = _live_stage_activation(cfg, ladder)
    freshness = _kill_input_freshness(
        live_test,
        live_history,
        settings,
        activation,
        as_of=observed_at,
    )
    kill = _kill_criteria(live_test, live_history, settings, freshness)
    sizing = _quarter_kelly_cap(carry_history, float(ladder["ladder_capital_usd"]), settings)
    today = observed_at.date()
    decision_date = _policy_date(settings)

    if kill["triggered"]:
        policy_row = _policy_row("kill_criteria")
    elif gates_pass and len(below_target) > int(settings["below_target_review_threshold"]):
        policy_row = _policy_row("post_gate_below_target")
    elif gates_pass and composition["stable"]:
        policy_row = _policy_row("gates_pass_composition_stable")
    elif gates_pass:
        policy_row = _policy_row("gates_pass_composition_churning")
    elif today >= decision_date and (gate_a == "pending" or gate_b == "pending"):
        policy_row = _policy_row("decision_date_pending_gate")
    elif not study:
        policy_row = _policy_row("study_missing")
    else:
        policy_row = _policy_row("pre_decision_evidence_pending")
    indicated = str(policy_row["indicated_action"])
    reason = "kill_input_data_stale" if kill["kill_data_stale"] else str(policy_row["reason"])

    payload = {
        "status": "ok",
        "registered_at_utc": REGISTERED_AT_UTC,
        "generated_at_utc": generated_at,
        "inputs_snapshot": {
            "maker_carry_study_generated_at_utc": study.get("generated_at_utc"),
            "maker_live_test_generated_at_utc": live_test.get("generated_at_utc"),
            "kill_input_latest_observation_utc": freshness["latest_observation_utc"],
            "kill_input_age_seconds": freshness["age_seconds"],
            "kill_input_maximum_age_seconds": freshness["maximum_age_seconds"],
            "live_stage_guard_active": freshness["guard_active"],
            "live_stage_guard_activation_reasons": freshness["guard_activation_reasons"],
            "maker_carry_history_rows": len(carry_history),
            "maker_live_test_history_rows": len(live_history),
            "gate_a_state": gate_a,
            "gate_b_state": gate_b,
            "target_net_usd_per_day": target,
            "recent_below_target_runs": len(below_target),
        },
        "indicated_action": indicated,
        "action_reason": reason,
        "composition_stability": composition,
        "ladder_stage_permitted": ladder["stage"],
        "ladder": ladder,
        "sizing": {**sizing, "kelly_overlay_interpretation": KELLY_OVERLAY_INTERPRETATION},
        "kill_criteria_status": kill,
        "kill_data_stale": kill["kill_data_stale"],
        "policy_note": "indicates, never executes - the human decides; the system never trades",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    write_json(path, payload)
    stage_ticket = evaluate_stage_ticket_eligibility(
        cfg,
        decision_policy=payload,
        study=study,
        as_of=observed_at,
    )
    _patch_quote_sheet(out_root, payload, stage_ticket)
    return payload


def main(config_path: str) -> dict[str, Any]:
    return run_decision_policy(load_config(config_path))
