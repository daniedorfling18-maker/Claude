from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, parse_qs

import pandas as pd


TEAM_BY_CODE = {
    "MEX": "Mexico", "RSA": "South Africa", "KOR": "South Korea", "CZE": "Czech Republic",
    "CAN": "Canada", "BIH": "Bosnia & Herzegovina", "QAT": "Qatar", "SUI": "Switzerland",
    "BRA": "Brazil", "MAR": "Morocco", "HTI": "Haiti", "SCO": "Scotland",
    "USA": "United States", "PAR": "Paraguay", "AUS": "Australia", "TUR": "Turkey",
    "GER": "Germany", "CUW": "Curacao", "CIV": "Ivory Coast", "ECU": "Ecuador",
    "NED": "Netherlands", "JPN": "Japan", "SWE": "Sweden", "TUN": "Tunisia",
    "ESP": "Spain", "CPV": "Cape Verde", "KSA": "Saudi Arabia", "URU": "Uruguay",
    "BEL": "Belgium", "EGY": "Egypt", "IRI": "Iran", "NZL": "New Zealand",
    "FRA": "France", "SEN": "Senegal", "IRQ": "Iraq", "NOR": "Norway",
    "ARG": "Argentina", "DZA": "Algeria", "AUT": "Austria", "JOR": "Jordan",
    "POR": "Portugal", "COD": "DR Congo", "UZB": "Uzbekistan", "COL": "Colombia",
    "ENG": "England", "CRO": "Croatia", "GHA": "Ghana", "PAN": "Panama",
}

SCORE_RE = re.compile(r"\b([A-Z]{2,4})\s+(\d{1,2})\s+(\d{1,2})\s+([A-Z]{2,4})\b")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill completed Superbru pool match results from a logged-in local Chrome CDP session."
    )
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    parser.add_argument("--pool-url", default="https://www.superbru.com/worldcup_predictor/pool.php?p=13236623&tab=matches#tab=matches")
    parser.add_argument("--out-csv", default="outputs/superbru_pool/superbru_match_results_auto.csv")
    parser.add_argument("--out-summary-json", default="outputs/superbru_pool/superbru_match_results_summary.json")
    parser.add_argument("--diagnostics-dir", default="outputs/superbru_pool/results_diagnostics")
    parser.add_argument("--settle-ms", type=int, default=9000)
    parser.add_argument("--timeout-ms", type=int, default=90000)
    parser.add_argument("--max-pages", type=int, default=160)
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


def url_key(url: str) -> str:
    # Superbru often uses hash fragments or broad client-side state for the Matches tab.
    # Keep the fragment so separate hash-driven match views are not collapsed into one URL.
    return urlparse(url).geturl()


EXTRACT_JS = r"""
() => {
  function clean(text) {
    return (text || '').replace(/\s+/g, ' ').trim();
  }
  function cssPath(el) {
    if (!el || !el.tagName) return '';
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 6) {
      let part = node.tagName.toLowerCase();
      if (node.id) {
        part += '#' + CSS.escape(node.id);
        parts.unshift(part);
        break;
      }
      const cls = Array.from(node.classList || []).slice(0, 3).map(c => '.' + CSS.escape(c)).join('');
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
  const links = Array.from(document.querySelectorAll('a[href]')).map((a, idx) => ({
    index: idx,
    href: a.href,
    text: clean(a.innerText || a.textContent),
    className: a.className || '',
    id: a.id || ''
  }));
  const clickables = Array.from(document.querySelectorAll('a,button,[role="button"],[onclick],.match,.fixture,.game,.swiper-slide,.roundmatch')).map((el, idx) => ({
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
    links,
    clickables,
    tableCount: tables.length,
    linkCount: links.length,
    clickableCount: clickables.length,
    scrapedAtUtc: new Date().toISOString()
  };
}
"""


def parse_score_expr(value: str) -> dict[str, Any] | None:
    m = SCORE_RE.search(txt(value))
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


def extract_from_exact_section(tables: list[dict[str, Any]]) -> dict[str, Any] | None:
    for table in tables:
        rows = table.get("matrix") or []
        in_exact = False
        for row in rows:
            cells = [txt(c) for c in row]
            first = cells[0].lower() if cells else ""
            if first.startswith("exact") or "exact:" in first:
                in_exact = True
                continue
            if in_exact and (first.startswith("close") or first.startswith("result") or first.startswith("wrong")):
                break
            if in_exact:
                joined = " ".join(cells)
                parsed = parse_score_expr(joined)
                if parsed:
                    parsed["score_source"] = "superbru_exact_section"
                    return parsed
    return None


def extract_any_score_expr(tables: list[dict[str, Any]]) -> dict[str, Any] | None:
    for table in tables:
        for row in table.get("matrix") or []:
            joined = " ".join(txt(c) for c in row)
            parsed = parse_score_expr(joined)
            if parsed:
                parsed["score_source"] = "superbru_table_score_expression"
                return parsed
    return None


def extract_match_result(state: dict[str, Any]) -> dict[str, Any] | None:
    tables = state.get("tables") or []
    parsed = extract_from_exact_section(tables) or extract_any_score_expr(tables)
    if not parsed:
        return None
    parsed.update({
        "source_url": state.get("currentUrl", ""),
        "status": "completed",
        "is_completed": True,
        "source_title": state.get("title", ""),
    })
    return parsed


