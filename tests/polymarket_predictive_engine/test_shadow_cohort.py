from __future__ import annotations

import ast
import contextlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
import types
from pathlib import Path

import pytest

from polymarket_predictive_engine import runtime_lock
from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.shadow_cohort import _write_shadow_pnl_history, update_shadow_cohort_evidence
import polymarket_predictive_engine.shadow_cohort as shadow_cohort_module
from polymarket_predictive_engine.utils import now_utc, read_json, write_csv


def _unlocked_shadow_update_calls(source: str) -> list[int]:
    tree = ast.parse(source)
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    def _is_acquired_attr(node: ast.AST, lock_name: str) -> bool:
        return (
            isinstance(node, ast.Attribute)
            and node.attr == "acquired"
            and isinstance(node.value, ast.Name)
            and node.value.id == lock_name
        )

    def _is_not_acquired(node: ast.AST, lock_name: str) -> bool:
        return (
            isinstance(node, ast.UnaryOp)
            and isinstance(node.op, ast.Not)
            and _is_acquired_attr(node.operand, lock_name)
        )

    def _contains(root: ast.AST, target: ast.AST) -> bool:
        return any(descendant is target for descendant in ast.walk(root))

    def _acquired_guarded(node: ast.AST, with_node: ast.With, lock_name: str) -> bool:
        # Lexical nesting is NOT the serialization contract: runtime_lock
        # deliberately ENTERS the block with acquired=False when the lock is
        # held (skip-when-held), so a call inside the with-block can still race
        # an existing writer unless control flow checks the acquisition. The
        # two production shapes are accepted:
        #   A) the call sits under `if lock.acquired:` (or in the else branch
        #      of `if not lock.acquired:`), or
        #   B) an earlier statement of the with-body is `if not lock.acquired:`
        #      ending in return/raise/continue/break, dominating the call.
        ancestor = parents.get(node)
        while ancestor is not None and ancestor is not with_node:
            if isinstance(ancestor, ast.If):
                in_body = any(_contains(stmt, node) for stmt in ancestor.body)
                if _is_acquired_attr(ancestor.test, lock_name) and in_body:
                    return True
                if _is_not_acquired(ancestor.test, lock_name) and not in_body:
                    return True
            ancestor = parents.get(ancestor)
        for statement in with_node.body:
            if _contains(statement, node):
                break
            if (
                isinstance(statement, ast.If)
                and _is_not_acquired(statement.test, lock_name)
                and statement.body
                and isinstance(statement.body[-1], (ast.Return, ast.Raise, ast.Continue, ast.Break))
            ):
                return True
        return False

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
                        lock_target = item.optional_vars
                        if isinstance(lock_target, ast.Name) and _acquired_guarded(
                            node, ancestor, lock_target.id
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

    def inside_any_prediction_lock_block(node: ast.AST) -> bool:
        # WO-143b.1 F2: true when `node` is lexically nested inside ANY
        # `runtime_lock(cfg, "prediction_cycle")` with-block, whether or not
        # the block checks `lock.acquired`. Used ONLY to separate two cases
        # that must be judged differently now that the callee holds its own
        # lock:
        #   * no prediction_cycle reference at all -> the call relies solely
        #     on `update_shadow_cohort_evidence`'s internal `shadow_cohort`
        #     lock, which WO-143b makes an accepted guard;
        #   * references prediction_cycle but never checks `acquired` -> still
        #     rejected. runtime_lock ENTERS the block with acquired=False when
        #     the lock is held, so this shape implies a gate that is not
        #     there, and stays a structural bug (Codex P2, first fix round).
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
        if not guarded and not inside_any_prediction_lock_block(node):
            # WO-143b.1 F2: the callee now acquires its own internal
            # `shadow_cohort` runtime lock at entry, so a caller that makes no
            # prediction_cycle guard attempt at all is NOT an unguarded write.
            guarded = True
        if not guarded:
            unlocked.append(node.lineno)
    return unlocked


# WO-143b.1 F2 (widen the scan root): the scan used to root at `Path("src")`,
# which is exactly WHY it never caught `scripts/run_polymarket_local_live_loop.py`
# -- the unguarded caller that motivated this entire work order. Three of the
# five F4 call sites live under `scripts/`. `Path("src")` is also CWD-relative,
# so it silently covers ZERO files when pytest runs from anywhere but the repo
# root (the same hermeticity family as F3). Anchor off this file's location
# instead, and scan both trees.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SHADOW_CALLER_SCAN_ROOTS = ("src", "scripts")


def test_all_source_shadow_update_callers_hold_prediction_cycle_lock() -> None:
    # WO-143b.1 F2 (test 5): a caller that attempts NO prediction_cycle guard
    # relies solely on the callee's internal `shadow_cohort` lock -- an
    # accepted guard, not an unguarded write. This case previously asserted
    # `== [2]`; the amendment retargets the rule it encoded.
    bare_call_relies_on_internal_lock = "def good(cfg, rows):\n    update_shadow_cohort_evidence(cfg, rows)\n"
    assert _unlocked_shadow_update_calls(bare_call_relies_on_internal_lock) == []

    # WO-143b.1 F2 (test 4): neither guard satisfied -- references the
    # prediction_cycle lock but never checks `acquired`. Codex review P2 on the
    # first fix round: lexical nesting alone must NOT satisfy the guard,
    # because runtime_lock enters the block with acquired=False when the lock
    # is held. This caller passes a naive scan and races an existing writer.
    nested_but_unchecked = (
        "def risky(cfg, rows):\n"
        '    with runtime_lock(cfg, "prediction_cycle") as lock:\n'
        "        update_shadow_cohort_evidence(cfg, rows)\n"
    )
    assert _unlocked_shadow_update_calls(nested_but_unchecked) == [3]

    # Both production guard shapes stay accepted: longshot_bias's
    # `if lock.acquired:` branch and paper_cycle's dominating early skip.
    acquired_branch = (
        "def good(cfg, rows):\n"
        '    with runtime_lock(cfg, "prediction_cycle") as lock:\n'
        "        if lock.acquired:\n"
        "            update_shadow_cohort_evidence(cfg, rows)\n"
    )
    assert _unlocked_shadow_update_calls(acquired_branch) == []

    early_skip = (
        "def good(cfg, rows):\n"
        '    with runtime_lock(cfg, "prediction_cycle") as lock:\n'
        "        if not lock.acquired:\n"
        "            return None\n"
        "        return update_shadow_cohort_evidence(cfg, rows)\n"
    )
    assert _unlocked_shadow_update_calls(early_skip) == []

    violations: list[str] = []
    visited = 0
    for root_name in _SHADOW_CALLER_SCAN_ROOTS:
        root = _REPO_ROOT / root_name
        assert root.is_dir(), f"scan root {root} is missing -- the scan would silently cover nothing"
        for path in sorted(root.rglob("*.py")):
            visited += 1
            # utf-8-sig: several repository sources carry a UTF-8 BOM, which plain
            # utf-8 hands to ast.parse as U+FEFF and the whole scan dies with a
            # SyntaxError instead of reporting lock violations.
            for line in _unlocked_shadow_update_calls(path.read_text(encoding="utf-8-sig")):
                violations.append(f"{path.relative_to(_REPO_ROOT)}:{line}")
    # A scan that visited nothing "passes" vacuously; assert it actually ran.
    assert visited > 0, "shadow-caller scan visited no files"
    assert violations == []


def test_shadow_caller_scan_covers_the_scripts_tree_that_motivated_wo143b() -> None:
    # WO-143b.1 F2: the scan is only a defence if it actually reaches the
    # `scripts/` callers. Rooting at `Path("src")` is why it never caught
    # `scripts/run_polymarket_local_live_loop.py`. Pin that the widened scan
    # genuinely visits every F4 call-site file, so a future narrowing of the
    # roots fails here instead of going quiet.
    scanned = {
        path.relative_to(_REPO_ROOT).as_posix()
        for root_name in _SHADOW_CALLER_SCAN_ROOTS
        for path in (_REPO_ROOT / root_name).rglob("*.py")
    }
    for expected in (
        "src/polymarket_predictive_engine/paper_cycle.py",
        "src/polymarket_predictive_engine/longshot_bias.py",
        "scripts/run_polymarket_local_live_loop.py",
        "scripts/run_alpha_candidate_shadow_evidence.py",
        "scripts/run_promoted_rule_shadow_scan.py",
    ):
        assert expected in scanned, f"{expected} is outside the shadow-caller scan roots"
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


# --- WO-143b: update_shadow_cohort_evidence's own `shadow_cohort` lock -----
#
# `update_shadow_cohort_evidence` used to document (and rely on) the caller
# holding the `prediction_cycle` runtime lock without enforcing it. The
# deployed live loop's per-tick shadow maintenance calls this function
# without holding that lock, racing the full paper cycle's in-lock call
# against the append-only `shadow_fills.csv`. These tests cover the
# function's own internal, independent `shadow_cohort` lock.

# WO-143b.1 F3: this baseline used to be resolved through git object
# `ab16ee9` via `git show`, and the loader SKIPPED the byte-identity
# regression whenever that object was unreachable -- the normal state of a
# shallow CI checkout or a worktree. A regression test that skips itself on
# the machines that run it is not a regression test. The baseline now ships
# as a committed fixture, byte-for-byte the parent-of-this-branch
# `shadow_cohort.py`, so the comparison is hermetic and offline with no git
# dependency at all, per `docs/ENGINEERING_STANDARDS.md`'s recorded-reality
# fixture rule. It is stored with a `.py.txt` suffix so pytest collection and
# `ruff check .` never treat the frozen historical copy as a live module.
_WO143B_PRE_FIX_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "wo143b_pre_fix_shadow_cohort.py.txt"


def _load_pre_fix_shadow_cohort_module() -> types.ModuleType:
    """Load shadow_cohort.py exactly as it stood immediately before WO-143b.

    Bound into the real ``polymarket_predictive_engine`` package (via
    ``__package__``) so its ``from .xxx import yyy`` relative imports resolve
    against the actual installed sibling modules, none of which this WO
    touches. Sourced from a committed fixture, so this never skips.
    """
    source = _WO143B_PRE_FIX_FIXTURE.read_text(encoding="utf-8")
    module_name = "polymarket_predictive_engine._wo143b_pre_fix_shadow_cohort"
    module = types.ModuleType(module_name)
    module.__package__ = "polymarket_predictive_engine"
    module.__file__ = str(_WO143B_PRE_FIX_FIXTURE)
    sys.modules[module_name] = module
    try:
        exec(compile(source, module.__file__, "exec"), module.__dict__)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def _shadow_lock_path(cfg: EngineConfig) -> Path:
    return runtime_lock.runtime_lock_path(cfg, "shadow_cohort")


def _write_foreign_shadow_lock(cfg: EngineConfig, *, acquired_at_utc: str, pid: int) -> Path:
    path = _shadow_lock_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "name": "shadow_cohort",
                "pid": pid,
                "process_started_at_utc": "2026-01-01T00:00:00Z",
                "acquired_at_utc": acquired_at_utc,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_wo143b_uncontended_call_is_byte_identical_to_the_pre_fix_build(tmp_path, monkeypatch):
    # WO-143b.1 F3 (test 6): the baseline is a committed fixture and this test
    # must never skip, on any machine. Assert that explicitly rather than
    # trusting that the loader no longer contains a skip -- if a future edit
    # reintroduces one, this turns a silent, passing-looking skip into a loud
    # failure.
    assert _WO143B_PRE_FIX_FIXTURE.is_file(), "committed fixture missing -- no git-object fallback exists"
    try:
        pre_fix = _load_pre_fix_shadow_cohort_module()
    except pytest.skip.Exception as exc:  # pragma: no cover - regression guard
        pytest.fail(f"WO-143b.1 F3: byte-identity regression must not skip, but skipped: {exc}")
    frozen_now = "2026-08-01T00:00:00Z"
    monkeypatch.setattr(pre_fix, "now_utc", lambda: frozen_now)
    monkeypatch.setattr(shadow_cohort_module, "now_utc", lambda: frozen_now)

    old_cfg = _cfg(tmp_path / "old")
    new_cfg = _cfg(tmp_path / "new")
    candidate = _wo119_candidate("m-wo143b", "t-wo143b")

    old_summary = pre_fix.update_shadow_cohort_evidence(old_cfg, [candidate])
    new_summary = shadow_cohort_module.update_shadow_cohort_evidence(new_cfg, [candidate])

    assert old_summary.get("status") == "computed"
    assert new_summary.get("status") == "computed"

    old_fills = (old_cfg.output_root / "polymarket_shadow" / "shadow_fills.csv").read_bytes()
    new_fills = (new_cfg.output_root / "polymarket_shadow" / "shadow_fills.csv").read_bytes()
    assert new_fills == old_fills

    old_positions = (old_cfg.output_root / "polymarket_shadow" / "shadow_positions.csv").read_bytes()
    new_positions = (new_cfg.output_root / "polymarket_shadow" / "shadow_positions.csv").read_bytes()
    assert new_positions == old_positions

    # Sanity: the fixture actually exercised a write (an empty-vs-empty diff
    # would trivially be "byte-identical" without proving anything).
    assert old_fills and old_positions


def test_wo143b_contended_shadow_lock_skips_with_no_writes(tmp_path):
    cfg = _cfg(tmp_path)
    _write_foreign_shadow_lock(
        cfg,
        acquired_at_utc=runtime_lock._PROCESS_STARTED_AT_UTC,
        pid=os.getpid() + 1,
    )

    summary = update_shadow_cohort_evidence(cfg, [_wo119_candidate("m-contend", "t-contend")])

    assert summary.get("status") == "skipped_shadow_lock_held"
    assert not (cfg.output_root / "polymarket_shadow" / "shadow_fills.csv").exists()
    assert not (cfg.output_root / "polymarket_shadow" / "shadow_positions.csv").exists()
    assert not (cfg.governance_root / "shadow_signal_cohort_pnl.json").exists()
    assert not (cfg.governance_root / "shadow_cohort_update_summary.json").exists()


def test_wo143b_lock_released_on_happy_path(tmp_path):
    cfg = _cfg(tmp_path)

    summary = update_shadow_cohort_evidence(cfg, [_wo119_candidate("m-happy", "t-happy")])

    assert summary.get("status") == "computed"
    assert not _shadow_lock_path(cfg).exists()

    # And the lock is genuinely usable again afterwards, not merely absent by
    # coincidence: a fresh acquire succeeds.
    lock = runtime_lock.acquire_runtime_lock(cfg, "shadow_cohort")
    assert lock.acquired is True
    runtime_lock.release_runtime_lock(lock)


def test_wo143b_lock_released_when_body_raises(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated shadow-cohort computation failure")

    monkeypatch.setattr(shadow_cohort_module, "_summarise_shadow", _boom)

    with pytest.raises(RuntimeError, match="simulated shadow-cohort computation failure"):
        update_shadow_cohort_evidence(cfg, [_wo119_candidate("m-raise", "t-raise")])

    assert not _shadow_lock_path(cfg).exists()


def test_wo143b_holding_prediction_cycle_lock_does_not_block_shadow_lock(tmp_path):
    cfg = _cfg(tmp_path)

    prediction_cycle_lock = runtime_lock.acquire_runtime_lock(cfg, "prediction_cycle")
    assert prediction_cycle_lock.acquired is True
    try:
        summary = update_shadow_cohort_evidence(cfg, [_wo119_candidate("m-nodeadlock", "t-nodeadlock")])
    finally:
        runtime_lock.release_runtime_lock(prediction_cycle_lock)

    assert summary.get("status") == "computed"
    rows = read_csv_rows(cfg.output_root / "polymarket_shadow" / "shadow_positions.csv")
    assert len(rows) == 1


def test_wo143b_malformed_stale_shadow_lock_is_reclaimed_not_wedged(tmp_path):
    cfg = _cfg(tmp_path)
    path = _shadow_lock_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"pid": "not-a-pid"}', encoding="utf-8")
    old = time.time() - 3600.0
    os.utime(path, (old, old))

    summary = update_shadow_cohort_evidence(cfg, [_wo119_candidate("m-reclaim", "t-reclaim")])

    assert summary.get("status") == "computed"
    rows = read_csv_rows(cfg.output_root / "polymarket_shadow" / "shadow_positions.csv")
    assert len(rows) == 1
    # The lock is released after reclaim, not left wedged for the next caller.
    assert not path.exists()


# --- WO-143b.1 F1: settlement budget, heartbeat, bounded critical section --
#
# F1 was originally dismissed as "not reachable under shipped defaults" on the
# basis of one HTTP call per position (25 x 20s = 500s against a 1800s stale
# window). That was wrong: the settlement path issues up to THREE calls per
# position (25 x 60s = 1500s, a 1.2x margin), and `urlopen(timeout=N)` is a
# per-socket timeout rather than a total deadline, so a single trickling read
# is unbounded in wall-clock terms. These tests cover the reversal.


def _settling_cfg(tmp_path: Path, **overrides) -> EngineConfig:
    cfg = _cfg(tmp_path)
    settings = cfg.raw["shadow_cohort_validation"]
    settings["settle_resolved_markets"] = True
    settings["settlement_grace_minutes"] = 0
    # Keep the mark-to-market time-exit out of these fixtures. The seeded
    # positions are deliberately old enough for settlement to be due, which
    # would otherwise also trip `maximum_holding_hours` and close them as
    # `shadow_time_exit` -- appending SELL_SHADOW fills that have nothing to do
    # with the settlement behaviour under test.
    settings["maximum_holding_hours"] = 10_000_000
    settings.update(overrides)
    return cfg


def _due_position(position_id: str, *, market_id: str) -> dict:
    """An open position whose market closed long ago, so settlement is due."""
    return {
        "shadow_position_id": position_id,
        "market_id": market_id,
        "market_slug": f"{market_id}-slug",
        "token_id": f"{market_id}-token",
        "outcome": "No",
        "category": "macro_rates",
        "signal_cohort": "structural",
        "shadow_source": "test_shadow",
        "status": "open",
        "entry_price": "0.50",
        "quantity": "20",
        "cost_basis_usdc": "10",
        "latest_mark_price": "0.50",
        "opened_at": "2026-07-01T00:00:00Z",
        "updated_at": "2026-07-01T00:00:00Z",
        "close_time": "2026-07-02T00:00:00Z",
    }


def _seed_due_positions(cfg: EngineConfig, count: int) -> list[dict]:
    rows = [_due_position(f"pos-{index}", market_id=f"m-{index}") for index in range(count)]
    write_csv(cfg.output_root / "polymarket_shadow" / "shadow_positions.csv", rows)
    return rows


def test_wo143b1_f1_settlement_budget_expiry_is_partial_and_writes_no_ledger_row(tmp_path, monkeypatch):
    # F1 test (7): a provider that blocks past the budget must leave the pass
    # PARTIAL and fail-closed -- remaining positions unprocessed and NO
    # partial settlement fill appended to the append-only ledger.
    cfg = _settling_cfg(tmp_path, settlement_budget_seconds=0.25)
    _seed_due_positions(cfg, 5)

    checked: list[str] = []

    def _slow_provider(_cfg, position, *, timeout_seconds):
        checked.append(str(position.get("shadow_position_id")))
        time.sleep(0.2)
        return None, "unresolved_stub"

    monkeypatch.setattr(shadow_cohort_module, "_settlement_price_for_position", _slow_provider)

    summary = update_shadow_cohort_evidence(cfg, [])

    assert summary["settlement_budget_expired"] is True
    assert summary["settlement_status"] == "partial_budget_expired"
    assert summary["settlement_positions_abandoned"] > 0
    # The pass stopped early: it did not touch every due position.
    assert len(checked) < 5
    assert summary["settlement_checks"] == len(checked)
    # Fail-closed: nothing settled, so no SELL_SHADOW row was appended.
    assert summary["settled_positions"] == 0
    fills_path = cfg.output_root / "polymarket_shadow" / "shadow_fills.csv"
    assert not fills_path.exists() or read_csv_rows(fills_path) == []


def test_wo143b1_f1_resolved_settlements_are_discarded_when_the_budget_expires(tmp_path, monkeypatch):
    """The no-partial-write contract, exercised with settlements that RESOLVE.

    The test above stubs every lookup as unresolved, so nothing is ever staged
    and its "no SELL_SHADOW row" assertion cannot fail. This one resolves the
    first few positions and only then blocks past the budget, which is the case
    the registered contract is actually about: "abandons remaining positions
    with a partial, fail-closed status and NO partial write" (Codex P1 wave-35).
    """
    cfg = _settling_cfg(tmp_path, settlement_budget_seconds=0.25)
    _seed_due_positions(cfg, 5)

    calls: list[str] = []

    def _resolves_then_stalls(_cfg, position, *, timeout_seconds):
        calls.append(str(position.get("shadow_position_id")))
        if len(calls) > 2:
            time.sleep(0.3)
        return 1.0, "resolved_stub"

    monkeypatch.setattr(shadow_cohort_module, "_settlement_price_for_position", _resolves_then_stalls)

    summary = update_shadow_cohort_evidence(cfg, [])

    assert summary["settlement_budget_expired"] is True
    assert summary["settlement_status"] == "partial_budget_expired"
    # BEHAVIOUR FIRST, so this fails on the partial write itself rather than on
    # a missing counter key: without the staging fix these two are 2 and one
    # SELL_SHADOW row respectively.
    assert summary["settled_positions"] == 0
    fills_path = cfg.output_root / "polymarket_shadow" / "shadow_fills.csv"
    assert not fills_path.exists() or read_csv_rows(fills_path) == []
    # Settlements WERE resolved this pass and were thrown away rather than
    # written -- the distinction the counter exists to make.
    assert summary["settlement_resolved_discarded_on_expiry"] >= 1
    # The positions stay OPEN, so the next pass can settle them properly.
    positions = read_csv_rows(cfg.output_root / "polymarket_shadow" / "shadow_positions.csv")
    assert positions, "positions must still be on disk"
    assert all(str(row.get("status")).lower() == "open" for row in positions)
    # And they DO advance in the rotation, which is the wave-40 correction to
    # this test's original claim. Holding their checkpoints back was meant to
    # keep them first in line, but it starved every later due position: a lookup
    # that repeatedly runs past the budget would be resolved and discarded
    # forever while others were never attempted. Advancing defers the discarded
    # position by one rotation -- settlement is idempotent and the position
    # stays open -- rather than starving it. The checkpoint is a SCHEDULING
    # record, not a publication record; the fail-closed guarantee is the
    # unwritten ledger asserted above, not a withheld checkpoint.
    checkpoints = read_json(
        cfg.governance_root / "shadow_settlement_checkpoints.json"
    ) or {}
    recorded = set((checkpoints.get("last_settlement_check_utc") or {}))
    assert set(calls) <= recorded, (
        "every position checked this pass must advance in the rotation, or a "
        "repeatedly-slow lookup starves everything behind it"
    )


def test_wo143b1_f1_abandoned_positions_are_not_closed_at_a_stale_mark(tmp_path, monkeypatch):
    """The rotation only helps ACROSS passes; this is the within-pass hole.

    An abandoned position already past maximum_holding_hours was closed as
    shadow_time_exit at its stale mark immediately after the settlement pass
    gave up on it -- before the oldest-first rotation could ever prioritise it,
    and recreating exactly the P&L distortion _settlement_check_sort_key's
    docstring says that rotation exists to prevent (Codex P1 wave-35).

    Note the holding limit: the existing rotation test sets it to ten million
    hours, which is what hid this. Here it is deliberately SHORT, so every due
    position is also past it.
    """
    cfg = _settling_cfg(
        tmp_path,
        settlement_budget_seconds=0.25,
        # The seeded positions opened 2026-07-01 and are far past this.
        maximum_holding_hours=1,
    )
    _seed_due_positions(cfg, 5)

    calls: list[str] = []

    def _stalls(_cfg, position, *, timeout_seconds):
        calls.append(str(position.get("shadow_position_id")))
        time.sleep(0.2)
        return None, "unresolved_stub"

    monkeypatch.setattr(shadow_cohort_module, "_settlement_price_for_position", _stalls)

    summary = update_shadow_cohort_evidence(cfg, [])

    assert summary["settlement_budget_expired"] is True

    rows = {
        str(row.get("shadow_position_id")): row
        for row in read_csv_rows(cfg.output_root / "polymarket_shadow" / "shadow_positions.csv")
    }
    # Derived from what the provider was ASKED, not from the new summary field,
    # so this assertion fails on the behaviour rather than on a missing key.
    abandoned = set(rows) - set(calls)
    assert abandoned, "the budget must have stopped the pass short of every position"
    for position_id in abandoned:
        row = rows[position_id]
        assert str(row.get("status")).lower() == "open", (
            f"{position_id} was abandoned by settlement and must stay open for the next pass"
        )
        assert str(row.get("close_reason") or "") != "shadow_time_exit"
    # No SELL_SHADOW fill was manufactured for them at a stale mark.
    fills_path = cfg.output_root / "polymarket_shadow" / "shadow_fills.csv"
    fill_ids = {str(row.get("shadow_position_id")) for row in read_csv_rows(fills_path)} if fills_path.exists() else set()
    assert not (fill_ids & abandoned)
    # And the pass NAMES them, so the caller can hold them rather than
    # re-deriving the set from what it happened to observe.
    assert set(summary["settlement_abandoned_ids"]) == abandoned


def test_wo143b1_f1_reclaim_does_not_delete_a_heartbeat_written_mid_reclaim(tmp_path):
    """The reclaim decision must be re-verified against the file it deletes.

    The decision is made from a payload read before the unlink, and a live
    holder can heartbeat in between. A plain unlink then deletes a FRESH
    heartbeat while the holder's os.replace reports success -- so the contender
    acquires, the holder believes it still holds, and both publish the shadow
    ledgers concurrently (Codex P1 wave-35).

    The interleaving is forced deterministically by beating from inside the
    read that the reclaim decision is made from.
    """
    from polymarket_predictive_engine import runtime_lock as runtime_lock_module

    cfg = _settling_cfg(tmp_path)
    held = runtime_lock_module.acquire_runtime_lock(cfg, "shadow_cohort", stale_after_seconds=0.01)
    assert held.acquired
    path = held.path

    time.sleep(0.05)  # the lock is now past its stale window

    real_read_json = runtime_lock_module.read_json
    beaten = {"done": False}

    def _beat_during_the_decision(target, *args, **kwargs):
        payload = real_read_json(target, *args, **kwargs)
        if not beaten["done"] and Path(target) == path:
            beaten["done"] = True
            # The holder makes progress and re-stamps AFTER the contender has
            # read the stale payload but BEFORE it acts on that decision.
            fresh = dict(held.payload)
            fresh["heartbeat_at_utc"] = now_utc()
            fresh["heartbeat_count"] = 1
            runtime_lock_module._rewrite_lock_payload(path, fresh)
        return payload

    runtime_lock_module.read_json = _beat_during_the_decision
    try:
        contender = runtime_lock_module.acquire_runtime_lock(
            cfg, "shadow_cohort", stale_after_seconds=0.01
        )
    finally:
        runtime_lock_module.read_json = real_read_json

    assert beaten["done"], "the test must actually have forced the interleaving"
    assert contender.acquired is False, (
        "a holder that heartbeated mid-reclaim still holds the lock"
    )
    # The holder's fresh heartbeat survives on disk -- it was not deleted.
    surviving = real_read_json(path, default={}) or {}
    assert surviving.get("heartbeat_count") == 1
    # No reclaim temp file is left behind.
    assert not list(path.parent.glob(f".{path.name}.*.reclaim"))


def test_wo143b1_f1_the_position_cap_cannot_mask_a_budget_expiry(tmp_path, monkeypatch):
    """The cap must not be reached before the budget is checked (wave-36).

    With more due positions than settlement_max_positions_per_cycle, a final
    permitted lookup that pushed elapsed time past the budget was met on the
    next iteration by the CAP branch instead. budget_expired stayed false, the
    staged settlements published, and the pass reported "complete" after
    exceeding its own fail-closed budget -- bypassing the staging rollback
    entirely rather than defeating it.
    """
    cfg = _settling_cfg(
        tmp_path,
        settlement_budget_seconds=0.25,
        # Fewer permitted checks than due positions, so the cap is reachable.
        settlement_max_positions_per_cycle=3,
    )
    _seed_due_positions(cfg, 6)

    def _resolves_slowly(_cfg, position, *, timeout_seconds):
        time.sleep(0.12)
        return 1.0, "resolved_stub"

    monkeypatch.setattr(shadow_cohort_module, "_settlement_price_for_position", _resolves_slowly)

    summary = update_shadow_cohort_evidence(cfg, [])

    # Three lookups at 0.12s each exceed the 0.25s budget, so the pass MUST
    # report expiry rather than a clean cap-limited completion.
    assert summary["settlement_budget_expired"] is True
    assert summary["settlement_status"] == "partial_budget_expired"
    # And the fail-closed contract holds: nothing written.
    assert summary["settled_positions"] == 0
    fills_path = cfg.output_root / "polymarket_shadow" / "shadow_fills.csv"
    assert not fills_path.exists() or read_csv_rows(fills_path) == []


def test_wo143b1_f1_a_future_heartbeat_does_not_wedge_the_lane(tmp_path):
    """A future heartbeat is malformed evidence, not fresh evidence (wave-36).

    A parseable future heartbeat_at_utc was selected as the lock's age basis
    and its negative age clamped to zero, and _valid_lock_payload performs no
    heartbeat sanity check -- so even a DEAD owner stayed non-stale until that
    date arrived and every shadow update was skipped until then.
    """
    from polymarket_predictive_engine import runtime_lock as runtime_lock_module

    cfg = _settling_cfg(tmp_path)
    held = runtime_lock_module.acquire_runtime_lock(cfg, "shadow_cohort", stale_after_seconds=60)
    assert held.acquired
    # The owner is gone; only its lock file remains, long past its stale window
    # and carrying a heartbeat from a corrupted clock. Ages are set explicitly
    # rather than by sleeping: now_utc() has SECOND resolution, so a sub-second
    # sleep measures an age of exactly 0.0 and the test would pass vacuously.
    wedged = dict(held.payload)
    # A FOREIGN pid, deliberately. This test runs in the process that took the
    # lock, and an own-pid lock older than the process start is reclaimed by
    # `_same_pid_lock_predates_current_process` regardless of any heartbeat --
    # which made the first version of this test pass without the fix, on a path
    # it was not trying to exercise at all.
    wedged["pid"] = 999_999
    wedged["acquired_at_utc"] = (
        datetime.now(timezone.utc) - timedelta(hours=1)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    wedged["heartbeat_at_utc"] = "2099-01-01T00:00:00Z"
    runtime_lock_module._rewrite_lock_payload(held.path, wedged)

    contender = runtime_lock_module.acquire_runtime_lock(
        cfg, "shadow_cohort", stale_after_seconds=60
    )

    assert contender.acquired is True, (
        "a far-future heartbeat must degrade to the ordinary stale timeout, "
        "not hold the lane until 2099"
    )


def test_wo143b1_f1_the_heartbeat_covers_the_trailing_writes(tmp_path, monkeypatch):
    """Progress continues past the ledgers, through the three trailing writes.

    fills_published was the final notification in the function while three
    writes followed it, so a cumulatively slow tail let the lock age out while
    this writer was still advancing -- and a contender that reclaimed could be
    overwritten by the original writer's older snapshot (Codex P2 wave-36).
    """
    cfg = _settling_cfg(tmp_path)
    _seed_due_positions(cfg, 1)

    labels: list[str] = []
    from polymarket_predictive_engine import runtime_lock as runtime_lock_module

    real = runtime_lock_module.RuntimeLockHeartbeat.record_progress

    def _capture(self, label=""):
        labels.append(str(label))
        return real(self, label)

    monkeypatch.setattr(
        runtime_lock_module.RuntimeLockHeartbeat, "record_progress", _capture
    )

    update_shadow_cohort_evidence(cfg, [])

    for expected in ("summary_published", "pnl_history_published", "update_summary_published"):
        assert expected in labels, f"no progress notification for {expected}: {labels}"


def test_wo143b1_f1_the_critical_section_holds_only_the_ledger_replaces(tmp_path, monkeypatch):
    """No heartbeat I/O between the two ledger replaces (Codex P1 wave-38).

    `_progress` reaches note_progress, which creates, writes and FSYNCS a temp
    file before replacing the lock payload. Beating between the replaces put
    that inside the one section meant to be two atomic renames, so a stuck
    filesystem could leave positions published without their matching
    append-only fill.
    """
    from polymarket_predictive_engine import runtime_lock as runtime_lock_module

    cfg = _settling_cfg(tmp_path)
    _seed_due_positions(cfg, 1)

    inside: list[str] = []
    depth = {"n": 0}

    real_section = runtime_lock_module.RuntimeLockHeartbeat.critical_section
    real_beat = runtime_lock_module.RuntimeLockHeartbeat.record_progress

    def _tracked_section(self):
        import contextlib

        @contextlib.contextmanager
        def _wrapper():
            depth["n"] += 1
            try:
                with real_section(self):
                    yield
            finally:
                depth["n"] -= 1

        return _wrapper()

    def _tracked_beat(self, label=""):
        if depth["n"] > 0:
            inside.append(str(label))
        return real_beat(self, label)

    monkeypatch.setattr(runtime_lock_module.RuntimeLockHeartbeat, "critical_section", _tracked_section)
    monkeypatch.setattr(runtime_lock_module.RuntimeLockHeartbeat, "record_progress", _tracked_beat)

    update_shadow_cohort_evidence(cfg, [])

    assert inside == [], f"heartbeat I/O ran inside the ledger critical section: {inside}"


def test_wo143b1_f1_the_abandoned_count_matches_the_abandoned_ids(tmp_path, monkeypatch):
    """The count feeds the registered day-after check; the list must match it.

    Discarded staged settlements were added to settlement_abandoned_ids without
    recomputing settlement_positions_abandoned, so the artifact could report
    fewer abandoned positions than it named (Codex P2 wave-38).
    """
    cfg = _settling_cfg(tmp_path, settlement_budget_seconds=0.25)
    _seed_due_positions(cfg, 6)

    calls: list[str] = []

    def _resolves_then_stalls(_cfg, position, *, timeout_seconds):
        calls.append(str(position.get("shadow_position_id")))
        if len(calls) > 2:
            time.sleep(0.3)
        return 1.0, "resolved_stub"

    monkeypatch.setattr(shadow_cohort_module, "_settlement_price_for_position", _resolves_then_stalls)

    summary = update_shadow_cohort_evidence(cfg, [])

    assert summary["settlement_budget_expired"] is True
    assert summary["settlement_resolved_discarded_on_expiry"] >= 1
    assert summary["settlement_positions_abandoned"] == len(summary["settlement_abandoned_ids"]), (
        "the day-after check reads the count, so it must not understate the list"
    )


def test_wo143b1_f1_lock_is_released_after_a_budget_expired_pass(tmp_path, monkeypatch):
    # F1 test (8): a budget expiry is a normal, bounded outcome -- it must not
    # leave the shadow_cohort lock held.
    cfg = _settling_cfg(tmp_path, settlement_budget_seconds=0.2)
    _seed_due_positions(cfg, 4)

    def _slow_provider(_cfg, _position, *, timeout_seconds):
        time.sleep(0.15)
        return None, "unresolved_stub"

    monkeypatch.setattr(shadow_cohort_module, "_settlement_price_for_position", _slow_provider)

    summary = update_shadow_cohort_evidence(cfg, [])

    assert summary["settlement_budget_expired"] is True
    assert not _shadow_lock_path(cfg).exists()
    regained = runtime_lock.acquire_runtime_lock(cfg, "shadow_cohort")
    assert regained.acquired is True
    runtime_lock.release_runtime_lock(regained)


def test_wo143b1_f1_progressing_worker_is_heartbeaten_and_resists_reclaim(tmp_path):
    # F1 test (9): a live, PROGRESSING holder must not be judged stale. The
    # heartbeat refreshes the payload, and `_lock_age_seconds` measures from
    # max(acquired_at_utc, heartbeat_at_utc), so a concurrent reclaim attempt
    # with a short stale window does not acquire.
    cfg = _cfg(tmp_path)

    # First: the real heartbeat object genuinely stamps the payload.
    lock = runtime_lock.acquire_runtime_lock(cfg, "shadow_cohort")
    assert lock.acquired is True
    heartbeat = runtime_lock.RuntimeLockHeartbeat(
        lock, cap_seconds=1800.0, critical_section_max_seconds=120.0
    )
    try:
        heartbeat.note_progress("working")
        assert heartbeat.beats == 1
        beaten = read_json(lock.path, default={}) or {}
        assert beaten["heartbeat_count"] == 1
        assert beaten["heartbeat_at_utc"]
        assert beaten["last_progress_at_utc"]
        # acquired_at_utc is NEVER re-stamped: other readers rely on it
        # meaning "when this lock was taken".
        assert beaten["acquired_at_utc"] == lock.payload["acquired_at_utc"]
    finally:
        runtime_lock.release_runtime_lock(lock)

    # Then: a FOREIGN holder whose acquisition is ancient but whose heartbeat
    # is fresh must not be reclaimed. A foreign pid is used deliberately --
    # a same-pid lock predating this process is reclaimed by a separate,
    # unrelated rule, which would mask what this test is checking.
    path = _shadow_lock_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "name": "shadow_cohort",
                "pid": os.getpid() + 1,
                "process_started_at_utc": "2020-01-01T00:00:00Z",
                "acquired_at_utc": "2020-01-01T00:00:00Z",
                "heartbeat_at_utc": now_utc(),
                "heartbeat_count": 7,
            }
        ),
        encoding="utf-8",
    )

    contender = runtime_lock.acquire_runtime_lock(cfg, "shadow_cohort", stale_after_seconds=60.0)
    assert contender.acquired is False, "a heartbeaten live holder was reclaimed"


