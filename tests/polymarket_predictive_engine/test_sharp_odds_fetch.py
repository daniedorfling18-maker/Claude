from pytest import approx

from polymarket_predictive_engine.sharp_odds_fetch import (
    event_market_slug,
    fetch_sharp_odds,
    parse_odds_api_events,
    redact_fetch_error,
)
from polymarket_predictive_engine.config import EngineConfig


def _event(bookmakers):
    return {
        "id": "evt1",
        "sport_key": "soccer_fifa_world_cup",
        "commence_time": "2026-06-15T18:00:00Z",
        "home_team": "Spain",
        "away_team": "France",
        "bookmakers": bookmakers,
    }


def _h2h(key, spain, draw, france):
    return {
        "key": key,
        "markets": [
            {"key": "h2h", "outcomes": [
                {"name": "Spain", "price": spain},
                {"name": "France", "price": france},
                {"name": "Draw", "price": draw},
            ]}
        ],
    }


def test_event_market_slug_is_normalised():
    assert event_market_slug("Spain", "France") == event_market_slug("spain", "FRANCE")


def test_pinnacle_is_preferred_over_betfair():
    events = [_event([_h2h("betfair_ex_eu", 2.2, 3.5, 3.6), _h2h("pinnacle", 2.10, 3.40, 3.80)])]
    rows = parse_odds_api_events(events)
    assert {r["bookmaker"] for r in rows} == {"pinnacle"}      # sharper book wins
    assert len(rows) == 3
    by_outcome = {r["outcome"]: r["decimal_odds"] for r in rows}
    assert by_outcome["Spain"] == approx(2.10)
    assert all(r["market_slug"] == event_market_slug("Spain", "France") for r in rows)


def test_betfair_fallback_when_no_pinnacle():
    rows = parse_odds_api_events([_event([_h2h("betfair_ex_uk", 2.2, 3.5, 3.6)])])
    assert {r["bookmaker"] for r in rows} == {"betfair_ex_uk"}
    assert len(rows) == 3


def test_outright_market_is_keyed_by_sport_not_fixture():
    rows = parse_odds_api_events(
        [
            {
                "id": "winner1",
                "sport_key": "soccer_fifa_world_cup_winner",
                "sport_title": "FIFA World Cup Winner",
                "bookmakers": [
                    {
                        "key": "pinnacle",
                        "markets": [
                            {
                                "key": "outrights",
                                "outcomes": [
                                    {"name": "Spain", "price": 6.0},
                                    {"name": "France", "price": 7.0},
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
        market_key="outrights",
    )
    assert len(rows) == 2
    assert {r["market_slug"] for r in rows} == {"soccer-fifa-world-cup-winner"}
    assert {r["market_key"] for r in rows} == {"outrights"}


def test_event_without_priority_book_is_skipped():
    rows = parse_odds_api_events([_event([_h2h("draftkings", 2.2, 3.5, 3.6)])],
                                 bookmaker_priority=("pinnacle", "betfair_ex_eu"))
    assert rows == []


def test_bad_prices_are_dropped():
    rows = parse_odds_api_events([_event([{
        "key": "pinnacle",
        "markets": [{"key": "h2h", "outcomes": [
            {"name": "Spain", "price": 2.10},
            {"name": "France", "price": 1.0},     # price <= 1 -> dropped
            {"name": "", "price": 3.4},           # missing name -> dropped
        ]}],
    }])])
    assert len(rows) == 1 and rows[0]["outcome"] == "Spain"


def test_fetch_without_api_key_is_graceful(tmp_path, monkeypatch):
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    cfg = EngineConfig(
        raw={"paths": {"output_root": str(tmp_path / "outputs")},
             "sharp_odds_fetch": {"sports": ["soccer_fifa_world_cup"]}},
        path=tmp_path / "cfg.yaml",
    )
    summary = fetch_sharp_odds(cfg)
    assert summary["status"] == "missing_api_key"      # no network call, no crash
    assert summary["rows"] == 0


def test_fetch_error_redacts_api_key_from_provider_url():
    api_key = "secret-key-123"
    message = (
        "401 Client Error: Unauthorized for url: "
        f"https://api.the-odds-api.com/v4/sports/soccer/odds?apiKey={api_key}&regions=eu"
    )

    redacted = redact_fetch_error(message, api_key)

    assert api_key not in redacted
    assert "apiKey=[REDACTED]" in redacted


def test_fetch_all_provider_errors_reports_error_and_redacts_key(tmp_path, monkeypatch):
    api_key = "secret-key-123"
    monkeypatch.setenv("THE_ODDS_API_KEY", api_key)

    def raise_unauthorized(*args, **kwargs):
        raise RuntimeError(
            "401 Client Error: Unauthorized for url: "
            f"https://api.the-odds-api.com/v4/sports/soccer/odds?apiKey={api_key}&regions=eu"
        )

    monkeypatch.setattr("polymarket_predictive_engine.sharp_odds_fetch.requests.get", raise_unauthorized)
    cfg = EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "sharp_odds_fetch": {
                "sports": ["soccer_fifa_world_cup"],
                "output_path": str(tmp_path / "sharp_odds.csv"),
            },
        },
        path=tmp_path / "cfg.yaml",
    )

    summary = fetch_sharp_odds(cfg)

    assert summary["status"] == "error"
    assert summary["errors"] == 1
    assert summary["rows"] == 0
    assert api_key not in summary["per_sport"][0]["error"]
    assert "apiKey=[REDACTED]" in summary["per_sport"][0]["error"]
