"""WO-88 fail-safe attribution of owner drill/maintenance fills.

The venue activity feed has no trustworthy experiment-mode label.  A fill is
therefore owner activity only when it matches an explicit human action inside
the registered time window and that action is already covered by the WO-61
byte-prefix anchor.  Every missing, malformed, unanchored, or unmatched fill
stays in the maker-test population.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Mapping, Sequence
from typing import Any

from .config import EngineConfig
from .utils import boolish, parse_timestamp, read_csv_rows, safe_float


WORK_ORDER = "WO-88"
OPERATOR_LOG = "execution/stage_operator_log.csv"
ANCHOR_CHAIN = "performance/ledger_anchor_chain.csv"
OWNER_ACTIVITY_ACTION_TYPES = ("drill_trade", "maintenance_trade")
REGISTERED_MATCH_WINDOW_SECONDS = 5 * 60
PRICE_TOLERANCE = 1e-9
SIZE_TOLERANCE = 1e-9


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _anchored_operator_actions(cfg: EngineConfig) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return only the latest manifest's verified operator-log byte prefix."""

    log_path = cfg.output_root / OPERATOR_LOG
    chain_rows = read_csv_rows(cfg.output_root / ANCHOR_CHAIN)
    state: dict[str, Any] = {
        "state": "operator_log_not_anchored",
        "operator_log_path": OPERATOR_LOG,
        "anchor_chain_path": ANCHOR_CHAIN,
        "anchor_date": None,
        "anchored_byte_length": 0,
        "anchored_actions": 0,
    }
    if not log_path.exists() or not chain_rows:
        return [], state
    latest = chain_rows[-1]
    state["anchor_date"] = str(latest.get("anchor_date") or "") or None
    try:
        manifest = json.loads(str(latest.get("ledger_manifest_json") or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        state["state"] = "anchor_manifest_invalid"
        return [], state
    if not isinstance(manifest, list):
        state["state"] = "anchor_manifest_invalid"
        return [], state
    item = next(
        (
            _mapping(row)
            for row in manifest
            if isinstance(row, Mapping) and str(row.get("path") or "") == OPERATOR_LOG
        ),
        {},
    )
    if str(item.get("status") or "") != "present" or str(item.get("mode") or "") != "append_only":
        state["state"] = "operator_log_not_present_in_latest_anchor"
        return [], state
    try:
        byte_length = int(item.get("byte_length") or 0)
    except (TypeError, ValueError):
        byte_length = 0
    expected = str(item.get("prefix_sha256") or "")
    if byte_length <= 0 or byte_length > log_path.stat().st_size or len(expected) != 64:
        state["state"] = "operator_log_anchor_bounds_invalid"
        return [], state
    prefix = log_path.read_bytes()[:byte_length]
    if hashlib.sha256(prefix).hexdigest() != expected:
        state["state"] = "operator_log_anchor_digest_mismatch"
        return [], state
    try:
        text = prefix.decode("utf-8")
        actions = [dict(row) for row in csv.DictReader(io.StringIO(text))]
    except (UnicodeDecodeError, csv.Error):
        state["state"] = "operator_log_anchored_prefix_invalid"
        return [], state
    state.update(
        {
            "state": "anchored_prefix_verified",
            "anchored_byte_length": byte_length,
            "anchored_actions": len(actions),
        }
    )
    return actions, state


def _eligible_action(row: Mapping[str, Any]) -> dict[str, Any] | None:
    action_type = str(row.get("action_type") or "").strip().lower()
    side = str(row.get("side") or "").strip().lower()
    timestamp = parse_timestamp(row.get("logged_at_utc"))
    price = safe_float(row.get("price"))
    size = safe_float(row.get("size_shares"))
    entry_id = str(row.get("entry_id") or "").strip()
    market_id = str(row.get("market_id") or "").strip().lower()
    if (
        action_type not in OWNER_ACTIVITY_ACTION_TYPES
        or str(row.get("actor") or "") != "human_operator"
        or not boolish(row.get("human_action_recorded"))
        or boolish(row.get("system_order_action_invoked"))
        or boolish(row.get("paper_trading_invoked"))
        or boolish(row.get("live_trading_invoked"))
        or side not in {"bid", "ask"}
        or timestamp is None
        or price is None
        or not 0 < price < 1
        or size is None
        or size <= 0
        or not entry_id
        or not market_id
    ):
        return None
    return {
        "entry_id": entry_id,
        "action_type": action_type,
        "logged_at_utc": timestamp.isoformat().replace("+00:00", "Z"),
        "timestamp": timestamp.timestamp(),
        "market_id": market_id,
        "venue_side": "BUY" if side == "bid" else "SELL",
        "price": price,
        "size_shares": size,
    }


def _trade_fields(row: Mapping[str, Any]) -> dict[str, Any] | None:
    timestamp = safe_float(row.get("timestamp"))
    if timestamp is None:
        parsed = parse_timestamp(row.get("timestamp"))
        timestamp = parsed.timestamp() if parsed is not None else None
    condition_id = str(row.get("conditionId") or row.get("condition_id") or "").strip().lower()
    side = str(row.get("side") or "").strip().upper()
    price = safe_float(row.get("price"))
    size = safe_float(row.get("size"))
    if (
        timestamp is None
        or not condition_id
        or side not in {"BUY", "SELL"}
        or price is None
        or size is None
        or size <= 0
    ):
        return None
    return {
        "timestamp": timestamp,
        "condition_id": condition_id,
        "side": side,
        "price": price,
        "size": size,
        "transaction_hash": str(row.get("transactionHash") or row.get("transaction_hash") or "").strip(),
    }


def attribute_owner_activity(
    cfg: EngineConfig,
    trades: Sequence[Mapping[str, Any]],
    *,
    wallet_role: str,
) -> dict[str, Any]:
    """Partition recent trade rows; unmatched is always the fail-safe default."""

    raw_trades = [dict(row) for row in trades]
    actions, anchor = _anchored_operator_actions(cfg)
    eligible = [parsed for row in actions if (parsed := _eligible_action(row)) is not None]
    if str(wallet_role).strip().lower() != "operator":
        eligible = []
        anchor = {**anchor, "state": "not_applicable_non_operator_wallet"}
    eligible.sort(key=lambda row: (float(row["timestamp"]), str(row["entry_id"])))
    allocated: dict[str, float] = {str(row["entry_id"]): 0.0 for row in eligible}
    owner_indexes: set[int] = set()
    matches: list[dict[str, Any]] = []
    ordered = sorted(
        enumerate(raw_trades),
        key=lambda item: (safe_float(item[1].get("timestamp")) or 0.0, item[0]),
    )
    for index, raw_trade in ordered:
        trade = _trade_fields(raw_trade)
        if trade is None:
            continue
        candidates: list[tuple[float, str, dict[str, Any]]] = []
        for action in eligible:
            delta = abs(float(trade["timestamp"]) - float(action["timestamp"]))
            action_id = str(action["entry_id"])
            if (
                delta <= REGISTERED_MATCH_WINDOW_SECONDS
                and trade["condition_id"] == action["market_id"]
                and trade["side"] == action["venue_side"]
                and abs(float(trade["price"]) - float(action["price"])) <= PRICE_TOLERANCE
                and allocated[action_id] + float(trade["size"])
                <= float(action["size_shares"]) + SIZE_TOLERANCE
            ):
                candidates.append((delta, action_id, action))
        if not candidates:
            continue
        delta, action_id, action = min(candidates, key=lambda item: (item[0], item[1]))
        allocated[action_id] += float(trade["size"])
        owner_indexes.add(index)
        matches.append(
            {
                "classification": "owner_activity",
                "activity_kind": str(action["action_type"]).removesuffix("_trade"),
                "action_entry_id": action_id,
                "action_logged_at_utc": action["logged_at_utc"],
                "trade_timestamp": trade["timestamp"],
                "condition_id": trade["condition_id"],
                "side": trade["side"],
                "price": trade["price"],
                "size": trade["size"],
                "transaction_hash": trade["transaction_hash"],
                "time_delta_seconds": round(delta, 3),
            }
        )
    owner = [row for index, row in enumerate(raw_trades) if index in owner_indexes]
    maker = [row for index, row in enumerate(raw_trades) if index not in owner_indexes]
    return {
        "work_order": WORK_ORDER,
        "policy": (
            "only an anchored human drill_trade/maintenance_trade row matching time, market, side, "
            "price, and cumulative size earns owner_activity; every other fill remains maker_test"
        ),
        "registered_match_window_seconds": REGISTERED_MATCH_WINDOW_SECONDS,
        "operator_log_anchor": anchor,
        "eligible_anchored_owner_actions": len(eligible),
        "raw_fills": len(raw_trades),
        "owner_activity_fills": len(owner),
        "maker_test_fills": len(maker),
        "owner_activity_matches": matches,
        "owner_activity_trades": owner,
        "maker_test_trades": maker,
    }
