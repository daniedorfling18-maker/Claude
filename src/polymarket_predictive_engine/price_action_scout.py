from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from .config import EngineConfig, load_config
from .strategy_v2_round_trip import _candidate_round_trip, _cohort_rows
from .utils import now_utc, parse_timestamp, read_csv_rows, safe_float, write_csv, write_json

OUTPUT_DIRNAME = "polymarket_price_action"
ENTRY_LEDGER_FILE = "price_action_scout_entries.csv"
ROUND_TRIP_FILE = "price_action_scout_round_trip_evidence.csv"
COHORT_FILE = "price_action_scout_cohort_evidence.csv"
SUMMARY_JSON = "price_action_scout_summary.json"

ENTRY_FIELDS = [
    "source",
    "signal_cohort",
    "family",
    "market_slug",
    "question",
    "outcome",
    "token_id",
    "first_seen_at_utc",
    "last_selected_at_utc",
    "entry_price",
    "stake_usdc",
    "quantity",
    "target_action",
    "target_score",
    "edge_lower_bound",
    "liquidity",
    "spread",
    "relative_spread",
    "time_to_close_hours",
    "candidate_reason",
]

ROUND_TRIP_FIELDS = [
    "source",
    "target_action",
    "candidate_reason",
    "edge_lower_bound",
    "signal_cohort",
    "family",
    "market_slug",
    "outcome",
    "token_id",
    "entry_time_utc",
    "entry_price",
    "stake_usdc",
    "quantity",
    "observations",
    "latest_time_utc",
    "latest_bid",
    "latest_ask",
    "latest_midpoint",
    "latest_spread",
    "max_bid",
    "max_bid_time_utc",
    "min_bid",
    "min_bid_time_utc",
    "exit_time_utc",
    "exit_price",
    "exit_reason",
    "round_trip_status",
    "realized_pnl_usdc",
    "realized_roi",
    "realized_monthly_run_rate_usdc",
    "mark_pnl_usdc",
    "mark_roi",
    "mark_monthly_run_rate_usdc",
    "take_profit_return",
    "stop_loss_return",
    "min_profit_usdc",
    "settlement_status",
]


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    return cfg.raw.get("price_action_scout", {}) if isinstance(cfg.raw.get("price_action_scout"), dict) else {}


def _strategy_settings(cfg: EngineConfig) -> dict[str, Any]:
    return cfg.raw.get("strategy_v2", {}) if isinstance(cfg.raw.get("strategy_v2"), dict) else {}


def _boolish(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _token_id(row: dict[str, Any]) -> str:
    return str(row.get("token_id") or row.get("asset_id") or row.get("outcome_token_id") or "").strip()


def _fmt_time(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _entry_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("source") or ""),
        _token_id(row),
        str(row.get("market_slug") or ""),
        str(row.get("outcome") or ""),
    )


def _match_key(row: dict[str, Any]) -> tuple[str, str]:
    return (_norm(row.get("market_slug")), _norm(row.get("outcome") or row.get("selection")))


def _watchlist_index(rows: list[dict[str, str]]) -> dict[tuple[str, str], dict[str, str]]:
    index: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        key = _match_key(row)
        if not key[0] or not key[1]:
            continue
        current = index.get(key)
        if current is None or (safe_float(row.get("liquidity")) or 0.0) > (safe_float(current.get("liquidity")) or 0.0):
            index[key] = row
    return index


