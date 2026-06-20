from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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

RESULT_COLUMNS = [
    "match_id", "round_label", "round_number", "home_team", "away_team", "actual_score",
    "home_goals", "away_goals", "home_code", "away_code", "is_completed", "status",
    "score_source", "raw_text", "source_url", "source_title", "scraped_at_utc",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill completed Superbru pool match results from a logged-in local Chrome CDP session."
    )
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    parser.add_argument("--pool-url", default="https://www.superbru.com/worldcup_predictor/pool.php?p=13236623&tab=matches#tab=matches")
    parser.add_argument("--out-csv", default="outputs/superbru_pool/superbru_match_results_auto.csv")
    parser.add_argument("--out-summary-json", default="outputs/superbru_pool/superbru_match_results_summary.json")
    parser.add_argument("--cache-csv", default="outputs/superbru_pool/superbru_match_results_cache.csv")
    parser.add_argument("--diagnostics-dir", default="outputs/superbru_pool/results_diagnostics")
    parser.add_argument("--settle-ms", type=int, default=7000)
    parser.add_argument("--timeout-ms", type=int, default=90000)
    parser.add_argument("--force-full-refresh", action="store_true")
    parser.add_argument(
        "--cached-round-lookback",
        type=int,
        default=2,
        help="When a cache exists, rescan this many recent cached rounds plus newer rounds.",
    )
    parser.add_argument(
        "--max-rounds-when-cached",
        type=int,
        default=4,
        help="Safety cap for round tabs clicked when cache exists. Set 0 for no cap.",
    )
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    low = txt(value).lower()
    return low in {"true", "1", "yes", "y", "completed"}


def slugify(value: Any) -> str:
    text = txt(value).lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def match_key(home: Any, away: Any) -> str:
    return f"{slugify(home)}-{slugify(away)}"


def round_number(value: Any) -> int | None:
    m = ROUND_RE.search(txt(value))
    if not m:
        return None
    return int(m.group(1))


def row_identity(row: dict[str, Any]) -> str:
    mid = txt(row.get("match_id"))
    if mid:
        return mid
    return match_key(row.get("home_team"), row.get("away_team"))


def row_rank(row: dict[str, Any]) -> tuple[int, str]:
    # Higher is better. Prefer completed result rows and newer scraped rows.
    source = txt(row.get("score_source"))
    score = 0
    if boolish(row.get("is_completed")):
        score += 100
    if txt(row.get("actual_score")):
        score += 20
    if source == "superbru_matches_tab_result_row":
        score += 10
    elif source == "superbru_match_detail_table":
        score += 5
    return score, txt(row.get("scraped_at_utc"))


def load_result_frame(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=RESULT_COLUMNS)
    try:
        frame = pd.read_csv(path).fillna("")
    except Exception:
        return pd.DataFrame(columns=RESULT_COLUMNS)
    for col in RESULT_COLUMNS:
        if col not in frame.columns:
            frame[col] = ""
    return frame[RESULT_COLUMNS].copy()


def frame_to_rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    return [{k: row.get(k, "") for k in frame.columns} for _, row in frame.iterrows()]


def normalise_result_row(row: dict[str, Any]) -> dict[str, Any]:
    out = {col: row.get(col, "") for col in RESULT_COLUMNS}
    if not txt(out.get("match_id")):
        out["match_id"] = match_key(out.get("home_team"), out.get("away_team"))
    rn = out.get("round_number")
    if txt(rn) == "":
        parsed_round = round_number(out.get("round_label"))
        out["round_number"] = parsed_round if parsed_round is not None else ""
    out["is_completed"] = boolish(out.get("is_completed"))
    return out


def merge_rows(cached_rows: list[dict[str, Any]], scraped_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    merged: dict[str, dict[str, Any]] = {}
    for row in cached_rows:
        norm = normalise_result_row(row)
        key = row_identity(norm)
        if key:
            merged[key] = norm

    changed = 0
    for row in scraped_rows:
        norm = normalise_result_row(row)
        key = row_identity(norm)
        if not key:
            continue
        existing = merged.get(key)
        if existing is None:
            changed += 1
            merged[key] = norm
            continue
        if row_rank(norm) >= row_rank(existing):
            before_score = txt(existing.get("actual_score"))
            before_status = txt(existing.get("status"))
            after_score = txt(norm.get("actual_score"))
            after_status = txt(norm.get("status"))
            if before_score != after_score or before_status != after_status:
                changed += 1
            merged[key] = norm

    return list(merged.values()), changed


def cached_rounds(cached_rows: list[dict[str, Any]]) -> set[int]:
    out: set[int] = set()
    for row in cached_rows:
        if not boolish(row.get("is_completed")):
            continue
        rn = row.get("round_number")
        if txt(rn) == "":
            rn = round_number(row.get("round_label"))
        try:
            if int(rn) > 0:
                out.add(int(rn))
        except Exception:
            continue
    return out


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
    scraped_at = txt(state.get("scrapedAtUtc")) or datetime.now(timezone.utc).isoformat()
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
            "round_number": round_number(round_label) or "",
            "source_url": state.get("currentUrl", ""),
            "status": "completed",
            "is_completed": True,
            "score_source": "superbru_matches_tab_result_row",
            "source_title": state.get("title", ""),
            "raw_text": text,
            "scraped_at_utc": scraped_at,
        })
        rows.append(parsed)
    return rows


