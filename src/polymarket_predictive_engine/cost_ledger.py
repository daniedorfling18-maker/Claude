"""WO-63 investor-grade true-net cost ledger.

The ledger is deliberately append-only and reporting-only.  Manual rail,
subscription, and other costs enter through ``add-cost``; investor-paid gas
observed by WO-62 is converted from POL to USD with CoinGecko's public
simple-price endpoint.  Relayer-paid transactions remain observations and are
never charged to the investor.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timezone
import math
import os
from pathlib import Path
import time
from typing import Any, Iterable

import requests

from .config import EngineConfig, load_config
from .profit_verdict import effective_verdict_settings
from .runtime_lock import runtime_lock
from .utils import now_utc, parse_timestamp, read_csv_rows, safe_float, write_json


CATEGORIES = ("gas", "rail", "subscription", "other")
LEDGER_FIELDS = ["date", "category", "usd", "cost_ref", "note"]
COINGECKO_SOURCE_NAME = "CoinGecko simple-price"


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = cfg.raw.get("cost_ledger", {}) if isinstance(cfg.raw.get("cost_ledger"), dict) else {}
    merged: dict[str, Any] = {
        "enabled": True,
        "ledger_file": "performance/cost_ledger.csv",
        "summary_file": "performance/cost_ledger_summary.json",
        "gas_observations_file": "performance/wallet_gas_observations.csv",
        "coingecko_simple_price_url": "https://api.coingecko.com/api/v3/simple/price",
        "pol_coin_id": "polygon-ecosystem-token",
        "request_timeout_seconds": 20,
        "request_pause_seconds": 0.1,
        "lock_stale_seconds": 1800,
    }
    merged.update({key: value for key, value in raw.items() if value is not None})
    return merged


def _enabled(value: Any) -> bool:
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _relative_output_path(cfg: EngineConfig, value: Any) -> Path:
    text = str(value or "").strip().replace("\\", "/")
    path = Path(text)
    if not text or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"cost-ledger path must stay below paths.output_root: {value!r}")
    return cfg.output_root / path


def _canonical_ref(value: Any) -> str:
    return str(value or "").strip().casefold()


def _normalise_date(value: Any | None) -> str:
    text = str(value or now_utc()[:10]).strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError("cost date must be an ISO date (YYYY-MM-DD)") from exc


def _normalise_entry(
    *,
    cost_date: Any,
    category: Any,
    usd: Any,
    cost_ref: Any,
    note: Any = "",
) -> dict[str, Any]:
    category_text = str(category or "").strip().lower()
    if category_text not in CATEGORIES:
        raise ValueError(f"cost category must be one of: {', '.join(CATEGORIES)}")
    amount = safe_float(usd)
    if amount is None or not math.isfinite(amount) or amount <= 0:
        raise ValueError("cost usd must be a finite positive amount")
    reference = str(cost_ref or "").strip()
    if not reference:
        raise ValueError("cost_ref is required and must be unique")
    return {
        "date": _normalise_date(cost_date),
        "category": category_text,
        "usd": round(float(amount), 8),
        "cost_ref": reference,
        "note": str(note or "").strip(),
    }


def _existing_header(path: Path) -> list[str]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", newline="", encoding="utf-8-sig", errors="strict") as handle:
        try:
            return [str(value) for value in next(csv.reader(handle))]
        except StopIteration:
            return []


def _append_entries(path: Path, entries: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Append new unique rows without ever rewriting existing ledger bytes."""

    path.parent.mkdir(parents=True, exist_ok=True)
    header = _existing_header(path)
    if header and header != LEDGER_FIELDS:
        raise ValueError(f"cost ledger header drift: expected {LEDGER_FIELDS}, found {header}")
    existing = read_csv_rows(path)
    seen = {_canonical_ref(row.get("cost_ref")) for row in existing if _canonical_ref(row.get("cost_ref"))}
    added: list[dict[str, Any]] = []
    duplicates: list[str] = []
    for row in entries:
        key = _canonical_ref(row.get("cost_ref"))
        if key in seen:
            duplicates.append(str(row.get("cost_ref") or ""))
            continue
        seen.add(key)
        added.append(row)
    if not added:
        return added, duplicates

    needs_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEDGER_FIELDS, extrasaction="ignore")
        if needs_header:
            writer.writeheader()
        for row in added:
            writer.writerow({field: row.get(field, "") for field in LEDGER_FIELDS})
        handle.flush()
        os.fsync(handle.fileno())
    return added, duplicates