def _scout_round_trip_settings(cfg: EngineConfig) -> dict[str, Any]:
    scout = _settings(cfg)
    strategy = _strategy_settings(cfg)
    stake = safe_float(scout.get("stake_usdc")) or safe_float(strategy.get("forward_evidence_stake_usdc")) or 10.0
    return {
        "forward_evidence_stake_usdc": stake,
        "round_trip_take_profit_return": safe_float(scout.get("take_profit_return"))
        or safe_float(strategy.get("round_trip_take_profit_return"))
        or 0.08,
        "round_trip_stop_loss_return": safe_float(scout.get("stop_loss_return"))
        or safe_float(strategy.get("round_trip_stop_loss_return"))
        or 0.06,
        "round_trip_min_profit_usdc": safe_float(scout.get("min_profit_usdc"))
        or safe_float(strategy.get("round_trip_min_profit_usdc"))
        or 0.25,
        "round_trip_min_closed_trades": safe_float(scout.get("min_closed_trades"))
        or safe_float(strategy.get("round_trip_min_closed_trades"))
        or 5,
        "round_trip_min_roi": safe_float(scout.get("min_roi")) or safe_float(strategy.get("round_trip_min_roi")) or 0.03,
        "round_trip_min_win_rate": safe_float(scout.get("min_win_rate")) or safe_float(strategy.get("round_trip_min_win_rate")) or 0.55,
        "round_trip_min_monthly_run_rate_usdc": safe_float(scout.get("min_monthly_run_rate_usdc"))
        or safe_float(strategy.get("round_trip_min_monthly_run_rate_usdc"))
        or 20.0,
        "round_trip_max_single_market_positive_pnl_share": safe_float(scout.get("max_single_market_positive_pnl_share"))
        or safe_float(strategy.get("round_trip_max_single_market_positive_pnl_share"))
        or 0.35,
    }


def _valid_book(row: dict[str, Any], settings: dict[str, Any]) -> bool:
    min_liquidity = float(safe_float(settings.get("min_liquidity")) or 100.0)
    max_spread = float(safe_float(settings.get("max_spread")) or 0.04)
    max_relative_spread = float(safe_float(settings.get("max_relative_spread")) or 0.15)
    ask = safe_float(row.get("best_ask") or row.get("entry_price") or row.get("gamma_price"))
    midpoint = safe_float(row.get("midpoint"))
    spread = safe_float(row.get("spread"))
    relative_spread = safe_float(row.get("relative_spread"))
    if relative_spread is None and spread is not None and midpoint is not None and midpoint > 0:
        relative_spread = spread / midpoint
    liquidity = safe_float(row.get("liquidity"))
    return bool(
        _token_id(row)
        and ask is not None
        and 0 < ask < 1
        and liquidity is not None
        and liquidity >= min_liquidity
        and spread is not None
        and spread <= max_spread
        and relative_spread is not None
        and relative_spread <= max_relative_spread
    )


def _relative_spread(row: dict[str, Any]) -> float | None:
    relative_spread = safe_float(row.get("relative_spread"))
    if relative_spread is not None:
        return relative_spread
    spread = safe_float(row.get("spread"))
    midpoint = safe_float(row.get("midpoint"))
    if spread is not None and midpoint is not None and midpoint > 0:
        return spread / midpoint
    entry_price = safe_float(row.get("entry_price") or row.get("best_ask"))
    if spread is not None and entry_price is not None and entry_price > 0:
        return spread / entry_price
    return None


def _entry_within_current_policy(row: dict[str, Any], settings: dict[str, Any]) -> bool:
    min_liquidity = float(safe_float(settings.get("min_liquidity")) or 100.0)
    max_spread = float(safe_float(settings.get("max_spread")) or 0.04)
    max_relative_spread = float(safe_float(settings.get("max_relative_spread")) or 0.15)
    entry_price = safe_float(row.get("entry_price") or row.get("best_ask"))
    liquidity = safe_float(row.get("liquidity"))
    spread = safe_float(row.get("spread"))
    relative_spread = _relative_spread(row)
    return bool(
        _token_id(row)
        and entry_price is not None
        and 0 < entry_price < 1
        and liquidity is not None
        and liquidity >= min_liquidity
        and spread is not None
        and spread <= max_spread
        and relative_spread is not None
        and relative_spread <= max_relative_spread
    )