def test_wo143b1_f1_hung_worker_stops_being_heartbeaten_and_becomes_reclaimable(tmp_path):
    # F1 test (9b) -- the mirror of (9), and the test that distinguishes this
    # design from a permanent wedge. A holder blocked inside a single unbounded
    # read stops advancing its progress counter, so no further beats are
    # written, so the lock ages normally and reclaim works on schedule.
    cfg = _cfg(tmp_path)
    lock = runtime_lock.acquire_runtime_lock(cfg, "shadow_cohort")
    assert lock.acquired is True
    heartbeat = runtime_lock.RuntimeLockHeartbeat(
        lock, cap_seconds=1800.0, critical_section_max_seconds=120.0
    )
    try:
        heartbeat.note_progress("last_progress_before_hanging")
        assert heartbeat.beats == 1
        beaten_at = (read_json(lock.path, default={}) or {}).get("heartbeat_at_utc")

        # The worker now hangs inside a single unbounded read: the progress
        # counter stops advancing. However often the beat is sampled -- as a
        # timer would -- it must write nothing. This is the whole point: a
        # timer-driven beat would keep stamping here and wedge the lane forever.
        for _ in range(5):
            assert heartbeat.maybe_beat() is False
        assert heartbeat.beats == 1, "a hung worker must not keep beating"
        assert (read_json(lock.path, default={}) or {}).get("heartbeat_at_utc") == beaten_at
    finally:
        runtime_lock.release_runtime_lock(lock)

    # With the beat stopped, the payload simply ages -- and a foreign holder
    # whose last beat is older than the window is reclaimed on schedule.
    path = _shadow_lock_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "name": "shadow_cohort",
                "pid": os.getpid() + 1,
                "process_started_at_utc": "2020-01-01T00:00:00Z",
                "acquired_at_utc": "2020-01-01T00:00:00Z",
                "heartbeat_at_utc": "2020-01-01T00:05:00Z",
                "heartbeat_count": 3,
            }
        ),
        encoding="utf-8",
    )

    contender = runtime_lock.acquire_runtime_lock(cfg, "shadow_cohort", stale_after_seconds=60.0)
    assert contender.acquired is True, "a hung holder must become reclaimable"
    assert contender.stale_lock_replaced is True
    runtime_lock.release_runtime_lock(contender)


