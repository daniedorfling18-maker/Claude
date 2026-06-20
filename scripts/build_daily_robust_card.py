from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

SEVERITY_BUMP = {
    "": 0.0,
    "low": 0.01,
    "medium": 0.025,
    "high": 0.05,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a daily robust final card using market movement, depth, manual flags and anti-flip-flop rules."
    )
    parser.add_argument("--locked-picks-csv", default="outputs/final_locked_picks/final_picks_locked.csv")
    parser.add_argument("--rescore-csv", default="outputs/component_validation/review_rescore_report.csv")
    parser.add_argument("--movement-csv", default="outputs/market_odds_history/market_odds_movement_report.csv")
    parser.add_argument("--manual-flags-csv", default="inputs/manual_match_flags.csv")
    parser.add_argument("--out-dir", default="outputs/daily_robust_card")
    parser.add_argument("--max-ev-loss", type=float, default=0.01)
    parser.add_argument("--min-delta-p-finish-first", type=float, default=-0.001)
    parser.add_argument("--min-candidate-component-score", type=float, default=0.65)
    parser.add_argument("--min-component-improvement", type=float, default=0.075)
    parser.add_argument("--min-bookmakers", type=int, default=2)
    parser.add_argument("--require-two-day-support", action="store_true")
    parser.add_argument("--allow-first-snapshot", action="store_true", help="Allow switches when no previous market snapshot exists.")
    parser.add_argument("--require-hard-rule", action="store_true")
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


def bval(value: Any) -> bool:
    return txt(value).lower() in {"true", "1", "yes", "y"}


def norm_team(value: Any) -> str:
    return "".join(ch.lower() for ch in txt(value) if ch.isalnum())


def match_key(home: Any, away: Any) -> tuple[str, str]:
    return norm_team(home), norm_team(away)


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


