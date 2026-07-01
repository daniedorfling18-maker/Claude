from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .cohort_validation import write_signal_cohort_pnl
from .config import EngineConfig
from .readiness import paper_trade_readiness
from .risk import risk_decision
from .shadow_cohort import _crypto_updown_proxy_settlement_price
from .storage import connect_db
from .utils import boolish, now_utc, parse_timestamp, read_csv_rows, read_json, safe_float, write_csv, write_json
from .worldcup_validation import normalised_correlation_key


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]}"


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _starting_cash(cfg: EngineConfig) -> float:
    return float(
        cfg.raw.get("paper_trading", {}).get(
            "starting_cash",
            cfg.raw.get("risk", {}).get("bankroll", 1000),
        )
    )


def _current_cash(con, cfg: EngineConfig) -> float:
    row = con.execute(
        "SELECT cash_balance_after_usdc FROM cash_ledger ORDER BY created_at DESC, rowid DESC LIMIT 1"
    ).fetchone()
    return _starting_cash(cfg) if row is None else float(row["cash_balance_after_usdc"])


def _ensure_initial_cash(con, cfg: EngineConfig) -> None:
    if con.execute("SELECT 1 FROM cash_ledger LIMIT 1").fetchone():
        return
    cash = _starting_cash(cfg)
    con.execute(
        """
        INSERT INTO cash_ledger(
            cash_entry_id, idempotency_key, created_at, entry_type,
            amount_usdc, cash_balance_after_usdc, note
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _stable_id("cash", "initial"),
            "paper_initial_cash",
            now_utc(),
            "initial_deposit",
            cash,
            cash,
            "Configured paper starting cash",
        ),
    )


def _write_cash_entry(
    con,
    *,
    key: str,
    entry_type: str,
    amount: float,
    balance_after: float,
    order_id: str = "",
    fill_id: str = "",
    note: str = "",
) -> None:
    con.execute(
        """
        INSERT OR IGNORE INTO cash_ledger(
            cash_entry_id, idempotency_key, created_at, entry_type,
            amount_usdc, cash_balance_after_usdc, order_id, fill_id, note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _stable_id("cash", key),
            key,
            now_utc(),
            entry_type,
            amount,
            balance_after,
            order_id,
            fill_id,
            note,
        ),
    )


def _paper_settings(cfg: EngineConfig) -> dict[str, Any]:
    return cfg.raw.get("paper_trading", {}) or {}


def _quote_time(quote: dict[str, Any]) -> datetime | None:
    return parse_timestamp(quote.get("timestamp"))


def _latest_websocket_quote(cfg: EngineConfig, market_id: str, token_id: str) -> dict[str, Any] | None:
    rows = read_csv_rows(cfg.output_root / "polymarket_training" / "websocket_market_features.csv")
    market_key = str(market_id or "").strip().lower()
    token_key = str(token_id or "").strip()
    if token_key:
        matches = [
            row
            for row in rows
            if token_key
            in {
                str(row.get("asset_id") or "").strip(),
                str(row.get("token_id") or "").strip(),
                str(row.get("outcome_token_id") or "").strip(),
            }
        ]
    else:
        matches = [
            row
            for row in rows
            if market_key and str(row.get("market_slug") or "").strip().lower() == market_key
        ]
    if not matches:
        return None
    matches.sort(key=lambda row: parse_timestamp(row.get("collected_at_utc")) or datetime.min.replace(tzinfo=timezone.utc))
    latest = matches[-1]
    return {
        "timestamp": latest.get("collected_at_utc") or now_utc(),
        "best_bid": safe_float(latest.get("best_bid")),
        "best_ask": safe_float(latest.get("best_ask")),
        "midpoint": safe_float(latest.get("midpoint")),
        "spread": safe_float(latest.get("spread")),
        "source": "websocket_market_features",
    }


def _latest_quote(con, cfg: EngineConfig, market_id: str, token_id: str) -> dict[str, Any]:
    websocket_quote = _latest_websocket_quote(cfg, market_id, token_id)
    row = con.execute(
        """
        SELECT collected_at, best_bid, best_ask, midpoint, spread
        FROM market_snapshots
        WHERE market_id = ? AND token_id = ?
        ORDER BY collected_at DESC, rowid DESC
        LIMIT 1
        """,
        (market_id, token_id),
    ).fetchone()
    snapshot_source = "market_snapshots"
    if row is None and token_id:
        row = con.execute(
            """
            SELECT collected_at, best_bid, best_ask, midpoint, spread
            FROM market_snapshots
            WHERE token_id = ?
            ORDER BY collected_at DESC, rowid DESC
            LIMIT 1
            """,
            (token_id,),
        ).fetchone()
        snapshot_source = "market_snapshots_token_alias"
    if row is not None:
        db_quote = {
            "timestamp": row["collected_at"],
            "best_bid": safe_float(row["best_bid"]),
            "best_ask": safe_float(row["best_ask"]),
            "midpoint": safe_float(row["midpoint"]),
            "spread": safe_float(row["spread"]),
            "source": snapshot_source,
        }
        if websocket_quote is not None and (_quote_time(websocket_quote) or datetime.min.replace(tzinfo=timezone.utc)) > (
            _quote_time(db_quote) or datetime.min.replace(tzinfo=timezone.utc)
        ):
            return websocket_quote
        return db_quote
    if websocket_quote is not None:
        return websocket_quote
    prediction = con.execute(
        """
        SELECT prediction_timestamp, market_probability, executable_price
        FROM model_predictions
        WHERE market_id = ? AND token_id = ?
        ORDER BY prediction_timestamp DESC, created_at DESC
        LIMIT 1
        """,
        (market_id, token_id),
    ).fetchone()
    if prediction is None:
        return {"timestamp": now_utc(), "best_bid": None, "best_ask": None, "midpoint": None, "spread": None, "source": "missing"}
    midpoint = safe_float(prediction["market_probability"])
    ask = safe_float(prediction["executable_price"])
    bid = None
    if midpoint is not None and ask is not None:
        bid = max(1e-6, min(0.999999, midpoint - max(0.0, ask - midpoint)))
    return {
        "timestamp": prediction["prediction_timestamp"],
        "best_bid": bid,
        "best_ask": ask,
        "midpoint": midpoint,
        "spread": None if bid is None or ask is None else max(0.0, ask - bid),
        "source": "model_predictions",
    }


