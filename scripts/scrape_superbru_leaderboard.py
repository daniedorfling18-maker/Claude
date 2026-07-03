from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

from auto_pick_match_scoped import compute_pool_standing, login, scrape_leaderboard_in_session


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scrape the live SuperBru pool leaderboard into a CSV.")
    parser.add_argument("--email", default=os.environ.get("SUPERBRU_EMAIL") or os.environ.get("SUPERBRU_USERNAME", ""))
    parser.add_argument("--password", default=os.environ.get("SUPERBRU_PASSWORD", ""))
    parser.add_argument("--login-url", default=os.environ.get("SUPERBRU_LOGIN_URL", "https://www.superbru.com/login"))
    parser.add_argument(
        "--pool-url",
        default=os.environ.get(
            "SUPERBRU_POOL_URL",
            "https://www.superbru.com/worldcup_predictor/pool_view.php?t=1296&p=13236623&view=matches",
        ),
    )
    parser.add_argument(
        "--leader-player",
        default=os.environ.get("SUPERBRU_PLAYER_NAME", "Danie"),
        help="Your display name on the SuperBru leaderboard.",
    )
    parser.add_argument(
        "--leaderboard-pool-keywords",
        default=os.environ.get("SUPERBRU_POOL_KEYWORDS", "Moore Infinity,FIFA WC 26,World Cup 2026"),
        help="Comma-separated keywords used to verify the correct pool page.",
    )
    parser.add_argument("--out-csv", default="outputs/superbru_pool/live_pool_leaderboard.csv")
    parser.add_argument("--summary-json", default="outputs/superbru_pool/live_pool_leaderboard_summary.json")
    parser.add_argument("--diagnostics-dir", default="outputs/superbru_pool/leaderboard_diagnostics")
    parser.add_argument("--headless", dest="headless", action="store_true", default=True)
    parser.add_argument("--headed", dest="headless", action="store_false")
    return parser


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["rank", "movement_or_yellow_caps", "player", "current_points"])
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "rank": row.get("rank", ""),
                    "movement_or_yellow_caps": row.get("movement_or_yellow_caps", ""),
                    "player": row.get("player", ""),
                    "current_points": row.get("current_points", ""),
                }
            )


def _write_summary(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


async def run(args: argparse.Namespace) -> int:
    if not args.email or not args.password:
        raise SystemExit(
            "SUPERBRU_EMAIL or SUPERBRU_USERNAME, plus SUPERBRU_PASSWORD, are required "
            "to scrape the live leaderboard."
        )
    if not args.pool_url:
        raise SystemExit("SUPERBRU_POOL_URL is required to scrape the live leaderboard.")

    out_csv = Path(args.out_csv)
    summary_json = Path(args.summary_json)
    diagnostics_dir = Path(args.diagnostics_dir) if args.diagnostics_dir else None
    keywords = [item.strip() for item in str(args.leaderboard_pool_keywords or "").split(",") if item.strip()]

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=args.headless)
        page = await browser.new_page()
        try:
            login_ok = await login(page, args, diagnostics_dir)
            if not login_ok:
                summary = {
                    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                    "status": "login_failed",
                    "out_csv": str(out_csv),
                }
                _write_summary(summary_json, summary)
                raise SystemExit("SuperBru login failed; refusing to use stale committed leaderboard.")

            rows = await scrape_leaderboard_in_session(page, args.pool_url, keywords, diagnostics_dir)
            if not rows:
                summary = {
                    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                    "status": "no_leaderboard_rows_found",
                    "pool_url": args.pool_url,
                    "pool_keywords": keywords,
                    "out_csv": str(out_csv),
                }
                _write_summary(summary_json, summary)
                raise SystemExit("No SuperBru leaderboard rows found; refusing to use stale committed leaderboard.")

            standing = compute_pool_standing(rows, args.leader_player, chaser_range=8.0)
            _write_csv(out_csv, rows)
            summary = {
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "status": "ok",
                "row_count": len(rows),
                "leader_player": args.leader_player,
                "pool_standing": standing,
                "top_rows": rows[:8],
                "out_csv": str(out_csv),
            }
            _write_summary(summary_json, summary)
            print(json.dumps(summary, indent=2, default=str))
            print(f"Wrote {out_csv}")
            print(f"Wrote {summary_json}")
            return 0
        finally:
            await browser.close()


def main() -> int:
    args = build_parser().parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
