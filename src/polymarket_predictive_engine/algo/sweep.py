"""Algo parameter sweep: hunt for microstructure edge through the replay path.

The sweep grids a registered strategy's parameters over recorded websocket
history, split chronologically: parameters are ranked on the TRAIN window only,
and the single train-selected combination is then scored once on the held-out
VALIDATION window. This is the standard quant loop (select on train, confirm
out-of-sample) applied to executable intent-level simulation — entry at the
book, marks to bid — rather than midpoint fantasy.

Fail-closed by construction:

- not enough events, or no combination reaches the minimum train fill count ->
  no candidate, decision explains why;
- the selected combination must show positive mark-to-bid P&L with enough
  fills on BOTH windows before it is even called a validated *shadow* candidate;
- a validated sweep candidate is a research lead for more forward collection.
  It never promotes, sizes, or trades: governance and forward shadow evidence
  still own that path entirely.
"""

from __future__ import annotations

import json
from itertools import product
from pathlib import Path
from typing import Any

from ..config import EngineConfig, load_config
from ..utils import git_commit_hash, now_utc, safe_float, write_csv, write_json
from .replay import iter_quote_events, run_replay

COMBO_FIELDS = [
    "strategy",
    "params",
    "tight_spread_maximum",
    "minimum_book_imbalance",
    "train_fills",
    "train_pnl_usdc",
    "train_cost_usdc",
    "validation_fills",
    "validation_pnl_usdc",
    "validation_cost_usdc",
    "train_candidate",
    "selected",
    "status",
]

DECISION_INSUFFICIENT_EVENTS = "insufficient_events_for_sweep"
DECISION_NO_CANDIDATE = "no_sweep_candidate_reached_minimum_train_fills"
DECISION_FAILED_VALIDATION = "sweep_candidate_failed_out_of_sample_validation"
DECISION_VALIDATED = "sweep_candidate_validated_shadow_only"


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    return cfg.raw.get("algo_sweep", {}) or {}


def _grid(settings: dict[str, Any]) -> list[tuple[float, float]]:
    spreads = settings.get("tight_spread_maximums") or [0.01, 0.02, 0.03]
    imbalances = settings.get("minimum_book_imbalances") or [0.55, 0.65, 0.75]
    return [(float(s), float(i)) for s, i in product(spreads, imbalances)]


def _grid_values(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple)):
        return list(value) or [None]
    return [value]


def _params_json(params: dict[str, Any]) -> str:
    return json.dumps(params, sort_keys=True, separators=(",", ":"))


def _configured_strategy_grids(settings: dict[str, Any], fallback_strategy: str) -> list[tuple[str, dict[str, Any]]]:
    strategies = settings.get("strategies")
    if isinstance(strategies, dict) and strategies:
        combos: list[tuple[str, dict[str, Any]]] = []
        for strategy, raw_grid in strategies.items():
            strategy_name = str(strategy or "").strip()
            if not strategy_name:
                continue
            grid = raw_grid if isinstance(raw_grid, dict) else {}
            keys = [str(key) for key in grid.keys()]
            values = [_grid_values(grid[key]) for key in keys]
            if not keys:
                combos.append((strategy_name, {}))
                continue
            for combo_values in product(*values):
                params = {key: value for key, value in zip(keys, combo_values, strict=True) if value is not None}
                combos.append((strategy_name, params))
        return combos
    return [
        (fallback_strategy, {"tight_spread_maximum": spread, "minimum_book_imbalance": imbalance})
        for spread, imbalance in _grid(settings)
    ]


def _combo_config(cfg: EngineConfig, params: dict[str, Any]) -> EngineConfig:
    algo = dict(cfg.raw.get("algo", {}) or {})
    algo.update(params)
    return EngineConfig(raw={**cfg.raw, "algo": algo}, path=cfg.path)


def _score(summary: dict[str, Any]) -> tuple[int, float, float]:
    fills = int(summary.get("fills") or 0)
    pnl = float(safe_float(summary.get("unrealised_mark_to_bid_pnl_usdc")) or 0.0)
    cost = float(safe_float(summary.get("total_cost_usdc")) or 0.0)
    return fills, pnl, cost


def _selection_key(combo: dict[str, Any]) -> tuple[float, int, float, float, str, str]:
    spread = safe_float(combo.get("tight_spread_maximum"))
    imbalance = safe_float(combo.get("minimum_book_imbalance"))
    return (
        -float(combo.get("train_pnl_usdc") or 0.0),
        -int(combo.get("train_fills") or 0),
        float(spread) if spread is not None else 999.0,
        -float(imbalance) if imbalance is not None else 999.0,
        str(combo.get("strategy") or ""),
        str(combo.get("params") or ""),
    )


