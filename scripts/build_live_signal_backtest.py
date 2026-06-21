from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

DEFAULT_POLICIES = {
    "raw_ev_model": ("outputs/final_leader_decision_daily_robust/final_picks.csv", ["raw_pick", "recommended_scoreline"]),
    "daily_robust_card": ("outputs/daily_robust_card/daily_robust_superbru_card.csv", ["robust_locked_pick", "daily_robust_pick"]),
    "final_leader_defence": ("outputs/final_leader_decision_daily_robust/final_picks.csv", ["final_pick", "leader_mc_pick"]),
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Build a trusted live backtest ledger from completed Superbru results and compare "
            "raw EV, naive, Oddspedia, daily robust and leader-defence policies separately."
        )
    )
    p.add_argument("--results-csv", default="outputs/superbru_pool/superbru_match_results_auto.csv")
    p.add_argument("--market-history-csv", default="outputs/market_odds_history/market_odds_history.csv")
    p.add_argument("--raw-policy-csv", default="outputs/final_leader_decision_daily_robust/final_picks.csv")
    p.add_argument("--daily-robust-csv", default="outputs/daily_robust_card/daily_robust_superbru_card.csv")
    p.add_argument("--leader-defence-csv", default="outputs/final_leader_decision_daily_robust/final_picks.csv")
    p.add_argument("--oddspedia-comparison-csv", default="outputs/oddspedia_pick_validation/oddspedia_pick_comparison.csv")
    p.add_argument("--oddspedia-ev-csv", default="outputs/oddspedia_pick_validation/oddspedia_ev_recommendations.csv")
    p.add_argument("--oddspedia-quality-summary-json", default="outputs/oddspedia_probability_extract/oddspedia_grid_quality_summary.json")
    p.add_argument("--rolling-out-csv", default="outputs/backtesting/live_signal_backtest_rolling.csv")
    p.add_argument("--summary-json", default="outputs/backtesting/live_signal_backtest_summary.json")
    p.add_argument("--ci-cutoff", type=float, default=1.5)
    p.add_argument("--min-oddspedia-ok-coverage", type=float, default=0.85)
    p.add_argument("--min-oddspedia-backtest-matches", type=int, default=30)
    return p


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def fnum(value: Any, default: float = float("nan")) -> float:
    try:
        text = txt(value).replace(",", ".")
        return float(text) if text else default
    except Exception:
        return default


def bval(value: Any) -> bool:
    return txt(value).lower() in {"1", "true", "yes", "y", "completed"}


def team_key(value: Any) -> str:
    text = txt(value).lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "", text)


def match_key(home: Any, away: Any) -> str:
    return f"{team_key(home)}-{team_key(away)}"