def test_wo143b1_f1_heartbeat_never_unlinks_the_lock_file(tmp_path, monkeypatch):
    # F1 test (9c): unlink-then-recreate is explicitly forbidden -- it opens a
    # window in which another process can legitimately acquire the lock. The
    # heartbeat must publish via temp-file + os.replace only.
    cfg = _cfg(tmp_path)
    lock = runtime_lock.acquire_runtime_lock(cfg, "shadow_cohort")
    assert lock.acquired is True
    heartbeat = runtime_lock.RuntimeLockHeartbeat(
        lock, cap_seconds=1800.0, critical_section_max_seconds=120.0
    )

    unlinked: list[str] = []
    real_unlink = os.unlink

    def _tracking_unlink(target, *args, **kwargs):
        unlinked.append(str(target))
        return real_unlink(target, *args, **kwargs)

    monkeypatch.setattr(runtime_lock.os, "unlink", _tracking_unlink)
    try:
        for index in range(4):
            heartbeat.note_progress(f"step-{index}")
            # The lock path is present at every observable point.
            assert lock.path.exists(), "the heartbeat left a window with no lock file"
        assert heartbeat.beats == 4
        assert str(lock.path) not in unlinked, "the heartbeat unlinked the lock file"
    finally:
        monkeypatch.undo()
        runtime_lock.release_runtime_lock(lock)


