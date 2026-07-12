from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.sharp_anchor_coverage import build_sharp_anchor_coverage
from polymarket_predictive_engine.utils import read_csv_rows, write_csv, write_json


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

    # The builder records every configured sport/market once per anchor timestamp,
    # including configured markets with zero fetched rows, so missing coverage is
    # visible instead of silently omitted.
    assert first["history_rows_appended"] == 2
    assert second["history_rows_appended"] == 0
    assert len(read_csv_rows(governance / "sharp_anchor_coverage_history.csv")) == 2


def test_anchor_funnel_preserves_denominators_and_executable_divergence(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg.raw["sharp_anchor_coverage"].update(
        {
            "max_anchor_age_seconds": 3600,
            "max_price_age_seconds": 300,
            "minimum_executable_divergence": 0.03,
            "max_actionable_spread": 0.04,
            "min_actionable_liquidity": 100,
        }
    )
    governance = cfg.governance_root
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    audit_path = governance / "sharp_anchor_mapping_audit.csv"
    write_csv(
        audit_path,
        [
            {
                "source": "pinnacle",
                "sport": "soccer_fifa_world_cup",
                "market_key": "h2h",
                "market_slug": "spain-vs-france",
                "outcome": "Spain",
                "anchor_timestamp_utc": stamp,
                "mapped_row": 1,
                "token_id": "T1",
                "anchor_probability": 0.60,
                "mapping_status": "joined",
            },
            {
                "source": "pinnacle",
                "sport": "soccer_fifa_world_cup",
                "market_key": "h2h",
                "market_slug": "spain-vs-france",
                "outcome": "France",
                "anchor_timestamp_utc": stamp,
                "mapped_row": 1,
                "token_id": "T2",
                "anchor_probability": 0.40,
                "mapping_status": "joined",
            },
            {
                "source": "pinnacle",
                "sport": "soccer_fifa_world_cup",
                "market_key": "h2h",
                "market_slug": "spain-vs-france",
                "outcome": "Draw",
                "anchor_timestamp_utc": stamp,
                "mapped_row": 0,
                "token_id": "",
                "anchor_probability": 0.20,
                "mapping_status": "ambiguous",
            },
        ],
    )
    write_json(
        governance / "sharp_odds_fetch_summary.json",
        {
            "generated_at_utc": stamp,
            "configured_sports": [{"sport": "soccer_fifa_world_cup", "markets": ["h2h"]}],
            "per_source_sport_market": [
                {
                    "source": "pinnacle",
                    "sport": "soccer_fifa_world_cup",
                    "market_key": "h2h",
                    "source_rows_fetched": 4,
                    "source_rows_normalized": 3,
                }
            ],
        },
    )
    write_json(
        governance / "sharp_anchor_summary.json",
        {
            "status": "built",
            "generated_at_utc": stamp,
            "mapping_audit_path": str(audit_path),
            "coverage_by_sport_market": [
                {
                    "sport": "soccer_fifa_world_cup",
                    "market_key": "h2h",
                    "rows_in": 3,
                    "priced_rows": 3,
                    "fundamental_rows": 2,
                    "skipped_no_token": 1,
                }
            ],
        },
    )
    write_csv(
        cfg.output_root / "polymarket_predictions" / "predictions.csv",
        [
            {
                "token_id": "T1",
                "prediction_timestamp": stamp,
                "market_slug": "will-spain-beat-france",
                "question": "Will Spain beat France?",
                "category": "soccer",
                "best_bid": 0.50,
                "best_ask": 0.52,
                "market_midpoint": 0.51,
                "spread": 0.02,
                "liquidity": 500,
            },
            {
                "token_id": "T2",
                "prediction_timestamp": stamp,
                "market_slug": "will-france-beat-spain",
                "question": "Will France beat Spain?",
                "category": "soccer",
                "best_bid": 0.45,
                "best_ask": 0.47,
                "market_midpoint": 0.46,
                "spread": 0.02,
                "liquidity": 500,
            },
        ],
    )

    result = build_sharp_anchor_coverage(cfg)
    row = result["source_sport_markets"][0]

    assert result["anchor_funnel"]["status"] == "complete"
    assert row["source_rows_fetched"] == 4
    assert row["source_rows_normalized"] == 3
    assert row["mapping_audit_rows"] == 3
    assert row["normalization_rejections"] == 1
    assert row["polymarket_rows_eligible"] == 2
    assert row["rows_mapped"] == 2
    assert row["rows_joined"] == 2
    assert row["unique_markets_mapped"] == 1
    assert row["mapping_rate"] == 0.666667
    assert row["join_rate"] == 1.0
    assert row["ambiguous_rows"] == 1
    assert row["stale_rows"] == 0
    assert row["mean_executable_buy_divergence"] == 0.005
    assert row["zero_current_join"] is False
    assert row["actionable_rows"] == 1
    assert row["denominator_conservation_ok"] is True
    assert row["mapping_audit_matches_normalized"] is True
    assert result["total_actionable_rows"] == 1
    assert len(read_csv_rows(governance / "sharp_anchor_funnel_history.csv")) == 1


def test_anchor_funnel_detects_stale_cross_stage_denominators(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    governance = cfg.governance_root
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    audit_path = governance / "sharp_anchor_mapping_audit.csv"
    write_csv(
        audit_path,
        [
            {
                "source": "Pinnacle",
                "sport": "SOCCER_FIFA_WORLD_CUP",
                "market_key": "H2H",
                "anchor_timestamp_utc": stamp,
                "mapping_status": "unmapped",
            }
        ],
    )
    write_json(
        governance / "sharp_odds_fetch_summary.json",
        {
            "per_source_sport_market": [
                {
                    "source": "pinnacle",
                    "sport": "soccer_fifa_world_cup",
                    "market_key": "h2h",
                    "source_rows_fetched": 3,
                    "source_rows_normalized": 2,
                }
            ]
        },
    )
    write_json(
        governance / "sharp_anchor_summary.json",
        {
            "status": "built",
            "generated_at_utc": stamp,
            "mapping_audit_path": str(audit_path),
        },
    )

    result = build_sharp_anchor_coverage(cfg)
    row = result["source_sport_markets"][0]

    assert len(result["source_sport_markets"]) == 1
    assert row["source"] == "pinnacle"
    assert row["source_rows_normalized"] == 2
    assert row["mapping_audit_rows"] == 1
    assert row["mapping_audit_matches_normalized"] is False
    assert row["denominator_conservation_ok"] is False
    assert row["accounting_status"] == "inconsistent_fetch_anchor_artifacts"
    assert result["anchor_funnel"]["status"] == "incomplete"
    assert result["anchor_funnel"]["totals"]["accounting_mismatch_count"] == 1
