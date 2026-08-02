"""Shadow-only longshot-bias scanner.

The hypothesis is structural: very low-priced YES tails can be overpriced in
slow, liquid markets. The executable research expression is the NO side. This
module only nominates/open shadow evidence rows so CLV and shadow P&L can judge
the thesis; it never invokes paper trading or live trading.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import EngineConfig, load_config
from .runtime_lock import runtime_lock
from .shadow_cohort import update_shadow_cohort_evidence
from .utils import git_commit_hash, now_utc, parse_timestamp, read_csv_rows, safe_float, write_csv, write_json
from .worldcup_validation import classify_market_family

LONGSHOT_FIELDS = [
    "prediction_timestamp",
    "market_id",
    "market_slug",
    "question",
    "category",
    "family",
    "signal_cohort",
    "outcome",
    "token_id",
    "yes_token_id",
    "no_token_id",
    "close_time",
    "time_to_close_hours",
    "yes_price",
    "yes_price_source",
    "yes_best_bid",
    "yes_best_ask",
    "no_best_bid",
    "no_best_ask",
    "best_bid",
    "best_ask",
    "market_midpoint",
    "executable_price",
    "spread",
    "relative_spread",
    "liquidity",
    "shadow_trade_candidate",
    "shadow_candidate_reason",
    "shadow_source",
    "shadow_priority_score",
    "alpha_trade_candidate",
    "validation_layer_pass",
    "decision_use",
]


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    settings = cfg.raw.get("longshot_bias", {})
    return settings if isinstance(settings, dict) else {}


def _output_root(cfg: EngineConfig) -> Path:
    root = cfg.output_root / "polymarket_longshot_bias"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _input_paths(cfg: EngineConfig) -> list[Path]:
    settings = _settings(cfg)
    raw_paths = settings.get("input_paths")
    if raw_paths is None:
        raw_paths = [
            cfg.output_root / "polymarket_liquidity_discovery" / "liquidity_watchlist.csv",
            cfg.governance_root / "websocket_liquidity_targets.csv",
            cfg.output_root / "polymarket_predictions" / "predictions.csv",
        ]
    if not isinstance(raw_paths, list):
        raw_paths = [raw_paths]
    return [Path(path) for path in raw_paths]


def _entry_price_band(cfg: EngineConfig) -> tuple[float, float]:
    """Use the same base entry band as paper risk for shadow nominations."""
    risk = cfg.raw.get("risk", {})
    if not isinstance(risk, dict):
        risk = {}
    minimum_entry_price = safe_float(risk.get("minimum_entry_price"))
    maximum_entry_price = safe_float(risk.get("maximum_entry_price"))
    return (
        float(minimum_entry_price) if minimum_entry_price is not None else 0.05,
        float(maximum_entry_price) if maximum_entry_price is not None else 0.90,
    )


def _first_price(row: dict[str, Any], keys: tuple[str, ...]) -> tuple[float | None, str]:
    for key in keys:
        value = safe_float(row.get(key))
        if value is not None and 0 < value < 1:
            return value, key
    return None, ""


def _row_time_to_close_hours(row: dict[str, Any], *, as_of: datetime) -> float | None:
    value = safe_float(row.get("time_to_close_hours") or row.get("hours_to_close"))
    if value is not None:
        return value
    close_time = parse_timestamp(
        row.get("close_time")
        or row.get("end_time")
        or row.get("endDate")
        or row.get("endDateIso")
        or row.get("market_close_time")
    )
    if close_time is None:
        return None
    return (close_time.astimezone(timezone.utc) - as_of).total_seconds() / 3600.0


def _market_key(row: dict[str, Any]) -> str:
    return str(row.get("market_id") or row.get("condition_id") or row.get("market_slug") or "").strip()


def _is_yes(row: dict[str, Any]) -> bool:
    return str(row.get("outcome") or "").strip().lower() == "yes"


def _is_no(row: dict[str, Any]) -> bool:
    return str(row.get("outcome") or "").strip().lower() == "no"


def _dedupe_latest(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            _market_key(row),
            str(row.get("token_id") or row.get("asset_id") or "").strip(),
            str(row.get("outcome") or "").strip().lower(),
        )
        if not key[0] or not key[1]:
            continue
        existing = best.get(key)
        if existing is None:
            best[key] = row
            continue
        existing_time = str(existing.get("timestamp") or existing.get("prediction_timestamp") or "")
        row_time = str(row.get("timestamp") or row.get("prediction_timestamp") or "")
        if row_time >= existing_time:
            best[key] = row
    return list(best.values())


def _load_source_rows(cfg: EngineConfig) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for path in _input_paths(cfg):
        path_rows = read_csv_rows(path)
        counts[str(path)] = len(path_rows)
        for row in path_rows:
            rows.append({**row, "_source_path": str(path)})
    return _dedupe_latest(rows), counts


def _candidate_from_group(
    yes_row: dict[str, Any],
    no_row: dict[str, Any],
    *,
    as_of: datetime,
) -> dict[str, Any] | None:
    yes_price, yes_price_source = _first_price(
        yes_row,
        ("best_ask", "executable_price", "gamma_price", "market_midpoint", "midpoint"),
    )
    no_ask, _ = _first_price(no_row, ("best_ask", "executable_price", "gamma_price", "market_midpoint", "midpoint"))
    if yes_price is None or no_ask is None:
        return None
    no_bid, _ = _first_price(no_row, ("best_bid", "executable_sell_price"))
    no_mid, _ = _first_price(no_row, ("midpoint", "market_midpoint"))
    if no_mid is None and no_bid is not None and no_ask >= no_bid:
        no_mid = (no_bid + no_ask) / 2.0
    spread = safe_float(no_row.get("spread"))
    if spread is None and no_bid is not None:
        spread = max(0.0, no_ask - no_bid)
    liquidity = max(
        safe_float(yes_row.get("liquidity")) or 0.0,
        safe_float(no_row.get("liquidity")) or 0.0,
    )
    ttc = _row_time_to_close_hours(yes_row, as_of=as_of)
    if ttc is None:
        ttc = _row_time_to_close_hours(no_row, as_of=as_of)
    close_time = (
        yes_row.get("close_time")
        or yes_row.get("end_time")
        or yes_row.get("endDate")
        or yes_row.get("endDateIso")
        or no_row.get("close_time")
        or no_row.get("end_time")
        or no_row.get("endDate")
        or no_row.get("endDateIso")
        or ""
    )
    family = classify_market_family(yes_row)
    if not family or family == "unknown":
        family = classify_market_family(no_row)
    if not family:
        family = "unknown"
    cohort = f"structural|longshot_no|{family}"
    priority = (
        yes_price
        + min(1.0, liquidity / 10_000.0) * 0.01
        - max(0.0, spread or 0.0)
    )
    return {
        "prediction_timestamp": as_of.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "market_id": _market_key(yes_row) or _market_key(no_row),
        "market_slug": yes_row.get("market_slug") or no_row.get("market_slug") or "",
        "question": yes_row.get("question") or no_row.get("question") or "",
        "category": family,
        "family": family,
        "signal_cohort": cohort,
        "outcome": "No",
        "token_id": no_row.get("token_id") or no_row.get("asset_id") or "",
        "yes_token_id": yes_row.get("token_id") or yes_row.get("asset_id") or "",
        "no_token_id": no_row.get("token_id") or no_row.get("asset_id") or "",
        "close_time": close_time,
        "time_to_close_hours": "" if ttc is None else round(ttc, 6),
        "yes_price": round(yes_price, 6),
        "yes_price_source": yes_price_source,
        "yes_best_bid": yes_row.get("best_bid", ""),
        "yes_best_ask": yes_row.get("best_ask", ""),
        "no_best_bid": no_row.get("best_bid", ""),
        "no_best_ask": no_row.get("best_ask", ""),
        "best_bid": "" if no_bid is None else round(no_bid, 6),
        "best_ask": round(no_ask, 6),
        "top_bid_size": no_row.get("top_bid_size", no_row.get("bid_size", "")),
        "top_ask_size": no_row.get("top_ask_size", no_row.get("ask_size", "")),
        "bid_depth_1pct": no_row.get("bid_depth_1pct", ""),
        "ask_depth_1pct": no_row.get("ask_depth_1pct", ""),
        "bid_depth_5pct": no_row.get("bid_depth_5pct", ""),
        "ask_depth_5pct": no_row.get("ask_depth_5pct", ""),
        "websocket_quote_age_seconds": no_row.get("websocket_quote_age_seconds", ""),
        "market_midpoint": "" if no_mid is None else round(no_mid, 6),
        "executable_price": round(no_ask, 6),
        "spread": "" if spread is None else round(spread, 6),
        "relative_spread": "" if spread is None else round(spread / no_ask, 6),
        "liquidity": round(liquidity, 6),
        "shadow_trade_candidate": True,
        "shadow_candidate_reason": "longshot_bias_no_side_shadow",
        "shadow_source": "longshot_bias",
        "shadow_priority_score": round(priority, 6),
        "alpha_trade_candidate": False,
        "validation_layer_pass": False,
        "decision_use": "shadow_clv_learning_target_not_paper_trade_authorisation",
    }


def scan_longshot_bias_candidates(
    cfg: EngineConfig,
    *,
    as_of: datetime | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    settings = _settings(cfg)
    as_of = as_of or datetime.now(timezone.utc)
    min_price = float(settings.get("min_price", 0.02))
    max_price = float(settings.get("max_price", 0.12))
    min_ttc = float(settings.get("min_time_to_close_hours", 168.0))
    min_liquidity = float(settings.get("minimum_liquidity", 500.0))
    max_candidates = int(settings.get("max_candidates", 25) or 25)
    minimum_entry_price, maximum_entry_price = _entry_price_band(cfg)

    rows, source_counts = _load_source_rows(cfg)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = _market_key(row)
        if key:
            grouped[key].append(row)

    skip_counts: Counter[str] = Counter()
    candidates: list[dict[str, Any]] = []
    for group_rows in grouped.values():
        yes_rows = [row for row in group_rows if _is_yes(row)]
        no_rows = [row for row in group_rows if _is_no(row)]
        if not yes_rows:
            skip_counts["missing_yes_side"] += 1
            continue
        yes_row = max(
            yes_rows,
            key=lambda row: safe_float(row.get("liquidity")) or 0.0,
        )
        yes_price, _price_source = _first_price(
            yes_row,
            ("best_ask", "executable_price", "gamma_price", "market_midpoint", "midpoint"),
        )
        if yes_price is None:
            skip_counts["missing_yes_price"] += 1
            continue
        if yes_price < min_price:
            skip_counts["yes_price_below_min"] += 1
            continue
        if yes_price > max_price:
            skip_counts["yes_price_above_max"] += 1
            continue
        ttc = _row_time_to_close_hours(yes_row, as_of=as_of)
        if ttc is None:
            skip_counts["missing_time_to_close"] += 1
            continue
        if ttc < min_ttc:
            skip_counts["fast_market_excluded"] += 1
            continue
        if not no_rows:
            skip_counts["missing_no_side_token"] += 1
            continue
        no_row = max(
            no_rows,
            key=lambda row: safe_float(row.get("liquidity")) or 0.0,
        )
        no_ask, _no_ask_source = _first_price(
            no_row,
            ("best_ask", "executable_price", "gamma_price", "market_midpoint", "midpoint"),
        )
        if no_ask is None:
            skip_counts["missing_no_executable_price"] += 1
            continue
        if no_ask < minimum_entry_price:
            skip_counts["no_entry_price_below_minimum"] += 1
            continue
        if no_ask > maximum_entry_price:
            skip_counts["no_entry_price_above_maximum"] += 1
            continue
        liquidity = max(
            safe_float(yes_row.get("liquidity")) or 0.0,
            safe_float(no_row.get("liquidity")) or 0.0,
        )
        if liquidity < min_liquidity:
            skip_counts["liquidity_below_minimum"] += 1
            continue
        candidate = _candidate_from_group(yes_row, no_row, as_of=as_of)
        if candidate is None:
            skip_counts["missing_no_executable_price"] += 1
            continue
        candidates.append(candidate)

    candidates.sort(
        key=lambda row: (
            safe_float(row.get("shadow_priority_score")) or 0.0,
            safe_float(row.get("liquidity")) or 0.0,
            safe_float(row.get("time_to_close_hours")) or 0.0,
        ),
        reverse=True,
    )
    if max_candidates > 0:
        candidates = candidates[:max_candidates]

    summary = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "git_commit": git_commit_hash(),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
        "settings": {
            "min_price": min_price,
            "max_price": max_price,
            "min_time_to_close_hours": min_ttc,
            "minimum_liquidity": min_liquidity,
            "max_candidates": max_candidates,
            "minimum_entry_price": minimum_entry_price,
            "maximum_entry_price": maximum_entry_price,
        },
        "source_rows": len(rows),
        "source_row_counts": source_counts,
        "markets_scanned": len(grouped),
        "candidates": len(candidates),
        "skip_counts": dict(sorted(skip_counts.items())),
        "candidate_cohorts": dict(sorted(Counter(str(row.get("signal_cohort") or "") for row in candidates).items())),
        "candidate_preview": candidates[:10],
        "decision_use": "shadow_clv_learning_target_not_paper_trade_authorisation",
    }
    return candidates, summary


def build_longshot_bias_scan(
    cfg: EngineConfig,
    *,
    as_of: datetime | None = None,
    emit_shadow: bool | None = None,
) -> dict[str, Any]:
    settings = _settings(cfg)
    enabled = str(settings.get("enabled", True)).strip().lower() not in {"0", "false", "no", "off"}
    if not enabled:
        payload = {
            "status": "disabled",
            "generated_at_utc": now_utc(),
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        }
        write_json(_output_root(cfg) / "longshot_bias_summary.json", payload)
        write_csv(_output_root(cfg) / "longshot_bias_candidates.csv", [], fieldnames=LONGSHOT_FIELDS)
        return {**payload, "candidate_rows": []}

    candidates, summary = scan_longshot_bias_candidates(cfg, as_of=as_of)
    root = _output_root(cfg)
    write_csv(root / "longshot_bias_candidates.csv", candidates, fieldnames=LONGSHOT_FIELDS)

    should_emit_shadow = (
        str(settings.get("emit_shadow_positions", True)).strip().lower() not in {"0", "false", "no", "off"}
        if emit_shadow is None
        else emit_shadow
    )
    shadow_summary: dict[str, Any] | None = None
    if should_emit_shadow and candidates:
        # WO-119: the shadow ledgers are read-modify-write state shared with
        # the paper cycle, whose caller holds the prediction_cycle runtime
        # lock. Running this standalone scan unlocked alongside the live loop
        # could drop the other writer's fills or shrink the anchored
        # shadow_fills prefix. Fail-safe: if the lock is held, skip the shadow
        # emission (the scan output itself is still written) rather than race.
        lock_settings = cfg.raw.get("runtime_resource_guard", {}) or {}
        lock_stale_seconds = float(
            lock_settings.get("prediction_cycle_lock_stale_seconds", 1800) or 1800
        )
        with runtime_lock(
            cfg, "prediction_cycle", stale_after_seconds=lock_stale_seconds
        ) as lock:
            if lock.acquired:
                shadow_summary = update_shadow_cohort_evidence(cfg, candidates)
            else:
                shadow_summary = {
                    "status": "skipped_prediction_cycle_lock_held",
                    "opened_this_cycle": 0,
                    "runtime_lock": lock.as_dict(),
                }
    # WO-143b.1 F4 (caller-honesty contract): `update_shadow_cohort_evidence`
    # now also returns `skipped_shadow_lock_held` (its own internal
    # `shadow_cohort` lock, WO-143b) alongside the `skipped_prediction_cycle_lock_held`
    # this module synthesises locally when it elects not to call at all. Both
    # mean nothing was written, so the reported count must be 0 -- not
    # whatever `.get` resolves to on a truncated skip payload, which was
    # `None`, not `0`. The status is surfaced verbatim on every path.
    shadow_update_status = (
        shadow_summary.get("status") if isinstance(shadow_summary, dict) else None
    )
    if not isinstance(shadow_update_status, str) or not shadow_update_status:
        shadow_update_status = "not_run"
    shadow_update_skipped = shadow_update_status.startswith("skipped_") or shadow_update_status == "not_run"
    summary = {
        **summary,
        "candidate_file": str(root / "longshot_bias_candidates.csv"),
        "emit_shadow_positions": bool(should_emit_shadow),
        "shadow_update": {
            "status": shadow_update_status,
            "opened_this_cycle": (
                0
                if shadow_update_skipped
                else (shadow_summary.get("opened_this_cycle") if isinstance(shadow_summary, dict) else 0)
            ),
            "open_positions": shadow_summary.get("open_positions") if isinstance(shadow_summary, dict) else None,
            "closed_this_cycle": shadow_summary.get("closed_this_cycle") if isinstance(shadow_summary, dict) else None,
        },
    }
    write_json(root / "longshot_bias_summary.json", summary)
    return {**summary, "candidate_rows": candidates}


def main(config_path: str) -> dict[str, Any]:
    return build_longshot_bias_scan(load_config(config_path))
