from __future__ import annotations

import argparse
import csv
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "audit_superbru_validation_data_freshness",
    ROOT / "scripts" / "audit_superbru_validation_data_freshness.py",
)
assert SPEC and SPEC.loader
audit_freshness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_freshness)
build_payload = audit_freshness.build_payload


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _args(tmp_path: Path, **overrides):
    defaults = {
        "locked_card_csv": str(tmp_path / "outputs/final_locked_picks/superbru_final_card.csv"),
        "oddspedia_grid_csv": str(tmp_path / "inputs/smartbet_grids/oddspedia_probability_grids_auto.csv"),
        "oddspedia_comparison_csv": str(tmp_path / "outputs/oddspedia_pick_validation/oddspedia_pick_comparison.csv"),
        "superbru_results_csv": str(tmp_path / "outputs/superbru_pool/superbru_match_results_auto.csv"),
        "market_history_csv": str(tmp_path / "outputs/market_odds_history/market_odds_history.csv"),
        "prediction_log_csv": str(tmp_path / "outputs/backtesting/prediction_log.csv"),
        "out_json": str(tmp_path / "outputs/superbru_validation_freshness/freshness.json"),
        "out_md": str(tmp_path / "outputs/superbru_validation_freshness/freshness.md"),
        "max_oddspedia_age_hours": 999999,
        "max_results_age_hours": 999999,
        "min_locked_card_coverage": 0.8,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_superbru_validation_freshness_reports_ready_when_sources_cover_locked_card(tmp_path):
    _write_csv(
        tmp_path / "outputs/final_locked_picks/superbru_final_card.csv",
        [
            {"home_team": "Spain", "away_team": "Austria", "locked_pick": "2-0"},
            {"home_team": "Canada", "away_team": "Morocco", "locked_pick": "0-2"},
        ],
    )
    grid_rows = [
        {"home_team": "Spain", "away_team": "Austria", "score_key": "2-0", "scraped_at_utc": "2026-07-03T08:00:00Z"},
        {"home_team": "Canada", "away_team": "Morocco", "score_key": "0-2", "scraped_at_utc": "2026-07-03T08:00:00Z"},
    ]
    _write_csv(tmp_path / "inputs/smartbet_grids/oddspedia_probability_grids_auto.csv", grid_rows)
    _write_csv(tmp_path / "outputs/oddspedia_pick_validation/oddspedia_pick_comparison.csv", grid_rows)
    _write_csv(
        tmp_path / "outputs/superbru_pool/superbru_match_results_auto.csv",
        [
            {
                "home_team": "Spain",
                "away_team": "Austria",
                "home_goals": "2",
                "away_goals": "0",
                "is_completed": "true",
                "scraped_at_utc": "2026-07-03T09:00:00Z",
            }
        ],
    )

    payload = build_payload(_args(tmp_path))

    assert payload["status"] == "ready"
    assert payload["oddspedia_grid"]["locked_card_coverage"]["matched_locked_matches"] == 2
    assert payload["superbru_results"]["completed_rows"] == 1
    assert payload["blockers"] == []


def test_superbru_validation_freshness_explains_stale_missing_coverage(tmp_path):
    _write_csv(
        tmp_path / "outputs/final_locked_picks/superbru_final_card.csv",
        [
            {"home_team": "Spain", "away_team": "Austria", "locked_pick": "2-0"},
            {"home_team": "Canada", "away_team": "Morocco", "locked_pick": "0-2"},
        ],
    )
    _write_csv(
        tmp_path / "inputs/smartbet_grids/oddspedia_probability_grids_auto.csv",
        [{"home_team": "Spain", "away_team": "Austria", "score_key": "2-0"}],
    )

    payload = build_payload(_args(tmp_path, min_locked_card_coverage=1.0))

    assert payload["status"] == "not_ready"
    assert payload["oddspedia_grid"]["locked_card_coverage"]["matched_locked_matches"] == 1
    assert any("Oddspedia grid covers 1/2" in blocker for blocker in payload["blockers"])
    assert any("SuperBru results CSV is missing" in blocker for blocker in payload["blockers"])
