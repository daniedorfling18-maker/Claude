from __future__ import annotations

from pathlib import Path

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.sharp_anchor_coverage import build_sharp_anchor_coverage
from polymarket_predictive_engine.utils import read_csv_rows, write_json


def _cfg(tmp_path: Path) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "sharp_anchor_coverage": {"zero_join_cycles_before_flag": 2},
            "sharp_odds_fetch": {
                "sports": [
                    {"key": "soccer_fifa_world_cup", "markets": "h2h"},
                    {"key": "basketball_nba_championship_winner", "markets": "outrights"},
                ]
            },
        },
        path=tmp_path / "cfg.yaml",
    )


def test_sharp_anchor_coverage_flags_zero_join_after_threshold(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    governance = cfg.governance_root
    write_json(
        governance / "sharp_anchor_summary.json",
        {
            "status": "built",
            "generated_at_utc": "2026-07-05T08:00:00Z",
            "coverage_by_sport_market": [
                {
                    "sport": "soccer_fifa_world_cup",
                    "market_key": "h2h",
                    "rows_in": 6,
                    "priced_rows": 6,
                    "fundamental_rows": 0,
                    "skipped_no_token": 6,
                },
                {
                    "sport": "basketball_nba_championship_winner",
                    "market_key": "outrights",
                    "rows_in": 4,
                    "priced_rows": 4,
                    "fundamental_rows": 2,
                    "token_map_joins": 2,
                },
            ],
        },
    )
    write_json(
        governance / "sharp_odds_fetch_summary.json",
        {
            "configured_sports": [
                {"sport": "soccer_fifa_world_cup", "markets": ["h2h"]},
                {"sport": "basketball_nba_championship_winner", "markets": ["outrights"]},
            ]
        },
    )

    first = build_sharp_anchor_coverage(cfg)
    assert first["status"] == "ok"
    assert first["flagged_no_mappable_market_count"] == 0
    assert first["mappable_count"] == 1
    assert first["paper_trading_invoked"] is False
    assert first["live_trading_invoked"] is False

    write_json(
        governance / "sharp_anchor_summary.json",
        {
            "status": "built",
            "generated_at_utc": "2026-07-05T09:00:00Z",
            "coverage_by_sport_market": [
                {
                    "sport": "soccer_fifa_world_cup",
                    "market_key": "h2h",
                    "rows_in": 5,
                    "priced_rows": 5,
                    "fundamental_rows": 0,
                    "skipped_no_token": 5,
                }
            ],
        },
    )
    second = build_sharp_anchor_coverage(cfg)

    flagged = second["flagged_no_mappable_market"]
    assert [(row["sport"], row["market_key"]) for row in flagged] == [("soccer_fifa_world_cup", "h2h")]
    assert flagged[0]["zero_join_streak"] == 2
    assert flagged[0]["classification"] == "no_mappable_market"
    assert "Recommendation only" in flagged[0]["recommendation"]

    history = read_csv_rows(governance / "sharp_anchor_coverage_history.csv")
    assert len(history) == 4


def test_sharp_anchor_coverage_is_idempotent_for_same_anchor_timestamp(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    governance = cfg.governance_root
    write_json(
        governance / "sharp_anchor_summary.json",
        {
            "status": "built",
            "generated_at_utc": "2026-07-05T08:00:00Z",
            "coverage_by_sport_market": [
                {
                    "sport": "basketball_nba_championship_winner",
                    "market_key": "outrights",
                    "rows_in": 4,
                    "fundamental_rows": 2,
                }
            ],
        },
    )

    first = build_sharp_anchor_coverage(cfg)
    second = build_sharp_anchor_coverage(cfg)

    assert first["history_rows_appended"] == 1
    assert second["history_rows_appended"] == 0
    assert len(read_csv_rows(governance / "sharp_anchor_coverage_history.csv")) == 1
