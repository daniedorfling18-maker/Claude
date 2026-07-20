"""WO-34 event-group sum-constraint detector: deviations must be charged live
taker fees, incomplete baskets must not be scored, and the ledger must accrue
persistence evidence across scans - all without any trading behaviour."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from polymarket_predictive_engine import event_group_consistency
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.event_group_consistency import scan_event_groups
from polymarket_predictive_engine.utils import read_csv_rows


def _config(tmp_path: Path):
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    raw["event_group_consistency"] = {
        "enabled": True,
        "event_pages": 1,
        "page_size": 100,
        "min_leg_count": 3,
        "deviation_threshold_per_basket": 0.002,
        "max_ledger_rows": 100000,
        "request_pause_seconds": 0,
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def _leg(bid: float | None, ask: float | None, *, fees: bool = False, token_id: str = "") -> dict[str, Any]:
    leg: dict[str, Any] = {"closed": False, "feesEnabled": fees}
    if token_id:
        leg["clobTokenIds"] = [token_id]
    if fees:
        leg["feeType"] = "sports_fees_v2"
    if bid is not None:
        leg["bestBid"] = bid
    if ask is not None:
        leg["bestAsk"] = ask
    return leg


def _event(slug: str, legs: list[dict[str, Any]], *, neg_risk: bool = True) -> dict[str, Any]:
    return {"slug": slug, "title": slug, "negRisk": neg_risk, "volume24hr": 1000, "markets": legs}


class _Response:
    def __init__(self, payload: Any):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


def _fake_requests(monkeypatch, events: list[dict[str, Any]]) -> None:
    def fake_get(url: str, params: dict[str, Any] | None = None, timeout: float | None = None):
        assert url.endswith("/events")
        return _Response(events if int((params or {}).get("offset", 0)) == 0 else [])

    monkeypatch.setattr(event_group_consistency.requests, "get", fake_get)


def _fake_requests_with_books(
    monkeypatch,
    events: list[dict[str, Any]],
    books: dict[str, Any],
) -> None:
    def fake_get(url: str, params: dict[str, Any] | None = None, timeout: float | None = None):
        params = params or {}
        if url.endswith("/events"):
            return _Response(events if int(params.get("offset", 0)) == 0 else [])
        if url.endswith("/book"):
            token_id = str(params.get("token_id") or "")
            raw = books[token_id]
            ask_levels = raw.get("asks", []) if isinstance(raw, dict) else raw
            bid_levels = raw.get("bids", [(0.01, 1)]) if isinstance(raw, dict) else [(0.01, 1)]
            asks = [{"price": price, "size": size} for price, size in ask_levels]
            bids = [{"price": price, "size": size} for price, size in bid_levels]
            return _Response({"asks": asks, "bids": bids})
        raise AssertionError(url)

    monkeypatch.setattr(event_group_consistency.requests, "get", fake_get)


def test_cheap_zero_fee_group_is_flagged_buy_side(tmp_path, monkeypatch):
    """Observed live at registration: a politics group (fees off) pricing to
    $0.955 on the ask side is a 4.5c/basket structural deviation."""
    cfg = _config(tmp_path)
    events = [_event("cheap-politics", [_leg(0.28, 0.30), _leg(0.28, 0.30), _leg(0.30, 0.355)])]
    _fake_requests(monkeypatch, events)

    summary = scan_event_groups(cfg)

    assert summary["neg_risk_groups_scanned"] == 1
    assert summary["flagged_deviations"] == 1
    assert summary["flagged_events"] == ["cheap-politics"]
    assert abs(summary["best_net_buy_all_yes"] - 0.045) < 1e-9
    assert summary["paper_trading_invoked"] is False
    assert summary["live_trading_invoked"] is False
    row = read_csv_rows(cfg.output_root / "event_group_consistency" / "event_group_deviations.csv")[0]
    assert row["flagged_side"] == "buy_all_yes"


def test_sports_fees_kill_a_sub_fee_deviation(tmp_path, monkeypatch):
    """A 1c gross buy-side gap on a sports group must NOT be flagged: the
    verified live schedule charges 0.03 x p x (1-p) per share per leg
    (~2c/basket at these prices), which exceeds the gross deviation."""
    cfg = _config(tmp_path)
    events = [_event("sports-near-miss", [_leg(0.30, 0.33, fees=True) for _ in range(3)])]
    _fake_requests(monkeypatch, events)

    summary = scan_event_groups(cfg)

    assert summary["neg_risk_groups_scanned"] == 1
    assert summary["flagged_deviations"] == 0
    # Gross 1 - 0.99 = +0.01; fees 3 x 0.03 x 0.33 x 0.67 ~= 0.0199.
    assert summary["best_net_buy_all_yes"] < 0


def test_incomplete_ask_side_scores_bid_side_only(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    # One leg has no real ask (1.0): buy-all-YES is not a basket. Bids are
    # complete and rich: sell side flags.
    events = [_event("rich-bids", [_leg(0.36, 0.40), _leg(0.36, 0.40), _leg(0.33, 1.0)])]
    _fake_requests(monkeypatch, events)

    summary = scan_event_groups(cfg)

    assert summary["groups_with_complete_ask_side"] == 0
    assert summary["groups_with_complete_bid_side"] == 1
    assert abs(summary["best_net_sell_all_yes"] - 0.05) < 1e-9
    row = read_csv_rows(cfg.output_root / "event_group_consistency" / "event_group_deviations.csv")[0]
    assert row["flagged_side"] == "sell_all_yes"
    assert row["sum_ask"] == ""


def test_non_negrisk_and_small_groups_are_skipped_and_ledger_accrues(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    events = [
        _event("not-negrisk", [_leg(0.28, 0.30)] * 3, neg_risk=False),
        _event("too-small", [_leg(0.28, 0.30)] * 2),
        _event("cheap-politics", [_leg(0.28, 0.30), _leg(0.28, 0.30), _leg(0.30, 0.355)]),
    ]
    _fake_requests(monkeypatch, events)

    scan_event_groups(cfg)
    summary = scan_event_groups(cfg)

    assert summary["neg_risk_groups_scanned"] == 1
    # Persistence evidence: the same deviation appended once per scan.
    assert summary["ledger_rows"] == 2


def test_flagged_event_depth_confirms_executable_buy_all_yes_edge(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    events = [
        _event(
            "cheap-with-depth",
            [
                _leg(0.28, 0.30, token_id="tok-a"),
                _leg(0.28, 0.30, token_id="tok-b"),
                _leg(0.30, 0.35, token_id="tok-c"),
            ],
        )
    ]
    _fake_requests_with_books(
        monkeypatch,
        events,
        {
            "tok-a": [(0.30, 100)],
            "tok-b": [(0.30, 60)],
            "tok-c": [(0.35, 50)],
        },
    )

    summary = scan_event_groups(cfg)

    assert summary["flagged_deviations"] == 1
    assert summary["flagged_with_executable_depth"] == 1
    assert summary["max_executable_basket_usd"] == 47.5
    row = read_csv_rows(cfg.output_root / "event_group_consistency" / "event_group_deviations.csv")[0]
    assert row["book_fetch_ok"] == "True"
    assert row["executable_basket_usd"] == "47.5"
    assert row["depth_weighted_net"] == "0.05"


def test_flagged_event_depth_stops_when_thin_books_kill_edge(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    events = [
        _event(
            "cheap-but-thin",
            [
                _leg(0.28, 0.30, token_id="tok-a"),
                _leg(0.28, 0.30, token_id="tok-b"),
                _leg(0.30, 0.35, token_id="tok-c"),
            ],
        )
    ]
    _fake_requests_with_books(
        monkeypatch,
        events,
        {
            "tok-a": [(0.30, 10), (0.60, 100)],
            "tok-b": [(0.30, 10), (0.60, 100)],
            "tok-c": [(0.35, 10), (0.60, 100)],
        },
    )

    summary = scan_event_groups(cfg)

    assert summary["flagged_with_executable_depth"] == 1
    assert summary["max_executable_basket_usd"] == 9.5
    row = read_csv_rows(cfg.output_root / "event_group_consistency" / "event_group_deviations.csv")[0]
    assert row["book_fetch_ok"] == "True"
    assert row["executable_basket_usd"] == "9.5"
    assert row["depth_weighted_net"] == "0.05"


def test_flagged_sell_all_yes_uses_bid_depth_for_every_leg(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    events = [
        _event(
            "rich-with-depth",
            [
                _leg(0.36, 0.40, token_id="tok-a"),
                _leg(0.36, 0.40, token_id="tok-b"),
                _leg(0.33, 0.40, token_id="tok-c"),
            ],
        )
    ]
    _fake_requests_with_books(
        monkeypatch,
        events,
        {
            "tok-a": {"bids": [(0.36, 100)]},
            "tok-b": {"bids": [(0.36, 60)]},
            "tok-c": {"bids": [(0.33, 50)]},
        },
    )

    summary = scan_event_groups(cfg)

    assert summary["flagged_deviations"] == 1
    assert summary["flagged_with_executable_depth"] == 1
    assert summary["max_executable_basket_usd"] == 52.5
    row = read_csv_rows(
        cfg.output_root / "event_group_consistency" / "event_group_deviations.csv"
    )[0]
    assert row["flagged_side"] == "sell_all_yes"
    assert row["book_fetch_ok"] == "True"
    assert row["executable_basket_usd"] == "52.5"
    assert row["depth_weighted_net"] == "0.05"
