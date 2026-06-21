"""
discover_oddspedia_match_urls_curl.py

Fetches the Oddspedia World Cup listing page with curl_cffi (Cloudflare bypass),
evaluates window.__NUXT__ via Node.js, walks the state tree to find match entries,
then aligns them to the locked-picks fixture list and writes inputs/oddspedia_match_urls.csv.

Replaces the Playwright-based build_oddspedia_match_urls_from_nuxt.py for environments
where a real browser is not available.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    cffi_requests = None  # type: ignore[assignment]

SEED_URL = "https://oddspedia.com/football/world/world-cup"

# Node.js script: walks the full __NUXT__ state recursively, collects any object
# that looks like a match entry (has match_key, ht/at slugs and names).
_NODE_WALK = r"""
const window = {};
try { %s } catch(e) {}
const nuxt = window.__NUXT__ || null;

function walk(obj, depth) {
  if (!obj || typeof obj !== 'object' || depth > 20) return [];
  const results = [];
  if (
    obj.match_key != null &&
    (obj.ht_slug_en || obj.ht_slug) &&
    (obj.at_slug_en || obj.at_slug) &&
    (obj.ht || obj.home_team) &&
    (obj.at || obj.away_team)
  ) {
    results.push(obj);
  }
  for (const val of Object.values(obj)) {
    if (Array.isArray(val)) {
      for (const item of val) results.push(...walk(item, depth + 1));
    } else if (val && typeof val === 'object') {
      results.push(...walk(val, depth + 1));
    }
  }
  return results;
}

const matches = nuxt ? walk(nuxt, 0) : [];
console.log(JSON.stringify(matches));
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Discover Oddspedia match URLs for upcoming WC fixtures via curl_cffi."
    )
    parser.add_argument("--seed-url", default=SEED_URL)
    parser.add_argument("--fixtures-csv", default="outputs/final_locked_picks/superbru_final_card.csv")
    parser.add_argument("--out-csv", default="inputs/oddspedia_match_urls.csv")
    parser.add_argument("--out-json", default="outputs/oddspedia_url_discovery/oddspedia_url_discovery.json")
    parser.add_argument("--impersonate", default="chrome124")
    parser.add_argument("--min-match-score", type=int, default=80)
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


def fetch_nuxt_js(url: str, impersonate: str) -> str:
    if cffi_requests is None:
        raise RuntimeError("curl_cffi not installed. Run: pip install curl_cffi")
    session = cffi_requests.Session(impersonate=impersonate)
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    html = resp.text
    m = re.search(r'(window\.__NUXT__\s*=\s*(?:__NUXT_DATA__|__NUXT__\(|[\(])\s*[\s\S]*?(?:;|\}\);|\)\);))\s*(?:</script>|$)', html)
    if not m:
        m = re.search(r'(window\.__NUXT__\s*=\s*[\s\S]+?)</script>', html)
    return m.group(1) if m else ""


