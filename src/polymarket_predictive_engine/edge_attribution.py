"""Post-trade edge attribution for Polymarket shadow and paper evidence.

The goal is to explain *why* a cohort made or lost money instead of treating
P&L as one opaque number.  For each cohort we decompose observed forward
evidence into:

```text
entry edge          = lower-bound model edge at entry * stake
line movement       = CLV probability move * position quantity
spread/slippage     = conservative bid/ask crossing and exit-spread cost
settlement surprise = residual unexplained P&L after the above components
```

This module is governance-only diagnostics.  It never invokes paper or live
trading and it does not change promotion gates.  The resulting artifact feeds
research focus so the system can distinguish "bad model thesis" from "good
thesis killed by costs/quote quality" and collect better evidence.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .config import EngineConfig, load_config
from .utils import git_commit_hash, now_utc, read_csv_rows, safe_float, write_csv, write_json

OUTPUT_JSON = "edge_attribution.json"
OUTPUT_CSV = "edge_attribution_cohorts.csv"

COHORT_FIELDS = [
    "signal_cohort",
    "family",
    "paper_round_trips",
    "paper_audited_round_trips",
    "paper_quote_conflict_round_trips",
    "paper_quote_unverified_round_trips",
    "paper_realized_pnl_usdc",
    "paper_audited_pnl_usdc",
    "paper_unaudited_pnl_usdc",
    "shadow_positions",
    "shadow_closed_positions",
    "shadow_realized_pnl_usdc",
    "shadow_unrealized_pnl_usdc",
    "shadow_total_pnl_usdc",
    "entry_edge_usdc",
    "spread_slippage_cost_usdc",
    "clv_positions",
    "clv_final_positions",
    "mean_clv",
    "mean_final_clv",
    "line_movement_usdc",
    "decision_pnl_usdc",
    "closed_decision_pnl_usdc",
    "residual_pnl_usdc",
    "settlement_surprise_usdc",
    "primary_drag",
    "recommended_action",
]


def _num(value: Any, default: float = 0.0) -> float:
    parsed = safe_float(value)
    return default if parsed is None else float(parsed)


def _cohort(row: dict[str, Any]) -> str:
    value = row.get("signal_cohort") or row.get("cohort") or row.get("family") or row.get("category")
    text = str(value or "").strip()
    return text or "unknown"


def _family(row: dict[str, Any]) -> str:
    value = row.get("family") or row.get("category") or row.get("signal_cohort") or row.get("cohort")
    text = str(value or "").strip()
    return text or "unknown"


def _quote_status(row: dict[str, Any]) -> str:
    return str(row.get("quote_consistency_status") or row.get("quote_consistency") or "").strip().lower()


def _is_quote_consistent(row: dict[str, Any]) -> bool:
    status = _quote_status(row)
    return status in {"quote_consistent", "consistent", "snapshot_consistent"} or status.startswith("quote_consistent")


def _is_quote_conflict(row: dict[str, Any]) -> bool:
    return "conflict" in _quote_status(row)


def _is_quote_unverified(row: dict[str, Any]) -> bool:
    status = _quote_status(row)
    return "unverified" in status or "missing_snapshot" in status


def _is_closed_shadow_position(row: dict[str, Any]) -> bool:
    status = str(row.get("status") or "").strip().lower()
    return bool(
        status in {"closed", "settled", "sold", "resolved"}
        or str(row.get("closed_at") or "").strip()
        or str(row.get("exit_price") or "").strip()
    )


def _spread_slippage_cost(row: dict[str, Any]) -> float:
    quantity = max(0.0, _num(row.get("quantity")))
    if quantity <= 0:
        return 0.0

    entry_price = safe_float(row.get("entry_price"))
    entry_bid = safe_float(row.get("entry_signal_bid"))
    entry_spread = safe_float(row.get("entry_signal_spread"))
    entry_cost = 0.0
    if entry_price is not None and entry_bid is not None:
        entry_cost = max(0.0, entry_price - entry_bid) * quantity
    elif entry_spread is not None:
        entry_cost = max(0.0, entry_spread) * quantity / 2.0

    exit_spread = safe_float(row.get("exit_quote_spread"))
    if exit_spread is None:
        exit_bid = safe_float(row.get("exit_quote_bid"))
        exit_ask = safe_float(row.get("exit_quote_ask"))
        if exit_bid is not None and exit_ask is not None:
            exit_spread = max(0.0, exit_ask - exit_bid)
    exit_cost = max(0.0, exit_spread or 0.0) * quantity / 2.0
    return entry_cost + exit_cost


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def _rounded(value: float | None, digits: int = 6) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _primary_drag(row: dict[str, Any]) -> str:
    decision_pnl = _num(row.get("decision_pnl_usdc"))
    entry_edge = _num(row.get("entry_edge_usdc"))
    spread_cost = _num(row.get("spread_slippage_cost_usdc"))
    mean_final_clv = safe_float(row.get("mean_final_clv"))
    mean_clv = safe_float(row.get("mean_clv"))
    if _num(row.get("paper_quote_conflict_round_trips")) > 0 or _num(row.get("paper_quote_unverified_round_trips")) > 0:
        return "quote_quality"
    if spread_cost > 0.05 and spread_cost > max(0.0, entry_edge) * 0.5:
        return "spread_slippage"
    if (mean_final_clv is not None and mean_final_clv < 0) or (mean_final_clv is None and mean_clv is not None and mean_clv < 0):
        return "adverse_line_movement"
    if decision_pnl < 0 and entry_edge > 0:
        return "model_edge_failed_to_transfer"
    if decision_pnl > 0:
        return "positive_forward_edge"
    return "insufficient_evidence"


def _recommended_action(primary_drag: str) -> str:
    return {
        "quote_quality": "fix_quote_audit_before_promotion",
        "spread_slippage": "tighten_liquidity_spread_or_reduce_size",
        "adverse_line_movement": "suppress_until_new_thesis_or_collect_independent_anchor",
        "model_edge_failed_to_transfer": "collect_more_or_retrain_before_promotion",
        "positive_forward_edge": "collect_confirmation_until_governance_threshold",
        "insufficient_evidence": "collect_more_forward_evidence",
    }.get(primary_drag, "collect_more_forward_evidence")


def _paper_components(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "families": Counter(),
            "paper_round_trips": 0,
            "paper_audited_round_trips": 0,
            "paper_quote_conflict_round_trips": 0,
            "paper_quote_unverified_round_trips": 0,
            "paper_realized_pnl_usdc": 0.0,
            "paper_audited_pnl_usdc": 0.0,
            "entry_edge_usdc": 0.0,
            "spread_slippage_cost_usdc": 0.0,
        }
    )
    for row in rows:
        group = groups[_cohort(row)]
        group["families"][_family(row)] += 1
        pnl = _num(row.get("realized_pnl_usdc"))
        group["paper_round_trips"] += 1
        group["paper_realized_pnl_usdc"] += pnl
        if _is_quote_consistent(row):
            group["paper_audited_round_trips"] += 1
            group["paper_audited_pnl_usdc"] += pnl
        if _is_quote_conflict(row):
            group["paper_quote_conflict_round_trips"] += 1
        if _is_quote_unverified(row):
            group["paper_quote_unverified_round_trips"] += 1
        group["entry_edge_usdc"] += max(0.0, _num(row.get("edge_lower_bound"))) * max(0.0, _num(row.get("stake_usdc")))
        group["spread_slippage_cost_usdc"] += _spread_slippage_cost(row)
    return dict(groups)


def _shadow_components(rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, tuple[str, float]]]:
    groups: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "families": Counter(),
            "shadow_positions": 0,
            "shadow_closed_positions": 0,
            "shadow_realized_pnl_usdc": 0.0,
            "shadow_unrealized_pnl_usdc": 0.0,
            "entry_edge_usdc": 0.0,
        }
    )
    quantities: dict[str, tuple[str, float]] = {}
    for row in rows:
        cohort = _cohort(row)
        group = groups[cohort]
        group["families"][_family(row)] += 1
        group["shadow_positions"] += 1
        if _is_closed_shadow_position(row):
            group["shadow_closed_positions"] += 1
        realised = _num(row.get("realised_pnl_usdc") or row.get("realized_pnl_usdc"))
        unrealised = _num(row.get("unrealised_pnl_usdc") or row.get("unrealized_pnl_usdc"))
        group["shadow_realized_pnl_usdc"] += realised
        group["shadow_unrealized_pnl_usdc"] += unrealised
        group["entry_edge_usdc"] += max(0.0, _num(row.get("entry_edge_lower_bound"))) * max(0.0, _num(row.get("stake_usdc")))
        position_id = str(row.get("shadow_position_id") or "").strip()
        if position_id:
            quantities[position_id] = (cohort, max(0.0, _num(row.get("quantity"))))
    return dict(groups), quantities


def _clv_components(rows: list[dict[str, Any]], quantities: dict[str, tuple[str, float]]) -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "clv_positions": 0,
            "clv_final_positions": 0,
            "clv_values": [],
            "final_clv_values": [],
            "line_movement_usdc": 0.0,
        }
    )
    for row in rows:
        position_id = str(row.get("shadow_position_id") or "").strip()
        cohort = _cohort(row)
        quantity = 0.0
        if position_id and position_id in quantities:
            cohort, quantity = quantities[position_id]
        group = groups[cohort]
        clv = safe_float(row.get("clv"))
        if clv is None:
            continue
        group["clv_positions"] += 1
        group["clv_values"].append(clv)
        group["line_movement_usdc"] += clv * quantity
        if str(row.get("line_kind") or "").strip().lower() == "closing":
            group["clv_final_positions"] += 1
            group["final_clv_values"].append(clv)
    return dict(groups)


def _dominant_family(*counters: Counter[str]) -> str:
    merged: Counter[str] = Counter()
    for counter in counters:
        merged.update(counter)
    if not merged:
        return "unknown"
    return merged.most_common(1)[0][0]


def build_edge_attribution(cfg: EngineConfig) -> dict[str, Any]:
    """Build edge-attribution JSON/CSV artifacts from existing evidence only."""
    price_root = cfg.output_root / "polymarket_price_action"
    shadow_root = cfg.output_root / "polymarket_shadow"
    governance = cfg.governance_root

    paper_rows = read_csv_rows(price_root / "paper_broker_round_trip_evidence.csv")
    shadow_rows = read_csv_rows(shadow_root / "shadow_positions.csv")
    clv_rows = read_csv_rows(governance / "closing_line_value_positions.csv")

    paper = _paper_components(paper_rows)
    shadow, shadow_quantities = _shadow_components(shadow_rows)
    clv = _clv_components(clv_rows, shadow_quantities)

    cohorts = sorted(set(paper) | set(shadow) | set(clv))
    rows: list[dict[str, Any]] = []
    for cohort in cohorts:
        paper_group = paper.get(cohort, {})
        shadow_group = shadow.get(cohort, {})
        clv_group = clv.get(cohort, {})

        paper_realized = _num(paper_group.get("paper_realized_pnl_usdc"))
        paper_audited = _num(paper_group.get("paper_audited_pnl_usdc"))
        paper_unaudited = paper_realized - paper_audited
        shadow_realized = _num(shadow_group.get("shadow_realized_pnl_usdc"))
        shadow_unrealized = _num(shadow_group.get("shadow_unrealized_pnl_usdc"))
        shadow_total = shadow_realized + shadow_unrealized
        entry_edge = _num(paper_group.get("entry_edge_usdc")) + _num(shadow_group.get("entry_edge_usdc"))
        spread_cost = _num(paper_group.get("spread_slippage_cost_usdc"))
        line_movement = _num(clv_group.get("line_movement_usdc"))
        decision_pnl = paper_audited + shadow_total
        closed_decision_pnl = paper_audited + shadow_realized
        residual = decision_pnl - entry_edge - line_movement + spread_cost

        row = {
            "signal_cohort": cohort,
            "family": _dominant_family(
                paper_group.get("families", Counter()) if isinstance(paper_group.get("families"), Counter) else Counter(),
                shadow_group.get("families", Counter()) if isinstance(shadow_group.get("families"), Counter) else Counter(),
            ),
            "paper_round_trips": int(_num(paper_group.get("paper_round_trips"))),
            "paper_audited_round_trips": int(_num(paper_group.get("paper_audited_round_trips"))),
            "paper_quote_conflict_round_trips": int(_num(paper_group.get("paper_quote_conflict_round_trips"))),
            "paper_quote_unverified_round_trips": int(_num(paper_group.get("paper_quote_unverified_round_trips"))),
            "paper_realized_pnl_usdc": round(paper_realized, 6),
            "paper_audited_pnl_usdc": round(paper_audited, 6),
            "paper_unaudited_pnl_usdc": round(paper_unaudited, 6),
            "shadow_positions": int(_num(shadow_group.get("shadow_positions"))),
            "shadow_closed_positions": int(_num(shadow_group.get("shadow_closed_positions"))),
            "shadow_realized_pnl_usdc": round(shadow_realized, 6),
            "shadow_unrealized_pnl_usdc": round(shadow_unrealized, 6),
            "shadow_total_pnl_usdc": round(shadow_total, 6),
            "entry_edge_usdc": round(entry_edge, 6),
            "spread_slippage_cost_usdc": round(spread_cost, 6),
            "clv_positions": int(_num(clv_group.get("clv_positions"))),
            "clv_final_positions": int(_num(clv_group.get("clv_final_positions"))),
            "mean_clv": _mean([float(v) for v in clv_group.get("clv_values", [])]),
            "mean_final_clv": _mean([float(v) for v in clv_group.get("final_clv_values", [])]),
            "line_movement_usdc": round(line_movement, 6),
            "decision_pnl_usdc": round(decision_pnl, 6),
            "closed_decision_pnl_usdc": round(closed_decision_pnl, 6),
            "residual_pnl_usdc": round(residual, 6),
            "settlement_surprise_usdc": round(residual, 6),
        }
        row["primary_drag"] = _primary_drag(row)
        row["recommended_action"] = _recommended_action(str(row["primary_drag"]))
        rows.append(row)

    rows.sort(
        key=lambda row: (
            _num(row.get("decision_pnl_usdc")),
            _num(row.get("paper_audited_pnl_usdc")),
            _num(row.get("shadow_total_pnl_usdc")),
        ),
        reverse=True,
    )

    primary_drag_counts = dict(Counter(str(row.get("primary_drag") or "unknown") for row in rows))
    total_paper_audited = sum(_num(row.get("paper_audited_pnl_usdc")) for row in rows)
    total_shadow = sum(_num(row.get("shadow_total_pnl_usdc")) for row in rows)
    total_entry_edge = sum(_num(row.get("entry_edge_usdc")) for row in rows)
    total_spread_cost = sum(_num(row.get("spread_slippage_cost_usdc")) for row in rows)
    total_line_movement = sum(_num(row.get("line_movement_usdc")) for row in rows)

    summary = {
        "status": "ok" if rows else "insufficient_evidence",
        "generated_at_utc": now_utc(),
        "git_commit": git_commit_hash(),
        "cohorts_attributed": len(rows),
        "paper_round_trips": len(paper_rows),
        "paper_audited_round_trips": sum(int(_num(row.get("paper_audited_round_trips"))) for row in rows),
        "shadow_positions": len(shadow_rows),
        "clv_positions": len(clv_rows),
        "quote_conflict_round_trips": sum(int(_num(row.get("paper_quote_conflict_round_trips"))) for row in rows),
        "quote_unverified_round_trips": sum(int(_num(row.get("paper_quote_unverified_round_trips"))) for row in rows),
        "total_paper_audited_pnl_usdc": _rounded(total_paper_audited),
        "total_shadow_pnl_usdc": _rounded(total_shadow),
        "total_decision_pnl_usdc": _rounded(total_paper_audited + total_shadow),
        "total_entry_edge_usdc": _rounded(total_entry_edge),
        "total_spread_slippage_cost_usdc": _rounded(total_spread_cost),
        "total_line_movement_usdc": _rounded(total_line_movement),
        "primary_drag_counts": primary_drag_counts,
        "top_positive_cohorts": [row for row in rows if _num(row.get("decision_pnl_usdc")) > 0][:10],
        "top_negative_cohorts": sorted(
            [row for row in rows if _num(row.get("decision_pnl_usdc")) < 0],
            key=lambda row: _num(row.get("decision_pnl_usdc")),
        )[:10],
        "cohorts": rows,
        "evidence_semantics": {
            "paper_audited_pnl_usdc": "paper round-trip P&L only where quote consistency is verified",
            "paper_unaudited_pnl_usdc": "raw paper P&L excluded from audited goal evidence because quote status is conflicted/unverified",
            "entry_edge_usdc": "sum(edge_lower_bound * stake_usdc) from paper signals and shadow entries",
            "line_movement_usdc": "sum(CLV probability move * shadow quantity); positive means the line moved toward the entry",
            "spread_slippage_cost_usdc": "estimated bid/ask crossing plus half exit spread cost from paper round trips",
            "settlement_surprise_usdc": "residual P&L after entry edge, CLV line movement, and spread/slippage cost",
        },
        "governance_note": "Edge attribution is diagnostic feedback for research focus. It does not authorise paper or live trading by itself.",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    write_csv(governance / OUTPUT_CSV, rows, fieldnames=COHORT_FIELDS)
    write_json(governance / OUTPUT_JSON, summary)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return build_edge_attribution(load_config(config_path))
