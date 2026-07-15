import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "polymarket_mispricing_bot.py"


def _load_scanner_module():
    spec = importlib.util.spec_from_file_location("polymarket_mispricing_bot", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _config(scanner, query: str):
    return scanner.BotConfig(
        mode="scan",
        execute_live=False,
        gamma_host="https://gamma-api.polymarket.com",
        clob_host="https://clob.polymarket.com",
        data_host="https://data-api.polymarket.com",
        query=query,
        tag_id="",
        event_slug="",
        limit=50,
        scan_seconds=1,
        scan_interval_seconds=5,
        min_edge=0.04,
        max_spread=0.04,
        max_order_usd=5,
        min_ask_size=5,
        min_bid_size=5,
        order_type="FOK",
        model_csv=Path("missing.csv"),
        positions_csv=Path("missing.csv"),
        output_dir=Path("outputs/polymarket"),
        wallet_address="",
        allow_sell_without_position=False,
        geoblock_required=False,
    )


def test_discover_events_uses_public_search_for_active_query(monkeypatch):
    scanner = _load_scanner_module()
    calls = []

    def fake_http_json(url, **_kwargs):
        calls.append(url)
        if "/public-search?" in url:
            return {"events": [{"slug": "xrp-up-or-down-on-june-26-2026", "markets": []}]}
        raise AssertionError("fallback event list should not be called when public-search finds events")

    monkeypatch.setattr(scanner, "http_json", fake_http_json)
    monkeypatch.setenv("POLYMARKET_PUBLIC_SEARCH_ENABLED", "true")

    events = scanner.discover_events(_config(scanner, "xrp up down"))

    assert [event["slug"] for event in events] == ["xrp-up-or-down-on-june-26-2026"]
    assert any("/public-search?" in url and "xrp+up+down" in url for url in calls)


def test_discover_events_falls_back_when_public_search_empty(monkeypatch):
    scanner = _load_scanner_module()

    def fake_http_json(url, **_kwargs):
        if "/public-search?" in url:
            return {"events": []}
        return [
            {"slug": "bitcoin-up-or-down-on-june-26-2026", "title": "Bitcoin Up or Down", "markets": []},
            {"slug": "unrelated", "title": "Unrelated", "markets": []},
        ]

    monkeypatch.setattr(scanner, "http_json", fake_http_json)
    monkeypatch.setenv("POLYMARKET_PUBLIC_SEARCH_ENABLED", "true")

    events = scanner.discover_events(_config(scanner, "bitcoin"))

    assert [event["slug"] for event in events] == ["bitcoin-up-or-down-on-june-26-2026"]