def _latest_prediction_payload(con, market_id: str, token_id: str) -> dict[str, Any]:
    row = con.execute(
        """
        SELECT raw_payload_json
        FROM model_predictions
        WHERE market_id = ? AND token_id = ?
        ORDER BY prediction_timestamp DESC, created_at DESC
        LIMIT 1
        """,
        (market_id, token_id),
    ).fetchone()
    if row is None:
        return {}
    try:
        payload = json.loads(row["raw_payload_json"] or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _latest_order_signal_payload(con, market_id: str, token_id: str) -> dict[str, Any]:
    row = con.execute(
        """
        SELECT source_signal_json
        FROM orders
        WHERE market_id = ? AND token_id = ? AND side = 'BUY_YES'
        ORDER BY created_at DESC, rowid DESC
        LIMIT 1
        """,
        (market_id, token_id),
    ).fetchone()
    if row is None:
        return {}
    try:
        payload = json.loads(row["source_signal_json"] or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _position_payload_for_settlement(con, position) -> dict[str, Any]:
    market_id = str(position["market_id"])
    token_id = str(position["token_id"])
    order_payload = _latest_order_signal_payload(con, market_id, token_id)
    prediction_payload = _latest_prediction_payload(con, market_id, token_id)
    return {
        **order_payload,
        **prediction_payload,
        **dict(position),
        "market_id": market_id,
        "token_id": token_id,
    }


def _proxy_resolution_for_position(con, cfg: EngineConfig, position) -> tuple[int, str] | None:
    settings = _paper_settings(cfg)
    if not boolish(settings.get("settle_crypto_updown_with_public_price", True)):
        return None
    payload = _position_payload_for_settlement(con, position)
    timeout_seconds = int(settings.get("settlement_request_timeout_seconds", 8) or 8)
    settlement_price, reason = _crypto_updown_proxy_settlement_price(
        payload,
        timeout_seconds=timeout_seconds,
    )
    target = safe_float(settlement_price)
    if target not in {0.0, 1.0}:
        return None
    return int(target), reason


def _position_mark_price(con, cfg: EngineConfig, position) -> float:
    quote = _latest_quote(con, cfg, str(position["market_id"]), str(position["token_id"]))
    mark_mode = str(_paper_settings(cfg).get("mark_price", "best_bid")).lower()
    if mark_mode == "midpoint":
        mark = safe_float(quote.get("midpoint"))
    elif mark_mode == "average_entry":
        mark = safe_float(position["average_entry_price"])
    else:
        mark = safe_float(quote.get("best_bid"))
        if mark is None:
            mark = safe_float(quote.get("midpoint"))
    if mark is None:
        mark = safe_float(position["average_entry_price"]) or 0.0
    return max(0.0, min(1.0, mark))


def _resolution_index(cfg: EngineConfig) -> dict[tuple[str, str], tuple[int, str]]:
    index: dict[tuple[str, str], tuple[int, str]] = {}
    training = cfg.output_root / "polymarket_training"
    for name in ("market_resolutions.csv", "historical_resolutions.csv", "websocket_resolutions.csv"):
        path = training / name
        for row in read_csv_rows(path):
            if row.get("resolution_quality") != "clean_settlement":
                continue
            target = str(row.get("target", "")).strip().lower()
            if target not in {"0", "1", "true", "false"}:
                continue
            token = row.get("token_id", "")
            if not token:
                continue
            target_int = 1 if target in {"1", "true"} else 0
            for market in (
                row.get("condition_id", ""),
                row.get("market_slug", ""),
                row.get("gamma_market_id", ""),
                "",
            ):
                index[(market, token)] = (target_int, str(path))
    return index


def settle_resolved_positions(con, cfg: EngineConfig) -> list[dict[str, Any]]:
    """Settle open paper positions exactly once from clean or public proxy resolution rows."""
    resolutions = _resolution_index(cfg)
    settled: list[dict[str, Any]] = []
    positions = con.execute(
        "SELECT * FROM positions WHERE status = 'open' AND quantity > 0"
    ).fetchall()
    for position in positions:
        key = (str(position["market_id"]), str(position["token_id"]))
        resolution = resolutions.get(key) or resolutions.get(("", str(position["token_id"])))
        if resolution is None:
            resolution = _proxy_resolution_for_position(con, cfg, position)
        if resolution is None:
            continue
        target, source = resolution
        idempotency_key = f"settlement|{position['position_id']}|{target}"
        if con.execute(
            "SELECT 1 FROM settlements WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone():
            continue

        quantity = float(position["quantity"])
        cost_basis = float(position["cost_basis_usdc"])
        payout = quantity if target == 1 else 0.0
        realised = payout - cost_basis
        settlement_id = _stable_id("settlement", idempotency_key)
        con.execute(
            """
            INSERT INTO settlements(
                settlement_id, idempotency_key, created_at, market_id, token_id,
                target, quantity, cost_basis_usdc, payout_usdc,
                realised_pnl_usdc, resolution_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                settlement_id,
                idempotency_key,
                now_utc(),
                position["market_id"],
                position["token_id"],
                target,
                quantity,
                cost_basis,
                payout,
                realised,
                source,
            ),
        )
        cash_before = _current_cash(con, cfg)
        _write_cash_entry(
            con,
            key=f"cash|{idempotency_key}",
            entry_type="settlement",
            amount=payout,
            balance_after=cash_before + payout,
            note=f"Settled target={target}; realised_pnl={realised:.6f}",
        )
        con.execute(
            """
            UPDATE positions
            SET quantity = 0, average_entry_price = 0, cost_basis_usdc = 0,
                realised_pnl_usdc = realised_pnl_usdc + ?, status = 'settled',
                updated_at = ?
            WHERE position_id = ?
            """,
            (realised, now_utc(), position["position_id"]),
        )
        settled.append(
            {
                "settlement_id": settlement_id,
                "market_id": position["market_id"],
                "token_id": position["token_id"],
                "target": target,
                "payout_usdc": payout,
                "realised_pnl_usdc": realised,
                "resolution_source": source,
            }
        )
    return settled


def portfolio_state(
    con,
    cfg: EngineConfig,
    signal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _ensure_initial_cash(con, cfg)
    positions = con.execute(
        "SELECT * FROM positions WHERE status = 'open' AND quantity > 0"
    ).fetchall()
    exposure = sum(float(row["cost_basis_usdc"]) for row in positions)
    market_value = 0.0
    for position in positions:
        mark = _position_mark_price(con, cfg, position)
        market_value += float(position["quantity"]) * mark
    realised = sum(
        float(row["realised_pnl_usdc"])
        for row in con.execute("SELECT realised_pnl_usdc FROM positions").fetchall()
    )
    cash = _current_cash(con, cfg)
    open_orders = int(
        con.execute(
            "SELECT COUNT(*) AS n FROM orders WHERE status IN ('accepted','resting','partially_filled')"
        ).fetchone()["n"]
    )
    minute_ago = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    order_rate = int(
        con.execute(
            "SELECT COUNT(*) AS n FROM orders WHERE created_at >= ?",
            (minute_ago,),
        ).fetchone()["n"]
    )
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    daily_realised = float(
        con.execute(
            "SELECT COALESCE(SUM(realised_pnl_usdc), 0) AS pnl FROM settlements WHERE substr(created_at,1,10) = ?",
            (today,),
        ).fetchone()["pnl"]
    )
    unrealised = market_value - exposure
    equity = cash + market_value
    peak_row = con.execute(
        "SELECT MAX(equity_usdc) AS peak FROM portfolio_snapshots"
    ).fetchone()
    peak = max(equity, float(peak_row["peak"] or equity))
    drawdown = max(0.0, (peak - equity) / peak) if peak else 0.0

    market_id = str((signal or {}).get("market_id", ""))
    category = str((signal or {}).get("category", ""))
    correlation_key = normalised_correlation_key(signal or {})
    return {
        "bankroll": _starting_cash(cfg),
        "cash": cash,
        "equity": equity,
        "open_orders": open_orders,
        "order_rate_per_minute": order_rate,
        "position_count": len(positions),
        "total_exposure": exposure,
        "market_value": market_value,
        "unrealised_pnl": unrealised,
        "realised_pnl": realised,
        "daily_loss": max(0.0, -daily_realised),
        "drawdown": drawdown,
        "current_market_exposure": sum(
            float(row["cost_basis_usdc"]) for row in positions if row["market_id"] == market_id
        ),
        "current_category_exposure": sum(
            float(row["cost_basis_usdc"]) for row in positions if row["category"] == category
        ),
        "current_correlated_exposure": sum(
            float(row["cost_basis_usdc"])
            for row in positions
            if normalised_correlation_key(dict(row)) == correlation_key
        ),
    }


def _upsert_position(
    con,
    *,
    market_id: str,
    token_id: str,
    side: str,
    category: str,
    correlation_key: str,
    quantity: float,
    fill_price: float,
    gross_notional: float,
) -> None:
    row = con.execute(
        "SELECT * FROM positions WHERE market_id = ? AND token_id = ? AND side = ?",
        (market_id, token_id, side),
    ).fetchone()
    if row is None:
        con.execute(
            """
            INSERT INTO positions(
                position_id, market_id, token_id, side, category, correlation_key,
                quantity, average_entry_price, cost_basis_usdc, status, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
            """,
            (
                _stable_id("position", f"{market_id}|{token_id}|{side}"),
                market_id,
                token_id,
                side,
                category,
                correlation_key,
                quantity,
                fill_price,
                gross_notional,
                now_utc(),
            ),
        )
        return
    old_quantity = float(row["quantity"])
    old_cost = float(row["cost_basis_usdc"])
    new_quantity = old_quantity + quantity
    new_cost = old_cost + gross_notional
    con.execute(
        """
        UPDATE positions
        SET category = ?, correlation_key = ?, quantity = ?,
            average_entry_price = ?, cost_basis_usdc = ?, status = 'open',
            updated_at = ?
        WHERE position_id = ?
        """,
        (
            category,
            correlation_key,
            new_quantity,
            new_cost / new_quantity if new_quantity else 0.0,
            new_cost,
            now_utc(),
            row["position_id"],
        ),
    )


def _timestamp_age_minutes(value: Any) -> float:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        updated = datetime.fromisoformat(text)
    except ValueError:
        return float("inf")
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - updated.astimezone(timezone.utc)).total_seconds() / 60.0)


def _position_age_minutes(position) -> float:
    return _timestamp_age_minutes(position["updated_at"])


def _exit_reason(position, quote: dict[str, Any], prediction: dict[str, Any], settings: dict[str, Any]) -> str | None:
    bid = safe_float(quote.get("best_bid"))
    if bid is None or bid <= 0:
        return None
    quantity = float(position["quantity"])
    cost_basis = float(position["cost_basis_usdc"])
    if quantity <= 0 or cost_basis <= 0:
        return None
    price_action_signal = boolish(prediction.get("price_action_signal"))
    minimum_hold = (
        safe_float(prediction.get("minimum_hold_minutes_before_exit"))
        if price_action_signal
        else safe_float(settings.get("minimum_hold_minutes_before_exit"))
    )
    if minimum_hold is None:
        minimum_hold = 0.0 if price_action_signal else 15.0
    if _position_age_minutes(position) < float(minimum_hold):
        return None
    exit_value = quantity * bid
    pnl = exit_value - cost_basis
    return_pct = pnl / cost_basis
    hard_stop_loss_return = safe_float(settings.get("hard_stop_loss_return"))
    hard_stop_loss_usdc = safe_float(settings.get("hard_stop_loss_usdc"))
    if hard_stop_loss_return is not None and return_pct <= -abs(hard_stop_loss_return):
        return "hard_stop_loss"
    if hard_stop_loss_usdc is not None and pnl <= -abs(hard_stop_loss_usdc):
        return "hard_stop_loss"
    alpha_edge = safe_float(prediction.get("edge_lower_bound"))
    if alpha_edge is None:
        alpha_edge = safe_float(prediction.get("edge"))
    take_profit_return = float(
        safe_float(prediction.get("take_profit_return"))
        if price_action_signal and safe_float(prediction.get("take_profit_return")) is not None
        else settings.get("take_profit_return", 0.25)
    )
    take_profit_min_usdc = float(
        safe_float(prediction.get("take_profit_min_usdc"))
        if price_action_signal and safe_float(prediction.get("take_profit_min_usdc")) is not None
        else settings.get("take_profit_min_usdc", 0.25)
    )
    if return_pct >= take_profit_return and pnl >= take_profit_min_usdc:
        return "take_profit"
    if (not price_action_signal) and bool(settings.get("exit_when_alpha_edge_below_threshold", True)):
        threshold = float(settings.get("alpha_exit_edge_threshold", -0.01))
        max_loss = float(settings.get("max_loss_usdc_for_alpha_exit", 1.0))
        if alpha_edge is not None and alpha_edge <= threshold and pnl >= -max_loss:
            return "alpha_edge_deteriorated"
    stop_loss_return = float(
        safe_float(prediction.get("stop_loss_return"))
        if price_action_signal and safe_float(prediction.get("stop_loss_return")) is not None
        else settings.get("stop_loss_return", 0.65)
    )
    stop_loss_max_edge = float(settings.get("stop_loss_max_edge", 0.0))
    if return_pct <= -stop_loss_return and (price_action_signal or alpha_edge is None or alpha_edge <= stop_loss_max_edge):
        return "stop_loss"
    max_hold = safe_float(prediction.get("max_hold_minutes_before_exit")) if price_action_signal else None
    if max_hold is not None and max_hold > 0 and _position_age_minutes(position) >= float(max_hold):
        return "fixed_horizon"
    return None


def close_paper_position(con, cfg: EngineConfig, position, reason: str, quote: dict[str, Any]) -> dict[str, Any]:
    bid = safe_float(quote.get("best_bid"))
    if bid is None or bid <= 0:
        return {"status": "exit_skipped", "reason": "missing executable bid", "position_id": position["position_id"]}
    settings = _paper_settings(cfg)
    configured_slippage = float(cfg.raw.get("costs", {}).get("slippage", 0.0))
    spread = safe_float(quote.get("spread"))
    exit_slippage = min(configured_slippage, max(0.0, spread / 2.0)) if spread is not None else 0.0
    fill_price = max(1e-6, min(0.999999, bid - exit_slippage))
    quantity = float(position["quantity"])
    cost_basis = float(position["cost_basis_usdc"])
    if quantity <= 0 or cost_basis <= 0:
        return {"status": "exit_skipped", "reason": "position is empty", "position_id": position["position_id"]}
    prediction = _latest_prediction_payload(con, str(position["market_id"]), str(position["token_id"]))
    quote_timestamp = str(quote.get("timestamp") or now_utc())
    key = "|".join(
        [
            str(position["position_id"]),
            "SELL_YES",
            quote_timestamp,
            reason,
            str(settings.get("exit_policy_version", "paper-exit-v1")),
        ]
    )
    idempotency_key = f"paper_exit|{key}"
    existing = con.execute("SELECT * FROM orders WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
    if existing:
        return {
            "status": "duplicate_exit_skipped",
            "order_id": existing["order_id"],
            "market_id": existing["market_id"],
            "token_id": existing["token_id"],
        }

    fee_rate = float(cfg.raw.get("costs", {}).get("transaction_cost", 0.0))
    gross_notional = quantity * fill_price
    fee = gross_notional * fee_rate
    net_credit = gross_notional - fee
    realised = net_credit - cost_basis
    cash = _current_cash(con, cfg)
    created_at = now_utc()
    order_id = _stable_id("order", idempotency_key)
    fill_key = f"paper_fill|{order_id}|exit_taker"
    fill_id = _stable_id("fill", fill_key)
    market_id = str(position["market_id"])
    token_id = str(position["token_id"])
    source_signal = {
        "side": "SELL_YES",
        "reason": reason,
        "quote": quote,
        "latest_prediction": prediction,
        "position_id": position["position_id"],
    }
    risk_decision = {
        "approved": True,
        "reason": f"risk-reducing exit: {reason}",
        "stake_usdc": 0.0,
        "quantity": quantity,
        "limit_price": fill_price,
    }
    con.execute(
        """
        INSERT INTO orders(
            order_id, idempotency_key, created_at, updated_at, mode, status,
            market_id, token_id, side, limit_price, stake_usdc, quantity,
            category, event_id, correlation_key, strategy_name, model_version,
            prediction_timestamp, risk_decision_json, source_signal_json
        ) VALUES (?, ?, ?, ?, 'paper', 'filled', ?, ?, 'SELL_YES', ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?)
        """,
        (
            order_id,
            idempotency_key,
            created_at,
            created_at,
            market_id,
            token_id,
            fill_price,
            gross_notional,
            quantity,
            position["category"],
            position["correlation_key"],
            f"paper_exit_{reason}",
            prediction.get("model_version", ""),
            quote_timestamp,
            _json(risk_decision),
            _json(source_signal),
        ),
    )
    con.execute(
        """
        INSERT INTO fills(
            fill_id, order_id, idempotency_key, created_at, market_id, token_id,
            side, fill_price, quantity, gross_notional_usdc, fee_usdc,
            slippage_usdc, fill_model
        ) VALUES (?, ?, ?, ?, ?, ?, 'SELL_YES', ?, ?, ?, ?, ?, 'exit_taker')
        """,
        (
            fill_id,
            order_id,
            fill_key,
            created_at,
            market_id,
            token_id,
            fill_price,
            quantity,
            gross_notional,
            fee,
            quantity * exit_slippage,
        ),
    )
    con.execute(
        """
        UPDATE positions
        SET quantity = 0, average_entry_price = 0, cost_basis_usdc = 0,
            realised_pnl_usdc = realised_pnl_usdc + ?, status = 'closed',
            updated_at = ?
        WHERE position_id = ?
        """,
        (realised, created_at, position["position_id"]),
    )
    _write_cash_entry(
        con,
        key=f"cash|{fill_key}",
        entry_type="paper_exit",
        amount=net_credit,
        balance_after=cash + net_credit,
        order_id=order_id,
        fill_id=fill_id,
        note=f"Closed paper position at {fill_price:.6f}; reason={reason}; realised_pnl={realised:.6f}",
    )
    return {
        "status": "closed",
        "reason": reason,
        "order_id": order_id,
        "fill_id": fill_id,
        "market_id": market_id,
        "token_id": token_id,
        "fill_price": fill_price,
        "quantity": quantity,
        "gross_notional_usdc": gross_notional,
        "net_credit_usdc": net_credit,
        "cost_basis_usdc": cost_basis,
        "realised_pnl_usdc": realised,
    }


def close_eligible_positions(con, cfg: EngineConfig) -> list[dict[str, Any]]:
    settings = _paper_settings(cfg)
    if not settings.get("exit_enabled", True):
        return []
    results: list[dict[str, Any]] = []
    positions = con.execute(
        "SELECT * FROM positions WHERE status = 'open' AND quantity > 0 ORDER BY updated_at, position_id"
    ).fetchall()
    for position in positions:
        quote = _latest_quote(con, cfg, str(position["market_id"]), str(position["token_id"]))
        prediction = _latest_prediction_payload(con, str(position["market_id"]), str(position["token_id"]))
        entry_signal = _latest_order_signal_payload(con, str(position["market_id"]), str(position["token_id"]))
        if entry_signal:
            prediction = {**prediction, **entry_signal}
        reason = _exit_reason(position, quote, prediction, settings)
        if reason is None:
            continue
        results.append(close_paper_position(con, cfg, position, reason, quote))
    return results


def _entry_pause_reason(con, cfg: EngineConfig) -> str:
    settings = cfg.raw.get("profit_tracking", {}) or {}
    threshold = safe_float(settings.get("pause_new_entries_below_pnl_usdc"))
    if threshold is None:
        return ""
    baseline_path = cfg.governance_root / str(settings.get("baseline_file", "paper_profit_target_baseline.json"))
    baseline = read_json(baseline_path, default={}) or {}
    if not isinstance(baseline, dict) or not baseline:
        return ""
    baseline_equity = safe_float(baseline.get("baseline_equity_usdc"))
    if baseline_equity is None:
        return ""
    state = portfolio_state(con, cfg)
    pnl = float(state["equity"]) - baseline_equity
    if pnl <= threshold:
        return f"clean forward P&L {pnl:.2f} <= pause threshold {threshold:.2f}; monitoring exits only"
    return ""


def _paper_signal_rows(cfg: EngineConfig) -> list[dict[str, Any]]:
    settings = _paper_settings(cfg)
    configured = settings.get("signal_files")
    base = cfg.output_root.parent
    paths: list[Path] = []
    if isinstance(configured, list):
        for item in configured:
            text = str(item or "").strip()
            if not text:
                continue
            path = Path(text)
            paths.append(path if path.is_absolute() else base / path)
    elif configured:
        path = Path(str(configured))
        paths.append(path if path.is_absolute() else base / path)
    else:
        paths = [
            cfg.output_root / "polymarket_predictions" / "trade_signals.csv",
            cfg.output_root / "polymarket_price_action" / "price_action_paper_signals.csv",
        ]

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for path in paths:
        for row in read_csv_rows(path):
            key = (
                str(row.get("market_id", "")),
                str(row.get("token_id", "")),
                str(row.get("side", "BUY_YES")),
                str(row.get("data_snapshot_timestamp") or row.get("prediction_timestamp") or ""),
                str(row.get("strategy_name", "")),
            )
            if key in seen:
                continue
            seen.add(key)
            rows.append({**row, "source_signal_file": str(path)})
    return rows


def submit_paper_signal(con, cfg: EngineConfig, signal: dict[str, Any]) -> dict[str, Any]:
    """Idempotently risk-check and immediately paper-fill a BUY_YES signal."""
    prediction_timestamp = str(
        signal.get("data_snapshot_timestamp")
        or signal.get("prediction_timestamp")
        or ""
    )
    key = "|".join(
        [
            str(signal.get("market_id", "")),
            str(signal.get("token_id", "")),
            str(signal.get("side", "BUY_YES")),
            prediction_timestamp,
            str(signal.get("model_version", "")),
        ]
    )
    idempotency_key = f"paper_order|{key}"
    existing = con.execute(
        "SELECT * FROM orders WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()
    if existing:
        return {
            "status": "duplicate_skipped",
            "order_id": existing["order_id"],
            "market_id": existing["market_id"],
            "token_id": existing["token_id"],
        }

    if str(signal.get("side", "BUY_YES")) != "BUY_YES":
        return {"status": "rejected", "reason": "paper broker currently supports BUY_YES only"}

    market_id = str(signal.get("market_id", ""))
    token_id = str(signal.get("token_id", ""))
    paper_settings = _paper_settings(cfg)
    if not bool(paper_settings.get("allow_position_pyramiding", False)):
        existing_position = con.execute(
            """
            SELECT quantity, cost_basis_usdc
            FROM positions
            WHERE market_id = ? AND token_id = ? AND side = 'BUY_YES'
              AND status = 'open' AND quantity > 0
            """,
            (market_id, token_id),
        ).fetchone()
        if existing_position is not None:
            return {
                "status": "rejected",
                "reason": "open position already exists; pyramiding disabled",
                "market_id": market_id,
                "token_id": token_id,
                "current_quantity": existing_position["quantity"],
                "current_cost_basis_usdc": existing_position["cost_basis_usdc"],
            }
    exit_cooldown = float(paper_settings.get("minimum_reentry_minutes_after_exit", 240.0))
    if exit_cooldown > 0:
        last_exit = con.execute(
            """
            SELECT created_at
            FROM fills
            WHERE market_id = ? AND token_id = ? AND side = 'SELL_YES'
            ORDER BY created_at DESC, rowid DESC
            LIMIT 1
            """,
            (market_id, token_id),
        ).fetchone()
        if last_exit is not None and _timestamp_age_minutes(last_exit["created_at"]) < exit_cooldown:
            return {
                "status": "rejected",
                "reason": "recent exit cooldown active",
                "market_id": market_id,
                "token_id": token_id,
            }

    costs = cfg.raw.get("costs", {})
    configured_slippage = float(costs.get("slippage", 0.0))
    signal_slippage = safe_float(signal.get("slippage"))
    slippage = max(0.0, min(configured_slippage, signal_slippage if signal_slippage is not None else configured_slippage))
    enriched = {**signal, "slippage": slippage}
    state = portfolio_state(con, cfg, enriched)
    decision = risk_decision(cfg, enriched, state)
    if not decision.get("approved"):
        return {"status": "rejected", "reason": decision.get("reason", ""), "risk": decision}

    price = float(decision["limit_price"])
    fill_price = min(0.999999, price + slippage)
    fee_rate = float(costs.get("transaction_cost", 0.0))
    cash = float(state["cash"])
    budget = min(float(decision["stake_usdc"]), cash)
    max_position_cost = safe_float(paper_settings.get("maximum_position_cost_usdc"))
    if max_position_cost is not None and max_position_cost > 0:
        existing_cost_row = con.execute(
            """
            SELECT COALESCE(SUM(cost_basis_usdc), 0) AS cost
            FROM positions
            WHERE market_id = ? AND token_id = ? AND side = 'BUY_YES'
              AND status = 'open' AND quantity > 0
            """,
            (market_id, token_id),
        ).fetchone()
        existing_cost = safe_float(existing_cost_row["cost"] if existing_cost_row else 0.0) or 0.0
        remaining_budget = max(0.0, max_position_cost - existing_cost)
        budget = min(budget, remaining_budget)
    quantity = budget / (fill_price * (1 + fee_rate)) if fill_price > 0 else 0.0
    gross_notional = quantity * fill_price
    fee = gross_notional * fee_rate
    total_debit = gross_notional + fee
    if quantity <= 0 or total_debit <= 0:
        return {"status": "rejected", "reason": "insufficient paper cash after costs"}

    order_id = _stable_id("order", idempotency_key)
    fill_key = f"paper_fill|{order_id}|taker_immediate"
    fill_id = _stable_id("fill", fill_key)
    created_at = now_utc()
    category = str(signal.get("category", ""))
    event_id = str(signal.get("event_id", ""))
    correlation_key = normalised_correlation_key(signal)

    con.execute(
        """
        INSERT INTO orders(
            order_id, idempotency_key, created_at, updated_at, mode, status,
            market_id, token_id, side, limit_price, stake_usdc, quantity,
            category, event_id, correlation_key, strategy_name, model_version,
            prediction_timestamp, risk_decision_json, source_signal_json
        ) VALUES (?, ?, ?, ?, 'paper', 'filled', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            order_id,
            idempotency_key,
            created_at,
            created_at,
            market_id,
            token_id,
            signal.get("side", "BUY_YES"),
            price,
            budget,
            quantity,
            category,
            event_id,
            correlation_key,
            signal.get("strategy_name", "predictive_directional"),
            signal.get("model_version", ""),
            prediction_timestamp,
            _json(decision),
            _json(signal),
        ),
    )
    con.execute(
        """
        INSERT INTO fills(
            fill_id, order_id, idempotency_key, created_at, market_id, token_id,
            side, fill_price, quantity, gross_notional_usdc, fee_usdc,
            slippage_usdc, fill_model
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fill_id,
            order_id,
            fill_key,
            created_at,
            market_id,
            token_id,
            signal.get("side", "BUY_YES"),
            fill_price,
            quantity,
            gross_notional,
            fee,
            quantity * slippage,
            "taker_immediate",
        ),
    )
    _upsert_position(
        con,
        market_id=market_id,
        token_id=token_id,
        side=str(signal.get("side", "BUY_YES")),
        category=category,
        correlation_key=correlation_key,
        quantity=quantity,
        fill_price=fill_price,
        gross_notional=total_debit,
    )
    _write_cash_entry(
        con,
        key=f"cash|{fill_key}",
        entry_type="paper_fill",
        amount=-total_debit,
        balance_after=cash - total_debit,
        order_id=order_id,
        fill_id=fill_id,
        note=f"Immediate taker paper fill at {fill_price:.6f}",
    )
    edge = safe_float(signal.get("edge")) or 0.0
    raw_expected_lower_bound_profit = max(0.0, quantity * edge)
    target_metric_min_price = float(cfg.raw.get("mispricing_alpha", {}).get("target_metric_min_price", 0.05))
    metric_price = max(fill_price, target_metric_min_price)
    expected_lower_bound_profit = max(0.0, total_debit * edge / metric_price) if metric_price > 0 else 0.0
    return {
        "status": "filled",
        "order_id": order_id,
        "fill_id": fill_id,
        "market_id": market_id,
        "token_id": token_id,
        "fill_price": fill_price,
        "quantity": quantity,
        "stake_usdc": total_debit,
        "effective_slippage": slippage,
        "edge": edge,
        "raw_expected_lower_bound_profit_usdc": raw_expected_lower_bound_profit,
        "expected_lower_bound_profit_usdc": expected_lower_bound_profit,
        "target_metric_price": metric_price,
        "risk": decision,
    }


def write_portfolio_snapshot(con, cfg: EngineConfig) -> dict[str, Any]:
    state = portfolio_state(con, cfg)
    timestamp = now_utc()
    snapshot_id = _stable_id("portfolio", f"{timestamp}:{time.time_ns()}")
    payload = {
        "snapshot_id": snapshot_id,
        "timestamp": timestamp,
        "cash": state["cash"],
        "equity": state["equity"],
        "open_orders": state["open_orders"],
        "position_count": state["position_count"],
        "total_exposure": state["total_exposure"],
        "realised_pnl": state["realised_pnl"],
        "unrealised_pnl": state["unrealised_pnl"],
        "daily_loss": state["daily_loss"],
        "drawdown": state["drawdown"],
    }
    con.execute(
        """
        INSERT INTO portfolio_snapshots(
            snapshot_id, created_at, cash_usdc, equity_usdc, open_order_count,
            position_count, total_exposure_usdc, realised_pnl_usdc,
            unrealised_pnl_usdc, daily_loss_usdc, drawdown, risk_usage_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["snapshot_id"],
            payload["timestamp"],
            payload["cash"],
            payload["equity"],
            payload["open_orders"],
            payload["position_count"],
            payload["total_exposure"],
            payload["realised_pnl"],
            payload["unrealised_pnl"],
            payload["daily_loss"],
            payload["drawdown"],
            _json(state),
        ),
    )
    return payload


def export_ledger(con, cfg: EngineConfig) -> None:
    out = cfg.output_root / "polymarket_portfolio"
    queries = {
        "paper_orders.csv": "SELECT * FROM orders WHERE mode = 'paper' ORDER BY created_at, order_id",
        "paper_fills.csv": "SELECT * FROM fills ORDER BY created_at, fill_id",
        "positions.csv": "SELECT * FROM positions ORDER BY market_id, token_id, side",
        "cash_ledger.csv": "SELECT * FROM cash_ledger ORDER BY created_at, rowid",
        "settlements.csv": "SELECT * FROM settlements ORDER BY created_at, settlement_id",
        "portfolio_snapshots.csv": "SELECT * FROM portfolio_snapshots ORDER BY created_at, snapshot_id",
    }
    for name, query in queries.items():
        rows = [dict(row) for row in con.execute(query).fetchall()]
        write_csv(out / name, rows)


def run_paper_broker(cfg: EngineConfig) -> dict[str, Any]:
    con = connect_db(cfg.database_path)
    try:
        with con:
            _ensure_initial_cash(con, cfg)
            settlements = settle_resolved_positions(con, cfg)
            gate = paper_trade_readiness(cfg)
            settings = _paper_settings(cfg)
            exit_monitoring_when_blocked = boolish(settings.get("monitor_exits_when_readiness_blocked", True)) and boolish(
                settings.get("exit_enabled", True)
            )
            results: list[dict[str, Any]] = []
            exit_results: list[dict[str, Any]] = []
            entry_pause_reason = ""
            if gate.get("approved_for_paper_trading"):
                exit_results = close_eligible_positions(con, cfg)
                entry_pause_reason = _entry_pause_reason(con, cfg)
                if not entry_pause_reason:
                    signals = _paper_signal_rows(cfg)
                    for signal in signals:
                        results.append(submit_paper_signal(con, cfg, signal))
            elif exit_monitoring_when_blocked:
                exit_results = close_eligible_positions(con, cfg)
            snapshot = write_portfolio_snapshot(con, cfg)
            export_ledger(con, cfg)
            cohort_pnl = write_signal_cohort_pnl(con, cfg)

        filled = sum(result.get("status") == "filled" for result in results)
        duplicate = sum(result.get("status") == "duplicate_skipped" for result in results)
        rejected = sum(result.get("status") == "rejected" for result in results)
        filled_orders = [result for result in results if result.get("status") == "filled"]
        closed_positions = [result for result in exit_results if result.get("status") == "closed"]
        exit_rejections = Counter(
            str(result.get("reason", ""))
            for result in exit_results
            if result.get("status") not in {"closed", "duplicate_exit_skipped"}
        )
        rejection_reasons = Counter(str(result.get("reason", "")) for result in results if result.get("status") == "rejected")
        status = "ran" if gate.get("approved_for_paper_trading") else "blocked_by_readiness_gate"
        if not gate.get("approved_for_paper_trading") and exit_monitoring_when_blocked:
            status = "monitoring_exits_only_readiness_gate_blocked"
        summary = {
            "mode": "paper",
            "status": status,
            "approved_for_paper_trading": bool(gate.get("approved_for_paper_trading")),
            "entry_gate_blocked": not bool(gate.get("approved_for_paper_trading")),
            "exit_monitoring_when_blocked": bool(exit_monitoring_when_blocked),
            "blockers": gate.get("blockers", []),
            "signals_processed": len(results),
            "entry_pause_reason": entry_pause_reason,
            "orders_filled": filled,
            "buy_orders_filled": filled,
            "exit_orders_filled": len(closed_positions),
            "duplicates_skipped": duplicate,
            "orders_rejected": rejected,
            "filled_orders": filled_orders,
            "closed_positions": closed_positions,
            "broker_rejection_reasons": dict(rejection_reasons),
            "exit_rejection_reasons": dict(exit_rejections),
            "positions_settled": len(settlements),
            "settlements": settlements,
            "cohort_pnl": cohort_pnl,
            "cash": snapshot["cash"],
            "equity": snapshot["equity"],
            "total_exposure": snapshot["total_exposure"],
            "database_path": str(cfg.database_path),
            "generated_at_utc": now_utc(),
        }
        write_json(
            cfg.output_root / "polymarket_portfolio" / "paper_trading_summary.json",
            summary,
        )
        return summary
    finally:
        con.close()
