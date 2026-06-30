from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROFILE_COLUMNS = [
    "player",
    "raw_weight",
    "top1_weight",
    "top2_weight",
    "top3_weight",
    "modal_weight",
    "chase_weight",
    "chalk_weight",
    "rank",
    "current_points",
    "point_gap_to_leader_player",
    "profile_type",
]

PROFILE_PRESETS: dict[str, dict[str, float]] = {
    # Player is ahead/tied with us: model mostly chalk/raw because they can protect.
    "ahead_or_tied": {
        "raw_weight": 0.34,
        "top1_weight": 0.26,
        "top2_weight": 0.14,
        "top3_weight": 0.06,
        "modal_weight": 0.10,
        "chase_weight": 0.02,
        "chalk_weight": 0.08,
    },
    # Close chaser: balanced, but not assumed to be an extreme differentiator.
    "close_chaser": {
        "raw_weight": 0.30,
        "top1_weight": 0.24,
        "top2_weight": 0.15,
        "top3_weight": 0.09,
        "modal_weight": 0.10,
        "chase_weight": 0.05,
        "chalk_weight": 0.07,
    },
    # Mid-range chaser: modest extra differentiation pressure.
    "medium_chaser": {
        "raw_weight": 0.26,
        "top1_weight": 0.22,
        "top2_weight": 0.16,
        "top3_weight": 0.11,
        "modal_weight": 0.08,
        "chase_weight": 0.10,
        "chalk_weight": 0.07,
    },
    # Longer chaser: more likely to chase exact/differentiate.
    "long_chaser": {
        "raw_weight": 0.22,
        "top1_weight": 0.18,
        "top2_weight": 0.16,
        "top3_weight": 0.14,
        "modal_weight": 0.06,
        "chase_weight": 0.18,
        "chalk_weight": 0.06,
    },
}


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def norm_name(value: Any) -> str:
    return "".join(ch.lower() for ch in txt(value) if ch.isalnum())


def fnum(value: Any, default: float = 0.0) -> float:
    try:
        text = txt(value).replace(",", ".")
        if not text:
            return default
        return float(text)
    except Exception:
        return default


