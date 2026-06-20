from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

SCORE_RE = re.compile(r"(?<!\d)(\d{1,2})\s*[-:–]\s*(\d{1,2})(?!\d)")
STATUS_RE = re.compile(r"\b(Exact|Close|Result|Wrong)\b", re.IGNORECASE)
POINTS_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:pts?|points?)\b", re.IGNORECASE)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scrape visible Superbru pool picks from an existing logged-in Chrome CDP session.")
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    parser.add_argument("--pool-url", default="https://www.superbru.com/worldcup_predictor/pool_view.php?t=1296&p=13236623&g=32&view=matches")
    parser.add_argument("--fixtures-csv", default="outputs/final_locked_picks/superbru_final_card.csv")
    parser.add_argument("--out-csv", default="outputs/superbru_pool/superbru_pool_picks_auto.csv")
    parser.add_argument("--out-summary-json", default="outputs/superbru_pool/superbru_pool_picks_summary.json")
    parser.add_argument("--diagnostics-dir", default="outputs/superbru_pool/pick_diagnostics")
    parser.add_argument("--settle-ms", type=int, default=12000)
    parser.add_argument("--timeout-ms", type=int, default=90000)
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def flat(value: Any) -> str:
    return re.sub(r"\s+", " ", txt(value))


def slugify(value: Any) -> str:
    text = txt(value).lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def match_key(home: Any, away: Any) -> str:
    return f"{slugify(home)}-{slugify(away)}"


def outcome(h: int | None, a: int | None) -> str:
    if h is None or a is None:
        return "unknown"
    if h > a:
        return "home"
    if h < a:
        return "away"
    return "draw"


EXTRACT_JS = r"""
() => {
  function clean(text) { return (text || '').replace(/\s+/g, ' ').trim(); }
  function visible(el) {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style && style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  }
  const tables = Array.from(document.querySelectorAll('table')).filter(visible).map((table, idx) => {
    const rows = Array.from(table.querySelectorAll('tr')).filter(visible).map((row) => {
      const cells = Array.from(row.querySelectorAll('th,td')).filter(visible).map((cell) => clean(cell.innerText || cell.textContent));
      return { cells, text: clean(row.innerText || row.textContent) };
    }).filter(r => r.cells.length || r.text);
    return { index: idx, rows };
  });
  const bodyText = document.body ? document.body.innerText.slice(0, 100000) : '';
  const bodyLines = bodyText.split('\n').map(clean).filter(line => line.length >= 3 && line.length <= 800);
  return {
    currentUrl: window.location.href,
    title: document.title || '',
    scrapedAtUtc: new Date().toISOString(),
    bodyText,
    tables,
    bodyLines
  };
}
"""


def load_fixtures(path: str | Path) -> list[dict[str, str]]:
    p = Path(path)
    if not p.exists():
        return []
    try:
        frame = pd.read_csv(p).fillna("")
    except Exception:
        return []
    rows: list[dict[str, str]] = []
    for _, r in frame.iterrows():
        home = txt(r.get("home_team"))
        away = txt(r.get("away_team"))
        if home and away:
            rows.append({
                "match_id": txt(r.get("match_id")) or match_key(home, away),
                "round_label": txt(r.get("round_label")),
                "home_team": home,
                "away_team": away,
                "commence_time": txt(r.get("commence_time")),
            })
    return rows


def infer_fixture(raw: str, fixtures: list[dict[str, str]]) -> dict[str, str]:
    low = raw.lower()
    for fixture in fixtures:
        if fixture["home_team"].lower() in low and fixture["away_team"].lower() in low:
            return fixture
    return {"match_id": "", "round_label": "", "home_team": "", "away_team": "", "commence_time": ""}