def test_wo143b1_f1_timing_validator_rejects_every_broken_ordering_relation(tmp_path):
    # F1 test (10): the load-time validator must reject a configuration
    # violating the FULL registered ordering -- all four relations -- rather
    # than silently inverting.
    base = _cfg(tmp_path)
    # The registered values satisfy every relation.
    timings = shadow_cohort_module.shadow_cohort_timings(base)
    assert timings["settlement_budget_seconds"] == 900
    assert timings["remainder_budget_seconds"] == 300
    assert timings["heartbeat_margin_seconds"] == 120
    assert timings["heartbeat_cap_seconds"] == 1800
    assert timings["critical_section_max_seconds"] == 120
    assert timings["shadow_cohort_stale_after_seconds"] == 2400
    # Relation 1: 900 + 300 + 120 = 1320 < 1800.
    assert (
        timings["settlement_budget_seconds"]
        + timings["remainder_budget_seconds"]
        + timings["heartbeat_margin_seconds"]
        < timings["heartbeat_cap_seconds"]
    )
    # Relation 3: 1800 + 120 = 1920 < 2400.
    assert (
        timings["heartbeat_cap_seconds"] + timings["critical_section_max_seconds"]
        < timings["shadow_cohort_stale_after_seconds"]
    )

    def _reject(**overrides):
        cfg = _cfg(tmp_path)
        cfg.raw["shadow_cohort_validation"].update(overrides)
        with pytest.raises(shadow_cohort_module.ShadowCohortTimingConfigError):
            shadow_cohort_module.shadow_cohort_timings(cfg)

    # Relation 1 broken: the phase budgets no longer fit under the cap, so a
    # legitimately slow pass would be cut off mid-flight.
    _reject(settlement_budget_seconds=1500)
    # Relation 2 broken: the cap sits at/above the stale window, so the beat
    # could still be running when another writer reclaims.
    _reject(heartbeat_cap_seconds=2400)
    # Relation 3 broken -- the one a three-relation test would miss. Every
    # other relation still holds here: 1320 < 1800 and 1800 < 2400, but the
    # effective maximum hold (1800 + 700 = 2500) exceeds the 2400s window and
    # re-opens the reclaim-mid-critical-section hole.
    _reject(critical_section_max_seconds=700)
    # Relation 4: positive and finite.
    _reject(heartbeat_margin_seconds=0)
    _reject(settlement_budget_seconds=-1)
    _reject(critical_section_max_seconds=float("inf"))
    _reject(heartbeat_cap_seconds=float("nan"))


