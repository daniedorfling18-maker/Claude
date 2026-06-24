from __future__ import annotations

import csv
import json
from pathlib import Path
from datetime import datetime, timezone

PRICE_FILE = Path("outputs/polymarket_training/worldcup_fixture_moneyline_clob_prices.csv")
MODEL_FILE = Path("outputs/polymarket_training/worldcup_fixture_model_probabilities.csv")
READINESS_GATE = Path("outputs/polymarket_model_governance/worldcup_model_readiness_gate.json")

OUT = Path("outputs/polymarket_paper_trading/worldcup_paper_trade_candidates.csv")
SUMMARY = Path("outputs/polymarket_model_governance/worldcup_paper_trade_candidates_summary.json")

BANKROLL = 1000.0
MIN_EDGE = 0.03
MAX_PRICE = 0.95
MIN_PRICE = 0.02
MAX_STAKE = 25.0
KELLY_FRACTION = 0.25

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def read_csv(path: Path):
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        return list(csv.DictReader(f))

def read_json(path: Path):
    if not path.exists() or path.stat().st_size == 0:
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))

def fnum(x):
    try:
        if x is None or x == "":
            return None
        return float(x)
    except Exception:
        return None

def boolish(x):
    return str(x).strip().lower() in {"true", "1", "yes", "y"}

def kelly_fraction_binary(q: float, p: float) -> float:
    if p >= 1:
        return 0.0
    return max(0.0, (q - p) / (1 - p))

prices = read_csv(PRICE_FILE)
model_rows = read_csv(MODEL_FILE)
readiness_gate = read_json(READINESS_GATE)
paper_gate_enabled = boolish(readiness_gate.get("paper_trading_enabled"))

model_lookup = {}
for r in model_rows:
    key = (r.get("fixture_slug", ""), r.get("outcome_name", ""))
    if key[0] and key[1]:
        model_lookup[key] = r

yes_rows = [r for r in prices if r.get("outcome") == "Yes"]

by_fixture = {}
for r in yes_rows:
    by_fixture.setdefault(r.get("fixture_slug", ""), []).append(r)

fixture_quality = {}

for fixture_slug, rows in by_fixture.items():
    ps = [fnum(r.get("effective_price")) for r in rows]
    ps = [p for p in ps if p is not None]

    raw_sum = sum(ps)
    flags = []

    if len(ps) != 3:
        flags.append("missing_moneyline_leg")

    if raw_sum < 0.95 or raw_sum > 1.05:
        flags.append("probability_sum_outlier")

    if any(r.get("extraction_quality") != "ok" for r in rows):
        flags.append("extraction_warning")

    fixture_quality[fixture_slug] = {
        "raw_probability_sum": raw_sum,
        "usable": not flags,
        "flags": "|".join(flags) if flags else "ok",
    }

signals = []

