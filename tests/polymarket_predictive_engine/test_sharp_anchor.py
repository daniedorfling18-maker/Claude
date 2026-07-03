import csv
from copy import deepcopy

from pytest import approx

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.sharp_anchor import (
    build_sharp_anchor,
    devig_multiplicative,
    devig_power,
    implied_from_decimal,
)


def test_implied_from_decimal():
    assert implied_from_decimal(2.0) == approx(0.5)
    assert implied_from_decimal(1.0) is None          # no real odds
    assert implied_from_decimal(None) is None


def test_multiplicative_devig_removes_overround_and_sums_to_one():
    raw = [1 / 2.10, 1 / 3.40, 1 / 3.80]              # sums to ~1.033 (3.3% overround)
    fair = devig_multiplicative(raw)
    assert sum(fair) == approx(1.0)
    assert fair[0] > fair[1] > fair[2]                # ordering preserved


def test_power_devig_sums_to_one_and_deflates_favourite_less():
    raw = [1 / 1.50, 1 / 4.50, 1 / 7.00]             # strong favourite + overround
    power = devig_power(raw)
    multiplicative = devig_multiplicative(raw)
    assert sum(power) == approx(1.0, abs=1e-6)
    # power method shrinks the favourite less than a flat proportional rescale
    assert power[0] > multiplicative[0]


