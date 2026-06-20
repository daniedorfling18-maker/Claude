from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pandas as pd


TEAM_BY_CODE = {
    "MEX": "Mexico", "RSA": "South Africa", "KOR": "South Korea", "CZE": "Czech Republic",
    "CAN": "Canada", "BIH": "Bosnia & Herzegovina", "BHI": "Bosnia & Herzegovina", "QAT": "Qatar", "SUI": "Switzerland",
    "BRA": "Brazil", "MAR": "Morocco", "MOR": "Morocco", "HTI": "Haiti", "HAI": "Haiti", "SCO": "Scotland",
    "USA": "United States", "PAR": "Paraguay", "AUS": "Australia", "TUR": "Turkey",
    "GER": "Germany", "CUW": "Curacao", "CUR": "Curacao", "CIV": "Ivory Coast", "ECU": "Ecuador",
    "NED": "Netherlands", "JPN": "Japan", "SWE": "Sweden", "TUN": "Tunisia",
    "ESP": "Spain", "CPV": "Cape Verde", "KSA": "Saudi Arabia", "URU": "Uruguay",
    "BEL": "Belgium", "EGY": "Egypt", "IRI": "Iran", "IRN": "Iran", "NZL": "New Zealand",
    "FRA": "France", "SEN": "Senegal", "IRQ": "Iraq", "NOR": "Norway",
    "ARG": "Argentina", "DZA": "Algeria", "ALG": "Algeria", "AUT": "Austria", "JOR": "Jordan",
    "POR": "Portugal", "COD": "DR Congo", "DRC": "DR Congo", "UZB": "Uzbekistan", "COL": "Colombia",
    "ENG": "England", "CRO": "Croatia", "GHA": "Ghana", "PAN": "Panama",
}

# Handles both "CZE 1 - 1 RSA" and table text like "CZE 1 1 RSA".
SCORE_RE = re.compile(r"\b([A-Z]{2,4})\s+(\d{1,2})\s*(?:-|–)?\s*(\d{1,2})\s+([A-Z]{2,4})\b")
ROUND_RE = re.compile(r"^Round\s+(\d+)\b", re.IGNORECASE)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill completed Superbru pool match results from a logged-in local Chrome CDP session."
    )
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    parser.add_argument("--pool-url", default="https://www.superbru.com/worldcup_predictor/pool.php?p=13236623&tab=matches#tab=matches")
    parser.add_argument("--out-csv", default="outputs/superbru_pool/superbru_match_results_auto.csv")
    parser.add_argument("--out-summary-json", default="outputs/superbru_pool/superbru_match_results_summary.json")
    parser.add_argument("--diagnostics-dir", default="outputs/superbru_pool/results_diagnostics")
    parser.add_argument("--settle-ms", type=int, default=7000)
    parser.add_argument("--timeout-ms", type=int, default=90000)
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


def match_key(home: Any, away: Any) -> str:
    return f"{slugify(home)}-{slugify(away)}"


EXTRACT_JS = r"""
() => {
  function clean(text) {
    return (text || '').replace(/\s+/g, ' ').trim();
  }
  function cssPath(el) {
    if (!el || !el.tagName) return '';
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 8) {
      let part = node.tagName.toLowerCase();
      if (node.id) {
        part += '#' + CSS.escape(node.id);
        parts.unshift(part);
        break;
      }
      const cls = Array.from(node.classList || []).slice(0, 4).map(c => '.' + CSS.escape(c)).join('');
      part += cls;
      const parent = node.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(x => x.tagName === node.tagName);
        if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
      }
      parts.unshift(part);
      node = parent;
    }
    return parts.join(' > ');
  }
  const tables = Array.from(document.querySelectorAll('table')).map((table, idx) => {
    const matrix = Array.from(table.querySelectorAll('tr')).map(row =>
      Array.from(row.querySelectorAll('th,td')).map(cell => clean(cell.innerText || cell.textContent))
    ).filter(row => row.length > 0);
    return {
      index: idx,
      id: table.id || '',
      className: table.className || '',
      text: clean(table.innerText).slice(0, 20000),
      matrix
    };
  });
  const clickables = Array.from(document.querySelectorAll('a,button,[role="button"],[onclick],.tab-control,.subtab-control,.game')).map((el, idx) => ({
    index: idx,
    text: clean(el.innerText || el.textContent),
    className: el.className || '',
    id: el.id || '',
    href: el.href || '',
    selector: cssPath(el)
  })).filter(x => x.text || x.href || x.id || x.className);
  const bodyText = document.body ? document.body.innerText.slice(0, 140000) : '';
  return {
    currentUrl: window.location.href,
    title: document.title || '',
    bodyText,
    tables,
    clickables,
    tableCount: tables.length,
    clickableCount: clickables.length,
    scrapedAtUtc: new Date().toISOString()
  };
}
"""


def parse_score_expr(value: str) -> dict[str, Any] | None:
    text = txt(value)
    m = SCORE_RE.search(text)
    if not m:
        return None
    home_code, home_goals, away_goals, away_code = m.groups()
    home_team = TEAM_BY_CODE.get(home_code, home_code)
    away_team = TEAM_BY_CODE.get(away_code, away_code)
    return {
        "home_code": home_code,
        "away_code": away_code,
        "home_team": home_team,
        "away_team": away_team,
        "home_goals": int(home_goals),
        "away_goals": int(away_goals),
        "actual_score": f"{int(home_goals)}-{int(away_goals)}",
        "match_id": match_key(home_team, away_team),
    }


