from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract Oddspedia winner and correct-score probabilities from debug page_state JSON files."
    )
    parser.add_argument("--page-debug-dir", default="outputs/oddspedia_page_debug")
    parser.add_argument("--state-glob", default="*_page_state.json")
    parser.add_argument("--out-csv", default="inputs/smartbet_grids/oddspedia_probability_grids_auto.csv")
    parser.add_argument("--out-summary-csv", default="inputs/smartbet_grids/oddspedia_probability_summary_auto.csv")
    parser.add_argument("--out-json", default="outputs/oddspedia_probability_extract/oddspedia_probability_extract_summary.json")
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace("%", "").strip())
    except Exception:
        return None


def slugify(value: Any) -> str:
    text = txt(value).lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def get_path(obj: Any, path: list[str]) -> Any:
    cur = obj
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def find_event_state(data: dict[str, Any]) -> dict[str, Any]:
    for root_key in ("nuxt", "store"):
        event = get_path(data, [root_key, "state", "event"])
        if isinstance(event, dict) and isinstance(event.get("probabilities"), dict):
            return event
        event = get_path(data, [root_key, "event"])
        if isinstance(event, dict) and isinstance(event.get("probabilities"), dict):
            return event
    return {}


def extract_match_identity(data: dict[str, Any], event: dict[str, Any], state_file: Path) -> dict[str, str]:
    row = data.get("row") if isinstance(data.get("row"), dict) else {}
    home = txt(row.get("home_team")) or txt(event.get("ht")) or txt(event.get("home_team"))
    away = txt(row.get("away_team")) or txt(event.get("at")) or txt(event.get("away_team"))
    match_id = txt(row.get("match_id")) or txt(event.get("match_key")) or state_file.name.replace("_page_state.json", "")
    return {
        "match_id": match_id,
        "home_team": home,
        "away_team": away,
        "source_url": txt(row.get("url")),
    }


def score_parts(score_key: str) -> tuple[str, str, str]:
    if re.fullmatch(r"\d+-\d+", score_key):
        home_goals, away_goals = score_key.split("-", 1)
        return home_goals, away_goals, "exact"
    if score_key == "Other_1":
        return "Other", "Other", "other_home_win"
    if score_key == "Other_X":
        return "Other", "Other", "other_draw"
    if score_key == "Other_2":
        return "Other", "Other", "other_away_win"
    return "", "", "unknown"


def extract_from_state_file(state_file: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    data = json.loads(state_file.read_text(encoding="utf-8"))
    event = find_event_state(data)
    ident = extract_match_identity(data, event, state_file)
    probabilities = event.get("probabilities") if isinstance(event, dict) else {}
    winner = probabilities.get("100") if isinstance(probabilities, dict) else None
    correct_score = probabilities.get("800") if isinstance(probabilities, dict) else None

    winner_home = winner_draw = winner_away = None
    if isinstance(winner, list) and len(winner) >= 3:
        winner_home = to_float(winner[0])
        winner_draw = to_float(winner[1])
        winner_away = to_float(winner[2])

    score_probs: dict[str, Any] = {}
    odds_value = odds_bookie = odds_handicap = ""
    if isinstance(correct_score, dict):
        raw_probs = correct_score.get("probabilities")
        if isinstance(raw_probs, dict):
            score_probs = raw_probs
        odds = correct_score.get("odds")
        if isinstance(odds, dict):
            odds_value = txt(odds.get("value"))
            odds_bookie = txt(odds.get("bookie"))
            odds_handicap = txt(odds.get("handicap_name"))

    rows: list[dict[str, Any]] = []
    for score_key, probability in score_probs.items():
        home_goals, away_goals, bucket = score_parts(txt(score_key))
        rows.append(
            {
                **ident,
                "market_id": "800",
                "market_name": "Correct Score",
                "score_key": txt(score_key),
                "home_goals": home_goals,
                "away_goals": away_goals,
                "score_bucket": bucket,
                "probability_pct": to_float(probability),
                "winner_home_pct": winner_home,
                "winner_draw_pct": winner_draw,
                "winner_away_pct": winner_away,
                "best_odds_value": odds_value,
                "best_odds_bookie": odds_bookie,
                "best_odds_scoreline": odds_handicap,
                "source_state_file": str(state_file),
            }
        )

    modal_score = ""
    modal_probability = None
    exact_rows = [r for r in rows if r["score_bucket"] == "exact" and r["probability_pct"] is not None]
    if exact_rows:
        best = max(exact_rows, key=lambda r: float(r["probability_pct"]))
        modal_score = txt(best["score_key"])
        modal_probability = best["probability_pct"]

    summary = {
        **ident,
        "winner_home_pct": winner_home,
        "winner_draw_pct": winner_draw,
        "winner_away_pct": winner_away,
        "correct_score_count": len(rows),
        "modal_correct_score": modal_score,
        "modal_correct_score_pct": modal_probability,
        "best_odds_value": odds_value,
        "best_odds_bookie": odds_bookie,
        "best_odds_scoreline": odds_handicap,
        "source_state_file": str(state_file),
    }
    return rows, summary


def main() -> int:
    args = build_parser().parse_args()
    state_files = sorted(Path(args.page_debug_dir).glob(args.state_glob))
    if not state_files:
        raise FileNotFoundError(f"No state files found under {args.page_debug_dir} using glob {args.state_glob}")

    all_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for state_file in state_files:
        try:
            rows, summary = extract_from_state_file(state_file)
            all_rows.extend(rows)
            summaries.append(summary)
        except Exception as exc:
            errors.append({"state_file": str(state_file), "error": str(exc)})

    out_csv = Path(args.out_csv)
    out_summary_csv = Path(args.out_summary_csv)
    out_json = Path(args.out_json)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_summary_csv.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(all_rows).to_csv(out_csv, index=False)
    pd.DataFrame(summaries).to_csv(out_summary_csv, index=False)

    summary_payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "state_file_count": len(state_files),
        "match_count": len(summaries),
        "correct_score_row_count": len(all_rows),
        "error_count": len(errors),
        "errors": errors,
        "out_csv": str(out_csv),
        "out_summary_csv": str(out_summary_csv),
    }
    out_json.write_text(json.dumps(summary_payload, indent=2), encoding="utf-8")
    print(json.dumps(summary_payload, indent=2))
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_summary_csv}")
    print(f"Wrote {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
