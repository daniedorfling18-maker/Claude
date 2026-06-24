from __future__ import annotations

from typing import Any

from .config import EngineConfig, load_config
from .market_relative_validation import validate_market_relative_model
from .utils import write_json


def validate_model(cfg: EngineConfig) -> dict[str, Any]:
    summary = validate_market_relative_model(cfg)
    oos = summary.get("oos") or {}
    card_lines = [
        "# Polymarket Model Card",
        "",
        f"Model version: {summary.get('model_version')}",
        f"Validation status: {summary.get('status')}",
        f"Split method: {summary.get('split_method')}",
        f"Clean settled joined rows: {summary.get('clean_settled_joined_rows')}",
        f"Out-of-sample rows: {oos.get('sample_size')}",
        f"Out-of-sample markets: {oos.get('markets')}",
        f"Model Brier score: {oos.get('model_brier_score')}",
        f"Market Brier score: {oos.get('market_brier_score')}",
        f"Brier improvement versus market: {oos.get('brier_improvement_vs_market')}",
        f"Brier gain CI95: {summary.get('brier_gain_vs_market_ci95')}",
        f"Approved for paper trading: {summary.get('approved_for_paper_trading')}",
        "Approved for automated execution: False",
    ]
    out = cfg.output_root / "polymarket_model_validation"
    (out / "model_card.md").parent.mkdir(parents=True, exist_ok=True)
    (out / "model_card.md").write_text("\n".join(card_lines) + "\n", encoding="utf-8")
    write_json(out / "model_validation_summary.json", summary)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return validate_model(load_config(config_path))
