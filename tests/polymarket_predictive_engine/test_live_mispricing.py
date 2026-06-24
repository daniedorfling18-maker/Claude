import csv

from pytest import approx

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.live_mispricing import evaluate_book, scan_live_mispricing


def test_buy_when_fair_beats_ask():
    ev = evaluate_book(0.70, 0.50, 0.55, min_edge=0.02)
    assert ev["action"] == "BUY_YES"
    assert ev["edge"] == approx(0.15)


def test_sell_when_fair_below_bid():
    ev = evaluate_book(0.30, 0.50, 0.55, min_edge=0.02)
    assert ev["action"] == "SELL_YES"
    assert ev["edge"] == approx(0.20)


def test_none_inside_tight_spread():
    ev = evaluate_book(0.525, 0.52, 0.53, min_edge=0.02)
    assert ev["action"] == "NONE"


def test_market_make_when_no_edge_and_wide_spread():
    ev = evaluate_book(0.50, 0.45, 0.55, min_edge=0.02, max_spread=0.10)
    assert ev["action"] == "MAKE"
    assert ev["mm_bid"] > 0.45 and ev["mm_ask"] < 0.55      # quote strictly inside the book
    assert ev["expected_capture_usdc"] > 0


def test_no_make_when_spread_exceeds_max():
    ev = evaluate_book(0.50, 0.40, 0.62, min_edge=0.02, max_spread=0.10)
    assert ev["action"] == "NONE"


def _write(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def test_scan_live_mispricing_joins_book_to_fair(tmp_path):
    cfg = EngineConfig(raw={"paths": {"output_root": str(tmp_path / "outputs")}}, path=tmp_path / "cfg.yaml")
    books = [
        {"asset_id": "A", "collected_at_utc": "2026-06-24T00:00:00Z", "market": "mA", "best_bid": "0.50", "best_ask": "0.55"},
        {"asset_id": "B", "collected_at_utc": "2026-06-24T00:00:00Z", "market": "mB", "best_bid": "0.50", "best_ask": "0.55"},
        {"asset_id": "C", "collected_at_utc": "2026-06-24T00:00:00Z", "market": "mC", "best_bid": "0.45", "best_ask": "0.55"},
        {"asset_id": "D", "collected_at_utc": "2026-06-24T00:00:00Z", "market": "mD", "best_bid": "0.50", "best_ask": "0.55"},
    ]
    _write(tmp_path / "outputs" / "polymarket_training" / "websocket_market_features.csv", books,
           ["asset_id", "collected_at_utc", "market", "best_bid", "best_ask"])
    fairs = [
        {"asset_id": "A", "fair_probability": "0.70", "question": "A wins?"},
        {"asset_id": "B", "fair_probability": "0.30", "question": "B wins?"},
        {"asset_id": "C", "fair_probability": "0.50", "question": "C wins?"},
    ]
    fairs_path = tmp_path / "fairs.csv"
    _write(fairs_path, fairs, ["asset_id", "fair_probability", "question"])

    signals, summary = scan_live_mispricing(cfg, fairs_path=str(fairs_path))
    actions = {s["asset_id"]: s["action"] for s in signals}
    assert actions == {"A": "BUY_YES", "B": "SELL_YES", "C": "MAKE"}
    assert summary["live_trading"] is False
    assert summary["buy_yes"] == 1 and summary["sell_yes"] == 1 and summary["market_make"] == 1
    assert summary["skipped_no_fair_or_book"] == 1            # D had a book but no fair
