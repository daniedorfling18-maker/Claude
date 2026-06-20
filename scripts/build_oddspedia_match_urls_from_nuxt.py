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
    parser = argparse.ArgumentParser(description="Build Oddspedia match URLs from the public window.__NUXT__ page state.")
    parser.add_argument("--seed-url", default="https://oddspedia.com/football/world/world-cup")
    parser.add_argument("--fixtures-csv", default="outputs/final_locked_picks/superbru_final_card.csv")
    parser.add_argument("--out-csv", default="inputs/oddspedia_match_urls.csv")
    parser.add_argument("--out-json", default="outputs/oddspedia_url_discovery/oddspedia_nuxt_matches.json")
    parser.add_argument("--headful", action="store_true")
    parser.add_argument("--timeout-ms", type=int, default=45000)
    parser.add_argument("--settle-ms", type=int, default=10000)
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def norm(value: Any) -> str:
    text = txt(value).lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def match_id_from_row(row: dict[str, Any]) -> str:
    explicit = txt(row.get("match_id"))
    if explicit:
        return explicit
    return f"{norm(row.get('home_team')).replace(' ', '-')}-{norm(row.get('away_team')).replace(' ', '-')}"


def walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def event_from_dict(d: dict[str, Any]) -> dict[str, Any] | None:
    match_key = txt(d.get("match_key"))
    ht_slug = txt(d.get("ht_slug_en") or d.get("ht_slug"))
    at_slug = txt(d.get("at_slug_en") or d.get("at_slug"))
    ht = txt(d.get("ht"))
    at = txt(d.get("at"))
    if not (match_key and ht_slug and at_slug and ht and at):
        return None
    return {
        "match_key": match_key,
        "home_team": ht,
        "away_team": at,
        "home_slug": ht_slug,
        "away_slug": at_slug,
        "commence_time": txt(d.get("md")),
        "url": f"https://oddspedia.com/football/{ht_slug}-{at_slug}-{match_key}",
        "alt_url": f"https://oddspedia.com/football/{at_slug}-{ht_slug}-{match_key}",
        "league_round_name": txt(d.get("league_round_name")),
        "group_name": txt(d.get("group_name")),
    }


def unique_events(nuxt: Any) -> list[dict[str, Any]]:
    seen: set[str] = set()
    events: list[dict[str, Any]] = []
    for d in walk(nuxt):
        event = event_from_dict(d)
        if event is None:
            continue
        key = event["match_key"]
        if key in seen:
            continue
        seen.add(key)
        events.append(event)
    return sorted(events, key=lambda e: (e.get("commence_time") or "", e.get("home_team") or ""))


def load_fixtures(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path).fillna("")


def match_score(event: dict[str, Any], fixture: dict[str, Any]) -> int:
    event_home = norm(event.get("home_team"))
    event_away = norm(event.get("away_team"))
    fixture_home = norm(fixture.get("home_team"))
    fixture_away = norm(fixture.get("away_team"))
    if event_home == fixture_home and event_away == fixture_away:
        return 100
    if event_home == fixture_away and event_away == fixture_home:
        return 90
    if {event_home, event_away} == {fixture_home, fixture_away}:
        return 80
    if event_home in {fixture_home, fixture_away} or event_away in {fixture_home, fixture_away}:
        return 25
    return 0


def align_to_fixtures(events: list[dict[str, Any]], fixtures: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if fixtures.empty:
        rows = [
            {
                "match_id": e["match_key"],
                "commence_time": e["commence_time"],
                "home_team": e["home_team"],
                "away_team": e["away_team"],
                "url": e["url"],
                "alt_url": e["alt_url"],
                "match_key": e["match_key"],
                "match_score": 100,
            }
            for e in events
        ]
        return pd.DataFrame(rows), pd.DataFrame()

    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    event_pool = list(events)
    for _, fixture_row in fixtures.iterrows():
        fixture = fixture_row.to_dict()
        best_event: dict[str, Any] | None = None
        best_score = 0
        for event in event_pool:
            score = match_score(event, fixture)
            if score > best_score:
                best_score = score
                best_event = event
        if best_event and best_score >= 80:
            matched.append(
                {
                    "match_id": match_id_from_row(fixture),
                    "commence_time": txt(fixture.get("commence_time") or best_event.get("commence_time")),
                    "home_team": txt(fixture.get("home_team") or best_event.get("home_team")),
                    "away_team": txt(fixture.get("away_team") or best_event.get("away_team")),
                    "url": best_event["url"],
                    "alt_url": best_event["alt_url"],
                    "match_key": best_event["match_key"],
                    "match_score": best_score,
                }
            )
        else:
            unmatched.append(
                {
                    "commence_time": txt(fixture.get("commence_time")),
                    "home_team": txt(fixture.get("home_team")),
                    "away_team": txt(fixture.get("away_team")),
                    "best_score": best_score,
                }
            )
    return pd.DataFrame(matched), pd.DataFrame(unmatched)


async def fetch_nuxt(seed_url: str, headful: bool, timeout_ms: int, settle_ms: int) -> Any:
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        raise RuntimeError("Install Playwright with: pip install playwright && python -m playwright install chromium") from exc
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not headful)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122 Safari/537.36")
        page = await context.new_page()
        print(f"Opening seed page: {seed_url}")
        await page.goto(seed_url, wait_until="domcontentloaded", timeout=timeout_ms)
        await page.wait_for_timeout(settle_ms)
        nuxt = await page.evaluate("() => window.__NUXT__ || null")
        await context.close()
        await browser.close()
    if nuxt is None:
        raise RuntimeError("window.__NUXT__ was not found on the seed page.")
    return nuxt


def main() -> int:
    args = build_parser().parse_args()
    import asyncio

    nuxt = asyncio.run(fetch_nuxt(args.seed_url, args.headful, args.timeout_ms, args.settle_ms))
    events = unique_events(nuxt)
    fixtures = load_fixtures(Path(args.fixtures_csv))
    matched, unmatched = align_to_fixtures(events, fixtures)

    out_csv = Path(args.out_csv)
    out_json = Path(args.out_json)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    matched.to_csv(out_csv, index=False)
    unmatched_path = out_json.with_name("oddspedia_nuxt_unmatched.csv")
    unmatched.to_csv(unmatched_path, index=False)
    out_json.write_text(json.dumps({"events": events}, indent=2), encoding="utf-8")

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "seed_url": args.seed_url,
        "events_found": len(events),
        "fixtures_count": int(len(fixtures)),
        "matched_count": int(len(matched)),
        "unmatched_count": int(len(unmatched)),
        "out_csv": str(out_csv),
        "unmatched_csv": str(unmatched_path),
        "out_json": str(out_json),
    }
    print(json.dumps(summary, indent=2))
    print(f"Wrote {out_csv}")
    print(f"Wrote {unmatched_path}")
    print(f"Wrote {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
