"""Edge attribution: decompose closed shadow P&L into its quant components.

For a BUY position the per-share identity is exact:

```text
exit - entry_fill = (exit - line)            settlement surprise
                  + (line - entry_mid)       line movement (CLV while held)
                  - (entry_fill - entry_mid) execution cost (half-spread + slippage)
```

Attribution turns "the cohort lost money" into an actionable diagnosis:

```text
cost_dominated                the line moved our way but execution ate it
                              -> improve entries (passive/join-bid), not the model
model_direction_not_confirmed the line moved against us -> re-model or suppress
settlement_adverse            line agreed with us but settlement broke against
                              the final line -> resolution/timing risk work
positive_edge_confirmed       realised P&L positive on enough closed positions
```

Inputs are existing artifacts only: closed shadow positions (with the embedded
entry signal JSON) joined to CLV lines by ``shadow_position_id``. Shadow-only
diagnostics: this module never trades and never feeds promotion gates; it
directs research and collection priorities.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from .config import EngineConfig, load_config
from .utils import git_commit_hash, now_utc, read_csv_rows, safe_float, write_csv, write_json

ROW_FIELDS = [
    "shadow_position_id",
    "signal_cohort",
    "category",
    "market_slug",
    "close_reason",
    "quantity",
    "entry_fill_price",
    "entry_mid_price",
    "line_price",
    "line_kind",
    "exit_price",
    "model_probability",
    "model_claimed_edge_per_share",
    "execution_cost_per_share",
    "line_movement_per_share",
    "settlement_surprise_per_share",
    "total_pnl_per_share",
    "execution_cost_usdc",
    "line_movement_usdc",
    "settlement_surprise_usdc",
    "total_pnl_usdc",
]

CLASS_POSITIVE = "positive_edge_confirmed"
CLASS_COST = "cost_dominated"
CLASS_DIRECTION = "model_direction_not_confirmed"
CLASS_SETTLEMENT = "settlement_adverse"
CLASS_MIXED = "mixed_attribution"
CLASS_INSUFFICIENT = "insufficient_attribution_evidence"

RECOMMENDED_ACTIONS = {
    CLASS_POSITIVE: "keep collecting; candidate for deeper forward evidence and governance review",
    CLASS_COST: "edge exists in the line but execution eats it: work passive entries (join bid), tighter spread gates, smaller stakes; do not loosen model thresholds",
    CLASS_DIRECTION: "the market moved against entries: re-model features for this cohort or suppress collection priority",
    CLASS_SETTLEMENT: "line agreed but settlement broke against the final line: investigate resolution risk, holding horizon, and exit policy",
    CLASS_MIXED: "no dominant loss driver: keep collecting closed evidence before acting",
    CLASS_INSUFFICIENT: "not enough closed attributed positions: collect more before drawing conclusions",
}


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    return cfg.raw.get("edge_attribution", {}) or {}


def _signal_payload(position: dict[str, Any]) -> dict[str, Any]:
    raw = position.get("source_signal_json") or "{}"
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _entry_mid(signal: dict[str, Any]) -> float | None:
    for key in ("market_midpoint", "midpoint"):
        value = safe_float(signal.get(key))
        if value is not None and 0 < value < 1:
            return value
    bid = safe_float(signal.get("best_bid"))
    ask = safe_float(signal.get("best_ask"))
    if bid is not None and ask is not None and 0 < bid <= ask < 1:
        return (bid + ask) / 2.0
    for key in ("market_price", "executable_price"):
        value = safe_float(signal.get(key))
        if value is not None and 0 < value < 1:
            return value
    return None


def _model_probability(signal: dict[str, Any]) -> float | None:
    for key in ("alpha_probability", "calibrated_probability", "model_probability"):
        value = safe_float(signal.get(key))
        if value is not None and 0 <= value <= 1:
            return value
    return None


def attribute_position(position: dict[str, Any], line_row: dict[str, Any] | None) -> dict[str, Any] | None:
    """Decompose one closed position; None when it cannot be attributed."""
    if str(position.get("status") or "").strip().lower() != "closed":
        return None
    entry_fill = safe_float(position.get("entry_price"))
    exit_price = safe_float(position.get("exit_price"))
    quantity = safe_float(position.get("quantity"))
    if entry_fill is None or exit_price is None or quantity is None or quantity <= 0:
        return None
    signal = _signal_payload(position)
    entry_mid = _entry_mid(signal)
    line_price = safe_float((line_row or {}).get("line_price"))
    line_kind = str((line_row or {}).get("line_kind") or "")
    if entry_mid is None or line_price is None:
        return None

    execution_cost_ps = entry_fill - entry_mid
    line_movement_ps = line_price - entry_mid
    settlement_ps = exit_price - line_price
    total_ps = exit_price - entry_fill
    model_probability = _model_probability(signal)
    return {
        "shadow_position_id": position.get("shadow_position_id", ""),
        "signal_cohort": position.get("signal_cohort", "") or "unknown",
        "category": position.get("category", "") or "unknown",
        "market_slug": position.get("market_slug", ""),
        "close_reason": position.get("close_reason", ""),
        "quantity": round(quantity, 6),
        "entry_fill_price": round(entry_fill, 6),
        "entry_mid_price": round(entry_mid, 6),
        "line_price": round(line_price, 6),
        "line_kind": line_kind,
        "exit_price": round(exit_price, 6),
        "model_probability": "" if model_probability is None else round(model_probability, 6),
        "model_claimed_edge_per_share": "" if model_probability is None else round(model_probability - entry_mid, 6),
        "execution_cost_per_share": round(execution_cost_ps, 6),
        "line_movement_per_share": round(line_movement_ps, 6),
        "settlement_surprise_per_share": round(settlement_ps, 6),
        "total_pnl_per_share": round(total_ps, 6),
        "execution_cost_usdc": round(execution_cost_ps * quantity, 6),
        "line_movement_usdc": round(line_movement_ps * quantity, 6),
        "settlement_surprise_usdc": round(settlement_ps * quantity, 6),
        "total_pnl_usdc": round(total_ps * quantity, 6),
    }


def _classify(rows: list[dict[str, Any]], minimum_positions: int) -> tuple[str, dict[str, float]]:
    n = len(rows)
    totals = {
        "total_pnl_usdc": sum(float(row["total_pnl_usdc"]) for row in rows),
        "execution_cost_usdc": sum(float(row["execution_cost_usdc"]) for row in rows),
        "line_movement_usdc": sum(float(row["line_movement_usdc"]) for row in rows),
        "settlement_surprise_usdc": sum(float(row["settlement_surprise_usdc"]) for row in rows),
    }
    if n < minimum_positions:
        return CLASS_INSUFFICIENT, totals
    if totals["total_pnl_usdc"] > 0:
        return CLASS_POSITIVE, totals
    if totals["line_movement_usdc"] <= 0:
        return CLASS_DIRECTION, totals
    if totals["execution_cost_usdc"] >= totals["line_movement_usdc"]:
        return CLASS_COST, totals
    if totals["settlement_surprise_usdc"] < 0 and abs(totals["settlement_surprise_usdc"]) > totals["line_movement_usdc"]:
        return CLASS_SETTLEMENT, totals
    return CLASS_MIXED, totals


def build_edge_attribution(cfg: EngineConfig) -> dict[str, Any]:
    """Attribute every closed shadow position and write governance artifacts."""
    settings = _settings(cfg)
    minimum_positions = int(settings.get("minimum_positions_per_cohort", 5))

    positions = read_csv_rows(cfg.output_root / "polymarket_shadow" / "shadow_positions.csv")
    clv_rows = read_csv_rows(cfg.governance_root / "closing_line_value_positions.csv")
    line_by_position = {str(row.get("shadow_position_id") or ""): row for row in clv_rows}

    attributed: list[dict[str, Any]] = []
    closed_seen = 0
    skipped_unattributable = 0
    for position in positions:
        if str(position.get("status") or "").strip().lower() == "closed":
            closed_seen += 1
        row = attribute_position(position, line_by_position.get(str(position.get("shadow_position_id") or "")))
        if row is None:
            if str(position.get("status") or "").strip().lower() == "closed":
                skipped_unattributable += 1
            continue
        attributed.append(row)

    cohorts: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in attributed:
        grouped[str(row["signal_cohort"])].append(row)
    for name in sorted(grouped):
        rows = grouped[name]
        classification, totals = _classify(rows, minimum_positions)
        cohorts.append(
            {
                "signal_cohort": name,
                "attributed_positions": len(rows),
                "total_pnl_usdc": round(totals["total_pnl_usdc"], 6),
                "execution_cost_usdc": round(totals["execution_cost_usdc"], 6),
                "line_movement_usdc": round(totals["line_movement_usdc"], 6),
                "settlement_surprise_usdc": round(totals["settlement_surprise_usdc"], 6),
                "attribution_class": classification,
                "recommended_action": RECOMMENDED_ACTIONS[classification],
            }
        )

    summary = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "git_commit": git_commit_hash(),
        "closed_positions_seen": closed_seen,
        "attributed_positions": len(attributed),
        "skipped_unattributable_closed": skipped_unattributable,
        "minimum_positions_per_cohort": minimum_positions,
        "cohorts": cohorts,
        "identity_note": "per share: exit - entry_fill == settlement_surprise + line_movement - execution_cost (exact)",
        "governance_note": "attribution directs research and collection; it never promotes, sizes, or trades by itself",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    write_csv(cfg.governance_root / "edge_attribution_positions.csv", attributed, fieldnames=ROW_FIELDS)
    write_json(cfg.governance_root / "edge_attribution.json", summary)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return build_edge_attribution(load_config(config_path))
