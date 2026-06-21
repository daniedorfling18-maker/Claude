from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_predictions_from_locked_card import find_pick_column, synthesize_predictions_from_card


def test_synthesize_predictions_from_card_when_base_predictions_missing(tmp_path):
    card_path = tmp_path / "daily_robust_locked_picks.csv"
    card = pd.DataFrame(
        [
            {
                "commence_time": "2026-06-21T16:00:00Z",
                "home_team": "Spain",
                "away_team": "Saudi Arabia",
                "robust_locked_pick": "3-0",
                "risk_tier": "medium",
                "confidence_tier": "medium",
                "raw_pick": "3-0",
            }
        ]
    )
    card.to_csv(card_path, index=False)

    loaded = pd.read_csv(card_path).fillna("")
    pick_col = find_pick_column(loaded)
    predictions = synthesize_predictions_from_card(loaded, pick_col, card_path)

    assert predictions.loc[0, "recommended_scoreline"] == "3-0"
    assert predictions.loc[0, "raw_ev_scoreline"] == "3-0"
    assert predictions.loc[0, "risk_tier"] == "medium"
    assert predictions.loc[0, "confidence_tier"] == "medium"
    assert predictions.loc[0, "prediction_source"] == "locked_card_fallback"
    assert set(["commence_time", "home_team", "away_team", "recommended_scoreline"]).issubset(
        predictions.columns
    )
