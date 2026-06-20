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
    parser = argparse.ArgumentParser(description="Validate Superbru score picks against market odds.")
    parser.add_argument("--final-picks-csv", default="outputs/final_leader_decision_round_summary_profiles/final_picks.csv")
    parser.add_argument("--market-odds-csv", default="outputs/market_odds/worldcup_market_odds_flat.csv")
    parser.add_argument("--validation-report-csv", default="outputs/pick_validation_report/pick_validation_report.csv")
    parser.add_argument("--out-dir", default="outputs/market_odds_validation")
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


def parse_score(value: Any) -> tuple[int, int] | None:
    match = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", txt(value))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def pred_outcome(score: tuple[int, int]) -> str:
    home_goals, away_goals = score
    if home_goals > away_goals:
        return "home"
    if away_goals > home_goals:
        return "away"
    return "draw"


def implied_probability(price: Any) -> float:
    decimal_price = fnum(price)
    if math.isnan(decimal_price) or decimal_price <= 1.0:
        return float("nan")
    return 1.0 / decimal_price


def prepared_odds(odds: pd.DataFrame) -> pd.DataFrame:
    odds = odds.copy()
    odds["_home_norm"] = odds["home_team"].map(norm_team)
    odds["_away_norm"] = odds["away_team"].map(norm_team)
    return odds


def h2h_consensus(event_odds: pd.DataFrame, home_team: str, away_team: str) -> dict[str, Any]:
    h2h = event_odds[event_odds["market"].eq("h2h")].copy()
    if h2h.empty:
        return {"market_available": False}

    home_key = norm_team(home_team)
    away_key = norm_team(away_team)
    book_probs: list[dict[str, float]] = []

    for _, group in h2h.groupby("bookmaker"):
        probs: dict[str, float | None] = {"home": None, "draw": None, "away": None}
        for _, row in group.iterrows():
            name = norm_team(row.get("outcome_name"))
            probability = implied_probability(row.get("price"))
            if math.isnan(probability):
                continue
            if name == home_key:
                probs["home"] = probability
            elif name == away_key:
                probs["away"] = probability
            elif "draw" in name:
                probs["draw"] = probability

        if all(probs[key] is not None for key in ("home", "draw", "away")):
            total = float(probs["home"] or 0) + float(probs["draw"] or 0) + float(probs["away"] or 0)
            if total > 0:
                book_probs.append({key: float(probs[key] or 0) / total for key in ("home", "draw", "away")})

    if not book_probs:
        return {"market_available": False}

    avg = {key: sum(book[key] for book in book_probs) / len(book_probs) for key in ("home", "draw", "away")}
    favourite = max(avg, key=avg.get)
    return {
        "market_available": True,
        "bookmaker_count_h2h": len(book_probs),
        "market_p_home": avg["home"],
        "market_p_draw": avg["draw"],
        "market_p_away": avg["away"],
        "market_favourite": favourite,
        "market_favourite_prob": avg[favourite],
    }


def totals_consensus(event_odds: pd.DataFrame) -> dict[str, Any]:
    totals = event_odds[event_odds["market"].eq("totals")].copy()
    if totals.empty:
        return {"totals_available": False}

    totals["_point"] = totals["point"].map(fnum)
    totals = totals[~totals["_point"].isna()]
    if totals.empty:
        return {"totals_available": False}

    rounded_points = totals["_point"].round(2)
    line = 2.5 if (rounded_points == 2.5).any() else float(rounded_points.value_counts().index[0])
    line_rows = totals[totals["_point"].round(2).eq(round(line, 2))]
    book_probs: list[dict[str, float]] = []

    for _, group in line_rows.groupby("bookmaker"):
        probs: dict[str, float | None] = {"over": None, "under": None}
        for _, row in group.iterrows():
            name = norm_team(row.get("outcome_name"))
            probability = implied_probability(row.get("price"))
            if math.isnan(probability):
                continue
            if "over" in name:
                probs["over"] = probability
            elif "under" in name:
                probs["under"] = probability
        if probs["over"] is not None and probs["under"] is not None:
            total = float(probs["over"] or 0) + float(probs["under"] or 0)
            if total > 0:
                book_probs.append({"over": float(probs["over"] or 0) / total, "under": float(probs["under"] or 0) / total})

    if not book_probs:
        return {"totals_available": False}

    over = sum(book["over"] for book in book_probs) / len(book_probs)
    under = sum(book["under"] for book in book_probs) / len(book_probs)
    return {
        "totals_available": True,
        "bookmaker_count_totals": len(book_probs),
        "total_line": line,
        "market_p_over": over,
        "market_p_under": under,
        "market_total_lean": "over" if over >= under else "under",
        "market_total_lean_prob": max(over, under),
    }


def spreads_consensus(event_odds: pd.DataFrame, home_team: str) -> dict[str, Any]:
    spreads = event_odds[event_odds["market"].eq("spreads")].copy()
    if spreads.empty:
        return {"spreads_available": False}

    home_key = norm_team(home_team)
    home_spreads: list[float] = []
    for _, row in spreads.iterrows():
        if norm_team(row.get("outcome_name")) != home_key:
            continue
        point = fnum(row.get("point"))
        if not math.isnan(point):
            home_spreads.append(point)

    if not home_spreads:
        return {"spreads_available": False}

    return {
        "spreads_available": True,
        "bookmaker_count_spreads": len(home_spreads),
        "avg_home_spread": sum(home_spreads) / len(home_spreads),
    }


