from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def load_script():
    path = ROOT / "scripts/convert_smartbet_grids_to_calibration.py"
    spec = importlib.util.spec_from_file_location("convert_smartbet_grids_to_calibration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sample_grid() -> pd.DataFrame:
    rows = []
    for h in range(2):
        for a in range(2):
            rows.append(
                {
                    "source_date": "2026-06-20",
                    "source": "SmartBet",
                    "screenshot_file": "image.png",
                    "home": "AAA",
                    "away": "BBB",
                    "home_name": "Team A",
                    "away_name": "Team B",
                    "actual_score": "1-0",
                    "winner_home_pct": 50,
                    "winner_draw_pct": 25,
                    "winner_away_pct": 25,
                    "modal_score": "1-0",
                    "home_goals": h,
                    "away_goals": a,
                    "probability_pct": 12.5 if (h, a) == (1, 0) else 5.0,
                    "probability_raw": 12.5 if (h, a) == (1, 0) else 5.0,
                    "cell_type": "exact_0_to_3_grid",
                }
            )
    return pd.DataFrame(rows)


def test_build_outputs_creates_baseline_and_results():
    script = load_script()
    grid = sample_grid()
    grid["source_file"] = "input.csv"
    grid["home_team"] = grid["home_name"]
    grid["away_team"] = grid["away_name"]
    grid["_home_key"] = grid["home_team"].map(script.norm_team)
    grid["_away_key"] = grid["away_team"].map(script.norm_team)
    grid["_source_date"] = grid["source_date"]
    grid["_source_file"] = grid["source_file"]
    grid["_actual_present"] = True
    grid["_probability_pct"] = grid["probability_pct"].map(script.fnum)

    baseline, results, detail, summary = script.build_outputs(grid)

    assert len(baseline) == 1
    assert len(results) == 1
    assert baseline.iloc[0]["recommended_scoreline"] == "1-0"
    assert abs(float(baseline.iloc[0]["p_exact"]) - 0.125) < 1e-9
    assert results.iloc[0]["home_goals"] == 1
    assert summary["completed_match_count"] == 1


def test_dedupe_completed_first_prefers_completed_copy():
    script = load_script()
    a = sample_grid()
    b = sample_grid()
    a["actual_score"] = ""
    a["source_file"] = "a.csv"
    b["source_file"] = "b.csv"
    data = pd.concat([a, b], ignore_index=True)
    data["home_team"] = data["home_name"]
    data["away_team"] = data["away_name"]
    data["_home_key"] = data["home_team"].map(script.norm_team)
    data["_away_key"] = data["away_team"].map(script.norm_team)
    data["_source_date"] = data["source_date"]
    data["_source_file"] = data["source_file"]
    data["_actual_present"] = data["actual_score"].map(lambda value: script.parse_score(value) is not None)
    data["_probability_pct"] = data["probability_pct"].map(script.fnum)

    deduped = script.dedupe_grid_rows(data, "completed_first")

    assert set(deduped["source_file"]) == {"b.csv"}
