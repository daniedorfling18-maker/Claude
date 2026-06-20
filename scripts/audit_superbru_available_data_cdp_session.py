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


SCORELINE_RE = re.compile(r"(?<!\d)(\d{1,2})\s*[-:]\s*(\d{1,2})(?!\d)")
ROUND_RE = re.compile(r"\bRound\s+\d+\b", re.IGNORECASE)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit visible Superbru pool page data, tables, pick coverage and network metadata from the logged-in Chrome CDP session."
    )
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    parser.add_argument("--pool-url", default="https://www.superbru.com/worldcup_predictor/pool_view.php?t=1296&p=13236623&g=32&view=matches")
    parser.add_argument("--fixtures-csv", default="outputs/final_locked_picks/superbru_final_card.csv")
    parser.add_argument("--out-dir", default="outputs/data_inventory")
    parser.add_argument("--diagnostics-dir", default="outputs/data_inventory/raw_diagnostics/superbru")
    parser.add_argument("--settle-ms", type=int, default=9000)
    parser.add_argument("--timeout-ms", type=int, default=90000)
    parser.add_argument("--write-raw-state", action="store_true")
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


def load_fixtures(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    frame = pd.read_csv(path).fillna("")
    rows: list[dict[str, str]] = []
    for _, record in frame.iterrows():
        row = {col: txt(record.get(col)) for col in frame.columns}
        home = row.get("home_team") or row.get("home") or row.get("team_home")
        away = row.get("away_team") or row.get("away") or row.get("team_away")
        if home and away:
            row["home_team"] = home
            row["away_team"] = away
            row["match_id"] = row.get("match_id") or f"{slugify(home)}-{slugify(away)}"
            rows.append(row)
    return rows


EXTRACT_PAGE_JS = r"""
() => {
  function clean(text) { return (text || '').replace(/\s+/g, ' ').trim(); }
  const tables = Array.from(document.querySelectorAll('table')).map((table, idx) => {
    const rows = Array.from(table.querySelectorAll('tr')).map((row) =>
      Array.from(row.querySelectorAll('th,td')).map((cell) => clean(cell.innerText || cell.textContent))
    ).filter((row) => row.length > 0);
    return {
      index: idx,
      id: table.id || '',
      className: table.className || '',
      rowCount: rows.length,
      maxColumnCount: rows.reduce((m, r) => Math.max(m, r.length), 0),
      headers: rows[0] || [],
      sampleRows: rows.slice(0, 25),
      textSample: clean(table.innerText || table.textContent).slice(0, 8000)
    };
  });
  const controls = Array.from(document.querySelectorAll('a,button,[role="button"],[onclick]')).map((el, idx) => ({
    index: idx,
    tagName: el.tagName || '',
    text: clean(el.innerText || el.textContent),
    id: el.id || '',
    className: el.className || '',
    href: el.href || ''
  })).filter(x => x.text || x.href || x.id).slice(0, 600);
  const forms = Array.from(document.forms || []).map((form, idx) => ({
    index: idx,
    id: form.id || '',
    className: form.className || '',
    action: form.action || '',
    method: form.method || '',
    inputCount: form.querySelectorAll('input,select,textarea').length
  }));
  const bodyText = document.body ? document.body.innerText : '';
  return {
    currentUrl: window.location.href,
    title: document.title || '',
    bodyTextLength: bodyText.length,
    bodyTextSample: clean(bodyText).slice(0, 30000),
    tableCount: tables.length,
    controlCount: controls.length,
    formCount: forms.length,
    tables,
    controls,
    forms,
    scrapedAtUtc: new Date().toISOString()
  };
}
"""


def trim_for_raw(obj: Any, max_chars: int) -> Any:
    try:
        text = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return {"error": "Could not serialise object"}
    if len(text) <= max_chars:
        return obj
    return {"truncated": True, "max_chars": max_chars, "json_prefix": text[:max_chars]}


def count_scorelines(text: str) -> int:
    return len(SCORELINE_RE.findall(text or ""))


def fixture_visible(body_text: str, home: str, away: str) -> tuple[bool, bool, int, str]:
    low = body_text.lower()
    home_l = home.lower()
    away_l = away.lower()
    home_found = home_l in low if home_l else False
    away_found = away_l in low if away_l else False
    scoreline_count = 0
    sample = ""
    if home_found and away_found:
        hi = low.find(home_l)
        ai = low.find(away_l)
        start = max(0, min(hi, ai) - 900)
        end = min(len(body_text), max(hi, ai) + 1400)
        sample = re.sub(r"\s+", " ", body_text[start:end]).strip()[:600]
        scoreline_count = count_scorelines(sample)
    return home_found, away_found, scoreline_count, sample


def build_table_inventory(state: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for table in state.get("tables") or []:
        text_sample = txt(table.get("textSample"))
        rows.append({
            "table_index": table.get("index", ""),
            "table_id": txt(table.get("id")),
            "table_class": txt(table.get("className")),
            "row_count": table.get("rowCount", 0),
            "max_column_count": table.get("maxColumnCount", 0),
            "headers": ";".join(map(txt, table.get("headers") or [])),
            "scoreline_count_in_sample": count_scorelines(text_sample),
            "round_mentions_in_sample": len(ROUND_RE.findall(text_sample)),
            "text_sample": text_sample[:1000],
        })
    return rows


def build_control_inventory(state: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for control in state.get("controls") or []:
        text = txt(control.get("text"))
        rows.append({
            "control_index": control.get("index", ""),
            "tag_name": txt(control.get("tagName")),
            "control_id": txt(control.get("id")),
            "class_name": txt(control.get("className")),
            "href": txt(control.get("href")),
            "text": text[:500],
            "is_round_control": bool(ROUND_RE.search(text)),
            "scoreline_count_in_text": count_scorelines(text),
        })
    return rows


def build_fixture_coverage(state: dict[str, Any], fixtures: list[dict[str, str]]) -> list[dict[str, Any]]:
    body = txt(state.get("bodyTextSample"))
    rows: list[dict[str, Any]] = []
    for fixture in fixtures:
        home = fixture.get("home_team", "")
        away = fixture.get("away_team", "")
        home_found, away_found, scoreline_count, sample = fixture_visible(body, home, away)
        rows.append({
            "match_id": fixture.get("match_id", ""),
            "commence_time": fixture.get("commence_time", ""),
            "home_team": home,
            "away_team": away,
            "home_visible": home_found,
            "away_visible": away_found,
            "fixture_visible": home_found and away_found,
            "nearby_scoreline_count": scoreline_count,
            "likely_pick_data_visible": (home_found and away_found and scoreline_count > 0),
            "nearby_text_sample": sample,
        })
    return rows


async def audit(args: argparse.Namespace, fixtures: list[dict[str, str]]) -> dict[str, Any]:
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except Exception as exc:
        raise RuntimeError("Install with: pip install playwright && python -m playwright install chromium") from exc

    out_dir = Path(args.out_dir)
    diagnostics_dir = Path(args.diagnostics_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    diagnostics_dir.mkdir(parents=True, exist_ok=True)

    network_rows: list[dict[str, Any]] = []

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(args.cdp_url)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()

        def on_response(response: Any) -> None:
            try:
                headers = response.headers or {}
                url = response.url
                if not any(token in url.lower() for token in ["superbru", "pool", "predictor", "api", "ajax", "json"]):
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
        print("Superbru inventory :: loading pool page")
        try:
            await page.goto(args.pool_url, wait_until="domcontentloaded", timeout=args.timeout_ms)
        except PlaywrightTimeoutError:
            print("WARNING: Timeout loading Superbru pool page. Checking current state.")
        await page.wait_for_timeout(args.settle_ms)
        state = await page.evaluate(EXTRACT_PAGE_JS)
        await browser.close()

    if args.write_raw_state:
        raw_file = diagnostics_dir / "superbru_page_state.json"
        raw_file.write_text(json.dumps(trim_for_raw(state, args.max_raw_chars), indent=2, default=str), encoding="utf-8")

    table_rows = build_table_inventory(state)
    control_rows = build_control_inventory(state)
    coverage_rows = build_fixture_coverage(state, fixtures)

    table_csv = out_dir / "superbru_table_inventory.csv"
    control_csv = out_dir / "superbru_control_inventory.csv"
    network_csv = out_dir / "superbru_network_inventory.csv"
    coverage_csv = out_dir / "superbru_visible_pick_coverage.csv"
    fields_json = out_dir / "superbru_available_fields.json"

    pd.DataFrame(table_rows).to_csv(table_csv, index=False)
    pd.DataFrame(control_rows).to_csv(control_csv, index=False)
    pd.DataFrame(network_rows).drop_duplicates().to_csv(network_csv, index=False)
    pd.DataFrame(coverage_rows).to_csv(coverage_csv, index=False)

    visible_count = sum(1 for row in coverage_rows if row.get("fixture_visible"))
    likely_pick_count = sum(1 for row in coverage_rows if row.get("likely_pick_data_visible"))
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "superbru",
        "pool_url": args.pool_url,
        "title": txt(state.get("title")),
        "current_url": txt(state.get("currentUrl")),
        "body_text_length": state.get("bodyTextLength", 0),
        "table_count": state.get("tableCount", 0),
        "control_count": state.get("controlCount", 0),
        "form_count": state.get("formCount", 0),
        "network_row_count": len(network_rows),
        "fixture_input_count": len(fixtures),
        "fixture_visible_count": visible_count,
        "likely_pick_data_visible_count": likely_pick_count,
        "table_scoreline_total": sum(int(row.get("scoreline_count_in_sample") or 0) for row in table_rows),
        "control_round_count": sum(1 for row in control_rows if row.get("is_round_control")),
        "resource_type_counts": dict(Counter(txt(row.get("resource_type")) for row in network_rows)),
        "outputs": {
            "table_csv": str(table_csv),
            "control_csv": str(control_csv),
            "network_csv": str(network_csv),
            "coverage_csv": str(coverage_csv),
        },
    }
    fields_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload


def main() -> int:
    args = build_parser().parse_args()
    fixtures = load_fixtures(Path(args.fixtures_csv))
    import asyncio
    payload = asyncio.run(audit(args, fixtures))
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
