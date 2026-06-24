from __future__ import annotations

import csv
import json
from pathlib import Path
from datetime import datetime, timezone

MODEL_FILE = Path("outputs/polymarket_training/worldcup_fixture_model_probabilities.csv")
CANDIDATES_FILE = Path("outputs/polymarket_paper_trading/worldcup_paper_trade_candidates.csv")
PAPER_ORDERS_FILE = Path("outputs/polymarket_paper_trading/worldcup_paper_orders.csv")
PAPER_POSITIONS_FILE = Path("outputs/polymarket_paper_trading/worldcup_paper_positions.csv")

COMPLETED_VALIDATION_FILES = list(Path("outputs/polymarket_training").glob("completed_game_validation_*.csv"))
MONEYLINE_VALIDATION_FILES = list(Path("outputs/polymarket_training").glob("*moneyline_validation*.csv"))

OUT = Path("outputs/polymarket_model_governance/worldcup_model_readiness_gate.json")
OUT_CSV = Path("outputs/polymarket_model_governance/worldcup_model_readiness_gate_checks.csv")

THRESHOLDS = {
    "minimum_completed_games": 25,
    "minimum_completed_moneyline_games": 25,
    "maximum_calibration_error": 0.08,
    "minimum_brier_improvement_vs_market": 0.01,
    "minimum_paper_trades_before_live": 100,
    "minimum_positive_paper_roi": 0.02,
}

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def read_csv(path: Path):
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

def boolish(x):
    return str(x).strip().lower() in {"true", "1", "yes", "y"}

def unique_count(rows, key):
    return len({r.get(key, "") for r in rows if r.get(key, "")})

model_rows = read_csv(MODEL_FILE)
candidate_rows = read_csv(CANDIDATES_FILE)
paper_orders = read_csv(PAPER_ORDERS_FILE)
paper_positions = read_csv(PAPER_POSITIONS_FILE)

completed_validation_rows = []
for path in COMPLETED_VALIDATION_FILES:
    completed_validation_rows.extend(read_csv(path))

moneyline_validation_rows = []
for moneyline_path in MONEYLINE_VALIDATION_FILES:
    for row in read_csv(moneyline_path):
        row["_source_file"] = str(moneyline_path)
        row["_source_stem"] = moneyline_path.stem
        moneyline_validation_rows.append(row)

trained_model_rows = [r for r in model_rows if boolish(r.get("trained"))]
tradable_model_rows = [r for r in model_rows if boolish(r.get("tradable"))]

paper_buy_rows = [r for r in candidate_rows if r.get("action") == "PAPER_BUY_YES"]
open_positions = [r for r in paper_positions if r.get("status") == "OPEN"]
closed_positions = [r for r in paper_positions if r.get("status") == "CLOSED"]

completed_games = unique_count(completed_validation_rows, "fixture")
moneyline_games = len({
    (
        r.get("fixture_slug")
        or r.get("fixture")
        or r.get("_source_stem")
        or ""
    ).strip()
    for r in moneyline_validation_rows
    if (
        r.get("fixture_slug")
        or r.get("fixture")
        or r.get("_source_stem")
        or ""
    ).strip()
})

# These will remain unknown/false until we build real validation reports.
brier_improvement_vs_market = None
calibration_error = None
paper_roi = None

if closed_positions:
    pnls = [fnum(r.get("pnl")) for r in closed_positions]
    stakes = [fnum(r.get("stake")) for r in closed_positions]
    pnls = [x for x in pnls if x is not None]
    stakes = [x for x in stakes if x is not None]
    if stakes and sum(stakes) > 0:
        paper_roi = sum(pnls) / sum(stakes)

checks = []

def add_check(name, passed, severity, detail):
    checks.append({
        "check": name,
        "passed": bool(passed),
        "severity": severity,
        "detail": detail,
    })

add_check(
    "model_file_exists",
    MODEL_FILE.exists(),
    "blocking",
    f"Model file: {MODEL_FILE}",
)

add_check(
    "trained_model_exists",
    len(trained_model_rows) > 0,
    "blocking",
    f"trained=True rows: {len(trained_model_rows)}",
)

add_check(
    "tradable_model_exists",
    len(tradable_model_rows) > 0,
    "blocking",
    f"tradable=True rows: {len(tradable_model_rows)}",
)

add_check(
    "completed_game_validation_exists",
    len(completed_validation_rows) > 0,
    "blocking",
    f"completed validation rows: {len(completed_validation_rows)}",
)

add_check(
    "minimum_completed_games_met",
    completed_games >= THRESHOLDS["minimum_completed_games"],
    "blocking",
    f"completed games: {completed_games}; required: {THRESHOLDS['minimum_completed_games']}",
)