def pool_id_from_url(url: str) -> str:
    try:
        return (parse_qs(urlparse(url).query).get("p") or [""])[0]
    except Exception:
        return ""


def candidate_links(state: dict[str, Any], base_url: str, pool_id: str) -> list[str]:
    candidates: list[str] = []
    for item in state.get("links") or []:
        href = txt(item.get("href"))
        text = txt(item.get("text"))
        if not href:
            continue
        url = urljoin(base_url, href)
        parsed = urlparse(url)
        if "superbru.com" not in parsed.netloc:
            continue
        if "worldcup_predictor" not in parsed.path:
            continue
        q = parse_qs(parsed.query)
        if pool_id and (q.get("p") or [""])[0] != pool_id:
            continue
        low = f"{url} {text}".lower()
        if any(skip in low for skip in ["tab=chat", "tab=invite", "tab=players", "tab=rules", "tab=news", "tab=leaderboard"]):
            continue
        if "tab=matches" in low or any(token in low for token in ["match", "round", "fixture", "game", "result"]):
            candidates.append(url)
    seen: set[str] = set()
    out: list[str] = []
    for url in candidates:
        key = url_key(url)
        if key in seen:
            continue
        seen.add(key)
        out.append(url)
    return out


def clickable_score(text: str) -> int:
    low = text.lower()
    score = 0
    if SCORE_RE.search(text):
        score += 5
    if any(code.lower() in low for code in TEAM_BY_CODE):
        score += 1
    if any(token in low for token in ["exact", "close", "wrong", "result"]):
        score += 1
    return score


async def load_state(page: Any, url: str, args: argparse.Namespace) -> dict[str, Any]:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=args.timeout_ms)
    except PlaywrightTimeoutError:
        print(f"Timeout loading {url}; checking current page state.")
    await page.wait_for_timeout(args.settle_ms)
    return await page.evaluate(EXTRACT_JS)


async def click_candidate(page: Any, selector: str, args: argparse.Namespace) -> dict[str, Any] | None:
    try:
        loc = page.locator(selector).first
        await loc.scroll_into_view_if_needed(timeout=5000)
        await loc.click(timeout=5000)
        await page.wait_for_timeout(args.settle_ms)
        return await page.evaluate(EXTRACT_JS)
    except Exception:
        return None


async def scrape(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        raise RuntimeError("Install with: pip install playwright && python -m playwright install chromium") from exc

    diagnostics_dir = Path(args.diagnostics_dir)
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    pool_id = pool_id_from_url(args.pool_url)

    results: list[dict[str, Any]] = []
    visited: set[str] = set()
    queue: list[str] = [args.pool_url]
    click_states = 0

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(args.cdp_url)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()

        while queue and len(visited) < args.max_pages:
            url = queue.pop(0)
            key = url_key(url)
            if key in visited:
                continue
            visited.add(key)
            state = await load_state(page, url, args)
            diag_path = diagnostics_dir / f"superbru_result_page_{len(visited):03d}.json"
            diag_path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
            result = extract_match_result(state)
            if result:
                results.append(result)

            # Follow URL-style navigation first.
            for candidate in candidate_links(state, args.pool_url, pool_id):
                candidate_key = url_key(candidate)
                if candidate_key not in visited and candidate not in queue:
                    queue.append(candidate)

            # Superbru also uses client-side clickable match rows. Click promising visible targets
            # from the same page to reveal prior match detail panes, then extract each result.
            ranked_clicks = sorted(
                [c for c in state.get("clickables") or [] if txt(c.get("selector"))],
                key=lambda c: clickable_score(txt(c.get("text"))),
                reverse=True,
            )[:40]
            seen_selectors: set[str] = set()
            for click in ranked_clicks:
                selector = txt(click.get("selector"))
                if selector in seen_selectors:
                    continue
                seen_selectors.add(selector)
                if clickable_score(txt(click.get("text"))) <= 0:
                    continue
                clicked_state = await click_candidate(page, selector, args)
                if not clicked_state:
                    continue
                click_states += 1
                cdiag = diagnostics_dir / f"superbru_clicked_state_{len(visited):03d}_{click_states:03d}.json"
                cdiag.write_text(json.dumps(clicked_state, indent=2, default=str), encoding="utf-8")
                clicked_result = extract_match_result(clicked_state)
                if clicked_result:
                    results.append(clicked_result)

        await browser.close()

    by_match: dict[str, dict[str, Any]] = {}
    for row in results:
        mid = txt(row.get("match_id"))
        if not mid:
            continue
        existing = by_match.get(mid)
        if existing is None or (txt(row.get("actual_score")) and not txt(existing.get("actual_score"))):
            by_match[mid] = row
    final = list(by_match.values())
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "pool_url": args.pool_url,
        "visited_pages": len(visited),
        "clicked_states": click_states,
        "raw_results_found": len(results),
        "deduped_results_found": len(final),
        "diagnostics_dir": str(diagnostics_dir),
        "note": "Primary extraction uses Superbru match detail panes. Diagnostics include pages and clicked states for tuning if rows are missing.",
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
        "match_id", "home_team", "away_team", "actual_score", "home_goals", "away_goals",
        "home_code", "away_code", "is_completed", "status", "score_source", "source_url", "source_title",
    ]
    out = pd.DataFrame(rows)
    for col in columns:
        if col not in out.columns:
            out[col] = ""
    out = out[columns].sort_values(["match_id"]).reset_index(drop=True) if not out.empty else pd.DataFrame(columns=columns)
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
