"""WO-36 step 4: scorecard for a HUMAN-run minimum-size maker experiment.

If the maker gates ever read evidence-supported, the human may choose to
place small real quotes through the Polymarket interface - outside this
system, with their own judgement and capital. This module gives that
experiment an honest scoreboard using only free, key-less, READ-ONLY public
endpoints on the wallet address:

(a) rewards received   - data-API activity feed, type=REWARD (paid daily at
                         00:00 UTC);
(b) inventory at mark  - open positions valued at current market price: THIS
                         is where maker losses hide while rewards look good;
(c) fills vs model     - activity type=TRADE per day against the study's
                         band-crossing rate x quote-sheet queue share; if
                         real fills outrun the model by ``fill_alert_multiple``
                         the flow is faster than the estimate and quote-sheet
                         rule 4 says STOP.

Winning = cumulative (a) + signed inventory PnL (b) staying positive across
a week, with (c) inside the alert bound. The verdict strings here describe
the HUMAN's experiment; the system itself still never places, amends, or
cancels an order, and an empty ``wallet_address`` (the default) leaves the
module fully inert.
"""
from __future__ import annotations

from typing import Any

import requests

from .config import EngineConfig, load_config
from .utils import append_csv_rows, now_utc, read_csv_rows, read_json, safe_float, write_json

DEFAULT_DATA_API_BASE_URL = "https://data-api.polymarket.com"

LEGACY_HISTORY_FIELDS = [
    "generated_at_utc",
    "rewards_usd_total",
    "rewards_usd_last_24h",
    "inventory_value_usd",
    "inventory_pnl_usd",
    "fills_last_24h",
    "modelled_fills_per_day",
    "fill_alert",
    "net_score_usd",
    "scoreboard",
]
WALLET_HISTORY_FIELDS = [
    "generated_at_utc",
    "wallet_role",
    "wallet_address",
    *LEGACY_HISTORY_FIELDS[1:],
]


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = cfg.raw.get("maker_live_test", {}) if isinstance(cfg.raw.get("maker_live_test"), dict) else {}
    merged = {
        "enabled": True,
        "wallet_address": "",
        "executor_wallet_address": "",
        "data_api_base_url": DEFAULT_DATA_API_BASE_URL,
        "activity_limit": 500,
        "fill_alert_multiple": 2.0,
        "request_timeout_seconds": 20,
    }
    merged.update({k: v for k, v in raw.items() if v is not None})
    return merged


