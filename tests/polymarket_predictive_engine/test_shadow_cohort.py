from __future__ import annotations

import ast
import json
from pathlib import Path

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.shadow_cohort import _write_shadow_pnl_history, update_shadow_cohort_evidence


def _unlocked_shadow_update_calls(source: str) -> list[int]:
    tree = ast.parse(source)
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    def inside_prediction_lock(node: ast.AST) -> bool:
        ancestor = parents.get(node)
        while ancestor is not None:
            if isinstance(ancestor, ast.With):
                for item in ancestor.items:
                    call = item.context_expr
                    if (
                        isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Name)
                        and call.func.id == "runtime_lock"
                        and any(
                            isinstance(arg, ast.Constant) and arg.value == "prediction_cycle"
                            for arg in call.args
                        )
                    ):
                        return True
            ancestor = parents.get(ancestor)
        return False

    # The paper-cycle implementation is deliberately split into a small lock
    # owner and a large ``_unlocked`` helper. Treat that lexical call edge as
    # guarded only when every invocation of the helper is itself in the lock.
    calls_by_name: dict[str, list[ast.Call]] = {}
    for candidate in ast.walk(tree):
        if isinstance(candidate, ast.Call) and isinstance(candidate.func, ast.Name):
            calls_by_name.setdefault(candidate.func.id, []).append(candidate)

    unlocked: list[int] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "update_shadow_cohort_evidence"
        ):
            continue
        guarded = inside_prediction_lock(node)
        enclosing = parents.get(node)
        while enclosing is not None and not isinstance(enclosing, (ast.FunctionDef, ast.AsyncFunctionDef)):
            enclosing = parents.get(enclosing)
        if not guarded and isinstance(enclosing, (ast.FunctionDef, ast.AsyncFunctionDef)):
            callers = calls_by_name.get(enclosing.name, [])
            guarded = bool(callers) and all(inside_prediction_lock(call) for call in callers)
        if not guarded:
            unlocked.append(node.lineno)
    return unlocked


def test_all_source_shadow_update_callers_hold_prediction_cycle_lock() -> None:
    deliberately_unlocked = "def bad(cfg, rows):\n    update_shadow_cohort_evidence(cfg, rows)\n"
    assert _unlocked_shadow_update_calls(deliberately_unlocked) == [2]

    violations: list[str] = []
    for path in Path("src").rglob("*.py"):
        # utf-8-sig: several repository sources carry a UTF-8 BOM, which plain
        # utf-8 hands to ast.parse as U+FEFF and the whole scan dies with a
        # SyntaxError instead of reporting lock violations.
        for line in _unlocked_shadow_update_calls(path.read_text(encoding="utf-8-sig")):
            violations.append(f"{path}:{line}")
    assert violations == []
from polymarket_predictive_engine.utils import read_csv_rows


def _cfg(tmp_path: Path) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {
                "data_root": str(tmp_path),
                "output_root": str(tmp_path / "outputs"),
                "database_path": str(tmp_path / "work" / "paper.sqlite"),
            },
            "risk": {
                "minimum_entry_price": 0.05,
                "maximum_entry_price": 0.90,
            },
            "shadow_cohort_validation": {
                "enabled": True,
                "stake_usdc": 10,
                "candidate_limit_per_cycle": 4,
                "maximum_open_positions": 10,
                "allow_near_miss_learning_candidates": True,
                "near_miss_candidate_limit_per_cycle": 2,
                "near_miss_cohort_prefix": "near_miss_learning",
                "settle_resolved_markets": False,
            },
            "cohort_promotion": {
                "minimum_filled_orders": 5,
                "minimum_settled_orders": 3,
                "minimum_pnl_usdc": 0.0,
                "minimum_roi": 0.03,
                "minimum_tracking_hours_for_promotion": 2,
                "minimum_monthly_run_rate_usdc": 20,
            },
            "costs": {"slippage": 0.0},
        },
        path=tmp_path / "config.yaml",
    )