def _write(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def _read(path):
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _complete_worldcup_outright_rows():
    rows = [
        ("Spain", "6.0"),
        ("France", "7.0"),
        ("Brazil", "6.5"),
        ("Argentina", "8.0"),
        ("England", "8.0"),
        ("Germany", "10.0"),
        ("Portugal", "12.0"),
        ("Italy", "12.0"),
        ("Netherlands", "15.0"),
        ("United States", "20.0"),
        ("Mexico", "25.0"),
    ]
    return [
        {
            "market_slug": "soccer-fifa-world-cup-winner",
            "outcome": team,
            "decimal_odds": odds,
            "market_key": "outrights",
            "sport": "soccer_fifa_world_cup_winner",
        }
        for team, odds in rows
    ]


def test_build_sharp_anchor_direct_token_id(tmp_path):
    cfg = EngineConfig(
        raw={"paths": {"output_root": str(tmp_path / "outputs")},
             "sharp_anchor": {"input_path": str(tmp_path / "sharp.csv"), "devig_method": "multiplicative"}},
        path=tmp_path / "cfg.yaml",
    )
    _write(tmp_path / "sharp.csv",
           [{"market_slug": "m", "outcome": "A", "decimal_odds": "2.10", "token_id": "tokA"},
            {"market_slug": "m", "outcome": "B", "decimal_odds": "3.40", "token_id": "tokB"},
            {"market_slug": "m", "outcome": "C", "decimal_odds": "3.80", "token_id": "tokC"}],
           ["market_slug", "outcome", "decimal_odds", "token_id"])

    summary = build_sharp_anchor(cfg)
    assert summary["status"] == "built"
    assert summary["fundamental_rows"] == 3
    assert summary["token_join"] == "direct_token_id"
    assert summary["mean_overround_removed"] > 0          # vig was present and removed

    out = _read(tmp_path / "outputs" / "polymarket_training" / "sharp_fundamental_probabilities.csv")
    probs = {r["token_id"]: float(r["probability"]) for r in out}
    assert set(probs) == {"tokA", "tokB", "tokC"}
    assert sum(probs.values()) == approx(1.0, abs=1e-5)   # de-vigged within the market (6dp output)
    assert probs["tokA"] > probs["tokB"] > probs["tokC"]


def test_build_sharp_anchor_joins_via_token_map(tmp_path):
    cfg = EngineConfig(
        raw={"paths": {"output_root": str(tmp_path / "outputs")},
             "sharp_anchor": {"input_path": str(tmp_path / "sharp.csv"),
                              "token_map_path": str(tmp_path / "map.csv")}},
        path=tmp_path / "cfg.yaml",
    )
    _write(tmp_path / "sharp.csv",
           [{"market_slug": "Spain vs France", "outcome": "Spain", "decimal_odds": "2.0"},
            {"market_slug": "Spain vs France", "outcome": "France", "decimal_odds": "2.0"}],
           ["market_slug", "outcome", "decimal_odds"])
    _write(tmp_path / "map.csv",
           [{"token_id": "T1", "market_slug": "Spain vs France", "outcome": "Spain"},
            {"token_id": "T2", "market_slug": "Spain vs France", "outcome": "France"}],
           ["token_id", "market_slug", "outcome"])

    summary = build_sharp_anchor(cfg)
    assert summary["token_join"] == "market_outcome_map"
    assert summary["fundamental_rows"] == 2
    out = {r["token_id"]: float(r["probability"]) for r in _read(tmp_path / "outputs" / "polymarket_training" / "sharp_fundamental_probabilities.csv")}
    assert out == {"T1": approx(0.5), "T2": approx(0.5)}   # symmetric odds -> 50/50 after de-vig


def test_build_sharp_anchor_joins_h2h_match_yes_question_without_guessing_no_side(tmp_path):
    cfg = EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "sharp_anchor": {
                "input_path": str(tmp_path / "sharp.csv"),
                "token_map_path": str(tmp_path / "map.csv"),
            },
        },
        path=tmp_path / "cfg.yaml",
    )
    _write(
        tmp_path / "sharp.csv",
        [
            {
                "market_slug": "Spain vs France",
                "outcome": "Spain",
                "decimal_odds": "2.10",
                "market_key": "h2h",
                "sport": "soccer_fifa_world_cup",
                "token_id": "",
            },
            {
                "market_slug": "Spain vs France",
                "outcome": "France",
                "decimal_odds": "3.80",
                "market_key": "h2h",
                "sport": "soccer_fifa_world_cup",
                "token_id": "",
            },
            {
                "market_slug": "Spain vs France",
                "outcome": "Draw",
                "decimal_odds": "3.40",
                "market_key": "h2h",
                "sport": "soccer_fifa_world_cup",
                "token_id": "",
            },
        ],
        ["market_slug", "outcome", "decimal_odds", "market_key", "sport", "token_id"],
    )
    _write(
        tmp_path / "map.csv",
        [
            {
                "token_id": "SPAIN_YES",
                "market_slug": "will-spain-beat-france",
                "question": "Will Spain beat France?",
                "outcome": "Yes",
            },
            {
                "token_id": "SPAIN_NO",
                "market_slug": "will-spain-beat-france",
                "question": "Will Spain beat France?",
                "outcome": "No",
            },
        ],
        ["token_id", "market_slug", "question", "outcome"],
    )

    summary = build_sharp_anchor(cfg)

    assert summary["fundamental_rows"] == 1
    assert summary["skipped_no_token"] == 2
    assert summary["token_join"] == "market_outcome_map"
    assert summary["token_map_joins"] == 1
    assert summary["direct_token_joins"] == 0
    out = _read(tmp_path / "outputs" / "polymarket_training" / "sharp_fundamental_probabilities.csv")
    assert {row["token_id"] for row in out} == {"SPAIN_YES"}
    assert {sample["outcome"] for sample in summary["skipped_no_token_samples"]} == {"France", "Draw"}