def aggregate_costs(
    cfg: EngineConfig,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Aggregate booked costs over an inclusive UTC date window."""

    settings = _settings(cfg)
    path = _relative_output_path(cfg, settings["ledger_file"])
    start = date.fromisoformat(start_date) if start_date else None
    end = date.fromisoformat(end_date) if end_date else None
    if start and end and start > end:
        raise ValueError("cost aggregation start_date cannot be after end_date")
    by_category = {category: 0.0 for category in CATEGORIES}
    row_count = 0
    invalid_rows = 0
    for row in read_csv_rows(path):
        try:
            row_date = date.fromisoformat(str(row.get("date") or ""))
        except ValueError:
            invalid_rows += 1
            continue
        amount = safe_float(row.get("usd"))
        category = str(row.get("category") or "").strip().lower()
        if amount is None or amount < 0 or category not in by_category:
            invalid_rows += 1
            continue
        if start and row_date < start:
            continue
        if end and row_date > end:
            continue
        by_category[category] += float(amount)
        row_count += 1
    rounded = {key: round(value, 8) for key, value in by_category.items()}
    return {
        "start_date": start.isoformat() if start else None,
        "end_date": end.isoformat() if end else None,
        "row_count": row_count,
        "invalid_rows": invalid_rows,
        "by_category_usd": rounded,
        "total_usd": round(sum(rounded.values()), 8),
        "ledger_path": str(path),
    }


def registered_hypothetical_cost_stack(cfg: EngineConfig) -> dict[str, Any]:
    """Return the registered conservative fee/haircut scenario for simulations."""

    settings = effective_verdict_settings(cfg)
    components = {
        "exit_cost_haircut_per_dollar": float(settings["exit_cost_haircut_per_dollar"]),
        "adverse_selection_haircut_per_dollar": float(settings["adverse_selection_haircut_per_dollar"]),
        "taker_fee_rate_ceiling": float(settings["taker_fee_rate"]),
    }
    return {
        "components": components,
        "registered_ceiling_per_dollar": round(sum(components.values()), 8),
        "usd_per_100_turnover": round(100.0 * sum(components.values()), 8),
        "basis": (
            "registered profit-verdict execution stack; taker fees are outcome-price-dependent, "
            "so the registered rate is shown as a conservative ceiling"
        ),
        "scenario_only": True,
        "booked_to_cost_ledger": False,
    }


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _observation_date(row: dict[str, Any]) -> str:
    raw = row.get("activity_timestamp")
    numeric = safe_float(raw)
    if numeric is not None and numeric > 0:
        if numeric > 10**12:
            numeric /= 1000.0
        try:
            return datetime.fromtimestamp(numeric, tz=timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            pass
    parsed = parse_timestamp(raw) or parse_timestamp(row.get("captured_at_utc"))
    return (parsed.date().isoformat() if parsed else now_utc()[:10])


def _fetch_pol_usd(settings: dict[str, Any]) -> dict[str, Any]:
    source = {
        "name": COINGECKO_SOURCE_NAME,
        "url": str(settings["coingecko_simple_price_url"]),
        "coin_id": str(settings["pol_coin_id"]),
        "quote_currency": "usd",
        "status": "unavailable",
        "price_usd": None,
        "last_updated_at_epoch": None,
        "error": "",
    }
    try:
        response = requests.get(
            source["url"],
            params={
                "ids": source["coin_id"],
                "vs_currencies": "usd",
                "include_last_updated_at": "true",
            },
            timeout=float(settings["request_timeout_seconds"]),
        )
        response.raise_for_status()
        payload = response.json()
        quote = payload.get(source["coin_id"]) if isinstance(payload, dict) else None
        price = safe_float(quote.get("usd")) if isinstance(quote, dict) else None
        if price is None or price <= 0:
            raise ValueError("CoinGecko response did not contain a positive POL/USD price")
        source.update(
            {
                "status": "ok",
                "price_usd": float(price),
                "last_updated_at_epoch": quote.get("last_updated_at"),
            }
        )
    except Exception as exc:  # fail-soft: the summary directs a manual entry
        source["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        time.sleep(max(0.1, float(settings.get("request_pause_seconds") or 0.1)))
    return source


def _base_summary(cfg: EngineConfig, settings: dict[str, Any], *, status: str) -> dict[str, Any]:
    return {
        "status": status,
        "generated_at_utc": now_utc(),
        "work_order": "WO-63",
        "reporting_only": True,
        "ledger_path": str(_relative_output_path(cfg, settings["ledger_file"])),
        "summary_path": str(_relative_output_path(cfg, settings["summary_file"])),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }


def _write_summary(cfg: EngineConfig, settings: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    payload["aggregate_all_time"] = aggregate_costs(cfg)
    write_json(_relative_output_path(cfg, settings["summary_file"]), payload)
    return payload


def add_manual_cost(
    cfg: EngineConfig,
    *,
    category: str,
    usd: float,
    cost_ref: str,
    note: str = "",
    cost_date: str | None = None,
) -> dict[str, Any]:
    """Append one manual cost, idempotently by ``cost_ref``."""

    settings = _settings(cfg)
    payload = _base_summary(cfg, settings, status="disabled")
    if not _enabled(settings.get("enabled", True)):
        return _write_summary(cfg, settings, payload)
    entry = _normalise_entry(
        cost_date=cost_date,
        category=category,
        usd=usd,
        cost_ref=cost_ref,
        note=note,
    )
    with runtime_lock(
        cfg,
        "cost_ledger",
        stale_after_seconds=float(settings["lock_stale_seconds"]),
    ) as lock:
        if not lock.acquired:
            payload.update({"status": "skipped_locked", "runtime_lock": lock.as_dict(), "new_rows": 0})
            return _write_summary(cfg, settings, payload)
        added, duplicates = _append_entries(
            _relative_output_path(cfg, settings["ledger_file"]),
            [entry],
        )
    payload.update(
        {
            "status": "ok",
            "entry": entry,
            "new_rows": len(added),
            "duplicate_cost_refs": duplicates,
            "idempotent_duplicate": not bool(added),
        }
    )
    return _write_summary(cfg, settings, payload)


def sync_cost_ledger(cfg: EngineConfig) -> dict[str, Any]:
    """Book newly observed investor-paid gas; fail soft when POL/USD is absent."""

    settings = _settings(cfg)
    payload = _base_summary(cfg, settings, status="disabled")
    if not _enabled(settings.get("enabled", True)):
        return _write_summary(cfg, settings, payload)

    ledger_path = _relative_output_path(cfg, settings["ledger_file"])
    observations_path = _relative_output_path(cfg, settings["gas_observations_file"])
    existing_refs = {
        _canonical_ref(row.get("cost_ref"))
        for row in read_csv_rows(ledger_path)
        if _canonical_ref(row.get("cost_ref"))
    }
    pending: list[dict[str, Any]] = []
    relayer_observations = 0
    invalid_observations = 0
    for row in read_csv_rows(observations_path):
        if not _truthy(row.get("attributed_to_wallet")):
            relayer_observations += 1
            continue
        tx_hash = str(row.get("transaction_hash") or "").strip().lower()
        gas_pol = safe_float(row.get("gas_cost_pol"))
        cost_ref = f"gas:{tx_hash}"
        if not tx_hash or gas_pol is None or gas_pol <= 0:
            invalid_observations += 1
            continue
        if _canonical_ref(cost_ref) in existing_refs:
            continue
        pending.append({"row": row, "tx_hash": tx_hash, "gas_pol": float(gas_pol), "cost_ref": cost_ref})

    rate_source: dict[str, Any]
    manual_required: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    if pending:
        rate_source = _fetch_pol_usd(settings)
        rate = safe_float(rate_source.get("price_usd"))
        if rate_source.get("status") == "ok" and rate is not None:
            for item in pending:
                entries.append(
                    _normalise_entry(
                        cost_date=_observation_date(item["row"]),
                        category="gas",
                        usd=item["gas_pol"] * rate,
                        cost_ref=item["cost_ref"],
                        note=(
                            f"{item['gas_pol']:.18f} POL at ${rate:.8f}/POL via {COINGECKO_SOURCE_NAME}; "
                            f"investor-paid transaction {item['tx_hash']}"
                        ),
                    )
                )
        else:
            manual_required = [
                {
                    "transaction_hash": item["tx_hash"],
                    "gas_cost_pol": item["gas_pol"],
                    "suggested_cost_ref": item["cost_ref"],
                    "reason": "POL/USD unavailable; use add-cost after independently valuing the gas",
                }
                for item in pending
            ]
    else:
        rate_source = {
            "name": COINGECKO_SOURCE_NAME,
            "url": str(settings["coingecko_simple_price_url"]),
            "coin_id": str(settings["pol_coin_id"]),
            "status": "not_requested_no_pending_gas",
            "price_usd": None,
        }

    with runtime_lock(
        cfg,
        "cost_ledger",
        stale_after_seconds=float(settings["lock_stale_seconds"]),
    ) as lock:
        if not lock.acquired:
            payload.update({"status": "skipped_locked", "runtime_lock": lock.as_dict(), "new_rows": 0})
            return _write_summary(cfg, settings, payload)
        added, duplicates = _append_entries(ledger_path, entries)

    payload.update(
        {
            "status": "partial_manual_required" if manual_required else "ok",
            "gas_observations_path": str(observations_path),
            "pending_attributed_gas_rows": len(pending),
            "relayer_paid_observations_excluded": relayer_observations,
            "invalid_gas_observations": invalid_observations,
            "new_rows": len(added),
            "duplicate_cost_refs": duplicates,
            "pol_usd_source": rate_source,
            "manual_gas_entries_required": manual_required,
        }
    )
    return _write_summary(cfg, settings, payload)


def main(config_path: str) -> dict[str, Any]:
    return sync_cost_ledger(load_config(config_path))
