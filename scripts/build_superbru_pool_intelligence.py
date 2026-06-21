from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PICK_COLUMNS = ["daily_robust_pick", "robust_locked_pick", "locked_pick", "private_chase_scoreline", "final_pick", "recommended_scoreline", "pick", "scoreline"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build Superbru pool intelligence from visible pool picks, leaderboard, and Oddspedia overlays.")
    parser.add_argument("--pool-picks-csv", default="outputs/superbru_pool/superbru_pool_picks_auto.csv")
    parser.add_argument("--leaderboard-csv", default="inputs/pool_leaderboard.csv")
    parser.add_argument("--locked-picks-csv", default="outputs/final_locked_picks/superbru_final_card.csv")
    parser.add_argument("--match-results-csv", default="outputs/superbru_pool/superbru_match_results_auto.csv")
    parser.add_argument("--oddspedia-comparison-csv", default="outputs/oddspedia_pick_validation/oddspedia_pick_comparison.csv")
    parser.add_argument("--oddspedia-ev-recommendations-csv", default="outputs/oddspedia_pick_validation/oddspedia_ev_recommendations.csv")
    parser.add_argument("--market-odds-csv", default="outputs/market_odds_validation/market_odds_validation_report.csv")
    parser.add_argument("--out-dir", default="outputs/superbru_pool")
    parser.add_argument("--us-player", default="Danie")
    parser.add_argument("--immediate-chaser-gap", type=float, default=3.0)
    parser.add_argument("--wider-chaser-gap", type=float, default=9.0)
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


def load_csv(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(p).fillna("")
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def parse_score(score: Any) -> tuple[int | None, int | None]:
    m = re.search(r"(\d+)\s*-\s*(\d+)", txt(score))
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def outcome_from_goals(h: int | None, a: int | None) -> str:
    if h is None or a is None:
        return "unknown"
    if h > a:
        return "home"
    if h < a:
        return "away"
    return "draw"


def outcome_from_score(score: Any) -> str:
    h, a = parse_score(score)
    return outcome_from_goals(h, a)


def is_close(pick: str, actual: str) -> bool:
    ph, pa = parse_score(pick)
    ah, aa = parse_score(actual)
    if None in {ph, pa, ah, aa}:
        return False
    if ph == ah and pa == aa:
        return False
    return outcome_from_goals(ph, pa) == outcome_from_goals(ah, aa) and abs(ph - ah) <= 1 and abs(pa - aa) <= 1


def score_points(pick: str, actual: str) -> float:
    if not pick or not actual:
        return 0.0
    if pick == actual:
        return 3.0
    if outcome_from_score(pick) != outcome_from_score(actual):
        return 0.0
    return 1.5 if is_close(pick, actual) else 1.0


def find_pick_column(frame: pd.DataFrame) -> str | None:
    for col in PICK_COLUMNS:
        if col in frame.columns:
            return col
    return None


def locked_lookup(locked: pd.DataFrame) -> dict[str, dict[str, Any]]:
    col = find_pick_column(locked)
    if locked.empty or not col:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for _, r in locked.iterrows():
        mid = txt(r.get("match_id"))
        if mid:
            pick = txt(r.get(col))
            out[mid] = {"locked_score": pick, "locked_outcome": outcome_from_score(pick), "home_team": txt(r.get("home_team")), "away_team": txt(r.get("away_team")), "commence_time": txt(r.get("commence_time"))}
    return out


def infer_points(row: pd.Series) -> float | None:
    p = to_float(row.get("points_earned"))
    if p is not None:
        return p
    status = txt(row.get("result_status")).lower()
    return {"exact": 3.0, "close": 1.5, "result": 1.0, "wrong": 0.0}.get(status)


def build_profiles(picks: pd.DataFrame, market: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    if picks.empty or "player" not in picks.columns:
        return pd.DataFrame(), "No pool picks available for player profiles."
    fav: dict[str, str] = {}
    for _, r in market.iterrows() if not market.empty else []:
        vals = {"home": to_float(r.get("home_win_probability_pct")) or to_float(r.get("home_prob_pct")), "draw": to_float(r.get("draw_probability_pct")) or to_float(r.get("draw_prob_pct")), "away": to_float(r.get("away_win_probability_pct")) or to_float(r.get("away_prob_pct"))}
        vals = {k: v for k, v in vals.items() if v is not None}
        if vals and txt(r.get("match_id")):
            fav[txt(r.get("match_id"))] = max(vals, key=vals.get)
    tmp = picks.copy()
    tmp["_points"] = tmp.apply(infer_points, axis=1)
    source = tmp[tmp["_points"].notna()].copy()
    if source.empty:
        source = tmp
    rows: list[dict[str, Any]] = []
    for player, g in source.groupby("player", dropna=False):
        statuses = g.get("result_status", pd.Series([""] * len(g))).map(lambda x: txt(x).lower())
        h = pd.to_numeric(g.get("picked_home_goals", pd.Series([""] * len(g))), errors="coerce")
        a = pd.to_numeric(g.get("picked_away_goals", pd.Series([""] * len(g))), errors="coerce")
        outcomes = g.get("picked_outcome", pd.Series([""] * len(g))).map(txt)
        fav_known = fav_hits = 0
        for _, r in g.iterrows():
            fav_outcome = fav.get(txt(r.get("match_id")))
            if fav_outcome:
                fav_known += 1
                fav_hits += int(txt(r.get("picked_outcome")) == fav_outcome)
        rows.append({
            "player": txt(player), "played": int(len(g)), "total_points": float(g["_points"].fillna(0).sum()), "points_per_match": float(g["_points"].mean()) if g["_points"].notna().any() else None,
            "exact_count": int((statuses == "exact").sum()), "close_count": int((statuses == "close").sum()), "result_count": int((statuses == "result").sum()), "wrong_count": int((statuses == "wrong").sum()),
            "exact_rate": float((statuses == "exact").mean()), "close_rate": float((statuses == "close").mean()), "result_rate": float((statuses == "result").mean()), "wrong_rate": float((statuses == "wrong").mean()),
            "average_goals_picked": float((h.fillna(0) + a.fillna(0)).mean()) if len(g) else None, "favourite_pick_rate": fav_hits / fav_known if fav_known else None,
            "draw_pick_rate": float((outcomes == "draw").mean()), "clean_sheet_pick_rate": float(((h == 0) | (a == 0)).mean()) if len(g) else None,
        })
    return pd.DataFrame(rows).sort_values(["points_per_match", "total_points"], ascending=False, na_position="last"), ""


def one_row_by_match(frame: pd.DataFrame, col: str) -> dict[str, str]:
    if frame.empty or "match_id" not in frame.columns or col not in frame.columns:
        return {}
    return {txt(r.get("match_id")): txt(r.get(col)) for _, r in frame.iterrows()}


def build_distribution(picks: pd.DataFrame, locked: dict[str, dict[str, Any]], comparison: pd.DataFrame, ev: pd.DataFrame) -> pd.DataFrame:
    if picks.empty or "match_id" not in picks.columns:
        return pd.DataFrame()
    modal = one_row_by_match(comparison, "modal_correct_score") or one_row_by_match(comparison, "oddspedia_best_score")
    best_ev = one_row_by_match(ev, "best_ev_scoreline")
    rows: list[dict[str, Any]] = []
    for mid, g in picks.groupby("match_id", dropna=False):
        mid = txt(mid)
        g = g.copy()
        total = len(g)
        score_counts = g["picked_scoreline"].map(txt).value_counts().to_dict() if "picked_scoreline" in g.columns else {}
        outcome_counts = g["picked_outcome"].map(txt).value_counts().to_dict() if "picked_outcome" in g.columns else {}
        most_score = next(iter(score_counts.keys()), "")
        locked_score = locked.get(mid, {}).get("locked_score", "")
        locked_outcome = locked.get(mid, {}).get("locked_outcome", "")
        pct_locked_score = 100.0 * score_counts.get(locked_score, 0) / total if total and locked_score else 0.0
        pct_locked_outcome = 100.0 * outcome_counts.get(locked_outcome, 0) / total if total and locked_outcome else 0.0
        pct_best_ev = 100.0 * score_counts.get(best_ev.get(mid, ""), 0) / total if total else 0.0
        pct_modal = 100.0 * score_counts.get(modal.get(mid, ""), 0) / total if total else 0.0
        top_pct = 100.0 * score_counts.get(most_score, 0) / total if total and most_score else 0.0
        rows.append({
            "match_id": mid, "home_team": locked.get(mid, {}).get("home_team", txt(g.iloc[0].get("home_team"))), "away_team": locked.get(mid, {}).get("away_team", txt(g.iloc[0].get("away_team"))),
            "visible_pick_count": int(total), "most_common_scoreline": most_score, "most_common_scoreline_pct": top_pct,
            "scoreline_counts_json": json.dumps(score_counts, sort_keys=True), "home_pick_pct": 100.0 * outcome_counts.get("home", 0) / total if total else 0.0, "draw_pick_pct": 100.0 * outcome_counts.get("draw", 0) / total if total else 0.0, "away_pick_pct": 100.0 * outcome_counts.get("away", 0) / total if total else 0.0,
            "locked_score": locked_score, "locked_outcome": locked_outcome, "percentage_on_our_locked_score": pct_locked_score, "percentage_on_our_locked_outcome": pct_locked_outcome,
            "oddspedia_best_ev_score": best_ev.get(mid, ""), "percentage_on_oddspedia_best_ev_score": pct_best_ev, "oddspedia_modal_score": modal.get(mid, ""), "percentage_on_oddspedia_modal_score": pct_modal,
            "crowding_risk_flag": bool(top_pct >= 40.0 or pct_locked_score >= 25.0 or pct_locked_outcome >= 60.0),
        })
    return pd.DataFrame(rows)


def classify_players(leaderboard: pd.DataFrame, us_player: str, immediate_gap: float, wider_gap: float) -> dict[str, str]:
    classes: dict[str, str] = {}
    if leaderboard.empty or "player" not in leaderboard.columns:
        return classes
    points_col = "current_points" if "current_points" in leaderboard.columns else next((c for c in leaderboard.columns if "point" in c.lower()), "")
    if not points_col:
        return classes
    tmp = leaderboard.copy()
    tmp["_pts"] = pd.to_numeric(tmp[points_col], errors="coerce")
    us = tmp[tmp["player"].astype(str).str.contains(us_player, case=False, na=False)]
    us_pts = float(us.iloc[0]["_pts"]) if not us.empty and pd.notna(us.iloc[0]["_pts"]) else float(tmp["_pts"].max())
    for _, r in tmp.iterrows():
        player = txt(r.get("player"))
        pts = to_float(r.get("_pts"))
        if not player or pts is None:
            continue
        gap = us_pts - pts
        if player.lower().find(us_player.lower()) >= 0:
            classes[player] = "us"
        elif pts > us_pts:
            classes[player] = "leader"
        elif 0 <= gap <= immediate_gap:
            classes[player] = "immediate_chaser"
        elif 0 <= gap <= wider_gap:
            classes[player] = "wider_chaser"
        else:
            classes[player] = "other"
    return classes


def build_chaser_exposure(picks: pd.DataFrame, locked: dict[str, dict[str, Any]], leaderboard: pd.DataFrame, args: argparse.Namespace, comparison: pd.DataFrame) -> pd.DataFrame:
    classes = classify_players(leaderboard, args.us_player, args.immediate_chaser_gap, args.wider_chaser_gap)
    if picks.empty or not classes:
        return pd.DataFrame()
    modal = one_row_by_match(comparison, "modal_correct_score") or one_row_by_match(comparison, "oddspedia_best_score")
    rows: list[dict[str, Any]] = []
    for mid, g in picks.groupby("match_id", dropna=False):
        mid = txt(mid)
        locked_score = locked.get(mid, {}).get("locked_score", "")
        locked_outcome = locked.get(mid, {}).get("locked_outcome", "")
        chasers = g[g["player"].map(lambda p: classes.get(txt(p)) in {"leader", "immediate_chaser", "wider_chaser"})].copy()
        exact = chasers[chasers["picked_scoreline"].map(txt) == locked_score]
        same_outcome = chasers[chasers["picked_outcome"].map(txt) == locked_outcome]
        higher_risk = chasers[(chasers["picked_outcome"].map(txt) != locked_outcome) | (chasers["picked_scoreline"].map(txt) == modal.get(mid, ""))]
        gain_if_locked = []
        gain_if_modal = []
        for _, r in chasers.iterrows():
            pick = txt(r.get("picked_scoreline"))
            gain_if_locked.append(score_points(pick, locked_score) - 3.0 if locked_score else 0.0)
            gain_if_modal.append(score_points(pick, modal.get(mid, "")) - score_points(locked_score, modal.get(mid, "")) if modal.get(mid, "") else 0.0)
        rows.append({
            "match_id": mid, "home_team": locked.get(mid, {}).get("home_team", txt(g.iloc[0].get("home_team"))), "away_team": locked.get(mid, {}).get("away_team", txt(g.iloc[0].get("away_team"))),
            "locked_score": locked_score, "locked_outcome": locked_outcome, "chaser_count_visible": int(len(chasers)),
            "chasers_picked_our_exact_score": ";".join(exact["player"].map(txt).tolist()), "chasers_picked_same_outcome": ";".join(same_outcome["player"].map(txt).tolist()), "chasers_picked_higher_risk_alternative": ";".join(higher_risk["player"].map(txt).tolist()),
            "potential_chaser_gain_loss_if_locked_score_lands": min(gain_if_locked) if gain_if_locked else 0.0, "potential_chaser_gain_loss_if_chaser_modal_lands": max(gain_if_modal) if gain_if_modal else 0.0,
            "exposure_risk_flag": bool(len(higher_risk) > 0 and (max(gain_if_modal) if gain_if_modal else 0.0) > 0),
        })
    return pd.DataFrame(rows)


def completed_match_ids(results: pd.DataFrame) -> set[str]:
    if results.empty or "match_id" not in results.columns:
        return set()
    return {txt(v) for v in results["match_id"].tolist() if txt(v)}


def build_leverage(locked: dict[str, dict[str, Any]], leaderboard: pd.DataFrame, results: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:
    done = completed_match_ids(results)
    remaining = [mid for mid in locked if mid not in done]
    classes = classify_players(leaderboard, args.us_player, args.immediate_chaser_gap, args.wider_chaser_gap)
    points_col = "current_points" if "current_points" in leaderboard.columns else next((c for c in leaderboard.columns if "point" in c.lower()), "") if not leaderboard.empty else ""
    rows: list[dict[str, Any]] = []
    us_pts = None
    if points_col and not leaderboard.empty:
        tmp = leaderboard.copy()
        tmp["_pts"] = pd.to_numeric(tmp[points_col], errors="coerce")
        us = tmp[tmp["player"].astype(str).str.contains(args.us_player, case=False, na=False)]
        us_pts = float(us.iloc[0]["_pts"]) if not us.empty and pd.notna(us.iloc[0]["_pts"]) else float(tmp["_pts"].max())
        players = tmp.to_dict(orient="records")
    else:
        players = []
    for mid in remaining:
        info = locked[mid]
        for p in players:
            player = txt(p.get("player"))
            pts = to_float(p.get("_pts"))
            if not player or pts is None or classes.get(player) == "us":
                continue
            gap_to_us = (us_pts - pts) if us_pts is not None else None
            max_possible = 3.0 * len(remaining)
            leverage = bool(gap_to_us is not None and gap_to_us <= max_possible and gap_to_us <= 6.0)
            rows.append({
                "match_id": mid, "home_team": info.get("home_team", ""), "away_team": info.get("away_team", ""), "player": player, "player_class": classes.get(player, "other"),
                "points_gap_to_us": gap_to_us, "remaining_matches_count": len(remaining), "max_possible_remaining_points": max_possible,
                "realistic_chase_range": max_possible * 0.35, "recommendation": "defensive" if gap_to_us is not None and gap_to_us <= 3.0 else "aggressive_ok", "leverage_flag": leverage,
            })
    return pd.DataFrame(rows)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def main() -> int:
    args = build_parser().parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    picks = load_csv(args.pool_picks_csv)
    leaderboard = load_csv(args.leaderboard_csv)
    locked = locked_lookup(load_csv(args.locked_picks_csv))
    results = load_csv(args.match_results_csv)
    comparison = load_csv(args.oddspedia_comparison_csv)
    ev = load_csv(args.oddspedia_ev_recommendations_csv)
    market = load_csv(args.market_odds_csv)

    profiles, profile_warning = build_profiles(picks, market)
    distribution = build_distribution(picks, locked, comparison, ev)
    exposure = build_chaser_exposure(picks, locked, leaderboard, args, comparison)
    leverage = build_leverage(locked, leaderboard, results, args)

    profiles.to_csv(out_dir / "superbru_player_profiles.csv", index=False)
    distribution.to_csv(out_dir / "superbru_pool_pick_distribution.csv", index=False)
    exposure.to_csv(out_dir / "superbru_chaser_exposure.csv", index=False)
    leverage.to_csv(out_dir / "superbru_remaining_fixture_leverage.csv", index=False)
    write_json(out_dir / "superbru_player_profiles_summary.json", {"generated_at_utc": datetime.now(timezone.utc).isoformat(), "player_count": int(len(profiles)), "warning": profile_warning})
    summary = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), "pool_pick_rows": int(len(picks)), "leaderboard_rows": int(len(leaderboard)), "player_profile_rows": int(len(profiles)), "distribution_rows": int(len(distribution)), "chaser_exposure_rows": int(len(exposure)), "remaining_leverage_rows": int(len(leverage)), "warning": "Pool picks unavailable or empty. Downstream pool outputs were still written as empty files." if picks.empty else ""}
    write_json(out_dir / "superbru_pool_intelligence_summary.json", summary)
    if summary["warning"]:
        print(f"WARNING: {summary['warning']}")
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
