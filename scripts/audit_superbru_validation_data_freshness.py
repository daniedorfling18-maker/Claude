from __future__ import annotations

import argparse
import csv
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit whether SuperBru/Oddspedia validation data is fresh enough to trust."
    )
    parser.add_argument("--locked-card-csv", default="outputs/final_locked_picks/superbru_final_card.csv")
    parser.add_argument("--oddspedia-grid-csv", default="inputs/smartbet_grids/oddspedia_probability_grids_auto.csv")
    parser.add_argument("--oddspedia-comparison-csv", default="outputs/oddspedia_pick_validation/oddspedia_pick_comparison.csv")
    parser.add_argument("--superbru-results-csv", default="outputs/superbru_pool/superbru_match_results_auto.csv")
    parser.add_argument("--market-history-csv", default="outputs/market_odds_history/market_odds_history.csv")
    parser.add_argument("--prediction-log-csv", default="outputs/backtesting/prediction_log.csv")
    parser.add_argument("--out-json", default="outputs/superbru_validation_freshness/superbru_validation_data_freshness.json")
    parser.add_argument("--out-md", default="outputs/superbru_validation_freshness/superbru_validation_data_freshness.md")
    parser.add_argument("--max-oddspedia-age-hours", type=float, default=36.0)
    parser.add_argument("--max-results-age-hours", type=float, default=72.0)
    parser.add_argument("--min-locked-card-coverage", type=float, default=0.80)
    return parser


ALIASES = {
    "usa": "unitedstates",
    "us": "unitedstates",
    "unitedstatesofamerica": "unitedstates",
    "bosniaandherzegovina": "bosniaherzegovina",
    "drc": "drcongo",
    "democraticrepublicofcongo": "drcongo",
    "cotedivoire": "ivorycoast",
    "côtedivoire": "ivorycoast",
}


def now() -> datetime:
    return datetime.now(timezone.utc)


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def norm_team(value: Any) -> str:
    text = txt(value).lower().replace("&", "and")
    text = re.sub(r"[^a-z0-9]+", "", text)
    return ALIASES.get(text, text)


def match_key(home: Any, away: Any) -> str:
    return f"{norm_team(home)}::{norm_team(away)}"


def read_rows(path: str | Path) -> list[dict[str, str]]:
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return []
    with p.open("r", newline="", encoding="utf-8-sig", errors="replace") as handle:
        return [{str(k): "" if v is None else str(v) for k, v in row.items()} for row in csv.DictReader(handle)]


def file_age_hours(path: str | Path) -> float | None:
    p = Path(path)
    if not p.exists():
        return None
    mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
    return max(0.0, (now() - mtime).total_seconds() / 3600.0)