def _selected_from(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        return {}
    return sorted(candidates, key=_selection_key)[0]


def _combo_fieldnames(combos: list[dict[str, Any]]) -> list[str]:
    fields = list(COMBO_FIELDS)
    for combo in combos:
        for key in combo:
            if key not in fields:
                fields.append(key)
    return fields


def run_algo_sweep(
    cfg: EngineConfig,
    *,
    strategy_name: str = "tight_spread_join_bid_shadow",
    features_input: str | Path | None = None,
) -> dict[str, Any]:
    settings = _settings(cfg)
    train_fraction = min(0.9, max(0.1, float(settings.get("train_fraction", 0.7))))
    minimum_events = int(settings.get("minimum_events", 40))
    minimum_train_fills = int(settings.get("minimum_train_fills", 5))
    minimum_validation_fills = int(settings.get("minimum_validation_fills", 3))

    features_path = Path(features_input) if features_input else cfg.output_root / "polymarket_training" / "websocket_market_features.csv"
    events = iter_quote_events(features_path)
    strategy_grids = _configured_strategy_grids(settings, strategy_name)
    strategy_names = sorted({name for name, _params in strategy_grids})

    base = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "git_commit": git_commit_hash(),
        "strategy": strategy_name,
        "strategies": strategy_names,
        "features_input": str(features_path),
        "events_total": len(events),
        "train_fraction": train_fraction,
        "minimum_train_fills": minimum_train_fills,
        "minimum_validation_fills": minimum_validation_fills,
        "selection_rule": "rank on train mark-to-bid P&L only; confirm the single selected combo out-of-sample",
        "governance_note": "a validated sweep candidate is a shadow research lead only; it never promotes, sizes, or trades",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }

    if len(events) < minimum_events:
        summary = {
            **base,
            "decision": DECISION_INSUFFICIENT_EVENTS,
            "events_needed": minimum_events,
            "combos": [],
            "selected": {},
            "by_strategy": {},
        }
        write_json(cfg.output_root / "polymarket_algo" / "algo_sweep_summary.json", summary)
        return summary

    split = max(1, min(len(events) - 1, int(len(events) * train_fraction)))
    train_events = events[:split]
    validation_events = events[split:]

    combos: list[dict[str, Any]] = []
    for combo_strategy, params in strategy_grids:
        combo_cfg = _combo_config(cfg, params)
        params_text = _params_json(params)
        train = run_replay(combo_cfg, combo_strategy, events=train_events, write_artifacts=False)
        validation = run_replay(combo_cfg, combo_strategy, events=validation_events, write_artifacts=False)
        if train.get("status") != "ok" or validation.get("status") != "ok":
            combos.append(
                {
                    "strategy": combo_strategy,
                    "params": params_text,
                    **params,
                    "train_fills": 0,
                    "train_pnl_usdc": 0.0,
                    "train_cost_usdc": 0.0,
                    "validation_fills": 0,
                    "validation_pnl_usdc": 0.0,
                    "validation_cost_usdc": 0.0,
                    "train_candidate": False,
                    "selected": False,
                    "status": str(train.get("status") if train.get("status") != "ok" else validation.get("status")),
                }
            )
            continue
        train_fills, train_pnl, train_cost = _score(train)
        validation_fills, validation_pnl, validation_cost = _score(validation)
        combos.append(
            {
                "strategy": combo_strategy,
                "params": params_text,
                **params,
                "train_fills": train_fills,
                "train_pnl_usdc": round(train_pnl, 6),
                "train_cost_usdc": round(train_cost, 6),
                "validation_fills": validation_fills,
                "validation_pnl_usdc": round(validation_pnl, 6),
                "validation_cost_usdc": round(validation_cost, 6),
                "train_candidate": bool(train_fills >= minimum_train_fills and train_pnl > 0),
                "selected": False,
                "status": "ok",
            }
        )

    candidates = [combo for combo in combos if combo["train_candidate"]]
    selected: dict[str, Any] = {}
    if not candidates:
        decision = DECISION_NO_CANDIDATE
    else:
        # Deterministic: best train P&L, then more fills, then tighter spread first.
        selected = _selected_from(candidates)
        selected["selected"] = True
        validated = (
            float(selected["validation_pnl_usdc"]) > 0
            and int(selected["validation_fills"]) >= minimum_validation_fills
        )
        decision = DECISION_VALIDATED if validated else DECISION_FAILED_VALIDATION

    by_strategy: dict[str, dict[str, Any]] = {}
    for name in strategy_names:
        strategy_combos = [combo for combo in combos if combo.get("strategy") == name]
        strategy_candidates = [combo for combo in strategy_combos if combo.get("train_candidate")]
        best = _selected_from(strategy_candidates) if strategy_candidates else _selected_from(strategy_combos)
        by_strategy[name] = {
            "strategy": name,
            "combos_tested": len(strategy_combos),
            "train_candidates": len(strategy_candidates),
            "selected": dict(best) if best else {},
            "decision": (
                DECISION_NO_CANDIDATE
                if not strategy_candidates
                else DECISION_VALIDATED
                if float(best.get("validation_pnl_usdc") or 0.0) > 0
                and int(best.get("validation_fills") or 0) >= minimum_validation_fills
                else DECISION_FAILED_VALIDATION
            ),
        }

    summary = {
        **base,
        "decision": decision,
        "train_events": len(train_events),
        "validation_events": len(validation_events),
        "combos_tested": len(combos),
        "train_candidates": len(candidates),
        "combos": combos,
        "selected": selected,
        "by_strategy": by_strategy,
    }
    out_root = cfg.output_root / "polymarket_algo"
    write_json(out_root / "algo_sweep_summary.json", summary)
    write_csv(out_root / "algo_sweep_combos.csv", combos, fieldnames=_combo_fieldnames(combos))
    return summary


def main(config_path: str, strategy_name: str = "tight_spread_join_bid_shadow") -> dict[str, Any]:
    return run_algo_sweep(load_config(config_path), strategy_name=strategy_name)