def parse_clickable_results(state: dict[str, Any], round_label: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in state.get("clickables") or []:
        text = txt(item.get("text"))
        klass = txt(item.get("className"))
        # Completed match rows look like: "CZE 1 - 1 RSA 0 WRONG".
        # Upcoming rows look like: "NED v SWE - -" and should be ignored.
        parsed = parse_score_expr(text)
        if not parsed:
            continue
        low = text.lower()
        is_result_row = any(token in low for token in ["exact", "close", "result", "wrong"]) or "subtab-control" in klass
        if not is_result_row:
            continue
        parsed.update({
            "round_label": round_label,
            "source_url": state.get("currentUrl", ""),
            "status": "completed",
            "is_completed": True,
            "score_source": "superbru_matches_tab_result_row",
            "source_title": state.get("title", ""),
            "raw_text": text,
        })
        rows.append(parsed)
    return rows


def parse_table_detail_result(state: dict[str, Any], round_label: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for table in state.get("tables") or []:
        for row in table.get("matrix") or []:
            text = " ".join(txt(c) for c in row)
            parsed = parse_score_expr(text)
            if not parsed:
                continue
            parsed.update({
                "round_label": round_label,
                "source_url": state.get("currentUrl", ""),
                "status": "completed",
                "is_completed": True,
                "score_source": "superbru_match_detail_table",
                "source_title": state.get("title", ""),
                "raw_text": text,
            })
            rows.append(parsed)
    return rows


def round_controls(state: dict[str, Any]) -> list[dict[str, str]]:
    controls: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in state.get("clickables") or []:
        text = txt(item.get("text"))
        klass = txt(item.get("className"))
        selector = txt(item.get("selector"))
        if not selector:
            continue
        if not ROUND_RE.search(text):
            continue
        if "tab-control" not in klass:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        controls.append({"text": text, "selector": selector, "className": klass})
    return controls


async def load_state(page: Any, url: str, args: argparse.Namespace) -> dict[str, Any]:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=args.timeout_ms)
    except PlaywrightTimeoutError:
        print(f"Timeout loading {url}; checking current page state.")
    await page.wait_for_timeout(args.settle_ms)
    return await page.evaluate(EXTRACT_JS)


async def click_selector(page: Any, selector: str, args: argparse.Namespace) -> dict[str, Any] | None:
    try:
        loc = page.locator(selector).first
        await loc.scroll_into_view_if_needed(timeout=5000)
        await loc.click(timeout=5000)
        await page.wait_for_timeout(args.settle_ms)
        return await page.evaluate(EXTRACT_JS)
    except Exception as exc:
        print(f"Could not click selector {selector}: {exc}")
        return None


async def scrape(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        raise RuntimeError("Install with: pip install playwright && python -m playwright install chromium") from exc

    diagnostics_dir = Path(args.diagnostics_dir)
    diagnostics_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    clicked_rounds = 0

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(args.cdp_url)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()

        initial = await load_state(page, args.pool_url, args)
        (diagnostics_dir / "superbru_round_state_initial.json").write_text(json.dumps(initial, indent=2, default=str), encoding="utf-8")
        results.extend(parse_clickable_results(initial, "current"))
        results.extend(parse_table_detail_result(initial, "current"))

        controls = round_controls(initial)
        for idx, control in enumerate(controls, start=1):
            # Reload before each round click so selectors point at the same DOM shape.
            await load_state(page, args.pool_url, args)
            state = await click_selector(page, control["selector"], args)
            if not state:
                continue
            clicked_rounds += 1
            round_label = control["text"]
            safe_name = re.sub(r"[^A-Za-z0-9]+", "_", round_label).strip("_") or f"round_{idx}"
            (diagnostics_dir / f"superbru_round_state_{idx:02d}_{safe_name}.json").write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
            results.extend(parse_clickable_results(state, round_label))
            results.extend(parse_table_detail_result(state, round_label))

        await browser.close()

    by_match: dict[str, dict[str, Any]] = {}
    for row in results:
        mid = txt(row.get("match_id"))
        if not mid:
            continue
        existing = by_match.get(mid)
        # Prefer rows parsed from result rows rather than detail tables if both exist.
        if existing is None:
            by_match[mid] = row
        elif txt(row.get("score_source")) == "superbru_matches_tab_result_row" and txt(existing.get("score_source")) != "superbru_matches_tab_result_row":
            by_match[mid] = row
    final = list(by_match.values())
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "pool_url": args.pool_url,
        "round_controls_found": len(controls),
        "rounds_clicked": clicked_rounds,
        "raw_results_found": len(results),
        "deduped_results_found": len(final),
        "diagnostics_dir": str(diagnostics_dir),
        "note": "Extracts completed match result rows from each Superbru round tab, including Round 1.",
    }
    return final, summary


def main() -> int:
    args = build_parser().parse_args()
    out_csv = Path(args.out_csv)
    out_json = Path(args.out_summary_json)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)

    rows, summary = __import__("asyncio").run(scrape(args))
    columns = [
        "match_id", "round_label", "home_team", "away_team", "actual_score", "home_goals", "away_goals",
        "home_code", "away_code", "is_completed", "status", "score_source", "raw_text", "source_url", "source_title",
    ]
    out = pd.DataFrame(rows)
    for col in columns:
        if col not in out.columns:
            out[col] = ""
    out = out[columns].sort_values(["round_label", "match_id"]).reset_index(drop=True) if not out.empty else pd.DataFrame(columns=columns)
    out.to_csv(out_csv, index=False)
    summary["out_csv"] = str(out_csv)
    summary["out_summary_json"] = str(out_json)
    out_json.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