def eval_nuxt(nuxt_js: str) -> list[dict[str, Any]]:
    if not nuxt_js.strip():
        return []
    code = _NODE_WALK % nuxt_js
    with tempfile.NamedTemporaryFile(suffix=".js", mode="w", delete=False, encoding="utf-8") as f:
        f.write(code)
        tmp = f.name
    try:
        result = subprocess.run(
            ["node", tmp], capture_output=True, text=True, timeout=30, encoding="utf-8"
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []
        return json.loads(result.stdout.strip())
    except Exception:
        return []
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def dedupe_events(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for d in raw:
        key = str(d.get("match_key", ""))
        if not key or key in seen:
            continue
        ht_slug = txt(d.get("ht_slug_en") or d.get("ht_slug"))
        at_slug = txt(d.get("at_slug_en") or d.get("at_slug"))
        ht = txt(d.get("ht") or d.get("home_team"))
        at = txt(d.get("at") or d.get("away_team"))
        if not (key and ht_slug and at_slug and ht and at):
            continue
        seen.add(key)
        out.append({
            "match_key": key,
            "home_team": ht,
            "away_team": at,
            "home_slug": ht_slug,
            "away_slug": at_slug,
            "commence_time": txt(d.get("md") or d.get("commence_time")),
            "url": f"https://oddspedia.com/football/{ht_slug}-{at_slug}-{key}",
            "alt_url": f"https://oddspedia.com/football/{at_slug}-{ht_slug}-{key}",
        })
    return sorted(out, key=lambda e: (e.get("commence_time") or "", e.get("home_team") or ""))


def match_score(event: dict[str, Any], fixture: dict[str, Any]) -> int:
    eh, ea = norm(event.get("home_team")), norm(event.get("away_team"))
    fh, fa = norm(fixture.get("home_team")), norm(fixture.get("away_team"))
    if eh == fh and ea == fa:
        return 100
    if eh == fa and ea == fh:
        return 90
    if {eh, ea} == {fh, fa}:
        return 80
    if eh in {fh, fa} or ea in {fh, fa}:
        return 25
    return 0


def align_to_fixtures(
    events: list[dict[str, Any]], fixtures: pd.DataFrame, min_score: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
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
    pool = list(events)
    for _, fix_row in fixtures.iterrows():
        fix = fix_row.to_dict()
        best_event: dict[str, Any] | None = None
        best = 0
        for ev in pool:
            s = match_score(ev, fix)
            if s > best:
                best = s
                best_event = ev
        if best_event and best >= min_score:
            mid = txt(fix.get("match_id")) or f"{norm(fix.get('home_team')).replace(' ', '-')}-{norm(fix.get('away_team')).replace(' ', '-')}"
            matched.append({
                "match_id": mid,
                "commence_time": txt(fix.get("commence_time") or best_event.get("commence_time")),
                "home_team": txt(fix.get("home_team") or best_event.get("home_team")),
                "away_team": txt(fix.get("away_team") or best_event.get("away_team")),
                "url": best_event["url"],
                "alt_url": best_event["alt_url"],
                "match_key": best_event["match_key"],
                "match_score": best,
            })
        else:
            unmatched.append({
                "home_team": txt(fix.get("home_team")),
                "away_team": txt(fix.get("away_team")),
                "commence_time": txt(fix.get("commence_time")),
                "best_score": best,
            })
    return pd.DataFrame(matched), pd.DataFrame(unmatched)


def main() -> int:
    args = build_parser().parse_args()

    print(f"Fetching Oddspedia listing page: {args.seed_url}")
    nuxt_js = fetch_nuxt_js(args.seed_url, args.impersonate)
    if not nuxt_js:
        print("WARNING: Could not extract window.__NUXT__ from listing page. URL list not updated.")
        return 1

    print("Evaluating __NUXT__ state via Node.js...")
    raw = eval_nuxt(nuxt_js)
    events = dedupe_events(raw)
    print(f"Found {len(events)} unique match entries in Nuxt state.")

    fixtures_path = Path(args.fixtures_csv)
    fixtures = pd.read_csv(fixtures_path).fillna("") if fixtures_path.exists() else pd.DataFrame()

    matched, unmatched = align_to_fixtures(events, fixtures, args.min_match_score)

    # Build the full URL list: matched fixtures first (with locked pick alignment),
    # then any Oddspedia events not yet in our picks (upcoming new fixtures).
    matched_keys = set(txt(r.get("match_key")) for _, r in matched.iterrows()) if not matched.empty else set()
    new_event_rows = [
        {
            "match_id": e["match_key"],
            "commence_time": e["commence_time"],
            "home_team": e["home_team"],
            "away_team": e["away_team"],
            "url": e["url"],
            "alt_url": e["alt_url"],
            "match_key": e["match_key"],
            "match_score": 0,  # not yet in locked picks
        }
        for e in events
        if e["match_key"] not in matched_keys
    ]
    new_events_df = pd.DataFrame(new_event_rows)
    full_url_list = pd.concat([matched, new_events_df], ignore_index=True, sort=False).fillna("")

    out_csv = Path(args.out_csv)
    out_json = Path(args.out_json)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)

    full_url_list.to_csv(out_csv, index=False)

    unmatched_csv = out_json.with_name("oddspedia_url_unmatched.csv")
    unmatched.to_csv(unmatched_csv, index=False)

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "seed_url": args.seed_url,
        "events_found": len(events),
        "fixtures_count": int(len(fixtures)),
        "matched_to_locked_picks": int(len(matched)),
        "new_events_not_yet_in_picks": len(new_event_rows),
        "total_urls_written": int(len(full_url_list)),
        "fixtures_not_on_oddspedia": int(len(unmatched)),
        "out_csv": str(out_csv),
        "unmatched_csv": str(unmatched_csv),
    }
    out_json.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    if not unmatched.empty:
        print(f"NOTE: {len(unmatched)} locked fixture(s) not yet listed on Oddspedia (completed or future).")
    if new_event_rows:
        print(f"NEW: {len(new_event_rows)} Oddspedia event(s) added to URL list — not yet in locked picks:")
        for r in new_event_rows:
            print(f"  {r['home_team']} vs {r['away_team']}  ({r['commence_time']})")

    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
