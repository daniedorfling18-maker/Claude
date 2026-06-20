from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

CONFIDENCE_SCORE = {"strong": 1.0, "medium": 0.68, "fragile": 0.38, "weak": 0.25, "": 0.5}
RISK_SCORE = {"low": 1.0, "medium": 0.68, "high": 0.36, "": 0.5}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a combined validation layer for Superbru final picks."
    )
    parser.add_argument("--final-picks-csv", default="outputs/final_leader_decision_round_summary_profiles/final_picks.csv")
    parser.add_argument("--final-report-json", default="outputs/final_leader_decision_round_summary_profiles/final_decision_report.json")
    parser.add_argument("--stress-support-csv", default="outputs/final_leader_decision_round_summary_profiles/stress/stress_switch_support.csv")
    parser.add_argument("--stress-summary-csv", default="outputs/final_leader_decision_round_summary_profiles/stress/stress_scenario_summary.csv")
    parser.add_argument("--predictions-csv", default="outputs/latest/predictions.csv")
    parser.add_argument("--calibration-summary-json", default="outputs/calibration_diagnostics/calibration_summary.json")
    parser.add_argument("--calibration-detail-csv", default="outputs/calibration_diagnostics/calibration_detail.csv")
    parser.add_argument("--round-behaviour-csv", default="outputs/round_summary_behaviour/round_behaviour_trends.csv")
    parser.add_argument("--out-dir", default="outputs/pick_validation_report")
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def fnum(value: Any, default: float = 0.0) -> float:
    try:
        text = txt(value).replace(",", ".")
        if text == "":
            return default
        return float(text)
    except Exception:
        return default


