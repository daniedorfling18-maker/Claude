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
    "final_pick",
    "recommended_scoreline",
    "private_chase_scoreline",
    "pick",
    "scoreline",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a post-prediction validation overlay. This is a review/validator layer only: "
            "it does not alter scoreline probabilities, candidate EV, locked picks, or model calibration."
        )
    )
    parser.add_argument("--locked-picks-csv", default="outputs/final_locked_picks/superbru_final_card.csv")
    parser.add_argument("--oddspedia-comparison-csv", default="outputs/oddspedia_pick_validation/oddspedia_pick_comparison.csv")
    parser.add_argument("--superbru-coverage-csv", default="outputs/data_inventory/superbru_visible_pick_coverage.csv")
    parser.add_argument("--superbru-detected-controls-csv", default="outputs/data_inventory/superbru_detected_match_controls.csv")
    parser.add_argument("--out-csv", default="outputs/validation/pick_validation_overlay.csv")
    parser.add_argument("--out-json", default="outputs/validation/pick_validation_overlay_summary.json")
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
        if not s:
            return None
        return float(s)
    except Exception:
        return None


def slugify(value: Any) -> str:
    text = txt(value).lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def match_key(home: Any, away: Any) -> str:
    return f"{slugify(home)}-{slugify(away)}"


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path).fillna("")
    except Exception:
        return pd.DataFrame()


def find_pick_column(frame: pd.DataFrame) -> str:
    for col in PICK_COLUMNS:
        if col in frame.columns:
            return col
    return ""


def parse_score(score: Any) -> tuple[int | None, int | None]:
    m = re.search(r"(\d+)\s*[-:]\s*(\d+)", txt(score))
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def outcome_from_score(score: Any) -> str:
    h, a = parse_score(score)
    if h is None or a is None:
        return "unknown"
    if h > a:
        return "home"
    if h < a:
        return "away"
    return "draw"


def boolish(value: Any) -> bool:
    return txt(value).lower() in {"true", "1", "yes", "y"}


def smartbet_validator_action(odd_row: pd.Series | None) -> tuple[str, str]:
    if odd_row is None:
        return "no_grid", "no smartbet/Oddspedia comparison row available"
    raw_action = txt(odd_row.get("action"))
    reason = txt(odd_row.get("reason")) or raw_action
    gap = to_float(odd_row.get("probability_gap_vs_locked_pct"))
    same_outcome = boolish(odd_row.get("same_outcome_as_locked"))

    if raw_action == "no_grid":
        return "no_grid", reason or "no smartbet probability grid for this match"
    if "market_outcome_conflict" in raw_action:
        return "manual_review", reason or "smartbet validator implies a different result outcome"
    if "review_missing_locked_score" in raw_action:
        return "manual_review", reason or "locked score is outside the available smartbet exact-score grid"
    if gap is not None and gap >= 4 and not same_outcome:
        return "manual_review", "large smartbet probability gap and different outcome"
    if gap is not None and gap >= 4 and same_outcome:
        return "watch", "large smartbet same-outcome exact-score gap"
    if "strong_same_outcome" in raw_action or "same_outcome_review" in raw_action:
        return "watch", reason or "smartbet prefers a different same-outcome scoreline"
    return "keep", reason or "locked pick is acceptable under smartbet validator"


