"""Smart-flow CLV evidence for public wallet fills.

This module scores *other traders'* observed buy fills against the same
settlement-independent closing-line-value machinery used for our own shadow
positions. The thesis is deliberately conservative: before following a wallet,
prove that its entries repeatedly beat later market lines.

It is diagnostic only. It never creates paper/live orders and never changes
promotion gates. Positive wallets become research targets for human/governance
review, not automatic signals.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .closing_line import (
    EVIDENCE_INSUFFICIENT,
    EVIDENCE_NEGATIVE,
    EVIDENCE_POSITIVE,
    _bootstrap_mean_ci,
    build_quote_history,
    position_clv_row,
)
from .config import EngineConfig, load_config
from .utils import boolish, git_commit_hash, now_utc, parse_timestamp, read_csv_rows, safe_float, write_csv, write_json

SMART_FLOW_POSITION_FIELDS = [
    "flow_position_id",
    "wallet",
    "wallet_short",
    "wallet_cohort",
    "source",
    "tx_hash",
    "signal_cohort",
    "category",
    "market_id",
    "token_id",
    "market_slug",
    "question",
    "outcome",
    "side",
    "opened_at",
    "close_time",
    "entry_price",
    "quantity",
    "line_price",
    "line_bid",
    "line_timestamp",
    "line_kind",
    "clv",
    "clv_pct",
    "clv_vs_bid",
    "beat_close",
    "quote_count",
]


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    return cfg.raw.get("smart_flow_clv", {}) or {}


def _wallet(row: dict[str, Any]) -> str:
    for key in ("wallet", "trader", "address", "maker_address", "proxy_wallet", "user"):
        value = str(row.get(key) or "").strip()
        if value:
            return value.lower()
    return ""


def _short_wallet(wallet: str) -> str:
    if len(wallet) <= 12:
        return wallet
    return f"{wallet[:6]}…{wallet[-4:]}"


def _first_text(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _first_float(row: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = safe_float(row.get(key))
        if value is not None:
            return value
    return None


def _is_buy(row: dict[str, Any]) -> bool:
    side = str(row.get("side") or row.get("action") or row.get("type") or "buy").strip().lower()
    return side in {"", "buy", "b", "buy_yes", "yes_buy", "long"}


def _position_from_fill(row: dict[str, Any], idx: int) -> dict[str, Any] | None:
    if not _is_buy(row):
        return None
    wallet = _wallet(row)
    token_id = _first_text(row, ("token_id", "asset_id", "outcome_token_id"))
    price = _first_float(row, ("entry_price", "price", "fill_price", "avg_price"))
    opened_at = _first_text(row, ("opened_at", "timestamp", "filled_at", "created_at", "collected_at_utc"))
    if not wallet or not token_id or price is None or not 0 < price < 1 or not opened_at:
        return None
    tx_hash = _first_text(row, ("tx_hash", "transaction_hash", "order_hash", "trade_id"))
    market_id = _first_text(row, ("market_id", "condition_id"))
    category = _first_text(row, ("category", "family", "market_family")) or "unknown"
    base_cohort = _first_text(row, ("signal_cohort", "cohort")) or f"smart_flow|{category}"
    wallet_short = _short_wallet(wallet)
    return {
        "shadow_position_id": f"smart-flow-{tx_hash or idx}",
        "flow_position_id": f"smart-flow-{tx_hash or idx}",
        "wallet": wallet,
        "wallet_short": wallet_short,
        "wallet_cohort": f"smart_flow|wallet={wallet_short}",
        "source": _first_text(row, ("source",)) or "public_wallet_fill",
        "tx_hash": tx_hash,
        "signal_cohort": base_cohort,
        "category": category,
        "market_id": market_id,
        "token_id": token_id,
        "market_slug": _first_text(row, ("market_slug", "slug")),
        "question": _first_text(row, ("question", "title")),
        "outcome": _first_text(row, ("outcome", "outcome_name")),
        "side": _first_text(row, ("side", "action", "type")) or "buy",
        "status": "observed_public_fill",
        "opened_at": opened_at,
        "close_time": _first_text(row, ("close_time", "end_date", "end_date_iso", "resolution_time")),
        "entry_price": price,
        "quantity": _first_float(row, ("quantity", "size", "shares", "amount")) or "",
    }


def _score_rows(
    fills: list[dict[str, Any]],
    quote_rows: list[dict[str, Any]],
    *,
    as_of: datetime | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    quotes = build_quote_history(quote_rows)
    rows: list[dict[str, Any]] = []
    skipped = {"not_buy_or_missing_required_fields": 0, "no_usable_quote_after_fill": 0}
    as_of = as_of or datetime.now(timezone.utc)
    for idx, fill in enumerate(fills, start=1):
        position = _position_from_fill(fill, idx)
        if position is None:
            skipped["not_buy_or_missing_required_fields"] += 1
            continue
        clv = position_clv_row(position, quotes, as_of=as_of)
        if clv is None:
            skipped["no_usable_quote_after_fill"] += 1
            continue
        rows.append(
            {
                **{key: position.get(key, "") for key in SMART_FLOW_POSITION_FIELDS},
                "line_price": clv["line_price"],
                "line_bid": clv["line_bid"],
                "line_timestamp": clv["line_timestamp"],
                "line_kind": clv["line_kind"],
                "clv": clv["clv"],
                "clv_pct": clv["clv_pct"],
                "clv_vs_bid": clv["clv_vs_bid"],
                "beat_close": clv["beat_close"],
                "quote_count": clv["quote_count"],
            }
        )
    return rows, skipped


def _aggregate(
    rows: list[dict[str, Any]],
    group_field: str,
    *,
    minimum_final_samples: int,
    iterations: int,
    seed: int,
    positive_ci_floor: float,
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(group_field) or "unknown")].append(row)
    out: list[dict[str, Any]] = []
    for group_name in sorted(groups):
        group = groups[group_name]
        final = [row for row in group if row.get("line_kind") == "closing"]
        clvs = [float(row["clv"]) for row in group]
        final_clvs = [float(row["clv"]) for row in final]
        ci_low, ci_high = _bootstrap_mean_ci(final_clvs, iterations=iterations, seed=seed)
        if len(final) >= minimum_final_samples and ci_low is not None and ci_low > positive_ci_floor:
            evidence = EVIDENCE_POSITIVE
            action = "watch_shadow_only"
        elif len(final) >= minimum_final_samples and ci_high is not None and ci_high < 0:
            evidence = EVIDENCE_NEGATIVE
            action = "suppress_following"
        else:
            evidence = EVIDENCE_INSUFFICIENT
            action = "collect_more_wallet_clv"
        wallets = sorted({str(row.get("wallet") or "") for row in group if row.get("wallet")})
        out.append(
            {
                group_field: group_name,
                "wallet": wallets[0] if len(wallets) == 1 else "",
                "wallet_count": len(wallets),
                "fills": len(group),
                "final_fills": len(final),
                "provisional_fills": len(group) - len(final),
                "mean_clv": round(sum(clvs) / len(clvs), 6) if clvs else None,
                "mean_final_clv": round(sum(final_clvs) / len(final_clvs), 6) if final_clvs else None,
                "beat_close_rate": round(sum(1 for row in group if str(row.get("beat_close")).lower() == "true") / len(group), 4)
                if group
                else None,
                "final_clv_ci_low": ci_low,
                "final_clv_ci_high": ci_high,
                "final_samples_needed": max(0, minimum_final_samples - len(final)),
                "clv_evidence": evidence,
                "recommended_action": action,
            }
        )
    out.sort(
        key=lambda row: (
            row.get("clv_evidence") == EVIDENCE_POSITIVE,
            float(row.get("mean_final_clv") or row.get("mean_clv") or -999),
            int(row.get("final_fills") or 0),
        ),
        reverse=True,
    )
    return out


def build_smart_flow_clv(
    cfg: EngineConfig,
    *,
    fills_input: str | Path | None = None,
    features_input: str | Path | None = None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    settings = _settings(cfg)
    fills_path = Path(fills_input or settings.get("fills_input") or cfg.data_root / "polymarket" / "public_wallet_fills.csv")
    features_path = Path(features_input or settings.get("features_input") or cfg.output_root / "polymarket_training" / "websocket_market_features.csv")
    minimum_final_samples = int(settings.get("minimum_final_samples_per_wallet", 20))
    iterations = int(settings.get("bootstrap_iterations", 1000))
    seed = int(settings.get("bootstrap_seed", 20260703))
    positive_ci_floor = float(settings.get("positive_ci_floor", 0.0))

    fills = read_csv_rows(fills_path)
    quote_rows = read_csv_rows(features_path)
    rows: list[dict[str, Any]] = []
    skipped = {"not_buy_or_missing_required_fields": 0, "no_usable_quote_after_fill": 0}
    if fills and quote_rows and boolish(settings.get("enabled", True)):
        rows, skipped = _score_rows(fills, quote_rows, as_of=as_of)

    wallets = _aggregate(
        rows,
        "wallet",
        minimum_final_samples=minimum_final_samples,
        iterations=iterations,
        seed=seed,
        positive_ci_floor=positive_ci_floor,
    )
    wallet_cohorts = _aggregate(
        rows,
        "wallet_cohort",
        minimum_final_samples=minimum_final_samples,
        iterations=iterations,
        seed=seed,
        positive_ci_floor=positive_ci_floor,
    )
    final_rows = [row for row in rows if row.get("line_kind") == "closing"]
    positive_wallets = [row["wallet"] for row in wallets if row.get("clv_evidence") == EVIDENCE_POSITIVE]
    watchlist = [row for row in wallets if row.get("clv_evidence") == EVIDENCE_POSITIVE][:10]

    summary = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "git_commit": git_commit_hash(),
        "fills_input": str(fills_path),
        "features_input": str(features_path),
        "fills_seen": len(fills),
        "fills_scored": len(rows),
        "final_line_fills": len(final_rows),
        "provisional_line_fills": len(rows) - len(final_rows),
        "minimum_final_samples_per_wallet": minimum_final_samples,
        "positive_ci_floor": positive_ci_floor,
        "positions_skipped": skipped,
        "positive_wallets": positive_wallets,
        "watchlist": watchlist,
        "wallets": wallets,
        "wallet_cohorts": wallet_cohorts,
        "evidence_semantics": {
            "clv": "later market midpoint minus observed public wallet entry price",
            "positive_wallet": "wallet has enough final-line fills and bootstrap lower CI above the configured floor",
            "use": "research watchlist only; shadow-follow ideas still require normal governance and paper proof",
        },
        "governance_note": "Smart-flow CLV is diagnostic edge discovery. It never authorises paper/live trading by itself.",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    write_csv(cfg.governance_root / "smart_flow_clv_positions.csv", rows, fieldnames=SMART_FLOW_POSITION_FIELDS)
    write_json(cfg.governance_root / "smart_flow_clv.json", summary)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return build_smart_flow_clv(load_config(config_path))