def test_build_sharp_anchor_enriches_h2h_match_tokens_from_public_search(tmp_path, monkeypatch):
    cfg = EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "sharp_anchor": {
                "input_path": str(tmp_path / "sharp.csv"),
                "token_map_path": str(tmp_path / "empty_map.csv"),
                "match_public_search_enabled": True,
                "match_public_search_max_queries": 5,
            },
        },
        path=tmp_path / "cfg.yaml",
    )
    _write(
        tmp_path / "sharp.csv",
        [
            {
                "market_slug": "Spain vs France",
                "outcome": "Spain",
                "decimal_odds": "2.10",
                "market_key": "h2h",
                "sport": "soccer_fifa_world_cup",
            },
            {
                "market_slug": "Spain vs France",
                "outcome": "France",
                "decimal_odds": "3.80",
                "market_key": "h2h",
                "sport": "soccer_fifa_world_cup",
            },
            {
                "market_slug": "Spain vs France",
                "outcome": "Draw",
                "decimal_odds": "3.40",
                "market_key": "h2h",
                "sport": "soccer_fifa_world_cup",
            },
        ],
        ["market_slug", "outcome", "decimal_odds", "market_key", "sport"],
    )
    _write(tmp_path / "empty_map.csv", [], ["token_id", "market_slug", "question", "outcome"])

    seen_queries = []

    def fake_get(*args, **kwargs):
        seen_queries.append(kwargs["params"]["q"])
        return _Response(
            {
                "events": [
                    {
                        "slug": "spain-vs-france",
                        "markets": [
                            {
                                "question": "Will Spain beat France?",
                                "outcomes": '["Yes", "No"]',
                                "clobTokenIds": '["SPAIN_YES", "SPAIN_NO"]',
                            },
                            {
                                "question": "Who will win Spain vs France?",
                                "outcomes": '["Spain", "France", "Draw"]',
                                "clobTokenIds": '["SPAIN_3WAY", "FRANCE_3WAY", "DRAW_3WAY"]',
                            },
                        ],
                    }
                ]
            }
        )

    monkeypatch.setattr("polymarket_predictive_engine.sharp_anchor.requests.get", fake_get)

    summary = build_sharp_anchor(cfg)

    assert seen_queries == ["spain vs france"]
    assert summary["fundamental_rows"] == 1
    assert summary["skipped_no_token"] == 2
    assert summary["h2h_public_search"]["queries"] == 1
    assert summary["h2h_public_search_tokens_available"] == 1
    assert summary["h2h_public_search_token_joins"] == 1
    assert summary["token_join"] == "match_public_search"
    out = _read(tmp_path / "outputs" / "polymarket_training" / "sharp_fundamental_probabilities.csv")
    assert {row["token_id"] for row in out} == {"SPAIN_YES"}
    assert {sample["outcome"] for sample in summary["skipped_no_token_samples"]} == {"France", "Draw"}


def test_build_sharp_anchor_enriches_three_way_h2h_win_and_draw_markets(tmp_path, monkeypatch):
    cfg = EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "sharp_anchor": {
                "input_path": str(tmp_path / "sharp.csv"),
                "token_map_path": str(tmp_path / "empty_map.csv"),
                "match_public_search_enabled": True,
                "match_public_search_max_queries": 5,
            },
        },
        path=tmp_path / "cfg.yaml",
    )
    _write(
        tmp_path / "sharp.csv",
        [
            {
                "market_slug": "Australia vs Egypt",
                "outcome": "Australia",
                "decimal_odds": "2.10",
                "market_key": "h2h",
                "sport": "soccer_fifa_world_cup",
            },
            {
                "market_slug": "Australia vs Egypt",
                "outcome": "Egypt",
                "decimal_odds": "3.80",
                "market_key": "h2h",
                "sport": "soccer_fifa_world_cup",
            },
            {
                "market_slug": "Australia vs Egypt",
                "outcome": "Draw",
                "decimal_odds": "3.40",
                "market_key": "h2h",
                "sport": "soccer_fifa_world_cup",
            },
        ],
        ["market_slug", "outcome", "decimal_odds", "market_key", "sport"],
    )
    _write(tmp_path / "empty_map.csv", [], ["token_id", "market_slug", "question", "outcome"])

    def fake_get(*args, **kwargs):
        assert "Mozilla/5.0" in kwargs["headers"]["User-Agent"]
        return _Response(
            {
                "events": [
                    {
                        "slug": "fifwc-aus-egy-2026-07-03",
                        "title": "Australia vs. Egypt",
                        "markets": [
                            {
                                "question": "Will Australia win on 2026-07-03?",
                                "outcomes": '["Yes", "No"]',
                                "clobTokenIds": '["AUSTRALIA_YES", "AUSTRALIA_NO"]',
                            },
                            {
                                "question": "Will Australia vs. Egypt end in a draw?",
                                "outcomes": '["Yes", "No"]',
                                "clobTokenIds": '["DRAW_YES", "DRAW_NO"]',
                            },
                            {
                                "question": "Will Egypt win on 2026-07-03?",
                                "outcomes": '["Yes", "No"]',
                                "clobTokenIds": '["EGYPT_YES", "EGYPT_NO"]',
                            },
                        ],
                    }
                ]
            }
        )

    monkeypatch.setattr("polymarket_predictive_engine.sharp_anchor.requests.get", fake_get)

    summary = build_sharp_anchor(cfg)

    assert summary["fundamental_rows"] == 3
    assert summary["skipped_no_token"] == 0
    assert summary["h2h_public_search"]["queries"] == 1
    assert summary["h2h_public_search"]["tokens"] == 3
    assert summary["h2h_public_search_token_joins"] == 3
    out = _read(tmp_path / "outputs" / "polymarket_training" / "sharp_fundamental_probabilities.csv")
    assert {row["token_id"] for row in out} == {"AUSTRALIA_YES", "EGYPT_YES", "DRAW_YES"}
    assert sum(float(row["probability"]) for row in out) == approx(1.0, abs=1e-5)


