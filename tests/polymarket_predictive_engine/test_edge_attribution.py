from __future__ import annotations

import json
from pathlib import Path

from pytest import approx

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.edge_attribution import (
    CLASS_COST,
    CLASS_DIRECTION,
    CLASS_INSUFFICIENT,
    attribute_position,
    build_edge_attribution,
)
from polymarket_predictive_engine.utils import read_json, write_csv


def _cfg(tmp_path: Path, minimum_positions: int = 1) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "edge_attribution": {"minimum_positions_per_cohort": minimum_positions},
        },
        path=tmp_path / "cfg.yaml",
    )


def _position(
    pid: str,
    cohort: str,
    *,
    entry: float,
    exit_price: float,
    quantity: float,
    signal: dict,
    status: str = "closed",
) -> dict:
    return {
        "shadow_position_id": pid,
        "signal_cohort": cohort,
        "category": "sports_other",
        "market_slug": f"slug-{pid}",
        "status": status,
        "close_reason": "shadow_clean_settlement",
        "entry_price": entry,
        "exit_price": exit_price,
        "quantity": quantity,
        "source_signal_json": json.dumps(signal),
    }


def _line(pid: str, line_price: float) -> dict:
    return {"shadow_position_id": pid, "line_price": line_price, "line_kind": "closing"}


def test_attribution_identity_is_exact():
    # entry mid 0.50, fill 0.52 (cost 0.02/share), line 0.58 (move +0.08),
    # settled 0.55 (surprise -0.03), total per share 0.55-0.52 = +0.03.
    row = attribute_position(
        _position(
            "p1",
            "c1",
            entry=0.52,
            exit_price=0.55,
            quantity=10,
            signal={"market_midpoint": 0.50, "alpha_probability": 0.60},
        ),
        _line("p1", 0.58),
    )
    assert row is not None
    assert row["execution_cost_per_share"] == approx(0.02)
    assert row["line_movement_per_share"] == approx(0.08)
    assert row["settlement_surprise_per_share"] == approx(-0.03)
    assert row["total_pnl_per_share"] == approx(0.03)
    # Identity: total == settlement + line - execution.
    assert row["total_pnl_per_share"] == approx(
        row["settlement_surprise_per_share"] + row["line_movement_per_share"] - row["execution_cost_per_share"]
    )
    assert row["total_pnl_usdc"] == approx(0.30)
    assert row["model_claimed_edge_per_share"] == approx(0.10)


def test_open_or_unlined_positions_are_not_attributed():
    open_row = attribute_position(
        _position("p1", "c1", entry=0.52, exit_price=0.55, quantity=10, signal={"market_midpoint": 0.5}, status="open"),
        _line("p1", 0.58),
    )
    assert open_row is None
    no_line = attribute_position(
        _position("p2", "c1", entry=0.52, exit_price=0.55, quantity=10, signal={"market_midpoint": 0.5}),
        None,
    )
    assert no_line is None


def test_cohort_classification_cost_vs_direction(tmp_path: Path):
    cfg = _cfg(tmp_path, minimum_positions=1)
    # cost_dominated: line moved up (+0.02/share) but execution cost 0.04/share ate it.
    cost_pos = _position(
        "cost1",
        "cohort_cost",
        entry=0.54,
        exit_price=0.51,
        quantity=10,
        signal={"market_midpoint": 0.50},
    )
    # direction wrong: line moved down.
    dir_pos = _position(
        "dir1",
        "cohort_direction",
        entry=0.51,
        exit_price=0.42,
        quantity=10,
        signal={"market_midpoint": 0.50},
    )
    write_csv(cfg.output_root / "polymarket_shadow" / "shadow_positions.csv", [cost_pos, dir_pos])
    write_csv(
        cfg.governance_root / "closing_line_value_positions.csv",
        [_line("cost1", 0.52), _line("dir1", 0.44)],
    )

    summary = build_edge_attribution(cfg)
    assert summary["attributed_positions"] == 2
    by_cohort = {row["signal_cohort"]: row for row in summary["cohorts"]}
    assert by_cohort["cohort_cost"]["attribution_class"] == CLASS_COST
    assert "passive entries" in by_cohort["cohort_cost"]["recommended_action"]
    assert by_cohort["cohort_direction"]["attribution_class"] == CLASS_DIRECTION
    assert summary["paper_trading_invoked"] is False

    written = read_json(cfg.governance_root / "edge_attribution.json")
    assert written["attributed_positions"] == 2
    assert (cfg.governance_root / "edge_attribution_positions.csv").exists()


def test_below_minimum_positions_is_insufficient(tmp_path: Path):
    cfg = _cfg(tmp_path, minimum_positions=5)
    pos = _position("p1", "c1", entry=0.52, exit_price=0.55, quantity=10, signal={"market_midpoint": 0.50})
    write_csv(cfg.output_root / "polymarket_shadow" / "shadow_positions.csv", [pos])
    write_csv(cfg.governance_root / "closing_line_value_positions.csv", [_line("p1", 0.58)])

    summary = build_edge_attribution(cfg)
    assert summary["cohorts"][0]["attribution_class"] == CLASS_INSUFFICIENT
