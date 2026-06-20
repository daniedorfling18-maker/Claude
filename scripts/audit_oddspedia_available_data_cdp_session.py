from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


SCALAR_TYPES = (str, int, float, bool, type(None))
MARKET_KEY_HINTS = {
    "probabilities", "probability", "odds", "markets", "market", "bookmakers", "bookmaker",
    "outcomes", "selections", "prices", "correctscore", "correct_score", "handicap",
    "total", "over", "under", "btts", "score", "winner",
}
LABEL_KEYS = ["name", "label", "marketName", "market_name", "title", "description", "handicap_name", "type"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit Oddspedia page, app-state, market and network data exposed to the logged-in local Chrome CDP session."
    )
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    parser.add_argument("--urls-csv", default="inputs/oddspedia_match_urls.csv")
    parser.add_argument("--out-dir", default="outputs/data_inventory")
    parser.add_argument("--diagnostics-dir", default="outputs/data_inventory/raw_diagnostics/oddspedia")
    parser.add_argument("--settle-ms", type=int, default=8000)
    parser.add_argument("--timeout-ms", type=int, default=90000)
    parser.add_argument("--max-matches", type=int, default=0)
    parser.add_argument("--try-alt-urls", action="store_true", help="Also inspect alt_url when present.")
    parser.add_argument("--write-raw-state", action="store_true", help="Write trimmed raw app-state samples for manual inspection.")
    parser.add_argument("--max-raw-chars", type=int, default=120000)
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


