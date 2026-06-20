from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Persist daily market-odds snapshots and compare today's market to the previous snapshot."
    )
    parser.add_argument("--market-validation-csv", default="outputs/market_odds_validation/market_odds_validation_report.csv")
    parser.add_argument("--market-odds-flat-csv", default="outputs/market_odds/worldcup_market_odds_flat.csv")
    parser.add_argument("--history-csv", default="outputs/market_odds_history/market_odds_history.csv")
    parser.add_argument("--out-dir", default="outputs/market_odds_history")
    parser.add_argument("--snapshot-id", default="", help="Optional fixed snapshot id/date. Defaults to current UTC timestamp.")
    parser.add_argument("--movement-threshold", type=float, default=0.025)
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def fnum(value: Any, default: float = float("nan")) -> float:
    try:
        text = txt(value).replace(",", ".")
        if text == "":
            return default
        return float(text)
    except Exception:
        return default


def norm_team(value: Any) -> str:
    return "".join(ch.lower() for ch in txt(value) if ch.isalnum())


def match_key(home: Any, away: Any) -> str:
    return f"{norm_team(home)}::{norm_team(away)}"


def parse_score(value: Any) -> tuple[int, int] | None:
    text = txt(value)
    if "-" not in text:
        return None
    left, right = text.split("-", 1)
    if not left.strip().isdigit() or not right.strip().isdigit():
        return None
    return int(left), int(right)


def score_outcome(scoreline: Any) -> str:
    parsed = parse_score(scoreline)
    if parsed is None:
        return ""
    if parsed[0] > parsed[1]:
        return "home"
    if parsed[1] > parsed[0]:
        return "away"
    return "draw"


def depth_class(count: int) -> str:
    if count >= 5:
        return "strong"
    if count >= 2:
        return "medium"
    if count == 1:
        return "weak"
    return "unavailable"


