import csv
from datetime import datetime, timezone

from pytest import approx

from polymarket_predictive_engine.sharp_odds_fetch import (
    event_market_slug,
    fetch_sharp_odds,
    parse_odds_api_events,
    redact_fetch_error,
)
from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.utils import write_json


class _Response:
    def __init__(self, payload, *, headers=None):
        self._payload = payload
        self.headers = headers or {}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


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


def _write(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


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


def test_fetch_uses_fallback_csv_when_provider_errors(tmp_path, monkeypatch):
    api_key = "secret-key-123"
    fallback = tmp_path / "fallback.csv"
    output = tmp_path / "sharp_odds.csv"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write(
        fallback,
        [
            {
                "market_slug": "soccer-fifa-world-cup-winner",
                "outcome": "France",
                "decimal_odds": "7.0",
                "bookmaker": "manual_pinnacle_snapshot",
                "token_id": "FRANCE_YES",
                "anchor_timestamp_utc": timestamp,
            },
            {
                "market_slug": "soccer-fifa-world-cup-winner",
                "outcome": "Spain",
                "decimal_odds": "6.0",
                "bookmaker": "manual_pinnacle_snapshot",
                "token_id": "SPAIN_YES",
                "anchor_timestamp_utc": timestamp,
            },
        ],
        ["market_slug", "outcome", "decimal_odds", "bookmaker", "token_id", "anchor_timestamp_utc"],
    )
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
                "sports": ["soccer_fifa_world_cup_winner"],
                "markets": "outrights",
                "output_path": str(output),
                "fallback_input_paths": [str(fallback)],
            },
        },
        path=tmp_path / "cfg.yaml",
    )

    summary = fetch_sharp_odds(cfg)

    assert summary["status"] == "fallback_loaded"
    assert summary["provider_status"] == "attempted"
    assert summary["errors"] == 1
    assert summary["rows"] == 2
    assert summary["fallback_rows"] == 2
    assert api_key not in summary["per_sport"][0]["error"]

    out = list(csv.DictReader(open(output, encoding="utf-8-sig")))
    assert {row["token_id"] for row in out} == {"FRANCE_YES", "SPAIN_YES"}
    assert {row["bookmaker"] for row in out} == {"manual_pinnacle_snapshot"}


def test_fallback_csv_rejections_are_reported(tmp_path, monkeypatch):
    fallback = tmp_path / "fallback.csv"
    output = tmp_path / "sharp_odds.csv"
    _write(
        fallback,
        [
            {
                "market_slug": "soccer-fifa-world-cup-winner",
                "outcome": "France",
                "decimal_odds": "7.0",
                "bookmaker": "manual_pinnacle_snapshot",
                "anchor_timestamp_utc": "2020-01-01T00:00:00Z",
            },
            {
                "market_slug": "",
                "outcome": "Spain",
                "decimal_odds": "1.0",
                "bookmaker": "",
                "anchor_timestamp_utc": "",
            },
        ],
        ["market_slug", "outcome", "decimal_odds", "bookmaker", "anchor_timestamp_utc"],
    )
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    cfg = EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "sharp_odds_fetch": {
                "sports": ["soccer_fifa_world_cup_winner"],
                "markets": "outrights",
                "output_path": str(output),
                "fallback_input_paths": [str(fallback)],
            },
        },
        path=tmp_path / "cfg.yaml",
    )

    summary = fetch_sharp_odds(cfg)

    assert summary["status"] == "missing_api_key"
    assert summary["rows"] == 0
    assert summary["fallback_rejected_rows"] == 2
    assert summary["fallback_rejection_reasons"]["stale_anchor_timestamp"] == 1
    assert summary["fallback_rejection_reasons"]["missing_market_slug"] == 1
    rejections = list(csv.DictReader(open(summary["fallback_rejections_path"], encoding="utf-8-sig")))
    assert len(rejections) == 2
    assert "decimal_odds_not_greater_than_one" in rejections[1]["reasons"]


def test_fetch_skips_when_budget_interval_has_not_elapsed(tmp_path, monkeypatch):
    monkeypatch.setenv("THE_ODDS_API_KEY", "secret-key-123")
    cfg = EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "sharp_odds_fetch": {
                "sports": ["soccer_fifa_world_cup"],
                "output_path": str(tmp_path / "sharp_odds.csv"),
                "fetch_interval_minutes": 60,
            },
        },
        path=tmp_path / "cfg.yaml",
    )
    write_json(
        cfg.governance_root / "sharp_odds_fetch_summary.json",
        {
            "status": "fetched",
            "provider_status": "attempted",
            "rows": 3,
            "markets": 1,
            "books_used": ["pinnacle"],
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )

    def fail_network(*args, **kwargs):
        raise AssertionError("budget guard should not call the provider")

    monkeypatch.setattr("polymarket_predictive_engine.sharp_odds_fetch.requests.get", fail_network)

    summary = fetch_sharp_odds(cfg)

    assert summary["status"] == "skipped_budget"
    assert summary["provider_status"] == "skipped_budget"
    assert summary["previous_status"] == "fetched"
    assert summary["seconds_until_next_fetch"] > 0


def test_fetch_skips_unknown_provider_sport_but_fetches_known_sports(tmp_path, monkeypatch):
    api_key = "secret-key-123"
    monkeypatch.setenv("THE_ODDS_API_KEY", api_key)
    output = tmp_path / "sharp_odds.csv"
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, params))
        if url.endswith("/sports"):
            return _Response([{"key": "basketball_nba"}, {"key": "soccer_fifa_world_cup"}])
        assert url.endswith("/sports/basketball_nba/odds")
        assert params["markets"] == "h2h"
        return _Response(
            [
                {
                    "id": "nba1",
                    "sport_key": "basketball_nba",
                    "sport_title": "NBA",
                    "home_team": "Boston Celtics",
                    "away_team": "Miami Heat",
                    "bookmakers": [_h2h("pinnacle", 1.7, 20.0, 2.2)],
                }
            ],
            headers={"x-requests-remaining": "499"},
        )

    monkeypatch.setattr("polymarket_predictive_engine.sharp_odds_fetch.requests.get", fake_get)
    cfg = EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "sharp_odds_fetch": {
                "sports": [
                    {"key": "tennis_atp", "markets": "h2h"},
                    {"key": "basketball_nba", "markets": "h2h"},
                ],
                "output_path": str(output),
                "fetch_interval_minutes": 0,
                "max_requests_per_run": 5,
            },
        },
        path=tmp_path / "cfg.yaml",
    )

    summary = fetch_sharp_odds(cfg)

    assert summary["status"] == "partial"
    assert summary["provider_sports_status"]["status"] == "ok"
    assert summary["skipped_unknown_sports"] == ["tennis_atp"]
    assert summary["requests_used"] == 1
    assert len(calls) == 2
    rows = list(csv.DictReader(open(output, encoding="utf-8-sig")))
    assert {row["sport"] for row in rows} == {"basketball_nba"}
    assert {row["market_key"] for row in rows} == {"h2h"}
