"""Deribit option-implied fundamentals for crypto Polymarket markets.

A Polymarket market like "Will BTC be above $K on DATE?" has a mechanically computable fair value:
the risk-neutral probability ``P(S_T > K)``, which the option market already prices. By
Breeden-Litzenberger, the undiscounted call price curve gives the risk-neutral survival function:

    P(S_T > K)  =  -dC/dK                  (C in the same units as K, i.e. USD)

so a digital "above K" claim is worth the negative slope of the call curve at K. Deribit quotes a
dense option chain on BTC/ETH; this module turns that chain into a ``P(S_T > K)`` curve per expiry
and reads off the probability for each configured Polymarket strike - a genuinely *independent*,
no-labelling-lag fundamental for the mispricing-alpha slot.

The math (instrument parsing, USD call conversion, the survival curve, interpolation) is pure and
unit-tested; the Deribit call is a thin public GET (no key required for market data).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import requests

from .config import EngineConfig, load_config
from .utils import now_utc, parse_timestamp, read_csv_rows, safe_float, write_csv, write_json

DEFAULT_BASE_URL = "https://www.deribit.com/api/v2"
_MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
           "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}


# --------------------------------------------------------------------------- pure parsing / math
def parse_deribit_instrument(name: str) -> dict[str, Any] | None:
    """``BTC-27JUN25-100000-C`` -> {currency, expiry(date), strike, option_type}. None if unparsable."""
    parts = str(name).split("-")
    if len(parts) != 4:
        return None
    currency, expiry_code, strike_s, opt = parts
    opt = opt.upper()
    if opt not in {"C", "P"} or len(expiry_code) < 7:
        return None
    try:
        day = int(expiry_code[:2])
        month = _MONTHS[expiry_code[2:5].upper()]
        year = 2000 + int(expiry_code[5:])
        strike = float(strike_s)
    except (ValueError, KeyError):
        return None
    return {"currency": currency.upper(), "expiry": date(year, month, day),
            "strike": strike, "option_type": opt}


def usd_call_curve(book_rows: Iterable[Mapping[str, Any]], currency: str, expiry: date) -> list[tuple[float, float]]:
    """Sorted ``(strike, call_price_usd)`` for one currency+expiry from a Deribit book summary.

    Deribit option ``mark_price`` is quoted in the underlying, so the USD price is
    ``mark_price * underlying_price``."""
    out: dict[float, float] = {}
    for row in book_rows:
        meta = parse_deribit_instrument(str(row.get("instrument_name", "")))
        if not meta or meta["option_type"] != "C" or meta["currency"] != currency.upper() or meta["expiry"] != expiry:
            continue
        mark = safe_float(row.get("mark_price"))
        underlying = safe_float(row.get("underlying_price")) or safe_float(row.get("index_price"))
        if mark is None or underlying is None or mark < 0:
            continue
        out[meta["strike"]] = mark * underlying
    return sorted(out.items())


def survival_probabilities_from_calls(strikes: Sequence[float], calls_usd: Sequence[float]) -> list[float]:
    """``P(S_T > K) = -dC/dK`` via finite differences (central interior, one-sided at the ends),
    clamped to ``[0, 1]``. Strikes must be ascending."""
    n = len(strikes)
    probs: list[float] = []
    for i in range(n):
        if n < 2:
            probs.append(float("nan"))
            continue
        if i == 0:
            d_k, d_c = strikes[1] - strikes[0], calls_usd[1] - calls_usd[0]
        elif i == n - 1:
            d_k, d_c = strikes[-1] - strikes[-2], calls_usd[-1] - calls_usd[-2]
        else:
            d_k, d_c = strikes[i + 1] - strikes[i - 1], calls_usd[i + 1] - calls_usd[i - 1]
        if not d_k:
            probs.append(float("nan"))
            continue
        p = -d_c / d_k
        probs.append(min(1.0, max(0.0, p)))
    return probs


def interpolate_survival(strikes: Sequence[float], probs: Sequence[float], strike: float) -> float | None:
    """Linear interpolation of the survival curve at ``strike`` (flat-extrapolated past the ends)."""
    pairs = [(k, p) for k, p in zip(strikes, probs) if p == p]  # drop NaN
    if not pairs:
        return None
    if strike <= pairs[0][0]:
        return pairs[0][1]
    if strike >= pairs[-1][0]:
        return pairs[-1][1]
    for (k0, p0), (k1, p1) in zip(pairs, pairs[1:]):
        if k0 <= strike <= k1:
            t = (strike - k0) / (k1 - k0) if k1 > k0 else 0.0
            return p0 + t * (p1 - p0)
    return None


def _target_expiry_date(value: Any) -> date | None:
    parsed = parse_timestamp(value)
    if parsed:
        return parsed.date()
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _nearest_expiry(available: Sequence[date], target: date) -> date | None:
    return min(available, key=lambda d: abs((d - target).days)) if available else None


# --------------------------------------------------------------------------- network (thin)
def fetch_deribit_book(currency: str, *, base_url: str = DEFAULT_BASE_URL, timeout: int = 20) -> list[dict[str, Any]]:
    resp = requests.get(f"{base_url}/public/get_book_summary_by_currency",
                        params={"currency": currency.upper(), "kind": "option"}, timeout=timeout)
    resp.raise_for_status()
    payload = resp.json()
    result = payload.get("result", payload) if isinstance(payload, Mapping) else payload
    return [row for row in result if isinstance(row, Mapping)] if isinstance(result, list) else []


# --------------------------------------------------------------------------- build
def build_crypto_fundamental(cfg: EngineConfig, *, targets_path: str | None = None,
                             book_by_currency: dict[str, list[Mapping[str, Any]]] | None = None) -> dict[str, Any]:
    """Price each configured crypto target (token_id, currency, strike, expiry) off Deribit's
    option-implied survival curve and write the fundamental contract. ``book_by_currency`` lets
    tests inject a chain instead of hitting the network."""
    settings = cfg.raw.get("crypto_fundamental", {}) or {}
    base_url = str(settings.get("base_url", DEFAULT_BASE_URL)).rstrip("/")
    timeout = int(settings.get("request_timeout_seconds", 20))
    in_path = Path(targets_path or settings.get("targets_path") or "inputs/polymarket/crypto_targets.csv")
    out_dir = cfg.output_root / "polymarket_training"
    out_path = out_dir / "crypto_fundamental_probabilities.csv"

    targets = read_csv_rows(in_path)
    if not targets:
        summary = {"status": "no_targets", "targets_path": str(in_path), "fundamental_rows": 0,
                   "output_file": str(out_path), "generated_at_utc": now_utc()}
        write_json(cfg.governance_root / "crypto_fundamental_summary.json", summary)
        write_csv(out_path, [])
        return summary

    by_currency: dict[str, list[Mapping[str, Any]]] = {}
    fetch_errors: list[dict[str, Any]] = []
    currencies = {str(t.get("currency", "BTC")).upper() for t in targets}
    for currency in currencies:
        if book_by_currency is not None:
            by_currency[currency] = list(book_by_currency.get(currency, []))
            continue
        try:
            by_currency[currency] = fetch_deribit_book(currency, base_url=base_url, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - per-currency network errors are not fatal
            by_currency[currency] = []
            fetch_errors.append({"currency": currency, "error": str(exc)})

    out_rows: list[dict[str, Any]] = []
    skipped: int = 0
    for target in targets:
        token_id = str(target.get("token_id") or target.get("asset_id") or "").strip()
        currency = str(target.get("currency", "BTC")).upper()
        strike = safe_float(target.get("strike"))
        target_expiry = _target_expiry_date(target.get("expiry"))
        book = by_currency.get(currency, [])
        if not token_id or strike is None or target_expiry is None or not book:
            skipped += 1
            continue
        available = sorted({m["expiry"] for m in (parse_deribit_instrument(str(r.get("instrument_name", ""))) or {} for r in book) if m})
        expiry = _nearest_expiry(available, target_expiry)
        if expiry is None:
            skipped += 1
            continue
        curve = usd_call_curve(book, currency, expiry)
        if len(curve) < 2:
            skipped += 1
            continue
        strikes = [k for k, _ in curve]
        probs = survival_probabilities_from_calls(strikes, [c for _, c in curve])
        probability = interpolate_survival(strikes, probs, strike)
        if probability is None:
            skipped += 1
            continue
        # A strike outside the quoted strike range is flat-extrapolated to the nearest tail value,
        # which overstates deep-OTM probabilities - flag it so the overlay/operator can discount it.
        in_curve_range = strikes[0] <= strike <= strikes[-1]
        out_rows.append({
            "token_id": token_id,
            "probability": round(probability, 6),
            "in_curve_range": in_curve_range,
            "curve_strike_min": strikes[0],
            "curve_strike_max": strikes[-1],
            "currency": currency,
            "strike": strike,
            "expiry_requested": target_expiry.isoformat(),
            "expiry_used": expiry.isoformat(),
            "curve_points": len(curve),
            "source": f"deribit_options_{currency.lower()}",
        })

    write_csv(out_path, out_rows)
    summary = {
        "status": "built",
        "targets_path": str(in_path),
        "targets_in": len(targets),
        "fundamental_rows": len(out_rows),
        "skipped": skipped,
        "out_of_curve_range": sum(1 for r in out_rows if not r["in_curve_range"]),
        "currencies": sorted(currencies),
        "fetch_errors": fetch_errors,
        "output_file": str(out_path),
        "note": "Deribit option-implied P(S_T > K) per market. Point "
                "mispricing_alpha.fundamental_probability_paths at output_file to use as the anchor.",
        "generated_at_utc": now_utc(),
    }
    write_json(cfg.governance_root / "crypto_fundamental_summary.json", summary)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return build_crypto_fundamental(load_config(config_path))