def load_csv(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    return pd.read_csv(p).fillna("")


def add_market_depth(validation: pd.DataFrame, flat_odds: pd.DataFrame) -> pd.DataFrame:
    out = validation.copy()
    if flat_odds.empty:
        out["h2h_bookmaker_count"] = 0
        out["totals_bookmaker_count"] = 0
        out["spreads_bookmaker_count"] = 0
        out["market_depth_class"] = "unavailable"
        return out

    counts: dict[str, dict[str, int]] = {}
    for _, row in flat_odds.iterrows():
        key = match_key(row.get("home_team"), row.get("away_team"))
        market = txt(row.get("market")).lower()
        bookmaker = txt(row.get("bookmaker_title")) or txt(row.get("bookmaker_key"))
        if not bookmaker:
            continue
        counts.setdefault(key, {"h2h": 0, "totals": 0, "spreads": 0})
        counts[key].setdefault(f"_{market}_books", set())
        counts[key][f"_{market}_books"].add(bookmaker)

    clean_counts: dict[str, dict[str, int]] = {}
    for key, values in counts.items():
        clean_counts[key] = {
            "h2h": len(values.get("_h2h_books", set())),
            "totals": len(values.get("_totals_books", set())),
            "spreads": len(values.get("_spreads_books", set())),
        }

    h2h_counts: list[int] = []
    totals_counts: list[int] = []
    spreads_counts: list[int] = []
    classes: list[str] = []
    for _, row in out.iterrows():
        key = match_key(row.get("home_team"), row.get("away_team"))
        c = clean_counts.get(key, {"h2h": 0, "totals": 0, "spreads": 0})
        h2h_counts.append(c["h2h"])
        totals_counts.append(c["totals"])
        spreads_counts.append(c["spreads"])
        classes.append(depth_class(max(c["h2h"], c["totals"], c["spreads"])))

    out["h2h_bookmaker_count"] = h2h_counts
    out["totals_bookmaker_count"] = totals_counts
    out["spreads_bookmaker_count"] = spreads_counts
    out["market_depth_class"] = classes
    return out


def latest_previous(history: pd.DataFrame, snapshot_id: str) -> pd.DataFrame:
    if history.empty or "snapshot_id" not in history.columns:
        return pd.DataFrame()
    older = history[history["snapshot_id"].astype(str) != snapshot_id].copy()
    if older.empty:
        return pd.DataFrame()
    older = older.sort_values("snapshot_id")
    previous_id = older.iloc[-1]["snapshot_id"]
    return older[older["snapshot_id"].astype(str).eq(str(previous_id))].copy()


def movement_direction(delta: float, threshold: float) -> str:
    if math.isnan(delta):
        return "unavailable"
    if delta >= threshold:
        return "up"
    if delta <= -threshold:
        return "down"
    return "stable"


def pick_market_prob(row: pd.Series, pick_col: str = "final_pick") -> float:
    outcome = score_outcome(row.get(pick_col))
    if outcome not in {"home", "draw", "away"}:
        return float("nan")
    return fnum(row.get(f"market_p_{outcome}"), float("nan"))


def build_movement_report(current: pd.DataFrame, previous: pd.DataFrame, threshold: float) -> pd.DataFrame:
    previous_lookup = {
        match_key(row.get("home_team"), row.get("away_team")): row
        for _, row in previous.iterrows()
    }
    rows: list[dict[str, Any]] = []
    for _, row in current.iterrows():
        key = match_key(row.get("home_team"), row.get("away_team"))
        prev = previous_lookup.get(key)
        current_pick_prob = pick_market_prob(row)
        previous_pick_prob = pick_market_prob(prev) if prev is not None else float("nan")
        pick_prob_delta = current_pick_prob - previous_pick_prob if not math.isnan(current_pick_prob) and not math.isnan(previous_pick_prob) else float("nan")

        current_favourite = txt(row.get("market_favourite"))
        previous_favourite = txt(prev.get("market_favourite")) if prev is not None else ""
        current_total_lean = txt(row.get("market_total_lean"))
        previous_total_lean = txt(prev.get("market_total_lean")) if prev is not None else ""
        current_spread = fnum(row.get("avg_home_spread"), float("nan"))
        previous_spread = fnum(prev.get("avg_home_spread"), float("nan")) if prev is not None else float("nan")
        spread_delta = current_spread - previous_spread if not math.isnan(current_spread) and not math.isnan(previous_spread) else float("nan")

        rows.append(
            {
                "snapshot_id": txt(row.get("snapshot_id")),
                "previous_snapshot_id": txt(prev.get("snapshot_id")) if prev is not None else "",
                "home_team": txt(row.get("home_team")),
                "away_team": txt(row.get("away_team")),
                "final_pick": txt(row.get("final_pick")),
                "market_available": txt(row.get("market_available")),
                "market_depth_class": txt(row.get("market_depth_class")),
                "h2h_bookmaker_count": int(fnum(row.get("h2h_bookmaker_count"), 0.0)),
                "totals_bookmaker_count": int(fnum(row.get("totals_bookmaker_count"), 0.0)),
                "spreads_bookmaker_count": int(fnum(row.get("spreads_bookmaker_count"), 0.0)),
                "current_pick_market_prob": None if math.isnan(current_pick_prob) else round(current_pick_prob, 6),
                "previous_pick_market_prob": None if math.isnan(previous_pick_prob) else round(previous_pick_prob, 6),
                "pick_market_prob_delta": None if math.isnan(pick_prob_delta) else round(pick_prob_delta, 6),
                "pick_market_movement": movement_direction(pick_prob_delta, threshold),
                "market_moving_against_pick": bool(not math.isnan(pick_prob_delta) and pick_prob_delta <= -threshold),
                "market_moving_towards_pick": bool(not math.isnan(pick_prob_delta) and pick_prob_delta >= threshold),
                "current_market_favourite": current_favourite,
                "previous_market_favourite": previous_favourite,
                "market_favourite_changed": bool(previous_favourite and current_favourite and previous_favourite != current_favourite),
                "current_total_lean": current_total_lean,
                "previous_total_lean": previous_total_lean,
                "total_lean_changed": bool(previous_total_lean and current_total_lean and previous_total_lean != current_total_lean),
                "current_avg_home_spread": None if math.isnan(current_spread) else round(current_spread, 6),
                "previous_avg_home_spread": None if math.isnan(previous_spread) else round(previous_spread, 6),
                "spread_delta": None if math.isnan(spread_delta) else round(spread_delta, 6),
            }
        )
    return pd.DataFrame(rows)


def main() -> int:
    args = build_parser().parse_args()
    snapshot_id = args.snapshot_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    validation = load_csv(args.market_validation_csv)
    if validation.empty:
        raise FileNotFoundError(f"No market validation rows found at {args.market_validation_csv}")
    flat_odds = load_csv(args.market_odds_flat_csv)

    current = add_market_depth(validation, flat_odds)
    current["snapshot_id"] = snapshot_id
    current["snapshot_created_at_utc"] = datetime.now(timezone.utc).isoformat()

    history_path = Path(args.history_csv)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    existing_history = load_csv(history_path)
    if not existing_history.empty and "snapshot_id" in existing_history.columns:
        existing_history = existing_history[~existing_history["snapshot_id"].astype(str).eq(snapshot_id)]
    history = pd.concat([existing_history, current], ignore_index=True).fillna("") if not existing_history.empty else current
    history.to_csv(history_path, index=False)

    previous = latest_previous(history, snapshot_id)
    movement = build_movement_report(current, previous, args.movement_threshold)
    movement_path = out_dir / "market_odds_movement_report.csv"
    current_snapshot_path = out_dir / "market_odds_snapshot_current.csv"
    summary_path = out_dir / "market_odds_history_summary.json"

    current.to_csv(current_snapshot_path, index=False)
    movement.to_csv(movement_path, index=False)

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "snapshot_id": snapshot_id,
        "snapshot_rows": int(len(current)),
        "history_rows": int(len(history)),
        "previous_snapshot_id": txt(previous.iloc[0].get("snapshot_id")) if not previous.empty else "",
        "market_depth_counts": current["market_depth_class"].value_counts().to_dict(),
        "movement_counts": movement["pick_market_movement"].value_counts().to_dict(),
        "moving_against_pick_count": int(movement["market_moving_against_pick"].sum()) if not movement.empty else 0,
        "moving_towards_pick_count": int(movement["market_moving_towards_pick"].sum()) if not movement.empty else 0,
        "outputs": {
            "history": str(history_path),
            "current_snapshot": str(current_snapshot_path),
            "movement_report": str(movement_path),
        },
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))
    print(f"Wrote {history_path}")
    print(f"Wrote {current_snapshot_path}")
    print(f"Wrote {movement_path}")
    print(f"Wrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