def load_json(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def load_csv(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    return pd.read_csv(p).fillna("")


def norm_team(value: Any) -> str:
    return "".join(ch.lower() for ch in txt(value) if ch.isalnum())


def match_key(row: pd.Series) -> tuple[str, str, str]:
    return (txt(row.get("commence_time")), norm_team(row.get("home_team")), norm_team(row.get("away_team")))


def report_summary(final_report: dict[str, Any]) -> dict[str, Any]:
    base = final_report.get("summaries", {}).get("base_mc", {}) or {}
    confirm = final_report.get("summaries", {}).get("confirmation_500k", {}) or {}
    stress = final_report.get("stress", {}) or {}

    base_p = fnum(base.get("final_p_finish_first"), float("nan"))
    confirm_p = fnum(confirm.get("final_p_finish_first"), float("nan"))
    confirm_tie = fnum(confirm.get("final_p_finish_first_or_tied"), float("nan"))
    simulations = fnum(confirm.get("simulations"), 0.0)
    mc_se = math.sqrt(confirm_p * (1.0 - confirm_p) / simulations) if simulations > 0 and 0 <= confirm_p <= 1 else float("nan")

    return {
        "base_p_finish_first": base_p,
        "confirmation_p_finish_first": confirm_p,
        "confirmation_p_finish_first_or_tied": confirm_tie,
        "base_confirmation_delta": confirm_p - base_p if not math.isnan(base_p) and not math.isnan(confirm_p) else float("nan"),
        "confirmation_simulations": int(simulations),
        "confirmation_95pct_half_width": 1.96 * mc_se if not math.isnan(mc_se) else None,
        "policy_switch_count": len(final_report.get("accepted_policy_switches", []) or []),
        "robust_switch_count": int(fnum(stress.get("robust_switch_count"), 0.0)),
        "stress_scenario_count": int(fnum(stress.get("scenario_count"), 0.0)),
        "quality_gates_passed": all(bool(gate.get("passed")) for gate in final_report.get("quality_gates", []) or []),
    }


def calibration_summary(calibration: dict[str, Any]) -> dict[str, Any]:
    if not calibration:
        return {
            "calibration_available": False,
            "matched_matches": 0,
            "exact_hit_rate": None,
            "outcome_hit_rate": None,
            "exact_brier": None,
            "outcome_brier": None,
            "calibration_haircut": 0.12,
        }

    matched_matches = int(fnum(calibration.get("matched_matches"), 0.0))
    outcome_hit = fnum(calibration.get("outcome_hit_rate"), 0.0)
    exact_hit = fnum(calibration.get("exact_hit_rate"), 0.0)
    outcome_brier = calibration.get("outcome_brier")
    exact_brier = calibration.get("exact_brier")

    # Conservative calibration penalty:
    # poor calibration should not improve confidence versus missing calibration.
    haircut = 0.0

    if matched_matches < 25:
        haircut += 0.04
    if outcome_hit < 0.45:
        haircut += 0.08
    if exact_hit < 0.08:
        haircut += 0.04
    if outcome_brier is not None and fnum(outcome_brier) > 0.25:
        haircut += 0.08
    if exact_brier is not None and fnum(exact_brier) > 0.16:
        haircut += 0.03

    return {
        "calibration_available": True,
        "matched_matches": matched_matches,
        "exact_hit_rate": exact_hit,
        "outcome_hit_rate": outcome_hit,
        "exact_brier": exact_brier,
        "outcome_brier": outcome_brier,
        "calibration_haircut": min(0.20, haircut),
    }


def join_predictions(picks: pd.DataFrame, predictions: pd.DataFrame) -> pd.DataFrame:
    if predictions.empty:
        return picks.copy()
    picks = picks.copy()
    predictions = predictions.copy()
    picks["_key"] = picks.apply(match_key, axis=1)
    predictions["_key"] = predictions.apply(match_key, axis=1)

    keep = ["_key"]
    for col in [
        "p_exact",
        "P(exact)",
        "exact_probability",
        "p_outcome",
        "P(outcome)",
        "outcome_probability",
        "expected_points",
        "private_chase_ev_loss",
        "p_home",
        "p_draw",
        "p_away",
    ]:
        if col in predictions.columns and col not in keep:
            keep.append(col)
    merged = picks.merge(predictions[keep], on="_key", how="left")
    return merged.drop(columns=["_key"], errors="ignore")


def stress_lookup(stress_support: pd.DataFrame) -> dict[tuple[str, str, str], dict[str, float]]:
    lookup: dict[tuple[str, str, str], dict[str, float]] = {}
    if stress_support.empty:
        return lookup
    for _, row in stress_support.iterrows():
        key = (norm_team(row.get("home_team")), norm_team(row.get("away_team")), txt(row.get("raw_pick")))
        current = lookup.get(key, {"max_pass_rate": 0.0, "max_positive_delta_rate": 0.0, "max_mean_delta": 0.0})
        current["max_pass_rate"] = max(current["max_pass_rate"], fnum(row.get("pass_rate")))
        current["max_positive_delta_rate"] = max(current["max_positive_delta_rate"], fnum(row.get("positive_delta_rate")))
        current["max_mean_delta"] = max(current["max_mean_delta"], fnum(row.get("mean_delta_p_finish_first")))
        lookup[key] = current
    return lookup


def label_from_score(score: float) -> str:
    if score >= 0.78:
        return "validated"
    if score >= 0.62:
        return "acceptable"
    if score >= 0.45:
        return "fragile"
    return "review"


def build_pick_report(
    picks: pd.DataFrame,
    predictions: pd.DataFrame,
    stress_support: pd.DataFrame,
    final_report: dict[str, Any],
    calibration: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    picks = join_predictions(picks, predictions)
    stress = stress_lookup(stress_support)
    rep = report_summary(final_report)
    cal = calibration_summary(calibration)
    global_stability = 1.0
    if rep["policy_switch_count"] > 0:
        global_stability -= 0.20
    if rep["robust_switch_count"] > 0:
        global_stability -= 0.20
    if rep.get("confirmation_95pct_half_width") and rep["confirmation_95pct_half_width"] > 0.005:
        global_stability -= 0.05
    if not rep.get("quality_gates_passed"):
        global_stability -= 0.20
    global_stability = max(0.0, min(1.0, global_stability))

    rows: list[dict[str, Any]] = []
    for _, row in picks.iterrows():
        confidence_tier = txt(row.get("confidence_tier")).lower()
        risk_tier = txt(row.get("risk_tier")).lower()
        base_score = 0.40 * CONFIDENCE_SCORE.get(confidence_tier, 0.5) + 0.30 * RISK_SCORE.get(risk_tier, 0.5) + 0.30 * global_stability

        key = (norm_team(row.get("home_team")), norm_team(row.get("away_team")), txt(row.get("final_pick")) or txt(row.get("raw_pick")))
        stress_data = stress.get(key, {"max_pass_rate": 0.0, "max_positive_delta_rate": 0.0, "max_mean_delta": 0.0})
        switch_pressure = stress_data["max_pass_rate"] * 0.10 + stress_data["max_positive_delta_rate"] * 0.05
        if bool(row.get("final_switched")):
            switch_pressure = 0.0

        prob_support = 0.0
        p_exact = fnum(row.get("p_exact", row.get("P(exact)", row.get("exact_probability", ""))), float("nan"))
        p_outcome = fnum(row.get("p_outcome", row.get("P(outcome)", row.get("outcome_probability", ""))), float("nan"))
        if not math.isnan(p_exact):
            prob_support += min(0.05, p_exact / 4.0)
        if not math.isnan(p_outcome):
            prob_support += min(0.05, p_outcome / 8.0)

        validation_score = max(0.0, min(1.0, base_score + prob_support - switch_pressure - cal["calibration_haircut"]))
        reasons: list[str] = []
        if confidence_tier == "strong":
            reasons.append("strong model confidence")
        elif confidence_tier == "fragile":
            reasons.append("fragile model confidence")
        if risk_tier == "high":
            reasons.append("high scoreline risk")
        elif risk_tier == "low":
            reasons.append("low scoreline risk")
        if stress_data["max_pass_rate"] > 0:
            reasons.append(f"alternative switch passed {stress_data['max_pass_rate']:.0%} of stress scenarios")
        if cal["calibration_available"]:
            reasons.append("calibration diagnostics available")
        else:
            reasons.append("calibration diagnostics not yet available")
        if global_stability >= 0.9:
            reasons.append("no robust policy switches")

        rows.append(
            {
                "commence_time": txt(row.get("commence_time")),
                "home_team": txt(row.get("home_team")),
                "away_team": txt(row.get("away_team")),
                "final_pick": txt(row.get("final_pick")) or txt(row.get("raw_pick")),
                "risk_tier": txt(row.get("risk_tier")),
                "confidence_tier": txt(row.get("confidence_tier")),
                "validation_score": round(validation_score, 6),
                "validation_label": label_from_score(validation_score),
                "max_switch_pass_rate": round(stress_data["max_pass_rate"], 6),
                "max_switch_positive_delta_rate": round(stress_data["max_positive_delta_rate"], 6),
                "global_stability_score": round(global_stability, 6),
                "calibration_haircut": round(cal["calibration_haircut"], 6),
                "validation_reasons": "; ".join(reasons),
            }
        )

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        **rep,
        **cal,
        "pick_count": int(len(rows)),
    }
    report = pd.DataFrame(rows).sort_values(["validation_score", "commence_time"], ascending=[True, True])
    if not report.empty:
        summary["validation_label_counts"] = report["validation_label"].value_counts().to_dict()
        summary["lowest_validation_picks"] = report.head(8)[["home_team", "away_team", "final_pick", "validation_score", "validation_label"]].to_dict(orient="records")
    return report, summary


def markdown_report(report: pd.DataFrame, summary: dict[str, Any]) -> str:
    lines = ["# Pick Validation Report", ""]
    lines.append(f"Generated: {summary.get('generated_at_utc')}")
    lines.append("")
    lines.append("## Decision stability")
    lines.append(f"- Confirmation p_finish_first: {summary.get('confirmation_p_finish_first')}")
    lines.append(f"- Confirmation p_finish_first_or_tied: {summary.get('confirmation_p_finish_first_or_tied')}")
    lines.append(f"- Confirmation 95% half-width: {summary.get('confirmation_95pct_half_width')}")
    lines.append(f"- Policy switch count: {summary.get('policy_switch_count')}")
    lines.append(f"- Robust switch count: {summary.get('robust_switch_count')}")
    lines.append(f"- Quality gates passed: {summary.get('quality_gates_passed')}")
    lines.append("")
    lines.append("## Calibration")
    lines.append(f"- Calibration available: {summary.get('calibration_available')}")
    lines.append(f"- Outcome hit rate: {summary.get('outcome_hit_rate')}")
    lines.append(f"- Exact hit rate: {summary.get('exact_hit_rate')}")
    lines.append(f"- Outcome Brier: {summary.get('outcome_brier')}")
    lines.append(f"- Exact Brier: {summary.get('exact_brier')}")
    lines.append("")
    lines.append("## Lowest validation picks")
    if report.empty:
        lines.append("No picks available.")
    else:
        lines.append("| Match | Pick | Score | Label | Reasons |")
        lines.append("|---|---:|---:|---|---|")
        for _, row in report.head(12).iterrows():
            lines.append(
                f"| {row['home_team']} vs {row['away_team']} | {row['final_pick']} | {row['validation_score']} | {row['validation_label']} | {row['validation_reasons']} |"
            )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = build_parser().parse_args()
    picks = load_csv(args.final_picks_csv)
    if picks.empty:
        raise ValueError(f"No final picks found at {args.final_picks_csv}")
    final_report = load_json(args.final_report_json)
    stress_support = load_csv(args.stress_support_csv)
    predictions = load_csv(args.predictions_csv)
    calibration = load_json(args.calibration_summary_json)

    report, summary = build_pick_report(picks, predictions, stress_support, final_report, calibration)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "pick_validation_report.csv"
    summary_path = out_dir / "pick_validation_summary.json"
    markdown_path = out_dir / "pick_validation_report.md"

    report.to_csv(report_path, index=False)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    markdown_path.write_text(markdown_report(report, summary), encoding="utf-8")

    print(json.dumps(summary, indent=2))
    print(f"Wrote {report_path}")
    print(f"Wrote {summary_path}")
    print(f"Wrote {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

