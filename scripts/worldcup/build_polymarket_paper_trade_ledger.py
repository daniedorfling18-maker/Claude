from __future__ import annotations

import csv
import json
from pathlib import Path
from datetime import datetime, timezone

CANDIDATES = Path("outputs/polymarket_paper_trading/worldcup_paper_trade_candidates.csv")

ORDERS = Path("outputs/polymarket_paper_trading/worldcup_paper_orders.csv")
POSITIONS = Path("outputs/polymarket_paper_trading/worldcup_paper_positions.csv")
SUMMARY = Path("outputs/polymarket_model_governance/worldcup_paper_trading_ledger_summary.json")

def read_csv(path):
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        return list(csv.DictReader(f))

def fnum(x):
    try:
        if x is None or x == "":
            return None
        return float(x)
    except Exception:
        return None

def now_iso():
    return datetime.now(timezone.utc).isoformat()

candidates = read_csv(CANDIDATES)

orders = []
positions = []

for i, r in enumerate(candidates, start=1):
    if r.get("action") != "PAPER_BUY_YES":
        continue

    stake = fnum(r.get("stake")) or 0.0
    price = fnum(r.get("market_price")) or 0.0

    if stake <= 0 or price <= 0:
        continue

    shares = stake / price

    order_id = f"paper_order_{i:06d}"

    orders.append({
        "order_id": order_id,
        "created_at_utc": now_iso(),
        "mode": "paper",
        "fixture_slug": r.get("fixture_slug", ""),
        "fixture_date": r.get("fixture_date", ""),
        "outcome_name": r.get("outcome_name", ""),
        "token_id": r.get("token_id", ""),
        "side": "BUY",
        "price": price,
        "stake": stake,
        "shares": shares,
        "model_probability": r.get("model_probability", ""),
        "edge": r.get("edge", ""),
        "model_version": r.get("model_version", ""),
        "status": "FILLED_PAPER",
        "reason": r.get("reason", ""),
    })

    positions.append({
        "position_id": f"paper_position_{i:06d}",
        "order_id": order_id,
        "opened_at_utc": now_iso(),
        "mode": "paper",
        "fixture_slug": r.get("fixture_slug", ""),
        "fixture_date": r.get("fixture_date", ""),
        "outcome_name": r.get("outcome_name", ""),
        "token_id": r.get("token_id", ""),
        "entry_price": price,
        "stake": stake,
        "shares": shares,
        "status": "OPEN",
        "settlement_price": "",
        "pnl": "",
        "roi": "",
    })

ORDERS.parent.mkdir(parents=True, exist_ok=True)
POSITIONS.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.parent.mkdir(parents=True, exist_ok=True)

order_fields = [
    "order_id",
    "created_at_utc",
    "mode",
    "fixture_slug",
    "fixture_date",
    "outcome_name",
    "token_id",
    "side",
    "price",
    "stake",
    "shares",
    "model_probability",
    "edge",
    "model_version",
    "status",
    "reason",
]

position_fields = [
    "position_id",
    "order_id",
    "opened_at_utc",
    "mode",
    "fixture_slug",
    "fixture_date",
    "outcome_name",
    "token_id",
    "entry_price",
    "stake",
    "shares",
    "status",
    "settlement_price",
    "pnl",
    "roi",
]

with ORDERS.open("w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=order_fields)
    writer.writeheader()
    writer.writerows(orders)

with POSITIONS.open("w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=position_fields)
    writer.writeheader()
    writer.writerows(positions)

summary = {
    "candidate_rows": len(candidates),
    "paper_orders": len(orders),
    "open_positions": len(positions),
    "orders_output": str(ORDERS),
    "positions_output": str(POSITIONS),
    "status": "ok",
    "governance": "No PAPER_BUY_YES candidates means no paper orders. This is expected until trained model probabilities exist.",
}

SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