def parse_dt(value: Any) -> datetime | None:
    text = txt(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def latest_timestamp(rows: list[dict[str, str]], fields: list[str]) -> str:
    stamps: list[datetime] = []
    for row in rows:
        for field in fields:
            parsed = parse_dt(row.get(field))
            if parsed is not None:
                stamps.append(parsed)
                break
    return max(stamps).isoformat() if stamps else ""


def boolish(value: Any) -> bool:
    return txt(value).lower() in {"1", "true", "yes", "y", "completed"}


def source_health(path: str | Path, rows: list[dict[str, str]], *, max_age_hours: float | None = None) -> dict[str, Any]:
    age = file_age_hours(path)
    status = "ok"
    if not Path(path).exists():
        status = "missing"
    elif not rows:
        status = "empty"
    elif max_age_hours is not None and age is not None and age > max_age_hours:
        status = "stale"
    return {
        "path": str(path),
        "exists": Path(path).exists(),
        "rows": len(rows),
        "file_age_hours": None if age is None else round(age, 3),
        "status": status,
    }


def coverage(locked_rows: list[dict[str, str]], source_rows: list[dict[str, str]]) -> dict[str, Any]:
    locked_keys = {match_key(row.get("home_team"), row.get("away_team")) for row in locked_rows}
    source_keys = {match_key(row.get("home_team"), row.get("away_team")) for row in source_rows}
    matched = sorted(locked_keys & source_keys)
    missing = sorted(locked_keys - source_keys)
    total = len(locked_keys)
    return {
        "locked_matches": total,
        "matched_locked_matches": len(matched),
        "coverage_fraction": round(len(matched) / total, 6) if total else 0.0,
        "missing_locked_match_keys": missing,
    }


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    locked = read_rows(args.locked_card_csv)
    grids = read_rows(args.oddspedia_grid_csv)
    comparison = read_rows(args.oddspedia_comparison_csv)
    results = read_rows(args.superbru_results_csv)
    market_history = read_rows(args.market_history_csv)
    prediction_log = read_rows(args.prediction_log_csv)

    grid_coverage = coverage(locked, grids)
    comparison_coverage = coverage(locked, comparison)
    completed_results = [
        row for row in results
        if boolish(row.get("is_completed")) or (txt(row.get("home_goals")) and txt(row.get("away_goals")))
    ]
    market_snapshots = sorted({txt(row.get("snapshot_id")) for row in market_history if txt(row.get("snapshot_id"))})

    oddspedia = source_health(args.oddspedia_grid_csv, grids, max_age_hours=args.max_oddspedia_age_hours)
    results_health = source_health(args.superbru_results_csv, results, max_age_hours=args.max_results_age_hours)
    comparison_health = source_health(args.oddspedia_comparison_csv, comparison, max_age_hours=args.max_oddspedia_age_hours)

    oddspedia_ok = (
        oddspedia["status"] == "ok"
        and grid_coverage["coverage_fraction"] >= args.min_locked_card_coverage
    )
    results_ok = results_health["status"] == "ok" and len(completed_results) > 0
    if oddspedia_ok and results_ok:
        validation_state = "ready"
    elif oddspedia_ok:
        validation_state = "oddspedia_ready_superbru_results_missing_or_stale"
    elif results_ok:
        validation_state = "superbru_results_ready_oddspedia_missing_or_stale"
    else:
        validation_state = "not_ready"

    blockers: list[str] = []
    if oddspedia["status"] != "ok":
        blockers.append(f"Oddspedia grid is {oddspedia['status']}.")
    if grid_coverage["coverage_fraction"] < args.min_locked_card_coverage:
        blockers.append(
            f"Oddspedia grid covers {grid_coverage['matched_locked_matches']}/"
            f"{grid_coverage['locked_matches']} locked-card matches."
        )
    if results_health["status"] != "ok":
        blockers.append(f"SuperBru results CSV is {results_health['status']}.")
    if not completed_results:
        blockers.append("No completed SuperBru result rows are available for realised validation.")

    return {
        "generated_at_utc": now().isoformat(),
        "status": validation_state,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
        "locked_card": source_health(args.locked_card_csv, locked),
        "oddspedia_grid": {
            **oddspedia,
            "latest_source_timestamp_utc": latest_timestamp(grids, ["scraped_at_utc", "source_date"]),
            "locked_card_coverage": grid_coverage,
        },
        "oddspedia_comparison": {
            **comparison_health,
            "locked_card_coverage": comparison_coverage,
        },
        "superbru_results": {
            **results_health,
            "completed_rows": len(completed_results),
            "latest_scraped_at_utc": latest_timestamp(results, ["scraped_at_utc"]),
        },
        "market_history": {
            **source_health(args.market_history_csv, market_history),
            "snapshot_count": len(market_snapshots),
            "latest_snapshot_created_at_utc": latest_timestamp(market_history, ["snapshot_created_at_utc"]),
        },
        "prediction_log": source_health(args.prediction_log_csv, prediction_log),
        "blockers": blockers,
        "recommended_actions": [
            "Run and commit the Oddspedia pipeline after every locked-card refresh.",
            "Run the SuperBru results backfill from an authenticated browser session, or add a headless-login results scraper.",
            "Use this freshness artifact before trusting CLV, ROI, or SuperBru policy validation.",
        ],
    }


def write_report(payload: dict[str, Any], path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# SuperBru validation data freshness",
        "",
        f"Generated: `{payload['generated_at_utc']}`",
        "",
        f"Status: **{payload['status']}**",
        "",
        "## Sources",
        "",
        "| Source | Rows | Status | Age hours | Coverage |",
        "| --- | ---: | --- | ---: | ---: |",
    ]
    for label, key in [
        ("Locked card", "locked_card"),
        ("Oddspedia grid", "oddspedia_grid"),
        ("Oddspedia comparison", "oddspedia_comparison"),
        ("SuperBru results", "superbru_results"),
        ("Market history", "market_history"),
        ("Prediction log", "prediction_log"),
    ]:
        item = payload.get(key, {})
        cov = item.get("locked_card_coverage", {})
        coverage_text = "-"
        if cov:
            coverage_text = f"{cov.get('matched_locked_matches', 0)}/{cov.get('locked_matches', 0)}"
        lines.append(
            f"| {label} | {item.get('rows', 0)} | {item.get('status', '-')} | "
            f"{item.get('file_age_hours', '-')} | {coverage_text} |"
        )
    blockers = payload.get("blockers", [])
    lines.extend(["", "## Blockers", ""])
    if blockers:
        lines.extend([f"- {item}" for item in blockers])
    else:
        lines.append("- None.")
    lines.extend(["", "## Recommended actions", ""])
    lines.extend([f"- {item}" for item in payload.get("recommended_actions", [])])
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = build_parser().parse_args()
    payload = build_payload(args)
    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    write_report(payload, args.out_md)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
