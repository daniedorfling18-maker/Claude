from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from statistics import median
from typing import Any

from .config import EngineConfig, load_config
from .utils import now_utc, parse_timestamp, read_csv_rows, safe_float, write_csv, write_json

OUTPUT_DIRNAME = "polymarket_strategy_v2"
FORWARD_EVIDENCE_FILE = "strategy_v2_forward_evidence.csv"
WEBSOCKET_FEATURES_FILE = "websocket_market_features.csv"
ROUND_TRIP_FILE = "strategy_v2_round_trip_evidence.csv"
COHORT_ROUND_TRIP_FILE = "strategy_v2_round_trip_cohort_evidence.csv"
SUMMARY_JSON = "strategy_v2_round_trip_evidence.json"

CANDIDATE_FIELDS = [
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

COHORT_FIELDS = [
    "signal_cohort",
    "family",
    "candidates",
    "closed_trades",
    "open_trades",
    "no_websocket_marks",
    "take_profit_exits",
    "stop_loss_exits",
    "win_rate",
    "stake_usdc",
    "closed_stake_usdc",
    "realized_pnl_usdc",
    "realized_roi",
    "realized_monthly_run_rate_usdc",
    "total_mark_pnl_usdc",
    "mark_roi",
    "median_mark_roi",
    "top_positive_market_pnl_share",
    "price_action_review_candidate",
    "status",
    "reason",
    "first_entry_time_utc",
    "last_mark_time_utc",
]


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    return cfg.raw.get("strategy_v2", {}) if isinstance(cfg.raw.get("strategy_v2"), dict) else {}


def _fmt_time(value: datetime | None) -> str:
    if value is None or value == datetime.min.replace(tzinfo=timezone.utc):
        return ""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_time(row: dict[str, Any]) -> datetime | None:
    return parse_timestamp(row.get("collected_at_utc") or row.get("source_timestamp") or row.get("timestamp_utc"))


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _candidate_feature_rows(
    candidate: dict[str, Any],
    websocket_rows: list[dict[str, str]],
    *,
    by_token: dict[str, list[dict[str, str]]],
) -> list[dict[str, str]]:
    token_id = str(candidate.get("token_id") or "").strip()
    if token_id and token_id in by_token:
        return by_token[token_id]

    market_slug = _norm(candidate.get("market_slug"))
    outcome = _norm(candidate.get("outcome"))
    if not market_slug or not outcome:
        return []
    return [
        row
        for row in websocket_rows
        if _norm(row.get("market_slug")) == market_slug
        and (_norm(row.get("selection")) == outcome or _norm(row.get("outcome")) == outcome)
    ]


def _first_exit(
    rows: list[dict[str, str]],
    *,
    entry_price: float,
    quantity: float,
    stake_usdc: float,
    take_profit_return: float,
    stop_loss_return: float,
    min_profit_usdc: float,
) -> tuple[dict[str, str] | None, str, float | None, float | None]:
    for row in rows:
        bid = safe_float(row.get("best_bid"))
        if bid is None:
            continue
        pnl = (bid - entry_price) * quantity
        roi = pnl / stake_usdc if stake_usdc > 0 else 0.0
        if roi >= take_profit_return and pnl >= min_profit_usdc:
            return row, "take_profit", pnl, roi
        if roi <= -stop_loss_return:
            return row, "stop_loss", pnl, roi
    return None, "", None, None


def _candidate_round_trip(
    candidate: dict[str, str],
    websocket_rows: list[dict[str, str]],
    *,
    by_token: dict[str, list[dict[str, str]]],
    settings: dict[str, Any],
) -> dict[str, Any]:
    take_profit_return = float(safe_float(settings.get("round_trip_take_profit_return")) or 0.08)
    stop_loss_return = float(safe_float(settings.get("round_trip_stop_loss_return")) or 0.06)
    min_profit_usdc = float(safe_float(settings.get("round_trip_min_profit_usdc")) or 0.25)

    entry_price = safe_float(candidate.get("entry_price"))
    stake_usdc = float(safe_float(candidate.get("stake_usdc")) or safe_float(settings.get("forward_evidence_stake_usdc")) or 10.0)
    quantity = safe_float(candidate.get("quantity"))
    if entry_price is None or entry_price <= 0 or stake_usdc <= 0:
        quantity = 0.0
        base = _base_candidate(candidate, entry_price, stake_usdc, quantity, take_profit_return, stop_loss_return, min_profit_usdc)
        return {
            **base,
            "round_trip_status": "invalid_entry",
            "exit_reason": "invalid_entry_price_or_stake",
            "settlement_status": "not_settled_price_action_only",
        }

    if quantity is None or quantity <= 0:
        quantity = stake_usdc / entry_price

    entry_time = parse_timestamp(candidate.get("first_seen_at_utc") or candidate.get("entry_time_utc"))
    matched_rows = _candidate_feature_rows(candidate, websocket_rows, by_token=by_token)
    timed_rows = [
        row
        for row in matched_rows
        if _row_time(row) is not None
        and (entry_time is None or _row_time(row) is not None and _row_time(row).astimezone(timezone.utc) >= entry_time.astimezone(timezone.utc))
        and safe_float(row.get("best_bid")) is not None
    ]
    timed_rows.sort(key=lambda row: _row_time(row) or datetime.min.replace(tzinfo=timezone.utc))

    base = _base_candidate(candidate, entry_price, stake_usdc, quantity, take_profit_return, stop_loss_return, min_profit_usdc)
    if not timed_rows:
        return {
            **base,
            "round_trip_status": "no_websocket_observations",
            "exit_reason": "no_bid_observations_after_entry",
            "settlement_status": "not_settled_price_action_only",
        }

    latest = timed_rows[-1]
    latest_time = _row_time(latest)
    latest_bid = safe_float(latest.get("best_bid"))
    latest_ask = safe_float(latest.get("best_ask"))
    latest_midpoint = safe_float(latest.get("midpoint"))
    latest_spread = safe_float(latest.get("spread"))

    bid_rows = [(row, safe_float(row.get("best_bid")), _row_time(row)) for row in timed_rows]
    bid_rows = [(row, bid, when) for row, bid, when in bid_rows if bid is not None and when is not None]
    max_row, max_bid, max_time = max(bid_rows, key=lambda item: item[1])
    min_row, min_bid, min_time = min(bid_rows, key=lambda item: item[1])
    del max_row, min_row

    exit_row, exit_reason, realized_pnl, realized_roi = _first_exit(
        timed_rows,
        entry_price=entry_price,
        quantity=quantity,
        stake_usdc=stake_usdc,
        take_profit_return=take_profit_return,
        stop_loss_return=stop_loss_return,
        min_profit_usdc=min_profit_usdc,
    )
    exit_time = _row_time(exit_row) if exit_row else None
    exit_price = safe_float(exit_row.get("best_bid")) if exit_row else None

    mark_pnl = ((latest_bid or 0.0) - entry_price) * quantity
    mark_roi = mark_pnl / stake_usdc if stake_usdc > 0 else 0.0
    mark_elapsed_hours = _elapsed_hours(entry_time, latest_time)
    mark_monthly_run_rate = (mark_pnl / mark_elapsed_hours * 24.0 * 30.0) if mark_elapsed_hours > 0 else None

    realized_monthly_run_rate = None
    status = "open_marked"
    if exit_row is not None:
        status = "closed_take_profit" if exit_reason == "take_profit" else "closed_stop_loss"
        realized_elapsed_hours = _elapsed_hours(entry_time, exit_time)
        if realized_elapsed_hours > 0 and realized_pnl is not None:
            realized_monthly_run_rate = realized_pnl / realized_elapsed_hours * 24.0 * 30.0
        mark_pnl = float(realized_pnl or 0.0)
        mark_roi = float(realized_roi or 0.0)
        mark_monthly_run_rate = realized_monthly_run_rate

    return {
        **base,
        "observations": len(timed_rows),
        "latest_time_utc": _fmt_time(latest_time),
        "latest_bid": latest_bid,
        "latest_ask": latest_ask,
        "latest_midpoint": latest_midpoint,
        "latest_spread": latest_spread,
        "max_bid": max_bid,
        "max_bid_time_utc": _fmt_time(max_time),
        "min_bid": min_bid,
        "min_bid_time_utc": _fmt_time(min_time),
        "exit_time_utc": _fmt_time(exit_time),
        "exit_price": "" if exit_price is None else exit_price,
        "exit_reason": exit_reason or "still_open",
        "round_trip_status": status,
        "realized_pnl_usdc": "" if realized_pnl is None else realized_pnl,
        "realized_roi": "" if realized_roi is None else realized_roi,
        "realized_monthly_run_rate_usdc": "" if realized_monthly_run_rate is None else realized_monthly_run_rate,
        "mark_pnl_usdc": mark_pnl,
        "mark_roi": mark_roi,
        "mark_monthly_run_rate_usdc": "" if mark_monthly_run_rate is None else mark_monthly_run_rate,
        "settlement_status": "not_settled_price_action_only",
    }


def _base_candidate(
    candidate: dict[str, str],
    entry_price: float | None,
    stake_usdc: float,
    quantity: float | None,
    take_profit_return: float,
    stop_loss_return: float,
    min_profit_usdc: float,
) -> dict[str, Any]:
    return {
        "signal_cohort": candidate.get("signal_cohort") or f"strategy_v2|{_norm(candidate.get('family')) or 'unknown'}",
        "family": candidate.get("family") or "unknown",
        "market_slug": candidate.get("market_slug") or "",
        "outcome": candidate.get("outcome") or "",
        "token_id": candidate.get("token_id") or "",
        "entry_time_utc": candidate.get("first_seen_at_utc") or "",
        "entry_price": "" if entry_price is None else entry_price,
        "stake_usdc": stake_usdc,
        "quantity": "" if quantity is None else quantity,
        "observations": 0,
        "latest_time_utc": "",
        "latest_bid": "",
        "latest_ask": "",
        "latest_midpoint": "",
        "latest_spread": "",
        "max_bid": "",
        "max_bid_time_utc": "",
        "min_bid": "",
        "min_bid_time_utc": "",
        "exit_time_utc": "",
        "exit_price": "",
        "exit_reason": "",
        "round_trip_status": "",
        "realized_pnl_usdc": "",
        "realized_roi": "",
        "realized_monthly_run_rate_usdc": "",
        "mark_pnl_usdc": "",
        "mark_roi": "",
        "mark_monthly_run_rate_usdc": "",
        "take_profit_return": take_profit_return,
        "stop_loss_return": stop_loss_return,
        "min_profit_usdc": min_profit_usdc,
        "settlement_status": "",
    }


def _elapsed_hours(start: datetime | None, end: datetime | None) -> float:
    if start is None or end is None:
        return 0.0
    return max((end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds() / 3600.0, 0.0)


def _cohort_rows(rows: list[dict[str, Any]], settings: dict[str, Any]) -> list[dict[str, Any]]:
    by_cohort: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_cohort[str(row.get("signal_cohort") or f"strategy_v2|{_norm(row.get('family')) or 'unknown'}")].append(row)

    min_closed = int(safe_float(settings.get("round_trip_min_closed_trades")) or 5)
    min_roi = float(safe_float(settings.get("round_trip_min_roi")) or 0.03)
    min_win_rate = float(safe_float(settings.get("round_trip_min_win_rate")) or 0.55)
    min_run_rate = float(safe_float(settings.get("round_trip_min_monthly_run_rate_usdc")) or 20.0)
    max_positive_share = float(safe_float(settings.get("round_trip_max_single_market_positive_pnl_share")) or 0.35)

    cohort_rows: list[dict[str, Any]] = []
    for cohort, cohort_candidates in by_cohort.items():
        family = str(cohort_candidates[0].get("family") or "unknown")
        closed = [row for row in cohort_candidates if str(row.get("round_trip_status") or "").startswith("closed_")]
        open_trades = [row for row in cohort_candidates if str(row.get("round_trip_status") or "") == "open_marked"]
        no_marks = [
            row
            for row in cohort_candidates
            if str(row.get("round_trip_status") or "") in {"no_websocket_observations", "invalid_entry"}
        ]
        total_stake = sum(float(safe_float(row.get("stake_usdc")) or 0.0) for row in cohort_candidates)
        closed_stake = sum(float(safe_float(row.get("stake_usdc")) or 0.0) for row in closed)
        realized_pnl = sum(float(safe_float(row.get("realized_pnl_usdc")) or 0.0) for row in closed)
        realized_roi = realized_pnl / closed_stake if closed_stake > 0 else 0.0
        mark_pnl = sum(float(safe_float(row.get("mark_pnl_usdc")) or 0.0) for row in cohort_candidates)
        mark_roi = mark_pnl / total_stake if total_stake > 0 else 0.0
        mark_rois = [float(value) for value in (safe_float(row.get("mark_roi")) for row in cohort_candidates) if value is not None]
        wins = sum(1 for row in closed if (safe_float(row.get("realized_pnl_usdc")) or 0.0) > 0)
        win_rate = wins / len(closed) if closed else 0.0
        take_profits = sum(1 for row in closed if str(row.get("round_trip_status") or "") == "closed_take_profit")
        stop_losses = sum(1 for row in closed if str(row.get("round_trip_status") or "") == "closed_stop_loss")
        positive_pnls = [max(0.0, float(safe_float(row.get("mark_pnl_usdc")) or 0.0)) for row in cohort_candidates]
        positive_total = sum(positive_pnls)
        top_positive_share = (max(positive_pnls) / positive_total) if positive_total > 0 else 0.0
        entry_times = [value for value in (parse_timestamp(row.get("entry_time_utc")) for row in cohort_candidates) if value]
        mark_times = [value for value in (parse_timestamp(row.get("latest_time_utc")) for row in cohort_candidates) if value]
        first_entry = min(entry_times, default=None)
        last_mark = max(mark_times, default=None)
        elapsed_hours = _elapsed_hours(first_entry, last_mark)
        realized_monthly_run_rate = (realized_pnl / elapsed_hours * 24.0 * 30.0) if elapsed_hours > 0 else None

        checks = [
            len(closed) >= min_closed,
            realized_pnl > 0,
            realized_roi >= min_roi,
            win_rate >= min_win_rate,
            realized_monthly_run_rate is not None and realized_monthly_run_rate >= min_run_rate,
            top_positive_share <= max_positive_share if positive_total > 0 else False,
        ]
        review_candidate = all(checks)
        if review_candidate:
            status = "ready_for_human_price_action_review"
            reason = "Round-trip bid/ask evidence passed price-action gates; settlement governance still applies before paper promotion."
        elif len(closed) < min_closed:
            status = "collect_more_closed_round_trips"
            reason = f"Needs >= {min_closed} closed simulated round trips; current closed count is {len(closed)}."
        elif realized_pnl <= 0 or realized_roi < min_roi:
            status = "negative_or_insufficient_realized_round_trip_pnl"
            reason = "Closed round-trip P&L/ROI does not clear the price-action review gate."
        elif win_rate < min_win_rate:
            status = "win_rate_below_review_threshold"
            reason = f"Closed round-trip win rate is below {min_win_rate:.0%}."
        elif realized_monthly_run_rate is None or realized_monthly_run_rate < min_run_rate:
            status = "run_rate_below_review_threshold"
            reason = f"Realized round-trip monthly run-rate is below ${min_run_rate:.2f}."
        elif top_positive_share > max_positive_share:
            status = "concentration_risk"
            reason = "Positive price-action P&L is too concentrated in one market."
        else:
            status = "collect_more_price_action_evidence"
            reason = "Round-trip price-action evidence remains incomplete."

        cohort_rows.append(
            {
                "signal_cohort": cohort,
                "family": family,
                "candidates": len(cohort_candidates),
                "closed_trades": len(closed),
                "open_trades": len(open_trades),
                "no_websocket_marks": len(no_marks),
                "take_profit_exits": take_profits,
                "stop_loss_exits": stop_losses,
                "win_rate": win_rate,
                "stake_usdc": total_stake,
                "closed_stake_usdc": closed_stake,
                "realized_pnl_usdc": realized_pnl,
                "realized_roi": realized_roi,
                "realized_monthly_run_rate_usdc": "" if realized_monthly_run_rate is None else realized_monthly_run_rate,
                "total_mark_pnl_usdc": mark_pnl,
                "mark_roi": mark_roi,
                "median_mark_roi": median(mark_rois) if mark_rois else 0.0,
                "top_positive_market_pnl_share": top_positive_share,
                "price_action_review_candidate": review_candidate,
                "status": status,
                "reason": reason,
                "first_entry_time_utc": _fmt_time(first_entry),
                "last_mark_time_utc": _fmt_time(last_mark),
            }
        )
    cohort_rows.sort(
        key=lambda row: (
            bool(row.get("price_action_review_candidate")),
            int(row.get("closed_trades") or 0),
            safe_float(row.get("realized_pnl_usdc")) or -999.0,
            safe_float(row.get("total_mark_pnl_usdc")) or -999.0,
        ),
        reverse=True,
    )
    return cohort_rows


def build_strategy_v2_round_trip_evidence(cfg: EngineConfig) -> dict[str, Any]:
    """Build conservative price-action evidence from Strategy V2 candidates.

    This simulates round trips using entry ask/executable price and later websocket
    bid marks. It is evidence only: no broker, paper, or live order path is invoked.
    """
    settings = _settings(cfg)
    out_dir = cfg.output_root / OUTPUT_DIRNAME
    forward_path = out_dir / FORWARD_EVIDENCE_FILE
    websocket_path = cfg.output_root / "polymarket_training" / WEBSOCKET_FEATURES_FILE

    candidates = read_csv_rows(forward_path)
    websocket_rows = read_csv_rows(websocket_path)
    by_token: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in websocket_rows:
        token_id = str(row.get("asset_id") or row.get("token_id") or row.get("outcome_token_id") or "").strip()
        if token_id:
            by_token[token_id].append(row)

    for rows in by_token.values():
        rows.sort(key=lambda row: _row_time(row) or datetime.min.replace(tzinfo=timezone.utc))

    round_trip_rows = [
        _candidate_round_trip(candidate, websocket_rows, by_token=by_token, settings=settings)
        for candidate in candidates
    ]
    round_trip_rows.sort(
        key=lambda row: (
            str(row.get("signal_cohort") or ""),
            str(row.get("market_slug") or ""),
            str(row.get("outcome") or ""),
        )
    )
    cohort_rows = _cohort_rows(round_trip_rows, settings)

    write_csv(out_dir / ROUND_TRIP_FILE, round_trip_rows, fieldnames=CANDIDATE_FIELDS)
    write_csv(out_dir / COHORT_ROUND_TRIP_FILE, cohort_rows, fieldnames=COHORT_FIELDS)

    closed = [row for row in round_trip_rows if str(row.get("round_trip_status") or "").startswith("closed_")]
    total_stake = sum(float(safe_float(row.get("stake_usdc")) or 0.0) for row in round_trip_rows)
    closed_stake = sum(float(safe_float(row.get("stake_usdc")) or 0.0) for row in closed)
    realized_pnl = sum(float(safe_float(row.get("realized_pnl_usdc")) or 0.0) for row in closed)
    mark_pnl = sum(float(safe_float(row.get("mark_pnl_usdc")) or 0.0) for row in round_trip_rows)
    observed = [row for row in round_trip_rows if int(safe_float(row.get("observations")) or 0) > 0]

    review_candidates = sum(1 for row in cohort_rows if str(row.get("price_action_review_candidate")).lower() == "true")
    summary = {
        "status": "computed",
        "generated_at_utc": now_utc(),
        "source_forward_evidence_file": str(forward_path),
        "source_websocket_features_file": str(websocket_path),
        "candidate_file": str(out_dir / ROUND_TRIP_FILE),
        "cohort_file": str(out_dir / COHORT_ROUND_TRIP_FILE),
        "forward_candidates": len(candidates),
        "websocket_feature_rows": len(websocket_rows),
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
        else "collect_more_round_trip_evidence"
        if observed
        else "collect_more_websocket_observations",
        "cohort_summary": cohort_rows[:25],
        "top_candidates": sorted(
            round_trip_rows,
            key=lambda row: safe_float(row.get("mark_pnl_usdc")) or -999.0,
            reverse=True,
        )[:25],
        "warnings": {
            "shadow_only": True,
            "not_paper_or_live_trading": True,
            "settlement_governance_still_required": True,
            "uses_bid_for_exit_not_midpoint": True,
            "price_action_is_not_settlement": True,
        },
    }
    write_json(out_dir / SUMMARY_JSON, summary)
    return summary


def run(config_path: str = "polymarket_predictive_config.example.yaml") -> dict[str, Any]:
    cfg = load_config(config_path)
    return build_strategy_v2_round_trip_evidence(cfg)
