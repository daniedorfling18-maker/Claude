from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

API_HOST = "https://api.the-odds-api.com"

FIELDS = [
    "event_id",
    "sport_key",
    "commence_time",
    "home_team",
    "away_team",
    "bookmaker",
    "bookmaker_title",
    "last_update",
    "market",
    "outcome_name",
    "price",
    "point",
    "description",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch market odds from The Odds API.")
    parser.add_argument("--api-key", default=os.getenv("THE_ODDS_API_KEY", ""))
    parser.add_argument("--sport", default="soccer_fifa_world_cup")
    parser.add_argument("--regions", default="uk,eu,us,au")
    parser.add_argument("--markets", default="h2h,spreads,totals")
    parser.add_argument("--odds-format", default="decimal")
    parser.add_argument("--date-format", default="iso")
    parser.add_argument("--commence-from", default="")
    parser.add_argument("--commence-to", default="")
    parser.add_argument("--list-soccer", action="store_true")
    parser.add_argument("--out-json", default="outputs/market_odds/market_odds_raw.json")
    parser.add_argument("--out-csv", default="outputs/market_odds/market_odds_flat.csv")
    parser.add_argument(
        "--allow-stale-on-failure",
        action="store_true",
        help="Keep using existing output files if The Odds API fetch fails.",
    )
    parser.add_argument(
        "--allow-empty-on-failure",
        action="store_true",
        help="Write empty, schema-valid outputs if the fetch fails and no stale outputs exist.",
    )
    return parser


def request_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "superbru-market-validator/1.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def require_key(api_key: str) -> None:
    if not api_key or api_key == "YOUR_THE_ODDS_API_KEY":
        raise SystemExit("Set THE_ODDS_API_KEY first, or pass --api-key.")


def read_http_error(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:
        body = ""
    return f"HTTP {exc.code} {exc.reason}: {body[:2000]}"


def list_soccer(api_key: str, out_json: Path) -> int:
    url = f"{API_HOST}/v4/sports/?" + urllib.parse.urlencode({"apiKey": api_key, "all": "true"})
    data = request_json(url)
    soccer = [item for item in data if str(item.get("group", "")).lower() == "soccer"]
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(soccer, indent=2), encoding="utf-8")
    for item in soccer:
        print(f"{item.get('key')} | {item.get('title')} | active={item.get('active')} | {item.get('description')}")
    print(f"Wrote {out_json}")
    return 0


def fetch_odds(args: argparse.Namespace) -> list[dict[str, Any]]:
    params: dict[str, str] = {
        "apiKey": args.api_key,
        "regions": args.regions,
        "markets": args.markets,
        "oddsFormat": args.odds_format,
        "dateFormat": args.date_format,
    }
    if args.commence_from:
        params["commenceTimeFrom"] = args.commence_from
    if args.commence_to:
        params["commenceTimeTo"] = args.commence_to
    url = f"{API_HOST}/v4/sports/{args.sport}/odds/?" + urllib.parse.urlencode(params)
    return request_json(url)


def flatten(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events:
        for bookmaker in event.get("bookmakers", []) or []:
            for market in bookmaker.get("markets", []) or []:
                for outcome in market.get("outcomes", []) or []:
                    rows.append(
                        {
                            "event_id": event.get("id", ""),
                            "sport_key": event.get("sport_key", ""),
                            "commence_time": event.get("commence_time", ""),
                            "home_team": event.get("home_team", ""),
                            "away_team": event.get("away_team", ""),
                            "bookmaker": bookmaker.get("key", ""),
                            "bookmaker_title": bookmaker.get("title", ""),
                            "last_update": bookmaker.get("last_update", ""),
                            "market": market.get("key", ""),
                            "outcome_name": outcome.get("name", ""),
                            "price": outcome.get("price", ""),
                            "point": outcome.get("point", ""),
                            "description": outcome.get("description", ""),
                        }
                    )
    return rows


def write_outputs(events: list[dict[str, Any]], out_json: Path, out_csv: Path) -> None:
    rows = flatten(events)
    out_json.write_text(json.dumps(events, indent=2), encoding="utf-8")
    with out_csv.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Fetched events: {len(events)}")
    print(f"Flattened market rows: {len(rows)}")
    print(f"Wrote {out_json}")
    print(f"Wrote {out_csv}")


def write_empty_outputs(out_json: Path, out_csv: Path) -> None:
    out_json.write_text("[]\n", encoding="utf-8")
    with out_csv.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
    print(f"Wrote empty fallback JSON: {out_json}")
    print(f"Wrote empty fallback CSV: {out_csv}")


def preserve_existing_outputs(out_json: Path, out_csv: Path) -> bool:
    if out_json.exists() and out_csv.exists():
        print(f"Using existing market odds outputs as stale fallback: {out_json}, {out_csv}")
        return True
    return False


def write_failure_report(path: Path, args: argparse.Namespace, error: str) -> None:
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "fetch_failed",
        "sport": args.sport,
        "regions": args.regions,
        "markets": args.markets,
        "error": error,
        "used_stale_fallback": bool(args.allow_stale_on_failure),
        "used_empty_fallback": bool(args.allow_empty_on_failure),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


def main() -> int:
    args = build_parser().parse_args()
    require_key(args.api_key)
    out_json = Path(args.out_json)
    out_csv = Path(args.out_csv)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    if args.list_soccer:
        return list_soccer(args.api_key, out_json)

    try:
        events = fetch_odds(args)
        if not isinstance(events, list):
            raise ValueError(f"Expected list response from The Odds API, got {type(events).__name__}")
        write_outputs(events, out_json, out_csv)
        return 0
    except urllib.error.HTTPError as exc:
        error = read_http_error(exc)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    write_failure_report(out_json.with_suffix(".error.json"), args, error)
    print(f"WARNING: The Odds API fetch failed: {error}")

    if args.allow_stale_on_failure and preserve_existing_outputs(out_json, out_csv):
        return 0
    if args.allow_empty_on_failure:
        write_empty_outputs(out_json, out_csv)
        return 0
    raise SystemExit(1)


if __name__ == "__main__":
    raise SystemExit(main())