def test_shadow_pnl_csv_accrues_one_latest_row_per_cohort_day(tmp_path):
    cfg = _cfg(tmp_path)
    first = {
        "generated_at_utc": "2026-07-10T08:00:00Z",
        "cohorts": [{"signal_cohort": "family_a", "shadow_total_pnl_usdc": 1.0, "shadow_roi": 0.01}],
    }
    replacement = {
        "generated_at_utc": "2026-07-10T20:00:00Z",
        "cohorts": [{"signal_cohort": "family_a", "shadow_total_pnl_usdc": 2.0, "shadow_roi": 0.02}],
    }
    next_day = {
        "generated_at_utc": "2026-07-11T08:00:00Z",
        "cohorts": [{"signal_cohort": "family_a", "shadow_total_pnl_usdc": 3.0, "shadow_roi": 0.03}],
    }

    _write_shadow_pnl_history(cfg, first)
    _write_shadow_pnl_history(cfg, replacement)
    _write_shadow_pnl_history(cfg, next_day)

    rows = read_csv_rows(cfg.governance_root / "shadow_signal_cohort_pnl.csv")
    assert len(rows) == 2
    assert rows[0]["generated_at_utc"] == "2026-07-10T20:00:00Z"
    assert rows[0]["shadow_total_pnl_usdc"] == "2.0"
    assert rows[1]["generated_at_utc"] == "2026-07-11T08:00:00Z"


def _wo119_candidate(market_id: str, token_id: str) -> dict:
    return {
        "market_id": market_id,
        "market_slug": f"{market_id}-slug",
        "token_id": token_id,
        "outcome": "No",
        "category": "crypto",
        "signal_cohort": "crypto",
        "near_miss_learning_candidate": True,
        "near_miss_priority_score": "0.052",
        "near_miss_learning_reason": "near_miss_eligible",
        "shadow_trade_candidate": False,
        "executable_price": "0.50",
        "best_bid": "0.49",
        "spread": "0.01",
        "liquidity": "1000",
        "edge_lower_bound": "0.009",
        "alpha_raw_edge": "0.054",
        "prediction_timestamp": "2026-06-27T04:00:00Z",
    }


def test_wo119_shadow_fills_are_appended_never_rewritten(tmp_path):
    # WO-119: shadow_fills.csv is append_only-enrolled in the WO-61 anchor
    # registry; a second cycle must extend the file, leaving every previously
    # anchored byte untouched (the old write_csv full rewrite broke this
    # whenever the schema drifted - the WO-115 chain-break class).
    cfg = _cfg(tmp_path)
    fills_path = cfg.output_root / "polymarket_shadow" / "shadow_fills.csv"

    update_shadow_cohort_evidence(cfg, [_wo119_candidate("m-w1", "t-w1")])
    first_bytes = fills_path.read_bytes()
    update_shadow_cohort_evidence(cfg, [_wo119_candidate("m-w2", "t-w2")])
    second_bytes = fills_path.read_bytes()

    assert second_bytes.startswith(first_bytes)
    rows = read_csv_rows(fills_path)
    assert len(rows) == 2
    assert {row["market_id"] for row in rows} == {"m-w1", "m-w2"}


def test_wo119_shadow_fills_tolerate_legacy_narrow_header(tmp_path):
    # A historical file with a narrower header must be appended UNDER that
    # header (keys the legacy schema cannot hold are dropped), never rewritten
    # to the current fieldnames - a rewrite would invalidate the anchors.
    cfg = _cfg(tmp_path)
    fills_path = cfg.output_root / "polymarket_shadow" / "shadow_fills.csv"
    fills_path.parent.mkdir(parents=True, exist_ok=True)
    legacy = (
        "shadow_fill_id,shadow_position_id,created_at,side,market_id,price,quantity,gross_notional_usdc,reason\r\n"
        "f-legacy,p-legacy,2026-07-01T00:00:00Z,BUY_SHADOW,m-old,0.5,20,10,legacy\r\n"
    )
    fills_path.write_bytes(legacy.encode("utf-8"))
    before = fills_path.read_bytes()

    update_shadow_cohort_evidence(cfg, [_wo119_candidate("m-w3", "t-w3")])
    after = fills_path.read_bytes()

    assert after.startswith(before)
    rows = read_csv_rows(fills_path)
    assert len(rows) == 2
    assert rows[1]["market_id"] == "m-w3"
    # The legacy header has no shadow_source column, so the new row simply
    # does not persist that key - schema stays byte-stable.
    assert "shadow_source" not in rows[1]