for r in yes_rows:
    fixture_slug = r.get("fixture_slug", "")
    outcome_name = r.get("team_or_draw", "")
    token_id = r.get("token_id", "")

    market_price = fnum(r.get("effective_price"))
    qinfo = fixture_quality.get(
        fixture_slug,
        {"usable": False, "flags": "missing_fixture_quality", "raw_probability_sum": ""},
    )

    model_row = model_lookup.get((fixture_slug, outcome_name), {})
    model_probability = fnum(model_row.get("model_probability"))
    model_version = model_row.get("model_version", "")
    model_trained = boolish(model_row.get("trained"))
    model_tradable = boolish(model_row.get("tradable"))

    action = "NO_TRADE"
    reason = []
    edge = ""
    stake = 0.0
    kelly_raw = 0.0
    expected_value_per_share = ""

    if not qinfo["usable"]:
        reason.append(qinfo["flags"])

    if not paper_gate_enabled:
        reason.append("paper_trading_readiness_gate_disabled")

    if market_price is None:
        reason.append("missing_market_price")

    if model_probability is None:
        reason.append("missing_model_probability")

    if model_probability is not None and not model_trained:
        reason.append("model_not_trained")

    if model_probability is not None and not model_tradable:
        reason.append("model_not_tradable")

    if market_price is not None and model_probability is not None:
        edge_value = model_probability - market_price
        edge = edge_value
        expected_value_per_share = edge_value

        if edge_value < MIN_EDGE:
            reason.append("edge_below_threshold")

        if market_price > MAX_PRICE:
            reason.append("price_too_high")

        if market_price < MIN_PRICE:
            reason.append("price_too_low")

        if (
            paper_gate_enabled
            and qinfo["usable"]
            and model_trained
            and model_tradable
            and edge_value >= MIN_EDGE
            and MIN_PRICE <= market_price <= MAX_PRICE
        ):
            kelly_raw = kelly_fraction_binary(model_probability, market_price)
            stake = min(MAX_STAKE, BANKROLL * kelly_raw * KELLY_FRACTION)

            if stake > 0:
                action = "PAPER_BUY_YES"
            else:
                reason.append("zero_kelly_stake")

    if not reason:
        reason.append("passes_all_gates")

    signals.append({
        "generated_at_utc": now_iso(),
        "mode": "paper",
        "fixture_slug": fixture_slug,
        "fixture": r.get("fixture", ""),
        "fixture_date": r.get("fixture_date", ""),
        "market_type": r.get("market_type", ""),
        "outcome_name": outcome_name,
        "question": r.get("question", ""),
        "token_id": token_id,
        "market_price": "" if market_price is None else market_price,
        "model_probability": "" if model_probability is None else model_probability,
        "model_version": model_version,
        "model_trained": model_trained,
        "model_tradable": model_tradable,
        "edge": edge,
        "expected_value_per_share": expected_value_per_share,
        "raw_probability_sum": qinfo["raw_probability_sum"],
        "quality_flags": qinfo["flags"],
        "kelly_raw": kelly_raw,
        "stake": stake,
        "action": action,
        "reason": "|".join(reason),
        "bankroll": BANKROLL,
        "min_edge": MIN_EDGE,
        "max_stake": MAX_STAKE,
        "kelly_fraction": KELLY_FRACTION,
    })

OUT.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.parent.mkdir(parents=True, exist_ok=True)

fieldnames = [
    "generated_at_utc",
    "mode",
    "fixture_slug",
    "fixture",
    "fixture_date",
    "market_type",
    "outcome_name",
    "question",
    "token_id",
    "market_price",
    "model_probability",
    "model_version",
    "model_trained",
    "model_tradable",
    "edge",
    "expected_value_per_share",
    "raw_probability_sum",
    "quality_flags",
    "kelly_raw",
    "stake",
    "action",
    "reason",
    "bankroll",
    "min_edge",
    "max_stake",
    "kelly_fraction",
]

with OUT.open("w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(signals)

summary = {
    "price_file": str(PRICE_FILE),
    "model_file": str(MODEL_FILE),
    "model_file_exists": MODEL_FILE.exists(),
    "readiness_gate_file": str(READINESS_GATE),
    "readiness_gate_exists": READINESS_GATE.exists(),
    "paper_trading_gate_enabled": paper_gate_enabled,
    "input_yes_rows": len(yes_rows),
    "signal_rows": len(signals),
    "paper_buy_yes_rows": sum(1 for r in signals if r["action"] == "PAPER_BUY_YES"),
    "no_trade_rows": sum(1 for r in signals if r["action"] == "NO_TRADE"),
    "rows_with_model_probability": sum(1 for r in signals if r["model_probability"] != ""),
    "rows_with_trained_model": sum(1 for r in signals if r["model_trained"]),
    "rows_with_tradable_model": sum(1 for r in signals if r["model_tradable"]),
    "fixtures": len({r["fixture_slug"] for r in signals}),
    "excluded_or_flagged_fixtures": [
        {
            "fixture_slug": fixture_slug,
            "raw_probability_sum": info["raw_probability_sum"],
            "quality_flags": info["flags"],
        }
        for fixture_slug, info in fixture_quality.items()
        if not info["usable"]
    ],
    "output": str(OUT),
    "governance": "Paper trades require fixture quality OK, model_probability present, model_trained=True, model_tradable=True, and edge above threshold.",
}

SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