def parse_table_detail_result(state: dict[str, Any], round_label: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    scraped_at = txt(state.get("scrapedAtUtc")) or datetime.now(timezone.utc).isoformat()
    for table in state.get("tables") or []:
        for row in table.get("matrix") or []:
            text = " ".join(txt(c) for c in row)
            parsed = parse_score_expr(text)
            if not parsed:
                continue
            parsed.update({
                "round_label": round_label,
                "round_number": round_number(round_label) or "",
                "source_url": state.get("currentUrl", ""),
                "status": "completed",
                "is_completed": True,
                "score_source": "superbru_match_detail_table",
                "source_title": state.get("title", ""),
                "raw_text": text,
                "scraped_at_utc": scraped_at,
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
        controls.append({
            "text": text,
            "selector": selector,
            "className": klass,
            "round_number": str(round_number(text) or ""),
        })
    return controls


def select_round_controls(
    controls: list[dict[str, str]],
    cached_rows: list[dict[str, Any]],
    args: argparse.Namespace,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    if args.force_full_refresh:
        return controls, {"cache_mode": "force_full_refresh", "cached_rounds": [], "scan_from_round": None}

    cached_nums = cached_rounds(cached_rows)
    if not cached_nums:
        return controls, {"cache_mode": "cold_start", "cached_rounds": [], "scan_from_round": None}

    max_cached = max(cached_nums)
    scan_from = max(1, max_cached - max(args.cached_round_lookback, 0) + 1)

    selected: list[dict[str, str]] = []
    for control in controls:
        rn = round_number(control.get("text"))
        if rn is None:
            continue
        if rn >= scan_from:
            selected.append(control)

    selected = sorted(selected, key=lambda c: round_number(c.get("text")) or 0)
    if args.max_rounds_when_cached and args.max_rounds_when_cached > 0:
        selected = selected[-args.max_rounds_when_cached:]

    return selected, {
        "cache_mode": "incremental",
        "cached_rounds": sorted(cached_nums),
        "scan_from_round": scan_from,
        "max_cached_round": max_cached,
    }


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


async def scrape(args: argparse.Namespace, cached_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        raise RuntimeError("Install with: pip install playwright && python -m playwright install chromium") from exc

    diagnostics_dir = Path(args.diagnostics_dir)
    diagnostics_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    clicked_rounds = 0
    controls: list[dict[str, str]] = []
    selected_controls: list[dict[str, str]] = []
    cache_plan: dict[str, Any] = {}

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(args.cdp_url)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()

        initial = await load_state(page, args.pool_url, args)
        (diagnostics_dir / "superbru_round_state_initial.json").write_text(json.dumps(initial, indent=2, default=str), encoding="utf-8")
        results.extend(parse_clickable_results(initial, "current"))
        results.extend(parse_table_detail_result(initial, "current"))

        controls = round_controls(initial)
        selected_controls, cache_plan = select_round_controls(controls, cached_rows, args)
        if cache_plan.get("cache_mode") == "incremental":
            labels = ", ".join(c["text"] for c in selected_controls) or "none"
            print(f"Using cached Superbru results. Scanning recent or new rounds only: {labels}")

        for idx, control in enumerate(selected_controls, start=1):
            # Reload before each round click so selectors point at the same DOM shape.
            await load_state(page, args.pool_url, args)
            state = await click_selector(page, control["selector"], args)
            if not state:
                continue
            clicked_rounds += 1
            round_label = control["text"]
            safe_round_name = re.sub(r"[^A-Za-z0-9]+", "_", round_label).strip("_") or f"round_{idx}"
            (diagnostics_dir / f"superbru_round_state_{idx:02d}_{safe_round_name}.json").write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
            results.extend(parse_clickable_results(state, round_label))
            results.extend(parse_table_detail_result(state, round_label))

        await browser.close()

    by_match: dict[str, dict[str, Any]] = {}
    for row in results:
        mid = txt(row.get("match_id"))
        if not mid:
            continue
        existing = by_match.get(mid)
        if existing is None or row_rank(row) >= row_rank(existing):
            by_match[mid] = row

    final = list(by_match.values())
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "pool_url": args.pool_url,
        "round_controls_found": len(controls),
        "rounds_selected_for_scan": len(selected_controls),
        "selected_round_labels": [c.get("text") for c in selected_controls],
        "rounds_clicked": clicked_rounds,
        "raw_results_found_this_run": len(results),
        "deduped_results_found_this_run": len(final),
        "diagnostics_dir": str(diagnostics_dir),
        "note": "Uses cache to avoid re-clicking every historical Superbru round tab on daily runs.",
        **cache_plan,
    }
    return final, summary


def main() -> int:
    args = build_parser().parse_args()
    out_csv = Path(args.out_csv)
    out_json = Path(args.out_summary_json)
    cache_csv = Path(args.cache_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    cache_csv.parent.mkdir(parents=True, exist_ok=True)

    cache_frame = load_result_frame(cache_csv)
    if cache_frame.empty and out_csv.exists():
        cache_frame = load_result_frame(out_csv)
    cached_rows = frame_to_rows(cache_frame)

    rows, summary = __import__("asyncio").run(scrape(args, cached_rows))
    merged_rows, changed_count = merge_rows(cached_rows, rows)

    out = pd.DataFrame([normalise_result_row(r) for r in merged_rows])
    for col in RESULT_COLUMNS:
        if col not in out.columns:
            out[col] = ""
    if not out.empty:
        out = out[RESULT_COLUMNS].sort_values(["round_number", "round_label", "match_id"]).reset_index(drop=True)
    else:
        out = pd.DataFrame(columns=RESULT_COLUMNS)

    out.to_csv(out_csv, index=False)
    out.to_csv(cache_csv, index=False)

    summary.update({
        "cache_csv": str(cache_csv),
        "cache_rows_before": len(cached_rows),
        "cache_rows_after": len(out),
        "new_or_updated_result_count": changed_count,
        "out_csv": str(out_csv),
        "out_summary_json": str(out_json),
    })
    out_json.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    print(f"Wrote {out_csv}")
    print(f"Wrote {cache_csv}")
    print(f"Wrote {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
