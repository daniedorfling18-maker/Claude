"""WO-71 coverage-driven suppression for paid sharp-odds requests.

The plan is derived from WO-31 coverage artifacts and persisted for review.
It never edits configuration. Persistently zero-join sport/market families
move to a slow probe cadence and return to normal immediately after a join.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from .config import EngineConfig
from .utils import now_utc, parse_timestamp, read_csv_rows, read_json, safe_float, write_json


OUTPUT_FILE = "sharp_fetch_suppression.json"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _enabled(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = _mapping(cfg.raw.get("collection_hygiene"))
    coverage = _mapping(cfg.raw.get("sharp_anchor_coverage"))
    default_threshold = max(1, int(safe_float(coverage.get("zero_join_cycles_before_flag")) or 5))
    threshold = max(1, int(safe_float(raw.get("zero_join_cycles_before_suppression")) or default_threshold))
    probe_hours = safe_float(raw.get("suppressed_probe_interval_hours"))
    return {
        "enabled": _enabled(raw.get("spend_suppression_enabled"), True),
        "zero_join_cycles_before_suppression": threshold,
        "suppressed_probe_interval_hours": max(1.0, probe_hours if probe_hours is not None else 24.0),
    }


def _key(sport: Any, market_key: Any) -> str:
    return f"{str(sport or '').strip().lower()}|{str(market_key or '').strip().lower()}"


def _history_streaks(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        key = _key(row.get("sport"), row.get("market_key"))
        if key == "|":
            continue
        grouped.setdefault(key, []).append(row)
    out: dict[str, dict[str, Any]] = {}
    for key, family_rows in grouped.items():
        family_rows.sort(
            key=lambda row: str(row.get("anchor_generated_at_utc") or row.get("recorded_at_utc") or ""),
            reverse=True,
        )
        streak = 0
        latest_mapped = 0
        latest_stamp = ""
        for index, row in enumerate(family_rows):
            fetched = int(safe_float(row.get("rows_fetched")) or 0)
            mapped = int(safe_float(row.get("rows_mapped")) or 0)
            if index == 0:
                latest_mapped = mapped
                latest_stamp = str(row.get("anchor_generated_at_utc") or row.get("recorded_at_utc") or "")
            if fetched <= 0:
                continue
            if mapped > 0:
                break
            streak += 1
        sport, market_key = key.split("|", 1)
        out[key] = {
            "sport": sport,
            "market_key": market_key,
            "zero_join_streak": streak,
            "rows_mapped": latest_mapped,
            "coverage_observed_at_utc": latest_stamp,
            "coverage_source": "sharp_anchor_coverage_history.csv",
        }
    return out


def _coverage(cfg: EngineConfig) -> tuple[dict[str, dict[str, Any]], str]:
    history_path = cfg.governance_root / "sharp_anchor_coverage_history.csv"
    rows = _history_streaks(read_csv_rows(history_path))
    summary_path = cfg.governance_root / "sharp_anchor_coverage.json"
    summary = _mapping(read_json(summary_path, default={}) or {})
    summary_stamp = str(summary.get("anchor_generated_at_utc") or summary.get("generated_at_utc") or "")
    sport_markets = summary.get("sport_markets") if isinstance(summary.get("sport_markets"), list) else []
    for raw in sport_markets:
        if not isinstance(raw, Mapping):
            continue
        key = _key(raw.get("sport"), raw.get("market_key"))
        if key == "|":
            continue
        rows[key] = {
            "sport": str(raw.get("sport") or "").strip(),
            "market_key": str(raw.get("market_key") or "").strip(),
            "zero_join_streak": int(safe_float(raw.get("zero_join_streak")) or 0),
            "rows_mapped": int(safe_float(raw.get("rows_mapped")) or 0),
            "classification": str(raw.get("classification") or ""),
            "coverage_observed_at_utc": summary_stamp,
            "coverage_source": "sharp_anchor_coverage.json",
        }
    return rows, summary_stamp


def _utc(as_of: datetime | None) -> datetime:
    current = as_of or datetime.now(timezone.utc)
    return current if current.tzinfo else current.replace(tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_sharp_spend_suppression(
    cfg: EngineConfig,
    sport_configs: Iterable[Mapping[str, Any]],
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    settings = _settings(cfg)
    current = _utc(as_of)
    generated_at = _iso(current)
    coverage, coverage_stamp = _coverage(cfg)
    path = cfg.governance_root / OUTPUT_FILE
    previous = _mapping(read_json(path, default={}) or {})
    previous_rows = {
        _key(row.get("sport"), row.get("market_key")): row
        for row in (previous.get("families") if isinstance(previous.get("families"), list) else [])
        if isinstance(row, Mapping)
    }
    threshold = int(settings["zero_join_cycles_before_suppression"])
    probe_hours = float(settings["suppressed_probe_interval_hours"])
    families: list[dict[str, Any]] = []
    for config in sport_configs:
        sport = str(config.get("sport") or config.get("key") or "").strip()
        markets_raw = config.get("markets") or ["h2h"]
        markets = markets_raw if isinstance(markets_raw, (list, tuple, set)) else str(markets_raw).split(",")
        for market_value in markets:
            market_key = str(market_value or "").strip().lower()
            if not sport or not market_key:
                continue
            key = _key(sport, market_key)
            observed = _mapping(coverage.get(key))
            prior = _mapping(previous_rows.get(key))
            streak = int(safe_float(observed.get("zero_join_streak")) or 0)
            rows_mapped = int(safe_float(observed.get("rows_mapped")) or 0)
            classification = str(observed.get("classification") or "")
            joined = rows_mapped > 0 or classification == "mappable"
            suppressed = bool(settings["enabled"] and not joined and streak >= threshold)
            last_probe = str(prior.get("last_probe_at_utc") or "")
            if suppressed and not last_probe:
                last_probe = str(observed.get("coverage_observed_at_utc") or coverage_stamp or "")
            parsed_probe = parse_timestamp(last_probe)
            next_probe = parsed_probe + timedelta(hours=probe_hours) if parsed_probe is not None else None
            probe_due = bool(suppressed and (next_probe is None or current >= next_probe))
            action = "slow_probe" if probe_due else ("suppress" if suppressed else "fetch")
            if joined:
                last_probe = ""
                next_probe = None
            families.append(
                {
                    "sport": sport,
                    "market_key": market_key,
                    "zero_join_streak": streak,
                    "rows_mapped": rows_mapped,
                    "classification": classification or "unclassified",
                    "coverage_source": observed.get("coverage_source") or "unobserved",
                    "coverage_observed_at_utc": observed.get("coverage_observed_at_utc") or None,
                    "suppressed": suppressed,
                    "action": action,
                    "last_probe_at_utc": last_probe or None,
                    "next_probe_due_at_utc": _iso(next_probe) if next_probe is not None else None,
                    "reason": (
                        f"{streak} consecutive fetched cycles produced zero joins; slow-probe every {probe_hours:g}h"
                        if suppressed
                        else ("join observed; normal cadence restored" if joined else "below suppression evidence threshold")
                    ),
                }
            )
    families.sort(key=lambda row: (row["sport"], row["market_key"]))
    payload = {
        "status": "active" if any(row["suppressed"] for row in families) else "clear",
        "generated_at_utc": generated_at,
        "work_order": "WO-71",
        "source_artifacts": [
            str(cfg.governance_root / "sharp_anchor_coverage.json"),
            str(cfg.governance_root / "sharp_anchor_coverage_history.csv"),
        ],
        "settings": settings,
        "families": families,
        "suppressed_family_count": sum(row["action"] == "suppress" for row in families),
        "slow_probe_family_count": sum(row["action"] == "slow_probe" for row in families),
        "normal_family_count": sum(row["action"] == "fetch" for row in families),
        "config_changed": False,
        "decision_use": "paid API request pacing only; never a model, governance, sizing, or trading input",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    write_json(path, payload)
    return payload


def permitted_markets(plan: Mapping[str, Any], sport: str, markets: Iterable[str]) -> tuple[list[str], list[str]]:
    by_key = {
        _key(row.get("sport"), row.get("market_key")): row
        for row in (plan.get("families") if isinstance(plan.get("families"), list) else [])
        if isinstance(row, Mapping)
    }
    permitted: list[str] = []
    suppressed: list[str] = []
    for market in markets:
        row = _mapping(by_key.get(_key(sport, market)))
        if row.get("action") == "suppress":
            suppressed.append(str(market))
        else:
            permitted.append(str(market))
    return permitted, suppressed


def finalize_sharp_spend_suppression(
    cfg: EngineConfig,
    plan: Mapping[str, Any],
    *,
    attempted_families: Iterable[tuple[str, str]],
    requests_suppressed: int,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    current = _utc(as_of)
    attempted = {_key(sport, market) for sport, market in attempted_families}
    payload = dict(plan)
    families: list[dict[str, Any]] = []
    for raw in plan.get("families", []) if isinstance(plan.get("families"), list) else []:
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        if row.get("action") == "slow_probe" and _key(row.get("sport"), row.get("market_key")) in attempted:
            row["last_probe_at_utc"] = _iso(current)
            interval = float(_mapping(plan.get("settings")).get("suppressed_probe_interval_hours") or 24.0)
            row["next_probe_due_at_utc"] = _iso(current + timedelta(hours=interval))
            row["probe_attempted_this_run"] = True
        else:
            row["probe_attempted_this_run"] = False
        families.append(row)
    payload.update(
        {
            "generated_at_utc": _iso(current),
            "families": families,
            "requests_suppressed_this_run": max(0, int(requests_suppressed)),
            "families_attempted_this_run": sorted(attempted),
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        }
    )
    write_json(cfg.governance_root / OUTPUT_FILE, payload)
    return payload