def test_near_miss_candidates_open_distinct_shadow_evidence_cohort(tmp_path):
    cfg = _cfg(tmp_path)

    summary = update_shadow_cohort_evidence(
        cfg,
        [
            {
                "market_id": "m-near",
                "market_slug": "near-miss-market",
                "token_id": "t-near",
                "outcome": "No",
                "category": "crypto",
                "signal_cohort": "crypto",
                "near_miss_learning_candidate": True,
                "near_miss_priority_score": "0.052",
                "near_miss_learning_reason": "near_miss_eligible",
                "shadow_trade_candidate": False,
                "executable_price": "0.50",
                "best_bid": "0.49",
                "spread": "0.01",
                "liquidity": "1000",
                "edge_lower_bound": "0.009",
                "alpha_raw_edge": "0.054",
                "prediction_timestamp": "2026-06-27T04:00:00Z",
            }
        ],
    )

    positions = read_csv_rows(cfg.output_root / "polymarket_shadow" / "shadow_positions.csv")
    fills = read_csv_rows(cfg.output_root / "polymarket_shadow" / "shadow_fills.csv")

    assert summary["opened_this_cycle"] == 1
    assert summary["near_miss_opened_this_cycle"] == 1
    assert summary["near_miss_candidates_seen"] == 1
    assert positions[0]["shadow_source"] == "near_miss_learning"
    assert positions[0]["signal_cohort"] == "near_miss_learning|crypto"
    assert fills[0]["shadow_source"] == "near_miss_learning"
    assert summary["cohorts"][0]["signal_cohort"] == "near_miss_learning|crypto"


