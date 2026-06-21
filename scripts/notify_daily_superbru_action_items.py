from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

REVIEW_ACTIONS = {
    "same_outcome_review",
    "strong_same_outcome_review",
    "market_outcome_conflict_review",
    "review_missing_locked_score",
}
EV_REVIEW_LEVELS = {"ev_review", "strong_ev_review", "missing_locked_pick"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create one daily notification only when Superbru action items changed.")
    parser.add_argument("--score-change-json", default="outputs/daily_robust_card/score_change_notification.json")
    parser.add_argument("--oddspedia-comparison-csv", default="outputs/oddspedia_pick_validation/oddspedia_pick_comparison.csv")
    parser.add_argument("--oddspedia-ev-recommendations-csv", default="outputs/oddspedia_pick_validation/oddspedia_ev_recommendations.csv")
    parser.add_argument("--out-md", default="outputs/daily_notifications/daily_superbru_action_items.md")
    parser.add_argument("--out-json", default="outputs/daily_notifications/daily_superbru_action_items.json")
    parser.add_argument("--state-json", default="outputs/daily_notifications/daily_superbru_action_state.json")
    parser.add_argument("--github-output", default="")
    parser.add_argument("--github-step-summary", default="")
    parser.add_argument("--update-state", action="store_true")
    parser.add_argument("--notify-on-first-state", action="store_true", help="Notify on first run if action items exist.")
    parser.add_argument("--include-market-conflict", action="store_true", default=True)
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def to_float(value: Any) -> float | None:
    try:
        s = txt(value)
        if not s:
            return None
        return float(s)
    except Exception:
        return None


def truthy(value: Any) -> bool:
    return txt(value).lower() in {"true", "1", "yes", "y"}


def load_score_changes(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    changes = data.get("changes", [])
    return changes if isinstance(changes, list) else []


def load_oddspedia_reviews(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    try:
        df = pd.read_csv(p).fillna("")
    except pd.errors.EmptyDataError:
        return []
    if "action" not in df.columns:
        return []
    reviews = df[df["action"].astype(str).isin(REVIEW_ACTIONS)].copy()
    if reviews.empty:
        return []
    if "probability_gap_vs_locked_pct" in reviews.columns:
        reviews["_gap"] = reviews["probability_gap_vs_locked_pct"].map(to_float).fillna(-999)
        reviews = reviews.sort_values("_gap", ascending=False).drop(columns=["_gap"])
    return reviews.to_dict(orient="records")


def load_ev_reviews(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    try:
        df = pd.read_csv(p).fillna("")
    except pd.errors.EmptyDataError:
        return []
    if df.empty:
        return []
    mask = pd.Series([False] * len(df))
    if "review_flag" in df.columns:
        mask = mask | df["review_flag"].map(truthy)
    if "review_level" in df.columns:
        mask = mask | df["review_level"].astype(str).isin(EV_REVIEW_LEVELS)
    reviews = df[mask].copy()
    if reviews.empty:
        return []
    if "ev_gap_vs_locked" in reviews.columns:
        reviews["_gap"] = reviews["ev_gap_vs_locked"].map(to_float).fillna(-999)
        reviews = reviews.sort_values("_gap", ascending=False).drop(columns=["_gap"])
    return reviews.to_dict(orient="records")


def stable_digest(score_changes: list[dict[str, Any]], oddspedia_reviews: list[dict[str, Any]], ev_reviews: list[dict[str, Any]]) -> str:
    payload = {
        "score_changes": [
            {"type": "score_change", "home_team": txt(i.get("home_team")), "away_team": txt(i.get("away_team")), "previous_pick": txt(i.get("previous_pick")), "new_pick": txt(i.get("new_pick"))}
            for i in score_changes
        ],
        "oddspedia_reviews": [
            {"type": "oddspedia_probability_review", "match_id": txt(i.get("match_id")), "locked_pick": txt(i.get("locked_pick")), "best_score": txt(i.get("oddspedia_best_score")), "action": txt(i.get("action"))}
            for i in oddspedia_reviews
        ],
        "ev_reviews": [
            {"type": "oddspedia_ev_review", "match_id": txt(i.get("match_id")), "locked_pick": txt(i.get("locked_pick")), "best_ev_scoreline": txt(i.get("best_ev_scoreline")), "review_level": txt(i.get("review_level"))}
            for i in ev_reviews
        ],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def load_previous_state(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def markdown_report(score_changes: list[dict[str, Any]], oddspedia_reviews: list[dict[str, Any]], ev_reviews: list[dict[str, Any]], notify: bool, state_changed: bool) -> str:
    now = datetime.now(timezone.utc).isoformat()
    lines = [
        "# Daily Superbru action items",
        "",
        f"Generated: `{now}`",
        "",
        f"Notify: **{'yes' if notify else 'no'}**",
        f"State changed: **{'yes' if state_changed else 'no'}**",
        "",
        f"Score-change items: **{len(score_changes)}**",
        f"Oddspedia probability review items: **{len(oddspedia_reviews)}**",
        f"Oddspedia Superbru EV review items: **{len(ev_reviews)}**",
        "",
    ]
    if score_changes:
        lines.extend(["## Score changes to make in Superbru", "", "| Kickoff UTC | Match | Previous | New | EV loss | Leader delta | Reason |", "|---|---|---:|---:|---:|---:|---|"])
        for item in score_changes:
            match = f"{txt(item.get('home_team'))} vs {txt(item.get('away_team'))}"
            reason = txt(item.get("reason")).replace("|", "/")
            lines.append(f"| {txt(item.get('commence_time'))} | {match} | {txt(item.get('previous_pick'))} | {txt(item.get('new_pick'))} | {txt(item.get('ev_loss'))} | {txt(item.get('delta_p_finish_first'))} | {reason} |")
        lines.append("")
    if oddspedia_reviews:
        lines.extend(["## Oddspedia SmartBet probability review items", "", "These are review flags, not automatic switches.", "", "| Match | Locked | Locked % | Oddspedia best | Best % | Gap | Action |", "|---|---:|---:|---:|---:|---:|---|"])
        for item in oddspedia_reviews:
            lines.append("| {match} | {locked} | {locked_p} | {best} | {best_p} | {gap} | {action} |".format(match=txt(item.get("match_id")), locked=txt(item.get("locked_pick")), locked_p=txt(item.get("locked_pick_probability_pct")), best=txt(item.get("oddspedia_best_score")), best_p=txt(item.get("oddspedia_best_probability_pct")), gap=txt(item.get("probability_gap_vs_locked_pct")), action=txt(item.get("action"))))
        lines.append("")
    if ev_reviews:
        lines.extend(["## Oddspedia Superbru EV review items", "", "These compare expected Superbru points from the Oddspedia correct-score grid. Do not auto-change picks.", "", "| Match | Locked | Locked EV | Best EV score | Best EV | Gap | Same outcome | Review |", "|---|---:|---:|---:|---:|---:|---:|---|"])
        for item in ev_reviews:
            lines.append("| {match} | {locked} | {locked_ev} | {best} | {best_ev} | {gap} | {same} | {review} |".format(match=txt(item.get("match_id")), locked=txt(item.get("locked_pick")), locked_ev=txt(item.get("current_locked_pick_ev")), best=txt(item.get("best_ev_scoreline")), best_ev=txt(item.get("best_ev_expected_points")), gap=txt(item.get("ev_gap_vs_locked")), same=txt(item.get("same_outcome_flag")), review=txt(item.get("review_level"))))
        lines.append("")
    if not score_changes and not oddspedia_reviews and not ev_reviews:
        lines.append("No changes or SmartBet review items today. Keep the current card unchanged.\n")
    elif not state_changed:
        lines.append("The same action set was already reported previously, so this run is suppressed to avoid repeated notifications.\n")
    else:
        lines.append("Review the items above and update only the scores that survive final judgement.\n")
    return "\n".join(lines)


def write_github_output(path: str, values: dict[str, Any]) -> None:
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def main() -> int:
    args = build_parser().parse_args()
    score_changes = load_score_changes(args.score_change_json)
    oddspedia_reviews = load_oddspedia_reviews(args.oddspedia_comparison_csv)
    ev_reviews = load_ev_reviews(args.oddspedia_ev_recommendations_csv)
    digest = stable_digest(score_changes, oddspedia_reviews, ev_reviews)
    previous_state = load_previous_state(args.state_json)
    previous_digest = txt(previous_state.get("digest"))
    first_state = not bool(previous_digest)
    action_count = len(score_changes) + len(oddspedia_reviews) + len(ev_reviews)
    has_actions = action_count > 0
    state_changed = digest != previous_digest
    notify = has_actions and state_changed and (not first_state or args.notify_on_first_state)

    out_md = Path(args.out_md)
    out_json = Path(args.out_json)
    state_path = Path(args.state_json)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    report = markdown_report(score_changes, oddspedia_reviews, ev_reviews, notify, state_changed)
    out_md.write_text(report, encoding="utf-8")
    if args.github_step_summary:
        Path(args.github_step_summary).write_text(report, encoding="utf-8")

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "notify": notify,
        "has_actions": has_actions,
        "state_changed": state_changed,
        "first_state": first_state,
        "digest": digest,
        "previous_digest": previous_digest,
        "score_change_count": len(score_changes),
        "oddspedia_probability_review_count": len(oddspedia_reviews),
        "oddspedia_ev_review_count": len(ev_reviews),
        "oddspedia_review_count": len(oddspedia_reviews) + len(ev_reviews),
        "action_count": action_count,
        "score_changes": score_changes,
        "oddspedia_reviews": oddspedia_reviews,
        "oddspedia_ev_reviews": ev_reviews,
        "notification_markdown": str(out_md),
        "state_json": str(state_path),
    }
    out_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    if args.update_state:
        state_path.write_text(json.dumps({"updated_at_utc": datetime.now(timezone.utc).isoformat(), "digest": digest, "action_count": action_count, "score_change_count": len(score_changes), "oddspedia_probability_review_count": len(oddspedia_reviews), "oddspedia_ev_review_count": len(ev_reviews), "oddspedia_review_count": len(oddspedia_reviews) + len(ev_reviews)}, indent=2), encoding="utf-8")

    write_github_output(args.github_output, {"notify": "true" if notify else "false", "has_actions": "true" if has_actions else "false", "state_changed": "true" if state_changed else "false", "action_count": action_count, "score_change_count": len(score_changes), "oddspedia_review_count": len(oddspedia_reviews) + len(ev_reviews), "body_file": str(out_md)})
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