def _entry_from_watchlist(row: dict[str, Any], *, source: str, signal_cohort: str, reason: str, settings: dict[str, Any]) -> dict[str, Any] | None:
    if not _valid_book(row, settings):
        return None
    entry_price = safe_float(row.get("best_ask")) or safe_float(row.get("midpoint")) or safe_float(row.get("gamma_price"))
    if entry_price is None or entry_price <= 0:
        return None
    stake = float(safe_float(settings.get("stake_usdc")) or 10.0)
    selected_at = parse_timestamp(row.get("timestamp")) or datetime.now(timezone.utc)
    return {
        "source": source,
        "signal_cohort": signal_cohort,
        "family": row.get("family") or row.get("category") or "unknown",
        "market_slug": row.get("market_slug") or "",
        "question": row.get("question") or "",
        "outcome": row.get("outcome") or row.get("selection") or "",
        "token_id": _token_id(row),
        "first_seen_at_utc": _fmt_time(selected_at),
        "last_selected_at_utc": _fmt_time(selected_at),
        "entry_price": entry_price,
        "stake_usdc": stake,
        "quantity": stake / entry_price,
        "target_action": row.get("target_action") or row.get("profit_sprint_target_action") or "",
        "target_score": row.get("target_score") or row.get("profit_sprint_target_score") or "",
        "edge_lower_bound": row.get("edge_lower_bound") or "",
        "liquidity": row.get("liquidity") or "",
        "spread": row.get("spread") or "",
        "relative_spread": "" if _relative_spread(row) is None else _relative_spread(row),
        "time_to_close_hours": row.get("time_to_close_hours") or "",
        "candidate_reason": reason,
    }


def _profit_sprint_candidates(
    *,
    profit_targets: list[dict[str, str]],
    watchlist_by_key: dict[tuple[str, str], dict[str, str]],
    settings: dict[str, Any],
) -> list[dict[str, Any]]:
    actions = settings.get("target_actions") or ["SHADOW_LABEL_GATE", "SHADOW_EDGE_WATCH", "WAIT_ACTIVE_WINDOW"]
    allowed_actions = {str(item).strip() for item in actions if str(item).strip()}
    rows: list[dict[str, Any]] = []
    for target in profit_targets:
        action = str(target.get("target_action") or "").strip()
        if action not in allowed_actions:
            continue
        edge = safe_float(target.get("edge_lower_bound"))
        if edge is None or edge <= 0:
            continue
        watch = watchlist_by_key.get(_match_key(target))
        if not watch:
            continue
        family = str(target.get("signal_cohort") or watch.get("family") or "unknown")
        merged = {**watch, **target}
        entry = _entry_from_watchlist(
            merged,
            source="profit_sprint_target",
            signal_cohort=f"price_action_scout|profit_sprint|{family}",
            reason=str(target.get("reason") or "positive-edge target blocked by research gates"),
            settings=settings,
        )
        if entry:
            rows.append(entry)
    return rows


def _fast_feedback_candidates(watchlist: list[dict[str, str]], settings: dict[str, Any]) -> list[dict[str, Any]]:
    if not _boolish(settings.get("include_fast_feedback_liquidity", True)):
        return []
    rows: list[dict[str, Any]] = []
    for row in watchlist:
        if not _boolish(row.get("fast_feedback_liquidity_candidate")):
            continue
        family = str(row.get("family") or "unknown")
        entry = _entry_from_watchlist(
            row,
            source="liquidity_fast_feedback",
            signal_cohort=f"price_action_scout|fast_liquidity|{family}",
            reason=str(row.get("fast_feedback_filter_reason") or "liquid tight book closes soon"),
            settings=settings,
        )
        if entry:
            rows.append(entry)
    return rows


def _merge_entries(existing: list[dict[str, str]], new_entries: list[dict[str, Any]], *, max_new: int) -> tuple[list[dict[str, Any]], int]:
    merged: dict[tuple[str, str, str, str], dict[str, Any]] = {(_entry_key(row)): dict(row) for row in existing}
    added = 0
    for row in new_entries[:max_new]:
        key = _entry_key(row)
        if not key[1]:
            continue
        if key in merged:
            merged[key]["last_selected_at_utc"] = row.get("last_selected_at_utc") or merged[key].get("last_selected_at_utc", "")
            merged[key]["liquidity"] = row.get("liquidity") or merged[key].get("liquidity", "")
            merged[key]["spread"] = row.get("spread") or merged[key].get("spread", "")
            merged[key]["time_to_close_hours"] = row.get("time_to_close_hours") or merged[key].get("time_to_close_hours", "")
            continue
        merged[key] = row
        added += 1
    rows = list(merged.values())
    rows.sort(key=lambda row: (str(row.get("first_seen_at_utc") or ""), str(row.get("source") or ""), str(row.get("market_slug") or "")))
    return rows, added