def test_wo143b1_f1_heartbeat_cap_is_conditional_on_the_critical_section(tmp_path):
    # F1 test (10b): past heartbeat_cap_seconds the beat normally stops, but
    # NOT while inside the ledger-write critical section -- cutting it off
    # mid-publish would invite the very reclaim-under-a-writer corruption the
    # section exists to prevent. The carve-out is itself bounded, and an
    # overrun is recorded loudly.
    cfg = _cfg(tmp_path)
    lock = runtime_lock.acquire_runtime_lock(cfg, "shadow_cohort")
    assert lock.acquired is True
    try:
        # cap_seconds=0 => the cap has already elapsed.
        beyond_cap = runtime_lock.RuntimeLockHeartbeat(
            lock, cap_seconds=0.0, critical_section_max_seconds=60.0
        )
        beyond_cap.note_progress("outside")
        assert beyond_cap.beats == 0, "the cap must stop the beat outside the critical section"

        with beyond_cap.critical_section():
            beyond_cap.note_progress("inside")
            assert beyond_cap.beats == 1, "the cap must NOT fire inside the critical section"
        assert beyond_cap.critical_section_overran is False

        # A critical section that overruns its own bound stops beating, and
        # says so, rather than wedging the lane indefinitely.
        overrunning = runtime_lock.RuntimeLockHeartbeat(
            lock, cap_seconds=0.0, critical_section_max_seconds=0.0
        )
        with overrunning.critical_section():
            time.sleep(0.01)
            overrunning.note_progress("inside_overrun")
        assert overrunning.beats == 0
        assert overrunning.critical_section_overran is True
    finally:
        runtime_lock.release_runtime_lock(lock)