def parse_score(score: Any) -> tuple[int, int] | None:
    m = re.search(r"(\d+)\s*-\s*(\d+)", txt(score))
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def outcome(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "home"
    if home_goals < away_goals:
        return "away"
    return "draw"


def closeness_index(pred_home: int, pred_away: int, actual_home: int, actual_away: int) -> float:
    return float(abs((pred_home - pred_away) - (actual_home - actual_away)) + abs((pred_home + pred_away) - (actual_home + actual_away)) / 2.0)


def score_prediction(pick: Any, actual_home: int, actual_away: int, ci_cutoff: float) -> tuple[float, str]:
    parsed = parse_score(pick)
    if parsed is None:
        return 0.0, "invalid_pick"
    pred_home, pred_away = parsed
    if pred_home == actual_home and pred_away == actual_away:
        return 3.0, "exact"
    if outcome(pred_home, pred_away) == outcome(actual_home, actual_away):
        if closeness_index(pred_home, pred_away, actual_home, actual_away) <= ci_cutoff:
            return 1.5, "close"
        return 1.0, "result_only"
    return 0.0, "miss"


def load_csv(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(p).fillna("")
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def parse_dt(value: Any) -> datetime | None:
    text = txt(value)
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def add_match_key(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    out = frame.copy()
    if "match_key" not in out.columns:
        out["match_key"] = out.apply(lambda row: match_key(row.get("home_team"), row.get("away_team")), axis=1)
    return out


def trusted_completed_results(results_csv: str | Path) -> pd.DataFrame:
    frame = add_match_key(load_csv(results_csv))
    if frame.empty:
        return frame
    rows = []
    for _, row in frame.iterrows():
        if "is_completed" in row.index and not bval(row.get("is_completed")):
            continue
        h = fnum(row.get("home_goals"))
        a = fnum(row.get("away_goals"))
        if math.isnan(h) or math.isnan(a):
            parsed = parse_score(row.get("actual_score"))
            if parsed is None:
                continue
            h, a = parsed
        payload = row.to_dict()
        payload["actual_home_goals"] = int(h)
        payload["actual_away_goals"] = int(a)
        payload["actual_scoreline"] = f"{int(h)}-{int(a)}"
        payload["actual_outcome"] = outcome(int(h), int(a))
        rows.append(payload)
    return pd.DataFrame(rows)


def pre_match_snapshots(history_csv: str | Path) -> pd.DataFrame:
    frame = add_match_key(load_csv(history_csv))
    if frame.empty:
        return frame
    rows = []
    for _, row in frame.iterrows():
        if "market_available" in row.index and not bval(row.get("market_available")):
            continue
        created = parse_dt(row.get("snapshot_created_at_utc"))
        kickoff = parse_dt(row.get("commence_time"))
        if created is not None and kickoff is not None and created > kickoff:
            continue
        rows.append(row.to_dict())
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    out["_snapshot_sort"] = out.apply(lambda row: txt(row.get("snapshot_created_at_utc")) or txt(row.get("snapshot_id")), axis=1)
    out = out.sort_values("_snapshot_sort").drop_duplicates("match_key", keep="last")
    return out.drop(columns=["_snapshot_sort"], errors="ignore")


def pick_lookup(path: str | Path, columns: list[str]) -> dict[str, str]:
    frame = add_match_key(load_csv(path))
    if frame.empty:
        return {}
    column = next((col for col in columns if col in frame.columns), "")
    if not column:
        return {}
    return {txt(row.get("match_key")): txt(row.get(column)) for _, row in frame.iterrows() if txt(row.get(column))}


def row_lookup(path: str | Path) -> dict[str, dict[str, Any]]:
    frame = add_match_key(load_csv(path))
    if frame.empty:
        return {}
    return {txt(row.get("match_key")): row.to_dict() for _, row in frame.iterrows()}


def naive_market_pick(history_row: dict[str, Any] | None) -> str:
    if not history_row:
        return ""
    favourite = txt(history_row.get("market_favourite"))
    total_lean = txt(history_row.get("market_total_lean"))
    if favourite == "home":
        return "1-0" if total_lean == "under" else "2-1"
    if favourite == "away":
        return "0-1" if total_lean == "under" else "1-2"
    if favourite == "draw":
        return "1-1"
    return ""


def oddspedia_coverage_gate(summary_path: str | Path, backtest_rows: pd.DataFrame, min_coverage: float, min_matches: int) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "not_promoted",
        "reason": "Oddspedia remains advisory until coverage and trusted backtest evidence pass thresholds.",
        "min_ok_coverage": min_coverage,
        "min_backtest_matches": min_matches,
    }
    path = Path(summary_path)
    if path.exists():
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            summary = {}
    else:
        summary = {}
    match_count = int(summary.get("match_count") or 0)
    ok_count = int(summary.get("ok_match_count") or 0)
    ok_coverage = ok_count / match_count if match_count else 0.0
    oddspedia_rows = backtest_rows[backtest_rows.get("policy", pd.Series(dtype=str)).astype(str).str.startswith("oddspedia_")]
    evaluated_matches = int(oddspedia_rows[oddspedia_rows["policy_pick"].astype(str).ne("")]["match_key"].nunique()) if not oddspedia_rows.empty else 0
    payload.update(
        {
            "grid_match_count": match_count,
            "grid_ok_match_count": ok_count,
            "grid_ok_coverage": round(ok_coverage, 4),
            "trusted_oddspedia_backtest_matches": evaluated_matches,
            "coverage_passed": ok_coverage >= min_coverage,
            "backtest_volume_passed": evaluated_matches >= min_matches,
        }
    )
    if payload["coverage_passed"] and payload["backtest_volume_passed"]:
        payload["status"] = "eligible_for_review"
        payload["reason"] = "Coverage and backtest volume thresholds passed. Human review still required before promotion."
    return payload


def summarise_policy(rows: pd.DataFrame) -> list[dict[str, Any]]:
    if rows.empty:
        return []
    summaries = []
    for policy, group in rows.groupby("policy"):
        valid = group[group["policy_pick"].astype(str).ne("")]
        summaries.append(
            {
                "policy": policy,
                "matches": int(len(valid)),
                "total_points": float(valid["points"].sum()) if not valid.empty else 0.0,
                "avg_points": float(valid["points"].mean()) if not valid.empty else None,
                "exact_hits": int((valid["points_band"] == "exact").sum()) if not valid.empty else 0,
                "close_hits": int((valid["points_band"] == "close").sum()) if not valid.empty else 0,
                "result_only_hits": int((valid["points_band"] == "result_only").sum()) if not valid.empty else 0,
                "misses": int((valid["points_band"] == "miss").sum()) if not valid.empty else 0,
                "invalid_or_blank_picks": int(len(group) - len(valid)),
            }
        )
    return summaries


def main() -> int:
    args = build_parser().parse_args()
    generated_at = datetime.now(timezone.utc).isoformat()

    results = trusted_completed_results(args.results_csv)
    history = pre_match_snapshots(args.market_history_csv)
    history_by_key = {txt(row.get("match_key")): row.to_dict() for _, row in history.iterrows()} if not history.empty else {}

    policy_lookups = {
        "raw_ev_model": pick_lookup(args.raw_policy_csv, ["raw_pick", "recommended_scoreline"]),
        "daily_robust_card": pick_lookup(args.daily_robust_csv, ["robust_locked_pick", "daily_robust_pick"]),
        "final_leader_defence": pick_lookup(args.leader_defence_csv, ["final_pick", "leader_mc_pick"]),
    }
    odd_compare = row_lookup(args.oddspedia_comparison_csv)
    odd_ev = row_lookup(args.oddspedia_ev_csv)

    rows: list[dict[str, Any]] = []
    if not results.empty:
        for _, result in results.iterrows():
            key = txt(result.get("match_key"))
            history_row = history_by_key.get(key)
            odd_compare_row = odd_compare.get(key, {})
            odd_ev_row = odd_ev.get(key, {})

            raw_pick = policy_lookups["raw_ev_model"].get(key, "")
            daily_pick = policy_lookups["daily_robust_card"].get(key, "")
            final_pick = policy_lookups["final_leader_defence"].get(key, "")
            naive_pick = naive_market_pick(history_row)
            odd_modal_pick = txt(odd_compare_row.get("oddspedia_best_score") or odd_compare_row.get("modal_correct_score"))
            odd_ev_pick = txt(odd_ev_row.get("best_ev_scoreline"))
            review_flag = bval(odd_ev_row.get("review_flag")) or txt(odd_compare_row.get("action")) not in {"", "keep", "no_grid"}
            review_level = txt(odd_ev_row.get("review_level") or odd_compare_row.get("action"))

            policies = {
                "raw_ev_model": raw_pick,
                "naive_favourite_low_score": naive_pick,
                "oddspedia_modal": odd_modal_pick,
                "oddspedia_ev": odd_ev_pick,
                "daily_robust_card": daily_pick,
                "final_leader_defence": final_pick,
            }
            actual_home = int(result.get("actual_home_goals"))
            actual_away = int(result.get("actual_away_goals"))

            for policy, pick in policies.items():
                points, band = score_prediction(pick, actual_home, actual_away, args.ci_cutoff)
                rows.append(
                    {
                        "generated_at_utc": generated_at,
                        "match_key": key,
                        "commence_time": txt(result.get("commence_time")),
                        "home_team": txt(result.get("home_team")),
                        "away_team": txt(result.get("away_team")),
                        "trusted_results_csv": args.results_csv,
                        "result_score_source": txt(result.get("score_source")),
                        "actual_scoreline": txt(result.get("actual_scoreline")),
                        "actual_outcome": txt(result.get("actual_outcome")),
                        "policy": policy,
                        "policy_pick": pick,
                        "points": points,
                        "points_band": band,
                        "pre_match_snapshot_id": txt((history_row or {}).get("snapshot_id")),
                        "pre_match_snapshot_created_at_utc": txt((history_row or {}).get("snapshot_created_at_utc")),
                        "market_favourite": txt((history_row or {}).get("market_favourite")),
                        "market_depth_class": txt((history_row or {}).get("market_depth_class")),
                        "raw_ev_pick": raw_pick,
                        "naive_favourite_low_score_pick": naive_pick,
                        "oddspedia_modal_pick": odd_modal_pick,
                        "oddspedia_ev_pick": odd_ev_pick,
                        "oddspedia_review_flag": review_flag,
                        "oddspedia_review_level": review_level,
                        "oddspedia_review_followed_by_policy": bool(review_flag and pick and pick == odd_ev_pick and pick != raw_pick),
                        "daily_robust_pick": daily_pick,
                        "final_leader_defence_pick": final_pick,
                    }
                )

    out = pd.DataFrame(rows)
    rolling_path = Path(args.rolling_out_csv)
    rolling_path.parent.mkdir(parents=True, exist_ok=True)
    if rolling_path.exists() and not out.empty:
        previous = load_csv(rolling_path)
        out = pd.concat([previous, out], ignore_index=True)
        out = out.drop_duplicates(["match_key", "policy", "actual_scoreline"], keep="last")
    out.to_csv(rolling_path, index=False)

    gate = oddspedia_coverage_gate(args.oddspedia_quality_summary_json, out, args.min_oddspedia_ok_coverage, args.min_oddspedia_backtest_matches)
    summary = {
        "generated_at_utc": generated_at,
        "status": "valid" if not out.empty else "no_trusted_completed_matches",
        "warning": "Only rows joined to trusted completed results are scored. Market-history home_goals/away_goals are never treated as actual results.",
        "trusted_results_csv": args.results_csv,
        "market_history_csv": args.market_history_csv,
        "trusted_completed_match_count": int(results["match_key"].nunique()) if not results.empty else 0,
        "scored_policy_rows": int(len(out)),
        "policy_summaries": summarise_policy(out),
        "oddspedia_promotion_gate": gate,
        "outputs": {
            "rolling_csv": str(rolling_path),
            "summary_json": args.summary_json,
        },
    }
    summary_path = Path(args.summary_json)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    return 0 if not out.empty else 1


if __name__ == "__main__":
    raise SystemExit(main())
