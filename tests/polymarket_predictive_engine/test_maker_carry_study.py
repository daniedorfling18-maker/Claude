"""WO-36 maker-carry study: measurement-only, fail-safe against the two
failure modes observed live on 2026-07-09 - thin in-game books faking huge
reward shares, and a calm last-24h window hiding news-gap pick-off risk."""
from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from polymarket_predictive_engine import maker_carry_study
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.maker_carry_study import (
    MAKER_CARRY_LEDGER_LOCK,
    _book_history_depth,
    _incumbent_hold,
    _maker_carry_ledger_lock_path,
    _measurement_eligible,
    _size_portfolio,
    run_maker_carry_study,
)
from polymarket_predictive_engine.utils import (
    csv_columns,
    read_csv_rows,
    read_json,
    write_csv,
    write_json,
)


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
        "quote_distance_fractions": [0.5],
        "reaction_minutes": 1,
        "max_trusted_reward_share": 0.05,
        "max_size_multiple": 5,
        "capital_cap_usd": 500,
        "target_net_usd_per_day": 3.33,
        "markout_horizon_minutes": 5,
        "markout_min_prints": 20,
        "min_daily_payout_usd": 1.0,
        "gate_min_runs_at_target": 7,
        # WO-113 depth/stickiness gate off by default here; the dedicated
        # WO-113 tests set live thresholds and stage a book archive.
        "maker_min_book_history_hours": 0,
        "maker_min_book_snapshots": 0,
        "maker_switch_margin_frac": 0.25,
        "maker_max_hold_days": 30,
        "request_pause_seconds": 0,
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def _market(
    question: str,
    token: str,
    pot: float,
    *,
    min_size: float = 100,
    max_spread: float = 3.0,
    fees_enabled: bool = True,
    fee_type: str = "sports_fees_v2",
) -> dict[str, Any]:
    return {
        "id": f"market-{token}",
        "question": question,
        "conditionId": f"0x{token}",
        "slug": f"market-{token}",
        "events": [{"slug": f"event-{token}"}],
        "outcomes": json.dumps(["Yes", "No"]),
        "clobTokenIds": json.dumps([token, f"{token}-no"]),
        "endDate": "2500-12-31T00:00:00Z",
        "orderPriceMinTickSize": 0.01,
        "negRisk": False,
        "feesEnabled": fees_enabled,
        "feeType": fee_type,
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


def _fake_requests(monkeypatch, *, markets, books, histories, prints=None) -> None:
    def book_for(token_id: str) -> dict[str, Any]:
        if token_id in books:
            book = dict(books[token_id])
        elif token_id.endswith("-no") and token_id[:-3] in books:
            # Existing WO-36 tests cared only about the YES token. Mirror the
            # book for the complement so WO-46 exercises the two-token model
            # without changing those tests' economic setup.
            book = dict(books[token_id[:-3]])
        else:
            book = {}
        return {"token_id": token_id, **book}

    def fake_get(url: str, params: dict[str, Any] | None = None, timeout: float | None = None):
        params = params or {}
        if url.endswith("/markets"):
            return _Response(markets if int(params.get("offset", 0)) == 0 else [])
        if url.endswith("/book"):
            return _Response(book_for(str(params["market" if "market" in params else "token_id"])))
        if url.endswith("/prices-history"):
            return _Response({"history": histories[(str(params["market"]), str(params["interval"]))]})
        if url.endswith("/trades"):
            return _Response((prints or {}).get(str(params["market"]), []))
        raise AssertionError(f"unexpected url {url}")

    def fake_post(url: str, json: Any = None, timeout: float | None = None):
        if url.endswith("/books"):
            requested = [str(row.get("token_id") or row.get("market") or "") for row in (json or [])]
            return _Response([book_for(token_id) for token_id in requested])
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(maker_carry_study.requests, "get", fake_get)
    monkeypatch.setattr(maker_carry_study.requests, "post", fake_post)


def _flat_history(points: int, price: float = 0.5) -> list[dict[str, float]]:
    return [{"t": i * 60, "p": price} for i in range(points)]


def _deep_book(mid: float = 0.5) -> dict[str, Any]:
    # Heavy resting competition just inside the band on both sides.
    return {
        "tick_size": "0.01",
        "bids": [{"price": f"{mid - 0.005:.3f}", "size": "20000"}],
        "asks": [{"price": f"{mid + 0.005:.3f}", "size": "20000"}],
    }


def test_recent_prints_retain_only_the_exact_condition_token_pair(tmp_path, monkeypatch) -> None:
    cfg = _config(tmp_path)
    trades = [
        {
            "price": 0.49,
            "size": 5,
            "side": "SELL",
            "timestamp": 1_700_000_000,
            "conditionId": "0xcalm",
            "asset": "calm",
        },
        {
            "price": 0.01,
            "size": 10_000,
            "side": "SELL",
            "timestamp": 1_700_000_001,
            "conditionId": "0xcalm",
            "asset": "calm-no",
        },
        {
            "price": 0.02,
            "size": 10_000,
            "side": "SELL",
            "timestamp": 1_700_000_002,
            "conditionId": "0xother",
            "asset": "calm",
        },
        {
            "price": 0.03,
            "size": 10_000,
            "side": "SELL",
            "timestamp": 1_700_000_003,
            "conditionId": "0xcalm",
        },
    ]
    _fake_requests(
        monkeypatch,
        markets=[],
        books={},
        histories={},
        prints={"0xcalm": trades},
    )

    rows = maker_carry_study._recent_prints(
        maker_carry_study._settings(cfg),
        "0xcalm",
        "calm",
    )

    assert len(rows) == 1
    assert rows[0]["condition_id"] == "0xcalm"
    assert rows[0]["token_id"] == "calm"
    assert rows[0]["price"] == 0.49


def test_recent_prints_fail_closed_when_requested_identity_is_missing(tmp_path, monkeypatch) -> None:
    cfg = _config(tmp_path)
    called = False

    def fail_get(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("identity check must happen before the request")

    monkeypatch.setattr(maker_carry_study.requests, "get", fail_get)
    settings = maker_carry_study._settings(cfg)

    assert maker_carry_study._recent_prints(settings, "", "calm") == []
    assert maker_carry_study._recent_prints(settings, "0xcalm", "") == []
    assert called is False


def _scan_market(token: str, pot: float, pot_rank: int) -> dict[str, Any]:
    return {
        "question": f"{token} market",
        "condition_id": f"0x{token}",
        "token_id": token,
        "complement_token_id": f"{token}-no",
        "pot_usd_per_day": pot,
        "pot_rank": pot_rank,
        "yield_rank": "",
        "expected_gross_at_min_size": "",
        "rewards_min_size_shares": 10.0,
        "rewards_max_spread_cents": 2.0,
    }


def test_yield_first_scan_selects_smaller_under_competed_pot(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    settings = maker_carry_study._settings(cfg)
    settings["max_book_candidates"] = 1
    settings["yield_scan_max_markets"] = 2
    universe = [_scan_market("crowded", 1_000.0, 1), _scan_market("open", 100.0, 2)]
    books = {
        "crowded": _deep_book(),
        "crowded-no": _deep_book(),
        "open": {
            "bids": [{"price": "0.49", "size": "200"}],
            "asks": [{"price": "0.51", "size": "200"}],
        },
        "open-no": {
            "bids": [{"price": "0.49", "size": "200"}],
            "asks": [{"price": "0.51", "size": "200"}],
        },
    }
    monkeypatch.setattr(maker_carry_study, "_fetch_books", lambda _settings, _tokens: books)

    selected, scan = maker_carry_study._yield_first_shortlist(settings, universe, [0.5])

    assert selected[0]["condition_id"] == "0xopen"
    assert selected[0]["pot_rank"] == 2
    assert selected[0]["yield_rank"] == 1
    assert selected[0]["expected_gross_at_min_size"] > 0
    assert scan["universe_scan_mode"] == "yield_first_v1"
    assert scan["yield_scan_considered_markets"] == 2
    assert scan["yield_scan_scored_markets"] == 2
    assert scan["yield_scan_fallback"] is False


def test_yield_first_scan_fails_soft_to_pot_rank(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    settings = maker_carry_study._settings(cfg)
    settings["max_book_candidates"] = 1
    settings["yield_scan_max_markets"] = 2
    universe = [_scan_market("large", 1_000.0, 1), _scan_market("small", 100.0, 2)]

    def fail_books(_settings, _tokens):
        raise RuntimeError("synthetic book outage")

    monkeypatch.setattr(maker_carry_study, "_fetch_books", fail_books)

    selected, scan = maker_carry_study._yield_first_shortlist(settings, universe, [0.5])

    assert selected[0]["condition_id"] == "0xlarge"
    assert selected[0]["pot_rank"] == 1
    assert scan["universe_scan_mode"] == "pot_rank_fallback"
    assert scan["yield_scan_fallback"] is True
    assert "synthetic book outage" in scan["yield_scan_error"]


def test_candidate_staleness_reasons_cover_close_and_resolution_states():
    as_of = datetime(2026, 7, 13, 12, tzinfo=timezone.utc)

    assert maker_carry_study._candidate_staleness_reasons(
        {"question": "Will X happen?", "endDate": "2026-07-13T11:59:59Z"},
        as_of=as_of,
    ) == ["venue_close_time_past"]
    assert maker_carry_study._candidate_staleness_reasons(
        {"question": "Will X happen?", "endDate": "2026-07-20T00:00:00Z", "umaResolutionStatus": "proposed"},
        as_of=as_of,
    ) == ["resolution_proposed"]
    assert maker_carry_study._candidate_staleness_reasons(
        {"question": "Will X happen?", "endDate": "2026-07-20T00:00:00Z", "umaResolutionStatus": "DISPUTED"},
        as_of=as_of,
    ) == ["resolution_disputed"]


def test_same_month_title_range_remains_live_through_its_last_day():
    market = {
        "question": "Will the event happen July 9-20, 2026?",
        "endDate": "2026-07-20T23:59:59Z",
    }

    assert maker_carry_study._candidate_staleness_reasons(
        market,
        as_of=datetime(2026, 7, 13, 12, tzinfo=timezone.utc),
    ) == []
    assert maker_carry_study._candidate_staleness_reasons(
        market,
        as_of=datetime(2026, 7, 21, 12, tzinfo=timezone.utc),
    ) == ["venue_close_time_past", "title_date_past"]


def test_past_dated_rewarded_market_is_excluded_before_selection(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    market = _market(
        "Will Iran take military action against a Gulf State on July 9?",
        "past-date",
        100.0,
    )
    # A late venue resolution deadline must not make a past event date look
    # quoteable. This is the exact pathology observed in the 2026-07-13 scan.
    market["endDate"] = "2026-12-31T00:00:00Z"
    market["clobRewards"] = [{"rewardsDailyRate": 100.0, "endDate": "2026-12-31"}]
    monkeypatch.setattr(maker_carry_study, "now_utc", lambda: "2026-07-13T12:00:00Z")
    _fake_requests(monkeypatch, markets=[market], books={}, histories={})

    summary = run_maker_carry_study(cfg)

    assert summary["universe_rewarded_markets"] == 0
    assert summary["excluded_stale"] == 1
    assert summary["excluded_stale_by_reason"] == {"title_date_past": 1}
    assert summary["yield_scan_considered_markets"] == 0
    assert read_csv_rows(cfg.output_root / "maker_carry" / "maker_carry_candidates.csv") == []


def test_published_share_model_worked_example_uses_market_and_complement(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    settings = maker_carry_study._settings(cfg)
    books = {
        "yes": {"bids": [{"price": "0.49", "size": "100"}], "asks": [{"price": "0.51", "size": "100"}]},
        "no": {"bids": [{"price": "0.49", "size": "100"}], "asks": [{"price": "0.51", "size": "100"}]},
    }

    def fake_post(url: str, json: Any = None, timeout: float | None = None):
        return _Response([{"token_id": row["token_id"], **books[row["token_id"]]} for row in json])

    monkeypatch.setattr(maker_carry_study.requests, "post", fake_post)

    competition = maker_carry_study._book_competition(settings, "yes", "no", 0.02)
    assert competition["band_eligible"] is True
    # 0.49 bid + complement 0.51 ask both map to the same YES-side score.
    assert round(competition["bid_score"], 6) == 50.0
    assert round(competition["ask_score"], 6) == 50.0
    share, marginal = maker_carry_study._share_from_published_score(settings, competition, 25.0)
    assert marginal == 25.0
    assert round(share, 6) == round(25.0 / 75.0, 6)


def test_single_sided_liquidity_scores_one_third_inside_band(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    settings = maker_carry_study._settings(cfg)
    # YES bid at 0.49 contributes Q_one=25. Ask exists only to form the mid.
    books = {
        "yes": {"bids": [{"price": "0.49", "size": "100"}], "asks": [{"price": "0.51", "size": "0"}]},
        "no": {"bids": [], "asks": []},
    }

    def fake_post(url: str, json: Any = None, timeout: float | None = None):
        return _Response([{"token_id": row["token_id"], **books[row["token_id"]]} for row in json])

    monkeypatch.setattr(maker_carry_study.requests, "post", fake_post)

    competition = maker_carry_study._book_competition(settings, "yes", "no", 0.02)
    assert competition["band_eligible"] is True
    assert round(competition["bid_score"], 6) == 25.0
    assert round(competition["ask_score"], 6) == 0.0
    assert round(competition["published_pool_score"], 6) == round(25.0 / 3.0, 6)


def test_outside_band_requires_strict_two_sided_score(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    settings = maker_carry_study._settings(cfg)
    books = {
        "yes": {"bids": [{"price": "0.94", "size": "100"}], "asks": [{"price": "0.96", "size": "0"}]},
        "no": {"bids": [], "asks": []},
    }

    def fake_post(url: str, json: Any = None, timeout: float | None = None):
        return _Response([{"token_id": row["token_id"], **books[row["token_id"]]} for row in json])

    monkeypatch.setattr(maker_carry_study.requests, "post", fake_post)

    competition = maker_carry_study._book_competition(settings, "yes", "no", 0.02)
    assert competition["band_eligible"] is False
    assert competition["published_pool_score"] == 0.0


def test_band_ineligible_candidate_is_excluded_from_portfolio(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    markets = [_market("outside band", "outside", 1000.0)]
    books = {
        "outside": {"bids": [{"price": "0.94", "size": "20000"}], "asks": [{"price": "0.96", "size": "20000"}]},
        "outside-no": {"bids": [{"price": "0.04", "size": "20000"}], "asks": [{"price": "0.06", "size": "20000"}]},
    }
    histories = {("outside", "1d"): _flat_history(200, price=0.95), ("outside", "1w"): _flat_history(200, price=0.95)}
    _fake_requests(monkeypatch, markets=markets, books=books, histories=histories)

    summary = run_maker_carry_study(cfg)

    row = read_csv_rows(cfg.output_root / "maker_carry" / "maker_carry_candidates.csv")[0]
    assert row["band_eligible"] == "False"
    assert row["estimate_quality"] == "band_ineligible"
    assert summary["portfolio_markets"] == 0
def test_supplementary_fee_and_rebate_tables_are_category_aware(tmp_path):
    cfg = _config(tmp_path)
    settings = maker_carry_study._settings(cfg)
    sports = {"fees_enabled": True, "fee_type": "sports_fees_v2"}
    unknown_enabled = {"fees_enabled": True, "fee_type": "other_fees_v1"}

    assert maker_carry_study._market_fee_rate(sports, settings) == 0.05
    assert maker_carry_study._maker_rebate_share(sports, settings) == 0.15
    assert maker_carry_study._market_fee_rate(unknown_enabled, settings) == 0.07
    assert maker_carry_study._maker_rebate_share(unknown_enabled, settings) == 0.25


def test_fee_free_markets_have_zero_supplementary_rebate(tmp_path):
    cfg = _config(tmp_path)
    settings = maker_carry_study._settings(cfg)
    row = {
        "fees_enabled": False,
        "fee_type": "",
        "capital_usd": 100.0,
        "rewards_min_size_shares": 100.0,
        "mid_price": 0.5,
        "band_crossing_prints_per_day": 50.0,
    }

    supplement = maker_carry_study._supplementary_income(row, settings, {"bid": 0.0, "ask": 0.0})

    assert supplement["supplementary_rebate_usd_per_day"] == 0.0
    assert supplement["supplementary_holding_usd_per_day"] > 0.0


def test_resolution_risk_keyword_classes_and_absent_report_tolerance(tmp_path):
    cfg = _config(tmp_path)
    stats = maker_carry_study._resolution_quality_class_stats(cfg)

    fed = maker_carry_study._resolution_risk_for_question(
        "Will the Fed leave interest rates unchanged after the July decision?",
        stats,
    )
    match = maker_carry_study._resolution_risk_for_question("Will Arsenal beat Chelsea in the match?", stats)
    numeric = maker_carry_study._resolution_risk_for_question("Will ETH close above $4,000 on July 31?", stats)
    election = maker_carry_study._resolution_risk_for_question("Who will win the official election result?", stats)
    high = maker_carry_study._resolution_risk_for_question("Will X officially announce a ceasefire deal?", stats)
    medium = maker_carry_study._resolution_risk_for_question("Will a minister visit Paris?", stats)

    assert fed["resolution_risk"] == "low"
    assert fed["resolution_risk_class"] == "fed_rate_decision"
    assert match["resolution_risk_class"] == "match_game_winner"
    assert numeric["resolution_risk_class"] == "numeric_close_above_below"
    assert election["resolution_risk_class"] == "official_election_result"
    assert high["resolution_risk"] == "high"
    assert high["resolution_risk_class"] == "subjective_announce"
    assert medium["resolution_risk"] == "medium"
    assert fed["resolution_risk_sample_markets"] == 0


def test_resolution_risk_corpus_overlay_escalates_low_only_after_sample_floor(tmp_path):
    cfg = _config(tmp_path)
    fields = ["market_slug", "question", "resolution_quality"]
    base_question = "Will ABC close above $100 on January 1?"
    rows = [
        {
            "market_slug": f"m-{idx}",
            "question": base_question,
            "resolution_quality": "clean_settlement" if idx < 44 else "ambiguous_settlement_vector",
        }
        for idx in range(49)
    ]
    write_csv(cfg.governance_root / "resolution_quality_report.csv", rows, fieldnames=fields)

    below_floor = maker_carry_study._resolution_risk_for_question(
        base_question,
        maker_carry_study._resolution_quality_class_stats(cfg),
    )

    assert below_floor["resolution_risk"] == "low"
    assert below_floor["resolution_risk_sample_markets"] == 49

    rows.append(
        {
            "market_slug": "m-49",
            "question": base_question,
            "resolution_quality": "ambiguous_settlement_vector",
        }
    )
    write_csv(cfg.governance_root / "resolution_quality_report.csv", rows, fieldnames=fields)

    escalated = maker_carry_study._resolution_risk_for_question(
        base_question,
        maker_carry_study._resolution_quality_class_stats(cfg),
    )
    high = maker_carry_study._resolution_risk_for_question(
        "Will X officially announce a ceasefire deal?",
        maker_carry_study._resolution_quality_class_stats(cfg),
    )

    assert escalated["resolution_risk"] == "medium"
    assert escalated["resolution_risk_sample_markets"] == 50
    assert escalated["resolution_risk_clean_share"] == 0.88
    assert "escalated by corpus" in escalated["resolution_risk_reason"]
    # Tighten-only: the clean numeric-close corpus cannot downgrade subjective
    # wording to low/medium.
    assert high["resolution_risk"] == "high"


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
    assert summary["universe_scan_mode"] == "yield_first_v1"
    assert summary["yield_scan_fallback"] is False
    assert summary["portfolio_markets"] == 1
    entry = summary["portfolio"][0]
    # Zero measured pick-off + diminishing share returns: size to the largest
    # multiple the $500 cap allows (100 shares x 2 x 0.5 mid = $100/unit).
    assert entry["size_multiple"] == 5
    assert entry["capital_usd"] == 500.0
    assert entry["market_url"] == "https://polymarket.com/event/event-calm"
    assert entry["outcome"] == "Yes"
    assert entry["token_id"] == "calm"
    assert entry["quote_bid_price"] == 0.48
    assert entry["quote_ask_price"] == 0.52
    assert entry["quote_size_shares"] == 500
    assert entry["order_ticket_status"] == "exact"
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
    assert history[0]["universe_scan_mode"] == "yield_first_v1"
    assert history[0]["top_portfolio_market"] == "0xcalm"
    assert history[0]["top_portfolio_question"] == "deep calm market"
    candidate = read_csv_rows(cfg.output_root / "maker_carry" / "maker_carry_candidates.csv")[0]
    assert candidate["pot_rank"] == "1"
    assert candidate["yield_rank"] == "1"
    sheet = (cfg.output_root / "maker_carry" / "maker_quote_sheet.md").read_text(encoding="utf-8")
    assert "Exact human order tickets (WO-66)" in sheet
    assert "[deep calm market](https://polymarket.com/event/event-calm)" in sheet
    assert "| Yes | 0.4800 | 0.5200 | 500 |" in sheet

    # A second run appends to the trend ledger instead of overwriting it.
    run_maker_carry_study(cfg)
    assert len(read_csv_rows(cfg.output_root / "maker_carry" / "maker_carry_history.csv")) == 2


def test_capital_curve_recovers_target_cap_and_diminishing_yield(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    markets = [_market("deep calm market", "calm", 1000.0)]
    books = {"calm": _deep_book()}
    histories = {("calm", "1d"): _flat_history(200), ("calm", "1w"): _flat_history(200)}
    _fake_requests(monkeypatch, markets=markets, books=books, histories=histories)

    summary = run_maker_carry_study(cfg)

    curve = summary["capital_curve"]
    assert [row["capital_cap_usd"] for row in curve] == [250.0, 500.0, 1000.0, 2000.0, 5000.0]
    net = [row["net_usd_per_day"] for row in curve]
    assert net == sorted(net)
    yields = [row["net_per_day_per_capital_used"] for row in curve]
    assert all(left >= right for left, right in zip(yields, yields[1:]))
    # At $250 only 2 x $100 quote units fit; at $500 all five fit. The
    # planted calm $1,000 pot therefore first clears $3.33/day at $500.
    assert net[0] == 1.8
    assert net[1] == 4.48
    assert summary["capital_for_100_per_month"] == 500.0
    assert curve[1]["net_usd_per_day"] == summary["portfolio_net_carry_usd_per_day"]
    sheet = (cfg.output_root / "maker_carry" / "maker_quote_sheet.md").read_text(encoding="utf-8")
    assert "planning aid - uncounted, not a gate input" in sheet


def test_capital_curve_toggle_cannot_change_registered_metric(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    markets = [_market("deep calm market", "calm", 1000.0)]
    books = {"calm": _deep_book()}
    histories = {("calm", "1d"): _flat_history(200), ("calm", "1w"): _flat_history(200)}
    _fake_requests(monkeypatch, markets=markets, books=books, histories=histories)
    enabled = run_maker_carry_study(cfg)

    raw = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    raw["maker_carry_study"]["capital_curve_enabled"] = False
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    disabled = run_maker_carry_study(load_config(tmp_path / "config.yaml"))

    assert disabled["capital_curve"] == []
    assert disabled["capital_for_100_per_month"] is None
    assert disabled["portfolio"] == enabled["portfolio"]
    assert disabled["portfolio_net_carry_usd_per_day"] == enabled["portfolio_net_carry_usd_per_day"]
    assert disabled["maker_gates"]["M_C_payout_floor"] == enabled["maker_gates"]["M_C_payout_floor"]


def test_capital_curve_returns_null_when_largest_cap_cannot_clear_target(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    markets = [_market("small calm pot", "small", 25.0)]
    books = {"small": _deep_book()}
    histories = {("small", "1d"): _flat_history(200), ("small", "1w"): _flat_history(200)}
    _fake_requests(monkeypatch, markets=markets, books=books, histories=histories)

    summary = run_maker_carry_study(cfg)

    assert [row["net_usd_per_day"] for row in summary["capital_curve"]] == [0, 0, 0, 0, 0]
    assert summary["capital_for_100_per_month"] is None
    assert summary["portfolio_net_carry_usd_per_day"] == 0


def test_resolution_high_risk_candidate_is_excluded_from_portfolio(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    markets = [_market("Will X officially announce a ceasefire deal?", "deal", 1000.0)]
    books = {"deal": _deep_book()}
    histories = {("deal", "1d"): _flat_history(200), ("deal", "1w"): _flat_history(200)}
    _fake_requests(monkeypatch, markets=markets, books=books, histories=histories)

    summary = run_maker_carry_study(cfg)

    row = read_csv_rows(cfg.output_root / "maker_carry" / "maker_carry_candidates.csv")[0]
    assert row["resolution_risk"] == "high"
    assert row["resolution_risk_class"] == "subjective_announce"
    assert row["estimate_quality"] == "book_and_history"
    assert float(row["net_carry_usd_per_day"]) > 0
    assert summary["candidates_resolution_high_risk"] == 1
    assert summary["portfolio_markets"] == 0
    sheet = (cfg.output_root / "maker_carry" / "maker_quote_sheet.md").read_text(encoding="utf-8")
    assert "Resolution risk" in sheet
    assert "if a proposal on a held market is disputed" in sheet


def test_supplementary_income_does_not_change_registered_gates_or_net_carry(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    raw = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    raw["maker_carry_study"]["target_net_usd_per_day"] = 10_000.0
    raw["maker_carry_study"]["holding_reward_apr"] = 3650.0
    raw["maker_carry_study"]["maker_rebate_share_by_fee_type"] = {"sports_fees_v2": 1_000.0}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    cfg = load_config(tmp_path / "config.yaml")
    markets = [_market("deep calm market", "calm", 1000.0)]
    books = {"calm": _deep_book()}
    histories = {("calm", "1d"): _flat_history(200), ("calm", "1w"): _flat_history(200)}
    # Quiet fills create measured band crossings for rebate estimation.
    prints = {
        "0xcalm": [
            {
                "price": 0.499,
                "size": 5,
                "side": "SELL",
                "timestamp": 600 + j * 60,
                "conditionId": "0xcalm",
                "asset": "calm",
            }
            for j in range(25)
        ]
    }
    _fake_requests(monkeypatch, markets=markets, books=books, histories=histories, prints=prints)

    summary = run_maker_carry_study(cfg)

    assert summary["portfolio_uncounted_supplementary_income_usd_per_day"] > summary["portfolio_net_carry_usd_per_day"]
    assert summary["clears_100_per_month_target"] is False
    assert summary["maker_gates"]["M_A_carry_evidence"]["state"] == "pending"
    assert "uncounted income" in summary["assumptions"]["supplementary_income"]
    sheet = (cfg.output_root / "maker_carry" / "maker_quote_sheet.md").read_text(encoding="utf-8")
    assert "NOT included in gates or net carry" in sheet


def test_markout_charges_empirical_pickoffs_and_gates_track_evidence(tmp_path, monkeypatch):
    """WO-36 step 2: prints that swept through our quote level are charged at
    their measured markout (queue-share weighted), and the charge wins when it
    exceeds the bar-based windows. Gates: one good day is never enough."""
    cfg = _config(tmp_path)
    markets = [_market("calm bars, hostile prints", "hostile", 1000.0)]
    books = {"hostile": _deep_book()}
    # Mids: 0.5 until t=12000, then 0.45. The fast bar window sees ONE 5c move
    # (excess 3.5c x 100 shares over ~0.21 days ~= $16.9/day). The prints see
    # sixty fills sweep our bid just before the drop: 60 x 3.5c x 100 shares x
    # queue share 100/20100, over the floored 1-hour span ~= $25/day - the
    # empirical charge must WIN the max().
    dropped = [{"t": i * 60, "p": (0.5 if i * 60 < 12000 else 0.45)} for i in range(300)]
    histories = {("hostile", "1d"): dropped, ("hostile", "1w"): _flat_history(300)}
    prints = {
        "0xhostile": [
            {
                "price": 0.484,
                "size": 100,
                "side": "SELL",
                "timestamp": 11700 + j * 5,
                "conditionId": "0xhostile",
                "asset": "hostile",
            }
            for j in range(60)
        ]
    }
    _fake_requests(monkeypatch, markets=markets, books=books, histories=histories, prints=prints)

    summary = run_maker_carry_study(cfg)

    row = read_csv_rows(cfg.output_root / "maker_carry" / "maker_carry_candidates.csv")[0]
    assert row["markout_measured"] == "True"
    markout_charge = float(row["adverse_usd_per_day_markout"])
    assert markout_charge > float(row["adverse_usd_per_day_1min_24h"])
    assert float(row["adverse_selection_usd_per_day"]) == markout_charge
    # Gates: M-B can pass on measured markout, M-A must stay pending on run 1.
    gates = summary["maker_gates"]
    assert gates["M_B_adverse_realism"]["state"] in {"pass", "pending"}
    assert gates["M_A_carry_evidence"]["state"] == "pending"
    assert gates["maker_verdict"] == "insufficient_evidence"


# --- WO-111: anchor-safe per-day portfolio-membership sidecar ------------------

_MC_HISTORY_FIELDS = {
    "generated_at_utc",
    "share_model",
    "universe_scan_mode",
    "universe_rewarded_markets",
    "universe_pot_usd_per_day",
    "portfolio_markets",
    "portfolio_capital_usd",
    "portfolio_net_carry_usd_per_day",
    "portfolio_markout_measured",
    "top_portfolio_market",
    "top_portfolio_question",
    "clears_100_per_month_target",
}


def _members_rows(cfg):
    return read_csv_rows(cfg.output_root / "maker_carry" / "maker_carry_portfolio_members.csv")


def _expected_members(summary):
    return [
        {
            "condition_id": str(entry.get("condition_id") or ""),
            "markout_measured": bool(entry.get("markout_measured")),
        }
        for entry in summary["portfolio"]
    ]


def test_wo119_members_sidecar_appends_without_rewriting_prefix(tmp_path, monkeypatch):
    # WO-119: the WO-111 sidecar is append_only-enrolled, so a second study
    # run must extend the file byte-for-byte - the old read->append->write_csv
    # full rewrite could re-serialise anchored history (the WO-115 class).
    cfg = _config(tmp_path)
    markets = [_market("deep calm market", "calm", 1000.0)]
    books = {"calm": _deep_book()}
    histories = {("calm", "1d"): _flat_history(200), ("calm", "1w"): _flat_history(200)}
    _fake_requests(monkeypatch, markets=markets, books=books, histories=histories)
    members_path = cfg.output_root / "maker_carry" / "maker_carry_portfolio_members.csv"

    run_maker_carry_study(cfg)
    first_bytes = members_path.read_bytes()
    run_maker_carry_study(cfg)
    second_bytes = members_path.read_bytes()

    assert second_bytes.startswith(first_bytes)
    assert len(_members_rows(cfg)) == 2


def test_wo111_sidecar_mirrors_unmeasured_portfolio_and_leaves_history_untouched(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    markets = [_market("deep calm market", "calm", 1000.0)]
    books = {"calm": _deep_book()}
    histories = {("calm", "1d"): _flat_history(200), ("calm", "1w"): _flat_history(200)}
    _fake_requests(monkeypatch, markets=markets, books=books, histories=histories)

    summary = run_maker_carry_study(cfg)

    rows = _members_rows(cfg)
    assert len(rows) == 1
    newest = json.loads(rows[-1]["portfolio_members"])
    # Sidecar mirrors the run's portfolio exactly, built from the SAME list that
    # feeds the aggregate flag.
    assert newest == _expected_members(summary)
    # Calm market has no prints -> markout unmeasured -> aggregate False.
    assert newest == [{"condition_id": "0xcalm", "markout_measured": False}]
    assert summary["portfolio_markout_measured"] is False
    # Aggregate/sidecar consistency invariant.
    assert summary["portfolio_markout_measured"] == all(m["markout_measured"] for m in newest)
    # Sidecar row correlates with the history row by generated_at_utc.
    history = read_csv_rows(cfg.output_root / "maker_carry" / "maker_carry_history.csv")
    assert rows[-1]["generated_at_utc"] == history[-1]["generated_at_utc"]
    # Anchor-safety: maker_carry_history.csv must NOT gain a portfolio_members
    # column (its append_only anchor prefix stays byte-identical).
    assert "portfolio_members" not in history[0]
    assert set(history[0].keys()) == _MC_HISTORY_FIELDS


def test_wo111_sidecar_records_measured_members(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    markets = [_market("deep calm market", "calm", 1000.0)]
    books = {"calm": _deep_book()}
    histories = {("calm", "1d"): _flat_history(200), ("calm", "1w"): _flat_history(200)}
    # 25 quiet prints inside the band edge: markout MEASURED, zero loss, so the
    # market still sizes into the portfolio with markout_measured=True.
    prints = {
        "0xcalm": [
            {
                "price": 0.499,
                "size": 5,
                "side": "SELL",
                "timestamp": 600 + j * 60,
                "conditionId": "0xcalm",
                "asset": "calm",
            }
            for j in range(25)
        ]
    }
    _fake_requests(monkeypatch, markets=markets, books=books, histories=histories, prints=prints)

    summary = run_maker_carry_study(cfg)

    newest = json.loads(_members_rows(cfg)[-1]["portfolio_members"])
    assert newest == _expected_members(summary)
    assert summary["portfolio"], "expected a sized portfolio for the measured case"
    assert all(m["markout_measured"] for m in newest)
    assert summary["portfolio_markout_measured"] is True
    assert newest[0]["condition_id"] == "0xcalm"


def test_wo111_sidecar_empty_portfolio_records_empty_list(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    markets = [_market("in-game thin book", "thin", 2000.0)]
    books = {"thin": {"bids": [{"price": "0.49", "size": "10"}], "asks": [{"price": "0.51", "size": "10"}]}}
    histories = {("thin", "1d"): _flat_history(200), ("thin", "1w"): _flat_history(200)}
    _fake_requests(monkeypatch, markets=markets, books=books, histories=histories)

    summary = run_maker_carry_study(cfg)

    assert summary["portfolio_markets"] == 0
    rows = _members_rows(cfg)
    assert rows[-1]["portfolio_members"] == "[]"
    assert json.loads(rows[-1]["portfolio_members"]) == []
    assert summary["portfolio_markout_measured"] is False


# --- P3-3: red-team maker-carry ledger write-lock ------------------------------


def _calm_market_requests(monkeypatch) -> None:
    markets = [_market("deep calm market", "calm", 1000.0)]
    books = {"calm": _deep_book()}
    histories = {("calm", "1d"): _flat_history(200), ("calm", "1w"): _flat_history(200)}
    _fake_requests(monkeypatch, markets=markets, books=books, histories=histories)


def test_p3_3_ledger_commit_reports_committed_and_appends(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    _calm_market_requests(monkeypatch)

    summary = run_maker_carry_study(cfg)

    assert summary["ledger_commit"]["status"] == "committed"
    assert summary["ledger_commit"]["lock"] == MAKER_CARRY_LEDGER_LOCK
    assert len(read_csv_rows(cfg.output_root / "maker_carry" / "maker_carry_history.csv")) == 1
    assert len(_members_rows(cfg)) == 1


def test_p3_3_skips_ledger_append_when_commit_flock_is_held(tmp_path, monkeypatch):
    # Fail-safe: if a concurrent study run already holds the ledger-commit flock,
    # this run must SKIP its append rather than clobber the ledger, and (#346 Codex P1)
    # force its maker gates pending so no evidence-unbacked pass is published. The
    # per-run snapshot artifacts still complete.
    cfg = _config(tmp_path)
    _calm_market_requests(monkeypatch)

    out_root = cfg.output_root / "maker_carry"
    out_root.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(_maker_carry_ledger_lock_path(out_root)), os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # a concurrent holder (separate fd)
    try:
        summary = run_maker_carry_study(cfg)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    assert summary["ledger_commit"]["status"] == "skipped_lock_held"
    assert read_csv_rows(out_root / "maker_carry_history.csv") == []
    assert read_csv_rows(out_root / "maker_carry_portfolio_members.csv") == []
    published = read_json(out_root / "maker_carry_study.json")
    assert published["ledger_commit"]["status"] == "skipped_lock_held"
    assert published["maker_gates"]["maker_verdict"] == "insufficient_evidence"


def test_p3_3_commit_re_reads_freshest_rows_inside_the_lock(tmp_path, monkeypatch):
    # The race the lock closes: a second run that read the history BEFORE a
    # concurrent run committed must not clobber that concurrent row. Proven by
    # injecting a concurrent append AFTER this run's early read but BEFORE it takes
    # the lock; the in-lock re-read must pick it up so the final ledger keeps it.
    cfg = _config(tmp_path)
    _calm_market_requests(monkeypatch)
    history_path = cfg.output_root / "maker_carry" / "maker_carry_history.csv"

    # Run 1 establishes the ledger with the current schema.
    monkeypatch.setattr(maker_carry_study, "now_utc", lambda: "2026-07-05T08:00:00Z")
    run_maker_carry_study(cfg)
    assert len(read_csv_rows(history_path)) == 1

    real_flock = maker_carry_study._maker_carry_ledger_flock

    @contextmanager
    def _racing_flock(out_root):
        # Simulate a concurrent run committing a row in the window between run 2's
        # early read and its own locked commit.
        cols = csv_columns(history_path)
        concurrent = read_csv_rows(history_path)
        concurrent.append({"generated_at_utc": "2026-07-02T00:00:00Z"})
        write_csv(history_path, concurrent, fieldnames=cols)
        with real_flock(out_root) as have_lock:
            yield have_lock

    monkeypatch.setattr(maker_carry_study, "_maker_carry_ledger_flock", _racing_flock)
    monkeypatch.setattr(maker_carry_study, "now_utc", lambda: "2026-07-06T08:00:00Z")
    summary = run_maker_carry_study(cfg)

    stamps = [row["generated_at_utc"] for row in read_csv_rows(history_path)]
    assert summary["ledger_commit"]["status"] == "committed"
    assert "2026-07-05T08:00:00Z" in stamps  # run 1
    assert "2026-07-02T00:00:00Z" in stamps  # concurrent row survived (not clobbered)
    assert "2026-07-06T08:00:00Z" in stamps  # run 2 appended on top
    assert len(stamps) == 3


def test_p3_3_force_pending_only_downgrades_pass_gates_and_verdict():
    # #346 Codex P1: an uncommitted run must never publish an evidence-unbacked pass.
    summary = {
        "maker_gates": {
            "M_A_carry_evidence": {"state": "pass", "runs_at_or_above_target": 7},
            "M_B_adverse_realism": {"state": "pass"},
            "M_C_payout_floor": {"state": "pass_by_construction"},
            "maker_verdict": "evidence_supported_pending_human_decision",
        }
    }

    maker_carry_study._force_maker_gates_pending_uncommitted(summary)

    mg = summary["maker_gates"]
    assert mg["M_A_carry_evidence"]["state"] == "pending"
    assert mg["M_A_carry_evidence"]["uncommitted_ledger_downgrade"] is True
    assert mg["M_B_adverse_realism"]["state"] == "pending"
    assert mg["M_C_payout_floor"]["state"] == "pass_by_construction"  # untouched by construction
    assert mg["maker_verdict"] == "insufficient_evidence"


def test_p3_3_members_written_before_history_so_crash_leaves_no_orphan_history(tmp_path, monkeypatch):
    # #346 Codex P2: the members sidecar is committed BEFORE the history row, so a
    # failure mid-pair leaves at most an orphan members row and NEVER a counted history
    # row without membership evidence (fail-closed for a future per-market recompute).
    cfg = _config(tmp_path)
    _calm_market_requests(monkeypatch)
    history_path = cfg.output_root / "maker_carry" / "maker_carry_history.csv"

    real_write_csv = maker_carry_study.write_csv

    def _write_csv(path, rows, **kwargs):
        if Path(path) == history_path:
            raise OSError("simulated failure before the history write completes")
        return real_write_csv(path, rows, **kwargs)

    monkeypatch.setattr(maker_carry_study, "write_csv", _write_csv)

    try:
        run_maker_carry_study(cfg)
    except OSError:
        pass  # the simulated write failure propagates; the invariant is what we assert

    assert len(_members_rows(cfg)) == 1  # members landed first
    assert read_csv_rows(history_path) == []  # no counted history row without membership


def test_wo111_sidecar_appends_forward_only_and_round_trips(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    markets = [_market("deep calm market", "calm", 1000.0)]
    books = {"calm": _deep_book()}
    histories = {("calm", "1d"): _flat_history(200), ("calm", "1w"): _flat_history(200)}
    _fake_requests(monkeypatch, markets=markets, books=books, histories=histories)

    run_maker_carry_study(cfg)
    run_maker_carry_study(cfg)

    rows = _members_rows(cfg)
    assert len(rows) == 2  # forward-only: one row appended per run, never rewritten in place
    for row in rows:
        parsed = json.loads(row["portfolio_members"])  # comma-bearing JSON survives CSV quoting
        assert parsed == [{"condition_id": "0xcalm", "markout_measured": False}]
    history = read_csv_rows(cfg.output_root / "maker_carry" / "maker_carry_history.csv")
    assert [r["generated_at_utc"] for r in rows] == [r["generated_at_utc"] for r in history]


def test_markout_unmeasured_when_no_print_has_a_horizon_mid() -> None:
    # #290 review P1: reaching markout_min_prints is not a measurement. If no
    # print has both an on-time AND a horizon mid (here prints sit far past the
    # series, so mid_later is None for all), there is no observed markout, and
    # _markout_adverse must return None so markout_measured stays False and the
    # day cannot count toward M-A/M-B on absent evidence.
    settings = {"markout_min_prints": 3, "markout_horizon_minutes": 30}
    series = [(1000.0, 0.5), (1060.0, 0.5), (1120.0, 0.5)]
    prints = [
        {"stamp": 100000.0 + i * 60, "price": 0.5, "size": 5, "side": "SELL"}
        for i in range(3)
    ]

    result = maker_carry_study._markout_adverse(
        settings,
        prints,
        series,
        quote_distance=0.01,
        quote_size=5.0,
        depth={"bid": 100.0, "ask": 100.0},
    )

    assert result is None


def test_distinct_days_require_measured_markout() -> None:
    # Red-team #283 regression: a day whose net cleared target only because its
    # markout was UNMEASURED (isolated prints below the minimum -> adverse leg
    # dropped from max(charges) -> adverse 0 -> inflated net) must not count
    # toward M-A. A measured day counts; a legacy row predating the field fails
    # closed (does not count).
    target = 3.0
    runs = [
        {
            "generated_at_utc": "2026-07-17T10:00:00Z",
            "portfolio_net_carry_usd_per_day": "5.0",
            "share_model": "published_v2",
            "portfolio_markout_measured": "True",
        },
        {
            "generated_at_utc": "2026-07-18T10:00:00Z",
            "portfolio_net_carry_usd_per_day": "5.0",
            "share_model": "published_v2",
            "portfolio_markout_measured": "False",
        },
        {
            "generated_at_utc": "2026-07-16T10:00:00Z",
            "portfolio_net_carry_usd_per_day": "5.0",
            "share_model": "published_v2",
        },
    ]

    days = maker_carry_study._distinct_days_at_target(
        runs, target, current_day="", latest_at_target=False
    )

    assert days == {"2026-07-17"}


def test_gate_a_passes_only_after_enough_distinct_days_at_target(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    markets = [_market("deep calm market", "calm", 1000.0)]
    books = {"calm": _deep_book()}
    histories = {("calm", "1d"): _flat_history(200), ("calm", "1w"): _flat_history(200)}
    # 25 quiet prints inside the band edge: markout measured, zero loss.
    prints = {
        "0xcalm": [
            {
                "price": 0.499,
                "size": 5,
                "side": "SELL",
                "timestamp": 600 + j * 60,
                "conditionId": "0xcalm",
                "asset": "calm",
            }
            for j in range(25)
        ]
    }
    _fake_requests(monkeypatch, markets=markets, books=books, histories=histories, prints=prints)

    # Registered clarification: intraday re-runs never fast-forward the clock.
    monkeypatch.setattr(maker_carry_study, "now_utc", lambda: "2026-07-10T08:00:00Z")
    for _ in range(7):
        same_day = run_maker_carry_study(cfg)
    assert len(read_csv_rows(cfg.output_root / "maker_carry" / "maker_carry_history.csv")) == 7
    assert same_day["maker_gates"]["M_A_carry_evidence"]["runs_at_or_above_target"] == 1
    assert same_day["maker_gates"]["M_A_carry_evidence"]["state"] == "pending"

    last = None
    for day in range(11, 17):  # six more distinct UTC days -> 7 total
        monkeypatch.setattr(maker_carry_study, "now_utc", lambda d=day: f"2026-07-{d}T08:00:00Z")
        last = run_maker_carry_study(cfg)
    gates = last["maker_gates"]
    assert gates["M_A_carry_evidence"]["runs_at_or_above_target"] == 7
    assert gates["M_A_carry_evidence"]["counting"] == "distinct_utc_days"
    assert gates["M_A_carry_evidence"]["state"] == "pass"
    # M-B.1: with no Tier-0 replay on disk, M-B is now pending (the closed hole).
    assert gates["M_B_adverse_realism"]["state"] == "pending"
    assert gates["M_B_adverse_realism"]["mb1_tier0_coverage_sufficient"] is False
    assert gates["maker_verdict"] == "insufficient_evidence"

    # Real pipeline runs the Tier-0 replay between study cycles. Write a
    # qualifying replay for the exact portfolio market(s), then re-run: M-B
    # passes and the verdict flips to evidence-supported.
    cov_rows = [
        {
            "condition_id": row["condition_id"],
            "asset_id": row.get("token_id", ""),
            "last_in_queue_evaluable_opportunities": 40,
            "confirmed_fills": 15,
            "coverage_ratio": 0.95,
            "simulation_to_reality_haircut": 0.7,
            "by_horizon": {
                "5m": {"windows_covered": 15},
                "15m": {"windows_covered": 14},
                "60m": {"windows_covered": 12},
            },
        }
        for row in last["portfolio"]
    ]
    write_json(
        cfg.output_root / "maker_carry" / "maker_fill_replay.json",
        {"generated_at_utc": "2026-07-16T06:00:00Z", "primary_book_source": "official", "per_market_coverage": cov_rows},
    )
    last = run_maker_carry_study(cfg)
    gates = last["maker_gates"]
    assert gates["M_A_carry_evidence"]["state"] == "pass"
    assert gates["M_B_adverse_realism"]["state"] == "pass"
    assert gates["M_B_adverse_realism"]["mb1_tier0_coverage_sufficient"] is True
    assert gates["maker_verdict"] == "evidence_supported_pending_human_decision"
    # Even a supported verdict never trades; the sheet says so in print.
    assert last["paper_trading_invoked"] is False
    sheet = (cfg.output_root / "maker_carry" / "maker_quote_sheet.md").read_text(encoding="utf-8")
    assert "NOT ADVICE" in sheet
    assert "places NO orders" in sheet


def test_distance_sweep_picks_the_net_maximising_fraction(tmp_path, monkeypatch):
    """Per-market optimisation: bars drift 1.2c/bar, so quoting at 0.25 x 3c
    (0.75c) is picked off every bar, while 0.5 x 3c (1.5c) rides above the
    noise; 0.75 x 3c survives too but earns a quadratically smaller share.
    The sweep must keep the 0.5 row."""
    cfg = _config(tmp_path)
    raw = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    raw["maker_carry_study"]["quote_distance_fractions"] = [0.25, 0.5, 0.75]
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    from polymarket_predictive_engine.config import load_config as _load

    cfg = _load(tmp_path / "config.yaml")
    markets = [_market("drifty market", "drifty", 1000.0)]
    books = {"drifty": _deep_book()}
    # Mid oscillates +/-1.2c per minute bar around 0.5.
    zigzag = [{"t": i * 60, "p": 0.5 + (0.012 if i % 2 else 0.0)} for i in range(300)]
    histories = {("drifty", "1d"): zigzag, ("drifty", "1w"): _flat_history(300)}
    _fake_requests(monkeypatch, markets=markets, books=books, histories=histories)

    run_maker_carry_study(cfg)

    row = read_csv_rows(cfg.output_root / "maker_carry" / "maker_carry_candidates.csv")[0]
    assert float(row["quote_distance_fraction"]) == 0.5
    assert float(row["adverse_usd_per_day_1min_24h"]) == 0.0


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


def test_legacy_share_model_days_never_count_toward_gate_a(tmp_path, monkeypatch):
    # 2026-07-11 dated tightening: the Jul 10 legacy-model day cleared target
    # under a share model later shown to overstate shares 3-9x. Only
    # published_v2 days may count, or M-A could pass on 6 honest days plus
    # one discredited one.
    cfg = _config(tmp_path)
    markets = [_market("deep calm market", "calm", 1000.0)]
    books = {"calm": _deep_book()}
    histories = {("calm", "1d"): _flat_history(200), ("calm", "1w"): _flat_history(200)}
    prints = {
        "0xcalm": [
            {
                "price": 0.499,
                "size": 5,
                "side": "SELL",
                "timestamp": 600 + j * 60,
                "conditionId": "0xcalm",
                "asset": "calm",
            }
            for j in range(25)
        ]
    }
    _fake_requests(monkeypatch, markets=markets, books=books, histories=histories, prints=prints)

    # Seed a legacy-era at-target day (share_model empty, hugely over target).
    out_root = cfg.output_root / "maker_carry"
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "maker_carry_history.csv").write_text(
        "generated_at_utc,share_model,universe_rewarded_markets,universe_pot_usd_per_day,"
        "portfolio_markets,portfolio_capital_usd,portfolio_net_carry_usd_per_day,clears_100_per_month_target\n"
        "2026-07-10T08:14:00Z,,3,1000.0,3,500.0,58.99,True\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(maker_carry_study, "now_utc", lambda: "2026-07-11T08:00:00Z")
    summary = run_maker_carry_study(cfg)

    gate_a = summary["maker_gates"]["M_A_carry_evidence"]
    # Today's published_v2 run counts; the legacy day must not.
    assert gate_a["runs_at_or_above_target"] == 1
    assert gate_a["share_model_scope"] == "published_v2_only"
    assert gate_a["state"] == "pending"


def _ma_run(day: str, net: float, *, model: str = "published_v2", hour: str = "12:00:00", markout_measured: str = "True") -> dict[str, Any]:
    return {"generated_at_utc": f"{day}T{hour}Z", "portfolio_net_carry_usd_per_day": net, "share_model": model, "portfolio_markout_measured": markout_measured}


def test_ma_nonfinite_net_carry_does_not_bank_a_day() -> None:
    # Red-team P3-1: a +inf/-inf/NaN net-carry row must NOT count toward M-A.
    # NaN already failed the >= comparison, but +inf silently passed it before the
    # finite guard; treat any non-finite net carry as not-at-target (tighten).
    for bad in (float("inf"), float("-inf"), float("nan")):
        runs = [_ma_run("2026-07-10", bad)]
        days = maker_carry_study._distinct_days_at_target(
            runs, 3.33, current_day="", latest_at_target=False
        )
        assert days == set(), bad


def test_finite_at_target_guard_is_robust_to_nonpositive_target() -> None:
    # Codex #342 review (A + B): the shared guard used for BOTH the prior-run day
    # counter and the current-run latest_at_target derivation must reject
    # non-finite values regardless of the sign of target. A zero/negative target
    # override must not let an `or 0.0`-style fallback bank a NaN/-inf day.
    guard = maker_carry_study._finite_at_target
    assert guard(5.0, 3.33) is True
    assert guard(1.0, 3.33) is False
    for bad in (float("inf"), float("-inf"), float("nan"), None, "", "n/a"):
        for target in (3.33, 0.0, -1.0, -1e9):
            assert guard(bad, target) is False, (bad, target)
    # A genuine finite value still counts at/above a non-positive target.
    assert guard(0.0, 0.0) is True
    assert guard(-2.0, -5.0) is True


def test_ma_intraday_spike_does_not_bank_a_day() -> None:
    # Maker-gate amendment M-A.1: a day counts only if its LAST published_v2
    # run met target. A noon spike that faded by the last run must not count.
    prior = [_ma_run("2026-07-11", 10.0, hour="12:00:00"), _ma_run("2026-07-11", 1.0, hour="20:00:00")]
    days = maker_carry_study._distinct_days_at_target(prior, 3.33, current_day="2026-07-12", latest_at_target=False)
    assert days == set()


def test_ma_last_run_at_target_counts_the_day() -> None:
    prior = [_ma_run("2026-07-11", 1.0, hour="12:00:00"), _ma_run("2026-07-11", 5.0, hour="20:00:00")]
    days = maker_carry_study._distinct_days_at_target(prior, 3.33, current_day="2026-07-12", latest_at_target=False)
    assert days == {"2026-07-11"}


def test_ma_today_governed_by_current_run_not_earlier_spike() -> None:
    prior = [_ma_run("2026-07-12", 9.0, hour="08:00:00")]
    assert maker_carry_study._distinct_days_at_target(prior, 3.33, current_day="2026-07-12", latest_at_target=False) == set()
    assert maker_carry_study._distinct_days_at_target(prior, 3.33, current_day="2026-07-12", latest_at_target=True) == {"2026-07-12"}


def test_ma_legacy_model_day_excluded() -> None:
    prior = [_ma_run("2026-07-10", 8.0, model="legacy", hour="20:00:00")]
    assert maker_carry_study._distinct_days_at_target(prior, 3.33, current_day="2026-07-12", latest_at_target=False) == set()


# --- M-B.1 amendment: own Tier-0 coverage requirement -----------------------

_MB_STUDY_STAMP = "2026-07-18T12:00:00Z"


def _mb_cov(condition_id: str = "0xc", token_id: str = "0xt", **o: Any) -> dict[str, Any]:
    return {
        "condition_id": condition_id,
        "asset_id": token_id,
        "last_in_queue_evaluable_opportunities": o.get("evaluable", 35),
        "confirmed_fills": o.get("confirmed", 12),
        "coverage_ratio": o.get("coverage", 0.90),
        "simulation_to_reality_haircut": o.get("haircut", 0.8),
        "by_horizon": o.get(
            "by_horizon",
            {"5m": {"windows_covered": 12}, "15m": {"windows_covered": 11}, "60m": {"windows_covered": 10}},
        ),
    }


def _mb_replay(rows: list[dict[str, Any]], *, stamp: str = "2026-07-18T06:00:00Z", source: str = "official") -> dict[str, Any]:
    return {"generated_at_utc": stamp, "primary_book_source": source, "per_market_coverage": rows}


_MB_PORT = [{"condition_id": "0xc", "token_id": "0xt", "markout_measured": True}]


def _mb_ok(replay: dict[str, Any], portfolio=_MB_PORT, **settings: Any) -> bool:
    return maker_carry_study._mb_tier0_coverage_sufficient(
        replay, portfolio, study_generated_at=_MB_STUDY_STAMP, settings=settings
    )


def test_mb1_sufficient_coverage_passes() -> None:
    assert _mb_ok(_mb_replay([_mb_cov()])) is True


def test_mb1_no_replay_fails_closed() -> None:
    # The exact hole being closed: markout measured but zero Tier-0 coverage.
    assert _mb_ok({}) is False


def test_mb1_low_coverage_ratio_fails() -> None:
    assert _mb_ok(_mb_replay([_mb_cov(coverage=0.5)])) is False


def test_mb1_stale_replay_fails() -> None:
    # 48h old vs the 26h registered bound.
    assert _mb_ok(_mb_replay([_mb_cov()], stamp="2026-07-16T12:00:00Z")) is False


def test_mb1_missing_market_row_fails() -> None:
    assert _mb_ok(_mb_replay([_mb_cov(condition_id="0xother", token_id="0xother")])) is False


def test_mb1_non_official_source_fails() -> None:
    assert _mb_ok(_mb_replay([_mb_cov()], source="archive")) is False


def test_mb1_insufficient_markout_windows_fail() -> None:
    thin = _mb_cov(by_horizon={"5m": {"windows_covered": 5}, "15m": {"windows_covered": 11}, "60m": {"windows_covered": 10}})
    assert _mb_ok(_mb_replay([thin])) is False


def test_mb1_haircut_above_ceiling_fails() -> None:
    assert _mb_ok(_mb_replay([_mb_cov(haircut=1.5)])) is False


def test_mb1_every_portfolio_market_must_be_covered() -> None:
    portfolio = [
        {"condition_id": "0xc", "token_id": "0xt", "markout_measured": True},
        {"condition_id": "0xd", "token_id": "0xu", "markout_measured": True},
    ]
    # Only the first market has coverage -> M-B stays pending.
    assert _mb_ok(_mb_replay([_mb_cov()]), portfolio=portfolio) is False
    assert _mb_ok(_mb_replay([_mb_cov(), _mb_cov("0xd", "0xu")]), portfolio=portfolio) is True


def test_mb1_override_cannot_loosen_replay_age() -> None:
    # A config that tries to WIDEN the age bound is ignored: a 48h replay still
    # fails the registered 26h ceiling.
    assert _mb_ok(_mb_replay([_mb_cov()], stamp="2026-07-16T12:00:00Z"), mb_tier0_max_replay_age_seconds=999999) is False


def test_mb1_override_can_only_tighten_min_confirmed() -> None:
    # Raising the confirmed-fill minimum is applied: 12 fills fails at 100.
    assert _mb_ok(_mb_replay([_mb_cov()]), mb_tier0_min_confirmed_fills=100) is False


def test_mb1_non_finite_coverage_fails_closed() -> None:
    # #262: a NaN coverage/haircut must fail the fail-closed gate, not slip
    # through because NaN comparisons are all False.
    assert _mb_ok(_mb_replay([_mb_cov(coverage="nan")])) is False
    assert _mb_ok(_mb_replay([_mb_cov(haircut="nan")])) is False


def test_mb1_zero_valued_max_haircut_override_is_honored() -> None:
    # #262: a stricter `mb_tier0_max_haircut: 0` must bind (a 0.8 haircut then
    # fails), not be treated as invalid and restored to the registered 1.0.
    assert _mb_ok(_mb_replay([_mb_cov(haircut=0.8)]), mb_tier0_max_haircut=0) is False


# --- WO-113: measurability-aware maker portfolio (eligibility + stickiness) ---


def _wo113_settings(**over: Any) -> dict[str, Any]:
    settings = {
        "max_size_multiple": 5,
        "min_daily_payout_usd": 1.0,
        "maker_switch_margin_frac": 0.25,
        "maker_max_hold_days": 30,
        "maker_min_book_history_hours": 48.0,
        "maker_min_book_snapshots": 100,
    }
    settings.update(over)
    return settings


def _wo113_candidate(cid: str, carry: float, hours: float, snaps: int, capital_usd: float = 20.0) -> dict[str, Any]:
    return {
        "condition_id": cid,
        "question": f"question {cid}",
        "net_carry_usd_per_day": carry,
        "estimate_quality": "book_and_history",
        "band_eligible": True,
        "resolution_risk": "medium",
        "book_history_hours": hours,
        "book_snapshot_count": snaps,
        "our_score_per_side": 10.0,
        "estimated_reward_share": 0.02,
        "pot_usd_per_day": 100.0,
        "adverse_selection_usd_per_day": 0.5,
        "capital_usd": capital_usd,
        "rewards_min_size_shares": 100,
        "quote_distance": 0.01,
        "quote_distance_fraction": 0.5,
    }


def test_wo113_book_history_depth_reads_span_and_count(tmp_path):
    import gzip

    books = tmp_path / "outputs" / "maker_carry" / "official_books"
    books.mkdir(parents=True)
    rows = ["condition_id,collected_at_utc"]
    rows += [f"0xdeep,2026-07-{18 + i:02d}T00:00:00Z" for i in range(3)]  # 3 snaps over 48h
    (books / "0xdeep.csv.gz").write_bytes(gzip.compress(("\n".join(rows) + "\n").encode()))

    hours, count = _book_history_depth(tmp_path / "outputs" / "maker_carry", "0xdeep")
    assert count == 3
    assert abs(hours - 48.0) < 1e-6
    # Fail-safe: a missing archive reports zero depth, never a phantom pass.
    assert _book_history_depth(tmp_path / "outputs" / "maker_carry", "0xmissing") == (0.0, 0)


def test_wo113_measurement_eligible_gate():
    settings = _wo113_settings()  # 48h / 100 snapshots
    assert _measurement_eligible({"book_history_hours": 120.0, "book_snapshot_count": 300}, settings) is True
    assert _measurement_eligible({"book_history_hours": 5.0, "book_snapshot_count": 18}, settings) is False
    assert _measurement_eligible({}, settings) is False  # missing depth fails closed
    disabled = _wo113_settings(maker_min_book_history_hours=0, maker_min_book_snapshots=0)
    assert _measurement_eligible({}, disabled) is True  # both thresholds zero -> gate off


def test_wo113_incumbent_hold_reads_membership_sidecar(tmp_path):
    out_root = tmp_path / "outputs" / "maker_carry"
    out_root.mkdir(parents=True)
    write_csv(
        out_root / "maker_carry_portfolio_members.csv",
        [
            {"generated_at_utc": f"2026-07-{18 + i:02d}T08:00:00Z",
             "portfolio_members": json.dumps([{"condition_id": "0xA", "markout_measured": i == 2}])}
            for i in range(3)
        ],
        fieldnames=["generated_at_utc", "portfolio_members"],
    )
    incumbents, hold = _incumbent_hold(out_root)
    assert incumbents == {"0xA"}
    assert hold["0xA"] == 3  # three consecutive distinct UTC days


def test_wo113_fresh_market_excluded_even_when_carry_is_highest():
    settings = _wo113_settings()
    fresh = _wo113_candidate("0xfresh", carry=5.0, hours=5.0, snaps=18)  # top carry, no depth
    deep = _wo113_candidate("0xdeep", carry=1.0, hours=120.0, snaps=300)
    portfolio, _capital, _net = _size_portfolio(settings, [fresh, deep], 1000.0)
    cids = {entry["condition_id"] for entry in portfolio}
    assert "0xdeep" in cids
    assert "0xfresh" not in cids  # eligibility beats headline carry


def test_wo113_stickiness_retains_incumbent_within_margin():
    settings = _wo113_settings(maker_min_book_history_hours=0, maker_min_book_snapshots=0)  # isolate stickiness
    incumbent = _wo113_candidate("0xinc", carry=1.0, hours=0.0, snaps=0, capital_usd=300.0)
    challenger = _wo113_candidate("0xchal", carry=1.1, hours=0.0, snaps=0, capital_usd=300.0)  # +10% < 25%
    portfolio, _capital, _net = _size_portfolio(
        settings, [incumbent, challenger], 500.0, incumbents={"0xinc"}, incumbent_hold_days={"0xinc": 1}
    )
    assert [entry["condition_id"] for entry in portfolio] == ["0xinc"]  # retained despite lower carry


def test_wo113_stickiness_switches_when_challenger_clears_margin():
    settings = _wo113_settings(maker_min_book_history_hours=0, maker_min_book_snapshots=0)
    incumbent = _wo113_candidate("0xinc", carry=1.0, hours=0.0, snaps=0, capital_usd=300.0)
    challenger = _wo113_candidate("0xchal", carry=1.3, hours=0.0, snaps=0, capital_usd=300.0)  # +30% > 25%
    portfolio, _capital, _net = _size_portfolio(
        settings, [incumbent, challenger], 500.0, incumbents={"0xinc"}, incumbent_hold_days={"0xinc": 1}
    )
    assert [entry["condition_id"] for entry in portfolio] == ["0xchal"]


def test_wo113_stickiness_bonus_drops_after_max_hold():
    settings = _wo113_settings(maker_min_book_history_hours=0, maker_min_book_snapshots=0, maker_max_hold_days=30)
    incumbent = _wo113_candidate("0xinc", carry=1.0, hours=0.0, snaps=0, capital_usd=300.0)
    challenger = _wo113_candidate("0xchal", carry=1.1, hours=0.0, snaps=0, capital_usd=300.0)
    portfolio, _capital, _net = _size_portfolio(
        settings, [incumbent, challenger], 500.0, incumbents={"0xinc"}, incumbent_hold_days={"0xinc": 30}
    )
    assert [entry["condition_id"] for entry in portfolio] == ["0xchal"]  # held >= max -> bonus dropped