def test_alpha_trade_candidates_can_enter_shadow_learning_only_when_enabled(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.raw["shadow_cohort_validation"]["allow_alpha_candidate_learning_candidates"] = True
    cfg.raw["shadow_cohort_validation"]["alpha_candidate_learning_candidate_limit_per_cycle"] = 2

    summary = update_shadow_cohort_evidence(
        cfg,
        [
            {
                "market_id": "m-alpha",
                "market_slug": "alpha-candidate-market",
                "token_id": "t-alpha",
                "outcome": "Yes",
                "category": "crypto",
                "signal_cohort": "crypto",
                "alpha_trade_candidate": True,
                "validation_layer_pass": True,
                "microstructure_filter_pass": True,
                "bookmaker_cross_check_pass": True,
                "edge_lower_bound": "0.08",
                "executable_price": "0.50",
                "best_bid": "0.49",
                "spread": "0.01",
                "liquidity": "1000",
                "prediction_timestamp": "2026-06-27T04:00:00Z",
            }
        ],
    )

    positions = read_csv_rows(cfg.output_root / "polymarket_shadow" / "shadow_positions.csv")
    fills = read_csv_rows(cfg.output_root / "polymarket_shadow" / "shadow_fills.csv")

    assert summary["opened_this_cycle"] == 1
    assert summary["alpha_candidate_learning_opened_this_cycle"] == 1
    assert summary["alpha_candidate_learning_candidates_seen"] == 1
    assert positions[0]["shadow_source"] == "alpha_candidate_learning"
    source_signal = json.loads(positions[0]["source_signal_json"])
    assert source_signal["shadow_candidate_reason"] == "alpha_candidate_shadow_evidence"
    assert fills[0]["shadow_source"] == "alpha_candidate_learning"


def test_alpha_trade_candidate_shadow_learning_defaults_closed(tmp_path):
    cfg = _cfg(tmp_path)

    summary = update_shadow_cohort_evidence(
        cfg,
        [
            {
                "market_id": "m-alpha-disabled",
                "market_slug": "alpha-candidate-market",
                "token_id": "t-alpha-disabled",
                "outcome": "Yes",
                "category": "crypto",
                "signal_cohort": "crypto",
                "alpha_trade_candidate": True,
                "validation_layer_pass": True,
                "microstructure_filter_pass": True,
                "bookmaker_cross_check_pass": True,
                "edge_lower_bound": "0.08",
                "executable_price": "0.50",
                "best_bid": "0.49",
                "spread": "0.01",
                "liquidity": "1000",
                "prediction_timestamp": "2026-06-27T04:00:00Z",
            }
        ],
    )

    positions = read_csv_rows(cfg.output_root / "polymarket_shadow" / "shadow_positions.csv")

    assert summary["opened_this_cycle"] == 0
    assert summary["alpha_candidate_learning_opened_this_cycle"] == 0
    assert summary["alpha_candidate_learning_candidates_seen"] == 1
    assert positions == []


def test_alpha_learning_prioritises_in_band_candidates_before_source_cap(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.raw["shadow_cohort_validation"]["allow_alpha_candidate_learning_candidates"] = True
    cfg.raw["shadow_cohort_validation"]["candidate_limit_per_cycle"] = 1
    cfg.raw["shadow_cohort_validation"]["alpha_candidate_learning_candidate_limit_per_cycle"] = 1

    summary = update_shadow_cohort_evidence(
        cfg,
        [
            {
                "market_id": "m-too-cheap",
                "market_slug": "too-cheap-alpha",
                "token_id": "t-too-cheap",
                "outcome": "Yes",
                "category": "crypto",
                "signal_cohort": "crypto",
                "alpha_trade_candidate": True,
                "validation_layer_pass": True,
                "microstructure_filter_pass": True,
                "bookmaker_cross_check_pass": True,
                "edge_lower_bound": "0.50",
                "executable_price": "0.01",
                "best_bid": "0.009",
                "spread": "0.001",
                "liquidity": "10000",
                "prediction_timestamp": "2026-06-27T04:00:00Z",
            },
            {
                "market_id": "m-in-band",
                "market_slug": "in-band-alpha",
                "token_id": "t-in-band",
                "outcome": "Yes",
                "category": "crypto",
                "signal_cohort": "crypto",
                "alpha_trade_candidate": True,
                "validation_layer_pass": True,
                "microstructure_filter_pass": True,
                "bookmaker_cross_check_pass": True,
                "edge_lower_bound": "0.08",
                "executable_price": "0.50",
                "best_bid": "0.49",
                "spread": "0.01",
                "liquidity": "1000",
                "prediction_timestamp": "2026-06-27T04:00:00Z",
            },
        ],
    )

    positions = read_csv_rows(cfg.output_root / "polymarket_shadow" / "shadow_positions.csv")

    assert summary["opened_this_cycle"] == 1
    assert summary["entry_price_band_skipped"] == 0
    assert positions[0]["market_slug"] == "in-band-alpha"


def test_shadow_candidates_skip_past_close_rows(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.raw["shadow_cohort_validation"]["allow_alpha_candidate_learning_candidates"] = True

    summary = update_shadow_cohort_evidence(
        cfg,
        [
            {
                "market_id": "m-expired",
                "market_slug": "expired-alpha",
                "token_id": "t-expired",
                "outcome": "Yes",
                "category": "crypto",
                "signal_cohort": "crypto",
                "alpha_trade_candidate": True,
                "validation_layer_pass": True,
                "microstructure_filter_pass": True,
                "bookmaker_cross_check_pass": True,
                "edge_lower_bound": "0.50",
                "executable_price": "0.50",
                "best_bid": "0.49",
                "spread": "0.01",
                "liquidity": "10000",
                "time_to_close_hours": "-0.01",
                "prediction_timestamp": "2026-06-27T04:00:00Z",
            }
        ],
    )

    positions = read_csv_rows(cfg.output_root / "polymarket_shadow" / "shadow_positions.csv")

    assert summary["opened_this_cycle"] == 0
    assert positions == []


def test_shadow_cohort_refuses_new_positions_outside_entry_band(tmp_path):
    cfg = _cfg(tmp_path)

    summary = update_shadow_cohort_evidence(
        cfg,
        [
            {
                "market_id": "m-fav",
                "market_slug": "expensive-favourite",
                "token_id": "t-fav",
                "outcome": "No",
                "category": "macro_rates",
                "signal_cohort": "expensive_shadow_probe",
                "shadow_trade_candidate": True,
                "shadow_candidate_reason": "would_poison_shadow_evidence",
                "shadow_source": "test_shadow",
                "executable_price": "0.95",
                "best_bid": "0.94",
                "spread": "0.01",
                "liquidity": "1000",
                "prediction_timestamp": "2026-06-27T04:00:00Z",
            }
        ],
    )

    positions = read_csv_rows(cfg.output_root / "polymarket_shadow" / "shadow_positions.csv")
    fills = read_csv_rows(cfg.output_root / "polymarket_shadow" / "shadow_fills.csv")

    assert summary["opened_this_cycle"] == 0
    assert summary["entry_price_band_skipped"] == 1
    assert summary["entry_price_band"] == {
        "minimum_entry_price": 0.05,
        "maximum_entry_price": 0.9,
    }
    assert positions == []
    assert fills == []
