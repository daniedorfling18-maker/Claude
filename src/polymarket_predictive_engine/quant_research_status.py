from __future__ import annotations

import importlib
from typing import Any

from .config import EngineConfig
from .utils import now_utc, read_json, write_json


CURRICULUM_MODULES = [
    (1, "Mathematical and statistical foundations", "quant_lab.foundations"),
    (2, "Financial markets, instruments, and data", "quant_lab.data"),
    (3, "Derivatives pricing and risk management", "quant_lab.pricing"),
    (4, "Classical strategies and backtesting", "quant_lab.strategies"),
    (5, "Machine learning for trading", "quant_lab.ml"),
    (6, "Reinforcement learning for trading", "quant_lab.rl"),
    (7, "NLP, sentiment, and alternative data", "quant_lab.sentiment"),
    (8, "Backtesting, risk, and deployment", "quant_lab.deployment"),
]


def _module_available(module_name: str) -> bool:
    try:
        importlib.import_module(module_name)
    except Exception:
        return False
    return True


def build_quant_research_status(cfg: EngineConfig) -> dict[str, Any]:
    """Write a single research-status artifact for the quant stack and bot integration."""
    price_action_model = read_json(cfg.output_root / "polymarket_price_action" / "price_action_model_summary.json", default={}) or {}
    if not isinstance(price_action_model, dict):
        price_action_model = {}
    price_action_feedback = read_json(cfg.governance_root / "price_action_feedback.json", default={}) or {}
    if not isinstance(price_action_feedback, dict):
        price_action_feedback = {}

    modules = [
        {
            "module": number,
            "name": name,
            "code_module": module,
            "implemented": _module_available(module),
        }
        for number, name, module in CURRICULUM_MODULES
    ]
    implemented = sum(1 for row in modules if row["implemented"])
    blockers = price_action_model.get("validation_blockers") or price_action_model.get("blockers") or []
    if not isinstance(blockers, list):
        blockers = [str(blockers)]

    payload = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "curriculum_modules": modules,
        "implemented_modules": implemented,
        "total_modules": len(modules),
        "implementation_complete": implemented == len(modules),
        "trading_posture": "shadow_research_only_until_positive_forward_bid_ask_evidence",
        "bot_integrations": [
            "strict future-bid repricing label: buy at ask, later sell/mark at executable bid",
            "chronological train/validation split with threshold selected on train only",
            "transaction-cost-aware validation through executable bid/ask P&L",
            "selected-trade drawdown, VaR, CVaR, and performance report attached to model summary",
            "promotion rule remains fail-closed until positive forward cohort evidence exists",
        ],
        "price_action_model": {
            "status": price_action_model.get("status"),
            "decision": price_action_model.get("decision"),
            "promotion_ready": price_action_model.get("promotion_ready", False),
            "training_events": price_action_model.get("training_events"),
            "validation_rows": price_action_model.get("validation_rows"),
            "selected_validation": price_action_model.get("validation_selected", {}),
            "selected_risk": price_action_model.get("validation_selected_risk", {}),
            "quant_promotion_decision": price_action_model.get("quant_promotion_decision", {}),
            "blockers": blockers,
        },
        "price_action_feedback": {
            "learning_state": price_action_feedback.get("learning_state"),
            "next_action": price_action_feedback.get("next_action"),
            "collection_queries": price_action_feedback.get("collection_queries", []),
            "promotion_candidates": price_action_feedback.get("promotion_candidates"),
            "positive_collect_candidates": price_action_feedback.get("positive_collect_candidates"),
            "best_positive_monthly_run_rate_usdc": price_action_feedback.get("best_positive_monthly_run_rate_usdc"),
        },
        "non_negotiables": [
            "no label leakage",
            "no paper promotion from in-sample fit",
            "no forced trades to create activity",
            "no live execution path from this research stack",
        ],
    }
    write_json(cfg.governance_root / "quant_research_status.json", payload)
    return payload