def test_build_sharp_anchor_joins_worldcup_outrights_by_team_name(tmp_path):
    cfg = EngineConfig(
        raw={"paths": {"output_root": str(tmp_path / "outputs")},
             "sharp_anchor": {"input_path": str(tmp_path / "sharp.csv"),
                              "token_map_path": str(tmp_path / "map.csv")}},
        path=tmp_path / "cfg.yaml",
    )
    _write(tmp_path / "sharp.csv",
           _complete_worldcup_outright_rows(),
           ["market_slug", "outcome", "decimal_odds", "market_key", "sport"])
    _write(tmp_path / "map.csv",
           [{"token_id": "SPAIN_YES", "market_slug": "will-spain-win-the-2026-fifa-world-cup-963",
             "question": "Will Spain win the 2026 FIFA World Cup?", "outcome": "Yes"},
            {"token_id": "FRANCE_YES", "market_slug": "will-france-win-the-2026-fifa-world-cup-924",
             "question": "Will France win the 2026 FIFA World Cup?", "outcome": "Yes"}],
           ["token_id", "market_slug", "question", "outcome"])

    summary = build_sharp_anchor(cfg)
    assert summary["token_join"] == "market_outcome_map+worldcup_winner_team_map"
    assert summary["worldcup_winner_token_joins"] == 2
    assert summary["fundamental_rows"] == 2
    out = {r["token_id"]: float(r["probability"]) for r in _read(tmp_path / "outputs" / "polymarket_training" / "sharp_fundamental_probabilities.csv")}
    assert set(out) == {"SPAIN_YES", "FRANCE_YES"}
    assert out["SPAIN_YES"] > out["FRANCE_YES"]        # shorter odds -> higher fair probability


def test_worldcup_outright_join_falls_back_to_repo_detail_file(tmp_path):
    cfg = EngineConfig(
        raw={"paths": {"output_root": str(tmp_path / "outputs")},
             "sharp_anchor": {"input_path": str(tmp_path / "sharp.csv"),
                              "token_map_path": str(tmp_path / "current_crypto_snapshot.csv")}},
        path=tmp_path / "cfg.yaml",
    )
    _write(tmp_path / "sharp.csv",
           _complete_worldcup_outright_rows(),
           ["market_slug", "outcome", "decimal_odds", "market_key", "sport"])
    _write(tmp_path / "current_crypto_snapshot.csv",
           [{"token_id": "BTC", "market_slug": "bitcoin-updown", "question": "Bitcoin up?", "outcome": "Yes"}],
           ["token_id", "market_slug", "question", "outcome"])
    _write(tmp_path / "outputs" / "polymarket" / "repo_worldcup_winner_probabilities.csv",
           [{"team": "Spain", "token_id": "SPAIN_FROM_DETAIL", "probability": "0.12"},
            {"team": "France", "token_id": "FRANCE_FROM_DETAIL", "probability": "0.13"}],
           ["team", "token_id", "probability"])

    summary = build_sharp_anchor(cfg)
    assert summary["worldcup_winner_token_joins"] == 2
    out = {r["token_id"]: float(r["probability"]) for r in _read(tmp_path / "outputs" / "polymarket_training" / "sharp_fundamental_probabilities.csv")}
    assert set(out) == {"SPAIN_FROM_DETAIL", "FRANCE_FROM_DETAIL"}


