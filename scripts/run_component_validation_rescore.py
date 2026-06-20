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
        description="Add component-level market validation and rescore fragile/review picks."
    )
    parser.add_argument("--market-validation-csv", default="outputs/market_odds_validation/market_odds_validation_report.csv")
    parser.add_argument("--pick-validation-csv", default="outputs/pick_validation_report/pick_validation_report.csv")
    parser.add_argument("--candidate-alternatives-csv", default="outputs/pick_validation_report/review_candidate_alternatives.csv")
    parser.add_argument("--out-dir", default="outputs/component_validation")
    parser.add_argument("--low-ev-loss", type=float, default=0.01)
    parser.add_argument("--max-negative-leader-delta", type=float, default=-0.001)
    parser.add_argument("--min-component-score", type=float, default=0.65)
    parser.add_argument("--min-component-improvement", type=float, default=0.05)
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
    match = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", txt(value))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def score_outcome(score: tuple[int, int]) -> str:
    home_goals, away_goals = score
    if home_goals > away_goals:
        return "home"
    if away_goals > home_goals:
        return "away"
    return "draw"


def component_label(score: float) -> str:
    if score >= 0.75:
        return "component_aligned"
    if score >= 0.55:
        return "component_mixed"
    return "component_conflict"


def outcome_component(row: pd.Series, score: tuple[int, int]) -> tuple[float, str]:
    predicted = score_outcome(score)
    market_available = bval(row.get("market_available"))
    if not market_available:
        return 0.50, "outcome market unavailable"

    selected_prob = fnum(row.get(f"market_p_{predicted}"), 0.0)
    favourite = txt(row.get("market_favourite"))

    if selected_prob >= 0.55:
        base = 1.00
    elif selected_prob >= 0.45:
        base = 0.85
    elif selected_prob >= 0.38:
        base = 0.72
    elif selected_prob >= 0.33:
        base = 0.58
    else:
        base = 0.35

    if predicted == favourite:
        base += 0.10
        reason = "outcome agrees with market favourite"
    else:
        base -= 0.10
        reason = "outcome does not match market favourite"

    return max(0.0, min(1.0, base)), reason


def total_component(row: pd.Series, score: tuple[int, int]) -> tuple[float, str]:
    if not bval(row.get("totals_available")):
        return 0.50, "totals market unavailable"

    line = fnum(row.get("total_line"), 2.5)
    lean = txt(row.get("market_total_lean"))
    lean_prob = fnum(row.get("market_total_lean_prob"), 0.0)
    goals = score[0] + score[1]
    pick_total = "over" if goals > line else "under"

    if pick_total == lean:
        return (0.88 if lean_prob >= 0.55 else 0.74), "goal total agrees with market lean"
    return (0.25 if lean_prob >= 0.57 else 0.45), "goal total conflicts with market lean"


def spread_component(row: pd.Series, score: tuple[int, int]) -> tuple[float, str]:
    if not bval(row.get("spreads_available")):
        return 0.50, "spread market unavailable"

    home_spread = fnum(row.get("avg_home_spread"), 0.0)
    # For conventional spreads, negative home spread means the home side is favoured.
    market_margin = -home_spread
    pick_margin = score[0] - score[1]

    if abs(market_margin) <= 0.25:
        if abs(pick_margin) <= 1:
            return 0.68, "spread market near even and score margin modest"
        return 0.48, "spread market near even but score margin is assertive"

    same_direction = (market_margin > 0 and pick_margin > 0) or (market_margin < 0 and pick_margin < 0)
    if not same_direction:
        return 0.28, "score margin direction conflicts with spread"

    gap = abs(abs(pick_margin) - abs(market_margin))
    if gap <= 1.0:
        return 0.88, "score margin direction and size agree with spread"
    if gap <= 1.75:
        return 0.72, "score margin direction agrees with spread"
    return 0.58, "score margin direction agrees but margin is stretched"


def component_score(row: pd.Series, pick: Any) -> dict[str, Any]:
    score = parse_score(pick)
    if score is None:
        return {
            "component_score": 0.0,
            "component_label": "component_invalid",
            "component_reasons": "invalid scoreline",
        }

    outcome_score, outcome_reason = outcome_component(row, score)
    total_score, total_reason = total_component(row, score)
    spread_score, spread_reason = spread_component(row, score)
    combined = 0.45 * outcome_score + 0.30 * total_score + 0.25 * spread_score

    return {
        "component_score": round(combined, 6),
        "component_label": component_label(combined),
        "outcome_component_score": round(outcome_score, 6),
        "total_component_score": round(total_score, 6),
        "spread_component_score": round(spread_score, 6),
        "component_reasons": "; ".join([outcome_reason, total_reason, spread_reason]),
    }


