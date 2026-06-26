import csv
from datetime import date

from pytest import approx

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.crypto_fundamental import (
    build_crypto_fundamental,
    interpolate_survival,
    parse_deribit_instrument,
    survival_probabilities_from_calls,
    usd_call_curve,
)


def test_parse_deribit_instrument():
    meta = parse_deribit_instrument("BTC-27JUN26-100000-C")
    assert meta == {"currency": "BTC", "expiry": date(2026, 6, 27), "strike": 100000.0, "option_type": "C"}
    assert parse_deribit_instrument("garbage") is None
    assert parse_deribit_instrument("BTC-27JUN26-100000-P")["option_type"] == "P"


def _book():
    # USD call prices 12000 / 6000 / 2000 at strikes 90k/100k/110k; mark = usd/underlying.
    u = 100000.0
    return [
        {"instrument_name": "BTC-27JUN26-90000-C", "mark_price": 12000 / u, "underlying_price": u},
        {"instrument_name": "BTC-27JUN26-100000-C", "mark_price": 6000 / u, "underlying_price": u},
        {"instrument_name": "BTC-27JUN26-110000-C", "mark_price": 2000 / u, "underlying_price": u},
        {"instrument_name": "BTC-27JUN26-100000-P", "mark_price": 0.05, "underlying_price": u},  # put: ignored
    ]


def test_usd_call_curve_filters_calls_and_converts_to_usd():
    curve = usd_call_curve(_book(), "BTC", date(2026, 6, 27))
    assert [k for k, _ in curve] == [90000.0, 100000.0, 110000.0]      # puts dropped, sorted
    assert [round(c) for _, c in curve] == [12000, 6000, 2000]         # mark * underlying


def test_survival_probabilities_are_monotone_and_bounded():
    strikes = [90000.0, 100000.0, 110000.0]
    calls = [12000.0, 6000.0, 2000.0]
    probs = survival_probabilities_from_calls(strikes, calls)
    assert probs[0] == approx(0.6)        # -(6000-12000)/10000
    assert probs[1] == approx(0.5)        # -(2000-12000)/20000  (central)
    assert probs[2] == approx(0.4)        # -(2000-6000)/10000
    assert probs[0] > probs[1] > probs[2] and all(0 <= p <= 1 for p in probs)


def test_interpolate_survival():
    ks, ps = [90000.0, 100000.0, 110000.0], [0.6, 0.5, 0.4]
    assert interpolate_survival(ks, ps, 95000) == approx(0.55)     # midway
    assert interpolate_survival(ks, ps, 80000) == approx(0.6)      # below range -> flat
    assert interpolate_survival(ks, ps, 130000) == approx(0.4)     # above range -> flat


def _write(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def test_build_crypto_fundamental_prices_targets_offline(tmp_path):
    cfg = EngineConfig(raw={"paths": {"output_root": str(tmp_path / "outputs")},
                            "crypto_fundamental": {"targets_path": str(tmp_path / "targets.csv")}},
                       path=tmp_path / "cfg.yaml")
    _write(tmp_path / "targets.csv",
           [{"token_id": "T100", "currency": "BTC", "strike": "100000", "expiry": "2026-06-27"},
            {"token_id": "T95", "currency": "BTC", "strike": "95000", "expiry": "2026-06-26"}],  # nearest expiry
           ["token_id", "currency", "strike", "expiry"])

    summary = build_crypto_fundamental(cfg, book_by_currency={"BTC": _book()})
    assert summary["status"] == "built"
    assert summary["fundamental_rows"] == 2

    out = {r["token_id"]: r for r in csv.DictReader(open(tmp_path / "outputs" / "polymarket_training" / "crypto_fundamental_probabilities.csv", encoding="utf-8-sig"))}
    assert float(out["T100"]["probability"]) == approx(0.5)
    assert float(out["T95"]["probability"]) == approx(0.55)
    assert out["T95"]["expiry_used"] == "2026-06-27"      # snapped to the nearest available expiry


def test_build_crypto_fundamental_no_targets(tmp_path):
    cfg = EngineConfig(raw={"paths": {"output_root": str(tmp_path / "outputs")},
                            "crypto_fundamental": {"targets_path": str(tmp_path / "missing.csv")}},
                       path=tmp_path / "cfg.yaml")
    summary = build_crypto_fundamental(cfg, book_by_currency={"BTC": _book()})
    assert summary["status"] == "no_targets"
    assert summary["fundamental_rows"] == 0