def test_worldcup_outright_join_uses_public_search_and_excludes_confederations(tmp_path, monkeypatch):
    cfg = EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "sharp_anchor": {
                "input_path": str(tmp_path / "sharp.csv"),
                "token_map_path": str(tmp_path / "missing_snapshot.csv"),
                "worldcup_public_search_enabled": True,
            },
        },
        path=tmp_path / "cfg.yaml",
    )
    _write(
        tmp_path / "sharp.csv",
        _complete_worldcup_outright_rows(),
        ["market_slug", "outcome", "decimal_odds", "market_key", "sport"],
    )

    def fake_get(*args, **kwargs):
        return _Response(
            {
                "events": [
                    {
                        "slug": "world-cup-winner",
                        "title": "World Cup Winner",
                        "markets": [
                            {
                                "question": "Will Spain win the 2026 FIFA World Cup?",
                                "outcomes": '["Yes", "No"]',
                                "clobTokenIds": '["SPAIN_YES", "SPAIN_NO"]',
                            },
                            {
                                "question": "Will France win the 2026 FIFA World Cup?",
                                "outcomes": ["Yes", "No"],
                                "clobTokenIds": ["FRANCE_YES", "FRANCE_NO"],
                            },
                            {
                                "question": "Will Europe (UEFA) win the 2026 FIFA World Cup?",
                                "outcomes": ["Yes", "No"],
                                "clobTokenIds": ["UEFA_YES", "UEFA_NO"],
                            },
                        ],
                    }
                ]
            }
        )

    monkeypatch.setattr("polymarket_predictive_engine.sharp_anchor.requests.get", fake_get)

    summary = build_sharp_anchor(cfg)

    assert summary["worldcup_winner_tokens_available"] == 2
    assert summary["worldcup_winner_token_joins"] == 2
    out = {
        r["token_id"]: float(r["probability"])
        for r in _read(tmp_path / "outputs" / "polymarket_training" / "sharp_fundamental_probabilities.csv")
    }
    assert set(out) == {"SPAIN_YES", "FRANCE_YES"}
    assert "UEFA_YES" not in out


def test_build_sharp_anchor_skips_partial_outright_to_avoid_inflated_edge(tmp_path):
    cfg = EngineConfig(
        raw={"paths": {"output_root": str(tmp_path / "outputs")},
             "sharp_anchor": {"input_path": str(tmp_path / "sharp.csv"),
                              "token_map_path": str(tmp_path / "map.csv")}},
        path=tmp_path / "cfg.yaml",
    )
    partial = deepcopy(_complete_worldcup_outright_rows()[:2])
    _write(tmp_path / "sharp.csv", partial, ["market_slug", "outcome", "decimal_odds", "market_key", "sport"])
    _write(tmp_path / "map.csv",
           [{"token_id": "SPAIN_YES", "market_slug": "will-spain-win-the-2026-fifa-world-cup-963",
             "question": "Will Spain win the 2026 FIFA World Cup?", "outcome": "Yes"},
            {"token_id": "FRANCE_YES", "market_slug": "will-france-win-the-2026-fifa-world-cup-924",
             "question": "Will France win the 2026 FIFA World Cup?", "outcome": "Yes"}],
           ["token_id", "market_slug", "question", "outcome"])

    summary = build_sharp_anchor(cfg)

    assert summary["fundamental_rows"] == 0
    assert summary["skipped_incomplete_markets"] == 1
    assert summary["incomplete_market_samples"][0]["reason"] == "implied_sum_below_complete_market_floor"


def test_build_sharp_anchor_no_input(tmp_path):
    cfg = EngineConfig(
        raw={"paths": {"output_root": str(tmp_path / "outputs")},
             "sharp_anchor": {"input_path": str(tmp_path / "missing.csv")}},
        path=tmp_path / "cfg.yaml",
    )
    summary = build_sharp_anchor(cfg)
    assert summary["status"] == "no_input"
    assert summary["fundamental_rows"] == 0
