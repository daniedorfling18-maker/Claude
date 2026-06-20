from __future__ import annotations

import argparse
import csv
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

API_HOST = "https://api.the-odds-api.com"


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
    return parser


def request_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "superbru-market-validator/1.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def require_key(api_key: str) -> None:
    if not api_key or api_key == "YOUR_THE_ODDS_API_KEY":
        raise SystemExit("Set THE_ODDS_API_KEY first, or pass --api-key.")


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


def main() -> int:
    args = build_parser().parse_args()
    require_key(args.api_key)
    out_json = Path(args.out_json)
    out_csv = Path(args.out_csv)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    if args.list_soccer:
        return list_soccer(args.api_key, out_json)

    events = fetch_odds(args)
    rows = flatten(events)
    out_json.write_text(json.dumps(events, indent=2), encoding="utf-8")

    fields = [
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
    with out_csv.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Fetched events: {len(events)}")
    print(f"Flattened market rows: {len(rows)}")
    print(f"Wrote {out_json}")
    print(f"Wrote {out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