def superbru_summary_for_match(match_id: str, coverage: pd.DataFrame, controls: pd.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {
        "superbru_data_available": False,
        "superbru_detected_scoreline_count": 0,
        "superbru_top_detected_scoreline": "",
        "superbru_top_detected_count": 0,
        "superbru_detected_scorelines": "",
        "superbru_detected_control_sample": "",
    }
    if not coverage.empty and "match_id" in coverage.columns:
        cm = coverage[coverage["match_id"].astype(str).eq(match_id)].copy()
        if not cm.empty:
            out["superbru_data_available"] = True
            if "detected_scoreline" in cm.columns:
                scorelines = [txt(v) for v in cm["detected_scoreline"] if txt(v)]
                if scorelines:
                    counts = pd.Series(scorelines).value_counts()
                    out["superbru_detected_scoreline_count"] = int(len(scorelines))
                    out["superbru_top_detected_scoreline"] = txt(counts.index[0])
                    out["superbru_top_detected_count"] = int(counts.iloc[0])
                    out["superbru_detected_scorelines"] = ";".join(counts.index.astype(str).tolist()[:8])
            if "nearby_text_sample" in cm.columns:
                samples = [txt(v) for v in cm["nearby_text_sample"] if txt(v)]
                if samples:
                    out["superbru_detected_control_sample"] = samples[0][:240]
    if not controls.empty and "match_id" in controls.columns:
        cc = controls[controls["match_id"].astype(str).eq(match_id)].copy()
        if not cc.empty:
            out["superbru_data_available"] = True
            if not txt(out["superbru_detected_control_sample"]) and "detected_control_text" in cc.columns:
                samples = [txt(v) for v in cc["detected_control_text"] if txt(v)]
                if samples:
                    out["superbru_detected_control_sample"] = samples[0][:240]
            if int(out["superbru_detected_scoreline_count"] or 0) == 0 and "detected_scoreline" in cc.columns:
                scorelines = [txt(v) for v in cc["detected_scoreline"] if txt(v)]
                if scorelines:
                    counts = pd.Series(scorelines).value_counts()
                    out["superbru_detected_scoreline_count"] = int(len(scorelines))
                    out["superbru_top_detected_scoreline"] = txt(counts.index[0])
                    out["superbru_top_detected_count"] = int(counts.iloc[0])
                    out["superbru_detected_scorelines"] = ";".join(counts.index.astype(str).tolist()[:8])
    return out


def superbru_game_theory_action(locked_pick: str, summary: dict[str, Any]) -> tuple[str, str]:
    if not summary.get("superbru_data_available"):
        return "no_superbru_data", "no Superbru game-theory/context data available"
    top_score = txt(summary.get("superbru_top_detected_scoreline"))
    detected_scores = [s for s in txt(summary.get("superbru_detected_scorelines")).split(";") if s]
    if not top_score and not detected_scores:
        return "context_available", "Superbru fixture context available, but no scoreline context detected"
    if locked_pick in detected_scores or locked_pick == top_score:
        return "keep", "locked pick is present in detected Superbru scoreline context"
    locked_outcome = outcome_from_score(locked_pick)
    top_outcome = outcome_from_score(top_score)
    if top_score and locked_outcome != "unknown" and top_outcome != "unknown" and locked_outcome != top_outcome:
        return "manual_review", "Superbru context points to a different outcome; review as game-theory signal only"
    if top_score and locked_outcome == top_outcome:
        return "watch", "Superbru context favours a different same-outcome scoreline; possible chase/leverage consideration"
    return "watch", "Superbru scoreline context differs from locked pick"


def final_review_action(smartbet_action: str, superbru_action: str) -> str:
    if "manual_review" in {smartbet_action, superbru_action}:
        return "manual_review"
    if "watch" in {smartbet_action, superbru_action}:
        return "watch"
    if smartbet_action == "no_grid" and superbru_action == "no_superbru_data":
        return "keep_unvalidated"
    return "keep"


def build_overlay(args: argparse.Namespace) -> tuple[pd.DataFrame, dict[str, Any]]:
    locked = read_csv(Path(args.locked_picks_csv))
    if locked.empty:
        raise FileNotFoundError(f"Missing or empty locked picks CSV: {args.locked_picks_csv}")
    if "match_id" not in locked.columns:
        locked["match_id"] = locked.apply(lambda r: match_key(r.get("home_team"), r.get("away_team")), axis=1)
    pick_col = find_pick_column(locked)
    if not pick_col:
        raise ValueError(f"Could not find locked pick column. Expected one of: {', '.join(PICK_COLUMNS)}")

    odd = read_csv(Path(args.oddspedia_comparison_csv))
    if not odd.empty and "match_id" not in odd.columns:
        odd["match_id"] = odd.apply(lambda r: match_key(r.get("home_team"), r.get("away_team")), axis=1)
    coverage = read_csv(Path(args.superbru_coverage_csv))
    controls = read_csv(Path(args.superbru_detected_controls_csv))

    rows: list[dict[str, Any]] = []
    for _, row in locked.iterrows():
        mid = txt(row.get("match_id")) or match_key(row.get("home_team"), row.get("away_team"))
        locked_pick = txt(row.get(pick_col))
        odd_row = None
        if not odd.empty and "match_id" in odd.columns:
            match = odd[odd["match_id"].astype(str).eq(mid)]
            if not match.empty:
                odd_row = match.iloc[0]
        smart_action, smart_reason = smartbet_validator_action(odd_row)
        sup_summary = superbru_summary_for_match(mid, coverage, controls)
        sup_action, sup_reason = superbru_game_theory_action(locked_pick, sup_summary)
        final_action = final_review_action(smart_action, sup_action)

        chase_pick = txt(row.get("private_chase_scoreline")) or txt(row.get("chase_scoreline"))
        chase_ev_loss = txt(row.get("private_chase_ev_loss")) or txt(row.get("chase_ev_loss"))

        out_row = {
            "match_id": mid,
            "commence_time": txt(row.get("commence_time")),
            "home_team": txt(row.get("home_team")),
            "away_team": txt(row.get("away_team")),
            "locked_pick_column": pick_col,
            "locked_pick": locked_pick,
            "locked_expected_points": txt(row.get("expected_points")) or txt(row.get("locked_expected_points")),
            "locked_p_exact": txt(row.get("p_exact")) or txt(row.get("locked_p_exact")),
            "locked_outcome": outcome_from_score(locked_pick),
            "smartbet_validator_action": smart_action,
            "smartbet_validator_reason": smart_reason,
            "smartbet_best_score": txt(odd_row.get("oddspedia_best_score")) if odd_row is not None else "",
            "smartbet_best_probability_pct": txt(odd_row.get("oddspedia_best_probability_pct")) if odd_row is not None else "",
            "smartbet_locked_probability_pct": txt(odd_row.get("locked_pick_probability_pct")) if odd_row is not None else "",
            "smartbet_probability_gap_vs_locked_pct": txt(odd_row.get("probability_gap_vs_locked_pct")) if odd_row is not None else "",
            "smartbet_modal_score": txt(odd_row.get("modal_correct_score")) if odd_row is not None else "",
            "smartbet_top_scores": ";".join([txt(odd_row.get(f"oddspedia_top{i}_score")) for i in [1, 2, 3] if odd_row is not None and txt(odd_row.get(f"oddspedia_top{i}_score"))]),
            "superbru_game_theory_action": sup_action,
            "superbru_game_theory_reason": sup_reason,
            **sup_summary,
            "chase_candidate": chase_pick,
            "chase_ev_loss": chase_ev_loss,
            "final_review_action": final_action,
        }
        rows.append(out_row)

    out = pd.DataFrame(rows)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "validator_overlay_only_no_model_math_changed",
        "locked_picks_csv": args.locked_picks_csv,
        "pick_column": pick_col,
        "oddspedia_comparison_csv": args.oddspedia_comparison_csv,
        "superbru_coverage_csv": args.superbru_coverage_csv,
        "superbru_detected_controls_csv": args.superbru_detected_controls_csv,
        "match_count": int(len(out)),
        "final_review_action_counts": out["final_review_action"].value_counts(dropna=False).to_dict() if not out.empty else {},
        "smartbet_validator_action_counts": out["smartbet_validator_action"].value_counts(dropna=False).to_dict() if not out.empty else {},
        "superbru_game_theory_action_counts": out["superbru_game_theory_action"].value_counts(dropna=False).to_dict() if not out.empty else {},
        "note": "This overlay validates locked model picks. It does not alter model calibration, scoreline probabilities, candidate EV, or locked picks.",
    }
    return out, payload


def main() -> int:
    args = build_parser().parse_args()
    out, payload = build_overlay(args)
    out_csv = Path(args.out_csv)
    out_json = Path(args.out_json)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_csv, index=False)
    payload["out_csv"] = str(out_csv)
    payload["out_json"] = str(out_json)
    out_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
