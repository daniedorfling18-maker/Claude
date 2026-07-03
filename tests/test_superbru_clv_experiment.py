from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

from pytest import approx

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "superbru_clv_experiment.py"
SPEC = importlib.util.spec_from_file_location("superbru_clv_experiment", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
clv = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(clv)


def _redirect(monkeypatch, tmp_path: Path) -> None:
    out = tmp_path / "superbru_clv"
    monkeypatch.setattr(clv, "OUT_DIR", out)
    monkeypatch.setattr(clv, "PICKS_CSV", out / "locked_pick_history.csv")
    monkeypatch.setattr(clv, "SNAPSHOTS_CSV", out / "odds_snapshots.csv")
    monkeypatch.setattr(clv, "REPORT_JSON", out / "clv_report.json")
    monkeypatch.setattr(clv, "REPORT_CSV", out / "clv_per_pick.csv")


def _event(home: str, away: str, commence: str, home_odds: float, draw_odds: float, away_odds: float) -> dict:
    return {
        "home_team": home,
        "away_team": away,
        "commence_time": commence,
        "bookmakers": [
            {
                "key": "pinnacle",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": home, "price": home_odds},
                            {"name": "Draw", "price": draw_odds},
                            {"name": away, "price": away_odds},
                        ],
                    }
                ],
            }
        ],
    }


def test_pick_outcome_parsing():
    assert clv._pick_outcome("2-0") == "home"
    assert clv._pick_outcome("1-1") == "draw"
    assert clv._pick_outcome("0-3") == "away"
    assert clv._pick_outcome("junk") is None


def test_devig_prefers_priority_book_and_normalises():
    fair = clv._devig_h2h(_event("Spain", "Austria", "2026-07-04T18:00:00Z", 1.5, 4.5, 7.0), ["pinnacle"])
    assert fair is not None
    assert fair["bookmaker"] == "pinnacle"
    total = fair["p_home"] + fair["p_draw"] + fair["p_away"]
    assert total == approx(1.0, abs=5e-6)
    assert fair["p_home"] > fair["p_draw"] > fair["p_away"]


def test_snapshot_and_report_end_to_end(monkeypatch, tmp_path: Path):
    _redirect(monkeypatch, tmp_path)

    # Two snapshots: pick time (Spain fair ~0.615) then near kickoff, line
    # moves toward the pick (shorter home odds -> higher fair probability).
    early = tmp_path / "early.json"
    early.write_text(json.dumps([_event("Spain", "Austria", "2026-07-04T18:00:00Z", 1.60, 4.2, 6.0)]), encoding="utf-8")
    late = tmp_path / "late.json"
    late.write_text(json.dumps([_event("Spain", "Austria", "2026-07-04T18:00:00Z", 1.45, 4.6, 7.5)]), encoding="utf-8")

    fixed_times = iter(["2026-07-04T10:00:00+00:00", "2026-07-04T17:30:00+00:00", "2026-07-04T19:00:00+00:00"])
    monkeypatch.setattr(clv, "_utc_now", lambda: clv._parse_ts(next(fixed_times)))
    first = clv.snapshot(str(early), 0, ["pinnacle"])
    second = clv.snapshot(str(late), 0, ["pinnacle"])
    assert first["events_priced"] == 1 and second["events_priced"] == 1

    picks_path = tmp_path / "superbru_clv" / "locked_pick_history.csv"
    with picks_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["locked_at_utc", "commence_time", "home_team", "away_team", "locked_pick", "pick_outcome", "match_key"])
        writer.writeheader()
        writer.writerow(
            {
                "locked_at_utc": "2026-07-04T11:00:00Z",
                "commence_time": "2026-07-04T18:00:00Z",
                "home_team": "Spain",
                "away_team": "Austria",
                "locked_pick": "2-0",
                "pick_outcome": "home",
                "match_key": clv._match_key("Spain", "Austria", "2026-07-04T18:00:00Z"),
            }
        )

    result = clv.report(minimum_samples=1, max_close_age_minutes=360)
    assert result["picks_scored"] == 1
    row = json.loads((tmp_path / "superbru_clv" / "clv_report.json").read_text(encoding="utf-8"))
    assert row["picks_scored"] == 1
    per_pick = list(csv.DictReader((tmp_path / "superbru_clv" / "clv_per_pick.csv").open()))
    pick = per_pick[0]
    # Hand-check: p_close (from 1.45) > p_pick (from 1.60) -> positive CLV.
    assert float(pick["p_close"]) > float(pick["p_pick_time"])
    assert float(pick["clv"]) == approx(float(pick["p_close"]) - float(pick["p_pick_time"]), abs=1e-6)
    assert pick["beat_close"] == "True"
    # One sample can never be a verdict - but with minimum_samples=1 and a
    # single value, the CI is undefined and the verdict must stay inconclusive.
    assert result["verdict"] == "no_detectable_edge_vs_close"
    assert result["stakes_placed"] is False


def test_report_fails_closed_below_minimum(monkeypatch, tmp_path: Path):
    _redirect(monkeypatch, tmp_path)
    (tmp_path / "superbru_clv").mkdir(parents=True)
    (tmp_path / "superbru_clv" / "locked_pick_history.csv").write_text(
        "locked_at_utc,commence_time,home_team,away_team,locked_pick,pick_outcome,match_key\n", encoding="utf-8"
    )
    result = clv.report(minimum_samples=15, max_close_age_minutes=360)
    assert result["verdict"] == "insufficient_samples"
    assert result["picks_scored"] == 0
