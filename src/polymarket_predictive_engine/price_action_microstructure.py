from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from .config import EngineConfig, load_config
from .utils import now_utc, parse_timestamp, read_csv_rows, safe_float, write_csv, write_json

OUTPUT_DIRNAME = "polymarket_price_action"
TRADE_EVENTS_FILE = "microstructure_trade_events.csv"
RULE_EVIDENCE_FILE = "microstructure_rule_evidence.csv"
CURRENT_CANDIDATES_FILE = "microstructure_current_candidates.csv"
SUMMARY_JSON = "microstructure_summary.json"

EVENT_FIELDS = [
    "split",
    "token_id",
    "market_slug",
    "question",
    "family",
    "outcome",
    "entry_time_utc",
    "entry_bid",
    "entry_ask",
    "entry_midpoint",
    "entry_spread",
    "relative_spread",
    "lookback_observations",
    "bid_move_abs",
    "mid_move_abs",
    "ask_move_abs",
    "spread_change",
    "net_buy_events",
    "net_buy_size",
    "current_side",
    "current_price_change_size",
    "exit_time_utc",
    "exit_bid",
    "exit_reason",
    "pnl_usdc",
    "roi",
    "stake_usdc",
    "observations_to_exit",
]

RULE_FIELDS = [
    "rule_id",
    "rule_family",
    "parameters",
    "total_trades",
    "train_trades",
    "validation_trades",
    "total_pnl_usdc",
    "train_pnl_usdc",
    "validation_pnl_usdc",
    "total_roi",
    "train_roi",
    "validation_roi",
    "validation_win_rate",
    "validation_top_positive_token_pnl_share",
    "validation_pass",
    "status",
    "reason",
]

CURRENT_FIELDS = [
    "rule_id",
    "rule_family",
    "signal_cohort",
    "token_id",
    "market_slug",
    "question",
    "family",
    "outcome",
    "latest_time_utc",
    "latest_bid",
    "latest_ask",
    "latest_midpoint",
    "latest_spread",
    "relative_spread",
    "bid_move_abs",
    "mid_move_abs",
    "spread_change",
    "net_buy_events",
    "net_buy_size",
    "validation_roi",
    "validation_win_rate",
    "validation_trades",
    "shadow_only",
]


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    value = cfg.raw.get("price_action_microstructure", {})
    return value if isinstance(value, dict) else {}


def _enabled(settings: dict[str, Any]) -> bool:
    return str(settings.get("enabled", True)).strip().lower() not in {"0", "false", "no"}


def _float_list(settings: dict[str, Any], key: str, default: list[float]) -> list[float]:
    raw = settings.get(key)
    if raw is None:
        return default
    if not isinstance(raw, list):
        raw = [raw]
    values = [safe_float(item) for item in raw]
    return [float(item) for item in values if item is not None]


def _int_setting(settings: dict[str, Any], key: str, default: int) -> int:
    value = safe_float(settings.get(key))
    return default if value is None else int(value)


def _float_setting(settings: dict[str, Any], key: str, default: float) -> float:
    value = safe_float(settings.get(key))
    return default if value is None else float(value)


def _token_id(row: dict[str, Any]) -> str:
    return str(row.get("asset_id") or row.get("token_id") or row.get("outcome_token_id") or "").strip()


def _row_time(row: dict[str, Any]) -> datetime | None:
    return parse_timestamp(row.get("collected_at_utc") or row.get("source_timestamp") or row.get("timestamp_utc"))