def test_wo143b1_f1_ledger_content_is_built_outside_the_critical_section(tmp_path, monkeypatch):
    # F1 test (10c): the section must contain only the atomic os.replace
    # publishes. Rendering CSV content inside it is what made a stalled volume
    # able to wedge the lane, so assert the ordering structurally.
    cfg = _cfg(tmp_path)
    events: list[str] = []

    real_write_csv = shadow_cohort_module.write_csv
    real_append = shadow_cohort_module.append_csv_rows_matching_existing_header
    real_critical_section = runtime_lock.RuntimeLockHeartbeat.critical_section

    def _tracked_write_csv(path, rows, *args, **kwargs):
        if "shadow_positions" in str(path):
            events.append("render_positions")
        return real_write_csv(path, rows, *args, **kwargs)

    def _tracked_append(path, rows, *args, **kwargs):
        events.append("render_fills")
        return real_append(path, rows, *args, **kwargs)

    @contextlib.contextmanager
    def _tracked_critical_section(self):
        events.append("critical_section_enter")
        with real_critical_section(self):
            yield self
        events.append("critical_section_exit")

    monkeypatch.setattr(shadow_cohort_module, "write_csv", _tracked_write_csv)
    monkeypatch.setattr(shadow_cohort_module, "append_csv_rows_matching_existing_header", _tracked_append)
    monkeypatch.setattr(runtime_lock.RuntimeLockHeartbeat, "critical_section", _tracked_critical_section)

    summary = update_shadow_cohort_evidence(cfg, [_wo119_candidate("m-cs", "t-cs")])
    assert summary["status"] == "computed"

    assert "critical_section_enter" in events
    enter = events.index("critical_section_enter")
    exit_ = events.index("critical_section_exit")
    # Every content-rendering step happened BEFORE the section opened.
    assert "render_positions" in events
    assert "render_fills" in events
    for rendered in ("render_positions", "render_fills"):
        assert events.index(rendered) < enter, f"{rendered} ran inside the critical section"
    # And nothing rendered between enter and exit.
    assert not [event for event in events[enter + 1 : exit_] if event.startswith("render_")]