def market_alignment(row: dict[str, Any]) -> tuple[float, str, str]:
    score = 0.5
    reasons: list[str] = []
    predicted_outcome = txt(row.get("pred_outcome"))

    if row.get("market_available"):
        outcome_probability = float(row.get(f"market_p_{predicted_outcome}", 0.0))
        score = 0.25 + min(0.45, outcome_probability * 0.75)
        if predicted_outcome == row.get("market_favourite"):
            score += 0.20
            reasons.append("pick outcome agrees with market favourite")
        else:
            score -= 0.15
            reasons.append("pick outcome opposes market favourite")
        if outcome_probability >= 0.50:
            reasons.append("market gives selected outcome strong support")
        elif outcome_probability < 0.34:
            score -= 0.10
            reasons.append("market gives selected outcome weak support")
    else:
        reasons.append("h2h market unavailable")

    if row.get("totals_available"):
        total_goals = int(row.get("home_goals", 0)) + int(row.get("away_goals", 0))
        line = float(row.get("total_line", 2.5))
        pick_total = "over" if total_goals > line else "under"
        if pick_total == row.get("market_total_lean"):
            score += 0.10
            reasons.append("goal total agrees with market lean")
        elif float(row.get("market_total_lean_prob", 0.0)) >= 0.55:
            score -= 0.10
            reasons.append("goal total conflicts with market lean")
    else:
        reasons.append("totals market unavailable")

    if row.get("spreads_available"):
        margin = int(row.get("home_goals", 0)) - int(row.get("away_goals", 0))
        spread = float(row.get("avg_home_spread", 0.0))
        market_side = "home" if spread < -0.25 else "away" if spread > 0.25 else "near_even"
        pick_side = "home" if margin > 0 else "away" if margin < 0 else "draw"
        if market_side == "near_even":
            reasons.append("spread market near even")
        elif market_side == pick_side:
            score += 0.08
            reasons.append("score margin direction agrees with spread")
        else:
            score -= 0.08
            reasons.append("score margin direction conflicts with spread")
    else:
        reasons.append("spreads market unavailable")

    score = max(0.0, min(1.0, score))
    label = "market_aligned" if score >= 0.72 else "market_mixed" if score >= 0.50 else "market_conflict"
    return score, label, "; ".join(reasons)


def main() -> int:
    args = build_parser().parse_args()
    picks = pd.read_csv(args.final_picks_csv).fillna("")
    odds = prepared_odds(pd.read_csv(args.market_odds_csv).fillna(""))
    validation = pd.read_csv(args.validation_report_csv).fillna("") if Path(args.validation_report_csv).exists() else pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for _, pick in picks.iterrows():
        parsed_score = parse_score(pick.get("final_pick") or pick.get("raw_pick"))
        if parsed_score is None:
            continue
        home = txt(pick.get("home_team"))
        away = txt(pick.get("away_team"))
        event_odds = odds[(odds["_home_norm"].eq(norm_team(home))) & (odds["_away_norm"].eq(norm_team(away)))]
        row: dict[str, Any] = {
            "commence_time": txt(pick.get("commence_time")),
            "home_team": home,
            "away_team": away,
            "final_pick": txt(pick.get("final_pick") or pick.get("raw_pick")),
            "home_goals": parsed_score[0],
            "away_goals": parsed_score[1],
            "pred_outcome": pred_outcome(parsed_score),
            "market_rows": int(len(event_odds)),
        }
        row.update(h2h_consensus(event_odds, home, away))
        row.update(totals_consensus(event_odds))
        row.update(spreads_consensus(event_odds, home))
        score, label, reasons = market_alignment(row)
        row["market_alignment_score"] = round(score, 6)
        row["market_alignment_label"] = label
        row["market_alignment_reasons"] = reasons
        rows.append(row)

    report = pd.DataFrame(rows)
    if not validation.empty and not report.empty:
        keep = ["home_team", "away_team", "validation_score", "validation_label"]
        report = report.merge(validation[keep], on=["home_team", "away_team"], how="left")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "market_odds_validation_report.csv"
    summary_path = out_dir / "market_odds_validation_summary.json"
    report.to_csv(report_path, index=False)

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "pick_count": int(len(report)),
        "market_available_count": int(report.get("market_available", pd.Series(dtype=bool)).fillna(False).astype(bool).sum()) if not report.empty else 0,
        "totals_available_count": int(report.get("totals_available", pd.Series(dtype=bool)).fillna(False).astype(bool).sum()) if not report.empty else 0,
        "spreads_available_count": int(report.get("spreads_available", pd.Series(dtype=bool)).fillna(False).astype(bool).sum()) if not report.empty else 0,
        "market_alignment_label_counts": report["market_alignment_label"].value_counts().to_dict() if not report.empty else {},
        "lowest_market_alignment": report.sort_values("market_alignment_score").head(10)[
            ["home_team", "away_team", "final_pick", "market_alignment_score", "market_alignment_label", "market_alignment_reasons"]
        ].to_dict(orient="records") if not report.empty else [],
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"Wrote {report_path}")
    print(f"Wrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