def load_csv(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    return pd.read_csv(p).fillna("")


def build_component_report(market: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for _, row in market.iterrows():
        components = component_score(row, row.get("final_pick"))
        out = row.to_dict()
        out.update(components)
        rows.append(out)
    return pd.DataFrame(rows)


def find_market_row(component_report: pd.DataFrame, home: Any, away: Any) -> pd.Series | None:
    key = match_key(home, away)
    for _, row in component_report.iterrows():
        if match_key(row.get("home_team"), row.get("away_team")) == key:
            return row
    return None


def build_review_rescore(
    component_report: pd.DataFrame,
    validation: pd.DataFrame,
    candidates: pd.DataFrame,
    low_ev_loss: float,
    max_negative_leader_delta: float,
    min_component_score: float,
    min_component_improvement: float,
) -> pd.DataFrame:
    if validation.empty or candidates.empty or component_report.empty:
        return pd.DataFrame()

    validation_keys = set()
    for _, row in validation.iterrows():
        if txt(row.get("validation_label")) in {"review", "fragile"}:
            validation_keys.add(match_key(row.get("home_team"), row.get("away_team")))

    rows: list[dict[str, Any]] = []
    filtered = candidates[candidates.apply(lambda r: match_key(r.get("home_team"), r.get("away_team")) in validation_keys, axis=1)]
    filtered = filtered[filtered["is_current"].astype(str).str.lower().ne("true")]

    for _, cand in filtered.iterrows():
        market_row = find_market_row(component_report, cand.get("home_team"), cand.get("away_team"))
        if market_row is None:
            continue

        current_component = fnum(market_row.get("component_score"), 0.0)
        candidate_components = component_score(market_row, cand.get("candidate_pick"))
        candidate_component = fnum(candidate_components.get("component_score"), 0.0)

        ev_loss = fnum(cand.get("ev_loss"), 999.0)
        delta_p = fnum(cand.get("delta_p_finish_first"), -999.0)
        stress_pass_rate = fnum(cand.get("stress_pass_rate"), 0.0)

        low_cost = ev_loss <= low_ev_loss
        leader_safe = delta_p >= max_negative_leader_delta
        market_supported = candidate_component >= min_component_score
        improves_market = candidate_component >= current_component + min_component_improvement
        switch_ok = bool(low_cost and leader_safe and market_supported and improves_market)

        reasons = []
        reasons.append("low EV cost" if low_cost else "EV cost too high")
        reasons.append("leader delta acceptable" if leader_safe else "leader delta too negative")
        reasons.append("candidate component-supported" if market_supported else "candidate not component-supported")
        reasons.append("candidate improves component validation" if improves_market else "candidate does not improve component validation enough")
        if stress_pass_rate > 0:
            reasons.append("stress support present")
        else:
            reasons.append("no stress-switch support")

        out = {
            "home_team": txt(cand.get("home_team")),
            "away_team": txt(cand.get("away_team")),
            "current_pick": txt(cand.get("current_pick")),
            "candidate_pick": txt(cand.get("candidate_pick")),
            "recommendation": "switch_candidate" if switch_ok else "keep_current",
            "current_component_score": round(current_component, 6),
            "candidate_component_score": round(candidate_component, 6),
            "component_improvement": round(candidate_component - current_component, 6),
            "candidate_component_label": candidate_components.get("component_label"),
            "candidate_component_reasons": candidate_components.get("component_reasons"),
            "ev_loss": round(ev_loss, 6),
            "delta_p_finish_first": round(delta_p, 6),
            "stress_pass_rate": round(stress_pass_rate, 6),
            "passes_hard_rule": txt(cand.get("passes_hard_rule")),
            "decision_reasons": "; ".join(reasons),
        }
        rows.append(out)

    if not rows:
        return pd.DataFrame()

    result = pd.DataFrame(rows)
    return result.sort_values(
        ["recommendation", "component_improvement", "ev_loss"],
        ascending=[True, False, True],
    )


def main() -> int:
    args = build_parser().parse_args()
    market = load_csv(args.market_validation_csv)
    validation = load_csv(args.pick_validation_csv)
    candidates = load_csv(args.candidate_alternatives_csv)

    if market.empty:
        raise ValueError(f"No market validation rows found at {args.market_validation_csv}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    component_report = build_component_report(market)
    review_rescore = build_review_rescore(
        component_report,
        validation,
        candidates,
        args.low_ev_loss,
        args.max_negative_leader_delta,
        args.min_component_score,
        args.min_component_improvement,
    )

    component_path = out_dir / "component_validation_report.csv"
    rescore_path = out_dir / "review_rescore_report.csv"
    summary_path = out_dir / "review_rescore_summary.json"

    component_report.to_csv(component_path, index=False)
    review_rescore.to_csv(rescore_path, index=False)

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "component_pick_count": int(len(component_report)),
        "component_label_counts": component_report["component_label"].value_counts().to_dict(),
        "review_candidate_count": int(len(review_rescore)),
        "switch_recommendation_count": int((review_rescore.get("recommendation", pd.Series(dtype=str)) == "switch_candidate").sum()) if not review_rescore.empty else 0,
        "switch_recommendations": review_rescore[review_rescore["recommendation"].eq("switch_candidate")].to_dict(orient="records") if not review_rescore.empty else [],
        "lowest_component_picks": component_report.sort_values("component_score").head(12)[
            ["home_team", "away_team", "final_pick", "validation_label", "market_alignment_label", "component_score", "component_label", "component_reasons"]
        ].to_dict(orient="records"),
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))
    print(f"Wrote {component_path}")
    print(f"Wrote {rescore_path}")
    print(f"Wrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