def test_wo143b1_f1_settlement_rotates_instead_of_starving_the_tail(tmp_path, monkeypatch):
    # F1 test (10d): with a budget that reaches only the first N of M due
    # positions, the NEXT pass must start with the previously unreached ones.
    # Without rotation the tail is never reached on any pass and those
    # positions close as shadow_time_exit at a stale mark instead of settling
    # at the true 0/1 outcome -- a systematic distortion of shadow P&L.
    cfg = _settling_cfg(tmp_path, settlement_max_positions_per_cycle=2)
    _seed_due_positions(cfg, 6)

    checked: list[str] = []

    def _recording_provider(_cfg, position, *, timeout_seconds):
        checked.append(str(position.get("shadow_position_id")))
        return None, "unresolved_stub"

    monkeypatch.setattr(shadow_cohort_module, "_settlement_price_for_position", _recording_provider)

    first = update_shadow_cohort_evidence(cfg, [])
    first_pass = list(checked)
    assert len(first_pass) == 2
    assert first["settlement_positions_abandoned"] == 4

    checked.clear()
    second = update_shadow_cohort_evidence(cfg, [])
    second_pass = list(checked)
    assert len(second_pass) == 2
    assert second["settlement_positions_abandoned"] == 4

    # The second pass reached positions the first never touched.
    assert not set(first_pass) & set(second_pass), (
        f"settlement re-checked the same positions and starved the tail: {first_pass} vs {second_pass}"
    )

    checked.clear()
    update_shadow_cohort_evidence(cfg, [])
    third_pass = list(checked)
    assert not (set(first_pass) | set(second_pass)) & set(third_pass)
    # After three passes every due position has been reached exactly once.
    assert len(set(first_pass) | set(second_pass) | set(third_pass)) == 6


def test_wo143b1_f1_settlement_pass_within_budget_is_byte_identical_to_the_pre_fix_build(tmp_path, monkeypatch):
    # F1 test (11): a settlement pass that finishes inside its budget must
    # produce exactly today's bytes. This is the guard that the budget,
    # heartbeat, staged-write and rotation work changed only WHEN the function
    # writes, never WHAT it writes.
    pre_fix = _load_pre_fix_shadow_cohort_module()
    frozen_now = "2026-08-01T00:00:00Z"
    monkeypatch.setattr(pre_fix, "now_utc", lambda: frozen_now)
    monkeypatch.setattr(shadow_cohort_module, "now_utc", lambda: frozen_now)

    def _settled_provider(_cfg, _position, *, timeout_seconds):
        return 1.0, "clean_settlement"

    monkeypatch.setattr(pre_fix, "_settlement_price_for_position", _settled_provider)
    monkeypatch.setattr(shadow_cohort_module, "_settlement_price_for_position", _settled_provider)

    old_cfg = _settling_cfg(tmp_path / "old")
    new_cfg = _settling_cfg(tmp_path / "new")
    _seed_due_positions(old_cfg, 3)
    _seed_due_positions(new_cfg, 3)

    old_summary = pre_fix.update_shadow_cohort_evidence(old_cfg, [])
    new_summary = shadow_cohort_module.update_shadow_cohort_evidence(new_cfg, [])

    assert old_summary.get("status") == "computed"
    assert new_summary.get("status") == "computed"
    # The pass really did settle inside its budget.
    assert new_summary["settled_positions"] == 3
    assert new_summary["settlement_budget_expired"] is False

    for ledger in ("shadow_fills.csv", "shadow_positions.csv"):
        old_bytes = (old_cfg.output_root / "polymarket_shadow" / ledger).read_bytes()
        new_bytes = (new_cfg.output_root / "polymarket_shadow" / ledger).read_bytes()
        assert new_bytes == old_bytes, f"{ledger} drifted from the pre-fix build"
        assert old_bytes, f"{ledger} was empty, so byte-identity proves nothing"


