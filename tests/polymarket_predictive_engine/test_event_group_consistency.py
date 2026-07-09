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


def _leg(bid: float | None, ask: float | None, *, fees: bool = False) -> dict[str, Any]:
    leg: dict[str, Any] = {"closed": False, "feesEnabled": fees}
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
