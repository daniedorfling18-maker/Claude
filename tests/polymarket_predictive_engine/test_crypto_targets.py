import csv

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.crypto_targets import build_crypto_targets, target_from_row


def _write(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_target_from_row_maps_terminal_above_yes_and_no():
    yes_target, yes_reason = target_from_row(
        {
            "token_id": "YES",
            "market_slug": "will-bitcoin-be-above-100k-on-june-30",
            "question": "Will Bitcoin be above $100,000 on June 30?",
            "outcome": "Yes",
            "close_time": "2026-06-30T23:59:00Z",
        }
    )
    no_target, no_reason = target_from_row(
        {
            "token_id": "NO",
            "market_slug": "will-bitcoin-be-above-100k-on-june-30",
            "question": "Will Bitcoin be above $100,000 on June 30?",
            "outcome": "No",
            "close_time": "2026-06-30T23:59:00Z",
        }
    )

    assert yes_reason == ""
    assert no_reason == ""
    assert yes_target["currency"] == "BTC"
    assert yes_target["strike"] == 100000.0
    assert yes_target["expiry"] == "2026-06-30"
    assert yes_target["direction"] == "above"
    assert no_target["direction"] == "below"


def test_target_from_row_rejects_path_dependent_dip_market():
    target, reason = target_from_row(
        {
            "token_id": "DIP",
            "market_slug": "will-ethereum-dip-to-1500-in-june-2026",
            "question": "Will Ethereum dip to $1,500 in June?",
            "outcome": "Yes",
            "close_time": "2026-07-01T04:00:00Z",
        }
    )

    assert target is None
    assert reason == "path_dependent_payoff_needs_barrier_model"


def test_build_crypto_targets_writes_terminal_targets_and_rejections(tmp_path):
    source = tmp_path / "source.csv"
    targets_path = tmp_path / "crypto_targets.csv"
    rows = [
        {
            "token_id": "BTC_Y",
            "market_slug": "will-bitcoin-be-above-100k-on-june-30",
            "question": "Will Bitcoin be above $100,000 on June 30?",
            "outcome": "Yes",
            "close_time": "2026-06-30T23:59:00Z",
            "category": "crypto_btc_special",
        },
        {
            "token_id": "BTC_N",
            "market_slug": "will-bitcoin-be-above-100k-on-june-30",
            "question": "Will Bitcoin be above $100,000 on June 30?",
            "outcome": "No",
            "close_time": "2026-06-30T23:59:00Z",
            "category": "crypto_btc_special",
        },
        {
            "token_id": "ETH_Y",
            "market_slug": "will-ethereum-be-below-1500-on-july-1",
            "question": "Will Ethereum be below $1,500 on July 1?",
            "outcome": "Yes",
            "close_time": "2026-07-01T04:00:00Z",
            "category": "crypto_eth_special",
        },
        {
            "token_id": "DIP",
            "market_slug": "will-ethereum-dip-to-1500-in-june-2026",
            "question": "Will Ethereum dip to $1,500 in June?",
            "outcome": "Yes",
            "close_time": "2026-07-01T04:00:00Z",
            "category": "crypto_eth_special",
        },
    ]
    _write(source, rows, ["token_id", "market_slug", "question", "outcome", "close_time", "category"])
    cfg = EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "crypto_target_generation": {"input_paths": [str(source)]},
            "crypto_fundamental": {"targets_path": str(targets_path)},
        },
        path=tmp_path / "cfg.yaml",
    )

    summary = build_crypto_targets(cfg)

    assert summary["status"] == "built"
    assert summary["target_rows"] == 3
    assert summary["top_rejection_reasons"] == {"path_dependent_payoff_needs_barrier_model": 1}

    out = {row["token_id"]: row for row in csv.DictReader(open(targets_path, encoding="utf-8-sig"))}
    assert out["BTC_Y"]["direction"] == "above"
    assert out["BTC_N"]["direction"] == "below"
    assert out["ETH_Y"]["direction"] == "below"