def test_wo143b1_budget_overrun_on_the_last_lookup_still_discards_the_batch(tmp_path, monkeypatch):
    """The loop can end without a next iteration to check (Codex P1 wave-42).

    The budget test runs at the TOP of each pass through the loop, so an
    overrun is noticed by the FOLLOWING iteration -- and the last or only due
    position has no following one. A lookup that started inside the budget and
    returned outside it left `budget_expired` false and the staged batch was
    applied and published, against the registered no-partial-write contract.

    ONE due position, so the very first lookup is also the last: the natural
    exhaustion path with nothing else to mask it.
    """
    cfg = _settling_cfg(tmp_path, settlement_budget_seconds=0.25)
    _seed_due_positions(cfg, 1)

    def _slow_but_resolving_provider(_cfg, _position, *, timeout_seconds):
        # Starts inside the budget, returns outside it -- and RESOLVES, so
        # there is a staged settlement to be wrongly applied.
        time.sleep(0.4)
        return 1.0, "clean_settlement"

    monkeypatch.setattr(
        shadow_cohort_module, "_settlement_price_for_position", _slow_but_resolving_provider
    )

    summary = update_shadow_cohort_evidence(cfg, [])

    assert summary["settlement_budget_expired"] is True, (
        "a lookup that returned after the deadline expired the budget, whether or "
        "not another iteration followed it"
    )
    assert summary["settlement_status"] == "partial_budget_expired"
    assert summary["settled_positions"] == 0
    assert summary["settlement_resolved_discarded_on_expiry"] == 1
    # The discarded settlement is deferred to the next pass, by name and by count.
    assert summary["settlement_abandoned_ids"] == ["pos-0"]
    assert summary["settlement_positions_abandoned"] == 1
    fills_path = cfg.output_root / "polymarket_shadow" / "shadow_fills.csv"
    assert not fills_path.exists() or read_csv_rows(fills_path) == []
    position = read_csv_rows(cfg.output_root / "polymarket_shadow" / "shadow_positions.csv")[0]
    assert position["status"] == "open", "the discarded settlement must not close the position"


def test_wo143b1_the_per_pass_deferral_marker_is_cleared_on_the_next_pass(tmp_path, monkeypatch):
    """`deferred_to_next_settlement_pass` is per-pass; the column is persistent.

    Codex P2 wave-42: nothing ever took the marker back off, so a position
    abandoned once carried it for the rest of its life -- reading "still
    waiting on settlement" through every later pass that checked it normally.
    """
    cfg = _settling_cfg(tmp_path, settlement_budget_seconds=0.25)
    _seed_due_positions(cfg, 5)

    def _slow_provider(_cfg, _position, *, timeout_seconds):
        time.sleep(0.2)
        return None, "unresolved_stub"

    monkeypatch.setattr(shadow_cohort_module, "_settlement_price_for_position", _slow_provider)
    first = update_shadow_cohort_evidence(cfg, [])
    deferred = set(first["settlement_abandoned_ids"])
    assert deferred, "the fixture must actually defer something"
    marked = {
        row["shadow_position_id"]: row
        for row in read_csv_rows(cfg.output_root / "polymarket_shadow" / "shadow_positions.csv")
    }
    assert all(
        marked[position_id]["settlement_policy"] == "deferred_to_next_settlement_pass"
        for position_id in deferred
    )

    # A pass that settles nothing and defers nothing: every position is checked
    # normally, so no marker should survive it.
    def _fast_unresolved_provider(_cfg, _position, *, timeout_seconds):
        return None, "unresolved_stub"

    monkeypatch.setattr(
        shadow_cohort_module, "_settlement_price_for_position", _fast_unresolved_provider
    )
    second = update_shadow_cohort_evidence(cfg, [])
    assert second["settlement_budget_expired"] is False
    assert second["settlement_abandoned_ids"] == []
    after = {
        row["shadow_position_id"]: row
        for row in read_csv_rows(cfg.output_root / "polymarket_shadow" / "shadow_positions.csv")
    }
    assert all(
        after[position_id]["settlement_policy"] == "mark_exit_allowed"
        for position_id in deferred
    ), "the deferral marker outlived the pass that deferred it"


def test_wo143b1_lock_heartbeat_diagnostics_cover_the_trailing_writes(tmp_path, monkeypatch):
    """The snapshot must be taken AFTER the trailing writes (Codex P2 wave-42).

    The wave-36 fix continued the heartbeat through the three trailing writes;
    the diagnostics were still captured once, before any of them. The lock is
    deleted on return, so that snapshot is the ONLY durable record -- and a
    heartbeat failure while publishing the summary or the history was therefore
    reported as never having happened, by the very artifact added to report it.

    The failure is INDUCED rather than counted around: beats are broken from
    the moment the history write begins, so every diagnostic that claims zero
    write failures afterwards is claiming something false.
    """
    cfg = _settling_cfg(tmp_path)
    _seed_due_positions(cfg, 1)
    monkeypatch.setattr(
        shadow_cohort_module,
        "_settlement_price_for_position",
        lambda _cfg, _position, *, timeout_seconds: (1.0, "clean_settlement"),
    )

    state = {"break_beats": False}
    original_rewrite = runtime_lock._rewrite_lock_payload
    original_history = shadow_cohort_module._write_shadow_pnl_history

    def _breaking_rewrite(path, payload):
        if state["break_beats"]:
            raise OSError("simulated heartbeat write failure during the trailing writes")
        return original_rewrite(path, payload)

    def _history(cfg_arg, summary_arg):
        state["break_beats"] = True
        return original_history(cfg_arg, summary_arg)

    monkeypatch.setattr(runtime_lock, "_rewrite_lock_payload", _breaking_rewrite)
    monkeypatch.setattr(shadow_cohort_module, "_write_shadow_pnl_history", _history)

    summary = update_shadow_cohort_evidence(cfg, [])
    published = read_json(cfg.governance_root / "shadow_cohort_update_summary.json")

    assert summary["shadow_lock_heartbeat"]["heartbeat_write_failures"] >= 1, (
        "the returned summary reports no heartbeat failure, but every beat after the "
        "history write failed"
    )
    assert published["shadow_lock_heartbeat"]["heartbeat_write_failures"] >= 1, (
        "the durable diagnostic is the only record once the lock is deleted, and it "
        "reports the trailing-write failures as never having happened"
    )
