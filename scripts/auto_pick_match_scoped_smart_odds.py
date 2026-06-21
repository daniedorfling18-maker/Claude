"""
Smart-odds wrapper for match-scoped Auto Pick.

This keeps the robust locked-card workflow as the source of truth, but spends
The Odds API credits only when a SuperBru pick actually needs to be submitted
or changed. Backup runs that find the locked-card score already visible on
SuperBru skip both the odds snapshot and submit step.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_base_module():
    base_path = Path(__file__).with_name("auto_pick_match_scoped.py")
    spec = importlib.util.spec_from_file_location("auto_pick_match_scoped", base_path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Could not load base auto-pick module: {base_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = load_base_module()


def normalized_score(value: Any) -> str:
    text = base.txt(value)
    numbers = re.findall(r"\d+", text)
    if len(numbers) < 2:
        return ""
    return f"{int(numbers[0])}-{int(numbers[1])}"


async def run(args: argparse.Namespace) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    scan_results, queued, scan_status = await base.scan_superbru_matches(args, out_dir)
    if scan_status == "login_failed":
        return {"status": "login_failed", "run_at_utc": now.isoformat(), "scan_results": scan_results}

    submitted_results: list[dict[str, Any]] = []
    for entry in queued:
        pick_lookup = base.find_pick_from_card(entry, args.pick_card_csv)
        entry["pick_lookup"] = pick_lookup
        if pick_lookup.get("status") != "found" or not base.txt(pick_lookup.get("pick")):
            entry["status"] = "pick_card_missing"
            submitted_results.append(entry)
            continue

        pick = base.txt(pick_lookup["pick"])
        entry["selected_pick"] = pick
        entry["current_pick_normalized"] = normalized_score(entry.get("current_pick"))
        entry["selected_pick_normalized"] = normalized_score(pick)

        if entry["current_pick_normalized"] and entry["current_pick_normalized"] == entry["selected_pick_normalized"]:
            entry["status"] = "already_picked"
            entry["match_odds"] = {"status": "skipped_already_picked"}
            submitted_results.append(entry)
            continue

        entry["match_odds"] = base.fetch_match_odds_snapshot(args, entry, out_dir)

        if args.dry_run:
            entry["status"] = "dry_run"
            submitted_results.append(entry)
            continue

        try:
            submit_result = await base.submit_pick(args, entry["home_team"], entry["away_team"], pick, out_dir)
            entry["status"] = submit_result.get("status", "unknown")
            entry["submit_result"] = submit_result
        except Exception as exc:
            entry["status"] = "submit_failed"
            entry["error"] = str(exc)
        submitted_results.append(entry)

    summary = {
        "run_at_utc": now.isoformat(),
        "mode": "match_scoped_locked_card_auto_pick_smart_odds",
        "window_minutes": args.window_minutes,
        "dry_run": args.dry_run,
        "pick_card_csv": args.pick_card_csv,
        "scan_results": scan_results,
        "queued_count": len(queued),
        "results": submitted_results,
        "submitted": sum(1 for item in submitted_results if item.get("status") == "submitted"),
        "already_picked": sum(1 for item in submitted_results if item.get("status") == "already_picked"),
        "dry_run_count": sum(1 for item in submitted_results if item.get("status") == "dry_run"),
        "pick_card_missing": sum(1 for item in submitted_results if item.get("status") == "pick_card_missing"),
        "submit_failed": sum(1 for item in submitted_results if item.get("status") == "submit_failed"),
    }

    ts = now.strftime("%Y%m%dT%H%M%SZ")
    (out_dir / f"{ts}_auto_pick_match_scoped_smart_odds.json").write_text(
        json.dumps(summary, indent=2, default=str),
        encoding="utf-8",
    )
    return summary


def main() -> int:
    args = base.build_parser().parse_args()
    if not args.email or not args.password:
        print("ERROR: SUPERBRU_EMAIL and SUPERBRU_PASSWORD must be set", file=sys.stderr)
        return 1
    result = asyncio.run(run(args))
    print("\n" + json.dumps(result, indent=2, default=str))
    return 0 if result.get("status") != "login_failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