def load_csv(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    return pd.read_csv(p).fillna("")


def choose_pick_column(frame: pd.DataFrame) -> str:
    for column in ["locked_pick", "final_pick", "recommended_scoreline", "raw_pick", "pick"]:
        if column in frame.columns:
            return column
    raise ValueError("Could not find a pick column in locked/final picks CSV.")


def hours_to_kickoff(row: pd.Series) -> float:
    text = txt(row.get("commence_time"))
    if not text:
        return float("nan")
    try:
        if text.endswith("Z"):
            text = text.replace("Z", "+00:00")
        kickoff = datetime.fromisoformat(text)
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        return (kickoff - datetime.now(timezone.utc)).total_seconds() / 3600.0
    except Exception:
        return float("nan")


def closing_adjusted_component_floor(base: float, hours: float) -> tuple[float, str]:
    if math.isnan(hours):
        return base, "unknown_kickoff"
    if hours <= 6:
        return max(0.60, base - 0.05), "inside_6h_market_priority"
    if hours <= 24:
        return max(0.63, base - 0.02), "inside_24h_market_priority"
    if hours <= 72:
        return base, "24_to_72h_balanced"
    return max(0.70, base + 0.05), "outside_72h_model_priority"


def load_manual_flags(path: str | Path) -> dict[tuple[str, str], dict[str, Any]]:
    frame = load_csv(path)
    if frame.empty:
        return {}
    flags: dict[tuple[str, str], dict[str, Any]] = {}
    for _, row in frame.iterrows():
        key = match_key(row.get("home_team"), row.get("away_team"))
        severity = txt(row.get("severity")).lower()
        current = flags.get(key, {"severity": "", "flag_types": [], "notes": [], "block_switch": False})
        if SEVERITY_BUMP.get(severity, 0.0) > SEVERITY_BUMP.get(current.get("severity", ""), 0.0):
            current["severity"] = severity
        if txt(row.get("flag_type")):
            current["flag_types"].append(txt(row.get("flag_type")))
        if txt(row.get("notes")):
            current["notes"].append(txt(row.get("notes")))
        current["block_switch"] = bool(current.get("block_switch")) or bval(row.get("block_switch"))
        flags[key] = current
    return flags


def market_candidate_previous_supported(candidate_pick: str, movement: pd.Series | None) -> bool | None:
    if movement is None:
        return None
    previous_favourite = txt(movement.get("previous_market_favourite"))
    if not previous_favourite:
        return None
    outcome = score_outcome(candidate_pick)
    return outcome == previous_favourite


def movement_lookup(movement: pd.DataFrame) -> dict[tuple[str, str], pd.Series]:
    return {
        match_key(row.get("home_team"), row.get("away_team")): row
        for _, row in movement.iterrows()
    }


def evaluate_switch(
    args: argparse.Namespace,
    candidate: pd.Series,
    locked_row: pd.Series,
    movement_row: pd.Series | None,
    manual: dict[str, Any] | None,
) -> tuple[bool, dict[str, Any]]:
    ev_loss = fnum(candidate.get("ev_loss"), 999.0)
    delta = fnum(candidate.get("delta_p_finish_first"), -999.0)
    component = fnum(candidate.get("candidate_component_score"), 0.0)
    improvement = fnum(candidate.get("component_improvement"), 0.0)
    hours = hours_to_kickoff(locked_row)
    component_floor, time_bucket = closing_adjusted_component_floor(args.min_candidate_component_score, hours)

    manual = manual or {"severity": "", "flag_types": [], "notes": [], "block_switch": False}
    severity = txt(manual.get("severity")).lower()
    manual_bump = SEVERITY_BUMP.get(severity, 0.0)
    improvement_floor = args.min_component_improvement + manual_bump

    depth_count = 0
    depth_class = "unavailable"
    movement_against = False
    previous_supported = None
    has_previous = False
    if movement_row is not None:
        depth_count = int(fnum(movement_row.get("h2h_bookmaker_count"), 0.0))
        depth_class = txt(movement_row.get("market_depth_class"))
        movement_against = bval(movement_row.get("market_moving_against_pick"))
        previous_supported = market_candidate_previous_supported(txt(candidate.get("candidate_pick")), movement_row)
        has_previous = bool(txt(movement_row.get("previous_snapshot_id")))

    checks = {
        "recommendation_is_switch_candidate": txt(candidate.get("recommendation")).lower() == "switch_candidate",
        "low_ev_cost": ev_loss <= args.max_ev_loss,
        "leader_delta_safe": delta >= args.min_delta_p_finish_first,
        "candidate_component_supported": component >= component_floor,
        "component_improvement_sufficient": improvement >= improvement_floor,
        "market_depth_sufficient": depth_count >= args.min_bookmakers,
        "manual_not_blocking": not bool(manual.get("block_switch")),
        "hard_rule_ok": (bval(candidate.get("passes_hard_rule")) if args.require_hard_rule else True),
    }

    if args.require_two_day_support:
        if has_previous:
            checks["two_day_support"] = bool(previous_supported)
        else:
            checks["two_day_support"] = bool(args.allow_first_snapshot)
    else:
        checks["two_day_support"] = True

    decision = all(checks.values())
    reasons = [name for name, ok in checks.items() if ok]
    blockers = [name for name, ok in checks.items() if not ok]

    audit = {
        "home_team": txt(candidate.get("home_team")),
        "away_team": txt(candidate.get("away_team")),
        "current_pick": txt(candidate.get("current_pick")),
        "candidate_pick": txt(candidate.get("candidate_pick")),
        "apply_switch": decision,
        "ev_loss": round(ev_loss, 6),
        "delta_p_finish_first": round(delta, 6),
        "candidate_component_score": round(component, 6),
        "component_floor": round(component_floor, 6),
        "component_improvement": round(improvement, 6),
        "improvement_floor": round(improvement_floor, 6),
        "hours_to_kickoff": None if math.isnan(hours) else round(hours, 2),
        "time_bucket": time_bucket,
        "market_depth_class": depth_class,
        "h2h_bookmaker_count": depth_count,
        "market_moving_against_current_pick": movement_against,
        "previous_candidate_supported": previous_supported,
        "manual_severity": severity,
        "manual_flag_types": ", ".join(manual.get("flag_types", [])),
        "manual_notes": " | ".join(manual.get("notes", [])),
        "passed_checks": "; ".join(reasons),
        "blocked_by": "; ".join(blockers),
        "source_decision_reasons": txt(candidate.get("decision_reasons")),
    }
    return decision, audit


def main() -> int:
    args = build_parser().parse_args()
    locked = load_csv(args.locked_picks_csv)
    rescore = load_csv(args.rescore_csv)
    movement = load_csv(args.movement_csv)
    if locked.empty:
        raise FileNotFoundError(f"No locked/final picks found at {args.locked_picks_csv}")
    if rescore.empty:
        raise FileNotFoundError(f"No rescore rows found at {args.rescore_csv}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pick_col = choose_pick_column(locked)
    locked = locked.copy()
    locked["pre_daily_pick"] = locked[pick_col].map(txt)
    locked["robust_locked_pick"] = locked[pick_col].map(txt)
    locked["daily_switch_applied"] = False
    locked["daily_switch_reason"] = ""

    movement_by_match = movement_lookup(movement) if not movement.empty else {}
    manual_flags = load_manual_flags(args.manual_flags_csv)

    candidates = rescore[rescore["recommendation"].astype(str).str.lower().eq("switch_candidate")].copy()
    candidates = candidates.sort_values(["component_improvement", "ev_loss"], ascending=[False, True])
    candidates["_key"] = candidates.apply(lambda row: match_key(row.get("home_team"), row.get("away_team")), axis=1)
    candidates = candidates.drop_duplicates("_key", keep="first")

    audits: list[dict[str, Any]] = []
    switch_lookup = {row["_key"]: row for _, row in candidates.iterrows()}

    for idx, row in locked.iterrows():
        key = match_key(row.get("home_team"), row.get("away_team"))
        candidate = switch_lookup.get(key)
        if candidate is None:
            continue
        current_pick = txt(candidate.get("current_pick"))
        card_pick = txt(row.get(pick_col))
        if current_pick and card_pick and current_pick != card_pick:
            audits.append(
                {
                    "home_team": txt(row.get("home_team")),
                    "away_team": txt(row.get("away_team")),
                    "current_pick": card_pick,
                    "candidate_pick": txt(candidate.get("candidate_pick")),
                    "apply_switch": False,
                    "blocked_by": f"candidate current_pick mismatch: {current_pick} vs card {card_pick}",
                }
            )
            continue
        apply, audit = evaluate_switch(args, candidate, row, movement_by_match.get(key), manual_flags.get(key))
        audits.append(audit)
        if apply:
            locked.at[idx, "robust_locked_pick"] = txt(candidate.get("candidate_pick"))
            locked.at[idx, "daily_switch_applied"] = True
            locked.at[idx, "daily_switch_reason"] = audit["passed_checks"]

    audit_df = pd.DataFrame(audits)
    card_cols = [col for col in ["commence_time", "home_team", "away_team", "robust_locked_pick"] if col in locked.columns]
    card = locked[card_cols].copy()
    if "commence_time" in card.columns:
        card = card.sort_values("commence_time")

    locked_path = out_dir / "daily_robust_locked_picks.csv"
    card_path = out_dir / "daily_robust_superbru_card.csv"
    audit_path = out_dir / "daily_switch_audit.csv"
    summary_path = out_dir / "daily_robust_summary.json"

    locked.to_csv(locked_path, index=False)
    card.to_csv(card_path, index=False)
    audit_df.to_csv(audit_path, index=False)

    applied = audit_df[audit_df.get("apply_switch", pd.Series(dtype=bool)).astype(str).str.lower().eq("true")] if not audit_df.empty else pd.DataFrame()
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_card_count": int(len(locked)),
        "candidate_switch_count": int(len(candidates)),
        "applied_switch_count": int(len(applied)),
        "require_two_day_support": bool(args.require_two_day_support),
        "allow_first_snapshot": bool(args.allow_first_snapshot),
        "applied_switches": applied.to_dict(orient="records") if not applied.empty else [],
        "blocked_switches": audit_df[audit_df.get("apply_switch", pd.Series(dtype=bool)).astype(str).str.lower().ne("true")].to_dict(orient="records") if not audit_df.empty else [],
        "outputs": {
            "daily_robust_locked_picks": str(locked_path),
            "daily_robust_superbru_card": str(card_path),
            "daily_switch_audit": str(audit_path),
        },
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))
    print(f"Wrote {locked_path}")
    print(f"Wrote {card_path}")
    print(f"Wrote {audit_path}")
    print(f"Wrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
