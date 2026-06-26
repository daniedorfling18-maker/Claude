from __future__ import annotations

import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

from polymarket_predictive_engine.config import load_config  # noqa: E402
from polymarket_predictive_engine.cohort_validation import write_signal_cohort_pnl  # noqa: E402
from polymarket_predictive_engine.mispricing_alpha import apply_mispricing_alpha  # noqa: E402
from polymarket_predictive_engine.shadow_cohort import update_shadow_cohort_evidence  # noqa: E402
from polymarket_predictive_engine.storage import connect_db  # noqa: E402
from polymarket_predictive_engine.strategy import generate_signals  # noqa: E402
from polymarket_predictive_engine.utils import write_csv, write_json  # noqa: E402
from polymarket_predictive_engine.worldcup_validation import (  # noqa: E402
    WORLDCUP_WINNER_CORRELATION_KEY,
    normalised_correlation_key,
)


def _base_config(tmp_path: Path):
    raw = yaml.safe_load((ROOT / "polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"] = {
        "data_root": str(tmp_path),
        "output_root": str(tmp_path / "outputs"),
        "database_path": str(tmp_path / "work" / "paper.sqlite"),
    }
    raw["governance_thresholds"]["min_paper_labels"] = 0
    raw["risk"].update(
        {
            "minimum_confidence": 0.0,
            "minimum_liquidity": 0,
            "minimum_time_to_close_minutes": 0,
            "maximum_order_rate_per_minute": 999,
        }
    )
    raw["mispricing_alpha"].update(
        {
            "bias_alpha_shrinkage": 0.0,
            "fundamental_residual_shrinkage": 0.35,
            "max_fundamental_adjustment": 0.12,
            "spread_penalty_weight": 0.0,
            "low_liquidity_penalty": 0.0,
            "missing_liquidity_penalty": 0.0,
            "depth_imbalance_penalty_weight": 0.0,
            "volatility_penalty_weight": 0.0,
            "uncertainty_penalty_weight": 0.0,
            "market_overround_penalty_weight": 0.0,
            "model_overround_penalty_weight": 0.0,
        }
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return load_config(config_path)


def _worldcup_prediction(**overrides):
    row = {
        "market_id": "wc-winner-market",
        "market_slug": "will-brazil-win-the-2026-fifa-world-cup",
        "question": "Will Brazil win the 2026 FIFA World Cup?",
        "category": "worldcup",
        "event_id": "worldcup-2026-winner",
        "token_id": "brazil-token",
        "prediction_timestamp": "2026-06-25T00:00:00Z",
        "market_midpoint": "0.10",
        "calibrated_probability": "0.10",
        "model_probability": "0.10",
        "executable_price": "0.11",
        "spread": "0.02",
        "liquidity": "1000",
        "time_to_close_hours": "720",
        "confidence": "1",
    }
    row.update(overrides)
    return row


def _write_same_category_labels(cfg, rows: int = 25) -> None:
    features = []
    labels = []
    for i in range(rows):
        timestamp = f"2025-01-{(i % 9) + 1:02d}T00:00:00Z"
        features.append(
            {
                "market_id": f"wc-label-{i}",
                "market_slug": f"wc-label-{i}",
                "token_id": f"wc-label-token-{i}",
                "prediction_timestamp": timestamp,
                "category": "worldcup",
                "midpoint": "0.4",
                "executable_buy_price": "0.41",
            }
        )
        labels.append(
            {
                "market_id": f"wc-label-{i}",
                "token_id": f"wc-label-token-{i}",
                "prediction_timestamp": timestamp,
                "target": "1" if i % 2 == 0 else "0",
            }
        )
    train_root = cfg.output_root / "polymarket_training"
    write_csv(train_root / "features_v2.csv", features)
    write_csv(train_root / "labels.csv", labels)


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        cfg = _base_config(tmp_path)
        write_csv(
            tmp_path / "inputs" / "polymarket" / "model_probabilities.csv",
            [{"token_id": "brazil-token", "probability": "0.30"}],
        )

        good = apply_mispricing_alpha(cfg, [_worldcup_prediction()])[0]
        assert good["bookmaker_cross_check_pass"] is True
        assert good["microstructure_filter_pass"] is True
        assert good["alpha_trade_candidate"] is True
        assert float(good["alpha_model_residual_adjustment"]) == 0.0
        assert float(good["haircut_fundamental_probability"]) == 0.20
        assert good["shadow_trade_candidate"] is True
        assert normalised_correlation_key(good) == WORLDCUP_WINNER_CORRELATION_KEY

        wide_spread = apply_mispricing_alpha(cfg, [_worldcup_prediction(spread="0.08")])[0]
        assert wide_spread["microstructure_filter_pass"] is False
        assert wide_spread["alpha_trade_candidate"] is False

        missing_bookmaker = apply_mispricing_alpha(cfg, [_worldcup_prediction(token_id="unknown-token")])[0]
        assert missing_bookmaker["bookmaker_cross_check_pass"] is False
        assert missing_bookmaker["alpha_trade_candidate"] is False

        _write_same_category_labels(cfg)
        prediction_path = cfg.output_root / "polymarket_predictions" / "predictions.csv"
        write_csv(prediction_path, [good])
        shadow = update_shadow_cohort_evidence(cfg, [good])
        assert shadow["opened_this_cycle"] == 1
        assert shadow["shadow_candidates_seen"] == 1
        con = connect_db(cfg.database_path)
        try:
            combined = write_signal_cohort_pnl(con, cfg)
        finally:
            con.close()
        assert combined["cohorts"][0]["shadow_fills"] == 1
        assert combined["cohorts"][0]["promoted"] is False

        approved, rejected = generate_signals(cfg, readiness={"approved_for_paper_trading": True, "blockers": []})
        assert approved == []
        assert rejected and "cohort promotion gate failed" in rejected[0]["rejection_reason"]

        write_json(
            cfg.governance_root / "signal_cohort_pnl.json",
            {
                "status": "computed",
                "cohorts": [
                    {
                        "signal_cohort": "worldcup_2026_winner_fundamental",
                        "buy_fills": 5,
                        "total_pnl_usdc": 1.0,
                        "promoted": True,
                    }
                ],
            },
        )
        approved, rejected = generate_signals(cfg, readiness={"approved_for_paper_trading": True, "blockers": []})
        assert len(approved) == 1
        assert rejected == []
        assert approved[0]["correlation_key"] == WORLDCUP_WINNER_CORRELATION_KEY

    print("polymarket edge validation layer smoke check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