def extract_player(cells: list[str], raw: str, score_token: str, fixture: dict[str, str]) -> str:
    blocked = {score_token, fixture.get("home_team", ""), fixture.get("away_team", "")}
    for cell in cells:
        c = flat(cell)
        if not c or c in blocked or SCORE_RE.search(c) or STATUS_RE.search(c) or POINTS_RE.search(c):
            continue
        if re.search(r"[A-Za-z]", c) and len(c) <= 80:
            return c
    before_score = raw.split(score_token, 1)[0].strip()
    pieces = [p.strip(" -:|•\t") for p in re.split(r"\s{2,}|\||•", before_score) if p.strip()]
    for piece in reversed(pieces):
        if re.search(r"[A-Za-z]", piece) and len(piece) <= 80:
            return piece
    return ""


def candidate_rows(state: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for table in state.get("tables", []):
        for row in table.get("rows", []):
            out.append({"text": row.get("text"), "cells": row.get("cells", [])})
    for line in state.get("bodyLines", []):
        out.append({"text": line, "cells": []})
    return out


def parse_picks(candidates: list[dict[str, Any]], fixtures: list[dict[str, str]], source_url: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in candidates:
        raw = flat(item.get("text"))
        score_match = SCORE_RE.search(raw)
        if not raw or not score_match:
            continue
        h = int(score_match.group(1))
        a = int(score_match.group(2))
        score = f"{h}-{a}"
        fixture = infer_fixture(raw, fixtures)
        cells = [flat(c) for c in item.get("cells", []) if flat(c)]
        player = extract_player(cells, raw, score_match.group(0), fixture)
        if not player:
            continue
        status_match = STATUS_RE.search(raw)
        points_match = POINTS_RE.search(raw)
        key = (fixture.get("match_id", ""), player, score, raw[:120])
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "match_id": fixture.get("match_id", ""),
            "round_label": fixture.get("round_label", ""),
            "home_team": fixture.get("home_team", ""),
            "away_team": fixture.get("away_team", ""),
            "player": player,
            "picked_home_goals": h,
            "picked_away_goals": a,
            "picked_scoreline": score,
            "picked_outcome": outcome(h, a),
            "points_earned": points_match.group(1) if points_match else "",
            "result_status": status_match.group(1).title() if status_match else "",
            "raw_text": raw,
            "source_url": source_url,
        })
    return pd.DataFrame(rows)


async def scrape(args: argparse.Namespace) -> dict[str, Any]:
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except Exception as exc:
        raise RuntimeError("Install with: pip install playwright && python -m playwright install chromium") from exc
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(args.cdp_url)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(args.pool_url, wait_until="domcontentloaded", timeout=args.timeout_ms)
        except PlaywrightTimeoutError:
            print("WARNING: Timeout loading Superbru pool page. Checking current page state.")
        await page.wait_for_timeout(args.settle_ms)
        state = await page.evaluate(EXTRACT_JS)
        await browser.close()
        return state


def main() -> int:
    args = build_parser().parse_args()
    out_csv = Path(args.out_csv)
    out_summary = Path(args.out_summary_json)
    diagnostics_dir = Path(args.diagnostics_dir)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_summary.parent.mkdir(parents=True, exist_ok=True)
    diagnostics_dir.mkdir(parents=True, exist_ok=True)

    import asyncio
    state = asyncio.run(scrape(args))
    fixtures = load_fixtures(args.fixtures_csv)
    candidates = candidate_rows(state)
    picks = parse_picks(candidates, fixtures, txt(state.get("currentUrl")))
    picks.to_csv(out_csv, index=False)

    (diagnostics_dir / "superbru_pool_picks_capture.json").write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    (diagnostics_dir / "superbru_pool_picks_body_text.txt").write_text(txt(state.get("bodyText")), encoding="utf-8")
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "pool_url": args.pool_url,
        "current_url": state.get("currentUrl"),
        "title": state.get("title"),
        "visible_table_count": len(state.get("tables", [])),
        "candidate_row_count": len(candidates),
        "pick_row_count": int(len(picks)),
        "fixture_count": len(fixtures),
        "out_csv": str(out_csv),
        "diagnostics_dir": str(diagnostics_dir),
        "warning": "No visible pool pick rows found. This is expected if picks are hidden before lock or the page does not expose pool picks." if picks.empty else "",
    }
    out_summary.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    if payload["warning"]:
        print(f"WARNING: {payload['warning']}")
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