def safe_name(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", txt(value)).strip("_")
    return text[:120] or "item"


def load_url_rows(path: Path, max_matches: int = 0) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing URL CSV: {path}")
    frame = pd.read_csv(path).fillna("")
    rows: list[dict[str, str]] = []
    for _, record in frame.iterrows():
        row = {col: txt(record.get(col)) for col in frame.columns}
        if not row.get("match_id"):
            row["match_id"] = f"{slugify(row.get('home_team'))}-{slugify(row.get('away_team'))}"
        if row.get("url") or row.get("alt_url"):
            rows.append(row)
    return rows[:max_matches] if max_matches and max_matches > 0 else rows


EXTRACT_APP_STATE_JS = r"""
() => {
  function serialise(value) {
    try { return JSON.parse(JSON.stringify(value)); } catch(e) { return null; }
  }
  function clean(text) { return (text || '').replace(/\s+/g, ' ').trim(); }
  const candidates = [];
  try { candidates.push(['nuxt_state', window.__NUXT__?.state || null]); } catch(e) {}
  try { candidates.push(['nuxt_event', window.__NUXT__?.event || null]); } catch(e) {}
  try { candidates.push(['store_state', window.$nuxt?.$store?.state || null]); } catch(e) {}
  const found = [];
  for (const [name, value] of candidates) {
    if (value) found.push({ sourceName: name, value: serialise(value) });
  }
  const metas = Array.from(document.querySelectorAll('meta')).map((m) => ({
    name: m.getAttribute('name') || m.getAttribute('property') || '',
    content: m.getAttribute('content') || ''
  })).filter(x => x.name || x.content).slice(0, 150);
  const scripts = Array.from(document.scripts || []).map((s, idx) => ({
    index: idx,
    type: s.type || '',
    src: s.src || '',
    textLength: (s.textContent || '').length,
    hasNuxt: (s.textContent || '').includes('__NUXT__'),
    hasProbability: /probab|market|odds/i.test(s.textContent || '')
  })).slice(0, 200);
  return {
    currentUrl: window.location.href,
    title: document.title || '',
    bodyTextLength: document.body ? document.body.innerText.length : 0,
    bodyTextSample: document.body ? clean(document.body.innerText).slice(0, 8000) : '',
    appStateCandidates: found,
    metaTags: metas,
    scripts,
    scrapedAtUtc: new Date().toISOString()
  };
}
"""


def short_sample(value: Any, limit: int = 180) -> str:
    if isinstance(value, SCALAR_TYPES):
        text = txt(value)
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            text = txt(type(value).__name__)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def walk_fields(value: Any, path: str = "root", max_nodes: int = 10000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen = 0

    def walk(item: Any, current_path: str) -> None:
        nonlocal seen
        if seen >= max_nodes:
            return
        seen += 1
        if isinstance(item, dict):
            keys = sorted(map(str, item.keys()))
            rows.append({
                "path": current_path,
                "value_type": "dict",
                "child_count": len(item),
                "keys": ";".join(keys[:40]),
                "sample": "",
            })
            for key, child in item.items():
                walk(child, f"{current_path}.{key}")
        elif isinstance(item, list):
            rows.append({
                "path": current_path,
                "value_type": "list",
                "child_count": len(item),
                "keys": "",
                "sample": short_sample(item[:3]),
            })
            for idx, child in enumerate(item[:100]):
                walk(child, f"{current_path}[{idx}]")
        else:
            rows.append({
                "path": current_path,
                "value_type": type(item).__name__,
                "child_count": 0,
                "keys": "",
                "sample": short_sample(item),
            })

    walk(value, path)
    return rows


def extract_label(obj: Any) -> str:
    if not isinstance(obj, dict):
        return ""
    for key in LABEL_KEYS:
        value = txt(obj.get(key))
        if value:
            return value
    return ""


def count_collection(value: Any) -> int:
    if isinstance(value, (list, dict)):
        return len(value)
    return 0


def looks_market_like(path: str, obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    keys = {str(k).lower() for k in obj.keys()}
    path_tokens = set(re.split(r"[^a-z0-9]+", path.lower()))
    return bool((keys | path_tokens) & MARKET_KEY_HINTS)


def walk_market_objects(value: Any, path: str = "root", max_nodes: int = 10000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen = 0

    def walk(item: Any, current_path: str) -> None:
        nonlocal seen
        if seen >= max_nodes:
            return
        seen += 1
        if isinstance(item, dict):
            if looks_market_like(current_path, item):
                keys = sorted(map(str, item.keys()))
                rows.append({
                    "market_path": current_path,
                    "market_label": extract_label(item),
                    "object_keys": ";".join(keys[:80]),
                    "probabilities_count": count_collection(item.get("probabilities")),
                    "odds_count": count_collection(item.get("odds")),
                    "markets_count": count_collection(item.get("markets")),
                    "bookmakers_count": count_collection(item.get("bookmakers") or item.get("bookmaker")),
                    "outcomes_count": count_collection(item.get("outcomes") or item.get("selections")),
                    "sample": short_sample(item, 260),
                })
            for key, child in item.items():
                walk(child, f"{current_path}.{key}")
        elif isinstance(item, list):
            for idx, child in enumerate(item[:120]):
                walk(child, f"{current_path}[{idx}]")

    walk(value, path)
    return rows


def trim_for_raw(obj: Any, max_chars: int) -> Any:
    try:
        text = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return {"error": "Could not serialise object"}
    if len(text) <= max_chars:
        return obj
    return {"truncated": True, "max_chars": max_chars, "json_prefix": text[:max_chars]}


async def audit(args: argparse.Namespace, rows: list[dict[str, str]]) -> dict[str, Any]:
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except Exception as exc:
        raise RuntimeError("Install with: pip install playwright && python -m playwright install chromium") from exc

    out_dir = Path(args.out_dir)
    diagnostics_dir = Path(args.diagnostics_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    diagnostics_dir.mkdir(parents=True, exist_ok=True)

    field_rows: list[dict[str, Any]] = []
    market_rows: list[dict[str, Any]] = []
    network_rows: list[dict[str, Any]] = []
    page_rows: list[dict[str, Any]] = []

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(args.cdp_url)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()

        def on_response(response: Any) -> None:
            try:
                headers = response.headers or {}
                url = response.url
                if not any(token in url.lower() for token in ["oddspedia", "api", "event", "odds", "probab", "market"]):
                    return
                network_rows.append({
                    "url": url,
                    "status": response.status,
                    "content_type": headers.get("content-type", ""),
                    "resource_type": response.request.resource_type,
                })
            except Exception:
                return

        page.on("response", on_response)

        for idx, row in enumerate(rows, start=1):
            candidates: list[tuple[str, str]] = []
            if row.get("url"):
                candidates.append(("url", row["url"]))
            if args.try_alt_urls and row.get("alt_url") and row.get("alt_url") != row.get("url"):
                candidates.append(("alt_url", row["alt_url"]))
            for url_type, url in candidates:
                print(f"[{idx}/{len(rows)}] Oddspedia inventory :: {row.get('home_team')} vs {row.get('away_team')} :: {url_type}")
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=args.timeout_ms)
                except PlaywrightTimeoutError:
                    print("WARNING: Timeout loading Oddspedia page. Checking current state.")
                await page.wait_for_timeout(args.settle_ms)
                state = await page.evaluate(EXTRACT_APP_STATE_JS)
                page_rows.append({
                    "match_id": row.get("match_id", ""),
                    "home_team": row.get("home_team", ""),
                    "away_team": row.get("away_team", ""),
                    "source_url_type": url_type,
                    "source_url": url,
                    "current_url": txt(state.get("currentUrl")),
                    "title": txt(state.get("title")),
                    "body_text_length": state.get("bodyTextLength", 0),
                    "app_state_candidate_count": len(state.get("appStateCandidates") or []),
                    "script_count": len(state.get("scripts") or []),
                    "meta_tag_count": len(state.get("metaTags") or []),
                })

                if args.write_raw_state:
                    raw_file = diagnostics_dir / f"{idx:03d}_{safe_name(row.get('match_id'))}_{url_type}_state.json"
                    raw_file.write_text(json.dumps(trim_for_raw(state, args.max_raw_chars), indent=2, default=str), encoding="utf-8")

                for candidate in state.get("appStateCandidates") or []:
                    source_name = txt(candidate.get("sourceName"))
                    value = candidate.get("value")
                    for field in walk_fields(value, f"{source_name}"):
                        field.update({
                            "match_id": row.get("match_id", ""),
                            "home_team": row.get("home_team", ""),
                            "away_team": row.get("away_team", ""),
                            "source_url_type": url_type,
                            "source_url": url,
                            "source_name": source_name,
                        })
                        field_rows.append(field)
                    for market in walk_market_objects(value, f"{source_name}"):
                        market.update({
                            "match_id": row.get("match_id", ""),
                            "home_team": row.get("home_team", ""),
                            "away_team": row.get("away_team", ""),
                            "source_url_type": url_type,
                            "source_url": url,
                            "source_name": source_name,
                        })
                        market_rows.append(market)

        await browser.close()

    fields_json = out_dir / "oddspedia_available_fields.json"
    markets_csv = out_dir / "oddspedia_available_markets.csv"
    network_csv = out_dir / "oddspedia_network_inventory.csv"
    pages_csv = out_dir / "oddspedia_page_inventory.csv"

    pd.DataFrame(market_rows).to_csv(markets_csv, index=False)
    pd.DataFrame(network_rows).drop_duplicates().to_csv(network_csv, index=False)
    pd.DataFrame(page_rows).to_csv(pages_csv, index=False)

    field_type_counts = Counter(txt(r.get("value_type")) for r in field_rows)
    field_path_counts = Counter(txt(r.get("path")).split(".")[0] for r in field_rows)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "oddspedia",
        "input_match_count": len(rows),
        "page_row_count": len(page_rows),
        "field_row_count": len(field_rows),
        "market_like_row_count": len(market_rows),
        "network_row_count": len(network_rows),
        "field_type_counts": dict(field_type_counts),
        "top_level_source_counts": dict(field_path_counts),
        "outputs": {
            "markets_csv": str(markets_csv),
            "network_csv": str(network_csv),
            "pages_csv": str(pages_csv),
        },
        "sample_field_rows": field_rows[:80],
    }
    fields_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload


def main() -> int:
    args = build_parser().parse_args()
    rows = load_url_rows(Path(args.urls_csv), args.max_matches)
    import asyncio
    payload = asyncio.run(audit(args, rows)) if rows else {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "oddspedia",
        "input_match_count": 0,
        "warning": f"No rows found in {args.urls_csv}",
    }
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
