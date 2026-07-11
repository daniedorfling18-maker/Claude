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
from polymarket_predictive_engine.utils import read_csv_rows, read_json, write_csv


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
        "question": question,
        "conditionId": f"0x{token}",
        "clobTokenIds": json.dumps([token, f"{token}-no"]),
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
        "bids": [{"price": f"{mid - 0.005:.3f}", "size": "20000"}],
        "asks": [{"price": f"{mid + 0.005:.3f}", "size": "20000"}],
    }


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

    # A second run appends to the trend ledger instead of overwriting it.
    run_maker_carry_study(cfg)
    assert len(read_csv_rows(cfg.output_root / "maker_carry" / "maker_carry_history.csv")) == 2


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
    prints = {"0xcalm": [{"price": 0.499, "size": 5, "side": "SELL", "timestamp": 600 + j * 60} for j in range(25)]}
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
            {"price": 0.484, "size": 100, "side": "SELL", "timestamp": 11700 + j * 5} for j in range(60)
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


def test_gate_a_passes_only_after_enough_distinct_days_at_target(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    markets = [_market("deep calm market", "calm", 1000.0)]
    books = {"calm": _deep_book()}
    histories = {("calm", "1d"): _flat_history(200), ("calm", "1w"): _flat_history(200)}
    # 25 quiet prints inside the band edge: markout measured, zero loss.
    prints = {"0xcalm": [{"price": 0.499, "size": 5, "side": "SELL", "timestamp": 600 + j * 60} for j in range(25)]}
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
    assert gates["M_B_adverse_realism"]["state"] == "pass"
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