def _fmt_time(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dedupe_sort_rows(rows: list[dict[str, str]], settings: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    max_rows_per_token = _int_setting(settings, "max_rows_per_token", 500)
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    seen: set[tuple[str, str, str, str, str]] = set()
    for row in rows:
        token = _token_id(row)
        if not token:
            continue
        bid = safe_float(row.get("best_bid"))
        ask = safe_float(row.get("best_ask"))
        if bid is None or ask is None or not 0 < bid < 1 or not 0 < ask < 1 or bid > ask:
            continue
        key = (
            token,
            str(row.get("collected_at_utc") or ""),
            str(row.get("source_timestamp") or ""),
            str(bid),
            str(ask),
        )
        if key in seen:
            continue
        seen.add(key)
        grouped[token].append(row)
    for token, token_rows in grouped.items():
        token_rows.sort(key=lambda item: (_row_time(item) or datetime.min.replace(tzinfo=timezone.utc), str(item.get("source_timestamp") or "")))
        if max_rows_per_token > 0 and len(token_rows) > max_rows_per_token:
            grouped[token] = token_rows[-max_rows_per_token:]
    return grouped


def _relative_spread(spread: float | None, ask: float | None) -> float | None:
    if spread is None or ask is None or ask <= 0:
        return None
    return spread / ask


def _book_values(row: dict[str, Any]) -> tuple[float | None, float | None, float | None, float | None]:
    bid = safe_float(row.get("best_bid"))
    ask = safe_float(row.get("best_ask"))
    midpoint = safe_float(row.get("midpoint"))
    spread = safe_float(row.get("spread"))
    if midpoint is None and bid is not None and ask is not None:
        midpoint = (bid + ask) / 2.0
    if spread is None and bid is not None and ask is not None:
        spread = max(0.0, ask - bid)
    return bid, ask, midpoint, spread


def _recent_pressure(rows: list[dict[str, str]]) -> tuple[int, float]:
    buy_events = 0
    sell_events = 0
    buy_size = 0.0
    sell_size = 0.0
    for row in rows:
        side = str(row.get("price_change_side") or "").strip().upper()
        size = safe_float(row.get("price_change_size")) or 0.0
        if side == "BUY":
            buy_events += 1
            buy_size += size
        elif side == "SELL":
            sell_events += 1
            sell_size += size
    return buy_events - sell_events, buy_size - sell_size


def _simulate_exit(
    future_rows: list[dict[str, str]],
    *,
    entry_ask: float,
    stake_usdc: float,
    take_profit_return: float,
    stop_loss_return: float,
    min_profit_usdc: float,
) -> dict[str, Any] | None:
    quantity = stake_usdc / entry_ask if entry_ask > 0 else 0.0
    last_payload: dict[str, Any] | None = None
    for offset, row in enumerate(future_rows, start=1):
        bid, _, _, _ = _book_values(row)
        if bid is None:
            continue
        pnl = (bid - entry_ask) * quantity
        roi = pnl / stake_usdc if stake_usdc > 0 else 0.0
        payload = {
            "exit_time_utc": _fmt_time(_row_time(row)),
            "exit_bid": bid,
            "pnl_usdc": pnl,
            "roi": roi,
            "observations_to_exit": offset,
        }
        last_payload = payload
        if roi >= take_profit_return and pnl >= min_profit_usdc:
            return {**payload, "exit_reason": "take_profit"}
        if roi <= -stop_loss_return:
            return {**payload, "exit_reason": "stop_loss"}
    if last_payload is None:
        return None
    return {**last_payload, "exit_reason": "fixed_horizon"}


def _feature_for_index(
    token_rows: list[dict[str, str]],
    index: int,
    *,
    settings: dict[str, Any],
    include_future: bool,
) -> dict[str, Any] | None:
    lookback = _int_setting(settings, "lookback_observations", 3)
    max_forward = _int_setting(settings, "max_forward_observations", 8)
    min_future = _int_setting(settings, "min_future_observations", 2)
    stake_usdc = _float_setting(settings, "stake_usdc", 10.0)
    take_profit_return = _float_setting(settings, "take_profit_return", 0.08)
    stop_loss_return = _float_setting(settings, "stop_loss_return", 0.06)
    min_profit_usdc = _float_setting(settings, "min_profit_usdc", 0.05)
    if index < lookback:
        return None
    if include_future and len(token_rows) - index - 1 < min_future:
        return None
    current = token_rows[index]
    previous = token_rows[index - lookback]
    bid, ask, midpoint, spread = _book_values(current)
    prev_bid, prev_ask, prev_midpoint, prev_spread = _book_values(previous)
    if bid is None or ask is None or midpoint is None or spread is None or prev_bid is None or prev_ask is None or prev_midpoint is None:
        return None
    if ask <= 0 or ask >= 1 or bid <= 0 or bid > ask:
        return None
    recent_rows = token_rows[max(0, index - lookback + 1) : index + 1]
    net_buy_events, net_buy_size = _recent_pressure(recent_rows)
    result: dict[str, Any] = {}
    if include_future:
        simulated = _simulate_exit(
            token_rows[index + 1 : index + 1 + max_forward],
            entry_ask=ask,
            stake_usdc=stake_usdc,
            take_profit_return=take_profit_return,
            stop_loss_return=stop_loss_return,
            min_profit_usdc=min_profit_usdc,
        )
        if simulated is None:
            return None
        result = simulated
    return {
        "token_id": _token_id(current),
        "market_slug": current.get("market_slug", ""),
        "question": current.get("question", ""),
        "family": current.get("category") or current.get("family") or "unknown",
        "outcome": current.get("selection") or current.get("outcome") or "",
        "entry_time_utc": _fmt_time(_row_time(current)),
        "entry_bid": bid,
        "entry_ask": ask,
        "entry_midpoint": midpoint,
        "entry_spread": spread,
        "relative_spread": _relative_spread(spread, ask),
        "lookback_observations": lookback,
        "bid_move_abs": bid - prev_bid,
        "mid_move_abs": midpoint - prev_midpoint,
        "ask_move_abs": ask - prev_ask,
        "spread_change": "" if prev_spread is None else spread - prev_spread,
        "net_buy_events": net_buy_events,
        "net_buy_size": net_buy_size,
        "current_side": current.get("price_change_side", ""),
        "current_price_change_size": current.get("price_change_size", ""),
        "stake_usdc": stake_usdc,
        **result,
    }


def _trade_events(grouped: dict[str, list[dict[str, str]]], settings: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for token_rows in grouped.values():
        for index in range(len(token_rows)):
            event = _feature_for_index(token_rows, index, settings=settings, include_future=True)
            if event is not None:
                events.append(event)
    events.sort(key=lambda row: row.get("entry_time_utc", ""))
    if not events:
        return events
    train_fraction = max(0.1, min(0.9, _float_setting(settings, "train_fraction", 0.6)))
    split_index = max(1, min(len(events) - 1, int(len(events) * train_fraction))) if len(events) > 1 else len(events)
    for idx, event in enumerate(events):
        event["split"] = "train" if idx < split_index else "validation"
    return events


def _latest_features(grouped: dict[str, list[dict[str, str]]], settings: dict[str, Any]) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    for token_rows in grouped.values():
        if not token_rows:
            continue
        event = _feature_for_index(token_rows, len(token_rows) - 1, settings=settings, include_future=False)
        if event is not None:
            features.append(event)
    features.sort(key=lambda row: row.get("entry_time_utc", ""), reverse=True)
    return features


def _rule_specs(settings: dict[str, Any]) -> list[dict[str, Any]]:
    max_spreads = _float_list(settings, "max_spread_grid", [0.01, 0.02, 0.04])
    min_moves = _float_list(settings, "min_move_grid", [0.005, 0.01, 0.02])
    buy_pressures = _float_list(settings, "min_buy_pressure_grid", [1, 2])
    compressions = _float_list(settings, "min_spread_compression_grid", [0.005, 0.01])
    max_relative_spread = _float_setting(settings, "max_relative_spread", 0.15)
    rules: list[dict[str, Any]] = []
    for max_spread in max_spreads:
        for min_move in min_moves:
            rules.append(
                {
                    "rule_family": "bid_momentum_tight",
                    "rule_id": f"bid_momentum_tight|move>={min_move:g}|spread<={max_spread:g}",
                    "min_bid_move": min_move,
                    "max_spread": max_spread,
                    "max_relative_spread": max_relative_spread,
                }
            )
            rules.append(
                {
                    "rule_family": "mid_momentum_tight",
                    "rule_id": f"mid_momentum_tight|move>={min_move:g}|spread<={max_spread:g}",
                    "min_mid_move": min_move,
                    "max_spread": max_spread,
                    "max_relative_spread": max_relative_spread,
                }
            )
        for min_pressure in buy_pressures:
            rules.append(
                {
                    "rule_family": "buy_pressure_tight",
                    "rule_id": f"buy_pressure_tight|net_buy>={int(min_pressure)}|spread<={max_spread:g}",
                    "min_net_buy_events": int(min_pressure),
                    "max_spread": max_spread,
                    "max_relative_spread": max_relative_spread,
                }
            )
        for compression in compressions:
            rules.append(
                {
                    "rule_family": "spread_compression_bid_stable",
                    "rule_id": f"spread_compression_bid_stable|compression>={compression:g}|spread<={max_spread:g}",
                    "min_spread_compression": compression,
                    "min_bid_move": 0.0,
                    "max_spread": max_spread,
                    "max_relative_spread": max_relative_spread,
                }
            )
    return rules


def _passes_rule(event: dict[str, Any], rule: dict[str, Any]) -> bool:
    spread = safe_float(event.get("entry_spread"))
    relative_spread = safe_float(event.get("relative_spread"))
    if spread is None or spread > float(rule.get("max_spread", 1.0)):
        return False
    if relative_spread is None or relative_spread > float(rule.get("max_relative_spread", 1.0)):
        return False
    family = str(rule.get("rule_family") or "")
    if family == "bid_momentum_tight":
        return (safe_float(event.get("bid_move_abs")) or 0.0) >= float(rule.get("min_bid_move", 0.0))
    if family == "mid_momentum_tight":
        return (safe_float(event.get("mid_move_abs")) or 0.0) >= float(rule.get("min_mid_move", 0.0))
    if family == "buy_pressure_tight":
        return (safe_float(event.get("net_buy_events")) or 0.0) >= float(rule.get("min_net_buy_events", 1.0))
    if family == "spread_compression_bid_stable":
        spread_change = safe_float(event.get("spread_change"))
        return (
            spread_change is not None
            and spread_change <= -float(rule.get("min_spread_compression", 0.0))
            and (safe_float(event.get("bid_move_abs")) or 0.0) >= float(rule.get("min_bid_move", 0.0))
        )
    return False


def _metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    stake = sum(float(safe_float(row.get("stake_usdc")) or 0.0) for row in events)
    pnl = sum(float(safe_float(row.get("pnl_usdc")) or 0.0) for row in events)
    wins = sum(1 for row in events if (safe_float(row.get("pnl_usdc")) or 0.0) > 0)
    positive_by_token: dict[str, float] = defaultdict(float)
    for row in events:
        positive_by_token[str(row.get("token_id") or "")] += max(0.0, safe_float(row.get("pnl_usdc")) or 0.0)
    positive_total = sum(positive_by_token.values())
    return {
        "trades": len(events),
        "stake_usdc": stake,
        "pnl_usdc": pnl,
        "roi": pnl / stake if stake > 0 else 0.0,
        "win_rate": wins / len(events) if events else 0.0,
        "top_positive_token_pnl_share": max(positive_by_token.values()) / positive_total if positive_total > 0 else 0.0,
    }


def _rule_evidence(events: list[dict[str, Any]], settings: dict[str, Any]) -> list[dict[str, Any]]:
    min_total_trades = _int_setting(settings, "min_total_trades", 8)
    min_validation_trades = _int_setting(settings, "min_validation_trades", 3)
    min_validation_roi = _float_setting(settings, "min_validation_roi", 0.03)
    min_validation_win_rate = _float_setting(settings, "min_validation_win_rate", 0.55)
    min_train_roi = _float_setting(settings, "min_train_roi", 0.0)
    max_positive_share = _float_setting(settings, "max_single_token_positive_pnl_share", 0.50)
    evidence: list[dict[str, Any]] = []
    for rule in _rule_specs(settings):
        selected = [event for event in events if _passes_rule(event, rule)]
        train = [event for event in selected if event.get("split") == "train"]
        validation = [event for event in selected if event.get("split") == "validation"]
        total_metrics = _metrics(selected)
        train_metrics = _metrics(train)
        validation_metrics = _metrics(validation)
        checks = [
            total_metrics["trades"] >= min_total_trades,
            validation_metrics["trades"] >= min_validation_trades,
            train_metrics["roi"] >= min_train_roi,
            validation_metrics["pnl_usdc"] > 0,
            validation_metrics["roi"] >= min_validation_roi,
            validation_metrics["win_rate"] >= min_validation_win_rate,
            validation_metrics["top_positive_token_pnl_share"] <= max_positive_share if validation_metrics["pnl_usdc"] > 0 else False,
        ]
        passed = all(checks)
        if passed:
            status = "candidate_for_forward_shadow"
            reason = "Chronological validation bid/ask round trips cleared microstructure thresholds."
        elif validation_metrics["trades"] < min_validation_trades:
            status = "insufficient_validation_trades"
            reason = f"Needs >= {min_validation_trades} validation trades; observed {validation_metrics['trades']}."
        elif validation_metrics["pnl_usdc"] <= 0 or validation_metrics["roi"] < min_validation_roi:
            status = "negative_or_insufficient_validation_roi"
            reason = "Validation P&L/ROI does not overcome bid/ask costs."
        elif validation_metrics["win_rate"] < min_validation_win_rate:
            status = "validation_win_rate_below_threshold"
            reason = f"Validation win rate is below {min_validation_win_rate:.0%}."
        elif train_metrics["roi"] < min_train_roi:
            status = "train_roi_below_threshold"
            reason = "Training split is not non-negative, so the validation result is not trusted."
        elif validation_metrics["top_positive_token_pnl_share"] > max_positive_share:
            status = "validation_concentration_risk"
            reason = "Validation positive P&L is too concentrated in one token."
        else:
            status = "collect_more_microstructure_evidence"
            reason = "Rule did not clear all validation gates."
        evidence.append(
            {
                "rule_id": rule["rule_id"],
                "rule_family": rule["rule_family"],
                "parameters": {key: value for key, value in rule.items() if key not in {"rule_id", "rule_family"}},
                "total_trades": total_metrics["trades"],
                "train_trades": train_metrics["trades"],
                "validation_trades": validation_metrics["trades"],
                "total_pnl_usdc": total_metrics["pnl_usdc"],
                "train_pnl_usdc": train_metrics["pnl_usdc"],
                "validation_pnl_usdc": validation_metrics["pnl_usdc"],
                "total_roi": total_metrics["roi"],
                "train_roi": train_metrics["roi"],
                "validation_roi": validation_metrics["roi"],
                "validation_win_rate": validation_metrics["win_rate"],
                "validation_top_positive_token_pnl_share": validation_metrics["top_positive_token_pnl_share"],
                "validation_pass": passed,
                "status": status,
                "reason": reason,
            }
        )
    evidence.sort(
        key=lambda row: (
            bool(row.get("validation_pass")),
            safe_float(row.get("validation_roi")) or -999.0,
            safe_float(row.get("validation_pnl_usdc")) or -999.0,
            int(safe_float(row.get("validation_trades")) or 0),
        ),
        reverse=True,
    )
    return evidence


def _current_candidates(
    latest_features: list[dict[str, Any]],
    rule_rows: list[dict[str, Any]],
    settings: dict[str, Any],
) -> list[dict[str, Any]]:
    max_candidates = _int_setting(settings, "max_current_candidates", 20)
    rule_lookup = {row["rule_id"]: row for row in rule_rows if str(row.get("validation_pass")).lower() == "true" or row.get("validation_pass") is True}
    if not rule_lookup:
        return []
    specs = [rule for rule in _rule_specs(settings) if rule["rule_id"] in rule_lookup]
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for rule in specs:
        evidence = rule_lookup[rule["rule_id"]]
        for feature in latest_features:
            token = str(feature.get("token_id") or "")
            if not token or not _passes_rule(feature, rule):
                continue
            key = (token, rule["rule_id"])
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "rule_id": rule["rule_id"],
                    "rule_family": rule["rule_family"],
                    "signal_cohort": f"price_action_microstructure|{rule['rule_family']}",
                    "token_id": token,
                    "market_slug": feature.get("market_slug", ""),
                    "question": feature.get("question", ""),
                    "family": feature.get("family", ""),
                    "outcome": feature.get("outcome", ""),
                    "latest_time_utc": feature.get("entry_time_utc", ""),
                    "latest_bid": feature.get("entry_bid", ""),
                    "latest_ask": feature.get("entry_ask", ""),
                    "latest_midpoint": feature.get("entry_midpoint", ""),
                    "latest_spread": feature.get("entry_spread", ""),
                    "relative_spread": feature.get("relative_spread", ""),
                    "bid_move_abs": feature.get("bid_move_abs", ""),
                    "mid_move_abs": feature.get("mid_move_abs", ""),
                    "spread_change": feature.get("spread_change", ""),
                    "net_buy_events": feature.get("net_buy_events", ""),
                    "net_buy_size": feature.get("net_buy_size", ""),
                    "validation_roi": evidence.get("validation_roi", ""),
                    "validation_win_rate": evidence.get("validation_win_rate", ""),
                    "validation_trades": evidence.get("validation_trades", ""),
                    "shadow_only": True,
                }
            )
    candidates.sort(
        key=lambda row: (
            safe_float(row.get("validation_roi")) or -999.0,
            safe_float(row.get("bid_move_abs")) or -999.0,
        ),
        reverse=True,
    )
    return candidates[:max_candidates]


def build_microstructure_edge_lab(cfg: EngineConfig) -> dict[str, Any]:
    """Run a shadow-only bid/ask microstructure rule lab over websocket history."""
    settings = _settings(cfg)
    out_dir = cfg.output_root / OUTPUT_DIRNAME
    if not _enabled(settings):
        summary = {
            "status": "disabled",
            "generated_at_utc": now_utc(),
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        }
        write_json(out_dir / SUMMARY_JSON, summary)
        return summary

    rows = read_csv_rows(cfg.output_root / "polymarket_training" / "websocket_market_features.csv")
    grouped = _dedupe_sort_rows(rows, settings)
    events = _trade_events(grouped, settings)
    latest = _latest_features(grouped, settings)
    rule_rows = _rule_evidence(events, settings) if events else []
    current = _current_candidates(latest, rule_rows, settings)
    write_csv(out_dir / TRADE_EVENTS_FILE, events, fieldnames=EVENT_FIELDS)
    write_csv(out_dir / RULE_EVIDENCE_FILE, rule_rows, fieldnames=RULE_FIELDS)
    write_csv(out_dir / CURRENT_CANDIDATES_FILE, current, fieldnames=CURRENT_FIELDS)

    passing = [row for row in rule_rows if str(row.get("validation_pass")).lower() == "true" or row.get("validation_pass") is True]
    top_rule = passing[0] if passing else (rule_rows[0] if rule_rows else {})
    summary = {
        "status": "computed",
        "generated_at_utc": now_utc(),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
        "source_rows": len(rows),
        "tokens": len(grouped),
        "trade_events": len(events),
        "rule_rows": len(rule_rows),
        "validation_pass_rules": len(passing),
        "current_candidates": len(current),
        "decision": "microstructure_rules_ready_for_forward_shadow"
        if passing
        else "collect_more_websocket_microstructure_evidence"
        if events
        else "collect_more_websocket_rows",
        "top_rule": top_rule,
        "top_rules": rule_rows[:15],
        "current_candidates_preview": current[:15],
        "trade_events_file": str(out_dir / TRADE_EVENTS_FILE),
        "rule_evidence_file": str(out_dir / RULE_EVIDENCE_FILE),
        "current_candidates_file": str(out_dir / CURRENT_CANDIDATES_FILE),
        "warnings": {
            "shadow_only": True,
            "not_paper_or_live_trading": True,
            "uses_chronological_train_validation_split": True,
            "uses_bid_for_exit_not_midpoint": True,
            "settlement_independent_price_action_only": True,
        },
    }
    write_json(out_dir / SUMMARY_JSON, summary)
    return summary


def run(config_path: str = "polymarket_predictive_config.example.yaml") -> dict[str, Any]:
    cfg = load_config(config_path)
    return build_microstructure_edge_lab(cfg)