def _source_priority(row: dict[str, Any]) -> int:
    return 2 if str(row.get("source") or "") == "profit_sprint_target" else 1


def _candidate_priority(row: dict[str, Any]) -> tuple[int, float, float, float, float]:
    return (
        _source_priority(row),
        safe_float(row.get("target_score")) or 0.0,
        safe_float(row.get("edge_lower_bound")) or 0.0,
        safe_float(row.get("liquidity")) or 0.0,
        -(safe_float(row.get("spread")) or 999.0),
    )


def _dedupe_candidates_by_token(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for row in sorted(rows, key=_candidate_priority, reverse=True):
        token_id = _token_id(row)
        if not token_id:
            continue
        if token_id not in selected:
            selected[token_id] = row
    return list(selected.values())


def _websocket_index(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    by_token: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        token_id = str(row.get("asset_id") or row.get("token_id") or row.get("outcome_token_id") or "").strip()
        if token_id:
            by_token[token_id].append(row)
    for token_rows in by_token.values():
        token_rows.sort(key=lambda row: parse_timestamp(row.get("collected_at_utc")) or datetime.min.replace(tzinfo=timezone.utc))
    return by_token


def build_price_action_scout(cfg: EngineConfig) -> dict[str, Any]:
    """Persist and evaluate fast, shadow-only bid/ask price-action candidates."""
    settings = _settings(cfg)
    if str(settings.get("enabled", True)).strip().lower() in {"0", "false", "no"}:
        summary = {"status": "disabled", "generated_at_utc": now_utc(), "paper_trading_invoked": False, "live_trading_invoked": False}
        write_json(cfg.output_root / OUTPUT_DIRNAME / SUMMARY_JSON, summary)
        return summary

    out_dir = cfg.output_root / OUTPUT_DIRNAME
    watchlist = read_csv_rows(cfg.output_root / "polymarket_liquidity_discovery" / "liquidity_watchlist.csv")
    profit_targets = read_csv_rows(cfg.governance_root / "profit_sprint_targets.csv")
    existing = read_csv_rows(out_dir / ENTRY_LEDGER_FILE)
    watchlist_by_key = _watchlist_index(watchlist)

    new_candidates: list[dict[str, Any]] = []
    if _boolish(settings.get("include_profit_sprint_targets", True)):
        new_candidates.extend(_profit_sprint_candidates(profit_targets=profit_targets, watchlist_by_key=watchlist_by_key, settings=settings))
    new_candidates.extend(_fast_feedback_candidates(watchlist, settings))
    new_candidates = _dedupe_candidates_by_token(new_candidates)
    new_candidates.sort(
        key=lambda row: (
            str(row.get("source") or "") == "profit_sprint_target",
            safe_float(row.get("target_score")) or 0.0,
            safe_float(row.get("edge_lower_bound")) or 0.0,
            safe_float(row.get("liquidity")) or 0.0,
        ),
        reverse=True,
    )
    max_new = int(safe_float(settings.get("max_new_entries_per_run")) or 24)
    entries, added = _merge_entries(existing, new_candidates, max_new=max_new)
    write_csv(out_dir / ENTRY_LEDGER_FILE, entries, fieldnames=ENTRY_FIELDS)
    policy_eligible_entries = [row for row in entries if _entry_within_current_policy(row, settings)]
    eligible_entries = _dedupe_candidates_by_token(policy_eligible_entries)

    websocket_rows = read_csv_rows(cfg.output_root / "polymarket_training" / "websocket_market_features.csv")
    by_token = _websocket_index(websocket_rows)
    round_trip_settings = _scout_round_trip_settings(cfg)
    round_trip_rows: list[dict[str, Any]] = []
    for entry in eligible_entries:
        row = _candidate_round_trip(entry, websocket_rows, by_token=by_token, settings=round_trip_settings)
        row["source"] = entry.get("source", "")
        row["target_action"] = entry.get("target_action", "")
        row["candidate_reason"] = entry.get("candidate_reason", "")
        row["edge_lower_bound"] = entry.get("edge_lower_bound", "")
        round_trip_rows.append(row)
    round_trip_rows.sort(
        key=lambda row: (
            str(row.get("round_trip_status") or "").startswith("closed_"),
            safe_float(row.get("mark_pnl_usdc")) or -999.0,
            safe_float(row.get("edge_lower_bound")) or -999.0,
        ),
        reverse=True,
    )
    cohort_rows = _cohort_rows(round_trip_rows, round_trip_settings)
    write_csv(out_dir / ROUND_TRIP_FILE, round_trip_rows, fieldnames=ROUND_TRIP_FIELDS)
    write_csv(out_dir / COHORT_FILE, cohort_rows)

    closed = [row for row in round_trip_rows if str(row.get("round_trip_status") or "").startswith("closed_")]
    observed = [row for row in round_trip_rows if int(safe_float(row.get("observations")) or 0) > 0]
    total_stake = sum(float(safe_float(row.get("stake_usdc")) or 0.0) for row in round_trip_rows)
    closed_stake = sum(float(safe_float(row.get("stake_usdc")) or 0.0) for row in closed)
    realized_pnl = sum(float(safe_float(row.get("realized_pnl_usdc")) or 0.0) for row in closed)
    mark_pnl = sum(float(safe_float(row.get("mark_pnl_usdc")) or 0.0) for row in round_trip_rows)
    review_candidates = sum(1 for row in cohort_rows if str(row.get("price_action_review_candidate")).lower() == "true")
    summary = {
        "status": "computed",
        "generated_at_utc": now_utc(),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
        "entry_file": str(out_dir / ENTRY_LEDGER_FILE),
        "candidate_file": str(out_dir / ROUND_TRIP_FILE),
        "cohort_file": str(out_dir / COHORT_FILE),
        "watchlist_rows": len(watchlist),
        "profit_sprint_target_rows": len(profit_targets),
        "new_entries": added,
        "ledger_entries": len(entries),
        "policy_eligible_entries": len(policy_eligible_entries),
        "policy_excluded_entries": len(entries) - len(policy_eligible_entries),
        "deduped_evidence_entries": len(eligible_entries),
        "duplicate_policy_eligible_entries": len(policy_eligible_entries) - len(eligible_entries),
        "round_trip_candidates": len(round_trip_rows),
        "observed_candidates": len(observed),
        "closed_trades": len(closed),
        "open_trades": sum(1 for row in round_trip_rows if str(row.get("round_trip_status") or "") == "open_marked"),
        "take_profit_exits": sum(1 for row in closed if str(row.get("round_trip_status") or "") == "closed_take_profit"),
        "stop_loss_exits": sum(1 for row in closed if str(row.get("round_trip_status") or "") == "closed_stop_loss"),
        "price_action_review_candidates": review_candidates,
        "total_stake_usdc": total_stake,
        "closed_stake_usdc": closed_stake,
        "realized_pnl_usdc": realized_pnl,
        "realized_roi": realized_pnl / closed_stake if closed_stake > 0 else 0.0,
        "total_mark_pnl_usdc": mark_pnl,
        "mark_roi": mark_pnl / total_stake if total_stake > 0 else 0.0,
        "decision": "ready_for_human_price_action_review_settlement_still_required"
        if review_candidates
        else "collect_more_closed_price_action_scout_evidence"
        if observed
        else "collect_more_websocket_observations",
        "cohort_summary": cohort_rows[:25],
        "top_candidates": round_trip_rows[:25],
        "warnings": {
            "shadow_only": True,
            "not_paper_or_live_trading": True,
            "settlement_governance_still_required": True,
            "scout_candidates_are_not_promoted_signals": True,
            "uses_bid_for_exit_not_midpoint": True,
        },
    }
    write_json(out_dir / SUMMARY_JSON, summary)
    return summary


def run(config_path: str = "polymarket_predictive_config.example.yaml") -> dict[str, Any]:
    cfg = load_config(config_path)
    return build_price_action_scout(cfg)
