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
                "home_team": None,
                "away_team": None,
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


def test_budget_skips_chain_and_carry_quota_reading(tmp_path, monkeypatch):
    """Consecutive attempts inside the interval must ALL skip, not alternate.

    A skip summary overwrites the file; if the guard only honoured
    provider_status == "attempted", the attempt after any skip would fall through and
    fetch for real - alternating real fetches on every other attempt instead of pacing
    to fetch_interval_minutes (observed 2026-07-07 once the loop attempted every full
    scan). Skips must chain via a carried budget_reference_at_utc and keep the last
    real quota reading visible.
    """
    monkeypatch.setenv("THE_ODDS_API_KEY", "secret-key-123")
    cfg = EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "sharp_odds_fetch": {
                "sports": ["soccer_fifa_world_cup"],
                "output_path": str(tmp_path / "sharp_odds.csv"),
                "fetch_interval_minutes": 180,
            },
        },
        path=tmp_path / "cfg.yaml",
    )
    real_fetch_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    write_json(
        cfg.governance_root / "sharp_odds_fetch_summary.json",
        {
            "status": "partial",
            "provider_status": "attempted",
            "rows": 55,
            "markets": 2,
            "books_used": ["pinnacle"],
            "requests_remaining": 19488,
            "budget_reference_at_utc": real_fetch_time,
            "generated_at_utc": real_fetch_time,
        },
    )

    def fail_network(*args, **kwargs):
        raise AssertionError("budget guard should not call the provider")

    monkeypatch.setattr("polymarket_predictive_engine.sharp_odds_fetch.requests.get", fail_network)

    first_skip = fetch_sharp_odds(cfg)
    assert first_skip["status"] == "skipped_budget"
    assert first_skip["requests_remaining"] == 19488
    assert first_skip["budget_reference_at_utc"] == real_fetch_time

    # Second attempt reads the SKIP summary from disk - it must still skip.
    second_skip = fetch_sharp_odds(cfg)
    assert second_skip["status"] == "skipped_budget"
    assert second_skip["requests_remaining"] == 19488
    assert second_skip["budget_reference_at_utc"] == real_fetch_time
    assert second_skip["previous_status"] == "partial"


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


def test_summary_reports_minimum_requests_remaining(tmp_path, monkeypatch):
    """The fetch rolls per-sport quota headers up to one conservative top-level number.

    This is what the dashboard fetch-health badge reads: a dead key or exhausted Odds
    API budget must be visible at a glance rather than buried inside per_sport.
    """
    api_key = "secret-key-123"
    monkeypatch.setenv("THE_ODDS_API_KEY", api_key)
    output = tmp_path / "sharp_odds.csv"

    def fake_get(url, params=None, timeout=None):
        if url.endswith("/sports"):
            return _Response([{"key": "soccer_fifa_world_cup"}, {"key": "basketball_nba"}])
        remaining = "480" if "basketball_nba" in url else "495"
        return _Response(
            [
                {
                    "id": "e1",
                    "sport_key": "x",
                    "sport_title": "X",
                    "home_team": "Spain",
                    "away_team": "France",
                    "bookmakers": [_h2h("pinnacle", 1.7, 20.0, 2.2)],
                }
            ],
            headers={"x-requests-remaining": remaining},
        )

    monkeypatch.setattr("polymarket_predictive_engine.sharp_odds_fetch.requests.get", fake_get)
    cfg = EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "sharp_odds_fetch": {
                "sports": [
                    {"key": "soccer_fifa_world_cup", "markets": "h2h"},
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

    assert summary["requests_remaining"] == 480  # most conservative across sports


def test_example_config_fetch_interval_protects_free_tier_quota():
    """The free Odds API tier is ~500 requests/month. The hourly cadence (2 paid requests
    per cycle for the live WC keys = ~48/day) was on track to exhaust the remaining quota
    days before the 2026-07-19 World Cup final. Keep the interval at 3h+ so the anchor
    survives the whole evidence window; do not lower it back without a quota plan.
    """
    import yaml
    from pathlib import Path

    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    assert int(raw["sharp_odds_fetch"]["fetch_interval_minutes"]) >= 180


def test_example_config_fetches_only_polymarket_mappable_markets():
    """WO-30: the shipped sharp_odds_fetch list targets only markets Polymarket lists.

    Polymarket has no per-game/per-match sports markets (verified via the Gamma API),
    so per-game h2h for NBA/MLB/MMA/tennis maps to nothing and wastes Odds API budget.
    Keep WC winner outright + WC h2h (feeds the WO-29 advance composite); NBA/MLB use
    championship-winner outrights; MMA/tennis per-game h2h are removed.
    """
    import yaml
    from pathlib import Path

    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    sports = raw["sharp_odds_fetch"]["sports"]
    by_key = {entry["key"]: entry.get("markets") for entry in sports}

    assert by_key.get("soccer_fifa_world_cup_winner") == "outrights"
    assert by_key.get("soccer_fifa_world_cup") == "h2h"  # feeds WO-29 composite advance
    assert by_key.get("basketball_nba_championship_winner") == "outrights"
    # WO-31 (closed 2026-07-08): MLB world-series outrights mapped ZERO Polymarket
    # tokens across the whole flag window while spending ~2 credits per cycle.
    # It must stay out until an MLB anchor mapping lane exists (WO-32 candidate).
    assert "baseball_mlb_world_series_winner" not in by_key

    # Per-game h2h for these maps to nothing on Polymarket - must not be fetched.
    for removed in ("basketball_nba", "baseball_mlb", "mma_mixed_martial_arts", "tennis_atp", "tennis_wta"):
        assert removed not in by_key, f"{removed} per-game h2h should be removed (WO-30)"
