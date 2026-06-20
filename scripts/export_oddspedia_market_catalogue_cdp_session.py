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
    parser = argparse.ArgumentParser(description="Export available Oddspedia Nuxt probability market keys for diagnostics only.")
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    parser.add_argument("--urls-csv", default="inputs/oddspedia_match_urls.csv")
    parser.add_argument("--out-csv", default="outputs/oddspedia_probability_extract/oddspedia_market_catalogue.csv")
    parser.add_argument("--out-summary-json", default="outputs/oddspedia_probability_extract/oddspedia_market_catalogue_summary.json")
    parser.add_argument("--settle-ms", type=int, default=8000)
    parser.add_argument("--timeout-ms", type=int, default=90000)
    parser.add_argument("--max-matches", type=int, default=0)
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def slugify(value: Any) -> str:
    text = txt(value).lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def load_rows(path: str | Path, max_matches: int = 0) -> list[dict[str, str]]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Missing URL CSV: {p}")
    frame = pd.read_csv(p).fillna("")
    rows: list[dict[str, str]] = []
    for _, r in frame.iterrows():
        row = {c: txt(r.get(c)) for c in frame.columns}
        if not row.get("match_id"):
            row["match_id"] = f"{slugify(row.get('home_team'))}-{slugify(row.get('away_team'))}"
        if row.get("url") or row.get("alt_url"):
            rows.append(row)
    return rows[:max_matches] if max_matches and max_matches > 0 else rows


EXTRACT_JS = r"""
() => {
  const candidates = [];
  try { candidates.push(['nuxt_state_event', window.__NUXT__?.state?.event]); } catch(e) {}
  try { candidates.push(['nuxt_event', window.__NUXT__?.event]); } catch(e) {}
  try { candidates.push(['store_state_event', window.$nuxt?.$store?.state?.event]); } catch(e) {}
  for (const [name, event] of candidates) {
    if (event && event.probabilities) {
      return { sourceName: name, probabilities: event.probabilities, currentUrl: window.location.href, title: document.title || '' };
    }
  }
  return { sourceName: '', probabilities: null, currentUrl: window.location.href, title: document.title || '' };
}
"""


def market_label(market: Any) -> str:
    if isinstance(market, dict):
        for key in ["name", "label", "marketName", "market_name", "title", "description", "handicap_name"]:
            val = txt(market.get(key))
            if val:
                return val
    return ""


def catalogue_rows(row: dict[str, str], state: dict[str, Any], url_type: str, url: str) -> list[dict[str, Any]]:
    probs = state.get("probabilities") or {}
    if not isinstance(probs, dict):
        return []
    out: list[dict[str, Any]] = []
    for market_id, market in probs.items():
        keys: list[str] = []
        has_probabilities = False
        has_odds = False
        if isinstance(market, dict):
            keys = sorted(map(str, market.keys()))
            has_probabilities = "probabilities" in market and bool(market.get("probabilities"))
            has_odds = "odds" in market and bool(market.get("odds"))
        elif isinstance(market, list):
            keys = ["list"]
            has_probabilities = bool(market)
        else:
            keys = [type(market).__name__]
        out.append({
            "match_id": row.get("match_id", ""),
            "commence_time": row.get("commence_time", ""),
            "home_team": row.get("home_team", ""),
            "away_team": row.get("away_team", ""),
            "market_id": txt(market_id),
            "market_object_keys": ";".join(keys),
            "market_label": market_label(market),
            "probabilities_present": has_probabilities,
            "odds_present": has_odds,
            "source_url_type": url_type,
            "source_url": url,
            "current_url": txt(state.get("currentUrl")),
            "source_name": txt(state.get("sourceName")),
        })
    return out


async def scrape(args: argparse.Namespace, rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except Exception as exc:
        raise RuntimeError("Install with: pip install playwright && python -m playwright install chromium") from exc
    output: list[dict[str, Any]] = []
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(args.cdp_url)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()
        for idx, row in enumerate(rows, start=1):
            candidates = []
            if row.get("url"):
                candidates.append(("url", row["url"]))
            if row.get("alt_url") and row.get("alt_url") != row.get("url"):
                candidates.append(("alt_url", row["alt_url"]))
            for url_type, url in candidates[:1]:
                print(f"[{idx}/{len(rows)}] market catalogue :: {row.get('home_team')} vs {row.get('away_team')}")
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=args.timeout_ms)
                except PlaywrightTimeoutError:
                    print("WARNING: Timeout loading Oddspedia page. Checking current state.")
                await page.wait_for_timeout(args.settle_ms)
                state = await page.evaluate(EXTRACT_JS)
                output.extend(catalogue_rows(row, state, url_type, url))
        await browser.close()
    return output


def main() -> int:
    args = build_parser().parse_args()
    rows = load_rows(args.urls_csv, args.max_matches)
    out_csv = Path(args.out_csv)
    out_json = Path(args.out_summary_json)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    import asyncio
    catalogue = asyncio.run(scrape(args, rows)) if rows else []
    out = pd.DataFrame(catalogue)
    out.to_csv(out_csv, index=False)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_match_count": len(rows),
        "catalogue_row_count": int(len(out)),
        "unique_market_count": int(out["market_id"].nunique()) if not out.empty and "market_id" in out.columns else 0,
        "out_csv": str(out_csv),
        "warning": "No market keys found. This is diagnostic only and does not affect picks." if out.empty else "",
    }
    out_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    if payload["warning"]:
        print(f"WARNING: {payload['warning']}")
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