def read_leaderboard(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Live leaderboard CSV missing: {path}")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    parsed = []
    for idx, row in enumerate(rows, start=1):
        player = txt(row.get("player"))
        if not player:
            continue
        parsed.append(
            {
                "rank": int(fnum(row.get("rank"), idx) or idx),
                "player": player,
                "current_points": fnum(row.get("current_points"), 0.0),
                "raw": row,
            }
        )
    if not parsed:
        raise ValueError(f"No usable leaderboard rows found in {path}")
    return sorted(parsed, key=lambda row: (int(row["rank"]), -float(row["current_points"])))


def find_player(rows: list[dict[str, Any]], player_name: str) -> dict[str, Any]:
    target = norm_name(player_name)
    exact = [row for row in rows if norm_name(row["player"]) == target]
    if exact:
        return exact[0]
    fuzzy = [
        row
        for row in rows
        if target and (target in norm_name(row["player"]) or norm_name(row["player"]) in target)
    ]
    if fuzzy:
        return fuzzy[0]
    raise ValueError(f"Leader player {player_name!r} was not found in live leaderboard.")


def profile_type_for_gap(point_gap_to_me: float) -> str:
    if point_gap_to_me <= 0:
        return "ahead_or_tied"
    if point_gap_to_me <= 2.0:
        return "close_chaser"
    if point_gap_to_me <= 5.0:
        return "medium_chaser"
    return "long_chaser"


def select_chasers(
    rows: list[dict[str, Any]],
    me: dict[str, Any],
    *,
    chaser_range: float,
    min_chasers: int,
    max_chasers: int,
) -> list[dict[str, Any]]:
    my_name_norm = norm_name(me["player"])
    my_points = float(me["current_points"])

    candidates = []
    for row in rows:
        if norm_name(row["player"]) == my_name_norm:
            continue
        points = float(row["current_points"])
        point_gap_to_me = my_points - points
        row = dict(row)
        row["point_gap_to_leader_player"] = round(point_gap_to_me, 2)
        row["profile_type"] = profile_type_for_gap(point_gap_to_me)
        candidates.append(row)

    # Primary live threat set: anyone ahead/tied, plus anyone behind within range.
    selected = [
        row
        for row in candidates
        if float(row["point_gap_to_leader_player"]) <= chaser_range
    ]

    # Safety fallback: never model a clean run with zero/too few chasers just because
    # the configured range was too tight. Add closest competitors by points.
    if len(selected) < min_chasers:
        selected_names = {norm_name(row["player"]) for row in selected}
        by_threat = sorted(
            candidates,
            key=lambda row: (
                abs(float(row["point_gap_to_leader_player"])),
                int(row["rank"]),
            ),
        )
        for row in by_threat:
            if norm_name(row["player"]) in selected_names:
                continue
            selected.append(row)
            selected_names.add(norm_name(row["player"]))
            if len(selected) >= min_chasers:
                break

    selected = sorted(
        selected,
        key=lambda row: (
            float(row["point_gap_to_leader_player"]) > 0,  # ahead/tied first
            abs(float(row["point_gap_to_leader_player"])),
            int(row["rank"]),
        ),
    )[:max_chasers]
    return selected


def build_profiles(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    profiles = []
    for row in selected:
        profile_type = txt(row.get("profile_type")) or "medium_chaser"
        weights = PROFILE_PRESETS.get(profile_type, PROFILE_PRESETS["medium_chaser"])
        payload = {
            "player": row["player"],
            **weights,
            "rank": row["rank"],
            "current_points": row["current_points"],
            "point_gap_to_leader_player": row["point_gap_to_leader_player"],
            "profile_type": profile_type,
        }
        profiles.append(payload)
    return profiles


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PROFILE_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build live chaser profiles from a freshly scraped SuperBru leaderboard.")
    parser.add_argument("--leaderboard-csv", default="outputs/superbru_pool/live_pool_leaderboard.csv")
    parser.add_argument("--leader-player", default=os.environ.get("SUPERBRU_PLAYER_NAME", "Danie"))
    parser.add_argument("--chaser-range", type=float, default=8.0)
    parser.add_argument("--min-chasers", type=int, default=3)
    parser.add_argument("--max-chasers", type=int, default=10)
    parser.add_argument("--out-csv", default="outputs/superbru_pool/live_chaser_profiles.csv")
    parser.add_argument("--out-chasers", default="outputs/superbru_pool/live_chasers.txt")
    parser.add_argument("--summary-json", default="outputs/superbru_pool/live_chaser_profiles_summary.json")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    leaderboard_path = Path(args.leaderboard_csv)
    rows = read_leaderboard(leaderboard_path)
    me = find_player(rows, args.leader_player)
    selected = select_chasers(
        rows,
        me,
        chaser_range=float(args.chaser_range),
        min_chasers=int(args.min_chasers),
        max_chasers=int(args.max_chasers),
    )
    if not selected:
        raise SystemExit("No live chasers selected from the leaderboard; refusing to use stale static profiles.")

    profiles = build_profiles(selected)
    out_csv = Path(args.out_csv)
    out_chasers = Path(args.out_chasers)
    summary_json = Path(args.summary_json)
    write_csv(out_csv, profiles)
    out_chasers.parent.mkdir(parents=True, exist_ok=True)
    chaser_names = [row["player"] for row in profiles]
    out_chasers.write_text(",".join(chaser_names), encoding="utf-8")

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "leader_player": me,
        "chaser_range": float(args.chaser_range),
        "min_chasers": int(args.min_chasers),
        "max_chasers": int(args.max_chasers),
        "selected_chaser_count": len(profiles),
        "selected_chasers": profiles,
        "source_leaderboard_csv": str(leaderboard_path),
        "out_csv": str(out_csv),
        "out_chasers": str(out_chasers),
        "note": "Profiles are live identity/points profiles derived from the scraped leaderboard with neutral gap-based behaviour priors; no committed static chaser file is used.",
    }
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    summary_json.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_chasers}")
    print(f"Wrote {summary_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
