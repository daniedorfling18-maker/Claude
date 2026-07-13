"""WO-62 read-only three-way live-wallet reconciliation.

Compares a human live-test scoreboard, public data-API activity/positions,
and Polygon state (pUSD plus CTF ERC-1155 balances) on a like-for-like NAV
basis. Contract addresses are parsed from Polymarket's official contracts
page and cached with source provenance; no venue address is embedded here.

Reporting only. This module has no authentication, signing, order, gate, or
sizing path and always records paper/live trading invocation as false.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any, Iterable

import requests

from .config import EngineConfig, load_config
from .utils import append_csv_rows, now_utc, read_csv_rows, read_json, safe_float, write_csv, write_json


OFFICIAL_CONTRACTS_URL = "https://docs.polymarket.com/resources/contracts"
ADDRESS_RE = re.compile(r"0x[a-fA-F0-9]{40}")
WALLET_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
TX_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")
ERC20_BALANCE_OF_SELECTOR = "70a08231"
ERC1155_BALANCE_OF_SELECTOR = "00fdd58e"

LEGACY_HISTORY_FIELDS = [
    "generated_at_utc",
    "snapshot_date",
    "wallet_address",
    "reconciliation_status",
    "internal_nav_usd",
    "data_api_nav_usd",
    "onchain_nav_usd",
    "max_pairwise_delta_usd",
    "unexplained_discrepancy_usd",
    "contracts_page_sha256",
    "rpc_chain_id",
]
WALLET_HISTORY_FIELDS = [
    "generated_at_utc",
    "snapshot_date",
    "wallet_role",
    *LEGACY_HISTORY_FIELDS[2:],
]

GAS_FIELDS = [
    "transaction_hash",
    "activity_timestamp",
    "payer_address",
    "attributed_to_wallet",
    "gas_used",
    "effective_gas_price_wei",
    "gas_cost_wei",
    "gas_cost_pol",
    "captured_at_utc",
    "rpc_chain_id",
]


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = cfg.raw.get("wallet_reconciliation", {}) if isinstance(cfg.raw.get("wallet_reconciliation"), dict) else {}
    merged = {
        "enabled": True,
        "data_api_base_url": "https://data-api.polymarket.com",
        "official_contracts_url": OFFICIAL_CONTRACTS_URL,
        "polygon_rpc_url": "https://polygon-rpc.com",
        "request_timeout_seconds": 20,
        "request_pause_seconds": 0.1,
        "positions_page_limit": 500,
        "activity_page_limit": 500,
        "max_pages": 20,
        "activity_start_epoch": 1,
        "max_onchain_positions": 100,
        "collateral_decimals": 6,
        "position_decimals": 6,
        "discrepancy_threshold_usd": 1.0,
        "position_quantity_tolerance": 0.000001,
        "starting_nav_usd": None,
        "operator_starting_nav_usd": None,
        "executor_starting_nav_usd": None,
        "baseline_epoch": 1,
        "gas_capture_enabled": True,
        "gas_scan_max_transactions": 50,
        "gas_payer_addresses": [],
        "max_gas_rows": 100000,
    }
    merged.update({key: value for key, value in raw.items() if value is not None})
    return merged


def _enabled(value: Any) -> bool:
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _wallets(cfg: EngineConfig) -> dict[str, str]:
    raw = cfg.raw.get("maker_live_test", {}) if isinstance(cfg.raw.get("maker_live_test"), dict) else {}
    return {
        "operator": str(raw.get("wallet_address") or "").strip(),
        "executor": str(raw.get("executor_wallet_address") or "").strip(),
    }


def _pause(settings: dict[str, Any]) -> None:
    time.sleep(max(0.1, float(settings.get("request_pause_seconds") or 0.1)))


def _get_json(settings: dict[str, Any], url: str, *, params: dict[str, Any]) -> Any:
    try:
        response = requests.get(url, params=params, timeout=float(settings["request_timeout_seconds"]))
        response.raise_for_status()
        return response.json()
    finally:
        _pause(settings)


def _fetch_pages(
    settings: dict[str, Any],
    *,
    path: str,
    wallet: str,
    limit_key: str,
    extra: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    limit = max(1, min(500, int(settings[limit_key])))
    max_pages = max(1, int(settings["max_pages"]))
    rows: list[dict[str, Any]] = []
    complete = False
    for page in range(max_pages):
        params: dict[str, Any] = {"user": wallet, "limit": limit, "offset": page * limit}
        params.update(extra or {})
        payload = _get_json(
            settings,
            f"{str(settings['data_api_base_url']).rstrip('/')}/{path.lstrip('/')}",
            params=params,
        )
        page_rows = payload if isinstance(payload, list) else []
        rows.extend(row for row in page_rows if isinstance(row, dict))
        if len(page_rows) < limit:
            complete = True
            break
    return rows, complete


def parse_official_contracts_page(text: str) -> dict[str, str]:
    """Extract only named addresses from the fetched official contracts page."""

    def after(labels: Iterable[str]) -> str:
        lowered = text.lower()
        locations = [lowered.find(label.lower()) for label in labels]
        locations = [location for location in locations if location >= 0]
        if not locations:
            return ""
        segment = text[min(locations) : min(locations) + 1200]
        match = ADDRESS_RE.search(segment)
        return match.group(0) if match else ""

    addresses = {
        "pusd": after(["pUSD — CollateralToken (proxy)", "pUSD - CollateralToken (proxy)", "CollateralToken (proxy)"]),
        "ctf": after(["Conditional Tokens (CTF)", "Conditional Tokens"]),
    }
    return {key: value for key, value in addresses.items() if ADDRESS_RE.fullmatch(value)}


def _contract_registry(cfg: EngineConfig, settings: dict[str, Any]) -> dict[str, Any]:
    path = cfg.output_root / "performance" / "wallet_contract_registry.json"
    source_url = str(settings["official_contracts_url"])
    try:
        try:
            response = requests.get(source_url, timeout=float(settings["request_timeout_seconds"]))
            response.raise_for_status()
            text = response.text
        finally:
            _pause(settings)
        addresses = parse_official_contracts_page(text)
        if set(addresses) != {"pusd", "ctf"}:
            raise ValueError("official contracts page did not expose both pUSD and CTF addresses")
        payload = {
            "status": "official_live",
            "fetched_at_utc": now_utc(),
            "source_url": source_url,
            "source_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "addresses": addresses,
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        }
        write_json(path, payload)
        return payload
    except Exception as exc:
        cached = read_json(path, default={}) or {}
        cached_addresses = cached.get("addresses") if isinstance(cached, dict) else {}
        if (
            isinstance(cached_addresses, dict)
            and set(cached_addresses) >= {"pusd", "ctf"}
            and all(ADDRESS_RE.fullmatch(str(cached_addresses[key])) for key in ("pusd", "ctf"))
            and str(cached.get("source_url") or "") == source_url
        ):
            return {
                **cached,
                "status": "cached_official",
                "refresh_error": f"{type(exc).__name__}: {exc}",
                "paper_trading_invoked": False,
                "live_trading_invoked": False,
            }
        return {
            "status": "unavailable",
            "source_url": source_url,
            "error": f"{type(exc).__name__}: {exc}",
            "addresses": {},
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        }


def _activity_key(row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(row.get(key) or "") for key in ("transactionHash", "timestamp", "type", "side", "asset", "size", "usdcSize"))


def _dedup_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, ...]] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        key = _activity_key(row)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _usd(row: dict[str, Any]) -> float:
    value = safe_float(row.get("usdcSize"))
    if value is not None:
        return abs(value)
    size = safe_float(row.get("size")) or 0.0
    price = safe_float(row.get("price")) or 0.0
    return abs(size * price)


def _activity_cash(rows: list[dict[str, Any]], *, baseline_epoch: int = 1) -> dict[str, Any]:
    cash = 0.0
    deposits = 0.0
    withdrawals = 0.0
    rewards = 0.0
    unknown_types: set[str] = set()
    positive = {"DEPOSIT", "REWARD", "YIELD", "MAKER_REBATE", "TAKER_REBATE", "REFERRAL_REWARD", "REDEEM", "MERGE"}
    negative = {"WITHDRAWAL", "SPLIT"}
    for row in rows:
        timestamp = int(safe_float(row.get("timestamp")) or 0)
        if timestamp < baseline_epoch:
            continue
        activity_type = str(row.get("type") or "").strip().upper()
        side = str(row.get("side") or "").strip().upper()
        amount = _usd(row)
        if activity_type in positive:
            cash += amount
        elif activity_type in negative:
            cash -= amount
        elif activity_type in {"TRADE", "CONVERSION"} and side == "BUY":
            cash -= amount
        elif activity_type in {"TRADE", "CONVERSION"} and side == "SELL":
            cash += amount
        else:
            unknown_types.add(activity_type or "missing")
        if activity_type == "DEPOSIT":
            deposits += amount
        elif activity_type == "WITHDRAWAL":
            withdrawals += amount
        elif activity_type in {"REWARD", "YIELD", "MAKER_REBATE", "TAKER_REBATE", "REFERRAL_REWARD"}:
            rewards += amount
    return {
        "reconstructed_cash_usd": round(cash, 8),
        "deposits_usd": round(deposits, 8),
        "withdrawals_usd": round(withdrawals, 8),
        "paid_rewards_usd": round(rewards, 8),
        "unknown_activity_types": sorted(unknown_types),
    }


def _position_mark(row: dict[str, Any]) -> float | None:
    mark = safe_float(row.get("curPrice"))
    size = safe_float(row.get("size"))
    if mark is None and size is not None and size > 0:
        current_value = safe_float(row.get("currentValue"))
        if current_value is not None:
            mark = current_value / size
    if mark is None:
        mark = safe_float(row.get("avgPrice"))
    return mark if mark is not None and 0 <= mark <= 1 else None


def _positions_value(rows: list[dict[str, Any]]) -> tuple[float, list[dict[str, Any]], list[str]]:
    total = 0.0
    marked: list[dict[str, Any]] = []
    errors: list[str] = []
    for row in rows:
        asset = str(row.get("asset") or "").strip()
        size = safe_float(row.get("size"))
        mark = _position_mark(row)
        if not asset or size is None or size < 0 or mark is None:
            errors.append(asset or str(row.get("conditionId") or "unknown"))
            continue
        value = size * mark
        total += value
        marked.append({**row, "_mark": mark, "_marked_value": value})
    return round(total, 8), marked, errors


def _data_api_snapshot(settings: dict[str, Any], wallet: str) -> dict[str, Any]:
    errors: list[str] = []
    positions: list[dict[str, Any]] = []
    activities: list[dict[str, Any]] = []
    positions_complete = False
    activity_complete = False
    official_positions_value: float | None = None
    try:
        positions, positions_complete = _fetch_pages(
            settings,
            path="positions",
            wallet=wallet,
            limit_key="positions_page_limit",
            extra={"sizeThreshold": 0},
        )
    except Exception as exc:
        errors.append(f"positions: {type(exc).__name__}: {exc}")
    try:
        activities, activity_complete = _fetch_pages(
            settings,
            path="activity",
            wallet=wallet,
            limit_key="activity_page_limit",
            extra={"start": int(settings["activity_start_epoch"]), "sortDirection": "ASC"},
        )
    except Exception as exc:
        errors.append(f"activity: {type(exc).__name__}: {exc}")
    try:
        value_payload = _get_json(
            settings,
            f"{str(settings['data_api_base_url']).rstrip('/')}/value",
            params={"user": wallet},
        )
        if isinstance(value_payload, list) and value_payload:
            official_positions_value = safe_float(value_payload[0].get("value"))
    except Exception as exc:
        errors.append(f"value: {type(exc).__name__}: {exc}")

    activities = _dedup_rows(activities)
    positions_value, marked_positions, mark_errors = _positions_value(positions)
    cash = _activity_cash(activities, baseline_epoch=int(settings["activity_start_epoch"]))
    reconstruction_complete = bool(activity_complete and positions_complete and not cash["unknown_activity_types"])
    nav = cash["reconstructed_cash_usd"] + positions_value if reconstruction_complete else None
    return {
        "status": "ok" if nav is not None and not errors and not mark_errors else "partial",
        "nav_usd": None if nav is None else round(nav, 8),
        "positions_value_usd": positions_value,
        "official_positions_value_usd": official_positions_value,
        "positions_value_delta_usd": None if official_positions_value is None else round(positions_value - official_positions_value, 8),
        "reconstructed_cash_usd": cash["reconstructed_cash_usd"],
        "deposits_usd": cash["deposits_usd"],
        "withdrawals_usd": cash["withdrawals_usd"],
        "paid_rewards_usd": cash["paid_rewards_usd"],
        "positions": marked_positions,
        "activities": activities,
        "positions_complete": positions_complete,
        "activity_complete": activity_complete,
        "unknown_activity_types": cash["unknown_activity_types"],
        "unmarked_assets": mark_errors,
        "errors": errors,
    }


def _internal_snapshot(
    cfg: EngineConfig,
    settings: dict[str, Any],
    api: dict[str, Any],
    *,
    wallet_role: str = "operator",
) -> dict[str, Any]:
    path = cfg.output_root / "maker_carry" / "maker_live_test.json"
    root_scoreboard = read_json(path, default={}) or {}
    wallet_scoreboards = root_scoreboard.get("wallets") if isinstance(root_scoreboard.get("wallets"), dict) else {}
    scoreboard = wallet_scoreboards.get(wallet_role) if isinstance(wallet_scoreboards.get(wallet_role), dict) else {}
    if not scoreboard and wallet_role == "operator":
        scoreboard = root_scoreboard
    cash = next(
        (
            value
            for value in (
                safe_float(scoreboard.get("cash_usd")),
                safe_float(scoreboard.get("cash_balance_usd")),
                safe_float(scoreboard.get("pusd_balance_usd")),
            )
            if value is not None
        ),
        None,
    )
    positions = next(
        (
            value
            for value in (
                safe_float(scoreboard.get("marked_positions_usd")),
                safe_float(scoreboard.get("inventory_value_usd")),
            )
            if value is not None
        ),
        None,
    )
    pending_rewards = safe_float(scoreboard.get("accrued_rewards_usd"))
    if pending_rewards is None:
        pending_rewards = safe_float(scoreboard.get("pending_rewards_usd")) or 0.0
    source = "scoreboard_components"
    nav: float | None = None
    baseline_flows = _activity_cash(
        api.get("activities") or [],
        baseline_epoch=int(settings.get("baseline_epoch") or 1),
    )
    if cash is not None and positions is not None:
        nav = cash + positions + pending_rewards
    else:
        role_setting = f"{wallet_role}_starting_nav_usd"
        starting_nav = safe_float(settings.get(role_setting))
        if starting_nav is None and wallet_role == "operator":
            starting_nav = safe_float(settings.get("starting_nav_usd"))
        net_score = safe_float(scoreboard.get("net_score_usd"))
        if starting_nav is not None and net_score is not None:
            net_flows = (safe_float(baseline_flows.get("deposits_usd")) or 0.0) - (safe_float(baseline_flows.get("withdrawals_usd")) or 0.0)
            nav = starting_nav + net_flows + net_score
            source = "configured_start_plus_score_and_external_flows"
    net_external_flows = (safe_float(baseline_flows.get("deposits_usd")) or 0.0) - (safe_float(baseline_flows.get("withdrawals_usd")) or 0.0)
    return {
        "status": "ok" if nav is not None else "unavailable",
        "source_path": str(path),
        "source": source if nav is not None else "missing_cash_or_starting_nav",
        "nav_usd": None if nav is None else round(nav, 8),
        "flow_adjusted_nav_usd": None if nav is None else round(nav - net_external_flows, 8),
        "cash_usd": cash,
        "marked_positions_usd": positions,
        "accrued_rewards_usd": pending_rewards,
        "deposits_usd": baseline_flows.get("deposits_usd"),
        "withdrawals_usd": baseline_flows.get("withdrawals_usd"),
        "explained_adjustment_usd": safe_float(scoreboard.get("reconciliation_explained_adjustment_usd")) or 0.0,
        "scoreboard_generated_at_utc": scoreboard.get("generated_at_utc"),
        "wallet_role": wallet_role,
    }


def _rpc(settings: dict[str, Any], method: str, params: list[Any], *, request_id: int) -> Any:
    try:
        response = requests.post(
            str(settings["polygon_rpc_url"]),
            json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
            timeout=float(settings["request_timeout_seconds"]),
        )
        response.raise_for_status()
        payload = response.json()
    finally:
        _pause(settings)
    if not isinstance(payload, dict) or payload.get("error"):
        raise RuntimeError(f"Polygon RPC {method} error: {payload.get('error') if isinstance(payload, dict) else payload}")
    return payload.get("result")


def _address_word(address: str) -> str:
    if not WALLET_RE.fullmatch(address):
        raise ValueError(f"invalid Polygon address: {address}")
    return address[2:].lower().rjust(64, "0")


def _balance_call(settings: dict[str, Any], contract: str, data: str, *, request_id: int) -> int:
    result = _rpc(settings, "eth_call", [{"to": contract, "data": "0x" + data}, "latest"], request_id=request_id)
    if not isinstance(result, str) or not result.startswith("0x"):
        raise ValueError("eth_call returned a non-hex balance")
    return int(result, 16)


def _onchain_snapshot(
    settings: dict[str, Any],
    wallet: str,
    registry: dict[str, Any],
    positions: list[dict[str, Any]],
) -> dict[str, Any]:
    addresses = registry.get("addresses") if isinstance(registry, dict) else {}
    if not isinstance(addresses, dict) or set(addresses) < {"pusd", "ctf"}:
        return {"status": "unavailable", "nav_usd": None, "error": "official contract registry unavailable"}
    try:
        chain_hex = _rpc(settings, "eth_chainId", [], request_id=1)
        if str(chain_hex).lower() != "0x89":
            raise RuntimeError(f"RPC is not Polygon mainnet: {chain_hex}")
        wallet_word = _address_word(wallet)
        collateral_raw = _balance_call(
            settings,
            str(addresses["pusd"]),
            ERC20_BALANCE_OF_SELECTOR + wallet_word,
            request_id=2,
        )
        collateral = collateral_raw / (10 ** int(settings["collateral_decimals"]))
        position_rows: list[dict[str, Any]] = []
        position_value = 0.0
        errors: list[str] = []
        max_positions = max(0, int(settings["max_onchain_positions"]))
        truncated = len(positions) > max_positions
        for index, row in enumerate(positions[:max_positions]):
            asset = str(row.get("asset") or "").strip()
            try:
                token_id = int(asset)
                if token_id < 0 or token_id >= 2**256:
                    raise ValueError("token ID outside uint256")
                raw_balance = _balance_call(
                    settings,
                    str(addresses["ctf"]),
                    ERC1155_BALANCE_OF_SELECTOR + wallet_word + f"{token_id:064x}",
                    request_id=10 + index,
                )
                quantity = raw_balance / (10 ** int(settings["position_decimals"]))
                api_size = safe_float(row.get("size")) or 0.0
                mark = safe_float(row.get("_mark"))
                if mark is None:
                    raise ValueError("position mark unavailable")
                value = quantity * mark
                position_value += value
                position_rows.append(
                    {
                        "asset": asset,
                        "condition_id": row.get("conditionId"),
                        "api_size": api_size,
                        "onchain_size": round(quantity, 8),
                        "quantity_delta": round(quantity - api_size, 8),
                        "mark": mark,
                        "marked_value_usd": round(value, 8),
                    }
                )
            except Exception as exc:
                errors.append(f"{asset or 'unknown'}: {type(exc).__name__}: {exc}")
        status = "ok" if not errors and not truncated else "partial"
        return {
            "status": status,
            "nav_usd": round(collateral + position_value, 8),
            "pusd_balance_usd": round(collateral, 8),
            "positions_value_usd": round(position_value, 8),
            "positions": position_rows,
            "positions_scanned": len(position_rows),
            "positions_truncated": truncated,
            "errors": errors,
            "rpc_chain_id": 137,
            "mark_source": "data-api position curPrice/currentValue used consistently across external NAV views",
            "contracts_source_url": registry.get("source_url"),
            "contracts_page_sha256": registry.get("source_sha256"),
        }
    except Exception as exc:
        return {
            "status": "unavailable",
            "nav_usd": None,
            "error": f"{type(exc).__name__}: {exc}",
            "rpc_chain_id": None,
        }


def _hex_int(value: Any) -> int | None:
    try:
        return int(str(value), 16) if str(value).startswith("0x") else int(value)
    except (TypeError, ValueError):
        return None


def _archive_overflow(cfg: EngineConfig, prefix: str, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    if not rows:
        return
    archive = cfg.output_root / "polymarket_training_archive" / f"{prefix}_{now_utc().replace(':', '')}.csv.gz"
    archive.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(archive, "wt", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _capped_write(
    cfg: EngineConfig,
    path: Path,
    rows: list[dict[str, Any]],
    *,
    fieldnames: list[str],
    max_rows: int,
    archive_prefix: str,
) -> None:
    if max_rows > 0 and len(rows) > max_rows:
        overflow = rows[:-max_rows]
        rows = rows[-max_rows:]
        _archive_overflow(cfg, archive_prefix, overflow, fieldnames)
    write_csv(path, rows, fieldnames=fieldnames)


def _capture_gas(
    cfg: EngineConfig,
    settings: dict[str, Any],
    wallet: str,
    activities: list[dict[str, Any]],
    rpc_chain_id: Any,
) -> dict[str, Any]:
    path = cfg.output_root / "performance" / "wallet_gas_observations.csv"
    existing = read_csv_rows(path)
    seen = {str(row.get("transaction_hash") or "").lower() for row in existing}
    if not _enabled(settings.get("gas_capture_enabled", True)) or rpc_chain_id != 137:
        return {"status": "disabled_or_rpc_unavailable", "new_rows": 0, "ledger_path": str(path), "errors": []}
    candidates: dict[str, dict[str, Any]] = {}
    for row in sorted(activities, key=lambda item: safe_float(item.get("timestamp")) or 0.0, reverse=True):
        tx_hash = str(row.get("transactionHash") or "").strip().lower()
        if TX_RE.fullmatch(tx_hash) and tx_hash not in seen:
            candidates.setdefault(tx_hash, row)
    errors: list[str] = []
    new_rows: list[dict[str, Any]] = []
    configured_payers = settings.get("gas_payer_addresses") or []
    if not isinstance(configured_payers, list):
        configured_payers = [configured_payers]
    payer_addresses = {wallet.lower(), *(str(value).strip().lower() for value in configured_payers)}
    for index, (tx_hash, activity) in enumerate(list(candidates.items())[: max(0, int(settings["gas_scan_max_transactions"]))]):
        try:
            receipt = _rpc(settings, "eth_getTransactionReceipt", [tx_hash], request_id=2000 + index * 2)
            if not isinstance(receipt, dict):
                raise ValueError("transaction receipt unavailable")
            gas_used = _hex_int(receipt.get("gasUsed"))
            gas_price = _hex_int(receipt.get("effectiveGasPrice"))
            transaction = _rpc(settings, "eth_getTransactionByHash", [tx_hash], request_id=2001 + index * 2)
            payer = str(transaction.get("from") or "").strip().lower() if isinstance(transaction, dict) else ""
            if gas_price is None:
                gas_price = _hex_int(transaction.get("gasPrice")) if isinstance(transaction, dict) else None
            if gas_used is None or gas_price is None:
                raise ValueError("receipt lacks gasUsed/effectiveGasPrice")
            cost_wei = gas_used * gas_price
            new_rows.append(
                {
                    "transaction_hash": tx_hash,
                    "activity_timestamp": activity.get("timestamp"),
                    "payer_address": payer,
                    "attributed_to_wallet": payer in payer_addresses,
                    "gas_used": gas_used,
                    "effective_gas_price_wei": gas_price,
                    "gas_cost_wei": cost_wei,
                    "gas_cost_pol": round(cost_wei / 10**18, 18),
                    "captured_at_utc": now_utc(),
                    "rpc_chain_id": rpc_chain_id,
                }
            )
        except Exception as exc:
            errors.append(f"{tx_hash}: {type(exc).__name__}: {exc}")
    combined = [*existing, *new_rows]
    _capped_write(
        cfg,
        path,
        combined,
        fieldnames=GAS_FIELDS,
        max_rows=int(settings["max_gas_rows"]),
        archive_prefix="wallet_gas_observations",
    )
    return {
        "status": "ok" if not errors else ("partial" if new_rows or existing else "failed"),
        "new_rows": len(new_rows),
        "ledger_rows": len(combined),
        "ledger_path": str(path),
        "errors": errors,
    }


def _pairwise(navs: dict[str, float]) -> dict[str, float]:
    names = list(navs)
    return {f"{left}_vs_{right}": round(abs(navs[left] - navs[right]), 8) for index, left in enumerate(names) for right in names[index + 1 :]}


def _reconciliation_state(
    internal: dict[str, Any],
    api: dict[str, Any],
    onchain: dict[str, Any],
    *,
    threshold: float,
) -> dict[str, Any]:
    values = {
        "internal": safe_float(internal.get("nav_usd")),
        "data_api": safe_float(api.get("nav_usd")),
        "onchain": safe_float(onchain.get("nav_usd")),
    }
    available = {key: value for key, value in values.items() if value is not None}
    deltas = _pairwise(available) if len(available) >= 2 else {}
    max_delta = max(deltas.values()) if deltas else None
    if len(available) < 3 or api.get("status") != "ok" or onchain.get("status") != "ok":
        return {
            "reconciliation_status": "partial",
            "pairwise_deltas_usd": deltas,
            "max_pairwise_delta_usd": max_delta,
            "unexplained_discrepancy_usd": None,
            "explanation": "three complete NAV views are required",
        }
    if max_delta is not None and max_delta <= threshold:
        return {
            "reconciliation_status": "clean",
            "pairwise_deltas_usd": deltas,
            "max_pairwise_delta_usd": max_delta,
            "unexplained_discrepancy_usd": 0.0,
            "explanation": "all three NAV views agree within tolerance",
        }
    adjustment = safe_float(internal.get("explained_adjustment_usd")) or 0.0
    adjusted = dict(available)
    adjusted["internal"] = adjusted["internal"] + adjustment
    adjusted_deltas = _pairwise(adjusted)
    adjusted_max = max(adjusted_deltas.values()) if adjusted_deltas else None
    if adjustment and adjusted_max is not None and adjusted_max <= threshold:
        return {
            "reconciliation_status": "explained",
            "pairwise_deltas_usd": deltas,
            "adjusted_pairwise_deltas_usd": adjusted_deltas,
            "max_pairwise_delta_usd": max_delta,
            "unexplained_discrepancy_usd": 0.0,
            "explained_adjustment_usd": adjustment,
            "explanation": "the scoreboard's explicit reconciliation adjustment explains the delta",
        }
    return {
        "reconciliation_status": "DISCREPANCY",
        "pairwise_deltas_usd": deltas,
        "max_pairwise_delta_usd": max_delta,
        "unexplained_discrepancy_usd": max_delta,
        "explanation": "unexplained three-way NAV difference exceeds tolerance",
    }


def _patch_quote_sheet(cfg: EngineConfig, payload: dict[str, Any]) -> None:
    path = cfg.output_root / "maker_carry" / "maker_quote_sheet.md"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    start = "<!-- wallet-reconciliation:start -->"
    end = "<!-- wallet-reconciliation:end -->"
    status = str(payload.get("reconciliation_status") or payload.get("status") or "unavailable")
    discrepancy = safe_float(payload.get("unexplained_discrepancy_usd"))
    if status == "DISCREPANCY" and discrepancy is not None and discrepancy > 1.0:
        headline = f"> 🔴 **WALLET RECONCILIATION DISCREPANCY — ${discrepancy:.2f} unexplained. Human review required.**"
    else:
        headline = f"> Wallet reconciliation: **{status}** (reporting only)."
    block = "\n".join([start, headline, end])
    pattern = re.compile(r"<!-- wallet-reconciliation:start -->.*?<!-- wallet-reconciliation:end -->", re.S)
    if start in text and end in text:
        text = pattern.sub(block, text)
    else:
        parts = text.split("\n", 2)
        text = "\n".join(parts[:2]) + "\n\n" + block + "\n\n" + (parts[2] if len(parts) > 2 else "")
    path.write_text(text, encoding="utf-8")


def _append_history_row(path: Path, payload: dict[str, Any], fieldnames: list[str]) -> None:
    rows = read_csv_rows(path)
    row = {field: payload.get(field) for field in fieldnames}
    key = tuple(str(row.get(field) or "") for field in fieldnames)
    existing = {tuple(str(item.get(field) or "") for field in fieldnames) for item in rows}
    if key not in existing:
        append_csv_rows(path, [row], fieldnames=fieldnames)


def _append_wallet_history(cfg: EngineConfig, payload: dict[str, Any]) -> None:
    _append_history_row(
        cfg.output_root / "performance" / "wallet_reconciliation_wallet_history.csv",
        payload,
        WALLET_HISTORY_FIELDS,
    )


def _append_legacy_history(cfg: EngineConfig, payload: dict[str, Any]) -> None:
    _append_history_row(
        cfg.output_root / "performance" / "wallet_reconciliation_history.csv",
        payload,
        LEGACY_HISTORY_FIELDS,
    )


def _reconcile_one_wallet(
    cfg: EngineConfig,
    settings: dict[str, Any],
    *,
    wallet_role: str,
    wallet: str,
    registry: dict[str, Any],
    stamp: str,
) -> dict[str, Any]:
    try:
        api = _data_api_snapshot(settings, wallet)
    except Exception as exc:
        api = {
            "status": "unavailable",
            "nav_usd": None,
            "positions": [],
            "activities": [],
            "errors": [f"{type(exc).__name__}: {exc}"],
        }
    internal = _internal_snapshot(cfg, settings, api, wallet_role=wallet_role)
    api_positions = api.get("positions") or []
    api_activities = api.get("activities") or []
    onchain = _onchain_snapshot(settings, wallet, registry, api_positions)
    state = _reconciliation_state(
        internal,
        api,
        onchain,
        threshold=float(settings["discrepancy_threshold_usd"]),
    )
    gas = _capture_gas(cfg, settings, wallet, api_activities, onchain.get("rpc_chain_id"))
    public_api = {key: value for key, value in api.items() if key not in {"positions", "activities"}}
    public_api.update(
        {
            "position_rows": len(api_positions),
            "activity_rows": len(api_activities),
            "raw_rows_persisted": False,
        }
    )
    payload = {
        "status": "ok" if state["reconciliation_status"] in {"clean", "explained"} else state["reconciliation_status"],
        **state,
        "generated_at_utc": stamp,
        "snapshot_date": stamp[:10],
        "wallet_role": wallet_role,
        "wallet_address": wallet,
        "discrepancy_threshold_usd": float(settings["discrepancy_threshold_usd"]),
        "internal_nav_usd": internal.get("nav_usd"),
        "data_api_nav_usd": api.get("nav_usd"),
        "onchain_nav_usd": onchain.get("nav_usd"),
        "internal": internal,
        "data_api": public_api,
        "onchain": onchain,
        "contract_registry": registry,
        "contracts_page_sha256": registry.get("source_sha256"),
        "rpc_chain_id": onchain.get("rpc_chain_id"),
        "gas_capture_hook": gas,
        "governance_note": "Reporting only. A discrepancy is a human-review alert and does not alter a gate or place an order.",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    _append_wallet_history(cfg, payload)
    return payload


def _unconfigured_wallet(role: str) -> dict[str, Any]:
    return {
        "status": "not_configured",
        "reconciliation_status": "not_configured",
        "wallet_role": role,
        "wallet_address": "",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }


def reconcile_wallet(cfg: EngineConfig) -> dict[str, Any]:
    settings = _settings(cfg)
    output_path = cfg.output_root / "performance" / "wallet_reconciliation.json"
    stamp = now_utc()
    wallets = _wallets(cfg)
    base: dict[str, Any] = {
        "status": "disabled",
        "reconciliation_status": "disabled",
        "generated_at_utc": stamp,
        "snapshot_date": stamp[:10],
        "operator_wallet_address": wallets["operator"],
        "executor_wallet_address": wallets["executor"],
        "wallets_combined": False,
        "nav_aggregation_policy": "operator and executor NAVs are reported separately and never summed",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    if not _enabled(settings.get("enabled", True)):
        base["wallets"] = {role: _unconfigured_wallet(role) for role in wallets}
        write_json(output_path, base)
        return base
    configured = {role: wallet for role, wallet in wallets.items() if wallet}
    if not configured:
        base.update(
            {
                "status": "awaiting_wallet_address",
                "reconciliation_status": "partial",
                "wallet_address": "",
                "wallet_role": "operator",
                "wallets": {role: _unconfigured_wallet(role) for role in wallets},
                "note": (
                    "Set maker_live_test.wallet_address and/or the public executor_wallet_address "
                    "to activate separate read-only reconciliations."
                ),
            }
        )
        write_json(output_path, base)
        return base

    valid = {role: wallet for role, wallet in configured.items() if WALLET_RE.fullmatch(wallet)}
    registry = _contract_registry(cfg, settings) if valid else {}
    results: dict[str, dict[str, Any]] = {}
    for role, wallet in wallets.items():
        if not wallet:
            results[role] = _unconfigured_wallet(role)
        elif not WALLET_RE.fullmatch(wallet):
            results[role] = {
                "status": "invalid_wallet_address",
                "reconciliation_status": "partial",
                "generated_at_utc": stamp,
                "snapshot_date": stamp[:10],
                "wallet_role": role,
                "wallet_address": wallet,
                "note": f"maker_live_test.{('wallet_address' if role == 'operator' else 'executor_wallet_address')} is not a 20-byte 0x Polygon address.",
                "paper_trading_invoked": False,
                "live_trading_invoked": False,
            }
        else:
            results[role] = _reconcile_one_wallet(
                cfg,
                settings,
                wallet_role=role,
                wallet=wallet,
                registry=registry,
                stamp=stamp,
            )

    primary_role = "operator" if wallets["operator"] else "executor"
    primary = dict(results[primary_role])
    active_results = [results[role] for role in configured]
    statuses = [str(row.get("reconciliation_status") or "partial") for row in active_results]
    if "DISCREPANCY" in statuses:
        overall = "DISCREPANCY"
    elif "partial" in statuses:
        overall = "partial"
    elif "explained" in statuses:
        overall = "explained"
    else:
        overall = "clean"
    discrepancies = [
        value
        for value in (safe_float(row.get("unexplained_discrepancy_usd")) for row in active_results)
        if value is not None
    ]
    primary.update(base)
    primary.update(
        {
            "status": "ok" if overall in {"clean", "explained"} else overall,
            "reconciliation_status": overall,
            "wallet_address": wallets[primary_role],
            "wallet_role": primary_role,
            "primary_wallet_role": primary_role,
            "wallets": results,
            "unexplained_discrepancy_usd": max(discrepancies) if discrepancies else None,
            "work_order": "WO-62+WO-73",
        }
    )
    payload = primary
    write_json(output_path, payload)
    _append_legacy_history(cfg, results[primary_role])
    _patch_quote_sheet(cfg, payload)
    return payload


def main(config_path: str) -> dict[str, Any]:
    return reconcile_wallet(load_config(config_path))
