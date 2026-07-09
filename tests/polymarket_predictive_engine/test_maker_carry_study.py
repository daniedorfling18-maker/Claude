"""WO-36 maker-carry study: measurement-only, fail-safe against the two
failure modes observed live on 2026-07-09 - thin in-game books faking huge
reward shares, and a calm last-24h window hiding news-gap pick-off risk."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from polymarket_predictive_engine import maker_carry_study
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.maker_carry_study import run_maker_carry_study
from polymarket_predictive_engine.utils import read_csv_rows, read_json


def _config(tmp_path: Path):
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    raw["maker_carry_study"] = {
        "enabled": True,
        "universe_pages": 1,
        "page_size": 100,
        "min_daily_pot_usd": 25,
        "max_book_candidates": 10,
        "quote_distance_fraction": 0.5,
        "reaction_minutes": 1,
        "max_trusted_reward_share": 0.05,
        "max_size_multiple": 5,
        "capital_cap_usd": 500,
        "target_net_usd_per_day": 3.33,
        "request_pause_seconds": 0,
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def _market(question: str, token: str, pot: float, *, min_size: float = 100, max_spread: float = 3.0) -> dict[str, Any]:
    return {
        "question": question,
        "conditionId": f"0x{token}",
        "clobTokenIds": json.dumps([token, f"{token}-no"]),
        "negRisk": False,
        "volume24hr": 50000,
        "rewardsMinSize": min_size,
        "rewardsMaxSpread": max_spread,
        "clobRewards": [{"rewardsDailyRate": pot, "endDate": "2500-12-31"}],
    }


class _Response:
    def __init__(self, payload: Any):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


def _fake_requests(monkeypatch, *, markets, books, histories) -> None:
    def fake_get(url: str, params: dict[str, Any] | None = None, timeout: float | None = None):
        params = params or {}
        if url.endswith("/markets"):
            return _Response(markets if int(params.get("offset", 0)) == 0 else [])
        if url.endswith("/book"):
            return _Response(books[str(params["market" if "market" in params else "token_id"])])
        if url.endswith("/prices-history"):
            return _Response({"history": histories[(str(params["market"]), str(params["interval"]))]})
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(maker_carry_study.requests, "get", fake_get)


def _flat_history(points: int, price: float = 0.5) -> list[dict[str, float]]:
    return [{"t": i * 60, "p": price} for i in range(points)]


def _deep_book(mid: float = 0.5) -> dict[str, Any]:
    # Heavy resting competition just inside the band on both sides.
    return {
        "bids": [{"price": f"{mid - 0.005:.3f}", "size": "20000"}],
        "asks": [{"price": f"{mid + 0.005:.3f}", "size": "20000"}],
    }


def test_thin_book_share_is_untrusted_and_kept_out_of_portfolio(tmp_path, monkeypatch):
    """Observed live: an in-game esports book with an empty band implied a 40-86%
    reward share on a $2k pot. Free money on a snapshot is a danger signal."""
    cfg = _config(tmp_path)
    markets = [_market("in-game thin book", "thin", 2000.0)]
    books = {"thin": {"bids": [{"price": "0.49", "size": "10"}], "asks": [{"price": "0.51", "size": "10"}]}}
    histories = {("thin", "1d"): _flat_history(200), ("thin", "1w"): _flat_history(200)}
    _fake_requests(monkeypatch, markets=markets, books=books, histories=histories)

    summary = run_maker_carry_study(cfg)

    assert summary["candidates_thin_book_untrusted"] == 1
    assert summary["portfolio_markets"] == 0
    assert summary["clears_100_per_month_target"] is False
    rows = read_csv_rows(cfg.output_root / "maker_carry" / "maker_carry_candidates.csv")
    assert rows[0]["estimate_quality"] == "thin_book_untrusted"


def test_pickoff_charge_takes_the_worse_of_both_windows(tmp_path, monkeypatch):
    """Observed live (LeBron market): flat 24h of 1-min bars but $11+/day of
    news gaps in the 7-day window. The worse window must be charged."""
    cfg = _config(tmp_path)
    markets = [_market("calm day, gappy week", "gappy", 500.0)]
    books = {"gappy": _deep_book()}
    # 1w window: one 5-cent gap across a 10-min bar; quote distance is 0.015,
    # so the excess is 0.035 x 100 shares = $3.50 over ~1.39 days of points.
    week = _flat_history(200)
    week[100] = {"t": week[100]["t"], "p": 0.55}
    histories = {("gappy", "1d"): _flat_history(200), ("gappy", "1w"): week}
    _fake_requests(monkeypatch, markets=markets, books=books, histories=histories)

    run_maker_carry_study(cfg)

    row = read_csv_rows(cfg.output_root / "maker_carry" / "maker_carry_candidates.csv")[0]
    assert float(row["adverse_usd_per_day_1min_24h"]) == 0.0
    assert float(row["adverse_usd_per_day_10min_7d"]) > 0.0
    assert float(row["adverse_selection_usd_per_day"]) == float(row["adverse_usd_per_day_10min_7d"])


def test_sized_portfolio_scales_within_capital_cap_and_never_trades(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    markets = [_market("deep calm market", "calm", 1000.0)]
    books = {"calm": _deep_book()}
    histories = {("calm", "1d"): _flat_history(200), ("calm", "1w"): _flat_history(200)}
    _fake_requests(monkeypatch, markets=markets, books=books, histories=histories)

    summary = run_maker_carry_study(cfg)

    assert summary["status"] == "ok"
    assert summary["portfolio_markets"] == 1
    entry = summary["portfolio"][0]
    # Zero measured pick-off + diminishing share returns: size to the largest
    # multiple the $500 cap allows (100 shares x 2 x 0.5 mid = $100/unit).
    assert entry["size_multiple"] == 5
    assert entry["capital_usd"] == 500.0
    assert summary["portfolio_capital_usd"] <= 500.0
    assert summary["portfolio_net_carry_usd_per_day"] > 0
    # Measurement only - the study can never flip a trading switch.
    assert summary["paper_trading_invoked"] is False
    assert summary["live_trading_invoked"] is False
    assert "UPPER BOUND" in summary["honesty_clause"]
    persisted = read_json(cfg.output_root / "maker_carry" / "maker_carry_study.json")
    assert persisted["portfolio_net_carry_usd_per_day"] == summary["portfolio_net_carry_usd_per_day"]
    history = read_csv_rows(cfg.output_root / "maker_carry" / "maker_carry_history.csv")
    assert len(history) == 1

    # A second run appends to the trend ledger instead of overwriting it.
    run_maker_carry_study(cfg)
    assert len(read_csv_rows(cfg.output_root / "maker_carry" / "maker_carry_history.csv")) == 2


def test_markets_without_live_pots_or_bands_are_filtered(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    expired = _market("expired pot", "expired", 900.0)
    expired["clobRewards"] = [{"rewardsDailyRate": 900.0, "endDate": "2020-01-01"}]
    no_band = _market("no qualifying band", "noband", 900.0, max_spread=0.0)
    markets = [expired, no_band]
    _fake_requests(monkeypatch, markets=markets, books={}, histories={})

    summary = run_maker_carry_study(cfg)

    assert summary["universe_rewarded_markets"] == 0
    assert summary["candidates_measured"] == 0