add_check(
    "minimum_moneyline_games_met",
    moneyline_games >= THRESHOLDS["minimum_completed_moneyline_games"],
    "blocking",
    f"moneyline validation games: {moneyline_games}; required: {THRESHOLDS['minimum_completed_moneyline_games']}",
)

add_check(
    "brier_better_than_market",
    brier_improvement_vs_market is not None and brier_improvement_vs_market >= THRESHOLDS["minimum_brier_improvement_vs_market"],
    "blocking",
    f"brier improvement vs market: {brier_improvement_vs_market}; required: {THRESHOLDS['minimum_brier_improvement_vs_market']}",
)

add_check(
    "calibration_error_acceptable",
    calibration_error is not None and calibration_error <= THRESHOLDS["maximum_calibration_error"],
    "blocking",
    f"calibration error: {calibration_error}; maximum allowed: {THRESHOLDS['maximum_calibration_error']}",
)

add_check(
    "paper_trade_candidate_engine_exists",
    CANDIDATES_FILE.exists(),
    "non_blocking",
    f"candidate rows: {len(candidate_rows)}",
)

add_check(
    "paper_orders_file_exists",
    PAPER_ORDERS_FILE.exists(),
    "non_blocking",
    f"paper orders: {len(paper_orders)}",
)

add_check(
    "minimum_paper_trades_before_live",
    len(closed_positions) >= THRESHOLDS["minimum_paper_trades_before_live"],
    "live_blocking",
    f"closed paper positions: {len(closed_positions)}; required before live: {THRESHOLDS['minimum_paper_trades_before_live']}",
)

add_check(
    "paper_roi_positive_before_live",
    paper_roi is not None and paper_roi >= THRESHOLDS["minimum_positive_paper_roi"],
    "live_blocking",
    f"paper ROI: {paper_roi}; required before live: {THRESHOLDS['minimum_positive_paper_roi']}",
)

blocking_failed = [c for c in checks if c["severity"] == "blocking" and not c["passed"]]
live_blocking_failed = [c for c in checks if c["severity"] in {"blocking", "live_blocking"} and not c["passed"]]

paper_trading_enabled = len(blocking_failed) == 0
live_trading_enabled = False

gate = {
    "generated_at_utc": now_iso(),
    "gate_version": "worldcup_model_readiness_gate_v1",
    "paper_trading_enabled": paper_trading_enabled,
    "live_trading_enabled": live_trading_enabled,
    "live_trading_hard_block": True,
    "thresholds": THRESHOLDS,
    "model_file": str(MODEL_FILE),
    "candidate_file": str(CANDIDATES_FILE),
    "paper_orders_file": str(PAPER_ORDERS_FILE),
    "paper_positions_file": str(PAPER_POSITIONS_FILE),
    "metrics": {
        "model_rows": len(model_rows),
        "trained_model_rows": len(trained_model_rows),
        "tradable_model_rows": len(tradable_model_rows),
        "candidate_rows": len(candidate_rows),
        "paper_buy_candidate_rows": len(paper_buy_rows),
        "paper_orders": len(paper_orders),
        "open_positions": len(open_positions),
        "closed_positions": len(closed_positions),
        "completed_validation_rows": len(completed_validation_rows),
        "completed_games": completed_games,
        "moneyline_validation_rows": len(moneyline_validation_rows),
        "moneyline_games": moneyline_games,
        "brier_improvement_vs_market": brier_improvement_vs_market,
        "calibration_error": calibration_error,
        "paper_roi": paper_roi,
    },
    "failed_blocking_checks": blocking_failed,
    "failed_live_checks": live_blocking_failed,
    "checks": checks,
    "governance": {
        "paper_trading_rule": "Paper trading requires trained=True, tradable=True, sufficient completed-game validation, market-beating Brier performance, and acceptable calibration.",
        "live_trading_rule": "Live trading remains hard-blocked until paper-trading performance is separately approved.",
    },
}

OUT.parent.mkdir(parents=True, exist_ok=True)

OUT.write_text(json.dumps(gate, indent=2, sort_keys=True), encoding="utf-8")

with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
    fieldnames = ["check", "passed", "severity", "detail"]
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(checks)

print(json.dumps({
    "paper_trading_enabled": paper_trading_enabled,
    "live_trading_enabled": live_trading_enabled,
    "failed_blocking_checks": [c["check"] for c in blocking_failed],
    "failed_live_checks": [c["check"] for c in live_blocking_failed],
    "output": str(OUT),
    "checks_output": str(OUT_CSV),
}, indent=2, sort_keys=True))