def _fetch(settings: dict[str, Any], path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        response = requests.get(
            f"{str(settings['data_api_base_url']).rstrip('/')}/{path}",
            params=params,
            timeout=float(settings["request_timeout_seconds"]),
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return []
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    return []


def _usd(row: dict[str, Any]) -> float:
    value = safe_float(row.get("usdcSize"))
    if value is None:
        size = safe_float(row.get("size")) or 0.0
        price = safe_float(row.get("price")) or 0.0
        value = size * price
    return value or 0.0


def _stamp(row: dict[str, Any]) -> float:
    return safe_float(row.get("timestamp")) or 0.0


def _modelled_fills_per_day(cfg: EngineConfig) -> float | None:
    """Expected OWN fills/day: study band-crossing rate x our queue share,
    summed over the current quote-sheet portfolio."""
    study = read_json(cfg.output_root / "maker_carry" / "maker_carry_study.json", default={}) or {}
    portfolio = study.get("portfolio") or []
    if not portfolio:
        return None
    by_condition: dict[str, dict[str, Any]] = {}
    for row in read_csv_rows(cfg.output_root / "maker_carry" / "maker_carry_candidates.csv"):
        by_condition[str(row.get("condition_id") or "")] = row
    total = 0.0
    seen = False
    for entry in portfolio:
        candidate = by_condition.get(str(entry.get("condition_id") or ""))
        if not candidate:
            continue
        crossings = safe_float(candidate.get("band_crossing_prints_per_day"))
        if crossings is None:
            continue
        quote_size = safe_float(entry.get("quote_size_shares")) or 0.0
        depth = min(
            safe_float(candidate.get("competitor_score_bid")) or 0.0,
            safe_float(candidate.get("competitor_score_ask")) or 0.0,
        )
        queue_share = quote_size / (quote_size + depth) if (quote_size + depth) > 0 else 0.0
        total += crossings * queue_share
        seen = True
    return round(total, 2) if seen else None


def _wallet_score(
    cfg: EngineConfig,
    settings: dict[str, Any],
    *,
    wallet_role: str,
    wallet_address: str,
    generated_at: str,
) -> dict[str, Any]:
    limit = int(settings["activity_limit"])
    rewards = _fetch(settings, "activity", {"user": wallet_address, "type": "REWARD", "limit": limit})
    trades = _fetch(settings, "activity", {"user": wallet_address, "type": "TRADE", "limit": limit})
    positions = _fetch(settings, "positions", {"user": wallet_address, "limit": limit})

    now_seconds = max([_stamp(row) for row in rewards + trades] + [0.0])
    day_ago = now_seconds - 86400.0
    rewards_total = round(sum(_usd(row) for row in rewards), 4)
    rewards_24h = round(sum(_usd(row) for row in rewards if _stamp(row) >= day_ago), 4)
    fills_24h = sum(1 for row in trades if _stamp(row) >= day_ago)

    inventory_value = 0.0
    inventory_pnl = 0.0
    for position in positions:
        size = safe_float(position.get("size")) or 0.0
        current = safe_float(position.get("curPrice"))
        if current is None:
            current = safe_float(position.get("currentValue")) or 0.0
            value = current
        else:
            value = size * current
        inventory_value += value
        pnl = safe_float(position.get("cashPnl"))
        if pnl is None:
            average = safe_float(position.get("avgPrice")) or 0.0
            pnl = value - size * average
        inventory_pnl += pnl

    modelled = _modelled_fills_per_day(cfg)
    fill_alert = bool(
        modelled is not None
        and modelled > 0
        and fills_24h > float(settings["fill_alert_multiple"]) * modelled
    )
    net_score = round(rewards_total + inventory_pnl, 4)
    if fill_alert:
        scoreboard = "STOP_fills_outrunning_model"
    elif not rewards and not trades and not positions:
        scoreboard = "no_activity_yet"
    elif net_score >= 0:
        scoreboard = "winning_so_far"
    else:
        scoreboard = "losing_so_far"
    return {
        "status": "ok",
        "generated_at_utc": generated_at,
        "wallet_role": wallet_role,
        "wallet_address": wallet_address,
        "rewards_usd_total": rewards_total,
        "rewards_usd_last_24h": rewards_24h,
        "inventory_value_usd": round(inventory_value, 4),
        "inventory_pnl_usd": round(inventory_pnl, 4),
        "fills_last_24h": fills_24h,
        "modelled_fills_per_day": modelled,
        "fill_alert_multiple": float(settings["fill_alert_multiple"]),
        "fill_alert": fill_alert,
        "net_score_usd": net_score,
        "scoreboard": scoreboard,
        "scoring_rule": (
            "winning = cumulative rewards + signed inventory PnL held positive across a week, "
            "with real fills inside the modelled alert bound."
        ),
        "read_only": True,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }


def run_maker_live_test(cfg: EngineConfig) -> dict[str, Any]:
    settings = _settings(cfg)
    out_root = cfg.output_root / "maker_carry"
    summary_path = out_root / "maker_live_test.json"
    summary: dict[str, Any] = {
        "status": "disabled",
        "generated_at_utc": now_utc(),
        "work_order": "WO-36+WO-73+WO-81",
        "account_structure": "single_project_account_under_custody_amendment_A1",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
        "note": (
            "READ-ONLY scoreboard for a human-run experiment. This system never places orders; "
            "it only watches the wallet the human chose to trade from."
        ),
    }
    if str(settings.get("enabled", True)).strip().lower() in {"0", "false", "no"}:
        write_json(summary_path, summary)
        return summary
    wallets = {
        "operator": str(settings.get("wallet_address") or "").strip(),
        "executor": str(settings.get("executor_wallet_address") or "").strip(),
    }
    configured = {role: wallet for role, wallet in wallets.items() if wallet}
    if not configured:
        summary.update(
            {
                "status": "awaiting_wallet_address",
                "wallets": {
                    role: {
                        "status": "not_configured",
                        "wallet_role": role,
                        "wallet_address": "",
                    }
                    for role in wallets
                },
                "wallets_combined": False,
                "how_to_enable": "set the public maker_live_test.wallet_address, then recreate the containers",
            }
        )
        write_json(summary_path, summary)
        return summary
    scored = {
        role: _wallet_score(
            cfg,
            settings,
            wallet_role=role,
            wallet_address=wallet,
            generated_at=summary["generated_at_utc"],
        )
        for role, wallet in configured.items()
    }
    wallet_rows = {
        role: scored.get(
            role,
            {
                "status": "not_configured",
                "wallet_role": role,
                "wallet_address": "",
                "paper_trading_invoked": False,
                "live_trading_invoked": False,
            },
        )
        for role in wallets
    }
    primary_role = "operator" if "operator" in scored else "executor"
    summary.update(scored[primary_role])
    summary.update(
        {
            "status": "ok",
            "work_order": "WO-36+WO-73+WO-81",
            "account_structure": "single_project_account_under_custody_amendment_A1",
            "attribution_policy": "human and executor mode/time windows never overlap; anchored ledgers attribute activity",
            "primary_wallet_role": primary_role,
            "operator_wallet_address": wallets["operator"],
            "executor_wallet_address": wallets["executor"],
            "wallets": wallet_rows,
            "wallets_combined": False,
            "aggregation_policy": (
                "single project wallet under A1; legacy separate-wallet rows are never summed if a superseded field is populated"
            ),
            "legacy_executor_wallet_config_drift": bool(wallets["executor"]),
            "auto_redeem_wins_owner_check": "required_on_single_project_account",
        }
    )
    write_json(summary_path, summary)
    # ``maker_live_test_history.csv`` was enrolled as append-only before
    # WO-73 introduced wallet roles. Keep its original schema immutable and
    # append only the primary wallet. Role-aware rows live in a new ledger.
    legacy_path = out_root / "maker_live_test_history.csv"
    legacy_row = {field: scored[primary_role].get(field) for field in LEGACY_HISTORY_FIELDS}
    legacy_keys = {
        tuple(str(row.get(field) or "") for field in LEGACY_HISTORY_FIELDS)
        for row in read_csv_rows(legacy_path)
    }
    legacy_key = tuple(str(legacy_row.get(field) or "") for field in LEGACY_HISTORY_FIELDS)
    if legacy_key not in legacy_keys:
        append_csv_rows(legacy_path, [legacy_row], fieldnames=LEGACY_HISTORY_FIELDS)

    wallet_path = out_root / "maker_live_test_wallet_history.csv"
    wallet_rows = [
        {field: row.get(field) for field in WALLET_HISTORY_FIELDS}
        for row in scored.values()
    ]
    wallet_keys = {
        tuple(str(row.get(field) or "") for field in WALLET_HISTORY_FIELDS)
        for row in read_csv_rows(wallet_path)
    }
    new_wallet_rows = [
        row
        for row in wallet_rows
        if tuple(str(row.get(field) or "") for field in WALLET_HISTORY_FIELDS) not in wallet_keys
    ]
    append_csv_rows(wallet_path, new_wallet_rows, fieldnames=WALLET_HISTORY_FIELDS)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return run_maker_live_test(load_config(config_path))
