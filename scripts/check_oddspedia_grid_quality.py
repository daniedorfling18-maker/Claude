from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PICK_COLUMNS = [
    "daily_robust_pick",
    "robust_locked_pick",
    "locked_pick",
    "private_chase_scoreline",
    "final_pick",
    "recommended_scoreline",
    "pick",
    "scoreline",
]
EXPECTED_EXACT_SCORES = [f"{h}-{a}" for h in range(4) for a in range(4)]
EXPECTED_OTHER_SCORES = ["Other_1", "Other_X", "Other_2"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate Oddspedia grid scrape integrity without failing the daily run.")
    parser.add_argument("--locked-picks-csv", default="outputs/final_locked_picks/superbru_final_card.csv")
    parser.add_argument("--oddspedia-grid-csv", default="inputs/smartbet_grids/oddspedia_probability_grids_auto.csv")
    parser.add_argument("--oddspedia-summary-csv", default="inputs/smartbet_grids/oddspedia_probability_summary_auto.csv")
    parser.add_argument("--markets-summary-csv", default="inputs/smartbet_grids/oddspedia_markets_summary_auto.csv")
    parser.add_argument("--urls-csv", default="inputs/oddspedia_match_urls.csv")
    parser.add_argument("--out-csv", default="outputs/oddspedia_probability_extract/oddspedia_grid_quality.csv")
    parser.add_argument("--out-summary-json", default="outputs/oddspedia_probability_extract/oddspedia_grid_quality_summary.json")
    parser.add_argument("--probability-total-min", type=float, default=98.0)
    parser.add_argument("--probability-total-max", type=float, default=102.0)
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def to_float(value: Any) -> float | None:
    try:
        s = txt(value).replace("%", "")
        return float(s) if s else None
    except Exception:
        return None


def slugify(value: Any) -> str:
    text = txt(value).lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def match_key(home: Any, away: Any) -> str:
    return f"{slugify(home)}-{slugify(away)}"


def parse_score(score: Any) -> tuple[int | None, int | None]:
    m = re.search(r"(\d+)\s*-\s*(\d+)", txt(score))
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def load_csv(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(p).fillna("")
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def ensure_match_id(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    out = frame.copy()
    if "match_id" not in out.columns:
        out["match_id"] = out.apply(lambda r: match_key(r.get("home_team"), r.get("away_team")), axis=1)
    return out


def find_pick_column(frame: pd.DataFrame) -> str | None:
    for col in PICK_COLUMNS:
        if col in frame.columns:
            return col
    return None


def pick_lookup(locked: pd.DataFrame) -> dict[str, str]:
    if locked.empty:
        return {}
    col = find_pick_column(locked)
    if not col:
        return {}
    return {txt(r.get("match_id")): txt(r.get(col)) for _, r in locked.iterrows()}


def url_lookup(urls: pd.DataFrame) -> dict[str, dict[str, str]]:
    if urls.empty:
        return {}
    return {txt(r.get("match_id")): {k: txt(r.get(k)) for k in urls.columns} for _, r in urls.iterrows()}


def _winner_direction(p_home: float | None, p_draw: float | None, p_away: float | None) -> str:
    vals = [(p_home, "home"), (p_draw, "draw"), (p_away, "away")]
    valid = [(v, lbl) for v, lbl in vals if v is not None]
    return max(valid, key=lambda x: x[0])[1] if valid else "unknown"


def validate_match(
    mid: str,
    g: pd.DataFrame,
    summary_row: pd.Series | None,
    locked_pick: str,
    url_row: dict[str, str],
    min_total: float,
    max_total: float,
    markets_row: pd.Series | None = None,
) -> dict[str, Any]:
    score_keys = set(g.get("score_key", pd.Series(dtype=str)).map(txt).tolist()) if not g.empty else set()
    missing_exact = [s for s in EXPECTED_EXACT_SCORES if s not in score_keys]
    missing_other = [s for s in EXPECTED_OTHER_SCORES if s not in score_keys]
    row_count = int(len(g))
    prob_total = float(g.get("probability_pct", pd.Series(dtype=float)).map(lambda v: to_float(v) or 0.0).sum()) if not g.empty else 0.0

    winner_home = winner_draw = winner_away = None
    source_url = current_url = modal_score = ""
    if summary_row is not None:
        winner_home = to_float(summary_row.get("winner_home_pct"))
        winner_draw = to_float(summary_row.get("winner_draw_pct"))
        winner_away = to_float(summary_row.get("winner_away_pct"))
        source_url = txt(summary_row.get("source_url"))
        current_url = txt(summary_row.get("current_url"))
        modal_score = txt(summary_row.get("modal_correct_score"))
    winner_total = sum(v for v in [winner_home, winner_draw, winner_away] if v is not None)

    # Market 100 (1X2) direct probabilities from markets summary
    market_home = market_draw = market_away = None
    market_1x2_consistent = True  # passes if no market data available
    if markets_row is not None:
        market_home = to_float(markets_row.get("p_home_win"))
        market_draw = to_float(markets_row.get("p_draw"))
        market_away = to_float(markets_row.get("p_away_win"))
        if market_home is not None and winner_home is not None:
            grid_dir = _winner_direction(winner_home, winner_draw, winner_away)
            mkt_dir = _winner_direction(market_home, market_draw, market_away)
            market_1x2_consistent = grid_dir == mkt_dir

    locked_h, locked_a = parse_score(locked_pick)
    locked_outside_grid = bool(locked_h is not None and locked_a is not None and (locked_h > 3 or locked_a > 3))
    locked_required = bool(locked_pick and locked_h is not None and locked_a is not None and not locked_outside_grid)
    locked_score_present = (not locked_required) or locked_pick in score_keys

    checks = {
        "has_grid": row_count > 0,
        "row_count_usually_19": row_count == 19,
        "has_exact_0_0_to_3_3": len(missing_exact) == 0,
        "has_other_rows": len(missing_other) == 0,
        "correct_score_probability_total_ok": min_total <= prob_total <= max_total,
        "winner_probability_total_ok": min_total <= winner_total <= max_total if winner_total else False,
        "market_1x2_vs_grid_direction_consistent": market_1x2_consistent,
        "source_url_loaded_successfully": bool(source_url or current_url),
        "modal_score_present": bool(modal_score),
        "locked_score_present_or_outside_grid": locked_score_present,
    }
    failed = [name for name, ok in checks.items() if not ok]
    return {
        "match_id": mid,
        "commence_time": txt(summary_row.get("commence_time")) if summary_row is not None else txt(url_row.get("commence_time")),
        "home_team": txt(summary_row.get("home_team")) if summary_row is not None else txt(url_row.get("home_team")),
        "away_team": txt(summary_row.get("away_team")) if summary_row is not None else txt(url_row.get("away_team")),
        "correct_score_row_count": row_count,
        "missing_exact_scores": ";".join(missing_exact),
        "missing_other_rows": ";".join(missing_other),
        "correct_score_probability_total_pct": prob_total,
        "winner_probability_total_pct": winner_total,
        "winner_home_pct": winner_home,
        "winner_draw_pct": winner_draw,
        "winner_away_pct": winner_away,
        "modal_correct_score": modal_score,
        "market_p_home_win": market_home,
        "market_p_draw": market_draw,
        "market_p_away_win": market_away,
        "locked_pick": locked_pick,
        "locked_pick_outside_0_3_grid": locked_outside_grid,
        "source_url": source_url or txt(url_row.get("url")) or txt(url_row.get("alt_url")),
        "current_url": current_url,
        **checks,
        "quality_status": "ok" if not failed else "warning",
        "failed_checks": ";".join(failed),
        "failed_check_count": len(failed),
    }


def main() -> int:
    args = build_parser().parse_args()
    grid = ensure_match_id(load_csv(args.oddspedia_grid_csv))
    summary = ensure_match_id(load_csv(args.oddspedia_summary_csv))
    markets = ensure_match_id(load_csv(args.markets_summary_csv))
    locked = ensure_match_id(load_csv(args.locked_picks_csv))
    urls = ensure_match_id(load_csv(args.urls_csv))
    locked_by_mid = pick_lookup(locked)
    urls_by_mid = url_lookup(urls)

    match_ids = set()
    for frame in [urls, summary, grid, locked]:
        if not frame.empty and "match_id" in frame.columns:
            match_ids.update(txt(v) for v in frame["match_id"].tolist() if txt(v))

    rows: list[dict[str, Any]] = []
    for mid in sorted(match_ids):
        g = grid[grid["match_id"].astype(str).eq(mid)].copy() if not grid.empty else pd.DataFrame()
        sframe = summary[summary["match_id"].astype(str).eq(mid)] if not summary.empty else pd.DataFrame()
        srow = sframe.iloc[0] if not sframe.empty else None
        mframe = markets[markets["match_id"].astype(str).eq(mid)] if not markets.empty else pd.DataFrame()
        mrow = mframe.iloc[0] if not mframe.empty else None
        rows.append(
            validate_match(
                mid,
                g,
                srow,
                locked_by_mid.get(mid, ""),
                urls_by_mid.get(mid, {}),
                args.probability_total_min,
                args.probability_total_max,
                markets_row=mrow,
            )
        )

    out = pd.DataFrame(rows)
    out_csv = Path(args.out_csv)
    out_json = Path(args.out_summary_json)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_csv, index=False)

    warning_rows = int((out.get("quality_status", pd.Series(dtype=str)) == "warning").sum()) if not out.empty else 0
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "match_count": int(len(out)),
        "warning_match_count": warning_rows,
        "ok_match_count": int(len(out) - warning_rows),
        "failed_check_counts": out["failed_checks"].value_counts(dropna=False).to_dict() if not out.empty else {},
        "out_csv": str(out_csv),
        "note": "Warnings are diagnostic only. This script exits 0 so the daily run can continue.",
    }
    out_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    if warning_rows:
        print(f"WARNING: Oddspedia grid quality found {warning_rows} match(es) with diagnostic warnings.")
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
