"""WO-52 hourly adverse-selection concentration study.

Reward sampling is minute-uniform; pick-off risk is probably not.  This
study measures UTC-hour concentration from existing price histories and trade
prints, then writes a report-only calm-hours advisory.  It never modifies
maker-carry charges, gates, sizing, paper trading, or live execution.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
from pathlib import Path
from typing import Any

from .config import EngineConfig, load_config
from .utils import now_utc, parse_timestamp, read_csv_rows, safe_float, write_json

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "min_events_per_bucket": 30,
    "fdr_alpha": 0.10,
    "price_history_paths": ["outputs/polymarket_training/historical_price_snapshots.csv"],
}


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = cfg.raw.get("hourly_adverse", {}) if isinstance(cfg.raw.get("hourly_adverse"), dict) else {}
    merged = dict(DEFAULT_SETTINGS)
    merged.update({key: value for key, value in raw.items() if value is not None})
    return merged


def _repo_root(cfg: EngineConfig) -> Path:
    data_root = cfg.data_root
    if not data_root.is_absolute():
        data_root = cfg.path.parent / data_root
    return data_root


def _expand_paths(cfg: EngineConfig, configured: Any) -> list[Path]:
    if isinstance(configured, (str, Path)):
        items = [configured]
    elif isinstance(configured, (list, tuple, set)):
        items = list(configured)
    else:
        items = []
    root = _repo_root(cfg)
    paths: list[Path] = []
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        raw = Path(text)
        if raw.is_absolute():
            matches = sorted(raw.parent.glob(raw.name)) if any(ch in raw.name for ch in "*?[") else [raw]
        else:
            matches = sorted(root.glob(text)) if any(ch in text for ch in "*?[") else [root / raw]
        for match in matches:
            if match not in paths:
                paths.append(match)
    return paths


def _timestamp(value: Any) -> datetime | None:
    parsed = parse_timestamp(value)
    if parsed is not None:
        return parsed.astimezone(timezone.utc)
    numeric = safe_float(value)
    if numeric is None:
        return None
    if numeric > 10_000_000_000:
        numeric = numeric / 1000.0
    try:
        return datetime.fromtimestamp(numeric, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _candidate_rows(cfg: EngineConfig) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in read_csv_rows(cfg.output_root / "maker_carry" / "maker_carry_candidates.csv"):
        token = str(row.get("token_id") or "").strip()
        market = str(row.get("condition_id") or row.get("market") or "").strip()
        quote_distance = safe_float(row.get("quote_distance"))
        quote_size = safe_float(row.get("rewards_min_size_shares") or row.get("quote_size_shares"))
        if not token or not market or quote_distance is None or quote_distance <= 0 or quote_size is None or quote_size <= 0:
            continue
        rows.append(
            {
                "token_id": token,
                "condition_id": market,
                "question": row.get("question", ""),
                "quote_distance": quote_distance,
                "quote_size": quote_size,
            }
        )
    return rows


def _history_by_token(cfg: EngineConfig, settings: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    by_token: dict[str, list[dict[str, Any]]] = {}
    for path in _expand_paths(cfg, settings.get("price_history_paths")):
        for row in read_csv_rows(path):
            token = str(row.get("token_id") or row.get("asset_id") or row.get("clob_token_id") or "").strip()
            timestamp = _timestamp(row.get("timestamp") or row.get("source_timestamp") or row.get("collected_at_utc"))
            price = safe_float(row.get("price") or row.get("midpoint") or row.get("market_midpoint"))
            if not token or timestamp is None or price is None or not (0 < price < 1):
                continue
            by_token.setdefault(token, []).append({"timestamp": timestamp, "price": price})
    for token in by_token:
        by_token[token].sort(key=lambda item: item["timestamp"])
    return by_token


def _bucket_template() -> list[dict[str, Any]]:
    return [
        {
            "utc_hour": hour,
            "price_bar_count": 0,
            "band_crossings": 0,
            "pickoff_charge_usd": 0.0,
            "print_volume_usd": 0.0,
        }
        for hour in range(24)
    ]


def _accumulate_price_bars(
    buckets: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    history: dict[str, list[dict[str, Any]]],
) -> int:
    bars = 0
    for candidate in candidates:
        series = history.get(str(candidate["token_id"]), [])
        if len(series) < 2:
            continue
        distance = float(candidate["quote_distance"])
        quote_size = float(candidate["quote_size"])
        for previous, current in zip(series, series[1:]):
            hour = int(current["timestamp"].hour)
            move = abs(float(current["price"]) - float(previous["price"]))
            buckets[hour]["price_bar_count"] += 1
            bars += 1
            if move > distance:
                buckets[hour]["band_crossings"] += 1
                buckets[hour]["pickoff_charge_usd"] += (move - distance) * quote_size
    return bars


def _accumulate_prints(cfg: EngineConfig, buckets: list[dict[str, Any]], candidate_markets: set[str]) -> int:
    rows = read_csv_rows(cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv")
    prints = 0
    for row in rows:
        market = str(row.get("market") or row.get("condition_id") or "").strip()
        if market not in candidate_markets:
            continue
        timestamp = _timestamp(row.get("timestamp") or row.get("matchTime") or row.get("collected_at_utc"))
        price = safe_float(row.get("price")) or 0.0
        size = safe_float(row.get("size")) or 0.0
        if timestamp is None or price <= 0 or size <= 0:
            continue
        buckets[int(timestamp.hour)]["print_volume_usd"] += price * size
        prints += 1
    return prints


def _bh_fdr(p_values: list[tuple[int, float]], alpha: float) -> set[int]:
    if not p_values:
        return set()
    ordered = sorted(p_values, key=lambda item: item[1])
    passed: set[int] = set()
    cutoff_rank = 0
    for rank, (_, p_value) in enumerate(ordered, start=1):
        if p_value <= alpha * rank / len(ordered):
            cutoff_rank = rank
    if cutoff_rank:
        passed = {hour for hour, _ in ordered[:cutoff_rank]}
    return passed


def _largest_calm_span(calm_hours: set[int]) -> list[int]:
    if not calm_hours:
        return []
    if len(calm_hours) == 24:
        return list(range(24))
    doubled = [hour % 24 for hour in range(48)]
    best: list[int] = []
    current: list[int] = []
    for hour in doubled:
        if hour in calm_hours and hour not in current:
            current.append(hour)
            if len(current) > len(best):
                best = list(current)
        else:
            current = [hour] if hour in calm_hours else []
    return best[:24]


def _span_label(hours: list[int]) -> str:
    if not hours:
        return "no calm-hours window"
    if len(hours) == 24:
        return "00:00-24:00 UTC"
    start = hours[0]
    end = (hours[-1] + 1) % 24
    return f"{start:02d}:00-{end:02d}:00 UTC"


def _score_buckets(buckets: list[dict[str, Any]], settings: dict[str, Any]) -> dict[str, Any]:
    total_charge = sum(float(row["pickoff_charge_usd"]) for row in buckets)
    total_print_volume = sum(float(row["print_volume_usd"]) for row in buckets)
    total_crossings = sum(int(row["band_crossings"]) for row in buckets)
    min_events = int(settings["min_events_per_bucket"])
    expected = 1.0 / 24.0
    tests: list[tuple[int, float]] = []
    for row in buckets:
        bars = int(row["price_bar_count"])
        charge_share = float(row["pickoff_charge_usd"]) / total_charge if total_charge > 0 else 0.0
        volume_share = float(row["print_volume_usd"]) / total_print_volume if total_print_volume > 0 else 0.0
        crossing_rate = int(row["band_crossings"]) / bars if bars else 0.0
        row.update(
            {
                "band_crossing_rate": round(crossing_rate, 6),
                "pickoff_charge_share": round(charge_share, 6),
                "print_volume_share": round(volume_share, 6),
                "sample_floor_met": bars >= min_events,
                "p_value": None,
                "fdr_flag": False,
            }
        )
        if bars >= min_events and total_charge > 0:
            excess = max(0.0, charge_share - expected)
            p_value = 1.0 if excess <= 0 else math.exp(-2.0 * bars * excess * excess)
            row["p_value"] = round(p_value, 8)
            tests.append((int(row["utc_hour"]), p_value))
    flagged = _bh_fdr(tests, float(settings["fdr_alpha"]))
    low_charge = set()
    for row in buckets:
        hour = int(row["utc_hour"])
        row["fdr_flag"] = hour in flagged
        raw_share = float(row["pickoff_charge_usd"]) / total_charge if total_charge > 0 else 0.0
        if not row["fdr_flag"] and raw_share <= max(expected, 0.000001) + 1e-12:
            low_charge.add(hour)
    calm_span = _largest_calm_span(low_charge)
    return {
        "total_pickoff_charge_usd": round(total_charge, 6),
        "total_print_volume_usd": round(total_print_volume, 6),
        "total_band_crossings": total_crossings,
        "tested_buckets": len(tests),
        "flagged_hours_utc": sorted(flagged),
        "recommended_calm_hours_utc": calm_span,
        "recommended_calm_hours_label": _span_label(calm_span),
        "recommended_calm_hours_covers_50pct_reward_minutes": len(calm_span) >= 12,
    }


def _patch_quote_sheet(cfg: EngineConfig, payload: dict[str, Any]) -> None:
    path = cfg.output_root / "maker_carry" / "maker_quote_sheet.md"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    start = "<!-- hourly-adverse:start -->"
    end = "<!-- hourly-adverse:end -->"
    block = "\n".join(
        [
            start,
            f"Calm-hours schedule (advisory): {payload.get('recommended_calm_hours_label', '-')}.",
            f"Toxic UTC hours flagged: {', '.join(str(h) for h in payload.get('flagged_hours_utc', [])) or 'none'}.",
            "Study-only: this does not modify the maker adverse charge, gates, sizing, or orders.",
            end,
            "",
        ]
    )
    marker = "Standing rules (non-negotiable if a human ever acts on this):"
    if start in text and end in text:
        before = text.split(start, 1)[0]
        after = text.split(end, 1)[1].lstrip("\n")
        text = before + block + after
    elif marker in text:
        text = text.replace(marker, block + marker, 1)
    else:
        text = text.rstrip() + "\n\n" + block
    path.write_text(text, encoding="utf-8")


def run_hourly_adverse_study(cfg: EngineConfig) -> dict[str, Any]:
    settings = _settings(cfg)
    out_path = cfg.output_root / "maker_carry" / "hourly_adverse.json"
    payload: dict[str, Any] = {
        "status": "disabled",
        "generated_at_utc": now_utc(),
        "settings": {
            "min_events_per_bucket": int(settings["min_events_per_bucket"]),
            "fdr_alpha": float(settings["fdr_alpha"]),
        },
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    if str(settings.get("enabled", True)).strip().lower() in {"0", "false", "no"}:
        write_json(out_path, payload)
        return payload

    candidates = _candidate_rows(cfg)
    buckets = _bucket_template()
    history = _history_by_token(cfg, settings)
    price_bars = _accumulate_price_bars(buckets, candidates, history)
    prints = _accumulate_prints(cfg, buckets, {str(row["condition_id"]) for row in candidates})
    scored = _score_buckets(buckets, settings)
    status = "ok"
    if not candidates:
        status = "no_candidates"
    elif price_bars == 0:
        status = "no_price_history"
    elif scored["tested_buckets"] == 0:
        status = "insufficient_samples"

    payload.update(
        {
            "status": status,
            "candidate_markets": len(candidates),
            "candidate_tokens_with_history": len({row["token_id"] for row in candidates if row["token_id"] in history}),
            "price_bar_rows": price_bars,
            "trade_print_rows": prints,
            "buckets": buckets,
            "decision_use": "advisory_calm_hours_schedule_only_no_charge_or_gate_change",
            **scored,
        }
    )
    write_json(out_path, payload)
    _patch_quote_sheet(cfg, payload)
    return payload


def main(config_path: str) -> dict[str, Any]:
    return run_hourly_adverse_study(load_config(config_path))
