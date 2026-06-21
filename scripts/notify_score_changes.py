from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Notify only when the daily robust card changes scores versus the previous card.")
    parser.add_argument("--current-card-csv", default="outputs/daily_robust_card/daily_robust_superbru_card.csv")
    parser.add_argument("--previous-card-csv", default="outputs/daily_robust_card/previous_superbru_card.csv")
    parser.add_argument("--audit-csv", default="outputs/daily_robust_card/daily_switch_audit.csv")
    parser.add_argument("--out-md", default="outputs/daily_robust_card/score_change_notification.md")
    parser.add_argument("--github-output", default="")
    parser.add_argument("--github-step-summary", default="")
    parser.add_argument("--notify-on-first-run", action="store_true")
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def norm_team(value: Any) -> str:
    return "".join(ch.lower() for ch in txt(value) if ch.isalnum())


def match_key(home: Any, away: Any) -> tuple[str, str]:
    return norm_team(home), norm_team(away)


def load_csv(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(p).fillna("")
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def pick_column(frame: pd.DataFrame) -> str:
    for col in ["robust_locked_pick", "locked_pick", "final_pick", "recommended_scoreline", "pick"]:
        if col in frame.columns:
            return col
    raise ValueError("Could not find a pick column in card CSV.")


def audit_lookup(audit: pd.DataFrame) -> dict[tuple[str, str, str], pd.Series]:
    lookup: dict[tuple[str, str, str], pd.Series] = {}
    if audit.empty:
        return lookup
    for _, row in audit.iterrows():
        key = (norm_team(row.get("home_team")), norm_team(row.get("away_team")), txt(row.get("candidate_pick")))
        lookup[key] = row
    return lookup


def compare_cards(current: pd.DataFrame, previous: pd.DataFrame, audit: pd.DataFrame) -> list[dict[str, Any]]:
    current_pick_col = pick_column(current)
    previous_pick_col = pick_column(previous)
    previous_lookup = {
        match_key(row.get("home_team"), row.get("away_team")): row
        for _, row in previous.iterrows()
    }
    audit_by_match_pick = audit_lookup(audit)

    changes: list[dict[str, Any]] = []
    for _, row in current.iterrows():
        key = match_key(row.get("home_team"), row.get("away_team"))
        prev = previous_lookup.get(key)
        if prev is None:
            continue
        old_pick = txt(prev.get(previous_pick_col))
        new_pick = txt(row.get(current_pick_col))
        if not old_pick or not new_pick or old_pick == new_pick:
            continue
        audit_key = (key[0], key[1], new_pick)
        audit_row = audit_by_match_pick.get(audit_key)
        changes.append(
            {
                "commence_time": txt(row.get("commence_time")),
                "home_team": txt(row.get("home_team")),
                "away_team": txt(row.get("away_team")),
                "previous_pick": old_pick,
                "new_pick": new_pick,
                "reason": txt(audit_row.get("source_decision_reasons")) if audit_row is not None else "daily robust card changed",
                "blocked_by": txt(audit_row.get("blocked_by")) if audit_row is not None else "",
                "component_improvement": txt(audit_row.get("component_improvement")) if audit_row is not None else "",
                "ev_loss": txt(audit_row.get("ev_loss")) if audit_row is not None else "",
                "delta_p_finish_first": txt(audit_row.get("delta_p_finish_first")) if audit_row is not None else "",
            }
        )
    return sorted(changes, key=lambda item: item.get("commence_time", ""))


def markdown_report(changes: list[dict[str, Any]], first_run: bool) -> str:
    now = datetime.now(timezone.utc).isoformat()
    if first_run and not changes:
        return f"# Superbru daily robust card\n\nGenerated: `{now}`\n\nNo previous card was available, so no score-change notification was created.\n"
    if not changes:
        return f"# Superbru daily robust card\n\nGenerated: `{now}`\n\nNo score changes needed today.\n"

    lines = [
        "# Superbru score changes needed",
        "",
        f"Generated: `{now}`",
        "",
        f"Change count: **{len(changes)}**",
        "",
        "| Kickoff UTC | Match | Previous | New | EV loss | Leader delta | Reason |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for item in changes:
        match = f"{item['home_team']} vs {item['away_team']}"
        reason = item.get("reason") or "daily robust card changed"
        reason = reason.replace("|", "/")
        lines.append(
            "| {kickoff} | {match} | {old} | {new} | {ev} | {delta} | {reason} |".format(
                kickoff=item.get("commence_time", ""),
                match=match,
                old=item.get("previous_pick", ""),
                new=item.get("new_pick", ""),
                ev=item.get("ev_loss", ""),
                delta=item.get("delta_p_finish_first", ""),
                reason=reason,
            )
        )
    lines.extend(
        [
            "",
            "Update only these scores in Superbru. If the table is empty, keep the current card unchanged.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_github_output(path: str, values: dict[str, Any]) -> None:
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def main() -> int:
    args = build_parser().parse_args()
    current = load_csv(args.current_card_csv)
    previous = load_csv(args.previous_card_csv)
    audit = load_csv(args.audit_csv)

    if current.empty:
        raise FileNotFoundError(f"Missing or empty current card: {args.current_card_csv}")

    first_run = previous.empty
    changes = [] if first_run and not args.notify_on_first_run else compare_cards(current, previous, audit)
    notify = bool(changes)

    out_md = Path(args.out_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    report = markdown_report(changes, first_run)
    out_md.write_text(report, encoding="utf-8")

    summary = {
        "notify": notify,
        "change_count": len(changes),
        "first_run": first_run,
        "changes": changes,
        "notification_markdown": str(out_md),
    }
    json_path = out_md.with_suffix(".json")
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    if args.github_step_summary:
        Path(args.github_step_summary).write_text(report, encoding="utf-8")

    write_github_output(
        args.github_output,
        {
            "notify": "true" if notify else "false",
            "change_count": len(changes),
            "first_run": "true" if first_run else "false",
            "body_file": str(out_md),
        },
    )

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
